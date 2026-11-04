"""England acquisition on a miniature lattice: 100 m tiles, a fake provider, real GeoTIFFs."""
import copy
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from quiet_uk import england_acquisition as e
from quiet_uk.budget_capture import atomic_json
from quiet_uk.source_pilot import json_bytes

PERIOD = {'start': '2021-01-01', 'end': '2021-12-31'}
NODATA = {'road': -96., 'rail': -96., 'aircraft': 3.3999999521443642e+38}


class Response:
    def __init__(self, status=200, body=b''):
        self.status_code, self.body = status, body
        self.url = 'https://example.org/wcs'; self.headers = {'Content-Type': 'image/tiff'}
    def iter_content(self, chunk_size):
        yield self.body
    def close(self):
        pass


def truth(source, metric, cx, cy):
    """A continuous synthetic field, so neighbouring requests agree on every seam cell."""
    if source == 'aircraft':
        return np.full(cx.shape, NODATA['aircraft'])
    level = 40 + ((cx//10 + 3*(cy//10)) % 25) + (metric == 'Lday')
    level[(cx//10) % 7 == 0] = 0
    if source == 'rail':
        level[(cy//10) % 3 != 0] = 0
    return level


def raster(source, metric, bounds):
    w, s, east, n = bounds
    cx, cy = np.meshgrid(np.arange(w+5, east, 10), np.arange(n-5, s, -10))
    dtype = 'float64' if source == 'aircraft' else 'float32'
    profile = dict(driver='GTiff', count=1, width=cx.shape[1], height=cx.shape[0], dtype=dtype,
                   nodata=NODATA[source], crs='EPSG:27700', transform=from_origin(w, n, 10, 10))
    with MemoryFile() as memory:
        with memory.open(**profile) as ds:
            ds.write(truth(source, metric, cx, cy).astype(dtype), 1)
        return memory.read()


def products():
    return {source: {'endpoint': f'https://example.org/{source}/wcs', 'version': '1.0.0',
                     'coverage_ids': {m: f'{source}-{m}' for m in ('Lden', 'Lday', 'Lnight')},
                     'requests_by_metric': {m: {'version': '1.0.0', 'coverage_id': f'{source}-{m}',
                                                'description_file': f'evidence/{source}-{m}-description.xml',
                                                'fallback_reason': None} for m in ('Lden', 'Lday', 'Lnight')},
                     'metadata_id': source+'-id', 'metadata_file': f'evidence/{source}.html'}
            for source in ('road', 'rail', 'aircraft')}


class Provider:
    def __init__(self):
        self.calls, self.fail = [], set()
    def __call__(self, url, *, params, **kwargs):
        params = dict(params)
        source, metric = params['coverage'].split('-')
        self.calls.append((source, metric, params['bbox']))
        parent = params['bbox'] == '105.0,105.0,205.0,205.0' or params['bbox'] == '105,105,205,205'
        if ((source, metric) in self.fail and not parent) or self.fail == {'all'}:
            return Response(503, b'temporary outage')
        return Response(body=raster(source, metric, list(map(float, params['bbox'].split(',')))))


@pytest.fixture
def world(tmp_path, monkeypatch):
    """A 3 x 3 lattice of 100 m tiles; the centre tile is the sealed 'Oxford–London' parent."""
    monkeypatch.setattr(e, 'TILE_M', 100)
    monkeypatch.setattr(e, 'LATTICE', (5, 5))
    mask = tmp_path/'mask.tif'
    land = np.zeros((4, 4), 'uint8'); land[1:3, 1:3] = 1
    with rasterio.open(mask, 'w', driver='GTiff', count=1, width=4, height=4, dtype='uint8', nodata=0,
                       crs='EPSG:27700', transform=from_origin(0, 400, 100, 100)) as ds:
        ds.write(land, 1)
    def inventory(root, **kwargs):
        for product in products().values():
            (Path(root)/product['metadata_file']).parent.mkdir(parents=True, exist_ok=True)
            (Path(root)/product['metadata_file']).write_text('metadata')
        return copy.deepcopy(products())
    monkeypatch.setattr(e.p, 'inventory', inventory)
    monkeypatch.setattr(e.p, 'provider_period', lambda *a: dict(PERIOD))
    monkeypatch.setattr(e, 'description', lambda path: {'status': 'available', 'envelope': [-95, -95, 495, 495],
                                                        'grid': {'center_origin': [0, 0]}})
    monkeypatch.setattr(e.c, 'verify', lambda *a, **k: {'complete': True})
    parent = tmp_path/'parent'
    core = [105, 105, 205, 205]
    parent_plan = {'tiles': [{'id': 'r0c0', 'row': 0, 'column': 0, 'core_bounds': core, 'halo_bounds': core}]}
    records = []
    for source, product in products().items():
        product['reference_period'] = dict(PERIOD); product['reference_period_status'] = 'provider_declared'
        for metric in ('Lden', 'Lday', 'Lnight'):
            body = raster(source, metric, core)
            key = hashlib.sha256(f'{source}{metric}'.encode()).hexdigest()
            (parent/'attempts'/key).mkdir(parents=True)
            (parent/'attempts'/key/'001.bin').write_bytes(body)
            atomic_json(parent/'attempts'/key/'001.json', {'sha256': hashlib.sha256(body).hexdigest()})
            records.append({'tile': 'r0c0', 'source': source, 'metric': metric, 'request_bounds': core,
                            'status': 'accepted', 'qa': {}, 'sha256': hashlib.sha256(body).hexdigest(),
                            'path': f'attempts/{key}/001.bin', 'http_record': f'attempts/{key}/001.json'})
    parent_products = products()
    for product in parent_products.values():
        product.update(reference_period=dict(PERIOD), reference_period_status='provider_declared')
    (parent/'evidence').mkdir()
    (parent/'evidence/road.html').write_text('metadata')
    for name, value in (('plan.json', parent_plan), ('products.json', parent_products), ('records.json', records)):
        atomic_json(parent/name, value)
    files = {path.relative_to(parent).as_posix(): e.p.file_hash(path) for path in sorted(parent.rglob('*')) if path.is_file()}
    manifest = {'files': files}
    manifest['release_id'] = 'canary-'+hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    atomic_json(parent/'manifest.json', manifest)
    return {'mask': mask, 'parent': parent, 'root': tmp_path/'england'}


def test_lattice_covers_every_land_cell_and_keeps_parent_halos(world):
    parent_plan = json.loads((world['parent']/'plan.json').read_text())
    recipe = e.plan(world['mask'], parent_plan)
    assert [t['id'] for t in recipe['tiles']][:3] == ['E5N5', 'E105N5', 'E205N5']
    assert len(recipe['tiles']) == 9 and recipe['planned_tiles'] == 9
    centre = next(t for t in recipe['tiles'] if t['id'] == 'E105N105')
    assert centre['parent_tile'] == 'r0c0' and centre['halo_bounds'] == [105, 105, 205, 205]
    corner = recipe['tiles'][0]
    assert corner['halo_bounds'] == [-5, -5, 115, 115] and 'parent_tile' not in corner
    parent_plan['tiles'][0]['core_bounds'] = [100, 100, 200, 200]
    with pytest.raises(ValueError, match='lattice'):
        e.plan(world['mask'], parent_plan)


def test_seed_download_resume_and_sealed_offline_replay(world, monkeypatch):
    root = world['root']
    seeded = e.seed(world['parent'], root, reference=None, mask_path=world['mask'])
    assert seeded['reused_rasters'] == 9 and seeded['planned_rasters'] == 81
    provider, waits, lines = Provider(), [], []
    kwargs = dict(mask_path=world['mask'], get=provider, sleep=waits.append, log=lambda *a, **k: lines.append(a[0]))
    assert e.acquire(root, max_new_rasters=5, **kwargs)['status'] == 'checkpoint'
    # Nine consistency probes of the reused tile, then five new rasters.
    assert len(provider.calls) == 14
    assert {r['match'] for r in json.loads((root/'probes.json').read_text()).values()} == {'byte_identical'}
    # A transient outage is retried after a wait, without stopping the run.
    provider.fail = {'all'}
    original = provider.__call__
    def flaky(url, **k):
        result = original(url, **k)
        provider.fail = set()
        return result
    (root/'STOP').write_text('')
    assert e.acquire(root, get=flaky, **{k: v for k, v in kwargs.items() if k != 'get'})['status'] == 'stopped'
    (root/'STOP').unlink()
    sealed = e.acquire(root, get=flaky, **{k: v for k, v in kwargs.items() if k != 'get'})
    assert 60 in waits
    report = sealed['verification']
    assert report['complete'] and report['accepted_rasters'] == 81 and report['reused_rasters'] == 9
    assert report['exact_seam_pairs'] == 2*6*9  # six east and six north neighbours per layer
    # Only failed responses and the nine probe responses stay uncompressed.
    probes = {r['body'] for r in json.loads((root/'probes.json').read_text()).values()}
    assert all(b.relative_to(root).as_posix() in probes or json.loads(b.with_suffix('.json').read_text())['status'] != 200
               for b in (root/'attempts').glob('*/*.bin'))
    # Archives reproduce the provider bytes exactly.
    record = json.loads((root/e.record_name('E5N5', 'road', 'Lden')).read_text())
    assert e.read_archive(root/record['path']) == raster('road', 'Lden', record['request_bounds'])
    monkeypatch.setattr(e.requests, 'get', lambda *a, **k: pytest.fail('Offline replay contacted provider'))
    assert e.verify(root, mask_path=world['mask']) == report
    with pytest.raises(FileExistsError):
        e.acquire(root, **kwargs)


def test_tampering_with_archives_or_reused_records_is_detected(world):
    root = world['root']
    e.seed(world['parent'], root, reference=None, mask_path=world['mask'])
    e.acquire(root, mask_path=world['mask'], get=Provider(), sleep=lambda s: None, log=lambda *a, **k: None)
    record_path = root/e.record_name('E205N205', 'rail', 'Lnight')
    record = json.loads(record_path.read_text())
    archive = root/record['path']
    good = archive.read_bytes()
    e.write_archive(archive, raster('rail', 'Lday', record['request_bounds']))
    with pytest.raises(ValueError, match='changed'):
        e.verify(root, mask_path=world['mask'])
    archive.write_bytes(good)
    reused_path = root/e.record_name('E105N105', 'road', 'Lden')
    reused = json.loads(reused_path.read_text())
    atomic_json(reused_path, {**reused, 'qa': {'changed': True}})
    with pytest.raises(ValueError, match='Reused'):
        e.verify(root, mask_path=world['mask'])


def test_persistent_failure_is_set_aside_then_retried_on_the_next_run(world):
    root = world['root']
    e.seed(world['parent'], root, reference=None, mask_path=world['mask'])
    provider = Provider(); provider.fail = {('rail', 'Lday')}
    quiet = dict(mask_path=world['mask'], sleep=lambda s: None, log=lambda *a, **k: None)
    result = e.acquire(root, get=provider, **quiet)
    assert result['status'] == 'incomplete' and len(result['failures']) == 8
    assert not (root/'manifest.json').exists()
    provider.fail = set()
    # Four of five attempts were used per failed layer; one more is allowed.
    assert e.acquire(root, get=provider, **quiet)['verification']['complete']


def test_products_that_changed_since_oxford_london_stop_before_download(world, monkeypatch):
    root = world['root']
    e.seed(world['parent'], root, reference=None, mask_path=world['mask'])
    changed = products(); changed['road']['requests_by_metric']['Lden']['coverage_id'] = 'road-Lden-2026'
    original = e.p.inventory
    monkeypatch.setattr(e.p, 'inventory', lambda root, **k: (original(root), copy.deepcopy(changed))[1])
    provider = Provider()
    with pytest.raises(e.AcquisitionStopped, match='differs'):
        e.acquire(root, mask_path=world['mask'], get=provider, sleep=lambda s: None, log=lambda *a, **k: None)
    assert provider.calls == []


def test_protocol_prefix_change_is_accepted_only_if_old_requests_still_reproduce(world, monkeypatch):
    root = world['root']
    e.seed(world['parent'], root, reference=None, mask_path=world['mask'])
    renamed = products(); renamed['road']['requests_by_metric']['Lnight']['coverage_id'] = 'service:road-Lnight'
    original = e.p.inventory
    monkeypatch.setattr(e.p, 'inventory', lambda root, **k: (original(root), copy.deepcopy(renamed))[1])
    provider = Provider()
    def drifted(url, **kwargs):
        response = provider(url, **kwargs)
        if dict(kwargs['params'])['coverage'] == 'rail-Lden':
            data = raster('rail', 'Lday', list(map(float, dict(kwargs['params'])['bbox'].split(','))))
            response = Response(body=data)
        return response
    quiet = dict(mask_path=world['mask'], sleep=lambda s: None, log=lambda *a, **k: None)
    with pytest.raises(e.AcquisitionStopped, match='rail Lden no longer reproduces'):
        e.acquire(root, get=drifted, max_new_rasters=1, **quiet)
    # Requests keep Oxford–London's recipe, whatever the live catalogue now advertises.
    assert all(call[0] != 'service' for call in provider.calls)
    assert e.acquire(root, get=provider, max_new_rasters=1, **quiet)['status'] == 'checkpoint'
    assert json.loads((root/'products.json').read_text())['road']['requests_by_metric']['Lnight']['coverage_id'] == 'road-Lnight'
