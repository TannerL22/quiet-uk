"""Unknowns and ambiguous spatial references must survive observation reuse."""
import copy
import importlib.util
from pathlib import Path
import numpy as np
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('corrected_road_runner',ROOT/'scripts/101_rerun_corrected_roads.py')
runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)

def source():
    return dict(link_id='road',source_cells=[dict(segment_id='cell',start_xy_bng=[0.,0.],end_xy_bng=[8.,0.],length_m=8.)])

def node(t,status='conditional_research',cell='cell',density=1.):
    return dict(link_id='road',physical_case_id='combined_reference',source_cell_id=cell,fraction=t,xy_bng=[8*t,0.],status=status,
        reasons=['still_unsupported'] if status=='withheld' else [],relative_energy_per_m=None if status=='withheld' else np.full((2,3,8),density).tolist())

@pytest.mark.parametrize('other',[node(.125,cell='different_cell'),dict(node(.25),xy_bng=[1.,0.])],ids=['different_source_cell','different_fraction'])
def test_spatial_alias_is_not_reused_without_unique_physical_identity(other):
    nodes=[node(.125),other,node(.375)]
    cached=runner.unique_xy_cache(nodes)
    assert (1.,0.) not in cached
    assert (3.,0.) in cached

def setup(cached):
    runner.TASK['xy_cache']={'road|combined_reference':runner.unique_xy_cache(cached)}
    runner.checker.BANK.clear();runner.checker.BANK[('road','combined_reference')]=dict(
        leaves=[dict(source_cell_id='cell',left_fraction=0.,right_fraction=1.,length_m=8.)],
        whole_road_relative_energy=np.full((2,3,8),8.).tolist(),prior_worst_spacing_change_db=9.3)

def check(cached,evaluate):
    setup(cached)
    return runner.cached_finer_rule(source(),evaluate,partition=None,controls={},seed_evaluations=[dict(physical_case_id='combined_reference')])

def test_known_exact_reuse_preserves_positive_length_weights_and_energy():
    called=[]
    def evaluate(xy):called.append(xy);return dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8)))
    result=check([node(t) for t in (.125,.375,.625,.875)],evaluate)
    assert not called
    assert result['status']=='finer_spacing_check_passed' and result['maximum_absolute_change_db']==0
    assert np.allclose(result['whole_road_relative_energy'],8.)
    assert sum(n['weight_length_m'] for n in result['evaluations'])==8.
    assert all(n['finer_observation_origin']=='verified_corrected_observation' for n in result['evaluations'])

def test_unknown_corrected_observation_is_sticky_in_finer_check():
    cached=[node(.125,status='withheld')];saved=copy.deepcopy(cached)
    result=check(cached,lambda xy:dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8))))
    assert cached==saved and result['whole_road_relative_energy'] is None
    assert result['status']=='finer_spacing_check_withheld_or_changed'
    assert result['unknown_reason_counts']=={'still_unsupported':1}

def test_reuse_does_not_hide_a_large_numeric_spacing_change():
    result=check([node(.125,density=4.)],lambda xy:dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8))))
    assert result['status']=='finer_spacing_check_withheld_or_changed' and result['maximum_absolute_change_db']>.1
    assert result['all_inclusive_noise_db'] is None

def historical():
    return dict(node(.125,status='withheld'),node_id=7,native_profile_sha256='original-native',candidate_kernel_scene={'points':'original'},_directory='old-generation')

@pytest.mark.parametrize('field,value',[('native_profile_sha256','changed-native'),('candidate_kernel_scene',{'points':'changed'})])
def test_native_reevaluation_cannot_change_inputs_under_a_compatibility_claim(field,value):
    old=historical();runner.TASK['unknowns']={'road|combined_reference':[old]}
    fresh=dict(native_profile_sha256='original-native',candidate_kernel_scene={'points':'original'},status='withheld',reasons=['different_issue']);fresh[field]=value
    with pytest.raises(AssertionError):runner.collect_unknowns(source(),lambda xy:fresh,partition=None,controls={},seed_evaluations=[old])

def test_remaining_native_failure_does_not_inherit_a_recovered_status():
    old=historical();saved=copy.deepcopy(old);runner.TASK['unknowns']={'road|combined_reference':[old]}
    fresh=dict(native_profile_sha256='original-native',candidate_kernel_scene={'points':'original'},status='withheld',reasons=['remaining_input_gap'])
    row=runner.collect_unknowns(source(),lambda xy:fresh,partition=None,controls={},seed_evaluations=[old])
    assert old==saved
    assert row['evaluations'][0]['status']=='withheld' and row['evaluations'][0]['reasons']==['remaining_input_gap']
    assert row['evaluations'][0]['model_reuse']['prior_reasons']==['still_unsupported']
