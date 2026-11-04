"""England map: Web Mercator display tiles plus native point evidence.

The map never copies or alters analytical rasters. Point values are read from
the acquisition's lossless archives and checked against each record's SHA-256.
Display tiles are a picture only. At every zoom each pixel shows the native
10 m cell under its centre, with Defra's zero definition (evidence contract v2).
At zoom 14 a pixel is 5.4–6.1 m on the ground, so every native cell is drawn.
"""
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import threading
import zlib

import numpy as np
from pyproj import Transformer
from pyproj.transformer import TransformerGroup
import pyproj
import pyproj.datadir

from . import england_acquisition as ea
from . import source_pilot as p
from .digests import file_hash, json_bytes
from .evidence_semantics import CONTRACT_V2, interpret, zero_rule

TILE_PX = 256
MIN_ZOOM, MAX_ZOOM, BLOCK_ZOOM = 5, 14, 10
BLOCK_PX = TILE_PX << (MAX_ZOOM - BLOCK_ZOOM)  # Zoom-14 pixels along one side of a zoom-10 tile.
CHUNK_PX = 512  # Zoom-14 pixels per side of one transformed chunk.
EARTH_RADIUS = 6378137.0
HALF_WORLD = math.pi * EARTH_RADIUS
PIXEL_M = 2 * HALF_WORLD / (TILE_PX << MAX_ZOOM)
LAYERS = tuple(f'{source}-{metric}' for source in p.PROVIDERS for metric in p.METRICS)
OUTSIDE, NODATA, BELOW, FIRST_BIN = 0, 1, 2, 3
CELL_M = 10
CACHE_RASTERS = 27
CELL_WINDOW = 100  # Cells per side of a native-cell close-up image (1 km).
MAX_CELL_WINDOWS = 24


def rgb(colour):
    return tuple(int(colour[i:i+2], 16) for i in (1, 3, 5))


MISSING = {item['encoding']: item['colour'] for item in CONTRACT_V2['display_missing']}
# Palette index 0 is transparent (outside the downloaded rasters); no data is
# hatched with two alphas; then below-cutoff and the eight value bands.
PALETTE = ([(0, 0, 0, 0), (*rgb(MISSING['tiff_nodata']), 90), (*rgb(MISSING['tiff_nodata']), 170),
            (*rgb(MISSING['zero_code']), 190)] + [(*rgb(c), 230) for c in p.COLOURS])
_yy, _xx = np.indices((TILE_PX, TILE_PX))
HATCH = ((_xx - _yy) % 8 < 2).astype('uint8')


class MapIntegrityError(ValueError):
    pass


def png(indices):
    """An indexed-colour PNG; identical inputs give identical bytes."""
    height, width = indices.shape
    rows = np.zeros((height, width + 1), dtype='uint8')
    rows[:, 1:] = indices

    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 3, 0, 0, 0))
            + chunk(b'PLTE', bytes(v for colour in PALETTE for v in colour[:3]))
            + chunk(b'tRNS', bytes(colour[3] for colour in PALETTE))
            + chunk(b'IDAT', zlib.compress(rows.tobytes(), 9)) + chunk(b'IEND', b''))


def encode(classes):
    indices = np.where(classes >= BELOW, classes + 1, 0).astype('uint8')
    missing = classes == NODATA
    indices[missing] = 1 + HATCH[:classes.shape[0], :classes.shape[1]][missing]
    return png(indices)


def classify(values, valid, rule):
    """Display classes for one raster; zero is below the cutoff only under a provider rule."""
    if (valid & ~((values >= 0) & (values <= 160))).any():
        raise MapIntegrityError('Unrecognised unmasked noise encoding')
    classes = np.full(values.shape, NODATA, dtype='uint8')
    reported = valid & (values > 0)
    classes[reported] = FIRST_BIN + np.searchsorted(p.BREAKS, values[reported], side='right')
    if rule is not None:
        classes[valid & (values == 0)] = BELOW
    return classes


def best_transformer():
    """The most accurate WGS 84 -> British National Grid operation installed here."""
    group = TransformerGroup('EPSG:4326', 'EPSG:27700', always_xy=True)
    if not group.transformers:
        raise MapIntegrityError('No WGS 84 to British National Grid transformation is available')
    chosen = group.transformers[0]
    probes = [[-1.25, 51.75], [-5.7, 50.06], [1.75, 52.48], [-2.03, 55.81]]
    grids = [{'name': g.short_name, 'url': g.url, 'bytes': Path(g.full_name).stat().st_size, 'sha256': file_hash(g.full_name)}
             for op in chosen.operations for g in op.grids]
    return {'description': chosen.description, 'definition': chosen.definition, 'grids': grids,
            'stated_accuracy_m': chosen.accuracy, 'proj_version': pyproj.proj_version_str,
            'better_operations_not_installed': [op.name for op in group.unavailable_operations
                                                if op.accuracy is not None and 0 <= op.accuracy < chosen.accuracy],
            'check_points': [[lon, lat, *chosen.transform(lon, lat)] for lon, lat in probes]}


def grid_path(name):
    """The installed grid file PROJ would use, or None, searched in pyproj's order (data folders, then the user folder)."""
    for folder in (*pyproj.datadir.get_data_dir().split(os.pathsep), pyproj.datadir.get_user_data_dir()):
        if (Path(folder) / name).is_file():
            return Path(folder) / name
    return None


def pinned_transformer(info):
    """The exact operation a map was built with, or an error if it no longer reproduces."""
    for grid in info.get('grids', []):
        path = grid_path(grid['name'])
        if path is None or file_hash(path) != grid['sha256']:
            raise MapIntegrityError(f'Transformation grid {grid["name"]} is missing or differs from the one this map was built with')
    transformer = Transformer.from_pipeline(info['definition'])
    for lon, lat, east, north in info['check_points']:
        if max(abs(a - b) for a, b in zip(transformer.transform(lon, lat), (east, north))) > 1e-6:
            raise MapIntegrityError('Coordinate transformation differs from the one this map was built with')
    return transformer


def owner(east, north):
    """The 10 km lattice tile owning each point: west/north edges included."""
    i = np.floor((east - ea.LATTICE[0]) / ea.TILE_M).astype('int64')
    j = np.ceil((north - ea.LATTICE[1]) / ea.TILE_M).astype('int64') - 1
    return i, j


def tile_for(i, j):
    return ea.tile_id(ea.LATTICE[0] + ea.TILE_M * int(i), ea.LATTICE[1] + ea.TILE_M * int(j))


def to_lonlat(x, y):
    return np.degrees(x / EARTH_RADIUS), np.degrees(2 * np.arctan(np.exp(y / EARTH_RADIUS)) - math.pi / 2)


def to_mercator(lon, lat):
    lon, lat = np.asarray(lon, dtype='float64'), np.asarray(lat, dtype='float64')
    return np.radians(lon) * EARTH_RADIUS, EARTH_RADIUS * np.log(np.tan(math.pi / 4 + np.radians(lat) / 2))


def layer_key(record):
    return f'{record["source"]}-{record["metric"]}'


def raster(acquisition, record):
    """Values, validity and grid origin of one archived provider response."""
    with ea.open_record(acquisition, record) as memory, memory.open() as ds:
        p.require_identity_encoding(ds)
        t = ds.transform
        if ds.crs != 'EPSG:27700' or (t.a, t.b, t.d, t.e) != (CELL_M, 0, 0, -CELL_M):
            raise MapIntegrityError('Unexpected native grid: ' + record['path'])
        return ds.read(1), ds.read_masks(1) != 0, (t.c, t.f)


class RasterCache:
    def __init__(self, load, size=CACHE_RASTERS):
        self.load, self.size, self.items, self.lock = load, size, OrderedDict(), threading.Lock()

    def get(self, record):
        key = record['path']
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        value = self.load(record)
        with self.lock:
            self.items[key] = value
            while len(self.items) > self.size:
                self.items.popitem(last=False)
        return value


_WORKER = {}


def _start_worker(acquisition, records, definition):
    accepted = [r for r in records if r['status'] == 'accepted']
    index = {(r['tile'], layer_key(r)): r for r in accepted}

    def load(record):
        values, valid, origin = raster(acquisition, record)
        return classify(values, valid, zero_rule(record['source'], record['metric'])), origin
    _WORKER.update(index=index, cache=RasterCache(load, size=64),
                   boxes=np.array([r['qa']['bounds'] for r in accepted], dtype='float64').reshape(-1, 4),
                   transformer=Transformer.from_pipeline(definition))


def render_block(block):
    """Zoom 14–10 tiles inside one zoom-10 tile, plus its samples for lower zooms."""
    bx, by = block
    index, cache, transformer, boxes = (_WORKER[k] for k in ('index', 'cache', 'transformer', 'boxes'))
    classes = np.zeros((len(LAYERS), BLOCK_PX, BLOCK_PX), dtype='uint8')
    edge = np.r_[0:CHUNK_PX:32, CHUNK_PX - 1]
    for top in range(0, BLOCK_PX, CHUNK_PX):
        lat = to_lonlat(0, HALF_WORLD - (by * BLOCK_PX + top + np.arange(CHUNK_PX) + .5) * PIXEL_M)[1]
        for left in range(0, BLOCK_PX, CHUNK_PX):
            lon = to_lonlat(-HALF_WORLD + (bx * BLOCK_PX + left + np.arange(CHUNK_PX) + .5) * PIXEL_M, 0)[0]
            # The chunk's outline bounds its grid footprint; skip chunks no raster reaches.
            ring_lon = np.r_[lon[edge], lon[edge], np.full(edge.size, lon[0]), np.full(edge.size, lon[-1])]
            ring_lat = np.r_[np.full(edge.size, lat[0]), np.full(edge.size, lat[-1]), lat[edge], lat[edge]]
            ring_e, ring_n = transformer.transform(ring_lon, ring_lat)
            w, s, e, n = ring_e.min() - CELL_M, ring_n.min() - CELL_M, ring_e.max() + CELL_M, ring_n.max() + CELL_M
            if not ((boxes[:, 0] < e) & (boxes[:, 2] > w) & (boxes[:, 1] < n) & (boxes[:, 3] > s)).any():
                continue
            east, north = transformer.transform(*np.meshgrid(lon, lat))
            if not (np.isfinite(east).all() and np.isfinite(north).all()):
                raise MapIntegrityError('Coordinate transformation failed inside a map block')
            east, north = east.ravel(), north.ravel()
            i, j = owner(east, north)
            keys, inverse = np.unique(i * 1_000_000 + j, return_inverse=True)
            order = np.argsort(inverse, kind='stable')
            ends = np.cumsum(np.bincount(inverse, minlength=len(keys)))
            for k, key in enumerate(keys):
                tile = tile_for(*divmod(int(key), 1_000_000))
                found = [(layer, index[(tile, name)]) for layer, name in enumerate(LAYERS) if (tile, name) in index]
                if not found:
                    continue
                members = order[(ends[k-1] if k else 0):ends[k]]
                rows, cols = np.divmod(members, CHUNK_PX)
                pe, pn = east[members], north[members]
                for layer, record in found:
                    values, (west, top_edge) = cache.get(record)
                    col = np.floor((pe - west) / CELL_M).astype('int64')
                    row = np.floor((top_edge - pn) / CELL_M).astype('int64')
                    inside = (row >= 0) & (row < values.shape[0]) & (col >= 0) & (col < values.shape[1])
                    classes[layer, top + rows[inside], left + cols[inside]] = values[row[inside], col[inside]]
    tiles, low = {}, {}
    for z in range(MAX_ZOOM, MIN_ZOOM - 1, -1):
        step = 1 << (MAX_ZOOM - z)
        for layer, name in enumerate(LAYERS):
            sampled = classes[layer, step // 2::step, step // 2::step]
            if z < BLOCK_ZOOM:
                if sampled.any():
                    low[(name, z)] = sampled.copy()
                continue
            count = 1 << (z - BLOCK_ZOOM)
            for ty in range(count):
                for tx in range(count):
                    part = sampled[ty*TILE_PX:(ty+1)*TILE_PX, tx*TILE_PX:(tx+1)*TILE_PX]
                    if part.any():
                        tiles[(name, z, bx*count + tx, by*count + ty)] = encode(part)
    return block, tiles, low


def blocks_for(records, transformer):
    """Zoom-10 tiles touching any downloaded 10 km core, using densified edges."""
    blocks = set()
    edge = np.linspace(0, 1, 21)
    for tile in sorted({r['tile'] for r in records if r['status'] == 'accepted'}):
        w, s = (int(v) for v in tile[1:].split('N'))
        e, n = w + ea.TILE_M, s + ea.TILE_M
        east = np.concatenate([w + (e-w)*edge, np.full(21, e), e - (e-w)*edge, np.full(21, w)])
        north = np.concatenate([np.full(21, s), s + (n-s)*edge, np.full(21, n), n - (n-s)*edge])
        x, y = to_mercator(*transformer.transform(east, north, direction='INVERSE'))
        size = BLOCK_PX * PIXEL_M
        columns = np.floor((x + HALF_WORLD) / size).astype(int)
        rows = np.floor((HALF_WORLD - y) / size).astype(int)
        blocks.update((bx, by) for bx in range(columns.min(), columns.max() + 1)
                      for by in range(rows.min(), rows.max() + 1))
    return sorted(blocks)


def index_digest(db):
    digest, counts = hashlib.sha256(), {}
    for layer, z, x, y, sha in db.execute('SELECT layer, z, x, y, sha256 FROM tile_index ORDER BY layer, z, x, y'):
        digest.update(f'{layer}/{z}/{x}/{y} {sha}\n'.encode())
        counts[layer] = counts.get(layer, 0) + 1
    return digest.hexdigest(), counts


def acquisition_state(acquisition, records, plan):
    """Sealed release identity, or an explicit incomplete-preview state."""
    manifest_path = acquisition / 'manifest.json'
    planned = {t['id'] for t in plan['tiles']}
    per_tile = {}
    for record in records:
        per_tile[record['tile']] = per_tile.get(record['tile'], 0) + 1
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text('utf-8'))
        base = {k: v for k, v in manifest.items() if k != 'release_id'}
        if manifest['release_id'] != 'england-' + hashlib.sha256(json_bytes(base)).hexdigest()[:20]:
            raise MapIntegrityError('Acquisition manifest identity mismatch')
        if json.loads((acquisition / 'records.json').read_text('utf-8')) != records:
            raise MapIntegrityError('Acquisition records differ from its sealed list')
        if manifest['files'].get('records.json') != file_hash(acquisition / 'records.json'):
            raise MapIntegrityError('Acquisition record list changed after sealing')
        return {'status': 'sealed', 'release_id': manifest['release_id'], 'planned_tiles': len(planned),
                'complete_tiles': len(planned)}
    return {'status': 'incomplete_preview', 'release_id': None, 'planned_tiles': len(planned),
            'complete_tiles': sum(per_tile.get(t) == len(LAYERS) for t in planned),
            'started_tiles': len(planned & set(per_tile))}


def build(acquisition, output, *, workers=6, allow_incomplete=False, log=print):
    acquisition, output = Path(acquisition).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    records = ea.load_records(acquisition)
    plan = json.loads((acquisition / 'plan.json').read_text('utf-8'))
    state = acquisition_state(acquisition, records, plan)
    if state['status'] != 'sealed' and not allow_incomplete:
        raise MapIntegrityError('The England acquisition is not sealed yet; build a preview explicitly')
    staging = output.with_name(output.name + '.partial')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    transform_info = best_transformer()
    transformer = pinned_transformer(transform_info)
    blocks = blocks_for(records, transformer)
    log(f'{len(blocks)} map blocks from {state.get("started_tiles", state["planned_tiles"])} squares', flush=True)
    db = sqlite3.connect(staging / 'tiles.sqlite3')
    # The small index is read in full at every server start; image bytes are read per request.
    db.execute('CREATE TABLE tile_data (id INTEGER PRIMARY KEY, png BLOB NOT NULL)')
    db.execute('CREATE TABLE tile_index (layer TEXT, z INTEGER, x INTEGER, y INTEGER, id INTEGER NOT NULL, '
               'sha256 TEXT NOT NULL, PRIMARY KEY (layer, z, x, y)) WITHOUT ROWID')
    low = {}

    def store(tiles):
        for key, data in tiles.items():
            identifier = db.execute('INSERT INTO tile_data (png) VALUES (?)', (data,)).lastrowid
            db.execute('INSERT INTO tile_index VALUES (?, ?, ?, ?, ?, ?)', (*key, identifier, hashlib.sha256(data).hexdigest()))

    def collect(result, done):
        block, tiles, samples = result
        store(tiles)
        for (name, z), sampled in samples.items():
            shift = BLOCK_ZOOM - z
            size = TILE_PX >> shift
            key = (name, z, block[0] >> shift, block[1] >> shift)
            target = low.setdefault(key, np.zeros((TILE_PX, TILE_PX), dtype='uint8'))
            ox, oy = (block[0] & ((1 << shift) - 1)) * size, (block[1] & ((1 << shift) - 1)) * size
            target[oy:oy+size, ox:ox+size] = sampled
        if done % 10 == 0 or done == len(blocks):
            db.commit()
            log(f'{done}/{len(blocks)} map blocks drawn', flush=True)

    if workers:
        with ProcessPoolExecutor(workers, initializer=_start_worker,
                                 initargs=(acquisition, records, transform_info['definition'])) as pool:
            for done, result in enumerate(pool.map(render_block, blocks), 1):
                collect(result, done)
    else:
        _start_worker(acquisition, records, transform_info['definition'])
        for done, block in enumerate(blocks, 1):
            collect(render_block(block), done)
    store({key: encode(classes) for key, classes in low.items() if classes.any()})
    db.commit()
    digest, counts = index_digest(db)
    db.execute('VACUUM')
    db.close()
    (staging / 'records.json').write_bytes(json_bytes(records))
    construction = {}
    for original in [*sorted(Path(__file__).parent.glob('*.py')), ea.REPOSITORY / 'scripts/124_build_england_map.py',
                     ea.REPOSITORY / 'requirements-windows-py314-amd64.lock']:
        target = staging / 'construction' / original.relative_to(ea.REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        construction[target.relative_to(staging).as_posix()] = file_hash(target)
    products = json.loads((acquisition / 'products.json').read_text('utf-8'))
    manifest = {
        'schema': 'quiet-uk-england-map', 'schema_version': 1,
        'title': 'Quiet UK · England transport noise at 10 m',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'acquisition': {**state, 'path': Path('..', acquisition.name).as_posix() if acquisition.parent == output.parent
                        else acquisition.as_posix(), 'plan_sha256': file_hash(acquisition / 'plan.json')},
        'records': {'file': 'records.json', 'sha256': file_hash(staging / 'records.json'), 'count': len(records)},
        'construction': construction,
        'tiles': {'file': 'tiles.sqlite3', 'count': sum(counts.values()), 'by_layer': counts, 'index_sha256': digest,
                  'zooms': [MIN_ZOOM, MAX_ZOOM], 'tile_size': TILE_PX},
        'evidence_contract': CONTRACT_V2,
        'products': {source: {k: products[source][k] for k in ('metadata_id', 'reference_period', 'reference_period_status')}
                     for source in p.PROVIDERS},
        'metrics': p.METRICS, 'units': 'dB(A)', 'receiver_height_m': 4, 'spatial_resolution_m': CELL_M,
        'transformation': transform_info,
        'display': {'colours': p.COLOURS, 'breaks_db': p.BREAKS, 'palette_rgba': PALETTE,
                    'method': 'each display pixel shows the native 10 m cell under its centre, at every zoom',
                    'analytical_use': False},
        'uncertainty': 'No cell-level uncertainty supplied. Grid spacing is not demonstrated accuracy.',
        'unavailable': ['compatible_all_source_total', 'values_below_reporting_cutoffs', 'background_LA90',
                        'event_maxima', 'event_counts'],
        'licence': 'Open Government Licence v3.0',
        'licence_url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
        'attribution': '© Department for Environment, Food & Rural Affairs copyright and/or database right 2023 (road/rail), 2024 (airport).',
    }
    manifest['release_id'] = 'england-map-' + hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    (staging / 'manifest.json').write_bytes(json_bytes(manifest))
    staging.replace(output)
    return manifest


def read_manifest(root):
    manifest = json.loads((Path(root) / 'manifest.json').read_text('utf-8'))
    base = {k: v for k, v in manifest.items() if k != 'release_id'}
    if (manifest.get('schema') != 'quiet-uk-england-map' or manifest.get('schema_version') != 1
            or manifest['release_id'] != 'england-map-' + hashlib.sha256(json_bytes(base)).hexdigest()[:20]):
        raise MapIntegrityError('England map manifest checksum/schema mismatch')
    if manifest['evidence_contract'] != CONTRACT_V2:
        raise MapIntegrityError('Unsupported evidence contract')
    return manifest


class EnglandMap:
    """A verified, read-only England map for serving tiles and point evidence."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read_manifest(self.root)
        self.release_id = self.manifest['release_id']
        if file_hash(self.root / 'records.json') != self.manifest['records']['sha256']:
            raise MapIntegrityError('England map record list changed')
        if any(file_hash(self.root / name) != digest for name, digest in self.manifest['construction'].items()):
            raise MapIntegrityError('England map construction code changed')
        records = json.loads((self.root / 'records.json').read_text('utf-8'))
        self.records = {(r['tile'], layer_key(r)): r for r in records}
        self.acquisition = (self.root / self.manifest['acquisition']['path']).resolve()
        self.transformer = pinned_transformer(self.manifest['transformation'])
        self.lock = threading.Lock()
        self.db = sqlite3.connect(f'file:{(self.root / "tiles.sqlite3").as_posix()}?mode=ro', uri=True,
                                  check_same_thread=False)
        with self.lock:
            digest, _ = index_digest(self.db)
        if digest != self.manifest['tiles']['index_sha256']:
            raise MapIntegrityError('England map tile index changed')
        self.cache = RasterCache(lambda record: raster(self.acquisition, record))

    def close(self):
        self.db.close()

    def public_manifest(self):
        return self.manifest

    def tile(self, layer, z, x, y):
        """PNG bytes, or None where nothing was downloaded; bytes are checked on every read."""
        if layer not in LAYERS:
            raise KeyError('Unknown map layer')
        with self.lock:
            row = self.db.execute('SELECT d.png, i.sha256 FROM tile_index i JOIN tile_data d ON d.id = i.id '
                                  'WHERE i.layer=? AND i.z=? AND i.x=? AND i.y=?', (layer, z, x, y)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise MapIntegrityError('England map tile changed')
        return row[0]

    def cell_windows(self, layer, west, south, east, north):
        """Native-cell images covering a close-up view: 1 km windows, one pixel per cell."""
        if layer not in LAYERS:
            raise KeyError('Unknown map layer')
        if not (np.isfinite([west, south, east, north]).all() and west < east and south < north
                and east - west <= 0.12 and north - south <= 0.08 and -9 <= west and east <= 3 and 49 <= south and north <= 56.5):
            raise ValueError('Native cells are available for close-up views of England only')
        edge = np.linspace(0, 1, 9)
        lons = np.r_[west + (east-west)*edge, west + (east-west)*edge, np.full(9, west), np.full(9, east)]
        lats = np.r_[np.full(9, south), np.full(9, north), south + (north-south)*edge, south + (north-south)*edge]
        e, n = self.transformer.transform(lons, lats)
        side = CELL_WINDOW * CELL_M
        xs = range(math.floor((e.min() - ea.LATTICE[0]) / side), math.floor((e.max() - ea.LATTICE[0]) / side) + 1)
        ys = range(math.floor((n.min() - ea.LATTICE[1]) / side), math.floor((n.max() - ea.LATTICE[1]) / side) + 1)
        if len(xs) * len(ys) > MAX_CELL_WINDOWS:
            raise ValueError('Zoom in further for native cells')
        windows = []
        for wx in xs:
            for wy in ys:
                record = self.window_record(layer, wx, wy)
                if record is None:
                    continue
                w, s = ea.LATTICE[0] + wx * side, ea.LATTICE[1] + wy * side
                lon, lat = self.transformer.transform([w, w + side, w + side, w], [s + side, s + side, s, s], direction='INVERSE')
                windows.append({'id': f'{layer}-{wx}-{wy}', 'url': f'/england-cells/{self.release_id}/{layer}/{wx}/{wy}.png',
                                'corners': [list(c) for c in zip(lon, lat)]})
        return {'release_id': self.release_id, 'layer': layer, 'windows': windows}

    def window_record(self, layer, wx, wy):
        """The accepted record owning a 1 km window that its raster reaches, if any."""
        record = self.records.get((tile_for(wx // (ea.TILE_M // (CELL_WINDOW * CELL_M)),
                                            wy // (ea.TILE_M // (CELL_WINDOW * CELL_M))), layer))
        if record is None or record['status'] != 'accepted':
            return None
        side = CELL_WINDOW * CELL_M
        w, s = ea.LATTICE[0] + wx * side, ea.LATTICE[1] + wy * side
        rw, rs, re, rn = record['qa']['bounds']
        return record if rw < w + side and re > w and rs < s + side and rn > s else None

    def cell_image(self, layer, wx, wy):
        """PNG of one window's native cells, drawn from the verified archive, or None."""
        if layer not in LAYERS:
            raise KeyError('Unknown map layer')
        record = self.window_record(layer, wx, wy)
        if record is None:
            return None
        values, valid, (rw, rt) = self.cache.get(record)
        side = CELL_WINDOW * CELL_M
        col0, row0 = (ea.LATTICE[0] + wx * side - rw) / CELL_M, (rt - ea.LATTICE[1] - (wy + 1) * side) / CELL_M
        if col0 != int(col0) or row0 != int(row0):
            raise MapIntegrityError('Raster is not on the 10 m lattice: ' + record['path'])
        col0, row0 = int(col0), int(row0)
        r0, r1 = max(row0, 0), min(row0 + CELL_WINDOW, values.shape[0])
        c0, c1 = max(col0, 0), min(col0 + CELL_WINDOW, values.shape[1])
        classes = np.zeros((CELL_WINDOW, CELL_WINDOW), dtype='uint8')
        classes[r0-row0:r1-row0, c0-col0:c1-col0] = classify(
            values[r0:r1, c0:c1], valid[r0:r1, c0:c1], zero_rule(record['source'], record['metric']))
        indices = np.where(classes >= BELOW, classes + 1, 0).astype('uint8')
        indices[classes == NODATA] = 1
        return png(indices)

    def lookup(self, lon, lat):
        if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError('Supply finite longitude/latitude in range')
        projectable = -9 <= lon <= 3 and 49 <= lat <= 56.5
        east, north = self.transformer.transform(lon, lat) if projectable else (None, None)
        tile = tile_for(*owner(east, north)) if projectable else None
        observations = []
        for name in LAYERS:
            source, metric = name.split('-')
            record = self.records.get((tile, name))
            raw = row = col = cell = None
            inside = masked = False
            if record is not None and record['status'] == 'accepted':
                values, valid, (west, top) = self.cache.get(record)
                col, row = math.floor((east - west) / CELL_M), math.floor((top - north) / CELL_M)
                inside = 0 <= row < values.shape[0] and 0 <= col < values.shape[1]
                if inside:
                    raw, masked = float(values[row, col]), not bool(valid[row, col])
                    corners = [(west + (col + dx) * CELL_M, top - (row + dy) * CELL_M)
                               for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
                    lons, lats = self.transformer.transform(*zip(*corners), direction='INVERSE')
                    cell = {'type': 'Polygon', 'coordinates': [[list(c) for c in zip(lons, lats)]]}
            rule = zero_rule(source, metric)
            # Defra defines a zero as a calculated result, so a zero cell is inside the calculation.
            calculated = True if inside and not masked and raw == 0 and rule else None
            evidence = interpret(raw if inside else None, inside=inside, masked=masked, domain=calculated,
                                 verified_zero_rule=rule)
            if evidence['value_db'] is not None:
                # Defra publishes 2 decimal places; float32 storage adds no information.
                value = round(evidence['value_db'], 2)
                evidence = {**evidence, 'value_db': value, 'bounds': {**evidence['bounds'], 'lower_db': value, 'upper_db': value}}
            product = self.manifest['products'][source]
            observations.append({
                'source': source, 'metric': metric, 'units': 'dB(A)', **evidence, 'raw_value': raw,
                'reporting_cutoff_db': rule['cutoff_db'] if rule else None,
                'reference_period': product['reference_period'],
                'reference_period_status': product['reference_period_status'],
                'record': None if record is None else {k: record.get(k) for k in ('tile', 'status', 'sha256', 'path', 'http_record')},
                'row': row if inside else None, 'column': col if inside else None, 'cell': cell})
        return {'release_id': self.release_id, 'longitude': lon, 'latitude': lat,
                'easting': east, 'northing': north, 'tile': tile if any(o['record'] for o in observations) else None,
                'evidence_contract_version': CONTRACT_V2['version'],
                'missing_value_policy': CONTRACT_V2['missing_value_policy'],
                'transformation': self.manifest['transformation']['description'],
                'uncertainty': self.manifest['uncertainty'], 'observations': observations}


def verify(root, *, reproduce_blocks=3, log=print):
    """Check every tile's bytes and record archive, and redraw sample blocks exactly."""
    england = EnglandMap(root)
    try:
        with england.lock:
            index = england.db.execute('SELECT layer, z, x, y FROM tile_index').fetchall()
        for key in index:
            england.tile(*key)  # Raises if the bytes no longer match the verified index.
        records = list(england.records.values())
        for record in records:
            if (record['status'] == 'accepted' and hashlib.sha256(
                    ea.read_archive(england.acquisition / record['path'])).hexdigest() != record['sha256']):
                raise MapIntegrityError('Archived response changed: ' + record['path'])
        log(f'{len(index)} tiles and {len(records)} records checked', flush=True)
        _start_worker(england.acquisition, records, england.manifest['transformation']['definition'])
        blocks = blocks_for(records, england.transformer)
        sample = sorted({blocks[0], blocks[len(blocks) // 2], blocks[-1]})[:reproduce_blocks] if blocks else []
        stored = set(index)
        for block in sample:
            _, tiles, _ = render_block(block)
            for key, data in tiles.items():
                if england.tile(*key) != data:
                    raise MapIntegrityError('Tile {}/{}/{}/{} does not reproduce'.format(*key))
            inside = {key for key in stored if key[1] >= BLOCK_ZOOM
                      and key[2] >> (key[1] - BLOCK_ZOOM) == block[0] and key[3] >> (key[1] - BLOCK_ZOOM) == block[1]}
            if inside != set(tiles):
                raise MapIntegrityError('Stored tiles differ from a redraw')
            log(f'block {list(block)}: {len(tiles)} tiles reproduced', flush=True)
        return {'release_id': england.release_id, 'tiles_checked': len(index), 'records': len(records),
                'reproduced_blocks': [list(b) for b in sample], 'acquisition': england.manifest['acquisition']['status']}
    finally:
        england.close()
