"""Where England ends, for drawing only.

Defra's England rasters come in 10 km squares, and the squares along the Welsh and
Scottish borders (and the coast) carry "no data" for the part outside England. Drawn
as they are, those parts make a staircase of grey squares. This module uses the ONS
country boundary to leave out *no-data* pixels outside England when tiles are drawn
for the page. Nothing else changes:

- pixels with a value, or under the cutoff, are drawn wherever they are;
- inside England every pixel is drawn exactly as stored;
- the stored, hash-checked tiles and every point lookup are untouched; the research
  tile route (`/england-tiles/`) still serves them unmasked.

Positions use the map's own pinned WGS 84 -> British National Grid transformation, so
the test of "inside England" is made in the boundary's own grid, as each pixel was
assigned to a Defra cell when the map was built. To keep tiles quick, the transformation
is made exactly on a 17 x 17 lattice across each tile and interpolated between (tests
check the result against the exact one).
"""
from collections import OrderedDict
import hashlib
import io
import json
import math
import threading
from pathlib import Path

import numpy as np
import shapely
from PIL import Image

from .england_map import CELL_M, CELL_WINDOW, HALF_WORLD, TILE_PX, png, to_lonlat
from . import england_acquisition as ea

SOURCE = {
    'name': 'Countries (December 2025) Boundaries UK BGC',
    'publisher': 'Office for National Statistics (Open Geography Portal)',
    'item': 'https://www.arcgis.com/home/item.html?id=c8bea35f51054f0c95aa876f66fe027c',
    'url': ('https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Countries_December_2025_Boundaries_UK_BGC/'
            'FeatureServer/replicafilescache/Countries_December_2025_Boundaries_UK_BGC_-4760573642517658145.geojson'),
    'file': 'Countries_December_2025_Boundaries_UK_BGC.geojson',
    'bytes': 8996850,
    'sha256': '8636470792773d500b467f442392320a987dcfe34d1b6e9b10124b135217c837',
    'retrieved': '2026-11-06',
    'crs': 'EPSG:27700',
    'generalisation': 'BGC: generalised to 20 m and clipped to the coastline (mean high water)',
    'licence': 'Open Government Licence v3.0',
    'attribution': ('Source: Office for National Statistics licensed under the Open Government Licence v.3.0. '
                    'Contains OS data © Crown copyright and database right 2025.'),
}
DEFAULT_PATH = Path('artifacts') / 'boundaries' / 'ons_countries_2025_bgc' / SOURCE['file']
ENGLAND, WALES, SCOTLAND = 'E92000001', 'W92000004', 'S92000003'
NO_DATA = (1, 2)  # Palette indices of the hatched no-data class; 0 is transparent.
MASKS = 512  # Cached per-tile masks (64 KB each); shared by every layer and look.
STEP = 16  # Pixels between exactly transformed lattice points.
EDGE_POINTS = TILE_PX // STEP + 1
# The page's border line, and its wash over everything outside England (shown zoomed out only).
LINE_TOLERANCE_M, WASH_TOLERANCE_M, WASH_MIN_AREA_M2 = 10, 150, 2e6
WASH_BOX = (-30.0, 40.0, 20.0, 65.0)  # Longitude and latitude the wash covers.


class OutlineIntegrityError(ValueError):
    pass


INSIDE, OUTSIDE = 'inside', 'outside'


def tile_points(z, x, y, offsets):
    """Longitude and latitude at pixel offsets (in both directions) across a 256 px tile."""
    size = 2 * HALF_WORLD / (TILE_PX << z)
    lon = to_lonlat(-HALF_WORLD + (x * TILE_PX + offsets) * size, 0)[0]
    lat = to_lonlat(0, HALF_WORLD - (y * TILE_PX + offsets) * size)[1]
    return np.meshgrid(lon, lat)


def tile_centres(z, x, y):
    """Longitude and latitude of each pixel centre."""
    return tile_points(z, x, y, np.arange(TILE_PX) + .5)


# For each pixel centre (along either axis): the lattice point before it and how far on it lies.
_CENTRES = np.arange(TILE_PX) + .5
LATTICE_INDEX = np.minimum((_CENTRES // STEP).astype(int), EDGE_POINTS - 2)
LATTICE_FRACTION = (_CENTRES - LATTICE_INDEX * STEP) / STEP


def bilinear(values):
    """Values on the lattice taken to every pixel centre. A pixel next to a point that could
    not be transformed (beyond the grid's extent) comes out not finite."""
    k, f = LATTICE_INDEX, LATTICE_FRACTION
    fy, fx = f[:, None], f[None, :]
    top = values[np.ix_(k, k)] * (1 - fx) + values[np.ix_(k, k + 1)] * fx
    bottom = values[np.ix_(k + 1, k)] * (1 - fx) + values[np.ix_(k + 1, k + 1)] * fx
    return top * (1 - fy) + bottom * fy


def tile_edge(z, x, y):
    """Points along a tile's edge, in longitude and latitude."""
    size = 2 * HALF_WORLD / (1 << z)
    t = np.linspace(0, 1, EDGE_POINTS)
    fx = np.r_[t, np.ones_like(t), t[::-1], np.zeros_like(t)]
    fy = np.r_[np.zeros_like(t), t, np.ones_like(t), t[::-1]]
    return to_lonlat(-HALF_WORLD + (x + fx) * size, HALF_WORLD - (y + fy) * size)


class EnglandOutline:
    """England's boundary, checked against the pinned download, for masking drawn tiles."""

    def __init__(self, path, transformer, source=SOURCE):
        """`source` pins the file; tests pass a small stand-in with its own size and hash."""
        path = Path(path)
        data = path.read_bytes()
        if len(data) != source['bytes'] or hashlib.sha256(data).hexdigest() != source['sha256']:
            raise OutlineIntegrityError(f'{path.name} differs from the boundary file this map was checked with')
        document = json.loads(data)
        if document.get('crs', {}).get('properties', {}).get('name') != SOURCE['crs']:
            raise OutlineIntegrityError('The boundary file is not in British National Grid')
        shapes = {f['properties']['CTRY25CD']: shapely.geometry.shape(f['geometry']) for f in document['features']}
        if not {ENGLAND, WALES, SCOTLAND} <= set(shapes):
            raise OutlineIntegrityError('The boundary file lacks England, Wales or Scotland')
        self.source = source
        self.countries = {'England': shapes[ENGLAND], 'Wales': shapes[WALES], 'Scotland': shapes[SCOTLAND]}
        self.england = shapes[ENGLAND]
        self.neighbours = shapely.union(shapes[WALES], shapes[SCOTLAND])
        for shape in self.countries.values():
            shapely.prepare(shape)
        self.transformer = transformer
        self.lock, self.page_lock = threading.Lock(), threading.Lock()
        self.masks = OrderedDict()
        self._page = None

    def public(self):
        return {**self.source, 'use': 'display only: no-data pixels outside England are not drawn; '
                                      'stored tiles and point evidence are unchanged'}

    def country(self, east, north):
        """England, Wales, Scotland, or None (the sea, or elsewhere), for a National Grid point."""
        if east is None or north is None:
            return None
        with self.lock:
            return next((name for name, shape in self.countries.items() if shapely.contains_xy(shape, east, north)), None)

    # --- Where England is --------------------------------------------------------

    def _classify(self, footprint):
        footprint = footprint.buffer(CELL_M)
        if self.england.contains(footprint):
            return INSIDE
        if not self.england.intersects(footprint):
            return OUTSIDE
        return None

    def _remember(self, key, mask):
        self.masks[key] = mask
        while len(self.masks) > MASKS:
            self.masks.popitem(last=False)
        return mask

    def tile_mask(self, z, x, y):
        """INSIDE, OUTSIDE or a boolean array (True inside England) for a map tile."""
        key = ('tile', z, x, y)
        with self.lock:
            if key in self.masks:
                self.masks.move_to_end(key)
                return self.masks[key]
            east, north = self.transformer.transform(*tile_edge(z, x, y))
            # A tile reaching beyond the transformation grid is tested pixel by pixel.
            mask = self._classify(shapely.Polygon(zip(east, north))) if np.isfinite([east, north]).all() else None
            if mask is None:
                east, north = self.tile_grid(z, x, y)
                mask = self._inside(east, north)
            return self._remember(key, mask)

    def tile_grid(self, z, x, y):
        """National Grid position of each pixel centre: exact on the lattice, bilinear between,
        and exact for any pixel beside a lattice point beyond the grid (not finite if it is too)."""
        east, north = self.transformer.transform(*tile_points(z, x, y, np.arange(0, TILE_PX + 1, STEP, dtype='float64')))
        with np.errstate(invalid='ignore'):
            east, north = bilinear(np.asarray(east)), bilinear(np.asarray(north))
        missing = ~(np.isfinite(east) & np.isfinite(north))
        if missing.any():
            lon, lat = tile_centres(z, x, y)
            east[missing], north[missing] = self.transformer.transform(lon[missing], lat[missing])
        return east, north

    def _inside(self, east, north):
        """True where a point is in England; points that could not be placed are not."""
        placed = np.isfinite(east) & np.isfinite(north)
        if not placed.any():
            return np.zeros(east.shape, dtype=bool)
        # Only the part of England near these points, so each test stays quick.
        pad = 4 * CELL_M
        e, n = east[placed], north[placed]
        local = shapely.clip_by_rect(self.england, e.min() - pad, n.min() - pad, e.max() + pad, n.max() + pad)
        inside = np.zeros(east.shape, dtype=bool)
        inside[placed] = shapely.contains_xy(local, e, n)
        return inside

    def window_mask(self, wx, wy):
        """The same for a 1 km window of native cells, tested at each cell's centre."""
        key = ('cells', wx, wy)
        with self.lock:
            if key in self.masks:
                self.masks.move_to_end(key)
                return self.masks[key]
            side = CELL_WINDOW * CELL_M
            west, south = ea.LATTICE[0] + wx * side, ea.LATTICE[1] + wy * side
            mask = self._classify(shapely.box(west, south, west + side, south + side))
            if mask is None:
                centres = (np.arange(CELL_WINDOW) + .5) * CELL_M
                east, north = np.meshgrid(west + centres, south + side - centres)
                mask = self._inside(east, north)
            return self._remember(key, mask)

    # --- Drawing ---------------------------------------------------------------

    @staticmethod
    def apply(data, mask):
        """The tile with no-data pixels outside England made transparent; None if nothing is left."""
        if data is None or mask is INSIDE:
            return data
        image = Image.open(io.BytesIO(data))
        if image.mode != 'P':
            raise OutlineIntegrityError('Map tiles are expected to be palette images')
        indices = np.asarray(image)
        drop = np.isin(indices, NO_DATA)
        if mask is not OUTSIDE:
            drop &= ~mask
        if not drop.any():
            return data
        indices = indices.copy()
        indices[drop] = 0
        return png(indices) if indices.any() else None

    def page_shapes(self):
        """GeoJSON for the page, made once: England's borders with Wales and Scotland (the
        lines the countries share), and a wash over everything outside England."""
        with self.page_lock:
            if self._page is None:
                border = shapely.line_merge(shapely.intersection(self.england.boundary, self.neighbours.boundary))
                border = shapely.simplify(border, LINE_TOLERANCE_M)
                parts = [p for p in shapely.get_parts(shapely.simplify(self.england, WASH_TOLERANCE_M))
                         if p.area >= WASH_MIN_AREA_M2]
                with self.lock:
                    england = shapely.transform(shapely.MultiPolygon(parts), self._to_wgs84)
                    border = shapely.transform(border, self._to_wgs84)
                # The wash is cut into half-degree squares around England. One large rectangle with
                # England as a hole is valid, but MapLibre's triangulation of it can leave stray
                # triangles across England; pieces without large holes draw reliably.
                x0, y0, x1, y1 = england.bounds
                west, south, east, north = math.floor(x0 * 2) / 2, math.floor(y0 * 2) / 2, math.ceil(x1 * 2) / 2, math.ceil(y1 * 2) / 2
                frame = shapely.difference(shapely.box(*WASH_BOX), shapely.box(west, south, east, north))
                cells = [shapely.box(x, y, x + .5, y + .5) for x in np.arange(west, east, .5) for y in np.arange(south, north, .5)]
                pieces = [p for cell in cells
                          for p in shapely.get_parts(shapely.difference(cell, shapely.clip_by_rect(england, *cell.bounds)))
                          if not p.is_empty]
                wash = shapely.MultiPolygon([*shapely.get_parts(frame), *pieces])
                features = [{'type': 'Feature', 'properties': {'kind': kind}, 'geometry': shapely.geometry.mapping(geometry)}
                            for kind, geometry in (('border', border), ('outside', wash))]
                self._page = json.dumps({'type': 'FeatureCollection', 'source': self.source['name'], 'licence': self.source['licence'],
                                         'features': features}, separators=(',', ':')).encode('utf-8')
            return self._page

    def _to_wgs84(self, coords):
        lon, lat = self.transformer.transform(coords[:, 0], coords[:, 1], direction='INVERSE')
        return np.round(np.column_stack([lon, lat]), 6)
