"""Conservation, analytic quadrature, path completeness and emission units."""
import copy
import json
import math
from pathlib import Path
import pytest
from scipy.integrate import quad
from quiet_uk.road_line_integration import integrate_road_line,point_path_transfer
from quiet_uk.road_emission import source_spectrum,BASE_CASE
from quiet_uk.road_input_audit import VEHICLE_FIELDS

def segment(**changes):
    result=dict(segment_id='road',start=[-50.,0.,.05],end=[50.,0.,.05],length_basis='horizontal',relative_power_per_m=[1000.]*8,breakpoints=[.5])
    result.update(changes);return result

def transfer(s,r,f):
    a=20*math.log10(math.dist(s,r))+11
    return dict(homogeneous=dict(status='conditional_research',attenuation_db=a),
        favourable=dict(status='conditional_research',attenuation_db=a-10*math.log10(2)),probability_favourable=.3)

def calculate(segments=None,receiver=None,provider=transfer,**changes):
    options=dict(scene_kind='synthetic',metre_coordinates_confirmed=True,path_support_complete=True,tolerance_db=.001)
    options.update(changes)
    return integrate_road_line([segment()] if segments is None else segments,[0.,30.,4.] if receiver is None else receiver,1000,provider,**options)

def test_receiver_power_matches_exact_finite_line_integral_and_independent_quad():
    result=calculate();a=math.hypot(30.,3.95)
    exact=1000*10**(-1.1)*2*math.atan(50/a)/a
    numerical=quad(lambda x:1000*10**(-1.1)/(x*x+a*a),-50,50,epsabs=1e-10)[0]
    assert numerical==pytest.approx(exact,rel=1e-12)
    assert result['branches']['homogeneous']['relative_energy']==pytest.approx(exact,rel=1e-5)
    assert result['branches']['favourable']['relative_energy']==pytest.approx(2*exact,rel=1e-5)
    assert result['branches']['mixed']['relative_energy']==pytest.approx(1.3*exact,rel=1e-5)
    assert result['source_relative_power']==100000.
    assert result['converged'] and result['annual_noise_prediction'] is None

def test_splitting_source_preserves_power_and_receiver_result():
    a=segment(segment_id='west',end=[0.,0.,.05],breakpoints=[])
    b=segment(segment_id='east',start=[0.,0.,.05],breakpoints=[])
    result=calculate([a,b]);original=calculate()
    assert result['source_relative_power']==original['source_relative_power']
    assert result['receiver_level_db']==pytest.approx(original['receiver_level_db'],abs=1e-10)

def test_three_dimensional_rotation_and_translation_preserve_explicit_3d_length_integral():
    original=segment(length_basis='3d');receiver=[0.,30.,4.]
    def transform(p):return [1000+p[2],2000+p[0],-100+p[1]]
    moved=copy.deepcopy(original);moved.update(start=transform(original['start']),end=transform(original['end']))
    assert calculate([moved],transform(receiver))['receiver_level_db']==pytest.approx(calculate([original],receiver)['receiver_level_db'],abs=1e-10)

def test_directional_weather_is_mixed_at_each_source_point():
    def directional(s,r,f):
        v=transfer(s,r,f);v['probability_favourable']=.1 if s[0]<0 else .9;return v
    result=calculate(provider=directional)
    assert result['branches']['mixed']['relative_energy']==pytest.approx(1.5*result['branches']['homogeneous']['relative_energy'],rel=1e-12)

@pytest.mark.parametrize('change,reason',[
    ({'relative_power_per_m':[None]*8},'unknown_source_power_band'),
    ({'start':[None,0.,0.]},'unknown_source_geometry'),
    ({'breakpoints':None},'unknown_path_or_emission_breakpoints'),
    ({'length_basis':None},'unknown_source_length_basis')])
def test_missing_positive_source_input_is_held(change,reason):
    assert calculate([segment(**change)])['reason']==reason

@pytest.mark.parametrize('case',[dict(scene_kind='unqualified'),dict(metre_coordinates_confirmed=None),dict(path_support_complete=None)])
def test_unqualified_support_never_creates_a_level(case):
    result=calculate(**case);assert result['status']=='withheld' and result['receiver_level_db'] is None

def test_unknown_positive_transfer_is_not_dropped():
    def incomplete(s,r,f):
        result=transfer(s,r,f)
        if s[0]<0:
            for state in ('homogeneous','favourable'):result[state]=dict(status='withheld',attenuation_db=None)
        return result
    assert calculate(provider=incomplete)['reason']=='incomplete_positive_transfer_contribution'

def test_zero_probability_ignores_unknown_unused_condition_but_unknown_weather_is_held():
    def incomplete(s,r,f):
        result=transfer(s,r,f);result['favourable']=dict(status='withheld',attenuation_db=None);result['probability_favourable']=0.;return result
    assert calculate(provider=incomplete)['status']=='conditional_research'
    def weather_unknown(s,r,f):
        result=transfer(s,r,f);result['probability_favourable']=None;return result
    result=calculate(provider=weather_unknown)
    assert result['status']=='withheld' and result['branches']['homogeneous']['receiver_level_db'] is not None

def test_zero_emission_requires_no_propagation_and_absent_paths_are_known_zero():
    def must_not_run(*args):raise AssertionError('Zero source must not request propagation')
    result=calculate([segment(relative_power_per_m=[0.]*8)],provider=must_not_run)
    assert result['status']=='absent' and result['evaluations']==0
    def absent(*args):return dict(homogeneous=dict(status='absent'),favourable=dict(status='absent'),probability_favourable=.3)
    assert calculate(provider=absent)['status']=='absent'

@pytest.mark.parametrize('other',[
    segment(),segment(segment_id='duplicate-geometry'),segment(segment_id='overlap',start=[-25.,0.,.05],end=[75.,0.,.05]),
    segment(segment_id='reverse',start=[50.,0.,.05],end=[-50.,0.,.05])])
def test_duplicate_or_overlapping_carriageway_sources_are_rejected(other):
    with pytest.raises(ValueError):calculate([segment(),other])

def test_distinct_parallel_carriageways_are_summed_once():
    other=segment(segment_id='other-lane',start=[-50.,1.,.05],end=[50.,1.,.05])
    result=calculate([segment(),other]);parts=[calculate([s]) for s in [segment(),other]]
    assert result['source_relative_power']==200000
    assert result['branches']['mixed']['relative_energy']==pytest.approx(sum(p['branches']['mixed']['relative_energy'] for p in parts),rel=1e-5)

def test_singular_receiver_and_resource_exhaustion_are_held():
    assert calculate(receiver=[0.,0.,.05])['reason']=='receiver_on_source_line'
    assert calculate(max_evaluations=5)['reason']=='quadrature_budget_exceeded'
    assert calculate(max_refinements=1)['reason']=='quadrature_not_converged'

def test_road_emission_per_metre_connects_to_receiver_without_double_length_weighting():
    coefficients=json.loads((Path(__file__).parent/'fixtures/road_emission_2021_coefficients.json').read_text())
    annual={field:2400. if field=='cars_and_taxis' else 0. for field in VEHICLE_FIELDS}
    profiles={field:dict(period_fractions=dict(day=.5,evening=1/6,night=1/3)) for field in VEHICLE_FIELDS}
    emission=source_spectrum(annual,profiles,BASE_CASE,coefficients)
    powers=emission['period_spectra']['day']['relative_power_per_m']
    road=segment(relative_power_per_m=powers);result=calculate([road])
    unit=calculate([segment(relative_power_per_m=[1.]*8)])
    assert result['source_relative_power']==pytest.approx(100*powers[4])
    assert result['branches']['mixed']['relative_energy']==pytest.approx(unit['branches']['mixed']['relative_energy']*powers[4],rel=1e-12)
    assert emission['receiver_pressure_db'] is None and result['annual_noise_prediction'] is None

def test_path_adapter_requires_complete_reflection_support_and_matches_endpoint_geometry():
    s,r=[0.,0.,.05],[0.,30.,4.]
    held=point_path_transfer(s,r,1000,direct_scene={},reflections=None,reflection_support_complete=None,air={})
    assert held['homogeneous']['status']=='withheld'
    scene=dict(source=[0.,.05],receiver=[31.,4.])
    with pytest.raises(ValueError,match='world endpoints'):point_path_transfer(s,r,1000,direct_scene=scene,reflections=[],reflection_support_complete=True,air={})

def adapter_inputs():
    return dict(source=[0.,0.,.05],receiver=[0.,30.,4.],frequency_hz=1000,
        direct_scene=dict(points=[[0.,0.],[30.,0.]],source=[0.,.05],receiver=[30.,4.],source_g=.5,
            ground_segments=[[0.,30.,.5]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic'),
        reflections=[],reflection_support_complete=True,probability_favourable=.3,absorption_frequency_policy='exact_base10',
        air=dict(temperature_c=10.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True))

def reflection_input():
    return dict(plan_input=dict(source_xy=[0.,0.],receiver_xy=[0.,30.],wall_start_xy=[-1000.,-20.],wall_end_xy=[1000.,-20.]),
        scene=dict(points=[[0.,0.],[70.,0.]],source=[0.,.05],receiver=[70.,4.],source_g=.5,
            ground_segments=[[0.,70.,.5]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic'),
        wall=dict(unfolded_x_m=20.,base_z_m=0.,top_z_m=8.,slope_from_vertical_deg=0.,absorption_by_band=[.2]*8,
            incident_view_height_m=dict(homogeneous=8.,favourable=8.),incident_view_width_m=dict(homogeneous=1000.,favourable=1000.),unfolded_geometry_confirmed=True))

def test_direct_and_reflected_paths_add_energy_once_before_weather_mix():
    options=adapter_inputs();direct=point_path_transfer(**options);options['reflections']=[reflection_input()]
    result=point_path_transfer(**options)
    for state in ('homogeneous','favourable'):assert result[state]['attenuation_db']<direct[state]['attenuation_db']
    h=10**(-result['homogeneous']['attenuation_db']/10);f=10**(-result['favourable']['attenuation_db']/10)
    assert 10**(-result['meteorological_mix']['attenuation_db']/10)==pytest.approx(.7*h+.3*f,rel=1e-12)

def test_duplicate_wall_and_mismatched_unfolding_are_rejected():
    options=adapter_inputs();options['reflections']=[reflection_input(),reflection_input()]
    with pytest.raises(ValueError,match='Duplicate'):point_path_transfer(**options)
    options['reflections']=[reflection_input()];options['reflections'][0]['scene']['receiver'][0]=100.
    with pytest.raises(ValueError,match='Unfolded'):point_path_transfer(**options)

@pytest.mark.parametrize('missing',['plan','wall','direct'])
def test_unknown_path_geometry_is_held_instead_of_dropping_a_path(missing):
    options=adapter_inputs();options['reflections']=[reflection_input()]
    if missing=='plan':options['reflections'][0]['plan_input']['source_xy']=None
    if missing=='wall':options['reflections'][0]['wall']['unfolded_x_m']=None
    if missing=='direct':options['direct_scene']['source'][1]=None
    result=point_path_transfer(**options)
    for state in ('homogeneous','favourable'):assert result[state]['status']=='withheld'

@pytest.mark.parametrize('value',[True,'1000',math.nan,math.inf,-1.])
def test_malformed_source_power_is_rejected(value):
    with pytest.raises(ValueError):calculate([segment(relative_power_per_m=[value]*8)])
