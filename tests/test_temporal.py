"""Acoustic identities and limits of temporal inference."""
import math
import pytest

from quiet_uk.temporal import SoundInterval as I, summarize_intervals


def summary(records, **kwargs):
    return summarize_intervals(records, window_start_s=0, window_end_s=86400, **kwargs)


def test_steady_and_short_extreme_event_have_distinct_profiles():
    steady = summary([I(0, 86400, 40)], level_kind='constant')
    intermittent = summary([I(0, 86390, 30), I(86390, 86400, 120)], level_kind='constant')
    assert steady['laeq_full_window_db'] == 40
    assert intermittent['laeq_full_window_db'] == pytest.approx(80.6349000937493)
    assert intermittent['la90_observed_db'] == 30
    assert intermittent['quiet_observed_seconds'] == 86390
    assert intermittent['longest_observed_quiet_interval_seconds'] == 86390
    assert steady['quiet_observed_seconds'] == 0  # strict below 40
    assert intermittent['event_count'] is None  # levels are not event annotations


def test_equal_energy_does_not_mean_equal_quiet_intervals():
    together = summary([I(0, 43200, 30), I(43200, 86400, 60)], level_kind='constant')
    separated = summary([I(0, 21600, 30), I(21600, 43200, 60), I(43200, 64800, 30), I(64800, 86400, 60)], level_kind='constant')
    assert together['laeq_full_window_db'] == pytest.approx(separated['laeq_full_window_db'])
    assert together['quiet_observed_seconds'] == separated['quiet_observed_seconds']
    assert together['longest_observed_quiet_interval_seconds'] == 43200
    assert separated['longest_observed_quiet_interval_seconds'] == 21600


def test_interval_means_do_not_establish_peaks_percentiles_or_quiet_time():
    result = summary([I(0, 86400, 30)])
    assert result['laeq_full_window_db'] == 30
    assert result['la90_observed_db'] is None
    assert result['quiet_observed_seconds'] is None
    assert result['event_count'] is None


def test_missing_time_is_not_silence_and_breaks_quiet_runs():
    result = summary([I(0, 100, 30), I(200, 400, 30)], level_kind='constant')
    assert result['laeq_full_window_db'] is None
    assert result['laeq_observed_db'] == 30
    assert result['observed_seconds'] == 300
    assert result['missing_seconds'] == 86100
    assert result['longest_observed_quiet_interval_seconds'] == 200


def test_splitting_intervals_does_not_change_energy_or_quiet_duration():
    one = summary([I(0, 86400, 20)], level_kind='constant')
    many = summary([I(0, 3, 20), I(3, 111, 20), I(111, 86400, 20)], level_kind='constant')
    assert one == many


def test_empty_observation_returns_no_level():
    result = summary([])
    assert result['laeq_observed_db'] is None
    assert result['coverage_fraction'] == 0
    assert result['missing_seconds'] == 86400


@pytest.mark.parametrize('records', [
    [I(-1, 10, 30)], [I(1, 0, 30)], [I(0, 0, 30)],
    [I(0, 86401, 30)], [I(0, 10, 30), I(9, 20, 40)],
    [I(0, 10, math.nan)], [I(0, math.inf, 30)],
])
def test_invalid_intervals_rejected(records):
    with pytest.raises(ValueError): summary(records)


def test_tiny_gap_cannot_be_promoted_to_complete():
    result = summary([I(0, 10, 30), I(10.0000001, 86400, 30)])
    assert result['complete'] is False
    assert result['laeq_full_window_db'] is None


def test_duration_weighting_is_not_sample_count_weighting():
    result = summary([I(0, 86399, 30), I(86399, 86400, 60)])
    expected = 10*math.log10((86399*1000 + 1000000)/86400)
    assert result['laeq_full_window_db'] == pytest.approx(expected)


def test_percentiles_use_duration_and_documented_inverse_cdf():
    result = summary([I(0, 8640, 20), I(8640, 86400, 60)], level_kind='constant')
    assert result['la90_observed_db'] == 20
    assert result['la50_observed_db'] == 60


@pytest.mark.parametrize('kwargs', [{'level_kind':'peak'}, {'quiet_threshold_db':math.nan}])
def test_invalid_summary_contract(kwargs):
    with pytest.raises(ValueError): summary([], **kwargs)
