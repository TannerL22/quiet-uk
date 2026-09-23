"""Versioned display publication and exact, qualified location inspection.

Display rasters are derivatives, never measurements or analysis exports.
Zero energy is nodata; quality 0/1/2 means outside/qualified/withheld.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import platform
from pathlib import Path
import shutil
import struct
import uuid
import zlib
from datetime import datetime, timezone
from functools import lru_cache

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform, transform_bounds
from rasterio.windows import Window, from_bounds as bounds_window

from .catalogue import DatasetCatalogue, CatalogueIntegrityError
from .land_mask import read_tile_land_mask
from .validation import STANDARD_TILE_BANDS, validate_production_arrays
from .evidence import evidence_capabilities, location_observations

DISPLAY_VERSION = 2
PALETTE = ['#258b92', '#bfd0a2', '#e0d498', '#deb16e', '#cb855f', '#ab605d', '#77465b']
BREAKS = [45, 50, 55, 60, 65, 70]
RELEASE_FILES = ('energy.tif', 'quality.tif', 'aircraft_energy.tif', 'aircraft_quality.tif', 'publisher_source.py')


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')


def publish_display(catalogue_dir, tile_root, mask_path, destination):
    """Publish a new display generation without modifying any scientific input."""
    destination = Path(destination)
    if destination.exists():
        raise FileExistsError('Display destination already exists; choose a new generation.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Inherit workspace ACLs. tempfile.mkdtemp's private Windows ACL otherwise
    # survives publication and makes the product unreadable to the user's app.
    staging = destination.parent / ('.explorer-' + uuid.uuid4().hex)
    staging.mkdir()
    try:
        with DatasetCatalogue(catalogue_dir, tile_root, mask_path) as catalogue:
            catalogue._verify_mask()
            with rasterio.open(mask_path) as mask:
                profile = dict(driver='GTiff', width=mask.width, height=mask.height,
                               count=1, crs=mask.crs, transform=mask.transform,
                               tiled=True, blockxsize=256, blockysize=256,
                               compress='deflate', nodata=0)
                with rasterio.open(staging / 'energy.tif', 'w', dtype='float32', **profile) as energy, \
                     rasterio.open(staging / 'quality.tif', 'w', dtype='uint8', **profile) as quality, \
                     rasterio.open(staging / 'aircraft_energy.tif', 'w', dtype='float32', **{**profile, 'nodata': -1}) as aircraft_energy, \
                     rasterio.open(staging / 'aircraft_quality.tif', 'w', dtype='uint8', **profile) as aircraft_quality:
                    # Start all authoritative land as unknown, including uncovered land.
                    for _, win in mask.block_windows(1):
                        land = mask.read(1, window=win) > 0
                        quality.write(np.where(land, 2, 0).astype('uint8'), 1, window=win)
                        aircraft_quality.write(np.where(land, 2, 0).astype('uint8'), 1, window=win)
                        aircraft_energy.write(np.full(land.shape, -1, dtype='float32'), 1, window=win)
                    tile_count = 0
                    for row, _, bands in catalogue.iter_tile_records():
                        path = catalogue.verified_tile_path(row)
                        with rasterio.open(path) as src:
                            arrays = src.read()
                            land = read_tile_land_mask(mask_path, src.bounds, src.shape)
                            validate_production_arrays(arrays, land, nodata=src.nodata,
                                                       band_names=src.descriptions,
                                                       config=catalogue.semantic_config)
                            if tuple(src.descriptions) != STANDARD_TILE_BANDS:
                                raise CatalogueIntegrityError('Unsupported display input schema')
                            win = bounds_window(*src.bounds, transform=mask.transform)
                            values = [win.col_off, win.row_off, win.width, win.height]
                            if any(abs(v-round(v)) > 1e-7 for v in values):
                                raise CatalogueIntegrityError('Display tile is not mask aligned')
                            # Coastal tiles may extend beyond the mask rectangle.
                            clipped = win.intersection(Window(0, 0, mask.width, mask.height))
                            sy = int(clipped.row_off-win.row_off)
                            sx = int(clipped.col_off-win.col_off)
                            h, w = int(clipped.height), int(clipped.width)
                            eligible = next(b['qualification'] for b in bands if b['band_name'] == 'road_rail_upper_db') == 'qualified'
                            state = np.where(land, 1 if eligible else 2, 0).astype('uint8')
                            # Aggregate energy, never arithmetic dB, for display overviews.
                            e = np.zeros(src.shape, dtype='float32')
                            if eligible:
                                e[land] = np.power(10.0, arrays[1][land].astype('float64') / 10.0)
                            energy.write(e[sy:sy+h, sx:sx+w], 1, window=clipped)
                            quality.write(state[sy:sy+h, sx:sx+w], 1, window=clipped)
                            airport_status = next(b['qualification'] for b in bands if b['band_name'] == 'airport_reported_lower_db')
                            airport_state = 1 if airport_status == 'qualified' else 3 if airport_status == 'qualified_with_coverage_limitation' else 2
                            aq = np.where(land, airport_state, 0).astype('uint8')
                            ae = np.full(src.shape, -1, dtype='float32')
                            if airport_state == 1:
                                # Unreported accepted cells contribute zero LOWER energy,
                                # not nodata, so display averaging retains their support.
                                ae[land] = 0
                                reported = land & (arrays[2] != src.nodata) & np.isfinite(arrays[2])
                                ae[reported] = np.power(10.0, arrays[2][reported].astype('float64') / 10.0)
                            aircraft_energy.write(ae[sy:sy+h, sx:sx+w], 1, window=clipped)
                            aircraft_quality.write(aq[sy:sy+h, sx:sx+w], 1, window=clipped)
                        tile_count += 1
                    factors = [n for n in [2, 4, 8, 16, 32, 64] if min(mask.shape)//n >= 1]
                    energy.build_overviews(factors, Resampling.average)
                    aircraft_energy.build_overviews(factors, Resampling.average)
                    # GDAL overview creation does not support maximum. Read native
                    # quality pixels during warp so tiny withheld areas survive.
                    energy.set_band_description(1, 'qualified_road_rail_upper_energy_display_only')
                    quality.set_band_description(1, '0_outside_1_qualified_2_withheld')
                    aircraft_energy.set_band_description(1, 'reported_aircraft_lower_energy_display_only_zero_unreported_minus1_unavailable')
                    aircraft_quality.set_band_description(1, '0_outside_1_accepted_2_withheld_3_coverage_limited')
                bounds = list(transform_bounds(mask.crs, 'EPSG:4326', *mask.bounds, densify_pts=21))
            catalogue._verify_mask()
            inputs = {k: {'sha256': v['sha256']} for k, v in catalogue.metadata['inputs'].items() if 'sha256' in v}
            identity = {
                'display_contract_version': DISPLAY_VERSION,
                'catalogue_build_id': catalogue.metadata['build_id'],
                'catalogue_sha256': file_hash(Path(catalogue_dir) / 'catalogue.sqlite3'),
                'publisher_sha256': file_hash(__file__), 'inputs': inputs,
            }
            release_id = 'explorer-' + hashlib.sha256(json_bytes(identity)).hexdigest()[:20]
            (staging / 'publisher_source.py').write_bytes(Path(__file__).read_bytes())
            floor = 10 * math.log10(sum(10**(catalogue.semantic_config['reporting_threshold_db'][s]/10) for s in ['road', 'rail']))
            manifest = {
                **identity, 'release_id': release_id,
                'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'title': 'Quiet UK historical England baseline — exploratory release',
                'software': {'python': platform.python_version(), 'numpy': np.__version__,
                             'rasterio': rasterio.__version__, 'gdal': rasterio.__gdal_version__,
                             'proj': rasterio.__proj_version__},
                'implementation_sha256': {p.name: file_hash(p) for p in sorted(Path(__file__).parent.glob('*.py'))},
                'research_status': 'provisional_historical_acquisition_linkage_unresolved',
                'historical_acquisition_linkage': 'unresolved',
                'metric': 'Lden', 'metric_evidence': 'configured_only_for_historical_baseline',
                'resolution_m': 100, 'analytical_crs': 'EPSG:27700',
                'reference_years': catalogue.metadata['reference_years'],
                'band_dictionary': catalogue.metadata['band_dictionary'],
                'capabilities': evidence_capabilities(),
                'views': {
                    'both': 'Road/rail upper-bound colours with purple aircraft-presence hatching; no numerical source combination.',
                    'road_rail': 'Historical road/rail upper bound.',
                    'aircraft': 'Historical reported aircraft lower bound; no reported energy is not silence.',
                },
                'road_rail_floor_db': floor, 'bounds_wgs84': bounds,
                'tile_count': tile_count, 'palette': PALETTE, 'breaks_db': BREAKS,
                'display_method': 'Separate energy-space means for road/rail upper and aircraft reported lower bounds. Aircraft unreported accepted cells retain zero lower energy in the denominator. Outside/withheld support is excluded; maximum quality resampling flags unavailable support. The default overlays aircraft presence without summing sources. Display summaries are not exposure estimates.',
                'limitations': [
                    'Historical source acquisition linkage and reference periods are unresolved.',
                    'Road/rail upper bounds exclude aviation and other environmental sources.',
                    'A reporting-floor tie cannot distinguish quieter places.',
                    '100 m spatial aggregates are not property-facade, indoor, personal or visit-time exposure.',
                    'Structural qualification is not independent acoustic validation.',
                    'A combined reported lower bound and road/rail upper bound are not one interval.',
                ],
                'attribution': 'Configured products: Defra strategic noise mapping, Crown copyright, Open Government Licence. Historical acquisition linkage unresolved. England mask derived from ONS boundaries; see source records.',
                'source_documentation': 'https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps',
                'files': {name: {'sha256': file_hash(staging/name), 'bytes': (staging/name).stat().st_size} for name in RELEASE_FILES},
            }
            (staging / 'dataset.json').write_bytes(json_bytes(manifest))
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists() and staging.resolve().parent == destination.parent.resolve() and staging.name.startswith('.explorer-'):
            shutil.rmtree(staging)


def load_release(root, catalogue_dir):
    root = Path(root)
    manifest = json.loads((root / 'dataset.json').read_text('utf-8'))
    if manifest['display_contract_version'] != DISPLAY_VERSION:
        raise ValueError('Unsupported display version; publish a new display generation')
    if file_hash(Path(catalogue_dir) / 'catalogue.sqlite3') != manifest['catalogue_sha256']:
        raise CatalogueIntegrityError('Display and analytical catalogue generations differ')
    for name in RELEASE_FILES:
        if file_hash(root / name) != manifest['files'][name]['sha256']:
            raise CatalogueIntegrityError(f'Display checksum mismatch: {name}')
    return manifest


def xyz_bounds(z, x, y):
    if not 5 <= z <= 16 or not 0 <= x < 2**z or not 0 <= y < 2**z:
        raise ValueError('Invalid display tile')
    half = 20037508.342789244
    span = 2 * half / 2**z
    return (-half+x*span, half-(y+1)*span, -half+(x+1)*span, half-y*span)


def rgba_png(rgba):
    h, w, _ = rgba.shape
    def chunk(kind, data):
        return struct.pack('!I', len(data)) + kind + data + struct.pack('!I', zlib.crc32(kind+data) & 0xffffffff)
    raw = b''.join(b'\x00'+row.tobytes() for row in rgba.astype('uint8'))
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', w, h, 8, 6, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b'')


def colourize(energy, quality, mode='road_rail'):
    rgba = np.zeros((*energy.shape, 4), dtype='uint8')
    valid = (quality == 1) & (energy > 0)
    db = 10*np.log10(np.maximum(energy, 1))
    bins = np.searchsorted(BREAKS, db, side='right')
    colours = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in PALETTE], dtype='uint8')
    rgba[valid, :3] = colours[bins[valid]]
    rgba[valid, 3] = 220
    unknown = (quality >= 2) | ((quality == 1) & ~valid)
    yy, xx = np.indices(energy.shape)
    stripes = ((xx+yy) % 10) < 4
    rgba[unknown, :3] = 158
    rgba[unknown, 3] = np.where(stripes[unknown], 220, 110)
    if mode == 'aircraft':
        unreported = (quality == 1) & (energy == 0)
        rgba[unreported] = [214, 207, 187, 150]
    elif mode == 'aircraft_presence':
        # This qualitative overlay deliberately shares no numeric colour scale
        # with the road/rail upper bound. Source comparability is unresolved.
        rgba[:] = 0
        rgba[valid, :3] = [100, 47, 119]
        rgba[valid, 3] = np.where(stripes[valid], 230, 55)
        withheld = quality == 2
        rgba[withheld, :3] = 100
        rgba[withheld, 3] = np.where(stripes[withheld], 160, 0)
    return rgba


class Explorer:
    def __init__(self, release_root, catalogue_dir, tile_root, mask_path):
        self.root = Path(release_root)
        self.catalogue_dir, self.tile_root, self.mask_path = catalogue_dir, tile_root, mask_path
        self.manifest = load_release(self.root, catalogue_dir)

    @lru_cache(maxsize=384)
    def tile(self, z, x, y, mode='road_rail'):
        if mode not in ('road_rail', 'aircraft', 'aircraft_presence'):
            raise ValueError('Unknown noise view')
        bounds = xyz_bounds(z, x, y)
        target = from_bounds(*bounds, 256, 256)
        arrays = []
        prefix = '' if mode == 'road_rail' else 'aircraft_'
        for name, method in [(prefix+'energy.tif', Resampling.average), (prefix+'quality.tif', Resampling.max)]:
            with rasterio.open(self.root/name) as src, WarpedVRT(src, crs='EPSG:3857', transform=target,
                    width=256, height=256, resampling=method, nodata=src.nodata) as vrt:
                arrays.append(vrt.read(1))
        return rgba_png(colourize(*arrays, mode=mode))

    def lookup(self, longitude, latitude):
        if not all(math.isfinite(v) for v in [longitude, latitude]) or not (-11 <= longitude <= 3 and 49 <= latitude <= 61):
            raise ValueError('Choose a location within the UK map extent')
        with DatasetCatalogue(self.catalogue_dir, self.tile_root, self.mask_path) as catalogue:
            if catalogue.metadata['build_id'] != self.manifest['catalogue_build_id']:
                raise CatalogueIntegrityError('Analytical dataset changed; restart with a matching release')
            result = catalogue.lookup(coordinate_crs='EPSG:4326', longitude=longitude, latitude=latitude)
            tile = catalogue.connection.execute('SELECT sha256 FROM tiles WHERE tile_id = ?', (result['tile_id'],)).fetchone()
        result['dataset'] = {
            'release_id': self.manifest['release_id'],
            'catalogue_build_id': self.manifest['catalogue_build_id'],
            'source_tile_sha256': tile['sha256'] if tile else None,
            'metric': self.manifest['metric'], 'metric_evidence': self.manifest['metric_evidence'],
            'resolution_m': 100, 'historical_acquisition_linkage': 'unresolved',
            'research_status': self.manifest['research_status'],
        }
        result['retrieved_at_utc'] = datetime.now(timezone.utc).isoformat()
        result['cell_geojson'] = None
        if result['cell']:
            west, south, east, north = result['cell']['bounds_bng']
            xs, ys = transform('EPSG:27700', 'EPSG:4326', [west, east, east, west, west], [south, south, north, north, south])
            result['cell_geojson'] = {'type': 'Feature', 'properties': {}, 'geometry': {'type': 'Polygon', 'coordinates': [list(map(list, zip(xs, ys)))]}}
        value = result['bands']['road_rail_upper_db']['value']
        result['at_reporting_floor'] = value is not None and abs(value-self.manifest['road_rail_floor_db']) < 1e-4
        result['evidence_schema_version'] = 1
        result['observations'] = location_observations(result)
        result['capabilities'] = evidence_capabilities()
        return result
