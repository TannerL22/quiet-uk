import copy
import math
import numpy as np
import pytest
from shapely.geometry import Polygon,LineString

from quiet_uk.adaptive_road_integration import integrate_adaptive,geometry_breakpoints,indicator_db
from quiet_uk.road_scene_builder import NativeTerrain,RoadSceneBuilder


def source(length=10.):
    return dict(prepared_length_m=length,source_cells=[dict(segment_id='a',start_xy_bng=[0.,0.],end_xy_bng=[length,0.],length_m=length)])


def evaluator(function):
    return lambda xy:dict(status='conditional_research',relative_energy_per_m=np.full((2,3,8),function(xy[0])))


def test_constant_conserves_length_and_zero():
    for density in (0.,2.):
        src=source(); old=copy.deepcopy(src)
        result=integrate_adaptive(src,evaluator(lambda x:density))
        assert result['diagnostic_tolerance_met']
        assert result['source_length_m']==pytest.approx(sum(p['length_m'] for p in result['leaves']))
        assert np.allclose(result['whole_road_relative_energy'],10*density)
        assert np.allclose(result['offset_relative_energy'],10*density)
        assert src==old
        assert result['all_inclusive_noise_db'] is None
        assert not result['refinement_convergence_proven']


def test_finite_line_against_analytic_integral():
    result=integrate_adaptive(source(20.),evaluator(lambda x:1/(1+(x-10)**2)),controls=dict(tolerance_db=.005,minimum_panel_m=.001))
    exact=2*math.atan(10)
    assert result['diagnostic_tolerance_met']
    assert abs(10*math.log10(np.asarray(result['whole_road_relative_energy'])[0,0,0]/exact))<.005
    assert result['refinements']>0


def test_declared_discontinuity_is_not_crossed():
    partition=dict(cells={'a':[dict(fraction=x,reasons=[]) for x in (0.,.37123,1.)]},partition_complete=False)
    result=integrate_adaptive(source(),evaluator(lambda x:1 if x<3.7123 else 100),partition=partition)
    assert result['diagnostic_tolerance_met']
    assert np.asarray(result['whole_road_relative_energy'])[0,0,0]==pytest.approx(3.7123+100*6.2877)
    assert all(not p['left_fraction']<.37123<p['right_fraction'] for p in result['leaves'])


def test_offset_retains_observed_peak_without_claiming_error_bound():
    # Parent and both quarter points miss this positive interval; Gauss catches it.
    result=integrate_adaptive(source(1.),evaluator(lambda x:100 if .20<x<.22 else 1),controls=dict(maximum_panel_m=1.,minimum_panel_m=.0001,tolerance_db=.1))
    assert result['refinements']>0
    assert result['diagnostic_tolerance_met']
    exact=1+99*.02
    actual_error=abs(10*math.log10(np.asarray(result['whole_road_relative_energy'])[0,0,0]/exact))
    assert actual_error<.25  # Unpartitioned step: much better than the 4.74 dB miss.
    assert actual_error>result['controls']['tolerance_db']  # Proxy is not a proof.
    assert result['rigorous_noise_error_bound_db'] is None


@pytest.mark.parametrize('unknown_x',[.5,.25,.75,.5-1/(2*math.sqrt(3))])
def test_any_observed_unknown_is_sticky(unknown_x):
    def evaluate(xy):
        if abs(xy[0]-unknown_x)<1e-10:return dict(status='withheld',reasons=['test_missing'])
        return evaluator(lambda x:1)(xy)
    result=integrate_adaptive(source(1.),evaluate)
    assert result['whole_road_relative_energy'] is None
    assert result['unknown_positive_sample_observed']
    assert result['termination']=='unknown_positive_contribution_observed'


@pytest.mark.parametrize('budget',[1,4,5,6,10])
def test_budget_never_publishes_partial_road(budget):
    result=integrate_adaptive(source(),evaluator(lambda x:math.exp(x)),controls=dict(maximum_evaluations=budget))
    assert result['evaluation_count']<=budget
    assert result['whole_road_relative_energy'] is None
    assert result['termination'] in ('evaluation_budget_exhausted','initial_partition_budget_exhausted')
    assert sum(p['length_m'] for p in result['leaves'])==pytest.approx(result['covered_source_length_m'])


def test_transactional_refinement_and_unknown_probe():
    calls=[]
    def evaluate(xy):
        calls.append(xy[0])
        if len(calls)==6:return dict(status='withheld',reasons=['new_missing'])
        return evaluator(lambda x:math.exp(10*x))(xy)
    result=integrate_adaptive(source(1.),evaluate,controls=dict(maximum_evaluations=6))
    assert result['covered_source_length_m']==pytest.approx(1.)
    assert len(result['leaves'])==1
    assert result['whole_road_relative_energy'] is None
    assert result['unknown_positive_sample_observed']


def test_error_proxy_does_not_cancel_local_errors():
    result=integrate_adaptive(source(8.),evaluator(lambda x:1+x*x if x<4 else 33-(x-4)**2),controls=dict(maximum_refinements=1,maximum_panel_m=4.,tolerance_db=.00001))
    assert np.max(np.asarray(result['absolute_error_proxy']))>0
    assert result['whole_road_relative_energy'] is None


def test_error_indicator_zero_and_singular():
    zero=np.zeros((2,3,8)); one=np.ones((2,3,8))
    assert indicator_db(zero,zero)==0
    assert indicator_db(zero,one) is None
    assert indicator_db(one,one) is None
    assert indicator_db(one,.01*one)==pytest.approx(-10*math.log10(.99))


@pytest.mark.parametrize('control',[dict(tolerance_db=0),dict(tolerance_db=float('nan')),dict(maximum_evaluations=True),dict(maximum_refinements=0),dict(minimum_panel_m=5),dict(unknown=1)])
def test_invalid_controls(control):
    with pytest.raises(ValueError):integrate_adaptive(source(),evaluator(lambda x:1),controls=control)


@pytest.mark.parametrize('fractions',[[0.,.5,.5,1.],[.1,1.],[0.,.7,.3,1.],[0.,float('nan'),1.],[0.,1.1,1.]])
def test_invalid_partition(fractions):
    partition=dict(cells={'a':[dict(fraction=x,reasons=[]) for x in fractions]})
    with pytest.raises(ValueError):integrate_adaptive(source(),evaluator(lambda x:1),partition=partition)


@pytest.mark.parametrize('density',[np.full((2,3,8),-1.),np.full((2,3,8),float('nan')),np.ones((3,8))])
def test_invalid_energy(density):
    with pytest.raises(ValueError):integrate_adaptive(source(),lambda xy:dict(status='conditional_research',relative_energy_per_m=density))


def test_duplicate_and_incomplete_source():
    src=source();src['source_cells']*=2
    with pytest.raises(ValueError):integrate_adaptive(src,evaluator(lambda x:1))
    src=source();src['prepared_length_m']=11
    with pytest.raises(ValueError):integrate_adaptive(src,evaluator(lambda x:1))


def test_source_grid_and_obstacle_ray_partition():
    terrain=NativeTerrain(np.zeros((10,10)),np.ones((10,10),bool),(0,0,20,20),2.,synthetic=True)
    records=[dict(feature_id='wall',geometry=LineString([(5,2),(5,3)]))]
    builder=RoadSceneBuilder(terrain,lambda xy:[],[],records)
    src=dict(prepared_length_m=10.,source_cells=[dict(segment_id='a',start_xy_bng=[10.,.1],end_xy_bng=[10.,10.1],length_m=10.)])
    result=geometry_breakpoints(src,[0.,0.],builder)
    cuts=result['cells']['a'];grid=[c['fraction'] for c in cuts if 'source_terrain_grid' in c['reasons']]
    rays=[c['fraction'] for c in cuts if 'obstacle_vertex_ray' in c['reasons']]
    assert grid==pytest.approx([.19,.39,.59,.79,.99])
    assert rays==pytest.approx([.39,.59])
    assert not result['partition_complete']


def test_breakpoints_preserve_all_cells_and_polygon_holes():
    terrain=NativeTerrain(np.zeros((10,10)),np.ones((10,10),bool),(0,0,20,20),2.,synthetic=True)
    polygon=Polygon([(4,1),(6,1),(6,5),(4,5)],holes=[[(4.5,2),(5.5,2),(5.5,3),(4.5,3)]])
    builder=RoadSceneBuilder(terrain,lambda xy:[],[dict(feature_id='b',geometry=polygon)],[])
    src=dict(prepared_length_m=10.,source_cells=[dict(segment_id='a',start_xy_bng=[10.,.1],end_xy_bng=[10.,10.1],length_m=10.)])
    partition=geometry_breakpoints(src,[0.,0.],builder)
    result=integrate_adaptive(src,evaluator(lambda x:1),partition=partition)
    assert result['covered_source_length_m']==pytest.approx(10.)
    assert partition['breakpoint_reason_counts']['obstacle_vertex_ray']>=8


def test_deterministic_reuse_and_weight_receipt():
    run=lambda:integrate_adaptive(source(1.),evaluator(lambda x:math.exp(5*x)),controls=dict(tolerance_db=.01,minimum_panel_m=.001))
    a,b=run(),run()
    assert a==b
    node_ids=[(n['source_cell_id'],n['fraction']) for n in a['evaluations']]
    assert len(node_ids)==len(set(node_ids))
    reconstructed=np.zeros((2,3,8))
    for leaf in a['leaves']:
        for node in leaf['node_ids'][1:3]:
            reconstructed+=np.asarray(a['evaluations'][node]['relative_energy_per_m'])*leaf['length_m']/2
    assert np.allclose(reconstructed,a['whole_road_relative_energy'])


def test_prior_probe_cannot_be_lost_and_is_not_double_weighted():
    density=lambda x:100 if .10<x<.12 else 1
    seed=dict(status='conditional_research',source_cell_id='a',fraction=.11,xy_bng=[.11,0.],relative_energy_per_m=np.full((2,3,8),100).tolist())
    result=integrate_adaptive(source(1.),evaluator(density),seed_evaluations=[seed],controls=dict(minimum_panel_m=.0001))
    assert result['refinements']>0
    assert result['evaluations'][0]['evaluation_origin']=='verified_prior_generation'
    assert result['new_evaluation_count']==result['evaluation_count']-1
    assert np.asarray(result['known_evaluated_relative_energy'])[0,0,0]>2.5


@pytest.mark.parametrize('change',[dict(fraction=0.),dict(xy_bng=[1.,0.]),dict(source_cell_id='missing')])
def test_invalid_seed(change):
    seed=dict(status='conditional_research',source_cell_id='a',fraction=.5,xy_bng=[5.,0.],relative_energy_per_m=np.ones((2,3,8)).tolist())
    seed.update(change)
    with pytest.raises((ValueError,KeyError)):integrate_adaptive(source(),evaluator(lambda x:1),seed_evaluations=[seed])


def test_seed_reuse_and_unknown_survive_refinement():
    seed=dict(status='withheld',reasons=['old_missing'],source_cell_id='a',fraction=.11,xy_bng=[.11,0.])
    result=integrate_adaptive(source(1.),evaluator(lambda x:1),seed_evaluations=[seed])
    assert result['unknown_positive_sample_observed']
    assert result['whole_road_relative_energy'] is None


def test_preflight_does_not_evaluate_an_impossible_initial_rule():
    def never(xy):raise AssertionError('An impossible initial rule must be held before evaluation')
    result=integrate_adaptive(source(),never,controls=dict(maximum_evaluations=14))
    assert result['termination']=='initial_partition_budget_exhausted'
    assert result['minimum_initial_observations']==15
    assert result['evaluation_count']==0
    assert result['unevaluated_source_length_m']==10.
    assert result['whole_road_relative_energy'] is None


def test_unknown_seed_stops_new_physics_calls():
    def never(xy):raise AssertionError('Existing unknown already withholds the whole source')
    seed=dict(status='withheld',reasons=['missing'],source_cell_id='a',fraction=.11,xy_bng=[.11,0.])
    result=integrate_adaptive(source(1.),never,seed_evaluations=[seed])
    assert result['termination']=='unknown_positive_contribution_observed'
    assert result['new_evaluation_count']==0
    assert result['covered_source_length_m']==0.


def test_first_unknown_panel_stops_further_base_sampling():
    result=integrate_adaptive(source(),lambda xy:dict(status='withheld',reasons=['missing']))
    assert result['evaluation_count']==5
    assert result['covered_source_length_m']==pytest.approx(10/3)
    assert result['unevaluated_source_length_m']==pytest.approx(20/3)
    assert result['whole_road_relative_energy'] is None


def test_complete_cached_rule_needs_no_new_physics():
    fractions=[.5,.25,.75,.5-1/(2*math.sqrt(3)),.5+1/(2*math.sqrt(3))]
    seeds=[dict(status='conditional_research',source_cell_id='a',fraction=t,xy_bng=[t,0.],relative_energy_per_m=np.ones((2,3,8)).tolist()) for t in fractions]
    def never(xy):raise AssertionError('Exact observations should be reused')
    result=integrate_adaptive(source(1.),never,seed_evaluations=seeds,controls=dict(maximum_evaluations=5))
    assert result['diagnostic_tolerance_met']
    assert result['new_evaluation_count']==0
    assert np.allclose(result['whole_road_relative_energy'],1.)
