import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
import requests

from quiet_uk.budget_capture import BudgetCapture, AcquisitionStopped, atomic_json
from quiet_uk import tiled_canary as c
from quiet_uk.source_pilot import grid_shape, json_bytes
from test_source_pilot import pilot


def limits(**overrides):
    return {**c.LIMITS, 'interval_seconds': 0, 'min_free_disk_bytes': 0,
            'response_bytes': 100, 'transfer_bytes': 1000, **overrides}


class Response:
    def __init__(self, status=200, chunks=(b'original',)):
        self.status_code = status; self.chunks = chunks
        self.url = 'https://example.org/data'; self.headers = {'Content-Type': 'image/tiff'}
        self.closed = False
    def iter_content(self, chunk_size):
        for chunk in self.chunks:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk
    def close(self):
        self.closed = True


def test_recipe_partition_halos_and_pilot_limits_stay_separate():
    recipe = c.plan()
    assert len(recipe['tiles']) == 25 and recipe['planned_raster_requests'] == 225
    assert sum((t['core_bounds'][2]-t['core_bounds'][0])*(t['core_bounds'][3]-t['core_bounds'][1]) for t in recipe['tiles']) == 50000**2
    for i, tile in enumerate(recipe['tiles']):
        assert grid_shape(tile['halo_bounds'], max_cells=1002)[0] <= 1002
        for other in recipe['tiles'][i+1:]:
            assert c.intersection(tile['core_bounds'], other['core_bounds']) is None
    middle = recipe['tiles'][12]
    with pytest.raises(ValueError):
        grid_shape(middle['halo_bounds'])  # four-site pilot guard unchanged
    assert grid_shape(middle['halo_bounds'], max_cells=1002) == (1002, 1002)
    overlap = c.intersection(middle['halo_bounds'], recipe['tiles'][13]['halo_bounds'])
    assert overlap[2]-overlap[0] == 20
    with pytest.raises(ValueError):
        c.plan([0, 0, 50000, 50000])


def test_provider_edges_clip_without_padding_or_rescaling():
    tile = c.plan()['tiles'][0]
    w, s, e, n = tile['halo_bounds']
    info = {'status': 'available', 'envelope': [w+5000, s, e+5000, n],
            'grid': {'center_origin': [w+5, s+5]}}
    assert c.request_bounds(tile, info) == [w+5000, s, e, n]
    assert c.request_bounds(tile, {**info, 'envelope': [e+100, s, e+5000, n]}) is None
    with pytest.raises(ValueError, match='phases'):
        c.request_bounds(tile, {**info, 'grid': {'center_origin': [w+6, s+5]}})


def test_retry_retains_failure_and_reuses_success_without_network(tmp_path):
    responses = [Response(503, [b'error']), Response()]
    calls = []
    def get(url, **kwargs):
        assert kwargs['stream'] and not kwargs['allow_redirects']
        calls.append(url); return responses[len(calls)-1]
    capture = BudgetCapture(tmp_path, limits(), get=get)
    with pytest.raises(AcquisitionStopped, match='HTTP 503'):
        capture.fetch('https://example.org/data')
    body, record = BudgetCapture(tmp_path, limits(), get=get).fetch('https://example.org/data')
    assert body.read_bytes() == b'original'
    assert len(list((tmp_path/'attempts').glob('*/*.bin'))) == 2
    assert capture.fetch('https://example.org/data') == (body, record)
    assert len(calls) == 2 and all(r.closed for r in responses)
    body.write_bytes(b'changed')
    with pytest.raises(AcquisitionStopped, match='changed'):
        capture.fetch('https://example.org/data')


@pytest.mark.parametrize('chunks', [(b'partial', requests.ConnectionError()), (b'x'*101,)])
def test_incomplete_or_oversized_response_cannot_be_accepted(tmp_path, chunks):
    response = Response(chunks=chunks)
    capture = BudgetCapture(tmp_path, limits(), get=lambda *a, **k: response)
    with pytest.raises(AcquisitionStopped):
        capture.fetch('https://example.org/data')
    record = json.loads(next((tmp_path/'attempts').glob('*/*.json')).read_text())
    assert not record['complete'] and response.closed
    assert record['bytes'] <= 100


def test_cumulative_request_limit_survives_restart(tmp_path):
    capture = BudgetCapture(tmp_path, limits(http_attempts=1), get=lambda *a, **k: Response())
    capture.fetch('https://example.org/first')
    resumed = BudgetCapture(tmp_path, limits(http_attempts=1), get=lambda *a, **k: pytest.fail('Budget must precede network'))
    with pytest.raises(AcquisitionStopped, match='Cumulative'):
        resumed.fetch('https://example.org/second')


def test_unfinished_attempt_reserves_bytes_before_resume(tmp_path):
    cap = BudgetCapture(tmp_path, limits(transfer_bytes=100), get=lambda *a, **k: pytest.fail('No budget left'))
    folder = tmp_path/'attempts'/'old'; folder.mkdir()
    atomic_json(folder/'001.json', {'state': 'started'})
    with pytest.raises(AcquisitionStopped, match='Cumulative'):
        cap.fetch('https://example.org/new')


def test_semantically_rejected_http_200_can_retry(tmp_path):
    cap = BudgetCapture(tmp_path, limits(), get=lambda *a, **k: Response())
    first, journal = cap.fetch('https://example.org/data')
    record = json.loads(journal.read_text()); record['rejected'] = True
    atomic_json(journal, record)
    second, _ = cap.fetch('https://example.org/data')
    assert first != second and first.exists()


def make_raster(path, west, values, nodata=-96):
    with rasterio.open(path, 'w', driver='GTiff', count=1, width=values.shape[1], height=values.shape[0],
                       dtype='float32', nodata=nodata, crs='EPSG:27700', transform=from_origin(west, 100, 10, 10)) as ds:
        ds.write(values.astype('float32'), 1)


def test_overlap_checks_cells_masks_encoding_and_phase(tmp_path):
    left, right = tmp_path/'left.tif', tmp_path/'right.tif'
    a = np.array([[50, 0, -96, 70]]*3)
    b = np.array([[0, -96, 70, 80]]*3)
    make_raster(left, 0, a); make_raster(right, 10, b)
    assert c.compare_window(left, right, [10, 70, 40, 100]) == 9
    for changed, west, nodata in [(b+1, 10, -96), (b, 11, -96), (b, 10, -97)]:
        make_raster(right, west, changed, nodata)
        with pytest.raises(ValueError):
            c.compare_window(left, right, [10, 70, 40, 100])
    make_raster(right, 10, b)
    with rasterio.open(right, 'r+') as ds:
        mask = ds.read_masks(1); mask[1, 0] = 0; ds.write_mask(mask)
    with pytest.raises(ValueError, match='masks'):
        c.compare_window(left, right, [10, 70, 40, 100])


def test_acquisition_resume_seams_reference_and_sealed_replay(pilot, tmp_path, monkeypatch):
    """Small real GeoTIFFs exercise the entire pipeline without a live provider."""
    import copy
    import shutil
    from rasterio.io import MemoryFile
    from rasterio.windows import from_bounds
    root = tmp_path/'canary'
    original = pilot.manifest['records'][0]
    west, _, _, north = original['qa']['bounds']
    south = north-100
    recipe = c.plan()
    recipe['bounds'] = [west, south, west+200, north]
    recipe['tiles'] = [
        {'id': 'r0c0', 'row': 0, 'column': 0, 'core_bounds': [west,south,west+100,north], 'halo_bounds': [west,south,west+110,north]},
        {'id': 'r0c1', 'row': 0, 'column': 1, 'core_bounds': [west+100,south,west+200,north], 'halo_bounds': [west+90,south,west+200,north]}]
    recipe['limits'] = limits(response_bytes=1024*1024, transfer_bytes=64*1024*1024)
    monkeypatch.setattr(c, 'plan', lambda *a, **k: copy.deepcopy(recipe))
    def inventory(destination, **kwargs):
        destination = Path(destination)
        for name in [r['metadata_file'] for r in pilot.manifest['products'].values()]:
            shutil.copyfile(pilot.root/name, destination/name)
        return copy.deepcopy(pilot.manifest['products'])
    monkeypatch.setattr(c.p, 'inventory', inventory)
    monkeypatch.setattr(c, 'description', lambda *a: {'status': 'available', 'envelope': original['qa']['bounds'], 'grid': original['native_grid']})
    calls = []
    def get(url, *, params, **kwargs):
        calls.append(params)
        if len(calls) == 5:
            return Response(503, [b'temporary outage'])
        params = dict(params)
        source, metric = params['coverage'].split('-')
        record = next(r for r in pilot.records.values() if r['source'] == source and r['metric'] == metric)
        bounds = list(map(float, params['bbox'].split(',')))
        with rasterio.open(pilot.root/record['path']) as ds:
            window = from_bounds(*bounds, transform=ds.transform).round_offsets().round_lengths()
            data = ds.read(1, window=window)
            profile = {**ds.profile, 'width': data.shape[1], 'height': data.shape[0], 'transform': ds.window_transform(window)}
        with MemoryFile() as memory:
            with memory.open(**profile) as output:
                output.write(data, 1)
            return Response(chunks=[memory.read()])
    monkeypatch.setattr(requests, 'get', get)
    with pytest.raises(AcquisitionStopped, match='HTTP 503'):
        c.acquire(root, pilot)
    assert len(json.loads((root/'records.json').read_text())) == 4
    assert not (root/'manifest.json').exists()
    # Simulate the observed power/interruption damage: a zero-filled checkpoint
    # and one lost HTTP journal. The unprovable response must be reacquired.
    checkpoint = json.loads((root/'records.json').read_text())
    damaged = root/checkpoint[0]['http_record']
    damaged.write_bytes(b'\0'*200)
    (root/'records.json').write_bytes(b'\0'*512)
    recovered = c.recover_checkpoint(root, pilot)
    assert recovered['accepted_rasters'] == 3
    assert len(recovered['damaged_journals_retained']) == 1
    assert (root/recovered['original_checkpoint']).read_bytes() == b'\0'*512
    assert damaged.with_suffix('.json.damaged').read_bytes() == b'\0'*200
    assert BudgetCapture(root, recipe['limits']).usage()['charged_bytes'] >= 1024*1024
    sealed = c.acquire(root, pilot)
    assert len(calls) == 20  # 18 accepted + outage + lost journal; three provable successes reused
    assert sealed['verification']['exact_seam_pairs'] == 9
    assert sealed['verification']['reference_source_indicator_cells'] == 1800
    monkeypatch.setattr(requests, 'get', lambda *a, **k: pytest.fail('Offline replay contacted provider'))
    assert c.verify(root, pilot) == sealed['verification']
    with pytest.raises(FileExistsError):
        c.acquire(root, pilot)
    (root/'records.json').write_bytes(b'[]')
    with pytest.raises(ValueError, match='evidence changed'):
        c.verify(root, pilot)


def test_failed_checkpoint_flush_preserves_previous_commit(tmp_path, monkeypatch):
    import os
    target = tmp_path/'checkpoint.json'
    atomic_json(target, {'accepted': 1})
    def fail(*args):
        raise OSError('Flush failed')
    monkeypatch.setattr(os, 'fsync', fail)
    with pytest.raises(OSError, match='Flush failed'):
        atomic_json(target, {'accepted': 2})
    assert json.loads(target.read_text()) == {'accepted': 1}
