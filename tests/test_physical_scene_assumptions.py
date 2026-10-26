from copy import deepcopy
from collections import Counter
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from quiet_uk.favourable_propagation import favourable_boundary
from quiet_uk.physical_scene_assumptions import (absolute_surface_summary, assumption_cases,
    feature_top, ground_candidates, materialize_profile, pavement_spectrum)
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_operating_inputs import operating_spectrum


def case(name='baseline'):
    return next(c for c in assumption_cases() if c['case_id'] == name)


def building(status='provisional_surface_height'):
    return dict(kind='building',original_height_status=status,native_summary=dict(
        surface_odn_m={'p10':3.,'p50':6.,'p90':9.},reference_base_odn_m=1.))


def profile(crossings=None):
    return dict(length_m=10.,ground_native_samples=[[0.,5.,0,0,30,'reported_class'],[5.,10.,0,1,10,'reported_class']],
        crossings=crossings or [],kernel_scene=dict(points=[[0.,0.],[5.,0.],[10.,0.]],source=[0.,.05],receiver=[10.,4.],
            source_g=None,ground_segments=[[0.,10.,None]],obstacles=[],opaque=None,infinite_lateral_extent=None,scene_kind='unqualified'))


def crossing(kind='building',intervals=None,contacts=None):
    return dict(feature_id='a',kind=kind,intervals_m=intervals if intervals is not None else [[3.,4.]],contacts_m=contacts or [])


def resolve(p,features=None,name='baseline',**kwargs):
    return materialize_profile(p,features or {'a':building()},case(name),source_policy=dict(status='conditional_at_grade_reference'),**kwargs)


def test_cases_are_unique_and_explicit():
    cases=assumption_cases()
    assert len(cases) == 15 and len({c['case_id'] for c in cases}) == 15
    assert case()['missing_building_height_m'] is None
    assert case()['barrier_mode'] == 'omit_unverified'
    assert case()['source_g'] == 0


def test_absolute_top_uses_paired_native_surface_not_terrain_plus_relative_median():
    terrain=np.array([[0.,10.],[20.,30.]])
    surface=np.array([[8.,15.],[30.,34.]])
    ones=np.ones((2,2),dtype='uint16');mask=ones.astype(bool)
    result=absolute_surface_summary(terrain,surface,ones,ones,ones,ones,mask,mask)
    assert result['surface_odn_m']['p50'] == 22.5
    assert result['difference_agl_m']['p50'] == 6.5
    assert result['reference_base_odn_m'] == 30


@pytest.mark.parametrize('invalid', ['mask','nonfinite','different_survey','ambiguous_survey'])
def test_native_summary_excludes_invalid_pairs(invalid):
    t=np.zeros((2,2));d=np.full((2,2),5.);m=np.ones((2,2),dtype='uint16');s=m.copy();n=m.copy()
    if invalid == 'mask':m[0,0]=0
    if invalid == 'nonfinite':d[0,0]=np.nan
    if invalid == 'different_survey':s[0,0]=2
    if invalid == 'ambiguous_survey':s[0,0]=n[0,0]=65535
    result=absolute_surface_summary(t,d,m,m,n,s,np.ones((2,2),bool),np.ones((2,2),bool))
    assert result['paired_pixels'] == 3 and result['surface_odn_m']['p50'] == 5


def test_native_summary_dimension_mismatch():
    ones=np.ones((2,2))
    with pytest.raises(ValueError):absolute_surface_summary(ones,np.ones((3,3)),ones,ones,ones,ones,ones.astype(bool),ones.astype(bool))


def test_ground_keeps_full_denominator_and_unobserved_substrate():
    r=ground_candidates(profile()['ground_native_samples'],10.,case())
    assert r['full_length_candidate_mean_g'] == .75
    assert r['observed_g'] is None
    assert r['basis_lengths_m']['unobserved_substrate_reference'] == 5


@pytest.mark.parametrize('name',[c['case_id'] for c in assumption_cases()][:5])
def test_nodata_does_not_turn_into_uniform_numeric_ground(name):
    samples=[[0.,5.,0,0,0,'nodata'],[5.,10.,0,1,50,'reported_class']]
    r=ground_candidates(samples,10.,case(name))
    assert r['unknown_length_m'] == 5 and r['full_length_candidate_mean_g'] is None
    assert r['segments'][0][2] is None


@pytest.mark.parametrize('samples', [[[1,10,0,0,30,'reported_class']],[[0,5,0,0,30,'reported_class']],[[0,6,0,0,30,'reported_class'],[5,10,0,0,30,'reported_class']]])
def test_ground_rejects_missing_or_overlapping_intervals(samples):
    with pytest.raises(ValueError):ground_candidates(samples,10.,case())


def test_missing_building_height_is_reference_only_in_named_cases():
    f=building('withheld')
    assert feature_top(f,case())[0] is None
    assert feature_top(f,case('missing_buildings_6m')) == (7.,'hypothetical_missing_building_prism')
    f['native_summary']['reference_base_odn_m']=None
    assert feature_top(f,case('missing_buildings_6m'))[0] is None


def test_fence_does_not_become_solid_in_wall_cases():
    f=dict(kind='barrier',category='generic_fence_candidate',tagged_height_m=2.,reference_base_odn_m=5.)
    assert feature_top(f,case('walls_3m'))[0] is None
    assert feature_top(f,case('solid_fences_and_walls_3m'))[0] == 7


def test_untagged_wall_uses_declared_reference_and_embankment_is_not_duplicated():
    f=dict(kind='barrier',category='generic_wall_candidate',tagged_height_m=None,reference_base_odn_m=5.)
    assert feature_top(f,case('walls_3m'))[0] == 8
    f['category']='embankment_candidate'
    assert feature_top(f,case('solid_fences_and_walls_3m'))[0] is None


def test_materializer_preserves_originals_and_real_scene_identity():
    p=profile([crossing()]);old=deepcopy(p);r=resolve(p)
    assert p == old and r['candidate_scene']['scene_kind'] == 'conditional_real_input'
    assert r['status'] == 'conditional_geometry_candidate'
    assert r['candidate_scene']['obstacles'][0]['vertices'] == [[3.,6.],[4.,6.]]
    assert r['geometry_diagnostic']['obstacles_above_endpoint_sightline'] == 1
    assert r['receiver_noise_db'] is None and r['qualified_obstacle_top_odn_m'] is None
    scene=r['candidate_scene']
    result=favourable_boundary(scene['points'],scene['source'],scene['receiver'],1000.,
        source_g=scene['source_g'],ground_segments=scene['ground_segments'],obstacles=scene['obstacles'],
        opaque=scene['opaque'],infinite_lateral_extent=scene['infinite_lateral_extent'],scene_kind=scene['scene_kind'])
    assert result['status'] == 'withheld'


def test_bridge_context_does_not_get_at_grade_elevation():
    r=materialize_profile(profile(),{},case(),source_policy=dict(status='withheld_structure_proximity_conflict'))
    assert r['candidate_scene']['source'][1] is None
    assert 'source_surface_or_deck_unresolved' in r['geometry_blockers']


def test_endpoints_inside_buildings_remain_held():
    r=resolve(profile(),endpoint_ids={'receiver':['a']})
    assert 'endpoint_intersects_generalised_building' in r['geometry_blockers']


@pytest.mark.parametrize('issue',['height','below_terrain','endpoint','overlap'])
def test_unsupported_geometry_stays_held(issue):
    p=profile([crossing()]);f=building()
    if issue=='height':f['original_height_status']='withheld'
    if issue=='below_terrain':p['kernel_scene']['points'][1][1]=20.
    if issue=='endpoint':p['crossings'][0]['intervals_m']=[[0,4]]
    if issue=='overlap':p['crossings'].append(crossing(intervals=[[3.5,4.5]]))
    r=resolve(p,{'a':f})
    assert r['status'] == 'withheld_geometry_candidate'


def test_reflecting_facade_contact_at_unfolded_joint_is_not_a_second_obstacle():
    p=profile([crossing(intervals=[],contacts=[5.])])
    r=resolve(p,reflecting_feature_ids=['a'],reflection_x_m=5.)
    assert r['candidate_scene']['obstacles'] == []


def test_other_facade_contact_is_preserved():
    p=profile([crossing(intervals=[],contacts=[5.])])
    assert len(resolve(p)['candidate_scene']['obstacles']) == 1


def source(speed=40.):
    coefficients=json.loads((Path(__file__).parent/'fixtures/road_emission_2021_coefficients.json').read_bytes())
    categories={f:0. for f in VEHICLE_FIELDS};categories['cars_and_taxis']=1000.
    profiles={f:dict(period_fractions=dict(day=.5,evening=.2,night=.3)) for f in VEHICLE_FIELDS}
    speeds=dict.fromkeys(VEHICLE_FIELDS,speed)
    road=dict(annual_categories=categories,speed=dict(choices={f:dict(candidate_kmh=speed) for f in VEHICLE_FIELDS}),
              conditional_baseline=operating_spectrum(categories,profiles,speeds,coefficients))
    return road,coefficients


def test_reference_pavement_reproduces_existing_source_power():
    road,c=source();old=deepcopy(road)
    r=pavement_spectrum(road,c,'Reference road surface')
    for period,row in r['period_spectra'].items():
        np.testing.assert_allclose(row['relative_power_per_m'],road['conditional_baseline']['period_spectra'][period]['relative_power_per_m'],rtol=1e-12)
    assert road == old and r['actual_pavement'] is None


def test_pavement_domain_is_per_category_without_clamping():
    road,c=source()
    r=pavement_spectrum(road,c,'Worked surface')
    assert r['status'] == 'withheld' and r['candidate_speed_kmh'] == 40
    road,c=source(70.)
    assert pavement_spectrum(road,c,'Worked surface')['status'] == 'conditional_research_source_emission'


def test_zero_flow_unsupported_class_does_not_withhold_other_categories():
    road,c=source(70.);road['speed']['choices']['buses_and_coaches']['candidate_kmh']=40.
    assert pavement_spectrum(road,c,'Worked surface')['status'] == 'conditional_research_source_emission'


def review_module():
    path=Path(__file__).resolve().parents[1]/'scripts/88_verify_physical_scene_assumptions.py'
    spec=importlib.util.spec_from_file_location('physical_assumption_review',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_independent_review_records_exact_polygon_boundary_ties():
    from rasterio.transform import from_origin
    from shapely.geometry import box
    module=review_module();xx,yy=np.meshgrid([1.,3.,5.],[5.,3.,1.]);counts=Counter();ties=[]
    selected=module.native_selection(box(1.,1.,5.,5.),xx,yy,from_origin(0.,6.,2.,2.),'fixture','interior',counts,ties)
    assert selected.sum()==6 and counts['native_polygon_boundary_tie_pixels']==5
    assert all(c['boundary_distance_m']==0 for r in ties for c in r['cells'])


def test_independent_review_rejects_nonboundary_membership_disagreement(monkeypatch):
    from rasterio.transform import from_origin
    from shapely.geometry import box
    module=review_module();xx,yy=np.meshgrid([1.,3.,5.],[5.,3.,1.])
    monkeypatch.setattr(module,'rasterize',lambda *args,**kwargs:np.zeros((3,3),dtype='uint8'))
    with pytest.raises(AssertionError):
        module.native_selection(box(0.,0.,6.,6.),xx,yy,from_origin(0.,6.,2.,2.),'fixture','interior',Counter(),[])
