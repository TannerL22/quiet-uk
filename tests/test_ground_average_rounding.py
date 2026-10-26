"""Convex ground means stay bounded while raw factors and coverage stay strict."""
from fractions import Fraction
import math
import random

import pytest

from quiet_uk.propagation_canary import ground_factor
from quiet_uk.terrain_boundary import ground_average, side_ground

AVERAGES = (ground_average, side_ground)
REAL_END = 12.968881310556787
REAL_SPLIT = 3.09681158594688


def exact_mean(parts, start, end):
    left, right = Fraction(start), Fraction(end)
    return sum((max(Fraction(0), min(Fraction(b), right)-max(Fraction(a), left))*Fraction(g)
                for a, b, g in parts), Fraction(0))/(right-left)


@pytest.mark.parametrize('average', AVERAGES)
def test_recorded_soft_ground_partition_recovers_exact_boundary(average):
    parts = [(0., REAL_SPLIT, 1.), (REAL_SPLIT, REAL_END, 1.)]
    prior = math.fsum((b-a)*g for a, b, g in parts)/REAL_END
    assert prior == math.nextafter(1., math.inf)
    assert exact_mean(parts, 0., REAL_END) == 1
    assert average(parts, 0., REAL_END) == 1.


def test_clipped_side_uses_only_the_requested_length():
    parts = [(0., REAL_SPLIT, 1.), (REAL_SPLIT, REAL_END+10., 1.)]
    assert side_ground(parts, 0., REAL_END) == 1.
    assert side_ground([(0., 1., 0.), (1., 3., 1.), (3., 4., 0.)], .5, 3.5) == 2/3


@pytest.mark.parametrize('average', AVERAGES)
def test_partitioned_valid_means_match_exact_arithmetic_and_keep_prior_numeric_values(average):
    rng = random.Random(92713)
    for offset in (-1000., 0., 500000.):
        for _ in range(32):
            points = [offset, *sorted(offset+rng.random()*100 for _ in range(10)), offset+100.]
            factors = [rng.choice((0., .2, .7, 1.)) for _ in range(len(points)-1)]
            parts = [(a, b, g) for a, b, g in zip(points, points[1:], factors)]
            actual = average(parts, points[0], points[-1])
            prior = math.fsum((b-a)*g for a, b, g in parts)/(points[-1]-points[0])
            assert 0 <= actual <= 1
            assert actual == pytest.approx(float(exact_mean(parts, points[0], points[-1])), rel=3e-15)
            if 0 <= prior <= 1:
                assert actual == prior


@pytest.mark.parametrize('average', AVERAGES)
@pytest.mark.parametrize('factor', [math.nextafter(1., math.inf), math.nextafter(0., -math.inf),
                                    math.inf, -math.inf, math.nan, True, '1'])
def test_invalid_supplied_factor_is_rejected_even_when_weighting_would_hide_it(average, factor):
    with pytest.raises(ValueError):
        average([(0., 1., factor), (1., 2., 0.)], 0., 2.)
    with pytest.raises(ValueError):
        ground_factor(factor)


@pytest.mark.parametrize('average', AVERAGES)
def test_unknown_or_empty_support_never_becomes_a_numeric_average(average):
    assert average([(0., 1., 1.), (1., 2., None)], 0., 2.) is None
    assert average([], 0., 2.) is None


@pytest.mark.parametrize('average', AVERAGES)
@pytest.mark.parametrize('parts', [[(0., 1., 1.), (1.5, 2., 1.)],
                                   [(0., 1.5, 1.), (1., 2., 1.)], [(0., 0., 1.)]])
def test_gaps_overlaps_and_zero_width_intervals_are_rejected(average, parts):
    with pytest.raises(ValueError):
        average(parts, 0., 2.)


@pytest.mark.parametrize('average', AVERAGES)
@pytest.mark.parametrize('start,end', [(0., 0.), (2., 1.), (-1., 1.), (0., 3.), (0., math.inf)])
def test_unsupported_averaging_ranges_are_rejected(average, start, end):
    with pytest.raises(ValueError):
        average([(0., 2., 1.)], start, end)
