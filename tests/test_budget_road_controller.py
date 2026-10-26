"""Resource bounds and disk-backed scene capture for larger road quadratures."""
import gzip
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('budget_pipeline_test', ROOT/'scripts/109_integrate_budget_held_roads.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)


def dense_source():
    source = dict(link_id='road', prepared_length_m=100., source_cells=[dict(
        segment_id='cell', start_xy_bng=[0., 0.], end_xy_bng=[100., 0.], length_m=100.)])
    partition = dict(cells={'cell': [dict(fraction=i/430, reasons=['geometry']) for i in range(431)]}, partition_complete=False)
    return source, partition


def numeric(xy):
    return dict(status='conditional_research', relative_energy_per_m=np.ones((2, 3, 8)).tolist(),
                native_profile_sha256='native', candidate_kernel_scene=dict(points=xy))


def test_dense_geometry_completes_without_changing_accuracy_or_partition():
    source, partition = dense_source()
    small = pipeline.ORIGINAL_INTEGRATOR(source, numeric, partition=partition)
    assert small['termination'] == 'initial_partition_budget_exhausted'
    controls = pipeline.bounded_controls(2150, 0)
    larger = pipeline.ORIGINAL_INTEGRATOR(source, numeric, partition=partition, controls=controls)
    assert larger['termination'] == 'diagnostic_tolerance_met'
    assert larger['partition'] == partition
    assert larger['covered_source_length_m'] == pytest.approx(100.)
    assert np.allclose(larger['whole_road_relative_energy'], 100.)
    assert {k: v for k, v in controls.items() if k != 'maximum_evaluations'} == {
        k: v for k, v in pipeline.runner.DEFAULTS.items() if k != 'maximum_evaluations'}


def test_historical_unknown_remains_a_hold_under_larger_budget():
    source, partition = dense_source()
    seed = dict(source_cell_id='cell', fraction=.5, xy_bng=[50., 0.], status='withheld', reasons=['missing_height'])
    result = pipeline.ORIGINAL_INTEGRATOR(source, numeric, partition=partition,
        controls=pipeline.bounded_controls(2150, 1), seed_evaluations=[seed])
    assert result['whole_road_relative_energy'] is None
    assert result['unknown_positive_sample_observed']
    assert result['evaluation_count'] == 1


def test_native_history_replay_streams_scene_and_preserves_witness(tmp_path):
    (tmp_path/'point_scenes').mkdir()
    (tmp_path/'case_progress').mkdir()
    old = dict(link_id='road', physical_case_id='combined_reference', source_cell_id='cell',
               fraction=.25, xy_bng=[25., 0.], native_profile_sha256='native', status='withheld')
    pipeline.WORK.update(output=tmp_path, mode='prepare')
    result = pipeline.captured_integration(dict(link_id='road'), numeric,
        partition=None, controls={}, seed_evaluations=[old])
    node = result['evaluations'][0]
    assert node['status'] == 'conditional_research'
    assert node['historical_status'] == 'withheld'
    assert node['fraction'] == old['fraction'] and node['xy_bng'] == old['xy_bng']
    assert 'candidate_kernel_scene' not in node
    reference = node['kernel_scene_reference']
    with gzip.open(tmp_path/reference['path'], 'rt') as stream:
        scene = json.loads(next(stream))
        assert next(stream, None) is None
    assert scene['candidate_kernel_scene'] == dict(points=old['xy_bng'])
    assert scene['record_id'] == reference['record_id']
    assert json.loads(next((tmp_path/'case_progress').iterdir()).read_bytes())['complete']


@pytest.mark.parametrize('minimum,retained', [(0, 1), (6, 0), (-5, 0), (5, -1), (True, 0), (5, 1.2)])
def test_invalid_workload_counts_rejected(minimum, retained):
    with pytest.raises(ValueError):
        pipeline.bounded_controls(minimum, retained)
