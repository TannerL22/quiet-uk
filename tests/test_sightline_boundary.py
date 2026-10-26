"""Clear-ray selection, declared roof support and fixed physical image chains."""
import math

import pytest

from quiet_uk.propagation_canary import BANDS
from quiet_uk.sightline_boundary import selected_chain_geometry,sightline_boundary
from quiet_uk.multiple_edge_boundary import multiple_edge_boundary
from quiet_uk.terrain_boundary import single_edge_boundary


def calculate(f=1000,**kwargs):
    config = dict(points=[(0,0),(100,0)],source=(0,1),receiver=(100,4),frequency_hz=f,
        ground_segments=[(0,100,.5)],source_g=.5,scene_kind='synthetic',opaque=True,infinite_lateral_extent=True,
        obstacles=[dict(vertices=[(30,10),(70,10)],roof_g=0.)])
    config.update(kwargs);return sightline_boundary(**config)


@pytest.mark.parametrize('f',BANDS)
def test_existing_blocked_chain_matches_completed_calculation(f):
    old = multiple_edge_boundary([(0,0),(100,0)],(0,1),(100,4),f,[(0,100,.5)],.5,
        obstacles=[dict(vertices=[(30,10),(70,10)])],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    new = calculate(f)
    assert new['boundary_db']==pytest.approx(old['boundary_db'],abs=1e-12)


@pytest.mark.parametrize('f',BANDS)
def test_original_tc11_tall_receiver_boundary(f):
    # Original public DIRECT TC11 expectations (pinned NoiseModelling source).
    values = [9.61,12.20,15.00,17.90,20.86,22.80,22.80,22.80]
    r = calculate(f,points=[(0,0),(20,0)],source=(0,1),receiver=(20,15),ground_segments=[(0,20,.5)],
        obstacles=[dict(vertices=[(5,10),(15,10)],roof_g=0.)])
    assert r['boundary_db']==pytest.approx(values[BANDS.index(f)],abs=.02)
    assert r['controlling_edges']==[[5,10]]
    assert r['receiver_side_g']==pytest.approx(1/6)
    assert r['receiver_plane']['slope']==pytest.approx(-8/9)
    # Primary Annex null-height rule, rather than the upstream table's absolute
    # height at O. The ground-floor branch dominates those published bands.
    assert r['receiver_frame']['source']['signed_height_m']<0
    assert r['receiver_frame']['source']['equivalent_height_m']==0


@pytest.mark.parametrize('f',BANDS)
def test_clear_single_screen_matches_completed_selector(f):
    old = single_edge_boundary([(0,0),(100,0)],(0,1),(100,4),f,[(0,100,.5)],.5,
        edge=(50,1),edge_width_m=0,opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    new = calculate(f,obstacles=[dict(vertices=[(50,1)])])
    assert new['boundary_db']==pytest.approx(old['boundary_db'],abs=1e-12)
    assert new['selection']['use_diffraction']==old['selection']['use_diffraction']


@pytest.mark.parametrize('f',BANDS)
def test_image_hull_changes_do_not_replace_physical_chain(f):
    r = calculate(f,obstacles=[dict(vertices=[(10,5)]),dict(vertices=[(20,10)]),dict(vertices=[(70,10)])])
    assert r['status']=='conditional_research'
    assert r['controlling_edges']==[[20,10],[70,10]]
    assert r['source_virtual_hull_changes_edges']
    assert r['source_virtual_hull_edges']==[[10,5],[20,10],[70,10]]
    expected_delta = math.hypot(20,11)+50+math.hypot(30,6)-math.hypot(100,5)
    path = r['source_image_geometry']
    assert path['path'][1:-1]==r['controlling_edges']
    assert path['delta_m']==pytest.approx(expected_delta,abs=1e-12)
    assert path['inter_edge_path_m']==50


@pytest.mark.parametrize('f',BANDS)
def test_clear_roof_sampling_does_not_create_new_edges(f):
    base = calculate(f,obstacles=[dict(vertices=[(30,1.6),(70,1.6)],roof_g=0.)])
    sampled = calculate(f,obstacles=[dict(vertices=[(30,1.6),(50,1.6),(70,1.6)],roof_g=0.)])
    assert base['physical_edge_candidates']==sampled['physical_edge_candidates']==[[30,1.6],[70,1.6]]
    assert base['selection']==sampled['selection']
    assert base['boundary_db']==pytest.approx(sampled['boundary_db'],abs=1e-12)


@pytest.mark.parametrize('f',BANDS)
def test_clear_planar_and_valley_sampling_invariance(f):
    base = calculate(f,obstacles=[])
    sampled = calculate(f,points=[(0,0),(20,0),(50,0),(100,0)],obstacles=[])
    assert base['branch']==sampled['branch']=='ground_only_no_edges'
    assert base['boundary_db']==pytest.approx(sampled['boundary_db'],abs=1e-12)
    valley = calculate(f,points=[(0,0),(50,-10),(100,0)],obstacles=[])
    sampled_valley = calculate(f,points=[(0,0),(25,-5),(50,-10),(75,-5),(100,0)],obstacles=[])
    assert valley['branch']==sampled_valley['branch']=='ground_only_no_edges'
    assert valley['boundary_db']==pytest.approx(sampled_valley['boundary_db'],abs=1e-11)


def test_tangent_screen_uses_strict_rayleigh_selection():
    a = calculate(1000,source=(0,1),receiver=(100,1),obstacles=[dict(vertices=[(50,1)])])
    b = calculate(2000,source=(0,1),receiver=(100,1),obstacles=[dict(vertices=[(50,1)])])
    assert not a['direct_ray_blocked'] and a['main_geometry']['delta_m']==0
    assert a['branch']=='ground_only_clear' and not a['selection']['rayleigh_condition']
    assert b['branch']=='diffraction_including_ground' and b['selection']['rayleigh_condition']
    assert b['rayleigh_mirror_edge']==[50,1]


def test_clear_selection_uses_largest_signed_excess():
    r = calculate(obstacles=[dict(vertices=[(20,1)]),dict(vertices=[(50,2.4)]),dict(vertices=[(80,1)])])
    assert not r['direct_ray_blocked']
    assert r['controlling_edges']==[[50,2.4]]
    assert r['main_geometry']['delta_m']<0
    assert r['main_geometry']['inter_edge_path_m']==0


def test_roof_ground_and_height_replace_supported_surface_only():
    r = calculate(source=(0,20),receiver=(100,20))
    assert r['branch']=='ground_only_clear'
    assert r['g_path']==pytest.approx(.3)
    assert r['common_plane']['height_at_origin_m']==pytest.approx(4)
    assert r['surface_segments']==[(0,30,0,0),(30,70,10,10),(70,100,0,0)]
    assert r['surface_ground_segments']==[(0,30,.5),(30,70,0),(70,100,.5)]
    assert r['annual_noise_prediction'] is None and not r['regulatory_implementation_qualified']


def test_ground_factor_splits_are_not_edges():
    a = calculate(obstacles=[])
    b = calculate(obstacles=[],ground_segments=[(0,25,.5),(25,50,.5),(50,100,.5)])
    assert a['physical_edge_candidates']==b['physical_edge_candidates']==[]
    assert a['boundary_db']==pytest.approx(b['boundary_db'],abs=1e-12)


def test_virtual_endpoint_before_fixed_edge_retains_returning_leg():
    path = selected_chain_geometry((0,1),(4,-1),[(5,10)])
    assert path['single_edge_outside_endpoint_span']
    assert path['delta_m']==pytest.approx(math.hypot(5,9)+math.hypot(1,11)-math.hypot(4,2))
    r = calculate(points=[(0,0),(20,0)],source=(0,1),receiver=(20,23),ground_segments=[(0,20,.2)],source_g=.2,
        obstacles=[dict(vertices=[(5,10),(11,10)],roof_g=0.)])
    assert r['receiver_mirror'][0]<r['controlling_edges'][0][0]
    assert r['receiver_image_geometry']['single_edge_outside_endpoint_span']
    assert r['receiver_image_diffraction_db']>r['main_diffraction_uncapped_db']


def test_vertical_datum_translation():
    a = calculate(obstacles=[dict(vertices=[(30,1.6),(70,1.6)],roof_g=0.)])
    b = calculate(points=[(0,100),(100,100)],source=(0,101),receiver=(100,104),
        obstacles=[dict(vertices=[(30,101.6),(70,101.6)],roof_g=0.)])
    assert a['selection']['use_diffraction']==b['selection']['use_diffraction']
    assert a['boundary_db']==pytest.approx(b['boundary_db'],abs=1e-10)


@pytest.mark.parametrize('change,reason',[
    (dict(obstacles=None),'unknown_obstacle_geometry_or_roof_ground'),
    (dict(obstacles=[dict(vertices=[(30,10),(70,10)])]),'unknown_obstacle_geometry_or_roof_ground'),
    (dict(obstacles=[dict(vertices=[(30,10),(70,None)],roof_g=0.)]),'unknown_obstacle_geometry_or_roof_ground'),
    (dict(opaque=None),'unknown_obstacle_geometry_or_properties'),
    (dict(infinite_lateral_extent=None),'unknown_obstacle_geometry_or_properties'),
    (dict(opaque=False),'unsupported_transmission_or_finite_lateral_extent'),
    (dict(infinite_lateral_extent=False),'unsupported_transmission_or_finite_lateral_extent'),
    (dict(source_g=None),'unknown_acoustic_ground'),
    (dict(ground_segments=[(0,100,None)]),'unknown_acoustic_ground'),
    (dict(source=None),'unknown_endpoint_elevation'),
    (dict(points=[(0,0),(50,None),(100,0)]),'unknown_terrain_support'),
    (dict(scene_kind='real_area'),'real_area_scene_not_qualified'),
    (dict(conditions='favourable'),'unsupported_propagation_conditions'),
    (dict(frequency_hz=123),'unsupported_nominal_frequency'),
])
def test_unknown_or_unsupported_stays_withheld(change,reason):
    r = calculate(**change)
    assert r['status']=='withheld' and r['reason']==reason
    assert r['boundary_db'] is None and r['annual_noise_prediction'] is None


@pytest.mark.parametrize('obstacles',[
    [dict(vertices=[])],[dict(vertices=[(70,10),(30,10)],roof_g=0.)],
    [dict(vertices=[(30,10),(30,20)],roof_g=0.)],[dict(vertices=[(0,10)])],
    [dict(vertices=[(100,10)])],[dict(vertices=[(30,-1)])],
    [dict(vertices=[(20,10),(60,10)],roof_g=0.),dict(vertices=[(50,10),(70,10)],roof_g=0.)],
    [dict(vertices=[(30,10,1)])],[dict(vertices=[(30,10),(70,10)],roof_g=1.1)],['bad'],
])
def test_invalid_geometry_or_ground_rejected(obstacles):
    with pytest.raises(ValueError): calculate(obstacles=obstacles)


def test_roof_does_not_erase_a_higher_terrain_break():
    with pytest.raises(ValueError): calculate(points=[(0,0),(50,20),(100,0)])
