"""Native 10 m road, rail and aircraft rasters for every England land tile.

Continues the sealed Oxford–London acquisition's 10 km lattice across every tile
that touches England land; its 450 rasters are reused, not downloaded again.
Accepted provider responses are archived byte-exact with zstd, so values, masks
and response hashes are unchanged. Provider rectangles constrain requests but
never certify calculation domains.
"""
from compression import zstd
import copy
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

import numpy as np
import rasterio
from rasterio.io import MemoryFile
import requests

from . import source_pilot as p
from . import tiled_canary as c
from .budget_capture import BudgetCapture, AcquisitionStopped, atomic_json
from .locking import ResourceLock
from .provider_audit import description

REPOSITORY = Path(__file__).resolve().parents[2]
MASK = 'data/processed/england_mask/england_100m_mask.tif'
TILE_M = 10_000
# Oxford–London cell edges (445005, 165005) reduced modulo the tile size.
LATTICE = (5005, 5005)
LIMITS = {'http_attempts': 20_000, 'transfer_bytes': 160*1024**3, 'response_bytes': 32*1024**2,
          'attempts_per_request': 5, 'interval_seconds': 1.0, 'min_free_disk_bytes': 20*1024**3}
RETRY_WAIT_SECONDS = (60, 300, 900)
# Budget and integrity stops end the run; anything else is retried, then set aside.
FATAL = ('Cumulative', 'Free disk', 'identity mismatch', 'response changed')


def tile_id(west, south):
    return f'E{west}N{south}'


def land_tiles(mask_path):
    """Lattice tiles touching any land cell of the 100 m England mask."""
    with rasterio.open(mask_path) as ds:
        t = ds.transform
        if (ds.crs != rasterio.crs.CRS.from_epsg(27700) or (t.a, t.b, t.d, t.e) != (100, 0, 0, -100)
                or t.c % 100 or t.f % 100):
            raise ValueError('Expected the 100 m EPSG:27700 England land mask')
        rows, cols = np.nonzero(ds.read(1) == 1)
    west, north = (t.c + cols*100).astype('int64'), (t.f - rows*100).astype('int64')
    # Integer cell edges never coincide with lattice edges (…5), so a cell
    # spans at most two tiles per axis.
    columns = [(west-LATTICE[0])//TILE_M, (west+99-LATTICE[0])//TILE_M]
    lines = [(north-100-LATTICE[1])//TILE_M, (north-1-LATTICE[1])//TILE_M]
    keys = set()
    for i in columns:
        for j in lines:
            keys.update(zip(i.tolist(), j.tolist()))
    return sorted(keys, key=lambda k: (k[1], k[0]))


def plan(mask_path, parent_plan):
    """Tiles in south-to-north, west-to-east order; parent tiles keep their halos."""
    inherited = {tuple(t['core_bounds']): t for t in parent_plan['tiles']}
    tiles = []
    for i, j in land_tiles(mask_path):
        w, s = LATTICE[0]+i*TILE_M, LATTICE[1]+j*TILE_M
        core = [w, s, w+TILE_M, s+TILE_M]
        tile = {'id': tile_id(w, s), 'core_bounds': core, 'halo_bounds': [w-10, s-10, w+TILE_M+10, s+TILE_M+10]}
        if tuple(core) in inherited:
            # Preserve the parent's request bytes; new neighbours supply the seam strip.
            tile.update(halo_bounds=inherited[tuple(core)]['halo_bounds'], parent_tile=inherited[tuple(core)]['id'])
        tiles.append(tile)
    if sum('parent_tile' in t for t in tiles) != len(parent_plan['tiles']):
        raise ValueError('Every Oxford–London tile must lie on the England lattice')
    return {'schema_version': 1, 'name': 'England native 10 m tiles', 'crs': 'EPSG:27700',
            'tile_m': TILE_M, 'halo_m': 10, 'lattice_origin': list(LATTICE),
            'mask': {'path': MASK, 'sha256': p.file_hash(mask_path), 'rule': 'tile touches any 100 m land cell'},
            'tiles': tiles, 'sources': list(p.PROVIDERS), 'metrics': list(p.METRICS),
            'planned_tiles': len(tiles), 'area_km2': len(tiles)*TILE_M**2//1_000_000,
            'limits': dict(LIMITS), 'status': 'verified_extraction_not_public_exposure_release'}


def record_name(tile, source, metric):
    return f'records/{tile}/{source}-{metric}.json'


def archive_name(tile, source, metric):
    return f'archive/{tile}/{source}-{metric}.tif.zst'


def read_archive(path):
    return zstd.decompress(Path(path).read_bytes())


def write_archive(target, data):
    target.parent.mkdir(parents=True, exist_ok=True)
    pending = target.with_suffix('.pending')
    pending.write_bytes(zstd.compress(data, level=10))
    if read_archive(pending) != data:
        raise AcquisitionStopped('Compressed archive does not reproduce the response')
    pending.replace(target)


def open_record(root, record):
    """An in-memory raster of the exact provider response; the caller closes it."""
    data = read_archive(Path(root)/record['path'])
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('Archived response changed: '+record['path'])
    return MemoryFile(data)


def keep_awake(active):
    """Stop idle sleep while downloading. Lid close or Start > Sleep still sleeps."""
    if os.name == 'nt':
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        kernel32.SetThreadExecutionState(0x80000000 | (0x1 if active else 0))


class Capture(BudgetCapture):
    """BudgetCapture with usage counted once, not re-read from every journal per request."""
    def __init__(self, root, limits, **kwargs):
        super().__init__(root, limits, **kwargs)
        self.counted = BudgetCapture.usage(self)

    def usage(self):
        return dict(self.counted)

    def fetch(self, url, params=None, *, required=True):
        prepared = requests.Request('GET', url, params=params).prepare().url
        folder = self.root/'attempts'/hashlib.sha256(prepared.encode()).hexdigest()
        before = set(folder.glob('*.json'))
        try:
            return super().fetch(url, params, required=required)
        finally:
            for path in set(folder.glob('*.json'))-before:
                record = json.loads(path.read_text('utf-8'))
                self.counted['http_attempts'] += 1
                self.counted['charged_bytes'] += record['bytes'] if record['state'] == 'finished' else self.limits['response_bytes']


PARENT_FILES = ('manifest.json', 'plan.json', 'products.json', 'records.json')


def parent_copy(root):
    """The copied parent identity, checked against its own sealed manifest."""
    parent = Path(root)/'parent'
    manifest = json.loads((parent/'manifest.json').read_text('utf-8'))
    base = {k: v for k, v in manifest.items() if k != 'release_id'}
    if manifest['release_id'] != 'canary-'+hashlib.sha256(p.json_bytes(base)).hexdigest()[:20]:
        raise ValueError('Parent manifest checksum mismatch')
    for path in parent.rglob('*'):
        name = path.relative_to(parent).as_posix()
        if path.is_file() and name != 'manifest.json' and manifest['files'].get(name) != p.file_hash(path):
            raise ValueError('Copied parent evidence changed: '+name)
    read = lambda name: json.loads((parent/name).read_text('utf-8'))
    return manifest, read('plan.json'), read('products.json'), read('records.json')


def reused_record(original, tile):
    return {**original, 'tile': tile, 'parent_tile': original['tile'], 'parent_path': original['path'],
            'path': archive_name(tile, original['source'], original['metric']),
            'http_record': 'parent/'+original['http_record'], 'archive': 'zstd'}


def seed(parent_root, root, reference, mask_path=REPOSITORY/MASK):
    """Verify the sealed Oxford–London acquisition and archive its rasters as reused."""
    parent_root, root = Path(parent_root).resolve(), Path(root).resolve()
    if root.is_relative_to(parent_root) or parent_root.is_relative_to(root):
        raise ValueError('England acquisition must have a separate destination')
    c.verify(parent_root, reference)
    manifest = json.loads((parent_root/'manifest.json').read_text('utf-8'))
    with ResourceLock(root, 'england seed'):
        if root.exists():
            raise FileExistsError('Use a new England destination')
        root.mkdir(parents=True)
        records = json.loads((parent_root/'records.json').read_text('utf-8'))
        metadata = [*PARENT_FILES[1:], *sorted({n for n in manifest['files'] if n.startswith('evidence/')}),
                    *[r['http_record'] for r in records]]
        for name in [*metadata, 'manifest.json']:
            target = root/'parent'/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(parent_root/name, target)
        _, parent_plan, _, parent_records = parent_copy(root)
        recipe = plan(mask_path, parent_plan)
        atomic_json(root/'plan.json', recipe)
        tiles = {t['parent_tile']: t['id'] for t in recipe['tiles'] if 'parent_tile' in t}
        for original in parent_records:
            if original['status'] != 'accepted':
                raise ValueError('Parent must contain only accepted rasters')
            record = reused_record(original, tiles[original['tile']])
            data = (parent_root/original['path']).read_bytes()
            if hashlib.sha256(data).hexdigest() != original['sha256']:
                raise ValueError('Parent raster changed while seeding')
            write_archive(root/record['path'], data)
            target = root/record_name(record['tile'], record['source'], record['metric'])
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(target, record)
    return {'status': 'seeded', 'tiles': len(recipe['tiles']), 'reused_rasters': len(parent_records),
            'planned_rasters': len(recipe['tiles'])*9, 'area_km2': recipe['area_km2']}


def load_records(root):
    return [json.loads(path.read_text('utf-8')) for path in sorted((Path(root)/'records').glob('*/*.json'))]


def check_recipe(root, mask_path):
    _, parent_plan, parent_products, _ = parent_copy(root)
    recipe = json.loads((Path(root)/'plan.json').read_text('utf-8'))
    if recipe != plan(mask_path, parent_plan):
        raise ValueError('England plan changed')
    return recipe, parent_products


def coverage_name(identifier):
    """The published coverage name, whichever WCS protocol prefix the server advertises."""
    return identifier.replace('__', ':').rsplit(':', 1)[-1]


def pinned(parent_products):
    """Oxford–London's exact request recipes; their descriptions live in the parent copy."""
    products = copy.deepcopy(parent_products)
    for product in products.values():
        product['metadata_file'] = 'parent/'+product['metadata_file']
        for request in product['requests_by_metric'].values():
            request['description_file'] = 'parent/'+request['description_file']
    return products


def live_inventory(root, capture):
    products = p.inventory(root, capture_response=capture)
    for product in products.values():
        product['reference_period'] = p.provider_period((root/product['metadata_file']).read_text('utf-8'), product['metadata_id'])
        product['reference_period_status'] = 'provider_declared' if product['reference_period'] else 'unspecified_by_provider'
    return products


def check_inventory(root, inventory, parent_products):
    """The live catalogue must still publish the same datasets, periods, grids and envelopes.

    Only the WCS protocol prefix of a coverage identifier may differ; downloads keep
    Oxford–London's exact recipes either way.
    """
    for source in p.PROVIDERS:
        now, then = inventory[source], parent_products[source]
        for metric in p.METRICS:
            a, b = now['requests_by_metric'][metric], then['requests_by_metric'][metric]
            if (now['metadata_id'] != then['metadata_id'] or now['reference_period'] != then['reference_period']
                    or coverage_name(a['coverage_id']) != coverage_name(b['coverage_id'])):
                raise AcquisitionStopped('Provider product or period differs from Oxford–London; review before mixing')
            fresh, old = description(Path(root)/a['description_file']), description(Path(root)/'parent'/b['description_file'])
            if (fresh['status'], fresh.get('grid'), fresh.get('envelope')) != (old['status'], old.get('grid'), old.get('envelope')):
                raise AcquisitionStopped('Provider grid or envelope differs from Oxford–London; review before mixing')


def probe_match(root, original, body):
    """Compare a fresh response for an Oxford–London request with the archived original."""
    if p.file_hash(body) == original['sha256']:
        return 'byte_identical'
    with open_record(root, original) as memory:
        c.compare_window(memory.name, body, original['request_bounds'])
    return 'values_masks_and_encoding_identical'


def fetch_with_retries(capture, product, metric, bounds, label, sleep, log):
    """(body, journal), or None once a layer's transient failures are set aside."""
    for wait in (*RETRY_WAIT_SECONDS, None):
        try:
            return capture.fetch(product['endpoint'], p.coverage_params(product, metric, bounds, max_cells=1002))
        except AcquisitionStopped as exc:
            if any(word in str(exc) for word in FATAL):
                raise
            if wait is None or 'Per-request' in str(exc):
                log(f'{label} set aside: {exc}', flush=True)
                return None
            log(f'{label}: {exc}; retrying in {wait} s', flush=True)
            sleep(wait)


def probe(root, capture, products, sleep, log):
    """Re-fetch one Oxford–London raster per layer with the pinned recipe before downloading."""
    reused = [r for r in load_records(root) if 'parent_tile' in r]
    results = {}
    for source, product in products.items():
        for metric in p.METRICS:
            original = next(r for r in reused if r['source'] == source and r['metric'] == metric)
            fetched = fetch_with_retries(capture, product, metric, original['request_bounds'], f'probe {source}/{metric}', sleep, log)
            if fetched is None:
                raise AcquisitionStopped(f'Consistency probe {source} {metric} unavailable; run again later')
            body, journal = fetched
            try:
                match = probe_match(root, original, body)
            except (ValueError, rasterio.errors.RasterioError) as exc:
                # Retained as evidence; a later run asks again rather than reusing it.
                http = json.loads(journal.read_text('utf-8')); http['rejected'] = True
                atomic_json(journal, http)
                raise AcquisitionStopped(f'{source} {metric} no longer reproduces Oxford–London ({exc}); review before mixing') from exc
            results[f'{source}-{metric}'] = {'tile': original['tile'], 'http_record': journal.relative_to(root).as_posix(),
                                             'body': body.relative_to(root).as_posix(), 'match': match}
    return results


def descriptions(root, products):
    return {(source, metric): description(Path(root)/request['description_file'])
            for source, product in products.items() for metric, request in product['requests_by_metric'].items()}


def acquire(root, *, mask_path=REPOSITORY/MASK, max_new_rasters=None, get=None, sleep=time.sleep, log=print):
    """Download every missing raster; resumable. A STOP file in root stops it cleanly."""
    root = Path(root).resolve()
    with ResourceLock(root, 'england acquisition'):
        if (root/'manifest.json').exists():
            raise FileExistsError('England acquisition already sealed')
        recipe, parent_products = check_recipe(root, mask_path)
        capture = Capture(root, recipe['limits'], get=get, sleep=sleep)
        if not (root/'inventory.json').exists():
            inventory = live_inventory(root, capture)
            check_inventory(root, inventory, parent_products)
            atomic_json(root/'inventory.json', inventory)
        check_inventory(root, json.loads((root/'inventory.json').read_text('utf-8')), parent_products)
        products = pinned(parent_products)
        if not (root/'products.json').exists():
            atomic_json(root/'products.json', products)
        if json.loads((root/'products.json').read_text('utf-8')) != products:
            raise ValueError('Pinned request recipes changed')
        if not (root/'probes.json').exists():
            atomic_json(root/'probes.json', probe(root, capture, products, sleep, log))
        info = descriptions(root, products)
        done = {(r['tile'], r['source'], r['metric']) for r in load_records(root)}
        total = len(recipe['tiles'])*len(products)*len(p.METRICS)
        failures, new, started = [], 0, time.monotonic()
        keep_awake(True)
        try:
            for tile in recipe['tiles']:
                for source, product in products.items():
                    for metric in p.METRICS:
                        if (tile['id'], source, metric) in done:
                            continue
                        if (root/'STOP').exists():
                            return {'status': 'stopped', 'records': len(done), 'failures': failures, **capture.usage()}
                        if max_new_rasters is not None and new >= max_new_rasters:
                            return {'status': 'checkpoint', 'records': len(done), 'failures': failures, **capture.usage()}
                        record = fetch_one(root, capture, tile, source, metric, product, info[source, metric], sleep, log)
                        if record is None:
                            failures.append(f'{tile["id"]}/{source}/{metric}')
                            continue
                        transient = record.pop('transient_path', None)
                        target = root/record_name(tile['id'], source, metric)
                        target.parent.mkdir(parents=True, exist_ok=True)
                        atomic_json(target, record)
                        if transient:
                            (root/transient).unlink()
                        done.add((tile['id'], source, metric)); new += 1
                        rate = (time.monotonic()-started)/new
                        log(f'{len(done)}/{total} {tile["id"]}/{source}/{metric}: {record["status"]}; '
                            f'about {rate*(total-len(done))/3600:.1f} h left', flush=True)
        finally:
            keep_awake(False)
        if failures:
            return {'status': 'incomplete', 'records': len(done), 'failures': failures, **capture.usage()}
        return seal(root, mask_path=mask_path, usage=capture.usage())


def fetch_one(root, capture, tile, source, metric, product, info, sleep, log):
    """One accepted record, an outside-rectangle record, or None when set aside."""
    bounds = c.request_bounds(tile, info)
    record = {'tile': tile['id'], 'source': source, 'metric': metric, 'request_bounds': bounds,
              'status': 'outside_provider_rectangle' if bounds is None else 'accepted'}
    if bounds is None:
        return record
    fetched = fetch_with_retries(capture, product, metric, bounds, f'{tile["id"]}/{source}/{metric}', sleep, log)
    if fetched is None:
        return None
    body, journal = fetched
    try:
        qa = p.inspect_raster(body, bounds, info['grid'], metric, max_cells=1002)
    except (ValueError, rasterio.errors.RasterioError) as exc:
        http = json.loads(journal.read_text('utf-8')); http['rejected'] = True
        atomic_json(journal, http)
        log(f'{tile["id"]}/{source}/{metric} rejected by QA, set aside: {exc}', flush=True)
        return None
    data = body.read_bytes()
    record.update(path=archive_name(tile['id'], source, metric), http_record=journal.relative_to(root).as_posix(),
                  sha256=hashlib.sha256(data).hexdigest(), qa=qa, archive='zstd')
    write_archive(root/record['path'], data)
    # The uncompressed response is removed only after its record is committed.
    return {**record, 'transient_path': body.relative_to(root).as_posix()}


def saved_capture(root):
    def capture(_root, name, url, params=None, required=True):
        path = root/name
        record = json.loads((root/(name+'.http.json')).read_text('utf-8'))
        if (record['request_url'] != requests.Request('GET', url, params=params).prepare().url
                or not record['complete'] or (required and record['status'] != 200)
                or p.file_hash(path) != record['sha256']):
            raise ValueError('Inventory HTTP evidence changed')
        return path
    return capture


def verify(root, *, mask_path=REPOSITORY/MASK, require_complete=True, log=lambda *a, **k: None):
    """Replay every record from local evidence; compare all seam strips exactly."""
    root = Path(root).resolve()
    recipe, parent_products = check_recipe(root, mask_path)
    _, _, _, parent_records = parent_copy(root)
    inventory = json.loads((root/'inventory.json').read_text('utf-8'))
    if inventory != live_inventory(root, saved_capture(root)):
        raise ValueError('Inventory differs from captured discovery evidence')
    check_inventory(root, inventory, parent_products)
    products = pinned(parent_products)
    if json.loads((root/'products.json').read_text('utf-8')) != products:
        raise ValueError('Pinned request recipes changed')
    probes = json.loads((root/'probes.json').read_text('utf-8'))
    if set(probes) != {f'{s}-{m}' for s in p.PROVIDERS for m in p.METRICS}:
        raise ValueError('Incomplete provider consistency probes')
    reused = {(r['tile'], r['source'], r['metric']): r for r in load_records(root) if 'parent_tile' in r}
    for layer, result in sorted(probes.items()):
        source, metric = layer.split('-')
        original = reused[result['tile'], source, metric]
        http = json.loads((root/result['http_record']).read_text('utf-8'))
        params = p.coverage_params(products[source], metric, original['request_bounds'], max_cells=1002)
        url = requests.Request('GET', products[source]['endpoint'], params=params).prepare().url
        if (http['request_url'] != url or http['status'] != 200 or not http['complete']
                or http['sha256'] != p.file_hash(root/result['body'])
                or probe_match(root, original, root/result['body']) != result['match']):
            raise ValueError('Provider consistency probe evidence changed')
    info = descriptions(root, products)
    tiles = {t['id']: t for t in recipe['tiles']}
    parents = {(r['tile'], r['source'], r['metric']): r for r in parent_records}
    records = load_records(root)
    keys = [(r['tile'], r['source'], r['metric']) for r in records]
    expected = {(t, s, m) for t in tiles for s in p.PROVIDERS for m in p.METRICS}
    if len(set(keys)) != len(keys) or not set(keys) <= expected or (require_complete and set(keys) != expected):
        raise ValueError('Incomplete or duplicate England coverage')
    accepted = {}
    for index, record in enumerate(records, 1):
        key = record['tile'], record['source'], record['metric']
        if not (root/record_name(*key)).exists():
            raise ValueError('Record stored under another tile/layer name')
        tile = tiles[record['tile']]
        if 'parent_tile' in record:
            original = parents.get((tile.get('parent_tile'), record['source'], record['metric']))
            if original is None or record != reused_record(original, tile['id']):
                raise ValueError('Reused Oxford–London record changed')
            with open_record(root, record):
                pass
        else:
            bounds = c.request_bounds(tile, info[record['source'], record['metric']])
            if record['request_bounds'] != bounds:
                raise ValueError('Tile recipe differs from provider intersection')
            if bounds is None:
                if record['status'] != 'outside_provider_rectangle':
                    raise ValueError('Outside rectangle misclassified')
                continue
            product = products[record['source']]
            http = json.loads((root/record['http_record']).read_text('utf-8'))
            url = requests.Request('GET', product['endpoint'], params=p.coverage_params(product, record['metric'], bounds, max_cells=1002)).prepare().url
            if (record['status'] != 'accepted' or http['request_url'] != url or http['status'] != 200
                    or not http['complete'] or http['sha256'] != record['sha256'] or http.get('rejected')
                    or record['path'] != archive_name(*key)):
                raise ValueError('Tile acquisition evidence mismatch')
            with open_record(root, record) as memory:
                if p.inspect_raster(memory.name, bounds, info[record['source'], record['metric']]['grid'],
                                    record['metric'], max_cells=1002) != record['qa']:
                    raise ValueError('Tile QA changed')
        accepted[key] = record
        if index % 500 == 0:
            log(f'{index}/{len(records)} records verified', flush=True)
    seams, cells = 0, 0
    for dx, dy in ((TILE_M, 0), (0, TILE_M)):
        for (tile, source, metric), left in sorted(accepted.items()):
            w, s = tiles[tile]['core_bounds'][:2]
            right = accepted.get((tile_id(w+dx, s+dy), source, metric))
            overlap = right and c.intersection(left['request_bounds'], right['request_bounds'])
            if overlap:
                with open_record(root, left) as a, open_record(root, right) as b:
                    cells += c.compare_window(a.name, b.name, overlap)
                seams += 1
                if seams % 1000 == 0:
                    log(f'{seams} seams compared', flush=True)
    return {'complete': set(keys) == expected, 'tiles': len(tiles), 'accepted_rasters': len(accepted),
            'reused_rasters': sum('parent_tile' in r for r in accepted.values()),
            'outside_rectangle': len(records)-len(accepted), 'exact_seam_pairs': seams, 'seam_source_indicator_cells': int(cells),
            'interpretation': 'Exact extraction checks only. Oxford–London regional reference comparisons are inherited from the sealed parent.'}


def seal(root, *, mask_path=REPOSITORY/MASK, usage=None, log=print):
    root = Path(root).resolve()
    report = verify(root, mask_path=mask_path, log=log)
    atomic_json(root/'verification.json', report)
    for original in [*Path(__file__).parent.glob('*.py'), REPOSITORY/'scripts/123_acquire_england.py',
                     REPOSITORY/'requirements-windows-py314-amd64.lock']:
        target = root/'construction'/original.relative_to(REPOSITORY)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, target)
    records = load_records(root)
    atomic_json(root/'records.json', records)
    files = {path.relative_to(root).as_posix(): p.file_hash(path) for path in sorted(root.rglob('*'))
             if path.is_file() and path.name != 'STOP'}
    manifest = {'schema_version': 1, 'status': 'verified_extraction_not_public_exposure_release',
                'created_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files,
                'parent_release_id': parent_copy(root)[0]['release_id'], 'verification': report,
                'usage': usage or Capture(root, json.loads((root/'plan.json').read_text('utf-8'))['limits']).usage()}
    manifest['release_id'] = 'england-'+hashlib.sha256(p.json_bytes(manifest)).hexdigest()[:20]
    atomic_json(root/'manifest.json', manifest)
    return manifest
