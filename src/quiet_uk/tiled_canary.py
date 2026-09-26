"""A bounded 50 km extraction canary, independent of the four-site pilot.

Core tiles partition the study rectangle; one-cell halos verify seams. Provider
storage envelopes constrain requests but never certify calculation domains.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

import numpy as np
import rasterio
from rasterio.windows import from_bounds
import requests

from . import source_pilot as p
from .budget_capture import BudgetCapture, AcquisitionStopped, atomic_json
from .locking import ResourceLock
from .provider_audit import description

LIMITS = {'http_attempts': 300, 'transfer_bytes': 3*1024**3, 'response_bytes': 32*1024**2,
          'attempts_per_request': 3, 'interval_seconds': 1.0, 'min_free_disk_bytes': 10*1024**3}
BOUNDS = [445005, 165005, 495005, 215005]


def intersection(a, b):
    result = [max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])]
    return result if result[0] < result[2] and result[1] < result[3] else None


def plan(bounds=BOUNDS, *, tile_m=10000):
    if (len(bounds) != 4 or not all(isinstance(v, int) and not isinstance(v, bool) and v % 10 == 5 for v in bounds)
            or bounds[2]-bounds[0] != 50000 or bounds[3]-bounds[1] != 50000 or tile_m != 10000):
        raise ValueError('Canary requires a 50 km square on native 10 m cell edges and 10 km core tiles')
    tiles = []
    for row in range(5):
        for col in range(5):
            w, s = bounds[0]+col*tile_m, bounds[1]+row*tile_m
            core = [w, s, w+tile_m, s+tile_m]
            halo = intersection([w-10, s-10, w+tile_m+10, s+tile_m+10], bounds)
            tiles.append({'id': f'r{row}c{col}', 'row': row, 'column': col, 'core_bounds': core, 'halo_bounds': halo})
    return {'schema_version': 1, 'name': 'Oxford–Reading–Chilterns tiled canary',
            'crs': 'EPSG:27700', 'bounds': list(bounds), 'tile_m': tile_m, 'halo_m': 10,
            'tiles': tiles, 'sources': list(p.PROVIDERS), 'metrics': list(p.METRICS),
            'planned_raster_requests': 225, 'core_source_indicator_cells': 225_000_000,
            'area_km2': 2500, 'limits': dict(LIMITS),
            'status': 'engineering_canary_not_a_map_or_research_exposure_release'}


def request_bounds(tile, info):
    if info['status'] != 'available':
        raise ValueError('Native grid/envelope description unavailable')
    result = intersection(tile['halo_bounds'], info['envelope'])
    if result is None:
        return None
    ox, oy = info['grid']['center_origin']
    if any(abs(value-round(value)) > 1e-7 for value in (
            (result[0]+5-ox)/10, (result[2]+5-ox)/10,
            (result[1]+5-oy)/10, (result[3]+5-oy)/10)):
        raise ValueError('Study rectangle and provider grid phases differ')
    p.grid_shape(result, max_cells=1002)
    return result


def compare_window(left, right, bounds):
    """Compare raw values AND masks, with exact encoding/grid compatibility."""
    with rasterio.open(left) as a, rasterio.open(right) as b:
        if a.crs != b.crs or a.dtypes != b.dtypes or a.nodata != b.nodata:
            raise ValueError('Overlap encoding mismatch')
        arrays, masks = [], []
        for ds in (a, b):
            if (ds.transform.a, ds.transform.b, ds.transform.d, ds.transform.e) != (10, 0, 0, -10):
                raise ValueError('Overlap grid is not native')
            win = from_bounds(*bounds, transform=ds.transform)
            numbers = [win.col_off, win.row_off, win.width, win.height]
            if (any(abs(n-round(n)) > 1e-7 for n in numbers)
                    or min(numbers) < 0 or win.col_off+win.width > ds.width or win.row_off+win.height > ds.height):
                raise ValueError('Overlap window is shifted or outside raster')
            win = win.round_offsets().round_lengths()
            arrays.append(ds.read(1, window=win)); masks.append(ds.read_masks(1, window=win))
        if not np.array_equal(arrays[0], arrays[1]) or not np.array_equal(masks[0], masks[1]):
            raise ValueError('Overlap values or masks differ')
        return arrays[0].size


def verify(root, reference, *, require_complete=True, records=None):
    root = Path(root)
    sealed = root/'manifest.json'
    if sealed.exists():
        manifest = json.loads(sealed.read_text('utf-8'))
        base = {k: v for k, v in manifest.items() if k != 'release_id'}
        if manifest['release_id'] != 'canary-'+hashlib.sha256(p.json_bytes(base)).hexdigest()[:20]:
            raise ValueError('Canary manifest checksum mismatch')
        for name, digest in manifest['files'].items():
            path = (root/name).resolve()
            if not path.is_relative_to(root.resolve()) or p.file_hash(path) != digest:
                raise ValueError('Sealed canary evidence changed')
    recipe = json.loads((root/'plan.json').read_text('utf-8'))
    if recipe != plan(recipe['bounds']):
        raise ValueError('Canary plan changed')
    products = json.loads((root/'products.json').read_text('utf-8'))
    def saved_capture(_root, name, url, params=None, required=True):
        path = root/name
        record = json.loads((root/(name+'.http.json')).read_text('utf-8'))
        if (record['request_url'] != requests.Request('GET', url, params=params).prepare().url
                or not record['complete'] or (required and record['status'] != 200)
                or p.file_hash(path) != record['sha256']):
            raise ValueError('Inventory HTTP evidence changed')
        return path
    reconstructed = p.inventory(root, capture_response=saved_capture)
    for product in reconstructed.values():
        product['reference_period'] = p.provider_period((root/product['metadata_file']).read_text('utf-8'), product['metadata_id'])
        product['reference_period_status'] = 'provider_declared' if product['reference_period'] else 'unspecified_by_provider'
    if products != reconstructed:
        raise ValueError('Products differ from captured discovery evidence')
    for source in p.PROVIDERS:
        for metric in p.METRICS:
            if product_identity(products[source], metric) != product_identity(reference.manifest['products'][source], metric):
                raise ValueError('Reference product/period differs')
    records = json.loads((root/'records.json').read_text('utf-8')) if records is None else records
    expected = {(t['id'], s, m) for t in recipe['tiles'] for s in recipe['sources'] for m in recipe['metrics']}
    keys = [(r['tile'], r['source'], r['metric']) for r in records]
    if len(set(keys)) != len(keys) or not set(keys) <= expected or (require_complete and set(keys) != expected):
        raise ValueError('Incomplete or duplicate tiled coverage')
    reference_manifest = json.loads((root/'reference.json').read_text('utf-8'))
    if reference_manifest != reference.manifest:
        raise ValueError('Reference release changed')
    by_key, bounds_by_key = {}, {}
    for record in records:
        key = record['tile'], record['source'], record['metric']
        tile = next(t for t in recipe['tiles'] if t['id'] == record['tile'])
        product = products[record['source']]
        desc = product['requests_by_metric'][record['metric']]['description_file']
        info = description(root/desc)
        bounds = request_bounds(tile, info)
        if record['request_bounds'] != bounds:
            raise ValueError('Tile recipe differs from provider intersection')
        if bounds is None:
            if record['status'] != 'outside_provider_rectangle':
                raise ValueError('Outside rectangle misclassified')
            continue
        if record['status'] != 'accepted' or p.file_hash(root/record['path']) != record['sha256']:
            raise ValueError('Accepted tile changed')
        http = json.loads((root/record['http_record']).read_text('utf-8'))
        params = p.coverage_params(product, record['metric'], bounds, max_cells=1002)
        url = requests.Request('GET', product['endpoint'], params=params).prepare().url
        if (http['request_url'] != url or http['status'] != 200 or not http['complete']
                or http['sha256'] != record['sha256'] or http.get('rejected')):
            raise ValueError('Tile acquisition evidence mismatch')
        qa = p.inspect_raster(root/record['path'], bounds, info['grid'], record['metric'], max_cells=1002)
        if qa != record['qa']:
            raise ValueError('Tile QA changed')
        by_key[key], bounds_by_key[key] = record, bounds
    seams, seam_cells, reference_pairs, reference_cells = 0, 0, 0, 0
    for tile in recipe['tiles']:
        for source in recipe['sources']:
            for metric in recipe['metrics']:
                key = tile['id'], source, metric
                if key not in by_key:
                    continue
                left = by_key[key]
                for row, col in ((tile['row']+1, tile['column']), (tile['row'], tile['column']+1)):
                    other = f'r{row}c{col}', source, metric
                    if other in by_key:
                        overlap = intersection(bounds_by_key[key], bounds_by_key[other])
                        if overlap:
                            seam_cells += compare_window(root/left['path'], root/by_key[other]['path'], overlap)
                            seams += 1
                for original in reference.records.values():
                    if original['source'] != source or original['metric'] != metric:
                        continue
                    overlap = intersection(intersection(tile['core_bounds'], bounds_by_key[key]) or [0,0,0,0], original['qa']['bounds'])
                    if overlap:
                        old_product = reference.manifest['products'][source]
                        if (product_identity(products[source], metric) != product_identity(old_product, metric)):
                            raise ValueError('Reference product/period differs')
                        reference_cells += compare_window(root/left['path'], reference.verified_path(original['path']), overlap)
                        reference_pairs += 1
    result = {'complete': set(keys) == expected, 'accepted_rasters': len(by_key),
            'outside_rectangle': len(records)-len(by_key), 'exact_seam_pairs': seams,
            'seam_source_indicator_cells': int(seam_cells), 'exact_reference_pairs': reference_pairs,
            'reference_source_indicator_cells': int(reference_cells),
            'interpretation': 'Exact extraction checks only; unknown remains unknown. No acoustic validation or extra quietness bounds.'}
    if sealed.exists() and result != manifest['verification']:
        raise ValueError('Sealed verification result changed')
    return result


def product_identity(product, metric):
    return product['metadata_id'], product['requests_by_metric'][metric]['coverage_id'], product['reference_period']


def recover_checkpoint(root, reference):
    """Explicit recovery: preserve damage, then replay provable successes only.

    Missing HTTP metadata is never invented. Unreadable journals remain charged
    at the full response allowance and cannot supply an accepted raster.
    """
    root = Path(root).resolve()
    with ResourceLock(root, 'tiled canary recovery'):
        if (root/'manifest.json').exists():
            raise FileExistsError('Never repair a sealed release in place')
        recipe = json.loads((root/'plan.json').read_text('utf-8'))
        if recipe != plan(recipe['bounds']) or json.loads((root/'reference.json').read_text('utf-8')) != reference.manifest:
            raise ValueError('Pinned recovery recipe/reference changed')
        products = json.loads((root/'products.json').read_text('utf-8'))
        verify(root, reference, require_complete=False, records=[])
        expected = {}
        for tile in recipe['tiles']:
            for source, product in products.items():
                for metric in p.METRICS:
                    info = description(root/product['requests_by_metric'][metric]['description_file'])
                    bounds = request_bounds(tile, info)
                    if bounds is not None:
                        params = p.coverage_params(product, metric, bounds, max_cells=1002)
                        url = requests.Request('GET', product['endpoint'], params=params).prepare().url
                        expected[hashlib.sha256(url.encode()).hexdigest()] = (url, tile, source, metric, bounds, info)
        damaged, accepted = [], {}
        for journal in sorted((root/'attempts').glob('*/*.json')):
            try:
                http = json.loads(journal.read_text('utf-8'))
                if not isinstance(http, dict) or 'state' not in http or 'request_url' not in http:
                    raise ValueError('Invalid journal structure')
            except (ValueError, UnicodeError):
                if journal.parent.name not in expected:
                    raise ValueError('Cannot reconstruct identity of damaged non-coverage journal')
                original = journal.with_suffix('.json.damaged')
                if original.exists() and p.file_hash(original) != p.file_hash(journal):
                    raise ValueError('Conflicting damaged-journal evidence')
                if not original.exists():
                    shutil.copyfile(journal, original)
                url = expected[journal.parent.name][0]
                http = {'state': 'unavailable', 'request_url': url,
                        'identity_basis': 'pinned_recipe_and_request_directory_hash_not_recovered_HTTP_metadata',
                        'damaged_journal': original.relative_to(root).as_posix(),
                        'damaged_sha256': p.file_hash(original)}
                atomic_json(journal, http)
                damaged.append(http['damaged_journal'])
            if journal.parent.name not in expected or http['state'] != 'finished' or not http.get('complete') or http.get('rejected') or http.get('status') != 200:
                continue
            url, tile, source, metric, bounds, info = expected[journal.parent.name]
            body = journal.with_suffix('.bin')
            if http['request_url'] != url or p.file_hash(body) != http['sha256'] or body.stat().st_size != http['bytes']:
                raise ValueError('Completed response checksum/request differs; manual review required')
            qa = p.inspect_raster(body, bounds, info['grid'], metric, max_cells=1002)
            key = tile['id'], source, metric
            accepted.setdefault(key, {'tile': tile['id'], 'source': source, 'metric': metric,
                'request_bounds': bounds, 'status': 'accepted', 'qa': qa, 'sha256': http['sha256'],
                'path': body.relative_to(root).as_posix(), 'http_record': journal.relative_to(root).as_posix()})
        checkpoint = root/'records.json'
        recovery = root/'recovery'; recovery.mkdir(exist_ok=True)
        previous = recovery/('records-'+p.file_hash(checkpoint)+'.original')
        if not previous.exists():
            shutil.copyfile(checkpoint, previous)
        report = verify(root, reference, require_complete=False, records=list(accepted.values()))
        atomic_json(checkpoint, list(accepted.values()))
        report.update(damaged_journals_retained=damaged, original_checkpoint=previous.relative_to(root).as_posix(),
                      recovered_at_utc=datetime.now(timezone.utc).isoformat())
        atomic_json(recovery/'report.json', report)
        return report


def acquire(root, reference, *, max_new_rasters=225):
    root = Path(root)
    with ResourceLock(root, 'tiled canary'):
        if (root/'manifest.json').exists():
            raise FileExistsError('Canary already sealed; verify or use a new destination')
        root.mkdir(parents=True, exist_ok=True)
        recipe = plan()
        for name, value in (('plan.json', recipe), ('reference.json', reference.manifest)):
            path = root/name
            if path.exists() and json.loads(path.read_text('utf-8')) != value:
                raise ValueError('Pinned plan/reference changed')
            if not path.exists():
                atomic_json(path, value)
        capture = BudgetCapture(root, recipe['limits'])
        products_path = root/'products.json'
        if products_path.exists():
            products = json.loads(products_path.read_text('utf-8'))
        else:
            products = p.inventory(root, capture_response=capture)
            for product in products.values():
                product['reference_period'] = p.provider_period((root/product['metadata_file']).read_text('utf-8'), product['metadata_id'])
                product['reference_period_status'] = 'provider_declared' if product['reference_period'] else 'unspecified_by_provider'
            atomic_json(products_path, products)
        records_path = root/'records.json'
        records = json.loads(records_path.read_text('utf-8')) if records_path.exists() else []
        if not records_path.exists():
            atomic_json(records_path, records)
        # Revalidate accepted work before resuming; a changed checkpoint is not trusted.
        verify(root, reference, require_complete=False)
        keys = {(r['tile'], r['source'], r['metric']) for r in records}
        completed = 0
        for tile in recipe['tiles']:
            for source, product in products.items():
                for metric in p.METRICS:
                    if (tile['id'], source, metric) in keys:
                        continue
                    if completed >= max_new_rasters:
                        return {'status': 'checkpoint', 'records': len(records), **capture.usage()}
                    info = description(root/product['requests_by_metric'][metric]['description_file'])
                    bounds = request_bounds(tile, info)
                    record = {'tile': tile['id'], 'source': source, 'metric': metric, 'request_bounds': bounds,
                              'status': 'outside_provider_rectangle' if bounds is None else 'accepted'}
                    if bounds is not None:
                        body, journal = capture.fetch(product['endpoint'], p.coverage_params(product, metric, bounds, max_cells=1002))
                        try:
                            qa = p.inspect_raster(body, bounds, info['grid'], metric, max_cells=1002)
                        except (ValueError, rasterio.errors.RasterioError):
                            http = json.loads(journal.read_text('utf-8')); http['rejected'] = True
                            atomic_json(journal, http)
                            raise AcquisitionStopped('Returned raster failed native-grid/value QA; attempt retained')
                        record.update(path=body.relative_to(root).as_posix(), http_record=journal.relative_to(root).as_posix(),
                                      sha256=p.file_hash(body), qa=qa)
                    records.append(record); completed += 1
                    atomic_json(records_path, records)
                    print(f'{len(records)}/225 {tile["id"]}/{source}/{metric}: {record["status"]}', flush=True)
        report = verify(root, reference)
        atomic_json(root/'verification.json', report)
        repo = Path(__file__).resolve().parents[2]
        for original in [*Path(__file__).parent.glob('*.py'), repo/'scripts/33_tiled_canary.py', repo/'requirements-windows-py314-amd64.lock']:
            target = root/'construction'/original.relative_to(repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
        files = {path.relative_to(root).as_posix(): p.file_hash(path) for path in sorted(root.rglob('*')) if path.is_file()}
        manifest = {'schema_version': 1, 'status': 'verified_extraction_canary_not_public_exposure_release',
                    'created_at_utc': datetime.now(timezone.utc).isoformat(), 'files': files,
                    'reference_release': reference.manifest['release_id'], 'verification': report, 'usage': capture.usage()}
        manifest['release_id'] = 'canary-'+hashlib.sha256(p.json_bytes(manifest)).hexdigest()[:20]
        atomic_json(root/'manifest.json', manifest)
        return manifest
