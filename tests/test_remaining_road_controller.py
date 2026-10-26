"""Native replay must preserve full primary rules and never overlook history."""
import copy
import importlib.util
from pathlib import Path

import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remaining_controller_test',ROOT/'scripts/113_complete_remaining_road_checks.py')
controller=importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


def sample():
    source=dict(link_id='road',prepared_length_m=8.,source_cells=[dict(segment_id='cell',
        start_xy_bng=[0.,0.],end_xy_bng=[8.,0.],length_m=8.)])
    row=controller.pipeline.ORIGINAL_INTEGRATOR(source,lambda xy:dict(
        status='conditional_research',relative_energy_per_m=np.ones((2,3,8))))
    observations=row.pop('evaluations')
    return row,observations


def test_complete_primary_replay_preserves_integral_and_leaf_partition():
    old,points=sample()
    new=controller.revalidated_row(old,points)
    assert np.array_equal(new['whole_road_relative_energy'],np.full((2,3,8),8.))
    assert new['indicator_db']==0
    assert [(p['left_fraction'],p['right_fraction']) for p in new['leaves']]==[(p['left_fraction'],p['right_fraction']) for p in old['leaves']]
    assert new['full_history_revalidated'] and new['native_primary_revalidation']
    assert 'native_primary_revalidation' not in old


def test_new_unknown_cannot_retain_an_old_primary_value():
    row,points=sample()
    points[0]=dict(points[0],status='withheld',relative_energy_per_m=None,reasons=['height'])
    with pytest.raises(ValueError,match='must remain numeric'):
        controller.revalidated_row(row,points)


@pytest.mark.parametrize('value',[-1.,float('nan'),float('inf')])
def test_invalid_fresh_density_cannot_reuse_an_old_integral(value):
    row,points=sample()
    points[0]['relative_energy_per_m']=np.full((2,3,8),value)
    with pytest.raises(ValueError,match='Finite nonnegative'):
        controller.revalidated_row(row,points)


def test_full_history_revalidation_rejects_a_previously_omitted_peak():
    row,points=sample()
    extra=copy.deepcopy(points[0]);extra.update(node_id=len(points),fraction=.123,xy_bng=[.984,0.],
        relative_energy_per_m=np.full((2,3,8),100.))
    points.append(extra);row['evaluation_count']+=1
    with pytest.raises(ValueError,match='full-history primary diagnostic'):
        controller.revalidated_row(row,points)


def test_harmless_additional_history_is_retained_in_final_leaf_rules():
    row,points=sample()
    extra=copy.deepcopy(points[0]);extra.update(node_id=len(points),fraction=.123,xy_bng=[.984,0.])
    points.append(extra);row['evaluation_count']+=1
    new=controller.revalidated_row(row,points)
    assert len(points)-1 in new['leaves'][0]['historical_probe_ids']
    assert np.array_equal(new['whole_road_relative_energy'],row['whole_road_relative_energy'])
