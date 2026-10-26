import importlib.util
import math
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('spacing_checker',ROOT/'scripts/95_check_refined_road_spacing.py')
checker=importlib.util.module_from_spec(spec);spec.loader.exec_module(checker)

def setup(energy=1.):
    source=dict(link_id='road',source_cells=[dict(segment_id='cell',start_xy_bng=[0.,0.],end_xy_bng=[8.,0.],length_m=8.)])
    original=dict(leaves=[dict(source_cell_id='cell',left_fraction=0.,right_fraction=.375,length_m=3.),dict(source_cell_id='cell',left_fraction=.375,right_fraction=1.,length_m=5.)],
        whole_road_relative_energy=np.full((2,3,8),8*energy).tolist(),prior_worst_spacing_change_db=9.3)
    checker.BANK.clear();checker.BANK[('road','combined_reference')]=original
    return source

def run(source,evaluator):
    return checker.finer_rule(source,evaluator,partition=None,controls={},seed_evaluations=[dict(physical_case_id='combined_reference')])

def test_smaller_spacing_preserves_length_and_turn_partition():
    result=run(setup(),lambda xy:dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8))))
    assert result['status']=='finer_spacing_check_passed'
    assert result['evaluation_count']==8
    assert sum(n['weight_length_m'] for n in result['evaluations'])==pytest.approx(8.)
    assert np.allclose(result['whole_road_relative_energy'],8.)
    assert [n['xy_bng'][0] for n in result['evaluations']]==pytest.approx([.375,1.125,1.875,2.625,3.625,4.875,6.125,7.375])
    assert result['maximum_absolute_change_db']==0

@pytest.mark.parametrize('j',range(8))
def test_any_new_unknown_withholds_check(j):
    count=0
    def evaluate(xy):
        nonlocal count
        index=count;count+=1
        return dict(status='withheld',reasons=['missing']) if index==j else dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8)))
    result=run(setup(),evaluate)
    assert result['status']=='finer_spacing_check_withheld_or_changed'
    assert result['whole_road_relative_energy'] is None
    assert result['change_from_adaptive_db'] is None
    assert result['unknown_reason_counts']=={'missing':1}

def test_changed_numeric_result_is_not_a_pass():
    result=run(setup(),lambda xy:dict(status='conditional_research',relative_energy_per_m=np.full((2,3,8),2.)))
    assert result['status']=='finer_spacing_check_withheld_or_changed'
    assert result['maximum_absolute_change_db']==pytest.approx(10*math.log10(2))
    assert result['all_inclusive_noise_db'] is None
    assert not result['real_area_validated']
