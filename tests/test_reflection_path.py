"""Single-wall geometry, known zero-energy paths and explicit reflection inputs."""
import math

import pytest

from quiet_uk.propagation_canary import BANDS
from quiet_uk.favourable_propagation import EXACT_CENTRES, atmospheric_absorption
from quiet_uk.reflection_path import (
    combine_paths_attenuation, retrodiffraction, single_wall_attenuation,
    specular_plan, upper_ray_height,
)


AIR = dict(temperature_c=10, relative_humidity_percent=70,
           pressure_kpa=101.325, uniform_air=True)


def scene(**changes):
    result = dict(points=[(0, 0), (1000, 0)], source=(0, 1),
                  receiver=(1000, 4), source_g=0.,
                  ground_segments=[(0, 1000, .5)], obstacles=[], opaque=True,
                  infinite_lateral_extent=True, scene_kind='synthetic')
    result.update(changes)
    return result


def wall(**changes):
    result = dict(unfolded_x_m=500., base_z_m=0., top_z_m=120.,
                  slope_from_vertical_deg=0.,
                  incident_view_height_m=dict(homogeneous=200., favourable=200.),
                  incident_view_width_m=dict(homogeneous=200., favourable=200.),
                  absorption_by_band=[0.] * 8, unfolded_geometry_confirmed=True)
    result.update(changes)
    return result


def calculate(f=1000, config=None, reflector=None, air=None, probability=.5,
              policy='exact_base10'):
    return single_wall_attenuation(
        scene() if config is None else config,
        wall() if reflector is None else reflector, f,
        AIR if air is None else air,
        probability_favourable=probability, absorption_frequency_policy=policy,
    )


def test_specular_point_and_path_distance_agree_with_analytic_mirror_solution():
    result = specular_plan((2, 1), (2, 9), (0, 0), (0, 10))
    assert result['status'] == 'conditional_research'
    assert result['image_source_xy'] == pytest.approx((-2, 1))
    assert result['reflection_xy'] == pytest.approx((0, 5))
    assert result['wall_fraction'] == pytest.approx(.5)
    assert result['reflection_distance_from_source_m'] == pytest.approx(math.sqrt(20))
    assert result['unfolded_horizontal_distance_m'] == pytest.approx(math.sqrt(80))


def test_specular_geometry_is_invariant_under_wall_orientation_and_reciprocity():
    original = specular_plan((2, 1), (2, 9), (0, 0), (0, 10))
    reversed_wall = specular_plan((2, 1), (2, 9), (0, 10), (0, 0))
    reciprocal = specular_plan((2, 9), (2, 1), (0, 0), (0, 10))
    for result in (reversed_wall, reciprocal):
        assert result['reflection_xy'] == pytest.approx(original['reflection_xy'])
        assert result['unfolded_horizontal_distance_m'] == pytest.approx(
            original['unfolded_horizontal_distance_m'])


def test_specular_geometry_commutes_with_world_rotation_and_translation():
    theta = math.radians(37)

    def transform(point):
        x, y = point
        return (-100 + x * math.cos(theta) - y * math.sin(theta),
                325 + x * math.sin(theta) + y * math.cos(theta))

    result = specular_plan(*map(transform, [(2, 1), (2, 9), (0, 0), (0, 10)]))
    assert result['reflection_xy'] == pytest.approx(transform((0, 5)), abs=1e-11)
    assert result['image_source_xy'] == pytest.approx(transform((-2, 1)), abs=1e-11)
    assert result['unfolded_horizontal_distance_m'] == pytest.approx(math.sqrt(80))


@pytest.mark.parametrize('source,receiver,angle,shift,status,fraction', [
    ((0, 1), (2, 9), .1, (400000, 200000), 'absent', None),
    ((2, -3), (6, 9), .1, (0, 0), 'conditional_research', 0.),
    ((2, 9), (6, 13), .4, (0, 0), 'conditional_research', 1.),
])
def test_rotated_exact_wall_plane_and_endpoint_hits_survive_roundoff(
        source, receiver, angle, shift, status, fraction):
    def transform(point):
        x, y = point
        return (x * math.cos(angle) - y * math.sin(angle) + shift[0],
                x * math.sin(angle) + y * math.cos(angle) + shift[1])

    result = specular_plan(*map(transform, [source, receiver, (0, 0), (0, 10)]))
    assert result['status'] == status
    if fraction is not None:
        assert result['wall_fraction'] == pytest.approx(fraction, abs=1e-13)
        assert result['reflection_xy'] == pytest.approx(transform((0, 10 * fraction)), abs=1e-13)


@pytest.mark.parametrize('source,receiver', [
    ((2, 1), (-2, 9)), ((0, 1), (2, 9)), ((2, 20), (2, 40)),
])
def test_geometrically_impossible_reflection_is_known_absent(source, receiver):
    assert specular_plan(source, receiver, (0, 0), (0, 10))['status'] == 'absent'


def test_unknown_plan_geometry_is_withheld_and_degenerate_wall_is_invalid():
    assert specular_plan(None, (2, 9), (0, 0), (0, 10))['status'] == 'withheld'
    with pytest.raises(ValueError):
        specular_plan((2, 1), (2, 9), (0, 0), (0, 0))


@pytest.mark.parametrize('bad', [True, '2', math.nan, math.inf])
def test_plan_coordinates_reject_malformed_numbers(bad):
    with pytest.raises(ValueError):
        specular_plan((bad, 1), (2, 9), (0, 0), (0, 10))


@pytest.mark.parametrize('x', [125., 500., 875.])
def test_homogeneous_hit_height_is_linear_and_favourable_hit_lies_on_original_circle(x):
    homogeneous = upper_ray_height((0, 1), (1000, 4), x, 'homogeneous')
    assert homogeneous['height_m'] == pytest.approx(1 + 3 * x / 1000)
    curved = upper_ray_height((0, 1), (1000, 4), x, 'favourable')
    chord = math.hypot(1000, 3)
    radius = max(1000., 8 * chord)
    # Independent centre construction; the centre lies below the chord.
    offset = math.sqrt(radius * radius - chord * chord / 4)
    centre_x = 500 + 3 * offset / chord
    centre_z = 2.5 - 1000 * offset / chord
    expected = centre_z + math.sqrt(radius * radius - (x - centre_x) ** 2)
    assert curved['radius_m'] == pytest.approx(radius)
    assert curved['height_m'] == pytest.approx(expected, abs=3e-11)
    assert curved['height_m'] >= homogeneous['height_m'] - 1e-11


@pytest.mark.parametrize('x', [0., 1000., -1., 1001.])
def test_reflecting_wall_abscissa_must_be_strictly_between_unfolded_endpoints(x):
    with pytest.raises(ValueError):
        upper_ray_height((0, 1), (1000, 4), x, 'homogeneous')


@pytest.mark.parametrize('f', BANDS)
def test_retrodiffraction_zero_excess_and_wavelength_threshold(f):
    wavelength = 340 / f
    assert retrodiffraction(f, 0) == pytest.approx(10 * math.log10(3))
    assert retrodiffraction(f, -wavelength / 20) == 0
    assert retrodiffraction(f, -wavelength / 20 - 1e-8) == 0
    assert retrodiffraction(f, -wavelength / 20 + 1e-8) > 0


@pytest.mark.parametrize('f', BANDS)
def test_half_absorbing_wall_reduces_reflected_energy_by_half(f):
    hard = calculate(f)
    half = calculate(f, reflector=wall(absorption_by_band=[.5] * 8))
    loss = 10 * math.log10(2)
    for condition in ('homogeneous', 'favourable'):
        a, b = hard[condition], half[condition]
        assert a['status'] == b['status'] == 'conditional_research'
        assert a['absorption_loss_db'] == 0
        assert b['reflection_power_correction_db'] == pytest.approx(-loss)
        assert b['absorption_loss_db'] == pytest.approx(loss)
        assert b['attenuation_db'] - a['attenuation_db'] == pytest.approx(loss)
    assert half['attenuation_db'] - hard['attenuation_db'] == pytest.approx(loss)


def test_per_band_absorption_remains_a_spectrum():
    spectrum = [.0, .1, .2, .3, .4, .5, .6, .7]
    for f, alpha in zip(BANDS, spectrum):
        result = calculate(f, reflector=wall(absorption_by_band=spectrum))
        for condition in ('homogeneous', 'favourable'):
            assert result[condition]['reflection_power_correction_db'] == pytest.approx(
                10 * math.log10(1 - alpha))


def test_fully_absorbing_wall_has_zero_energy_without_nonfinite_attenuation():
    result = calculate(reflector=wall(absorption_by_band=[1.] * 8))
    for condition in ('homogeneous', 'favourable'):
        assert result[condition]['status'] == 'absent'
        assert result[condition]['energy_ratio'] == 0
        assert result[condition]['attenuation_db'] is None
    assert result['attenuation_db'] is None
    assert result['annual_noise_prediction'] is None


def test_curved_ray_above_wall_is_known_absent_and_weather_weights_remaining_power():
    reflector = wall(top_z_m=10)
    result = calculate(reflector=reflector)
    assert result['homogeneous']['status'] == 'conditional_research'
    assert result['homogeneous']['hit_height_m'] == pytest.approx(2.5)
    assert result['favourable']['status'] == 'absent'
    assert result['favourable']['energy_ratio'] == 0
    h = result['homogeneous']['attenuation_db']
    assert result['attenuation_db'] == pytest.approx(h + 10 * math.log10(2))
    assert calculate(reflector=reflector, probability=0)['attenuation_db'] == pytest.approx(h)
    assert calculate(reflector=reflector, probability=1)['attenuation_db'] is None


@pytest.mark.parametrize('condition', ['homogeneous', 'favourable'])
def test_wall_top_includes_exact_hit_and_rejects_hit_above_top(condition):
    hit = upper_ray_height((0, 1), (1000, 4), 500, condition)['height_m']
    at_top = calculate(reflector=wall(top_z_m=hit))
    below_top = calculate(reflector=wall(top_z_m=hit - 1e-7))
    assert at_top[condition]['status'] == 'conditional_research'
    assert below_top[condition]['status'] == 'absent'


def test_admissible_hit_at_wall_top_has_zero_excess_retrodiffraction_loss():
    hit = upper_ray_height((0, 1), (1000, 4), 500, 'homogeneous')['height_m']
    result = calculate(reflector=wall(top_z_m=hit))
    assert result['homogeneous']['retrodiffraction_db'] == pytest.approx(10 * math.log10(3))
    assert result['favourable']['status'] == 'absent'


def test_reflected_transfer_uses_unfolded_three_dimensional_distance_for_air_and_divergence():
    result = calculate()
    distance = math.hypot(1000, 3)
    alpha = atmospheric_absorption(EXACT_CENTRES[1000], **AIR)['alpha_db_per_km']
    for condition in ('homogeneous', 'favourable'):
        assert result[condition]['reflected_distance_m'] == pytest.approx(distance)
        assert result[condition]['divergence_db'] == pytest.approx(20 * math.log10(distance) + 11)
        assert result[condition]['atmospheric_attenuation_db'] == pytest.approx(alpha * distance / 1000)


def test_wall_top_outside_curved_retrodiffraction_domain_is_withheld_without_exception():
    result = calculate(reflector=wall(top_z_m=1e10))
    assert result['homogeneous']['status'] == 'conditional_research'
    assert result['favourable']['status'] == 'withheld'
    assert result['favourable']['reason'] == 'unsupported_retrodiffraction_geometry'
    assert result['favourable']['energy_ratio'] is None
    assert result['status'] == 'withheld'
    assert result['attenuation_db'] is None


def test_vertical_datum_shift_preserves_hit_geometry_and_attenuation():
    shifted_scene = scene(points=[(0, 200), (1000, 200)],
                          source=(0, 201), receiver=(1000, 204))
    shifted_wall = wall(base_z_m=200, top_z_m=320)
    original, shifted = calculate(), calculate(config=shifted_scene, reflector=shifted_wall)
    assert shifted['attenuation_db'] == pytest.approx(original['attenuation_db'], abs=1e-10)
    for condition in ('homogeneous', 'favourable'):
        assert shifted[condition]['hit_height_m'] == pytest.approx(
            original[condition]['hit_height_m'] + 200, abs=1e-10)
        assert shifted[condition]['retrodiffraction_db'] == pytest.approx(
            original[condition]['retrodiffraction_db'], abs=1e-10)


@pytest.mark.parametrize('dimension', ['incident_view_height_m', 'incident_view_width_m'])
def test_incident_dimension_threshold_is_half_a_metre(dimension):
    below = calculate(reflector=wall(**{dimension: dict(homogeneous=.49999, favourable=.49999)}))
    at = calculate(reflector=wall(**{dimension: dict(homogeneous=.5, favourable=.5)}))
    for condition in ('homogeneous', 'favourable'):
        assert below[condition]['status'] == 'absent'
        assert at[condition]['status'] == 'conditional_research'


@pytest.mark.parametrize('slope,status', [(15., 'absent'), (20., 'absent'), (1., 'withheld')])
def test_wall_slope_obeys_method_limit_and_bounded_vertical_scope(slope, status):
    result = calculate(reflector=wall(slope_from_vertical_deg=slope))
    for condition in ('homogeneous', 'favourable'):
        assert result[condition]['status'] == status


@pytest.mark.parametrize('field,value', [
    ('unfolded_geometry_confirmed', None), ('unfolded_geometry_confirmed', False),
    ('base_z_m', None), ('top_z_m', None), ('unfolded_x_m', None),
    ('absorption_by_band', None), ('slope_from_vertical_deg', None),
    ('incident_view_height_m', None), ('incident_view_width_m', None),
])
def test_unknown_wall_inputs_withhold_energy(field, value):
    result = calculate(reflector=wall(**{field: value}))
    assert result['attenuation_db'] is None
    assert result['status'] == 'withheld'
    assert result['annual_noise_prediction'] is None


def test_missing_one_condition_dimension_is_unknown_not_zero_reflection():
    result = calculate(reflector=wall(incident_view_width_m=dict(homogeneous=200.)))
    assert result['homogeneous']['status'] == 'conditional_research'
    assert result['favourable']['status'] == 'withheld'
    assert result['favourable']['energy_ratio'] is None
    assert result['attenuation_db'] is None


def test_zero_favourable_probability_ignores_unknown_favourable_dimensions():
    result = calculate(probability=0, reflector=wall(
        incident_view_width_m=dict(homogeneous=200., favourable=None)))
    assert result['status'] == 'conditional_research'
    assert result['attenuation_db'] == pytest.approx(result['homogeneous']['attenuation_db'])
    assert result['favourable']['status'] == 'withheld'
    assert result['favourable']['energy_ratio'] is None


def test_unit_favourable_probability_ignores_unknown_homogeneous_dimensions():
    result = calculate(probability=1, reflector=wall(
        incident_view_width_m=dict(homogeneous=None, favourable=200.)))
    assert result['status'] == 'conditional_research'
    assert result['attenuation_db'] == pytest.approx(result['favourable']['attenuation_db'])
    assert result['homogeneous']['status'] == 'withheld'
    assert result['homogeneous']['energy_ratio'] is None


@pytest.mark.parametrize('air_field', ['temperature_c', 'relative_humidity_percent', 'pressure_kpa', 'uniform_air'])
def test_missing_air_input_withholds_reflection_attenuation(air_field):
    result = calculate(air=dict(AIR, **{air_field: None}))
    assert result['status'] == 'withheld'
    assert result['attenuation_db'] is None


def test_unqualified_real_scene_and_unknown_frequency_policy_are_withheld():
    for result in (calculate(config=scene(scene_kind='unqualified')), calculate(policy=None)):
        assert result['status'] == 'withheld'
        assert result['attenuation_db'] is None
        assert result['annual_noise_prediction'] is None


@pytest.mark.parametrize('changes', [
    dict(source=(0, None)), dict(source_g=None),
    dict(ground_segments=[(0, 1000, None)]),
    dict(obstacles=[dict(vertices=[(500, 40)])]),
])
def test_incomplete_boundary_or_reflection_diffraction_interaction_is_withheld(changes):
    result = calculate(config=scene(**changes))
    assert result['status'] == 'withheld'
    assert result['attenuation_db'] is None


def test_missing_weather_probability_preserves_conditional_branches_but_withholds_mix():
    result = calculate(probability=None)
    assert result['homogeneous']['status'] == 'conditional_research'
    assert result['favourable']['status'] == 'conditional_research'
    assert result['attenuation_db'] is None
    assert result['annual_noise_prediction'] is None


@pytest.mark.parametrize('alpha', [-.01, 1.01, True, '0.5', math.nan, math.inf])
def test_absorption_coefficients_reject_out_of_domain_or_malformed_values(alpha):
    with pytest.raises(ValueError):
        calculate(reflector=wall(absorption_by_band=[alpha] * 8))


def test_wall_absorption_requires_all_eight_explicit_bands():
    with pytest.raises(ValueError):
        calculate(reflector=wall(absorption_by_band=[.5] * 7))


@pytest.mark.parametrize('bad', [True, '500', math.nan, math.inf])
def test_wall_geometry_rejects_malformed_numbers(bad):
    with pytest.raises(ValueError):
        calculate(reflector=wall(unfolded_x_m=bad))


def test_path_powers_are_added_in_energy_space_and_stably_for_large_losses():
    for levels in ((20., 20.), (20., 30.), (10000., 10010.)):
        result = combine_paths_attenuation([
            dict(status='conditional_research', attenuation_db=a) for a in levels])
        base = min(levels)
        expected = base - 10 * math.log10(sum(10 ** (-(a - base) / 10) for a in levels))
        assert result['attenuation_db'] == pytest.approx(expected)
    assert combine_paths_attenuation([
        dict(status='conditional_research', attenuation_db=20.),
        dict(status='absent', attenuation_db=None, energy_ratio=0),
    ])['attenuation_db'] == pytest.approx(20.)


def test_unknown_path_prevents_incomplete_total_and_absent_paths_are_zero_energy():
    partial = combine_paths_attenuation([
        dict(status='conditional_research', attenuation_db=20.),
        dict(status='withheld', attenuation_db=None, energy_ratio=None),
    ])
    assert partial['status'] == 'withheld'
    assert partial['attenuation_db'] is None
    absent = combine_paths_attenuation([
        dict(status='absent', attenuation_db=None, energy_ratio=0),
        dict(status='absent', attenuation_db=None, energy_ratio=0),
    ])
    assert absent['status'] == 'absent'
    assert absent['attenuation_db'] is None
    assert absent['energy_ratio'] == 0
