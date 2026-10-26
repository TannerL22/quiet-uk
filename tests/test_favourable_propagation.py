"""Curved physical paths, explicit-air units and sound-energy weather weighting."""
import math

import pytest
from scipy.integrate import quad

from quiet_uk.favourable_propagation import (
    EXACT_CENTRES,arc_length,circular_envelope,curved_geometry,direct_attenuation,
    atmospheric_absorption,favourable_boundary,favourable_ground,weather_weighted_attenuation,
)
from quiet_uk.propagation_canary import BANDS

AIR=dict(temperature_c=10,relative_humidity_percent=70,pressure_kpa=101.325,uniform_air=True)


def scene(**kw):
    config=dict(points=[(0,0),(1000,0)],source=(0,1),receiver=(1000,4),source_g=0.,ground_segments=[(0,1000,.5)],
        obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    config.update(kw);return config


@pytest.mark.parametrize('chord,radius',[(0,1000),(.1,1000),(100,1000),(1000,8000),(1999,1000)])
def test_arc_length_matches_circular_line_integral(chord,radius):
    length=quad(lambda u:1/math.sqrt(1-(u/radius)**2),-chord/2,chord/2,epsabs=1e-10)[0]
    assert arc_length(chord,radius)==pytest.approx(length,rel=1e-10,abs=1e-10)


def test_curved_envelope_clears_a_straight_ray_obstruction():
    assert circular_envelope([(500,12)],(0,1),(1000,4),8000)==[[0,1],[1000,4]]
    geometry=curved_geometry((0,1),(1000,4),[(500,12)])
    assert geometry['delta_m']<0
    r=favourable_boundary(frequency_hz=1000,**scene(obstacles=[dict(vertices=[(500,12)])]))
    assert not r['direct_arc_blocked']
    assert r['branch']=='ground_only_clear'


def test_shortest_curved_path_removes_a_middle_chord_hull_corner():
    s,r=(0,1),(1000,4)
    assert circular_envelope([(200,40),(500,44),(800,40)],s,r,8000)[1:-1]==[[200,40],[800,40]]
    assert circular_envelope([(200,40),(500,60),(800,40)],s,r,8000)[1:-1]==[[200,40],[500,60],[800,40]]


@pytest.mark.parametrize('f',BANDS)
def test_original_tc11_favourable_boundary_and_below_plane_rule(f):
    # Original pinned NoiseModelling DIRECT TC11 favourable expectation.
    expected=[9.61,12.2,15.,17.9,20.86,22.8,22.8,22.8]
    config=scene(points=[(0,0),(20,0)],source=(0,1),receiver=(20,15),source_g=.5,ground_segments=[(0,20,.5)],
        obstacles=[dict(vertices=[(5,10),(15,10)],roof_g=0.)])
    r=favourable_boundary(frequency_hz=f,**config)
    assert r['boundary_db']==pytest.approx(expected[BANDS.index(f)],abs=.02)
    assert r['receiver_frame']['source']['equivalent_height_m']==0
    assert r['receiver_frame']['source']['signed_height_m']<0
    assert r['receiver_ground_result']['g_w']==pytest.approx(1/6)


@pytest.mark.parametrize('f',BANDS)
def test_vertical_datum_translation_does_not_change_curved_or_ground_attenuation(f):
    a=scene(points=[(0,0),(100,0)],receiver=(100,4),ground_segments=[(0,100,.5)],
            obstacles=[dict(vertices=[(30,10),(70,10)],roof_g=0.)])
    b=dict(a,points=[(0,100),(100,100)],source=(0,101),receiver=(100,104),
           obstacles=[dict(vertices=[(30,110),(70,110)],roof_g=0.)])
    ra,rb=[favourable_boundary(frequency_hz=f,**c) for c in (a,b)]
    assert ra['boundary_db']==pytest.approx(rb['boundary_db'],abs=1e-11)
    assert ra['main_geometry']['delta_m']==pytest.approx(rb['main_geometry']['delta_m'],abs=1e-11)


def test_image_path_retains_fixed_edges_and_positive_returning_leg():
    geometry=curved_geometry((0,1),(8,2),[(11,10)])
    assert geometry['single_edge_outside_endpoint_span']
    assert geometry['delta_m']>0
    assert geometry['path'][1:-1]==[[11,10]]


def test_source_and_receiver_ground_factors_are_distinct():
    source=favourable_ground(500,40,1,2,.7,0.)
    receiver=favourable_ground(500,40,1,2,.7,0.,receiver_side=True)
    assert source['g_w']==receiver['g_w']==.7
    assert source['g_m']==pytest.approx(.7*40/90)
    assert receiver['g_m']==.7


def test_long_hard_ground_limit_can_exceed_three_db_gain():
    r=favourable_ground(1000,1000,1,4,0.,0.)
    assert r['attenuation_db']==pytest.approx(-8.1)
    assert favourable_ground(1000,1000,0,0,0.,0.)['attenuation_db']==-9
    assert favourable_ground(1000,1000,0,0,.5,0.)['attenuation_db'] is None


@pytest.mark.parametrize('f',BANDS)
def test_original_atmospheric_reference_octaves(f):
    # Original pinned public expectation at 10 C, 70% RH, standard pressure.
    expected=[.12,.41,1.04,1.93,3.66,9.66,32.77,116.88]
    r=atmospheric_absorption(EXACT_CENTRES[f],**AIR)
    assert r['alpha_db_per_km']==pytest.approx(expected[BANDS.index(f)],abs=.02)
    assert r['status']=='conditional_research'
    assert not r['regulatory_implementation_qualified']


def test_relative_humidity_is_percent_not_fraction():
    r=atmospheric_absorption(1000,temperature_c=20,relative_humidity_percent=50,pressure_kpa=101.325,uniform_air=True)
    assert 1<r['water_vapour_molar_percent']<1.3
    assert atmospheric_absorption(1000,temperature_c=20,relative_humidity_percent=.5,pressure_kpa=101.325,uniform_air=True)['status']=='withheld'


def test_cold_dry_air_has_the_wider_standard_accuracy_estimate():
    r=atmospheric_absorption(1000,temperature_c=-20,relative_humidity_percent=10,pressure_kpa=101.325,uniform_air=True)
    assert r['water_vapour_molar_percent']<.05
    assert r['standard_estimated_accuracy_percent']==20


@pytest.mark.parametrize('key',list(AIR))
def test_unknown_air_input_withholds_absorption_and_combined_attenuation(key):
    air=dict(AIR);air[key]=None
    assert atmospheric_absorption(1000,**air)['alpha_db_per_km'] is None
    r=direct_attenuation(scene(),1000,air,probability_favourable=.5,absorption_frequency_policy='exact_base10')
    assert r['status']=='withheld' and r['attenuation_db'] is None
    assert r['annual_noise_prediction'] is None


@pytest.mark.parametrize('change,reason',[
    (dict(uniform_air=False),'inhomogeneous_absorption_air'),(dict(temperature_c=51),'outside_bounded_absorption_domain'),
    (dict(relative_humidity_percent=9),'outside_bounded_absorption_domain'),(dict(pressure_kpa=121),'outside_bounded_absorption_domain')])
def test_unsupported_air_withholds(change,reason):
    r=atmospheric_absorption(1000,**dict(AIR,**change))
    assert r['status']=='withheld' and r['reason']==reason


@pytest.mark.parametrize('bad',[True,'70',float('nan'),float('inf')])
def test_malformed_air_raises(bad):
    with pytest.raises(ValueError): atmospheric_absorption(1000,**dict(AIR,relative_humidity_percent=bad))


def test_frequency_policy_is_required_and_recorded():
    assert direct_attenuation(scene(),8000,AIR,probability_favourable=.5)['status']=='withheld'
    exact=direct_attenuation(scene(),8000,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
    nominal=direct_attenuation(scene(),8000,AIR,probability_favourable=.5,absorption_frequency_policy='nominal')
    assert exact['absorption']['frequency_hz']==pytest.approx(7943.282347242816)
    assert nominal['absorption']['frequency_hz']==8000
    assert nominal['atmospheric_attenuation_db']>exact['atmospheric_attenuation_db']


def test_absorption_uses_direct_distance_instead_of_the_horizontal_roof_detour():
    config=scene(points=[(0,0),(20,0)],source=(0,1),receiver=(20,4),ground_segments=[(0,20,.5)],
                 obstacles=[dict(vertices=[(5,10),(15,10)],roof_g=0.)])
    r=direct_attenuation(config,1000,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
    assert r['direct_distance_m']==pytest.approx(math.hypot(20,3))
    assert r['favourable']['main_geometry']['path_distance_m']>r['direct_distance_m']
    assert r['atmospheric_attenuation_db']==pytest.approx(r['absorption']['alpha_db_per_km']*math.hypot(20,3)/1000)
    assert r['favourable_attenuation_db']==pytest.approx(r['divergence_db']+r['atmospheric_attenuation_db']+r['favourable']['boundary_db'])


def test_missing_meteorology_does_not_invent_a_mixed_or_annual_level():
    r=direct_attenuation(scene(),1000,AIR,absorption_frequency_policy='exact_base10')
    assert r['status']=='conditional_research'
    assert r['attenuation_db'] is None
    assert r['homogeneous_attenuation_db'] is not None and r['favourable_attenuation_db'] is not None
    assert r['meteorological_mix_status']=='withheld_unknown_probability'
    assert r['annual_noise_prediction'] is None


@pytest.mark.parametrize('p',[0.,.25,.5,.75,1.])
def test_weather_average_is_in_sound_energy(p):
    expected=-10*math.log10((1-p)*10**(-20/10)+p*10**(-10/10))
    assert weather_weighted_attenuation(20,10,p)==pytest.approx(expected,abs=1e-12)
    if p==.5: assert abs(expected-15)>2


def test_weather_average_is_stable_for_extreme_attenuation():
    assert weather_weighted_attenuation(1000000,-1000000,.5)==pytest.approx(-1000000+10*math.log10(2))
    assert weather_weighted_attenuation(1,2,None) is None
    with pytest.raises(ValueError): weather_weighted_attenuation(1,2,1.1)


@pytest.mark.parametrize('change,reason',[
    (dict(scene_kind='unqualified'),'real_area_scene_not_qualified'),(dict(obstacles=None),'unknown_obstacle_geometry_or_roof_ground'),
    (dict(source_g=None),'unknown_acoustic_ground'),(dict(opaque=None),'unknown_obstacle_geometry_or_properties'),
    (dict(infinite_lateral_extent=False),'unsupported_transmission_or_finite_lateral_extent'),
    (dict(obstacles=[dict(vertices=[(500,20000)])]),'unsupported_circular_envelope'),
    (dict(conditions='upward_refraction'),'unsupported_propagation_conditions')])
def test_incomplete_or_unsupported_scene_remains_withheld(change,reason):
    r=favourable_boundary(frequency_hz=1000,**scene(**change))
    assert r['status']=='withheld' and r['reason']==reason
    assert r['boundary_db'] is None and r['annual_noise_prediction'] is None
