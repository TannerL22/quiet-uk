"""England map overview: area-averaged zoomed-out levels and quiet-area hexagons.

Zoomed out, one display pixel covers hundreds or thousands of native 10 m cells.
The England map's own tiles show the single cell under each pixel's centre, which
is exact but turns into speckle. This overview is a separate display product:

* Tiles for zooms 5-13. Each pixel shows the energy average of the native cells
  under it, 10*log10(mean(10^(L/10))), the standard way to average sound levels.
  Cells are sampled at zoom-14 pixel centres (5.4-6.1 m apart, finer than the
  10 m grid), so each cell counts in proportion to its area. Cells below Defra's
  cutoff have no published number and count as silence, so the average is a lower
  bound. A pixel whose average is under the cutoff is drawn as below the cutoff.
  No-data cells are left out of the average; a pixel with no calculated cells at
  all is drawn as no data.
* Hexagons about 5.2 km across on the British National Grid, with exact counts of
  native cells by status for every layer (share below the cutoff, at 55 dB or
  more, at or above the WHO 2018 guideline) and their energy sum.

Both are pictures and summaries for reading the map. Point values, evidence and
research exports always come from the native cells, never from here. The overview
pins the England map release it was built from.
"""
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import threading

import numpy as np
from pyproj import Transformer

from . import england_acquisition as ea
from . import england_map as em
from . import source_pilot as p
from .digests import file_hash, json_bytes
from .evidence_semantics import zero_rule
from .map_presentation import WHO_2018

MIN_ZOOM, MAX_ZOOM = 5, 13
NODATA_CODE, BELOW_CODE, VALUE0 = 1, 2, 3  # 0: no raster here.
HEX_RADIUS_M = 3000  # Centre to corner; 5.196 km across flats, 23.4 km² each.
LOUD_DB = 55
# Energy for every stored value code: 10^(L/10), with L in hundredths of a dB.
ENERGY = np.concatenate([np.zeros(VALUE0), 10.0 ** (np.arange(16001) / 1000.0)])


class OverviewIntegrityError(ValueError):
    pass


def encode_values(values, valid, rule):
    """Native cells as uint16 codes: 1 no data, 2 below the cutoff, 3 + value in hundredths of a dB.

    The same reading as england_map.classify: zero is below the cutoff only under a provider rule."""
    if (valid & ~((values >= 0) & (values <= 160))).any():
        raise OverviewIntegrityError('Unrecognised unmasked noise encoding')
    codes = np.full(values.shape, NODATA_CODE, dtype='uint16')
    reported = valid & (values > 0)
    codes[reported] = VALUE0 + np.rint(values[reported] * 100).astype('uint16')
    if rule is not None:
        codes[valid & (values == 0)] = BELOW_CODE
    return codes


def cutoff(layer):
    source, metric = layer.split('-')
    rule = zero_rule(source, metric)
    return rule['cutoff_db'] if rule else None


def who_limit(layer):
    source, metric = layer.split('-')
    return WHO_2018.get(source, {}).get(metric)


def sums(codes):
    """Energy sum, calculated-cell count and no-data count for one array of codes."""
    return (ENERGY[codes], (codes >= BELOW_CODE).astype('int32'), (codes == NODATA_CODE).astype('int32'))


def reduce(arrays, factor):
    out = []
    for a in arrays:
        n, m = a.shape
        out.append(a.reshape(n // factor, factor, m // factor, factor).sum(axis=(1, 3)))
    return tuple(out)


def classify_average(energy, land, nodata, limit):
    """Display classes (england_map's OUTSIDE/NODATA/BELOW/FIRST_BIN+band) for averaged pixels."""
    classes = np.full(energy.shape, em.OUTSIDE, dtype='uint8')
    classes[nodata > 0] = em.NODATA
    has = land > 0
    level = np.full(energy.shape, -np.inf)
    positive = has & (energy > 0)
    level[positive] = 10 * np.log10(energy[positive] / land[positive])
    classes[has] = em.FIRST_BIN + np.searchsorted(p.BREAKS, level[has], side='right')
    if limit is not None:
        classes[has & (level < limit)] = em.BELOW
    return classes


# ---------------------------------------------------------------- tiles
_WORKER = {}


def _start_worker(acquisition, records, definition):
    em.use_project_grids()  # A fresh worker process must find the pinned grid too.
    accepted = [r for r in records if r['status'] == 'accepted']
    index = {(r['tile'], em.layer_key(r)): r for r in accepted}

    def load(record):
        values, valid, origin = em.raster(acquisition, record)
        return encode_values(values, valid, zero_rule(record['source'], record['metric'])), origin
    _WORKER.update(index=index, cache=em.RasterCache(load, size=48),
                   boxes=np.array([r['qa']['bounds'] for r in accepted], dtype='float64').reshape(-1, 4),
                   transformer=Transformer.from_pipeline(definition))


def sample_block(block):
    """Codes of the native cell under every zoom-14 pixel centre of one zoom-10 tile, per layer.

    The same pixel-to-cell rule as england_map.render_block."""
    bx, by = block
    index, cache, transformer, boxes = (_WORKER[k] for k in ('index', 'cache', 'transformer', 'boxes'))
    codes = np.zeros((len(em.LAYERS), em.BLOCK_PX, em.BLOCK_PX), dtype='uint16')
    edge = np.r_[0:em.CHUNK_PX:32, em.CHUNK_PX - 1]
    for top in range(0, em.BLOCK_PX, em.CHUNK_PX):
        lat = em.to_lonlat(0, em.HALF_WORLD - (by * em.BLOCK_PX + top + np.arange(em.CHUNK_PX) + .5) * em.PIXEL_M)[1]
        for left in range(0, em.BLOCK_PX, em.CHUNK_PX):
            lon = em.to_lonlat(-em.HALF_WORLD + (bx * em.BLOCK_PX + left + np.arange(em.CHUNK_PX) + .5) * em.PIXEL_M, 0)[0]
            ring_lon = np.r_[lon[edge], lon[edge], np.full(edge.size, lon[0]), np.full(edge.size, lon[-1])]
            ring_lat = np.r_[np.full(edge.size, lat[0]), np.full(edge.size, lat[-1]), lat[edge], lat[edge]]
            ring_e, ring_n = transformer.transform(ring_lon, ring_lat)
            w, s = ring_e.min() - em.CELL_M, ring_n.min() - em.CELL_M
            e, n = ring_e.max() + em.CELL_M, ring_n.max() + em.CELL_M
            if not ((boxes[:, 0] < e) & (boxes[:, 2] > w) & (boxes[:, 1] < n) & (boxes[:, 3] > s)).any():
                continue
            east, north = transformer.transform(*np.meshgrid(lon, lat))
            if not (np.isfinite(east).all() and np.isfinite(north).all()):
                raise OverviewIntegrityError('Coordinate transformation failed inside a map block')
            east, north = east.ravel(), north.ravel()
            i, j = em.owner(east, north)
            keys, inverse = np.unique(i * 1_000_000 + j, return_inverse=True)
            order = np.argsort(inverse, kind='stable')
            ends = np.cumsum(np.bincount(inverse, minlength=len(keys)))
            for k, key in enumerate(keys):
                tile = em.tile_for(*divmod(int(key), 1_000_000))
                found = [(layer, index[(tile, name)]) for layer, name in enumerate(em.LAYERS) if (tile, name) in index]
                if not found:
                    continue
                members = order[(ends[k-1] if k else 0):ends[k]]
                rows, cols = np.divmod(members, em.CHUNK_PX)
                pe, pn = east[members], north[members]
                for layer, record in found:
                    values, (west, top_edge) = cache.get(record)
                    col = np.floor((pe - west) / em.CELL_M).astype('int64')
                    row = np.floor((top_edge - pn) / em.CELL_M).astype('int64')
                    inside = (row >= 0) & (row < values.shape[0]) & (col >= 0) & (col < values.shape[1])
                    codes[layer, top + rows[inside], left + cols[inside]] = values[row[inside], col[inside]]
    return codes


def render_block(block):
    """Averaged tiles for zooms 13-10 inside one zoom-10 tile, plus zoom-9 sums for lower zooms."""
    bx, by = block
    codes = sample_block(block)
    tiles, low = {}, {}
    for layer, name in enumerate(em.LAYERS):
        if not codes[layer].any():
            continue
        limit = cutoff(name)
        current = reduce(sums(codes[layer]), 2)  # Zoom 13.
        for z in range(MAX_ZOOM, em.BLOCK_ZOOM - 1, -1):
            if z < MAX_ZOOM:
                current = reduce(current, 2)
            classes = classify_average(*current, limit)
            count = 1 << (z - em.BLOCK_ZOOM)
            for ty in range(count):
                for tx in range(count):
                    part = classes[ty*em.TILE_PX:(ty+1)*em.TILE_PX, tx*em.TILE_PX:(tx+1)*em.TILE_PX]
                    if part.any():
                        tiles[(name, z, bx*count + tx, by*count + ty)] = em.encode(part)
        low[name] = reduce(current, 2)  # Zoom 9: 128 x 128 sums.
    return block, tiles, low


def index_digest(db):
    return em.index_digest(db)


# ---------------------------------------------------------------- hexagons
def hex_keys(east, north, radius=HEX_RADIUS_M):
    """Pointy-top axial (q, r) of the hexagon containing each point, by cube rounding."""
    q = (math.sqrt(3) / 3 * east - north / 3) / radius
    r = (2 / 3 * north) / radius
    x, z = q, r
    y = -x - z
    rx, ry, rz = np.rint(x), np.rint(y), np.rint(z)
    dx, dy, dz = np.abs(rx - x), np.abs(ry - y), np.abs(rz - z)
    fix_x = (dx > dy) & (dx > dz)
    fix_z = ~fix_x & ~(dy > dz)
    rx = np.where(fix_x, -ry - rz, rx)
    rz = np.where(fix_z, -rx - ry, rz)
    return rx.astype('int64'), rz.astype('int64')


def hex_corners(q, r, radius=HEX_RADIUS_M):
    cx, cy = radius * math.sqrt(3) * (q + r / 2), radius * 1.5 * r
    return [(cx + radius * math.cos(math.radians(60 * i - 30)), cy + radius * math.sin(math.radians(60 * i - 30)))
            for i in range(6)]


HEX_COLUMNS = ('value_cells', 'below_cells', 'nodata_cells', 'energy_sum', 'cells_ge_55', 'cells_ge_who')


def _square_core(record, acquisition):
    """One square's own 1000 x 1000 cells as codes, from its archived response."""
    values, valid, (west, top) = em.raster(acquisition, record)
    w, s = (int(v) for v in record['tile'][1:].split('N'))
    c0, r0 = round((w - west) / em.CELL_M), round((top - (s + ea.TILE_M)) / em.CELL_M)
    n = ea.TILE_M // em.CELL_M
    codes = np.zeros((n, n), dtype='uint16')
    rs, cs = max(r0, 0), max(c0, 0)
    re, ce = min(r0 + n, values.shape[0]), min(c0 + n, values.shape[1])
    if re > rs and ce > cs:
        codes[rs-r0:re-r0, cs-c0:ce-c0] = encode_values(values[rs:re, cs:ce], valid[rs:re, cs:ce],
                                                        zero_rule(record['source'], record['metric']))
    return codes


def square_hexes(job):
    """Per-hexagon cell counts for every layer of one 10 km square."""
    acquisition, tile, records = job
    w, s = (int(v) for v in tile[1:].split('N'))
    n = ea.TILE_M // em.CELL_M
    east = w + em.CELL_M / 2 + em.CELL_M * np.arange(n)
    north = s + ea.TILE_M - em.CELL_M / 2 - em.CELL_M * np.arange(n)
    q, r = hex_keys(*np.meshgrid(east, north))
    keys, inverse = np.unique(q * 1_000_000 + r, return_inverse=True)
    inverse = inverse.ravel()
    out = {}
    for name, record in records.items():
        codes = _square_core(record, acquisition).ravel()
        if not codes.any():
            continue
        value = codes >= VALUE0
        limit = who_limit(name)
        counts = [np.bincount(inverse, weights=w, minlength=len(keys)) for w in (
            value, codes == BELOW_CODE, codes == NODATA_CODE, ENERGY[codes],
            value & (codes >= VALUE0 + LOUD_DB * 100),
            value & (codes >= VALUE0 + limit * 100) if limit is not None else np.zeros(codes.size, bool))]
        out[name] = (keys, np.stack(counts, axis=1))
    return tile, out


def build_hexes(acquisition, records, transformer, *, workers, log):
    accepted = {}
    for r in records:
        if r['status'] == 'accepted':
            accepted.setdefault(r['tile'], {})[em.layer_key(r)] = r
    jobs = [(acquisition, tile, accepted[tile]) for tile in sorted(accepted)]
    totals = {name: {} for name in em.LAYERS}

    def collect(result, done):
        _, out = result
        for name, (keys, counts) in out.items():
            table = totals[name]
            for key, row in zip(keys.tolist(), counts):
                if key in table:
                    table[key] = table[key] + row
                else:
                    table[key] = row.copy()
        if done % 100 == 0 or done == len(jobs):
            log(f'{done}/{len(jobs)} squares summarised into hexagons', flush=True)

    if workers:
        with ProcessPoolExecutor(workers) as pool:
            for done, result in enumerate(pool.map(square_hexes, jobs, chunksize=4), 1):
                collect(result, done)
    else:
        for done, job in enumerate(jobs, 1):
            collect(square_hexes(job), done)
    keys = sorted({k for table in totals.values() for k, row in table.items() if row[0] + row[1] > 0})
    hexes = []
    for key in keys:
        q, r = divmod(key, 1_000_000)  # r >= 0 everywhere on the National Grid, so floor division is exact.
        corners = hex_corners(q, r)
        lon, lat = transformer.transform(*zip(*corners), direction='INVERSE')
        hexes.append({'q': q, 'r': r, 'ring': [[round(a, 6), round(b, 6)] for a, b in zip(lon, lat)]})
    layers = {}
    for name in em.LAYERS:
        table = totals[name]
        layers[name] = [None if key not in table or table[key][0] + table[key][1] == 0 else
                        [int(v) for v in table[key][:3]] + [float(table[key][3])] + [int(v) for v in table[key][4:]]
                        for key in keys]
    return {'radius_m': HEX_RADIUS_M, 'orientation': 'pointy-top axial on EPSG:27700', 'columns': list(HEX_COLUMNS),
            'loud_db': LOUD_DB, 'who_2018': {name: who_limit(name) for name in em.LAYERS},
            'cutoffs': {name: cutoff(name) for name in em.LAYERS}, 'hexes': hexes, 'layers': layers}


def hex_features(hexes, layer):
    """GeoJSON for one layer: shares of each hexagon's calculated cells, and the energy average."""
    features = []
    for i, (h, row) in enumerate(zip(hexes['hexes'], hexes['layers'][layer])):
        if row is None:
            continue
        value, below, nodata, energy, loud, who = row
        land = value + below
        if land == 0:
            continue
        limit = hexes['who_2018'][layer]
        features.append({'type': 'Feature', 'id': i, 'geometry': {'type': 'Polygon', 'coordinates': [h['ring'] + [h['ring'][0]]]},
                         'properties': {'quiet': below / land if hexes['cutoffs'][layer] is not None else None,
                                        'loud': loud / land, 'who': who / land if limit is not None else None,
                                        'mean_db': round(10 * math.log10(energy / land), 2) if energy > 0 else None,
                                        'land_km2': round(land * em.CELL_M ** 2 / 1e6, 3),
                                        'nodata_km2': round(nodata * em.CELL_M ** 2 / 1e6, 3)}})
    return {'type': 'FeatureCollection', 'features': features}


# ---------------------------------------------------------------- build, read, verify
def build(map_root, output, *, workers=6, log=print):
    map_root, output = Path(map_root).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    england = em.EnglandMap(map_root)  # Verifies the map's manifest, index, records and transformation.
    records = list(england.records.values())
    acquisition, definition = england.acquisition, england.manifest['transformation']['definition']
    england.close()
    staging = output.with_name(output.name + '.partial')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    # The code is recorded when the build starts: the exact files that run, even if they are edited meanwhile.
    construction = {}
    for original in [*sorted(Path(__file__).parent.glob('*.py')), ea.REPOSITORY / 'scripts/125_build_england_overview.py',
                     ea.REPOSITORY / 'requirements-windows-py314-amd64.lock']:
        target = staging / 'construction' / original.relative_to(ea.REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
        construction[target.relative_to(staging).as_posix()] = file_hash(target)
    blocks = em.blocks_for(records, Transformer.from_pipeline(definition))
    log(f'{len(blocks)} overview blocks', flush=True)
    db = sqlite3.connect(staging / 'tiles.sqlite3')
    db.execute('CREATE TABLE tile_data (id INTEGER PRIMARY KEY, png BLOB NOT NULL)')
    db.execute('CREATE TABLE tile_index (layer TEXT, z INTEGER, x INTEGER, y INTEGER, id INTEGER NOT NULL, '
               'sha256 TEXT NOT NULL, PRIMARY KEY (layer, z, x, y)) WITHOUT ROWID')
    low = {}

    def store(tiles):
        for key, data in tiles.items():
            identifier = db.execute('INSERT INTO tile_data (png) VALUES (?)', (data,)).lastrowid
            db.execute('INSERT INTO tile_index VALUES (?, ?, ?, ?, ?, ?)', (*key, identifier, hashlib.sha256(data).hexdigest()))

    def accumulate(name, z, px, py, arrays):
        """Add sums whose top-left pixel is (px, py) at zoom z into that zoom's 256-pixel tiles."""
        size = arrays[0].shape[0]
        key = (name, z, px // em.TILE_PX, py // em.TILE_PX)
        target = low.setdefault(key, (np.zeros((em.TILE_PX, em.TILE_PX)), np.zeros((em.TILE_PX, em.TILE_PX), 'int32'),
                                      np.zeros((em.TILE_PX, em.TILE_PX), 'int32')))
        ox, oy = px % em.TILE_PX, py % em.TILE_PX
        for t, a in zip(target, arrays):
            t[oy:oy+size, ox:ox+size] += a

    def collect(result, done):
        (bx, by), tiles, sums9 = result
        store(tiles)
        for name, arrays in sums9.items():
            accumulate(name, em.BLOCK_ZOOM - 1, bx * 128, by * 128, arrays)
        if done % 10 == 0 or done == len(blocks):
            db.commit()
            log(f'{done}/{len(blocks)} overview blocks drawn', flush=True)

    if workers:
        with ProcessPoolExecutor(workers, initializer=_start_worker, initargs=(acquisition, records, definition)) as pool:
            for done, result in enumerate(pool.map(render_block, blocks), 1):
                collect(result, done)
    else:
        _start_worker(acquisition, records, definition)
        for done, block in enumerate(blocks, 1):
            collect(render_block(block), done)
    for z in range(em.BLOCK_ZOOM - 1, MIN_ZOOM - 1, -1):
        level = sorted(k for k in low if k[1] == z)
        for key in level:
            name, _, x, y = key
            arrays = low.pop(key)
            part = classify_average(*arrays, cutoff(name))
            if part.any():
                store({key: em.encode(part)})
            if z > MIN_ZOOM:
                accumulate(name, z - 1, x * 128, y * 128, reduce(arrays, 2))
        db.commit()
    digest, counts = index_digest(db)
    db.execute('VACUUM')
    db.close()
    log('summarising hexagons', flush=True)
    hexes = build_hexes(acquisition, records, Transformer.from_pipeline(definition), workers=workers, log=log)
    (staging / 'hexes.json').write_bytes(json_bytes(hexes))
    manifest = {
        'schema': 'quiet-uk-england-overview', 'schema_version': 1,
        'title': 'Quiet UK · England noise overview (area averages and hexagons)',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'map': {'release_id': england.release_id, 'manifest_sha256': file_hash(map_root / 'manifest.json')},
        'tiles': {'file': 'tiles.sqlite3', 'count': sum(counts.values()), 'by_layer': counts, 'index_sha256': digest,
                  'zooms': [MIN_ZOOM, MAX_ZOOM], 'tile_size': em.TILE_PX},
        'hexes': {'file': 'hexes.json', 'sha256': file_hash(staging / 'hexes.json'), 'count': len(hexes['hexes']),
                  'radius_m': HEX_RADIUS_M, 'loud_db': LOUD_DB},
        'method': {
            'tiles': 'Each pixel is the energy average 10*log10(mean(10^(L/10))) of the native 10 m cells under it, '
                     'sampled at zoom-14 pixel centres so each cell counts in proportion to its area. Cells below the '
                     'provider cutoff count as silence (a lower bound); an average under the cutoff is drawn as below '
                     'it. No-data cells are left out; a pixel with no calculated cells is no data.',
            'hexes': 'Exact counts of native cells by status in hexagons about 5.2 km across on the British National '
                     'Grid (each cell assigned by its centre), with the energy sum of reported values.',
            'analytical_use': False,
        },
        'construction': construction,
    }
    manifest['release_id'] = 'england-overview-' + hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    (staging / 'manifest.json').write_bytes(json_bytes(manifest))
    staging.replace(output)
    return manifest


def read_manifest(root):
    manifest = json.loads((Path(root) / 'manifest.json').read_text('utf-8'))
    base = {k: v for k, v in manifest.items() if k != 'release_id'}
    if (manifest.get('schema') != 'quiet-uk-england-overview' or manifest.get('schema_version') != 1
            or manifest['release_id'] != 'england-overview-' + hashlib.sha256(json_bytes(base)).hexdigest()[:20]):
        raise OverviewIntegrityError('England overview manifest checksum/schema mismatch')
    return manifest


class EnglandOverview:
    """A verified, read-only overview, valid only beside the England map release it was built from."""

    def __init__(self, root, england):
        self.root = Path(root).resolve()
        self.manifest = read_manifest(self.root)
        self.release_id = self.manifest['release_id']
        if self.manifest['map']['release_id'] != england.release_id:
            raise OverviewIntegrityError('The overview was built from a different England map release')
        if any(file_hash(self.root / name) != digest for name, digest in self.manifest['construction'].items()):
            raise OverviewIntegrityError('England overview construction code changed')
        if file_hash(self.root / 'hexes.json') != self.manifest['hexes']['sha256']:
            raise OverviewIntegrityError('England overview hexagons changed')
        self.hexes = json.loads((self.root / 'hexes.json').read_text('utf-8'))
        self.lock = threading.Lock()
        self.db = sqlite3.connect(f'file:{(self.root / "tiles.sqlite3").as_posix()}?mode=ro', uri=True, check_same_thread=False)
        with self.lock:
            digest, _ = index_digest(self.db)
        if digest != self.manifest['tiles']['index_sha256']:
            raise OverviewIntegrityError('England overview tile index changed')
        self.features = {}

    def close(self):
        self.db.close()

    def public(self):
        return {k: self.manifest[k] for k in ('release_id', 'tiles', 'hexes', 'method', 'map')}

    def tile(self, layer, z, x, y):
        if layer not in em.LAYERS:
            raise KeyError('Unknown map layer')
        with self.lock:
            row = self.db.execute('SELECT d.png, i.sha256 FROM tile_index i JOIN tile_data d ON d.id = i.id '
                                  'WHERE i.layer=? AND i.z=? AND i.x=? AND i.y=?', (layer, z, x, y)).fetchone()
        if row is None:
            return None
        if hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise OverviewIntegrityError('England overview tile changed')
        return row[0]

    def hex_layer(self, layer):
        if layer not in em.LAYERS:
            raise KeyError('Unknown map layer')
        with self.lock:
            if layer not in self.features:
                self.features[layer] = json_bytes({'release_id': self.release_id, 'layer': layer,
                                                   'loud_db': LOUD_DB, 'who_2018_db': who_limit(layer),
                                                   'cutoff_db': cutoff(layer), **hex_features(self.hexes, layer)})
            return self.features[layer]


def verify(root, map_root, *, reproduce_blocks=3, workers=6, log=print):
    """Check every tile's bytes, redraw sample blocks exactly, and recount the hexagons."""
    england = em.EnglandMap(map_root)
    overview = EnglandOverview(root, england)
    try:
        with overview.lock:
            index = overview.db.execute('SELECT layer, z, x, y FROM tile_index').fetchall()
        for key in index:
            overview.tile(*key)
        log(f'{len(index)} overview tiles checked', flush=True)
        records = list(england.records.values())
        definition = england.manifest['transformation']['definition']
        _start_worker(england.acquisition, records, definition)
        blocks = em.blocks_for(records, Transformer.from_pipeline(definition))
        step = max(1, len(blocks) // reproduce_blocks)
        for block in blocks[::step][:reproduce_blocks]:
            _, tiles, _ = render_block(block)
            for key, data in tiles.items():
                if overview.tile(*key) != data:
                    raise OverviewIntegrityError(f'Overview tile {key} does not reproduce')
        log(f'{min(reproduce_blocks, len(blocks))} blocks redrawn exactly', flush=True)
        hexes = build_hexes(england.acquisition, records, Transformer.from_pipeline(definition), workers=workers, log=log)
        if json_bytes(hexes) != (overview.root / 'hexes.json').read_bytes():
            raise OverviewIntegrityError('Hexagon counts do not reproduce')
        log('hexagons recounted exactly', flush=True)
        return {'release_id': overview.release_id, 'tiles': len(index), 'hexes': len(hexes['hexes']), 'verified': True}
    finally:
        overview.close()
        england.close()
