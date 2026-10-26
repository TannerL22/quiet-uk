"""Bounded connection checks: identity, distance, missing support and length."""
from copy import deepcopy
from dataclasses import replace
import math

import numpy as np
import pytest
from shapely.geometry import LineString, box

from quiet_uk.conditional_reflection_domain import (reflection_cases,
    reflection_execution_policy, reflection_scene_supported)
from quiet_uk.conditional_road_reflections import (combine_path_density,
    facade_inputs, integrate_selected_road_paths, materialize_reflection, road_point_energy)
from quiet_uk.conditional_scene_domain import execution_policy
from quiet_uk.physical_scene_assumptions import assumption_cases
from quiet_uk.road_scene_builder import NativeTerrain, RoadSceneBuilder, segment_road
from quiet_uk.screened_reflection import screened_wall_attenuation
from quiet_uk.conditional_road_propagation import REFERENCE_AIR


def cases():
    return next(c for c in assumption_cases() if c['case_id']=='combined_reference'), reflection_cases()[1]


def fixture():
    terrain=NativeTerrain(np.zeros((100,100)),np.ones((100,100)),(-100,-100,100,100),synthetic=True)
    building=dict(feature_id='wall',geometry=box(-80,-30,80,-20))
    builder=RoadSceneBuilder(terrain,lambda xy:[dict(row=0,column=0,code=50,state='reported_class') for _ in xy],
        [building],[],maximum_reflections=1)
    features={'wall':dict(kind='building',original_height_status='provisional_surface_height',
                         native_summary=dict(surface_odn_m=dict(p10=8.,p50=8.,p90=8.)))}
    return builder, features, dict(status='conditional_at_grade_reference')


def paths():
    plan=dict(wall_start_xy=[-100.,-20.],wall_end_xy=[100.,-20.])
    return [dict(path_id='direct',transfer=dict(status='conditional_research',relative_transfer=np.ones((2,8)),direct_distance_m=30.)),
            dict(path_id='wall',plan_input=plan,transfer=dict(status='conditional_research',relative_transfer=np.full((2,8),.5),reflected_distance_m=70.))]


def test_real_native_bridge_preserves_evidence_and_keeps_complete_noise_withheld():
    builder,features,source_policy=fixture();physical,reflection=cases()
    prepared=builder.prepare_point([-10.,0.],[0.,30.]);old=deepcopy(prepared)
    r=road_point_energy(prepared,features,physical,source_policy,reflection,lambda d:np.ones((3,8)))
    assert r['status']=='conditional_research' and prepared==old
    assert len(r['reflected_evidence'])==1 and len(r['paths'])==2
    assert r['reflected_evidence'][0]['transfer']['candidate_kernel_scene']['scene_kind']=='conditional_real_input'
    assert not r['complete_physical_path_support'] and r['all_inclusive_noise_db'] is None
    assert r['reflection_search']['support_complete'] is False


def test_reflection_requires_exact_policy_and_does_not_open_the_default_gate():
    builder,features,sp=fixture();physical,reflection=cases();p=builder.prepare_point([-10.,0.],[0.,30.])
    c=materialize_reflection(p['reflections'][0],p,features,physical,sp,reflection)
    s={k:v for k,v in c['candidate_scene'].items() if k in ('points','source','receiver','source_g','ground_segments','obstacles','opaque','infinite_lateral_extent','scene_kind')}
    s['conditional_input_policy']=execution_policy(physical)
    token=reflection_execution_policy(physical,reflection)
    assert reflection_scene_supported(s,c['wall'],token)
    for change in (dict(path_scope='all_paths'),dict(real_area_validated=True),dict(absolute_release_eligible=True),dict(reflection_case_id='invented')):
        assert not reflection_scene_supported(s,c['wall'],replace(token,**change))
    r=screened_wall_attenuation(s,c['wall'],1000,REFERENCE_AIR,absorption_frequency_policy='exact_base10')
    assert r['homogeneous']['reason']=='real_area_scene_not_qualified'
    changed=deepcopy(reflection);changed['absorption_by_band'][0]=.3
    with pytest.raises(ValueError):reflection_execution_policy(physical,changed)


def test_each_path_uses_its_own_source_weighted_air_distance():
    calls=[]
    def power(distance):calls.append(distance);return np.full((3,8),math.exp(-distance/100))
    r=combine_path_density(paths(),power)
    assert calls==[30.,70.]
    np.testing.assert_allclose(r['relative_energy_per_m'],math.exp(-.3)+.5*math.exp(-.7),rtol=1e-14)


@pytest.mark.parametrize('duplicate',['identity','reversed_facade','overlapping_facade'])
def test_duplicate_paths_cannot_increase_the_total(duplicate):
    p=paths();extra=deepcopy(p[1])
    if duplicate!='identity':
        extra['path_id']='other-id';extra['plan_input']={k:v for k,v in extra['plan_input'].items()}
        a,b=extra['plan_input']['wall_start_xy'],extra['plan_input']['wall_end_xy']
        extra['plan_input'].update(wall_start_xy=b,wall_end_xy=a)
        if duplicate=='overlapping_facade':extra['plan_input'].update(wall_start_xy=[-50.,-20.],wall_end_xy=[50.,-20.])
    with pytest.raises(ValueError):combine_path_density(p+[extra],lambda d:np.ones((3,8)))


def test_unknown_selected_reflection_withholds_sum_but_retains_known_direct_energy():
    p=paths();p[1]['transfer']=dict(status='withheld',reasons=['missing_reflector_height'])
    r=combine_path_density(p,lambda d:np.ones((3,8)))
    assert r['status']=='withheld' and r['relative_energy_per_m'] is None
    np.testing.assert_array_equal(r['known_selected_energy_per_m'],np.ones((2,3,8)))


def test_unknown_reflected_air_does_not_reuse_shorter_direct_air():
    r=combine_path_density(paths(),lambda d:np.ones((3,8)) if d<50 else None)
    assert r['status']=='withheld' and r['relative_energy_per_m'] is None


def test_known_absent_reflection_does_not_request_unneeded_air_support():
    p=paths();p[1]['transfer']['relative_transfer'][:]=0
    r=combine_path_density(p,lambda d:np.ones((3,8)) if d<50 else None)
    assert r['status']=='conditional_research'
    np.testing.assert_array_equal(r['relative_energy_per_m'],np.ones((2,3,8)))


def test_search_budget_exhaustion_stays_withheld():
    builder,features,sp=fixture();physical,reflection=cases();p=builder.prepare_point([-10.,0.],[0.,30.])
    p['reflections']=[];p['reflection_search']['reason']='reflection_search_budget_exceeded'
    r=road_point_energy(p,features,physical,sp,reflection,lambda d:np.ones((3,8)))
    assert r['status']=='withheld' and r['relative_energy_per_m'] is None


def test_facade_with_both_legs_inside_its_own_footprint_is_held():
    builder,features,sp=fixture();builder.maximum_reflections=20;physical,reflection=cases()
    p=builder.prepare_point([-10.,0.],[0.,30.])
    r=road_point_energy(p,features,physical,sp,reflection,lambda d:np.ones((3,8)))
    assert r['status']=='withheld'
    assert 'reflector_hit_adjacent_to_own_footprint_interior' in r['reasons']


def test_coincident_facades_with_different_roof_tops_are_not_arbitrarily_resolved():
    builder,features,sp=fixture();physical,reflection=cases();p=builder.prepare_point([-10.,0.],[0.,30.])
    features['other']=deepcopy(features['wall']);features['other']['native_summary']['surface_odn_m']['p50']=12.
    p['reflections'][0]['coincident_feature_ids']=['wall','other']
    c=materialize_reflection(p['reflections'][0],p,features,physical,sp,reflection)
    assert 'coincident_reflector_top_ambiguity' in c['geometry_blockers']


def test_source_length_is_applied_once_to_the_selected_path_sum():
    geometry=LineString([[-10.,0.],[0.,0.],[10.,0.]])
    source=dict(source_cells=segment_road('test',geometry),prepared_length_m=geometry.length)
    r=integrate_selected_road_paths(source,[0.,30.],lambda xy:combine_path_density(paths(),lambda d:np.ones((3,8))))
    np.testing.assert_allclose(r['selected_paths_whole_line_relative_energy'],30.)
    assert r['whole_road_relative_energy'] is None and r['all_inclusive_noise_db'] is None


@pytest.mark.parametrize('dimension',['height','width'])
def test_wall_cannot_be_smaller_than_its_assumed_apparent_dimensions(dimension):
    builder,features,sp=fixture();physical,reflection=cases();p=builder.prepare_point([-10.,0.],[0.,30.])
    ref=p['reflections'][0]
    if dimension=='height':features['wall']['native_summary']['surface_odn_m']['p50']=.6
    else:ref['plan_input'].update(wall_start_xy=[0.,-20.],wall_end_xy=[.6,-20.])
    c=facade_inputs(ref,features,physical,reflection)
    assert any('below_assumed_view' in reason for reason in c['geometry_blockers'])


@pytest.mark.parametrize('change',['source','unfolded'])
def test_mismatched_world_or_unfolded_geometry_is_rejected(change):
    builder,features,sp=fixture();physical,reflection=cases();p=builder.prepare_point([-10.,0.],[0.,30.])
    ref=p['reflections'][0]
    if change=='source':ref['plan_input']['source_xy'][0]+=1
    else:ref['wall']['unfolded_x_m']+=1
    with pytest.raises(ValueError):materialize_reflection(ref,p,features,physical,sp,reflection)
