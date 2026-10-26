"""Conservation, joint hourly energy, conditional scope and numerical receipts."""
from copy import deepcopy
from dataclasses import replace
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
import pytest
from shapely.geometry import LineString,box

from quiet_uk.conditional_road_propagation import (A_WEIGHT_DB,REFERENCE_AIR,PERIODS,
    build_air_tables,direct_transfer,integrate_source_cells,receiver_metrics,road_guard,
    simplify_vertical,source_joint_power)
from quiet_uk.conditional_scene_domain import ConditionalExecution,execution_policy,scene_supported
from quiet_uk.favourable_propagation import atmospheric_absorption,direct_attenuation,EXACT_CENTRES
from quiet_uk.physical_scene_assumptions import assumption_cases,materialize_profile
from quiet_uk.propagation_canary import BANDS
from quiet_uk.propagation_geometry_cache import cached_geometry,geometry_cache
from quiet_uk.road_emission import vehicle_power
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_scene_builder import NativeTerrain,RoadSceneBuilder,segment_road


def case(name='baseline'):return next(c for c in assumption_cases() if c['case_id']==name)


def scene(**changes):
    r=dict(points=[[0.,0.],[20.,0.],[100.,0.]],source=[0.,.05],receiver=[100.,4.],
        source_g=0.,ground_segments=[[0.,100.,0.]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='conditional_real_input')
    r.update(changes);return r


def candidate(s=None):return dict(geometry_blockers=[],candidate_scene=s or scene())


def coefficients():return json.loads((Path(__file__).parent/'fixtures/road_emission_2021_coefficients.json').read_text())


def weather_calendar():
    weather=[dict(period=p,candidate_absorption_air=dict(REFERENCE_AIR,relative_humidity_percent=h)) for p in PERIODS for h in (10.,90.)]
    calendar=dict(groups={'cars':dict(slot_weights=[.45,.05]*3),'other':dict(slot_weights=[.05,.45]*3)},
        vehicle_profiles={f:dict(profile_group='cars' if f=='cars_and_taxis' else 'other') for f in VEHICLE_FIELDS})
    return weather,calendar


def road():
    return dict(annual_categories={f:2400. if f in ('cars_and_taxis','LGVs') else 0. for f in VEHICLE_FIELDS},
        speed=dict(choices={f:dict(candidate_kmh=70.) for f in VEHICLE_FIELDS}),
        conditional_baseline=dict(period_traffic={f:{p:dict(assumed_average_vehicles_per_hour=100. if f in ('cars_and_taxis','LGVs') else 0.) for p in PERIODS} for f in VEHICLE_FIELDS}))


def source():
    g=LineString([[-50.,0.],[0.,0.],[50.,0.]])
    return dict(link_id='road',geometry_wkt_bng=g.wkt,source_cells=segment_road('road',g),prepared_length_m=g.length)


def test_real_scene_requires_explicit_exact_declared_scope():
    policy=execution_policy(case())
    assert scene_supported('conditional_real_input',policy)
    assert not scene_supported('conditional_real_input')
    assert not scene_supported('unqualified',policy)
    assert not scene_supported('conditional_real_input',dict(physical_case_id='baseline'))
    for changes in (dict(path_scope='all_paths'),dict(real_area_validated=True),dict(absolute_release_eligible=True),dict(physical_case_id='invented'),dict(physical_policy_id='other')):
        assert not scene_supported('conditional_real_input',replace(policy,**changes))
    changed=case();changed['missing_building_height_m']=5
    with pytest.raises(ValueError):execution_policy(changed)


def test_default_kernel_gate_and_explicit_adapter_identity():
    s=scene();old=deepcopy(s)
    assert direct_attenuation(s,1000,REFERENCE_AIR,absorption_frequency_policy='exact_base10')['status']=='withheld'
    r=direct_transfer(candidate(s),case(),terrain_tolerance_m=0,retain_kernel_scene=True)
    assert r['status']=='conditional_research' and s==old
    assert r['input_scene_kind']=='conditional_real_input'
    assert r['candidate_kernel_scene']['scene_kind']=='conditional_real_input'
    assert r['scope']=='direct_only_reflections_omitted'
    policy_scene=dict(s,conditional_input_policy=execution_policy(case()))
    for bi,f in enumerate(BANDS):
        expected=direct_attenuation(policy_scene,f,REFERENCE_AIR,absorption_frequency_policy='exact_base10')
        for j,name in enumerate(('homogeneous','favourable')):
            assert r['relative_transfer'][j,bi]==pytest.approx(10**(-(expected[name+'_attenuation_db']-expected['atmospheric_attenuation_db'])/10),rel=1e-13)


@pytest.mark.parametrize('blocked',['missing_native_terrain','source_surface_or_deck_unresolved','missing_building_or_screen_height'])
def test_adapter_does_not_compute_held_geometry(blocked):
    r=direct_transfer(dict(geometry_blockers=[blocked],candidate_scene=None),case())
    assert r['status']=='withheld' and 'relative_transfer' not in r


@pytest.mark.parametrize('s',[scene(obstacles=None),scene(source_g=None),scene(opaque=False)])
def test_unknown_or_unsupported_physics_stays_unknown(s):
    r=direct_transfer(candidate(s),case())
    assert r['status']=='withheld' and r['reasons']


def test_vertical_simplification_preserves_bound_endpoints_and_native_input():
    points=[[float(x),math.sin(x/10)+.04*math.cos(x)] for x in range(201)];old=deepcopy(points)
    selected,error=simplify_vertical(points,.1)
    assert points==old and selected[0]==points[0] and selected[-1]==points[-1]
    assert len(selected)<len(points) and error<=.1
    mid=np.linspace(0,200,2001)
    assert np.max(np.abs(np.interp(mid,np.asarray(points)[:,0],np.asarray(points)[:,1])-np.interp(mid,np.asarray(selected)[:,0],np.asarray(selected)[:,1])))<=.1+1e-12
    exact,error=simplify_vertical(points,0)
    assert exact==points and error==0


@pytest.mark.parametrize('points',[[],[[0,0]],[[0,None],[1,1]],[[0,0],[0,1]],[[0,0],[1,math.inf]],[[0,0,0],[1,1,1]]])
def test_simplification_never_repairs_missing_invalid_native_geometry(points):
    with pytest.raises(ValueError):simplify_vertical(points)


@pytest.mark.parametrize('tolerance',[-.1,.251,math.nan])
def test_simplification_tolerance_is_explicit_and_bounded(tolerance):
    with pytest.raises(ValueError):simplify_vertical([[0,0],[1,0]],tolerance)


def test_per_path_cache_is_exact_restored_and_does_not_leak():
    calls=[]
    @cached_geometry
    def compute(points,scale=1):calls.append(1);return sum(points)*scale
    points=[1.,2.,3.,4.]
    with geometry_cache() as outer:
        assert compute(points)==10 and compute(points)==10
        assert outer['hits']==1 and outer['misses']==1
        with pytest.raises(RuntimeError):
            with geometry_cache() as inner:
                assert compute(points)==10 and inner['misses']==1
                raise RuntimeError('cleanup')
        assert compute(points)==10 and outer['hits']==2
        assert compute(points,scale=2)==20
    points[0]=10.
    assert compute(points)==19
    with geometry_cache() as fresh:assert compute(points)==19 and fresh['misses']==1
    assert len(calls)==5


@pytest.mark.parametrize('shape', ['flat','clear_terrain','blocked_terrain','roof','screens','slope'])
def test_all_bands_cached_uncached_kernel_results_match_exactly(shape):
    s=scene(scene_kind='synthetic',ground_segments=[[0.,40.,.3],[40.,100.,.7]])
    if shape=='clear_terrain':s['points']=[[0,0],[20,.03],[70,.02],[100,0]]
    if shape=='blocked_terrain':s['points']=[[0,0],[40,5],[60,5],[100,0]]
    if shape=='roof':s['obstacles']=[dict(vertices=[[40.,5.],[60.,5.]],roof_g=0.)]
    if shape=='screens':s['obstacles']=[dict(vertices=[[30.,4.]]),dict(vertices=[[70.,5.]])]
    if shape=='slope':s.update(points=[[0,0],[100,10]],source=[0,.05],receiver=[100,14])
    original=[direct_attenuation(s,f,REFERENCE_AIR,absorption_frequency_policy='exact_base10') for f in BANDS]
    with geometry_cache() as cache:
        results=[direct_attenuation(s,f,REFERENCE_AIR,absorption_frequency_policy='exact_base10') for f in BANDS]
    assert results==original and cache['hits']>0
    for r in results:
        if r['status']=='conditional_research':assert r['homogeneous']['regulatory_implementation_qualified'] is False


def test_joint_hourly_air_matches_direct_weighted_energy_and_not_average_weather():
    weather,calendar=weather_calendar();air=build_air_tables(weather,calendar,maximum_distance_m=1000.)
    for p in PERIODS:
        indices=[i for i,w in enumerate(weather) if w['period']==p]
        for f in BANDS:
            table=air['tables'][(p,f)]
            for g in table['groups']:
                weights=np.array(calendar['groups'][g]['slot_weights'])[indices];weights/=weights.sum()
                for distance in (0.,17.3,123.456,1000.):
                    exact=sum(weight*10**(-atmospheric_absorption(EXACT_CENTRES[f],**weather[i]['candidate_absorption_air'])['alpha_db_per_km']*distance/10000) for weight,i in zip(weights,indices))
                    actual=np.interp(distance,table['distance_m'],table['transmission'][table['groups'].index(g)])
                    assert actual>=exact-1e-14
                    assert abs(10*math.log10(actual/exact))<=.002+1e-10
    assert all(b['max_interpolation_db']<=.002 for b in air['interpolation_bounds'])
    t=air['tables'][('day',8000)]
    assert abs(10*math.log10(t['transmission'][0,-1]/t['transmission'][1,-1]))>1.


def test_source_weather_keeps_category_timing_and_consumes_power_per_m_once():
    weather,calendar=weather_calendar();air=build_air_tables(weather,calendar,maximum_distance_m=1000.)
    r=road();coeff=coefficients();actual=source_joint_power(r,coeff,calendar,air,1000.)
    single=vehicle_power(coeff,'1',70.,'Reference road surface')
    for bi,f in enumerate(BANDS):
        table=air['tables'][('day',f)];ts=[table['transmission'][table['groups'].index(g),-1] for g in ('cars','other')]
        assert actual[0,bi]==pytest.approx(single[bi]*100/(70000)*sum(ts),rel=1e-13)
    zero=source_joint_power(r,coeff,calendar,air,0)
    assert zero==pytest.approx(np.tile(single*200/70000,(3,1)),rel=1e-13)
    r['annual_categories']['cars_and_taxis']=None
    assert source_joint_power(r,coeff,calendar,air,0) is None
    assert source_joint_power(road(),coeff,calendar,air,1000.1) is None


@pytest.mark.parametrize('bad',['missing_period','zero_weight','unknown_air'])
def test_air_table_cannot_drop_positive_unknown_weather_or_period(bad):
    weather,calendar=weather_calendar()
    if bad=='missing_period':weather=weather[:4];calendar['groups']={g:dict(slot_weights=v['slot_weights'][:4]) for g,v in calendar['groups'].items()}
    if bad=='zero_weight':calendar['groups']['cars']['slot_weights']=[0.]*6
    if bad=='unknown_air':weather[0]['candidate_absorption_air']['temperature_c']=None
    with pytest.raises(ValueError):build_air_tables(weather,calendar,maximum_distance_m=10.)


def test_receiver_metrics_energy_weighted_octaves_period_penalties_and_zero():
    e=np.zeros((3,8));e[:,4]=[100.,10.,1.];m=receiver_metrics(e)
    assert m['day']==20 and m['evening']==10 and m['night']==0
    assert m['den']==pytest.approx(10*math.log10((1200+40*math.sqrt(10)+80)/24))
    assert receiver_metrics(np.zeros((3,8)))=={p:None for p in (*PERIODS,'den')}
    e[:,4]=0;e[:,0]=100
    assert receiver_metrics(e)['day']==pytest.approx(20-26.2)
    assert A_WEIGHT_DB.tolist()==[-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1]


def test_constant_per_metre_energy_conserves_every_cell_at_both_spacings():
    s=source();e=np.ones((2,3,8))*1000
    r=integrate_source_cells(s,[0,30],lambda xy:dict(status='conditional_research',relative_energy_per_m=e))
    assert np.asarray(r['whole_road_relative_energy'])==pytest.approx(e*100)
    assert len(r['evaluations'])==3*len(s['source_cells'])
    for h in r['quadrature_history']:assert h['known_source_length_m']==100 and h['unknown_source_length_m']==0
    assert all(v==0 for d in r['spacing_change_db'].values() for v in d.values())
    assert r['all_inclusive_noise_db'] is None and not r['refinement_convergence_proven']


def test_unknown_positive_fine_cell_withholds_whole_road_not_known_partial_energy():
    def evaluate(xy):
        if -10<xy[0]<0:return dict(status='withheld',reasons=['unknown_positive_path'])
        return dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8)))
    r=integrate_source_cells(source(),[0,30],evaluate)
    assert r['quadrature_history'][0]['whole_road_relative_energy'] is not None
    assert r['whole_road_relative_energy'] is None and r['spacing_change_db'] is None
    assert r['quadrature_history'][-1]['unknown_source_length_m']==12.5
    assert np.asarray(r['known_evaluated_relative_energy'])==pytest.approx(np.full((2,3,8),87.5))


def test_midpoint_refinement_agrees_with_analytic_finite_line_integral():
    radius=math.hypot(30.,3.95)
    def evaluate(xy):
        e=1000*10**(-1.1)/(xy[0]**2+radius**2)
        return dict(status='conditional_research',relative_energy_per_m=np.full((2,3,8),e))
    r=integrate_source_cells(source(),[0,30],evaluate,subdivisions=(1,32))
    exact=1000*10**(-1.1)*2*math.atan(50/radius)/radius
    assert np.asarray(r['whole_road_relative_energy'])==pytest.approx(np.full((2,3,8),exact),rel=2e-5)
    assert not r['refinement_convergence_proven']


def test_known_zero_does_not_create_minus_infinity_or_invalid_spacing_change():
    r=integrate_source_cells(source(),[0,30],lambda xy:dict(status='conditional_research',relative_energy_per_m=np.zeros((2,3,8))))
    assert r['whole_road_relative_energy'] is not None
    assert all(v==0 for d in r['spacing_change_db'].values() for v in d.values())


@pytest.mark.parametrize('bad',['duplicate','omitted','wrong_length','invalid_energy','bad_subdivision'])
def test_line_integration_rejects_lost_or_invalid_positive_support(bad):
    s=source();subdivisions=(1,2);e=np.ones((2,3,8))
    if bad=='duplicate':s['source_cells'].append(s['source_cells'][0])
    if bad=='omitted':s['source_cells'].pop()
    if bad=='wrong_length':s['source_cells'][0]['length_m']=1
    if bad=='invalid_energy':e[0,0,0]=-1
    if bad=='bad_subdivision':subdivisions=(2,1)
    with pytest.raises(ValueError):integrate_source_cells(s,[0,30],lambda xy:dict(status='conditional_research',relative_energy_per_m=e),subdivisions=subdivisions)


def builder(buildings):
    terrain=NativeTerrain(np.zeros((100,100)),np.ones((100,100)),(-100,-100,100,100),synthetic=True)
    return RoadSceneBuilder(terrain,lambda xy:[],buildings,[],maximum_reflections=0)


def test_tiny_positive_source_footprint_cannot_disappear_between_quadrature_points():
    b=builder([dict(feature_id='tiny',geometry=box(-3,-1,-2,1))])
    assert road_guard(source(),b,{},case(),dict(status='conditional_at_grade_reference'),[0,30])==['positive_source_length_intersects_generalised_building']


def test_unknown_height_fan_and_deck_context_keep_whole_road_unknown():
    b=builder([dict(feature_id='tiny',geometry=box(-2,12,-1,13))]);features={'tiny':dict(kind='building',original_height_status='withheld',native_summary=dict(reference_base_odn_m=0))}
    assert road_guard(source(),b,features,case(),dict(status='conditional_at_grade_reference'),[0,30])==['unresolved_building_height_in_source_receiver_fan']
    assert road_guard(source(),b,features,case('combined_reference'),dict(status='conditional_at_grade_reference'),[0,30])==[]
    assert road_guard(source(),b,features,case('combined_reference'),dict(status='withheld_structure_proximity_conflict'),[0,30])==['source_surface_or_deck_unresolved']


def runner():
    path=Path(__file__).parents[1]/'scripts/89_integrate_conditional_roads.py'
    spec=importlib.util.spec_from_file_location('conditional_runner_test',path);r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r);return r


def test_node_writer_retains_declared_identity_and_rejects_mismatched_cases():
    r=runner();node=dict(physical_case_id='baseline',status='withheld')
    assert r.node_record('road','baseline',node)==dict(link_id='road',physical_case_id='baseline',status='withheld')
    assert r.node_record('road','baseline',dict(status='withheld'))['physical_case_id']=='baseline'
    with pytest.raises(ValueError):r.node_record('road','combined_reference',node)


def test_worker_submission_is_bounded_and_preserves_input_order():
    r=runner()
    class Pool:
        outstanding=0;maximum=0
        def submit(self,function,source):
            self.outstanding+=1;self.maximum=max(self.maximum,self.outstanding)
            pool=self
            class Future:
                def result(self):pool.outstanding-=1;return source
            return Future()
    pool=Pool();assert list(r.bounded_results(pool,range(30)))==list(range(30))
    assert pool.maximum==4 and pool.outstanding==0


def test_empty_worker_inventory_completes_without_submission():
    r=runner()
    class Pool:
        def submit(self,*args):raise AssertionError('No source')
    assert list(r.bounded_results(Pool(),[]))==[]
