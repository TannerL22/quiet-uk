"""Bounded multiple-edge equation, geometry and missing-input contract checks."""
import math

import pytest

from quiet_uk.multiple_edge_boundary import (
    MAX_OBSTACLES, chain_geometry, multiple_coefficient, multiple_edge_boundary,
)
from quiet_uk.propagation_canary import BANDS
from quiet_uk.terrain_boundary import single_edge_boundary


def calculate(f=1000, **kwargs):
    config = dict(points=[(0, 0), (100, 0)], source=(0, 1), receiver=(100, 4), frequency_hz=f,
        ground_segments=[(0, 100, .5)], source_g=.5, scene_kind='synthetic', opaque=True,
        infinite_lateral_extent=True, obstacles=[dict(vertices=[(30, 10), (70, 10)])])
    config.update(kwargs)
    return multiple_edge_boundary(**config)


@pytest.mark.parametrize('f', BANDS)
def test_published_cubic_building_boundary(f):
    # Original NoiseModelling TC10 DIRECT (commit 1a015895...), GPLv3 fixtures
    # retained in artifacts; these rounded values are independent expectations.
    result = calculate(f, points=[(0, 0), (20, 0)], source=(0, 1), receiver=(20, 4),
                       ground_segments=[(0, 20, .5)], obstacles=[dict(vertices=[(5, 10), (15, 10)])])
    boundary = [15.69, 19.36, 22.48, 22.48, 22.48, 22.48, 22.48, 22.48]
    pure = [18.23, 21.88, 26.33, 30.63, 34.21, 37.39, 40.45, 43.47]
    assert result['boundary_db'] == pytest.approx(boundary[BANDS.index(f)], abs=.02)
    assert result['main_diffraction_uncapped_db'] == pytest.approx(pure[BANDS.index(f)], abs=.02)
    assert result['controlling_edge_count'] == 2
    assert result['main_geometry']['inter_edge_path_m'] == 10
    assert result['annual_noise_prediction'] is None and not result['regulatory_implementation_qualified']


@pytest.mark.parametrize('f', BANDS)
def test_two_screens_and_roof_have_same_direct_term(f):
    roof = calculate(f)
    screens = calculate(f, obstacles=[dict(vertices=[(30, 10)]), dict(vertices=[(70, 10)])])
    assert roof['boundary_db'] == screens['boundary_db']
    assert roof['obstacle_widths_m'] == [40] and screens['obstacle_widths_m'] == [0, 0]


@pytest.mark.parametrize('f', BANDS)
def test_redundant_and_shadowed_vertices_do_not_add_diffractions(f):
    base = calculate(f)
    sampled = calculate(f, obstacles=[dict(vertices=[(30, 10), (50, 10), (70, 10)])])
    shadowed = calculate(f, obstacles=[dict(vertices=[(30, 10)]), dict(vertices=[(50, 2)]), dict(vertices=[(70, 10)])])
    assert sampled['boundary_db'] == shadowed['boundary_db'] == base['boundary_db']
    assert sampled['controlling_edge_count'] == shadowed['controlling_edge_count'] == 2


@pytest.mark.parametrize('f', BANDS)
def test_single_screen_matches_completed_module(f):
    old = single_edge_boundary([(0, 0), (100, 0)], (0, 1), (100, 4), f, [(0, 100, .5)], .5,
        edge=(50, 10), scene_kind='synthetic', edge_width_m=0, opaque=True, infinite_lateral_extent=True)
    new = calculate(f, obstacles=[dict(vertices=[(50, 10)])])
    assert new['boundary_db'] == pytest.approx(old['boundary_db'], abs=1e-12)


@pytest.mark.parametrize('f', BANDS)
def test_only_main_term_capped(f):
    r = calculate(f, obstacles=[dict(vertices=[(30, 200), (70, 200)])])
    assert r['main_diffraction_db'] == 25
    assert r['main_diffraction_uncapped_db'] > 25
    assert r['source_image_diffraction_db'] > 25 and r['receiver_image_diffraction_db'] > 25
    assert r['boundary_db'] == 25+r['source_ground_coupling_db']+r['receiver_ground_coupling_db']
    assert r['separate_ground_db'] == 0


@pytest.mark.parametrize('e', [0, .1, .3])
def test_coefficient_threshold(e):
    assert multiple_coefficient(1000, e) == 1


def test_coefficient_equation_and_limits():
    assert multiple_coefficient(1000, 1.7) == pytest.approx(1.5)
    assert 1 < multiple_coefficient(1000, .30001) < multiple_coefficient(1000, 30) < 3
    with pytest.raises(ValueError): multiple_coefficient(1000, -1)


def test_inter_edge_length_uses_sloping_path():
    r = chain_geometry((0, 1), (100, 1), [(20, 10), (50, 20), (80, 10)])
    assert r['inter_edge_path_m'] == pytest.approx(2*math.hypot(30, 10))
    assert r['inter_edge_path_m'] > 60
    assert r['delta_m'] == pytest.approx(2*math.hypot(20, 9)+2*math.hypot(30, 10)-100)


def test_vertical_datum_and_chainage_translation():
    base = calculate()
    shifted = calculate(points=[(500, 100), (600, 100)], source=(500, 101), receiver=(600, 104),
        ground_segments=[(500, 600, .5)], obstacles=[dict(vertices=[(530, 110), (570, 110)])])
    assert shifted['boundary_db'] == pytest.approx(base['boundary_db'], abs=1e-11)


def test_opaque_roof_does_not_replace_outer_ground_planes():
    r = calculate(obstacles=[dict(vertices=[(30, 50), (70, 50)])])
    assert r['source_plane']['slope'] == r['receiver_plane']['slope'] == 0
    assert r['source_plane']['profile_length_m'] == r['receiver_plane']['profile_length_m'] == 30
    assert r['source_frame']['receiver']['equivalent_height_m'] == 50


def test_different_ground_on_each_outer_side():
    r = calculate(ground_segments=[(0, 30, .9), (30, 70, 0), (70, 100, .2)], source_g=.9)
    assert r['source_side_g'] == .9 and r['receiver_side_g'] == .2
    assert r['source_side_corrected_g'] == pytest.approx(.9)
    assert r['receiver_ground_db'] >= -3*(1-.2)


@pytest.mark.parametrize('change,reason', [
    (dict(obstacles=None), 'unknown_obstacle_geometry_or_properties'),
    (dict(obstacles=[dict(vertices=None)]), 'unknown_obstacle_geometry_or_properties'),
    (dict(obstacles=[dict(vertices=[(30, 10), (70, None)])]), 'unknown_obstacle_geometry_or_properties'),
    (dict(opaque=None), 'unknown_obstacle_geometry_or_properties'),
    (dict(infinite_lateral_extent=None), 'unknown_obstacle_geometry_or_properties'),
    (dict(opaque=False), 'unsupported_transmission_or_finite_lateral_extent'),
    (dict(infinite_lateral_extent=False), 'unsupported_transmission_or_finite_lateral_extent'),
    (dict(source_g=None), 'unknown_acoustic_ground'),
    (dict(ground_segments=[(0, 100, None)]), 'unknown_acoustic_ground'),
    (dict(receiver=None), 'unknown_endpoint_elevation'),
    (dict(points=[(0, 0), (50, None), (100, 0)]), 'unknown_terrain_support'),
    (dict(scene_kind='real_area'), 'real_area_scene_not_qualified'),
    (dict(conditions='favourable'), 'unsupported_propagation_conditions'),
    (dict(frequency_hz=123), 'unsupported_nominal_frequency'),
    (dict(source=(0, 20), receiver=(100, 20)), 'clear_path_multiple_geometry_requires_selection'),
    (dict(obstacles=[dict(vertices=[(10, 5)]), dict(vertices=[(20, 10)]), dict(vertices=[(70, 10)])]), 'mirror_changes_controlling_chain'),
])
def test_unsupported_or_unknown_withheld(change, reason):
    r = calculate(**change)
    assert r['status'] == 'withheld' and r['reason'] == reason
    assert r['boundary_db'] is None and r['annual_noise_prediction'] is None


@pytest.mark.parametrize('obstacles', [
    [dict(vertices=[])], [dict(vertices=[(70, 10), (30, 10)])],
    [dict(vertices=[(30, 10), (30, 20)])], [dict(vertices=[(0, 10)])],
    [dict(vertices=[(100, 10)])], [dict(vertices=[(30, -1)])],
    [dict(vertices=[(30, -1), (70, 10)])],
    [dict(vertices=[(20, 10), (60, 10)]), dict(vertices=[(50, 10), (70, 10)])],
    [dict(vertices=[(70, 10)]), dict(vertices=[(30, 10)])],
    [dict(vertices=[(30, 10)])]*(MAX_OBSTACLES+1),
])
def test_malformed_geometry_rejected(obstacles):
    with pytest.raises(ValueError): calculate(obstacles=obstacles)


def test_roof_cannot_hide_ground_break_above_it():
    with pytest.raises(ValueError):
        calculate(points=[(0, 0), (50, 20), (100, 0)])


@pytest.mark.parametrize('side', ['source', 'receiver', 'both'])
def test_below_mean_rule_keeps_physical_endpoints(side):
    points = [(0, 0), (10, 10), (190, 10), (200, 0)]
    # Long outer slopes induce a fitted plane above the low physical endpoints.
    s = (0, 1) if side in ('source', 'both') else (0, 14)
    r = (200, 1) if side in ('receiver', 'both') else (200, 14)
    result = calculate(points=points, source=s, receiver=r, ground_segments=[(0, 200, .5)],
        obstacles=[dict(vertices=[(70, 400), (130, 400)])])
    assert result['status'] == 'conditional_research'
    for end in (['source', 'receiver'] if side=='both' else [side]):
        frame = result[end+'_frame'][end]
        assert frame['original'] == list(s if end=='source' else r)
        assert result[end+'_below_mean_plane_rule']
        assert result[end+'_image_diffraction_db'] == result['main_diffraction_uncapped_db']
        assert result[end+'_ground_coupling_db'] == result[end+'_ground_db']
