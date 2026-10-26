"""Controlled propagation checks, never a real-area noise predictor.

Flat mean ground, homogeneous atmosphere, one infinitely wide opaque thin
screen. JRC72550 VI-12, 14-18, 21-22, 30-32 and the 2021 clear-path selector.
No terrain solver, finite building, lateral path, reflection search, atmospheric
absorption, transmission, weather averaging or regulatory compliance is claimed.
"""
import math

import numpy as np

DIVERGENCE_FACTOR = 10 ** -1.1  # Published rounded +11 dB, rather than 10log(4pi).
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)
SOUND_SPEED = 340.0


def finite_number(value):
    if value is None or isinstance(value, (bool, str)):
        raise ValueError('A finite numeric input is required')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('A finite numeric input is required')
    return result


def positive(value):
    result = finite_number(value)
    if result <= 0:
        raise ValueError('A positive value is required')
    return result


def nonnegative(value):
    result = finite_number(value)
    if result < 0:
        raise ValueError('A nonnegative value is required')
    return result


def ground_factor(value):
    result = nonnegative(value)
    if result > 1:
        raise ValueError('Ground factor must lie in [0, 1]')
    return result


def power_db(power):
    power = nonnegative(power)
    return None if power == 0 else 10 * math.log10(power)


def sum_powers(values):
    # A missing component must not disappear into an otherwise numeric total.
    return math.fsum(nonnegative(v) for v in values)


def point_gain(distance_m):
    return DIVERGENCE_FACTOR / positive(distance_m) ** 2


def finite_line_power(power_per_m, half_length_m, perpendicular_distance_m):
    """Analytic integral of incoherent point sources on a centred straight line.

    Perpendicular distance is 3D distance to the source line, including height.
    No ground effect or atmospheric loss; metres convert line to point power.
    """
    q, length, a = nonnegative(power_per_m), positive(half_length_m), positive(perpendicular_distance_m)
    return q * DIVERGENCE_FACTOR * 2 * math.atan(length / a) / a


def midpoint_line_power(power_per_m, half_length_m, perpendicular_distance_m, maximum_step_m):
    q, length, a, step = (nonnegative(power_per_m), positive(half_length_m),
                          positive(perpendicular_distance_m), positive(maximum_step_m))
    n = math.ceil(2 * length / step)
    if n > 1_000_000:
        raise ValueError('Synthetic point-source budget exceeded')
    width = 2 * length / n
    x = -length + (np.arange(n) + .5) * width
    return float(q * width * DIVERGENCE_FACTOR * np.sum(1 / (x * x + a * a)))


def corrected_ground(g_path, g_source, dp, zs, zr):
    g_path, g_source = ground_factor(g_path), ground_factor(g_source)
    dp, zs, zr = positive(dp), nonnegative(zs), nonnegative(zr)
    height = zs + zr
    fraction = 1. if height == 0 else min(dp / (30 * height), 1.)
    return fraction * g_path + (1 - fraction) * g_source


def ground_homogeneous(frequency_hz, dp, zs, zr, g_path, g_source=0., *, receiver_side=False):
    """VI-14..18 on a flat mean plane; negative attenuation is amplification.

    Receiver-side diffraction uses Gpath directly. For the source-side term,
    follow the checked VI-31 text (Gprime for Gw and Gm). No extra road-reflection
    or pavement correction is added to this boundary term.
    """
    f, dp = positive(frequency_hz), positive(dp)
    zs, zr, g = nonnegative(zs), nonnegative(zr), ground_factor(g_path)
    g_source = ground_factor(g_source)
    if g == 0:
        return -3.
    gp = g if receiver_side else corrected_ground(g, g_source, dp, zs, zr)
    k = 2 * math.pi * f / SOUND_SPEED
    w = .0185 * f ** 2.5 * gp ** 2.6 / (
        f ** 1.5 * gp ** 2.6 + 1300 * f ** .75 * gp ** 1.3 + 1.16e6)
    cf = dp * (1 + 3 * w * dp * math.exp(-math.sqrt(w * dp))) / (1 + w * dp)
    # Algebraically identical to z^2-sqrt(2Cf/k)z+Cf/k, always positive.
    b = cf / (2 * k)
    p = 4 * k * k / (dp * dp) * ((zs - math.sqrt(b)) ** 2 + b) * ((zr - math.sqrt(b)) ** 2 + b)
    return max(-10 * math.log10(p), -3 * (1 - gp))


def signed_path_difference(dp, zs, zr, edge_x, edge_height):
    dp, x = positive(dp), positive(edge_x)
    zs, zr, h = finite_number(zs), finite_number(zr), finite_number(edge_height)
    if x >= dp:
        raise ValueError('Screen must lie strictly between source and receiver')
    direct = math.hypot(dp, zr - zs)
    excess = math.hypot(x, h - zs) + math.hypot(dp - x, zr - h) - direct
    above = h >= zs + (zr - zs) * x / dp
    return max(excess, 0.) * (1 if above else -1), above


def pure_diffraction(frequency_hz, delta_m, edge_height_m, *, cap_main=False):
    f, delta, h = positive(frequency_hz), finite_number(delta_m), nonnegative(edge_height_m)
    value = 40 * delta * f / SOUND_SPEED
    raw = 0. if value < -2 else max(10 * min(f * h / 250, 1.) * math.log10(3 + value), 0.)
    return min(raw, 25.) if cap_main else raw


def flat_boundary(frequency_hz, dp, zs, zr, g_path, *, screen=None):
    """One boundary branch: ground alone or diffraction including ground.

    Inputs are explicit synthetic geometry. None ground/height withholds the
    whole result. An absent screen means the synthetic scene explicitly has no
    obstacle; it must never be used to represent an unclassified real area.
    G_source is fixed at the method's road-platform value 0, not a measured
    pavement property. Pure diffraction terms used inside ground coupling are
    uncapped; only the main horizontal-edge term is capped at 25 dB.
    """
    f, dp, zs, zr = positive(frequency_hz), positive(dp), nonnegative(zs), nonnegative(zr)
    if g_path is None:
        return dict(status='withheld', reason='unknown_ground', boundary_db=None)
    g = ground_factor(g_path)
    if screen is None:
        return dict(status='conditional_synthetic', branch='ground_only', boundary_db=ground_homogeneous(f, dp, zs, zr, g))
    if screen.get('height_m') is None:
        return dict(status='withheld', reason='unknown_obstacle_height', boundary_db=None)
    x, h = positive(screen['x_m']), nonnegative(screen['height_m'])
    delta, blocked = signed_path_difference(dp, zs, zr, x, h)
    mirror_delta, _ = signed_path_difference(dp, -zs, -zr, x, h)
    wavelength = SOUND_SPEED / f
    # 2021/1226 selector; this canary has exact common flat source/receiver planes.
    use_diffraction = blocked or (delta > -wavelength / 20 and delta > wavelength / 4 - mirror_delta)
    if not use_diffraction:
        return dict(status='conditional_synthetic', branch='ground_only_clear_edge', delta_m=delta,
                    boundary_db=ground_homogeneous(f, dp, zs, zr, g))
    ds = signed_path_difference(dp, -zs, zr, x, h)[0]
    dr = signed_path_difference(dp, zs, -zr, x, h)[0]
    main = pure_diffraction(f, delta, h)
    source_image, receiver_image = pure_diffraction(f, ds, h), pure_diffraction(f, dr, h)
    source_ground = ground_homogeneous(f, x, zs, h, g)
    receiver_ground = ground_homogeneous(f, dp - x, h, zr, g, receiver_side=True)

    def coupled(ground, image):
        term = 1 + (10 ** (-ground / 20) - 1) * 10 ** (-(image - main) / 20)
        if term <= 0:
            raise ValueError('Unsupported nonpositive diffraction/ground coupling')
        return -20 * math.log10(term)

    gs, gr = coupled(source_ground, source_image), coupled(receiver_ground, receiver_image)
    return dict(status='conditional_synthetic', branch='diffraction_including_ground', delta_m=delta,
        main_diffraction_uncapped_db=main, main_diffraction_db=min(main, 25.),
        source_ground_coupling_db=gs, receiver_ground_coupling_db=gr,
        separate_ground_db=0., boundary_db=min(main, 25.) + gs + gr)


def source_elevation_candidate(terrain_odn_m, *, at_grade_confirmed=False, structure='', road_deck_odn_m=None):
    """Terrain is not road deck. A blank structure field does not prove at grade."""
    if 'tunnel' in structure.lower():
        return dict(status='withheld', reason='tunnel_requires_special_model', source_odn_m=None)
    if road_deck_odn_m is not None:
        return dict(status='conditional', basis='supplied_road_deck', source_odn_m=finite_number(road_deck_odn_m) + .05)
    if terrain_odn_m is None or not at_grade_confirmed or structure:
        return dict(status='withheld', reason='road_surface_elevation_unqualified', source_odn_m=None)
    return dict(status='conditional', basis='explicit_at_grade_assumption', source_odn_m=finite_number(terrain_odn_m) + .05)


def native_cell(x, y, bounds, resolution=2):
    """Native pixel ownership; east/south edges excluded, west/north included."""
    x, y = finite_number(x), finite_number(y)
    west, south, east, north = bounds
    resolution = positive(resolution)
    if not (west <= x < east and south < y <= north):
        return None
    return math.floor((north - y) / resolution), math.floor((x - west) / resolution)
