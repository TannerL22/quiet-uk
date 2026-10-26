import math

import pytest

from quiet_uk.propagation_canary import (corrected_ground, finite_line_power, flat_boundary,
    ground_homogeneous, midpoint_line_power, native_cell, point_gain, power_db,
    pure_diffraction, signed_path_difference, source_elevation_candidate, sum_powers)


def test_point_doubling_distance_and_power_are_distinct():
    assert power_db(point_gain(10)) == pytest.approx(-31.)
    assert power_db(point_gain(20) / point_gain(10)) == pytest.approx(-6.020599913279624)
    assert power_db(sum_powers([point_gain(10)] * 2) / point_gain(10)) == pytest.approx(3.010299956639812)


@pytest.mark.parametrize('bad', [None, True, '10', -1, 0, float('nan'), float('inf')])
def test_invalid_distance_does_not_get_clamped(bad):
    with pytest.raises((ValueError, TypeError)):
        point_gain(bad)


def test_zero_power_and_unknown_component_stay_distinct():
    assert sum_powers([0, 0]) == 0
    assert power_db(0) is None
    with pytest.raises(ValueError):
        sum_powers([1, None])


def test_line_analytic_short_and_infinite_limits():
    a = 25.
    # Short line behaves as a point carrying total power q times its length.
    q, half = 3., .001
    assert finite_line_power(q, half, a) == pytest.approx(2 * half * q * point_gain(a), rel=1e-9)
    assert finite_line_power(q, 1e9, a) == pytest.approx(q * math.pi * 10 ** -1.1 / a, rel=2e-8)
    assert (q * math.pi * 10 ** -1.1 / (2 * a)) / (q * math.pi * 10 ** -1.1 / a) == .5


@pytest.mark.parametrize('step,tolerance', [(5, 5e-6), (1, 2e-7), (.5, 5e-8)])
def test_source_segmentation_retains_metres_and_converges(step, tolerance):
    exact = finite_line_power(7., 200., 25.)
    numeric = midpoint_line_power(7., 200., 25., step)
    assert numeric == pytest.approx(exact, rel=tolerance)
    assert midpoint_line_power(0., 200., 25., step) == 0


def test_segmentation_budget_is_bounded():
    with pytest.raises(ValueError, match='budget'):
        midpoint_line_power(1, 1000, 20, .0001)


def test_midpoint_error_shrinks_quadratically_with_spacing():
    exact = finite_line_power(1., 200., 25.)
    errors = [abs(midpoint_line_power(1., 200., 25., s) / exact - 1) for s in (5., 1., .5)]
    assert errors[0] / errors[1] == pytest.approx(25, rel=.001)
    assert errors[1] / errors[2] == pytest.approx(4, rel=.001)


def test_longer_source_margin_adds_energy_but_is_not_an_error_bound():
    powers = [finite_line_power(1, h, 1000) for h in (500, 1000, 2000, 5000, 10000)]
    assert powers == sorted(powers)
    # A 5km half-length captures only about 87.4% of this ideal line.
    assert powers[3] / (math.pi * 10 ** -1.1 / 1000) == pytest.approx(.8743340836219977)


@pytest.mark.parametrize('frequency', [63, 125, 250, 500, 1000, 2000, 4000, 8000])
def test_hard_ground_closed_form_and_no_extra_reflection(frequency):
    result = flat_boundary(frequency, 100, .05, 4, 0)
    assert result['boundary_db'] == -3
    assert result['branch'] == 'ground_only'
    assert point_gain(100) * 10 ** (-result['boundary_db'] / 10) == pytest.approx(10 ** -4.8)


def test_source_platform_weighting_and_receiver_side_are_different():
    assert corrected_ground(1, 0, 30, 1, 4) == pytest.approx(.2)
    assert corrected_ground(1, 0, 150, 1, 4) == 1
    assert corrected_ground(1, 0, 200, 1, 4) == 1
    assert ground_homogeneous(1000, 30, 1, 4, 1) != ground_homogeneous(1000, 30, 1, 4, 1, receiver_side=True)


@pytest.mark.parametrize('g', [0, .3, .7, 1])
def test_ground_lower_bound(g):
    gp = corrected_ground(g, 0, 100, .05, 4)
    assert ground_homogeneous(1000, 100, .05, 4, g) >= -3 * (1 - gp)


@pytest.mark.parametrize('g', [-.01, 1.01, float('nan'), True])
def test_bad_ground_is_refused(g):
    with pytest.raises(ValueError):
        flat_boundary(1000, 100, .05, 4, g)


def test_missing_ground_and_obstacle_are_withheld():
    assert flat_boundary(1000, 100, .05, 4, None)['reason'] == 'unknown_ground'
    result = flat_boundary(1000, 100, .05, 4, 0, screen={'x_m': 40, 'height_m': None})
    assert result['boundary_db'] is None
    assert result['reason'] == 'unknown_obstacle_height'


def test_signed_path_geometry_tangent_and_clear_are_different():
    assert signed_path_difference(100, 1, 1, 50, 1) == (0., True)
    d, blocked = signed_path_difference(100, 1, 1, 50, 3)
    assert d == pytest.approx(2 * math.sqrt(2504) - 100)
    assert blocked
    d, blocked = signed_path_difference(100, 1, 1, 50, 0)
    assert d < 0 and not blocked


@pytest.mark.parametrize('x', [0, 100, 101])
def test_edge_must_be_on_path(x):
    with pytest.raises(ValueError):
        flat_boundary(1000, 100, 1, 4, 0, screen={'x_m': x, 'height_m': 3})


def test_pure_diffraction_canary_and_main_cap():
    # At delta=lambda/10, C''=1 and Ch=1, the published expression is 10log(7).
    assert pure_diffraction(1000, .034, 1) == pytest.approx(8.450980400142567)
    assert pure_diffraction(1000, 0, 1) == pytest.approx(4.771212547196624)
    assert pure_diffraction(1000, -.034, 1) == 0
    assert pure_diffraction(1000, 100, 1) > 25
    assert pure_diffraction(1000, 100, 1, cap_main=True) == 25
    assert pure_diffraction(63, .1 * 340 / 63, .1) == pytest.approx(.0252 * 8.450980400142567)


def test_screen_includes_ground_once_and_image_terms_are_uncapped():
    result = flat_boundary(8000, 100, .05, 4, 0, screen={'x_m': 40, 'height_m': 60})
    assert result['branch'] == 'diffraction_including_ground'
    assert result['separate_ground_db'] == 0
    assert result['main_diffraction_uncapped_db'] > 25
    assert result['main_diffraction_db'] == 25
    assert result['boundary_db'] == pytest.approx(25 + result['source_ground_coupling_db'] + result['receiver_ground_coupling_db'])


def test_clear_edge_rayleigh_branch_has_no_diffraction():
    result = flat_boundary(1000, 100, 4, 4, 0, screen={'x_m': 50, 'height_m': 0})
    assert result['branch'] == 'ground_only_clear_edge'
    assert result['boundary_db'] == -3


def test_near_clear_edge_also_requires_rayleigh_condition():
    d, blocked = signed_path_difference(100, .05, 4, 40, 1)
    assert not blocked
    assert d > -340 / 63 / 20  # The older wavelength-only selector would accept.
    result = flat_boundary(63, 100, .05, 4, 0, screen={'x_m': 40, 'height_m': 1})
    assert result['branch'] == 'ground_only_clear_edge'
    assert result['boundary_db'] == -3


def test_blocked_screen_reduces_energy_and_is_reciprocal_on_hard_ground():
    screen = {'x_m': 40, 'height_m': 3}
    a = flat_boundary(1000, 100, .05, 4, 0, screen=screen)
    b = flat_boundary(1000, 100, 4, .05, 0, screen={'x_m': 60, 'height_m': 3})
    assert a['boundary_db'] > -3
    assert b['boundary_db'] == pytest.approx(a['boundary_db'])


def test_bridge_tunnel_and_unknown_grade_do_not_become_terrain_sources():
    assert source_elevation_candidate(25)['source_odn_m'] is None
    assert source_elevation_candidate(25, at_grade_confirmed=True, structure='Bridge')['source_odn_m'] is None
    assert source_elevation_candidate(25, structure='Road In Tunnel', road_deck_odn_m=20)['source_odn_m'] is None
    assert source_elevation_candidate(25, structure='Bridge', road_deck_odn_m=31)['source_odn_m'] == 31.05


@pytest.mark.parametrize('terrain', [0, -3, 100])
def test_elevation_datum_and_zero_are_preserved(terrain):
    assert source_elevation_candidate(terrain, at_grade_confirmed=True)['source_odn_m'] == terrain + .05


@pytest.mark.parametrize('x,y,cell', [(10, 20, (0, 0)), (12, 18, (1, 1)), (20, 15, None), (15, 10, None), (9, 15, None), (15, 21, None)])
def test_native_grid_boundary_ownership(x, y, cell):
    assert native_cell(x, y, (10, 10, 20, 20)) == cell
