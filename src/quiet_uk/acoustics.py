from __future__ import annotations
import numpy as np


def db_to_energy(db):
    """Convert dB values to relative acoustic energy."""
    db = np.asarray(db, dtype=float)
    return np.power(10.0, db / 10.0)


def energy_to_db(energy):
    """Convert relative acoustic energy to dB. Non-positive energy -> NaN."""
    energy = np.asarray(energy, dtype=float)
    out = np.full_like(energy, np.nan, dtype=float)
    mask = energy > 0
    out[mask] = 10.0 * np.log10(energy[mask])
    return out


def db_sum(*levels):
    """Logarithmically add dB arrays/scalars; NaN contributes no known energy."""
    arrays = np.broadcast_arrays(*[np.asarray(x, dtype=float) for x in levels])
    known = np.zeros_like(arrays[0], dtype=bool)
    energy = np.zeros_like(arrays[0], dtype=float)
    for arr in arrays:
        m = np.isfinite(arr)
        known |= m
        energy[m] += db_to_energy(arr[m])
    out = energy_to_db(energy)
    out[~known] = np.nan
    return out


def db_energy_mean(levels, axis=None):
    """Energy-equivalent mean of fully observed dB values (NaNs ignored)."""
    x = np.asarray(levels, dtype=float)
    energy = np.nanmean(db_to_energy(x), axis=axis)
    return energy_to_db(energy)


def combine_censored_sources(source_levels: dict[str, np.ndarray],
                             thresholds_db: dict[str, float]):
    """
    Combine source rasters while preserving lower-reporting censoring.

    finite value = reported source level
    NaN          = source unreported / below reporting threshold

    lower_energy is the known reported energy only.
    upper_energy adds threshold energy for every censored source, giving a
    deliberately conservative cell-level upper bound.
    """
    names = list(source_levels)
    arrays = {k: np.asarray(source_levels[k], dtype=float) for k in names}
    shape = np.broadcast_shapes(*[a.shape for a in arrays.values()])
    arrays = {k: np.broadcast_to(v, shape) for k, v in arrays.items()}

    lower_energy = np.zeros(shape, dtype=float)
    upper_energy = np.zeros(shape, dtype=float)
    any_reported = np.zeros(shape, dtype=bool)
    stacked = []

    for name in names:
        arr = arrays[name]
        finite = np.isfinite(arr)
        any_reported |= finite

        known_e = np.zeros(shape, dtype=float)
        known_e[finite] = db_to_energy(arr[finite])
        lower_energy += known_e
        upper_energy += known_e

        censored = ~finite
        upper_energy[censored] += db_to_energy(float(thresholds_db[name]))
        stacked.append(np.where(finite, arr, -np.inf))

    lower_db = energy_to_db(lower_energy)
    lower_db[~any_reported] = np.nan
    upper_db = energy_to_db(upper_energy)

    uncertainty_db = upper_db - lower_db
    uncertainty_db[~any_reported] = np.nan

    stack = np.stack(stacked, axis=0)
    idx = np.argmax(stack, axis=0)
    dominant = np.empty(shape, dtype=object)
    for i, name in enumerate(names):
        dominant[idx == i] = name
    dominant[~any_reported] = "none-reported"

    return {
        "lower_db": lower_db,
        "upper_db": upper_db,
        "lower_energy": lower_energy,
        "upper_energy": upper_energy,
        "uncertainty_db": uncertainty_db,
        "dominant_source": dominant,
        "all_sources_below_threshold": ~any_reported,
    }


def aggregate_energy_bounds(lower_energy: np.ndarray,
                            upper_energy: np.ndarray,
                            factor: int = 10):
    """
    Aggregate fine-grid acoustic energy bounds to coarser cells.

    Crucial censoring rule:
    - lower bound: an unreported fine cell has a mathematical lower energy of 0,
      so it remains in the denominator rather than being dropped.
    - upper bound: censored-source threshold energy is already present.

    This avoids the upward bias that would result from np.nanmean on dB values.
    """
    lo = np.asarray(lower_energy, dtype=float)
    hi = np.asarray(upper_energy, dtype=float)
    if lo.shape != hi.shape:
        raise ValueError("lower_energy and upper_energy must have identical shapes")
    if factor <= 0:
        raise ValueError("factor must be positive")

    h = (lo.shape[0] // factor) * factor
    w = (lo.shape[1] // factor) * factor
    if h == 0 or w == 0:
        raise ValueError("raster is smaller than aggregation factor")

    lo = lo[:h, :w]
    hi = hi[:h, :w]

    lo_blocks = lo.reshape(h // factor, factor, w // factor, factor)
    hi_blocks = hi.reshape(h // factor, factor, w // factor, factor)

    lo_mean_e = lo_blocks.mean(axis=(1, 3))
    hi_mean_e = hi_blocks.mean(axis=(1, 3))

    return {
        "lower_energy": lo_mean_e,
        "upper_energy": hi_mean_e,
        "lower_db": energy_to_db(lo_mean_e),
        "upper_db": energy_to_db(hi_mean_e),
    }
