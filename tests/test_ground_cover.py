"""Guard against turning classified cover or mapped fences into acoustic truth."""
import math

import numpy as np
import pytest
from affine import Affine
from shapely.geometry import LineString

from quiet_uk.ground_cover import (CLASSES, barrier_candidate, conditional_ground,
    crosswalk, midpoint_samples, native_window, parse_height, sample_class, snapshot_consistency)


@pytest.mark.parametrize('x,y,expected', [(0, 2, (0, 0)), (.9, 1.1, (0, 0)),
    (1, 2, (0, 1)), (0, 1, (1, 0)), (2, 1, None), (1, 0, None), (-.001, 1, None),
    (1, 2.001, None), (math.nan, 1, None), (1, math.inf, None)])
def test_native_pixel_ownership(x, y, expected):
    a = np.array([[10, 30], [50, 80]], dtype=np.uint8)
    result = sample_class(a, np.full_like(a, 255), Affine(1, 0, 0, 0, -1, 2), x, y)
    if expected is None:
        assert result == dict(row=None, column=None, code=None, state='outside')
    else:
        assert (result['row'], result['column']) == expected
        assert result['code'] == a[expected]
        assert result['state'] == 'reported_class'


@pytest.mark.parametrize('code,mask,state', [(0, 255, 'nodata'), (50, 0, 'nodata'), (255, 255, 'unsupported_class'), (30, 255, 'reported_class')])
def test_zero_mask_unknown_and_valid_class_stay_distinct(code, mask, state):
    result = sample_class(np.array([[code]], dtype='uint8'), np.array([[mask]], dtype='uint8'), Affine(1, 0, 0, 0, -1, 1), .5, .5)
    assert result['code'] == code and result['state'] == state


def test_window_contains_envelope_without_reprojection():
    t = Affine(.1, 0, -1, 0, -.1, 3)
    w = native_window(t, 100, 100, (-.94, 2.32, -.73, 2.98))
    assert w.flatten() == (0, 0, 3, 7)


def test_geographic_outer_edges_stay_excluded_despite_rounding():
    t = Affine(1/12000, 0, -.552, 0, -1/12000, 51.56)
    a = np.full((100, 100), 30, dtype='uint8')
    mask = np.full_like(a, 255)
    for x,y in [(t.c+100*t.a, t.f), (t.c, t.f+100*t.e)]:
        assert sample_class(a,mask,t,x,y)['state'] == 'outside'


@pytest.mark.parametrize('envelope', [(-2, 2, 0, 3), (0, 2, 12, 3), (0, -8, 1, 3), (0, 2, 1, 4), (0, 3, 1, 2), (0, 2, math.nan, 3)])
def test_window_outside_or_invalid_is_not_silently_clipped(envelope):
    with pytest.raises(ValueError):
        native_window(Affine(.1, 0, -1, 0, -.1, 3), 100, 100, envelope)


def test_midpoint_weights_sum_to_entire_path_without_duplicate_endpoints():
    line = LineString([(0, 0), (5, 0)])
    rows = list(midpoint_samples(line, 2))
    assert len(rows) == 3
    assert math.fsum(r[1] for r in rows) == 5
    assert rows[0][0] > 0 and rows[-1][0] < 5
    assert all(0 < r[1] <= 2 for r in rows)


@pytest.mark.parametrize('code', list(CLASSES))
def test_crosswalk_never_claims_measured_g_or_probabilities(code):
    row = crosswalk()[str(code)]
    assert row['observed_ground_g'] is None
    assert row['sensitivity_cases_g'] == [0, .3, .7, 1]
    if code not in (30, 40, 50, 80):
        assert row['conditional_analogy_g'] is None


def test_conditional_mix_uses_entire_length_and_does_not_omit_missing():
    assert conditional_ground({30: 30, 50: 70})['conditional_analogy_g'] == .3
    for unknown in (None, 0, 10, 20, 60, 90, 255):
        row = conditional_ground({30: 30, 50: 70, unknown: 2})
        assert row['conditional_analogy_g'] is None
        assert row['ambiguous_or_missing_length_m'] == 2
        assert row['path_length_m'] == 102
    assert conditional_ground({50: 100})['conditional_analogy_g'] == 0
    assert conditional_ground({50: 100})['observed_ground_g'] is None


@pytest.mark.parametrize('lengths', [{}, {30: 0}, {30: -1}, {30: math.nan}, {30: math.inf}])
def test_conditional_ground_rejects_invalid_length_evidence(lengths):
    with pytest.raises(ValueError):
        conditional_ground(lengths)


@pytest.mark.parametrize('raw,expected', [('2', 2), ('2.5', 2.5), (' 2 m ', 2), ('.8 m', .8),
    ('2m', 2), (None, None), ('', None), ('0', None), ('-2', None), ('nan', None),
    ('2;3', None), ('2-3', None), ('6 ft', None), ('2,5', None), ('~2', None), ('3e0', None)])
def test_heights_keep_ambiguous_ranges_units_and_missing_unknown(raw, expected):
    assert parse_height(raw)['tagged_height_m'] == expected


@pytest.mark.parametrize('tags,category', [({'barrier': 'wall', 'wall': 'noise_barrier'}, 'explicit_noise_barrier_candidate'),
    ({'barrier': 'wall'}, 'generic_wall_candidate'), ({'barrier': 'retaining_wall'}, 'generic_wall_candidate'),
    ({'barrier': 'fence', 'fence_type': 'chain_link'}, 'generic_fence_candidate'),
    ({'barrier': 'wire_fence'}, 'generic_fence_candidate'), ({'man_made': 'embankment'}, 'embankment_candidate'),
    ({'barrier': 'hedge'}, 'other_barrier_tag_no_acoustic_classification'),
    ({'barrier': 'guard_rail'}, 'other_barrier_tag_no_acoustic_classification')])
def test_barrier_tags_do_not_establish_an_opaque_acoustic_screen(tags, category):
    row = barrier_candidate({**tags, 'height': '2', 'material': 'wood'})
    assert row['category'] == category
    assert row['tagged_height_m'] == 2
    assert row['acoustic_screen_height_m'] is None
    assert row['acoustic_top_elevation_odn_m'] is None
    assert row['opaque_and_sealed'] is None
    assert row['acoustic_absorption'] is None


def test_snapshot_bracketing_does_not_claim_physical_presence_or_change_date():
    a = dict(geometry=[{'lon': 0, 'lat': 1}], tags={'barrier': 'wall'}, version=1)
    b = {**a, 'version': 2}
    assert snapshot_consistency(a, b).startswith('matching_geometry_and_tags')
    assert 'physical persistence unestablished' in snapshot_consistency(a, b)
    assert snapshot_consistency(a, {**b, 'tags': {'barrier': 'fence'}}).startswith('geometry_or_tags_differ')
    assert snapshot_consistency(None, a).startswith('not_in_start')
    assert snapshot_consistency(a, None).startswith('not_in_end')
