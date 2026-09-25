import json
import hashlib
from pathlib import Path
import shutil
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from quiet_uk.provider_audit import compare_rasters, description, request_params, verify


BOUNDS = [400005., 199805., 400205., 200005.]


def raster(path, *, value_change=False, mask_change=False, shift=0, dtype='float32', nodata=-96):
    a = np.arange(400, dtype=dtype).reshape(20, 20)/10 + 40
    a[0, 0] = 0
    if value_change:
        a[1, 1] += 0.125
    profile = dict(driver='GTiff', width=20, height=20, count=1, dtype=dtype,
                   crs='EPSG:27700', nodata=nodata,
                   transform=from_origin(BOUNDS[0]+shift, BOUNDS[3], 10, 10))
    with rasterio.open(path, 'w', **profile) as ds:
        ds.write(a, 1)
        if mask_change:
            mask = np.full((20, 20), 255, dtype='uint8')
            mask[1, 1] = 0
            ds.write_mask(mask)
    return path


@pytest.mark.parametrize('change,status,field,count', [
    ({}, 'exact_match', 'raw_difference_cells', 0),
    ({'value_change': True}, 'encoding_or_value_mismatch', 'valid_difference_cells', 1),
    ({'mask_change': True}, 'encoding_or_value_mismatch', 'mask_difference_cells', 1),
    ({'nodata': -999}, 'encoding_or_value_mismatch', 'nodata_equal', False),
    ({'shift': 10}, 'grid_mismatch', 'request_grid_matches', [True, False]),
])
def test_audit_detects_value_mask_nodata_and_grid_changes(tmp_path, change, status, field, count):
    left = raster(tmp_path/'left.tif')
    right = raster(tmp_path/'right.tif', **change)
    result = compare_rasters(left, right, BOUNDS)
    assert result['status'] == status
    assert result[field] == count


def test_http_success_with_xml_exception_cannot_pass(tmp_path):
    error = tmp_path/'error.tif'
    error.write_text('<ServiceExceptionReport><ServiceException>Coverage missing</ServiceException></ServiceExceptionReport>', encoding='utf-8')
    assert description(error)['status'] == 'service_exception'
    assert compare_rasters(error, error, BOUNDS)['status'] == 'unavailable'


def test_requests_retain_independent_identifiers_and_native_recipe():
    first = dict(request_params('1.0.0', 'observed:first', BOUNDS))
    second = request_params('2.0.1', 'observed__second', BOUNDS)
    assert first['coverage'] == 'observed:first'
    assert first['width'] == first['height'] == '20'
    assert ('coverageId', 'observed__second') in second
    assert [v for k, v in second if k == 'subset'] == ['E(400005.0,400205.0)', 'N(199805.0,200005.0)']
    with pytest.raises(ValueError):
        request_params('2.0.1', 'x', [0, 0, 10000, 10000])


@pytest.fixture(scope='module')
def captured_audit(tmp_path_factory):
    root = tmp_path_factory.mktemp('provider-audit')
    with zipfile.ZipFile(Path(__file__).parent/'fixtures/provider_audit.zip') as archive:
        assert all((root/name).resolve().is_relative_to(root.resolve()) for name in archive.namelist())
        archive.extractall(root)
    return root


def test_complete_provider_audit_reproduces_without_network(captured_audit, monkeypatch):
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'request', lambda *a, **kw: pytest.fail('Offline audit attempted network'))
    result = verify(captured_audit)
    assert result['report_reproduced']
    assert result['quietness_bounds_authorized'] is False
    report = json.loads((captured_audit/'report.json').read_text('utf-8'))
    # A service exception and an altered edge resolution must not disappear into
    # an aggregate "all methods agree" claim.
    cases = {r['id']: r for r in report['results']}
    assert cases['road-Lnight-reported']['protocol_comparison']['status'] == 'unavailable'
    assert cases['road-Lden-envelope-edge']['protocol_comparison']['status'] == 'grid_mismatch'
    for source in ('road', 'rail'):
        assert cases[f'{source}-Lden-transition']['protocol_comparison']['status'] == 'exact_match'
    assert cases['aircraft-Lden-transition']['protocol_comparison']['status'] == 'unavailable'
    assert cases['aircraft-Lden-transition']['reference_comparisons']['2.0.1']['status'] == 'exact_match'
    assert result['protocol_counts'] == {'exact_match': 15, 'unavailable': 11, 'grid_mismatch': 2}
    assert result['reference_counts'] == {'exact_match': 31, 'unavailable': 9}


@pytest.mark.parametrize('version', ['1.0.0', '2.0.1'])
def test_production_native_grid_guard_already_rejects_clipped_edge(captured_audit, version):
    from quiet_uk.source_pilot import inspect_raster, native_grid
    root = captured_audit
    plan = json.loads((root/'plan.json').read_text('utf-8'))
    case = next(c for c in plan['cases'] if c['id'] == 'road-Lden-envelope-edge')
    grid = native_grid((root/'evidence/road-Lden-2.0.1-description.xml').read_bytes())
    with pytest.raises(ValueError):
        inspect_raster(root/f'raw/{case["id"]}-{version}.tif', case['bounds'], grid, 'Lden')


def test_changed_captured_bytes_are_rejected(captured_audit, tmp_path):
    root = tmp_path/'changed'
    shutil.copytree(captured_audit, root)
    path = root/'raw/road-Lden-reported-1.0.0.tif'
    path.write_bytes(path.read_bytes()+b'changed')
    with pytest.raises(ValueError, match='checksum'):
        verify(root)


def test_aircraft_tiff_alias_probes_preserve_failures(tmp_path):
    with zipfile.ZipFile(Path(__file__).parent/'fixtures/provider_format_probe.zip') as archive:
        assert all((tmp_path/name).resolve().is_relative_to(tmp_path.resolve()) for name in archive.namelist())
        archive.extractall(tmp_path)
    index = json.loads((tmp_path/'probe.json').read_text('utf-8'))
    for name, digest in index['files'].items():
        assert hashlib.sha256((tmp_path/name).read_bytes()).hexdigest() == digest
    for i in range(2):
        assert description(tmp_path/f'format-{i}.tif')['status'] == 'service_exception'
        http = json.loads((tmp_path/f'format-{i}.tif.http.json').read_text('utf-8'))
        assert http['status'] == 200
        assert http['sha256'] == index['files'][f'format-{i}.tif']


def test_response_must_match_discovered_identifier_and_recipe(captured_audit, tmp_path):
    root = tmp_path/'changed-request'
    shutil.copytree(captured_audit, root)
    name = 'raw/road-Lden-reported-1.0.0.tif.http.json'
    path = root/name
    record = json.loads(path.read_text('utf-8'))
    record['request_url'] = record['request_url'].replace('width=20', 'width=10')
    path.write_text(json.dumps(record), encoding='utf-8')
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    manifest['files'][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    (root/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError, match='pinned audit recipe'):
        verify(root)
