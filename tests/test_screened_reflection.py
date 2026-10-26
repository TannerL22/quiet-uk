"""Physical-leg reflections and safety of missing or ineligible paths."""
import copy
import math
import pytest
from quiet_uk.screened_reflection import screened_wall_attenuation,leg_ray_height,leg_retrodiffraction
from quiet_uk.reflection_path import single_wall_attenuation,retrodiffraction

AIR=dict(temperature_c=10.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)

def scene():
    return dict(points=[[0.,0.],[200.,0.]],source=[0.,1.],receiver=[200.,4.],source_g=.5,
        ground_segments=[[0.,200.,.5]],obstacles=[dict(vertices=[[80.,12.]])],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')

def wall(x=130.):
    return dict(unfolded_x_m=x,base_z_m=0.,top_z_m=20.,slope_from_vertical_deg=0.,absorption_by_band=[.2]*8,
        incident_view_height_m=dict(homogeneous=20.,favourable=20.),incident_view_width_m=dict(homogeneous=20.,favourable=20.),unfolded_geometry_confirmed=True)

def calculate(s=None,w=None,f=63):
    return screened_wall_attenuation(scene() if s is None else s,wall() if w is None else w,f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')

@pytest.mark.parametrize('x,leg',[(40.,[[0.,1.],[80.,12.]]),(130.,[[80.,12.],[200.,4.]])])
def test_reflection_uses_adjacent_screen_leg(x,leg):
    result=calculate(w=wall(x))
    for state in ('homogeneous','favourable'):
        assert result[state]['reflecting_leg']==leg
        assert result[state]['status']=='conditional_research'
    a,b=leg;assert result['homogeneous']['hit_height_m']==pytest.approx(a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]))

def test_favourable_retrodiffraction_retains_whole_path_radius_not_local_leg_radius():
    a,b=[85.16596373948585,12.],[198.04031414744156,12.]
    top=[129.74960684335466,15.];radius=8*math.dist([0.,1.],b)
    result=retrodiffraction(63,leg_retrodiffraction(a,b,top,radius))
    assert result==pytest.approx(2.7667909357140053,abs=1e-9)
    assert abs(result-retrodiffraction(63,leg_retrodiffraction(a,b,top,1000.)))>.4

def test_unobstructed_extension_agrees_with_frozen_clear_wall_kernel():
    s=scene();s['obstacles']=[]
    for f in (63,500,8000):
        new=calculate(s=s,f=f);old=single_wall_attenuation(s,wall(),f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
        for state in ('homogeneous','favourable'):assert new[state]['attenuation_db']==pytest.approx(old[state]['attenuation_db'],abs=1e-10)

def test_terrain_datum_translation_preserves_levels_and_moves_hit_height():
    s=scene();s.update(points=[[0.,0.],[80.,10.],[200.,0.]],obstacles=[]);w=wall()
    original=calculate(s,w);s=copy.deepcopy(s);w=copy.deepcopy(w)
    for p in [*s['points'],s['source'],s['receiver']]:p[1]-=100.
    w['base_z_m']-=100.;w['top_z_m']-=100.;moved=calculate(s,w)
    for state in ('homogeneous','favourable'):
        assert moved[state]['attenuation_db']==pytest.approx(original[state]['attenuation_db'],abs=1e-9)
        assert moved[state]['hit_height_m']==pytest.approx(original[state]['hit_height_m']-100,abs=1e-9)

def test_coincident_reflector_and_diffraction_edge_is_held():
    result=calculate(w=wall(80.))
    for state in ('homogeneous','favourable'):assert result[state]['reason']=='coincident_reflection_and_diffraction_or_unsupported_order'

@pytest.mark.parametrize('change,reason',[
    ({'top_z_m':2.},'ray_passes_outside_wall_height'),
    ({'absorption_by_band':[1.]*8},'perfectly_absorbing_wall_zero_energy_extension'),
    ({'incident_view_width_m':dict(homogeneous=.49,favourable=.49)},'incident_view_wall_dimension_below_minimum')])
def test_known_absence_has_zero_energy(change,reason):
    w=wall();w.update(change);r=calculate(w=w)
    for state in ('homogeneous','favourable'):
        assert r[state]['status']=='absent' and r[state]['energy_ratio']==0
        assert r[state]['reason']==reason

@pytest.mark.parametrize('change,reason',[
    ({'ground_segments':[[0.,200.,None]]},'incomplete_unfolded_boundary_inputs'),
    ({'scene_kind':'unqualified'},'real_area_scene_not_qualified'),
    ({'obstacles':[dict(vertices=[[80.,12.],[140.,12.]],roof_g=None)]},'incomplete_unfolded_boundary_inputs')])
def test_unknown_real_inputs_never_become_a_reflection_level(change,reason):
    s=scene();s.update(change);r=calculate(s=s)
    for state in ('homogeneous','favourable'):assert r[state]['reason']==reason and r[state]['attenuation_db'] is None
    assert r['annual_noise_prediction'] is None

def test_ray_height_and_negative_excess_are_invariant_when_endpoints_reversed_in_height():
    a,b=[0.,12.],[200.,1.];x=50.
    assert leg_ray_height(a,b,x,1600)>leg_ray_height(a,b,x)
    assert leg_retrodiffraction(a,b,[x,20.],1600)<0

@pytest.mark.parametrize('invalid',[True,'63',math.nan,math.inf])
def test_invalid_frequency_is_rejected(invalid):
    with pytest.raises(ValueError):calculate(f=invalid)
