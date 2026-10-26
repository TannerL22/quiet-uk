import numpy as np
import pytest
import hashlib
import json
from pathlib import Path
import runpy
import rasterio
from rasterio.transform import from_origin

from quiet_uk.transport_readiness import SOURCES, joint_reported_support
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.evidence_semantics import CONTRACT
from quiet_uk.explorer import file_hash, json_bytes

audit = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/40_audit_transport_readiness.py'))['run']


def test_disjoint_sets_count_all_eight_patterns_and_preserve_unknowns():
    layers = {}
    for bit, source in enumerate(SOURCES):
        values = np.array([50. if code & (1 << bit) else 0. for code in range(8)]).reshape(2, 4)
        layers[source] = values
    result = joint_reported_support(layers)
    assert result['cells'] == 8
    assert result['all_three_reported_cells'] == result['no_sources_reported_cells'] == 1
    assert result['reported_cells_by_source'] == dict.fromkeys(SOURCES, 4)
    assert all(item['cells'] == 1 for item in result['disjoint_source_sets'])
    assert sum(item['cells'] for item in result['disjoint_source_sets']) == result['cells']
    assert not any('db' in key or 'total' in key for key in result)


def test_masked_nodata_is_not_a_contribution_even_with_positive_payload():
    layers = dict.fromkeys(SOURCES, np.full((1, 3), 50.))
    layers['aircraft'] = np.ma.array([[3.4e38, np.nan, 0.]], mask=[[True, True, False]])
    result = joint_reported_support(layers)
    assert result['all_three_reported_cells'] == 0
    assert result['reported_cells_by_source']['aircraft'] == 0
    assert next(s['cells'] for s in result['disjoint_source_sets'] if s['sources'] == ['road', 'rail']) == 3


@pytest.mark.parametrize('invalid', [np.nan, np.inf, -96., -1., 161.])
def test_unrecognised_unmasked_encodings_reject_inventory(invalid):
    layers = dict.fromkeys(SOURCES, np.full((2, 2), 50.))
    layers['road'] = np.full((2, 2), invalid)
    with pytest.raises(ValueError, match='encoding'):
        joint_reported_support(layers)


def test_missing_source_and_mismatched_grid_shape_reject_inventory():
    layers = dict.fromkeys(SOURCES, np.full((2, 2), 50.))
    with pytest.raises(ValueError, match='once each'):
        joint_reported_support({'road': layers['road'], 'rail': layers['rail']})
    layers['aircraft'] = np.full((3, 2), 50.)
    with pytest.raises(ValueError, match='shapes'):
        joint_reported_support(layers)


@pytest.fixture
def release(tmp_path):
    root = tmp_path / 'release'
    root.mkdir()
    records, files = [], {}
    for metric in ('Lden', 'Lday', 'Lnight'):
        for source in SOURCES:
            values = {'road': [[50, 50], [50, 50]], 'rail': [[50, 0], [0, 50]],
                      'aircraft': [[50, 50], [-96, 0]]}[source]
            name = f'{source}-{metric}.tif'
            with rasterio.open(root / name, 'w', driver='GTiff', count=1, dtype='float32',
                               width=2, height=2, crs='EPSG:27700', nodata=-96,
                               transform=from_origin(400005, 200005, 10, 10)) as raster:
                raster.write(np.array(values, dtype='float32'), 1)
            files[name] = file_hash(root / name)
            records.append({'site': 'area', 'tile': 'one', 'source': source, 'metric': metric,
                            'core_bounds': [400005, 199985, 400025, 200005],
                            'path': name, 'sha256': files[name]})
    manifest = {'schema_version': 3, 'evidence_contract': CONTRACT, 'records': records,
                'files': files, 'products': {source: {'reference_period': None,
                                                    'reference_period_status': 'unspecified_by_provider'}
                                           for source in SOURCES}}
    return root, manifest


def save_manifest(root, manifest):
    base = {k: v for k, v in manifest.items() if k != 'release_id'}
    manifest['release_id'] = 'pilot-' + hashlib.sha256(json_bytes(base)).hexdigest()[:20]
    (root / 'manifest.json').write_bytes(json_bytes(manifest))


def test_offline_audit_owns_native_cells_and_keeps_metadata_gate_closed(release, tmp_path):
    root, manifest = release
    save_manifest(root, manifest)
    report = audit(root, tmp_path / 'output')
    assert report['combination_status'] == 'not_qualified'
    assert len(report['source_hashes']) == 9
    for metric in report['summary']:
        assert metric['cells'] == 4 and metric['area_km2'] == .0004
        assert metric['all_three_reported_cells'] == 1
        assert metric['all_three_reported_percent'] == 25
    assert json.loads((tmp_path / 'output/report.json').read_bytes()) == report


def test_offline_audit_rejects_changed_source_bytes(release, tmp_path):
    root, manifest = release
    save_manifest(root, manifest)
    path = root / 'road-Lden.tif'
    path.write_bytes(path.read_bytes() + b'changed')
    with pytest.raises(CatalogueIntegrityError, match='identity'):
        audit(root, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()


def test_offline_audit_rejects_half_cell_shift_even_with_rehashed_sources(release, tmp_path):
    root, manifest = release
    record = next(r for r in manifest['records'] if r['source'] == 'aircraft' and r['metric'] == 'Lden')
    with rasterio.open(root / record['path'], 'r+') as raster:
        raster.transform = from_origin(400000, 200005, 10, 10)
    record['sha256'] = manifest['files'][record['path']] = file_hash(root / record['path'])
    save_manifest(root, manifest)
    with pytest.raises(ValueError, match='integer-aligned'):
        audit(root, tmp_path / 'output')
    assert not (tmp_path / 'output').exists()
