"""Scheduling helpers must reproduce the engine's coordinates and journal format exactly."""
import gzip
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest

from quiet_uk import calculation_scheduling as plan
from quiet_uk.adaptive_road_integration import DEFAULTS, integrate_adaptive

ROOT = Path(__file__).resolve().parents[1]


def source():
    # Two irregular cells with interior partition cuts, as cached by script 119.
    cells = [dict(segment_id='a', start_xy_bng=[507012.3, 179003.7], end_xy_bng=[507031.9, 179017.1]),
             dict(segment_id='b', start_xy_bng=[507031.9, 179017.1], end_xy_bng=[507029.4, 179040.6])]
    for cell in cells:
        cell['length_m'] = math.dist(cell['start_xy_bng'], cell['end_xy_bng'])
    partition = dict(cells={'a': [dict(fraction=0.), dict(fraction=.3141592653589793), dict(fraction=.71), dict(fraction=1.)],
                            'b': [dict(fraction=0.), dict(fraction=1/3), dict(fraction=1.)]})
    return dict(link_id='road', source_cells=cells, prepared_length_m=math.fsum(c['length_m'] for c in cells)), partition


def engine_order(src, partition):
    seen = []
    def record(xy):
        seen.append(xy)
        return dict(status='conditional_research', relative_energy_per_m=np.zeros((2, 3, 8)))
    result = integrate_adaptive(src, record, partition=partition, controls=dict(DEFAULTS, maximum_evaluations=10**6))
    return seen, result['leaves']


def test_primary_plan_matches_engine_bits_order_and_leaves():
    src, partition = source()
    seen, leaves = engine_order(src, partition)
    points, mine = plan.primary_plan(src, partition, DEFAULTS['maximum_panel_m'])
    assert [plan.coordinate_key(p) for p in points] == [plan.coordinate_key(p) for p in seen]
    assert [(l['source_cell_id'], l['left_fraction'], l['right_fraction']) for l in leaves] == \
           [(l['source_cell_id'], l['left_fraction'], l['right_fraction']) for l in mine]


def test_finer_plan_matches_script_119_formula():
    src, partition = source()
    _, leaves = engine_order(src, partition)
    cells = {c['segment_id']: c for c in src['source_cells']}
    expected = []
    for leaf in leaves:
        cell = cells[leaf['source_cell_id']]
        for j in range(4):
            t = leaf['left_fraction']+(leaf['right_fraction']-leaf['left_fraction'])*(j+.5)/4
            expected.append([a+(b-a)*t for a, b in zip(cell['start_xy_bng'], cell['end_xy_bng'])])
    assert [plan.coordinate_key(p) for p in plan.finer_plan(src, leaves)] == [plan.coordinate_key(p) for p in expected]


def test_truncation_keeps_whole_panel():
    points = list(range(23))
    assert plan.truncate_to_panel(points, 0) == points[:5]
    assert plan.truncate_to_panel(points, 7) == points[:10]
    assert plan.truncate_to_panel(points, 22) == points


@pytest.fixture(scope='module')
def engine():
    spec = importlib.util.spec_from_file_location('pinned_engine_for_scheduling_test', ROOT/'scripts/119_calculate_real_road_reflections.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_staged_chunks_are_engine_journal_records(engine, tmp_path):
    observations, staging = tmp_path/'observations', tmp_path/'staging'
    journal = engine.PointJournal(observations, 'run')
    journal.store([1.5, 2.25], dict(status='conditional_research', relative_energy_per_m=[[0.1]]))
    journal.close()
    staging.mkdir()
    staged = [dict(fingerprint='run', xy_bng=[3.0, 4.0+1e-9], value=dict(status='withheld', reasons=['x'])),
              dict(fingerprint='run', xy_bng=[5.0, 6.0], value=dict(status='conditional_research', relative_energy_per_m=[[0.2]]))]
    plan.write_staged(staging/'0001-a.jsonl.gz', staged)
    assert plan.merge_staged(staging, observations) == 1
    assert sorted(p.name for p in observations.glob('chunk-*')) == ['chunk-000001.jsonl.gz', 'chunk-000002.jsonl.gz']
    restored = engine.PointJournal(observations, 'run')
    for record in staged:
        key = restored.key(record['xy_bng'])
        # v5 caches compact JSON text and decodes through value(); v4 caches dicts.
        value = restored.value(key) if hasattr(restored, 'value') else restored.cache[key]
        assert value == record['value']
    restored.store([7.0, 8.0], dict(status='withheld'))
    restored.close()
    assert (observations/'chunk-000003.jsonl.gz').exists()
    assert plan.journal_keys(observations, 'run') == {plan.coordinate_key(p) for p in ([1.5, 2.25], [3.0, 4.0+1e-9], [5.0, 6.0], [7.0, 8.0])}
    with pytest.raises(ValueError, match='another calculation'):
        plan.journal_keys(observations, 'other')


def test_journal_keys_ignore_partial_active_tail(tmp_path):
    (tmp_path/'active.jsonl').write_bytes(plan.journal_line(dict(fingerprint='run', xy_bng=[1., 2.], value={})) + b'{"partial":')
    assert plan.journal_keys(tmp_path, 'run') == {plan.coordinate_key([1., 2.])}


def test_earliest_staged_witness_is_promoted_once(tmp_path):
    road, staging = tmp_path/'road', tmp_path/'staging'
    staging.mkdir()
    for phase, index in (('fine', 2), ('base', 40), ('base', 7)):
        (staging/plan.witness_name(phase, index)).write_text(f'{phase}{index}')
    assert plan.promote_first_witness(staging, road)
    assert (road/'first_held_profile.json.gz').read_text() == 'base7'
    assert not plan.promote_first_witness(staging, road)  # an existing witness always wins
