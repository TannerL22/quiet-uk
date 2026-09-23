"""Duration-aware acoustic summaries, independent of map display.

Inputs are non-overlapping intervals at ONE receiver, on a common seconds axis,
with comparable A-weighted levels. No event history is inferred from raster data.
Callers must retain the source, time origin, weighting, receiver and provenance.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SoundInterval:
    start_s: float
    end_s: float
    level_db: float


def summarize_intervals(intervals, *, window_start_s, window_end_s,
                        level_kind='interval_laeq', quiet_threshold_db=40.0):
    """Summarise interval LAeq or explicitly piecewise-constant sound levels.

    Missing time stays missing. Full-window LAeq is null unless completely
    observed. Percentiles and quiet intervals require constant levels: interval
    energy averages cannot establish either. Threshold comparisons are strict <.
    This function neither detects acoustic events nor derives Lden from LAeq.
    """
    if level_kind not in ('interval_laeq', 'constant'):
        raise ValueError('Unknown level_kind')
    if not all(math.isfinite(x) for x in (window_start_s, window_end_s, quiet_threshold_db)) or window_end_s <= window_start_s:
        raise ValueError('A finite, positive observation window is required')
    records = sorted(intervals, key=lambda x: x.start_s)
    previous = window_start_s
    for item in records:
        if not all(math.isfinite(x) for x in (item.start_s, item.end_s, item.level_db)):
            raise ValueError('Intervals must contain finite times and levels')
        if item.start_s < previous or item.end_s <= item.start_s or item.end_s > window_end_s:
            raise ValueError('Intervals must be within the window and must not overlap')
        previous = item.end_s
    duration = window_end_s - window_start_s
    observed = math.fsum(x.end_s-x.start_s for x in records)
    complete = bool(records) and records[0].start_s == window_start_s and records[-1].end_s == window_end_s and all(a.end_s == b.start_s for a, b in zip(records, records[1:]))
    laeq = None
    if records:
        # Offset before exponentiation, avoiding overflow at large input levels.
        reference = max(x.level_db for x in records)
        energy = math.fsum(((x.end_s-x.start_s)/observed)*10**((x.level_db-reference)/10) for x in records)
        laeq = reference + 10*math.log10(energy)
    result = {
        'schema_version': 1, 'input_level_kind': level_kind,
        'window_start_s': window_start_s, 'window_end_s': window_end_s,
        'observed_seconds': observed, 'missing_seconds': max(0.0, duration-observed),
        'coverage_fraction': observed/duration, 'complete': complete,
        'laeq_observed_db': laeq, 'laeq_full_window_db': laeq if complete else None,
        'la90_observed_db': None, 'la50_observed_db': None,
        'maximum_input_level_db': max((x.level_db for x in records), default=None),
        'quiet_threshold_db': quiet_threshold_db, 'quiet_observed_seconds': None,
        'longest_observed_quiet_interval_seconds': None,
        'event_count': None, 'event_count_status': 'requires_event_records_or_explicit_detection_model',
        'distribution_status': 'unavailable_from_interval_energy_means',
    }
    if level_kind == 'constant' and records:
        # Weighted inverse empirical CDF; LA90 is the lower 10th percentile.
        ordered = sorted(records, key=lambda x: x.level_db)
        for key, fraction in [('la90_observed_db', .1), ('la50_observed_db', .5)]:
            elapsed = 0.0
            for item in ordered:
                elapsed += item.end_s-item.start_s
                if elapsed >= observed*fraction:
                    result[key] = item.level_db
                    break
        quiet = run = longest = 0.0
        previous_end = None
        for item in records:
            if item.start_s != previous_end:
                run = 0.0
            if item.level_db < quiet_threshold_db:
                length = item.end_s-item.start_s
                quiet += length
                run += length
                longest = max(longest, run)
            else:
                run = 0.0
            previous_end = item.end_s
        result.update(quiet_observed_seconds=quiet,
                      longest_observed_quiet_interval_seconds=longest,
                      distribution_status='piecewise_constant_observed_time_only')
    return result
