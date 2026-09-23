from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from .config import DEFAULT_REPORTING_THRESHOLDS


STANDARD_TILE_BANDS = (
    "combined_reported_lower_db",
    "road_rail_upper_db",
    "airport_reported_lower_db",
    "airport_reported_fraction",
)
COMBINED_UPPER_BAND = "combined_upper_db"
DEFAULT_NODATA = -9999.0
ACOUSTIC_MIN_DB = 0.0
ACOUSTIC_MAX_DB = 150.0
SEMANTIC_TOLERANCE = 1e-5


def effective_reporting_thresholds(config: Mapping) -> dict[str, float | None]:
    configured = config.get("reporting_threshold_db", {})
    result: dict[str, float | None] = {}
    for source, default in DEFAULT_REPORTING_THRESHOLDS.items():
        value = configured.get(source, default)
        result[source] = None if value is None else float(value)
    return result


def expected_band_schema(config: Mapping) -> list[str]:
    bands = list(STANDARD_TILE_BANDS)
    if effective_reporting_thresholds(config)["airport"] is not None:
        bands.append(COMBINED_UPPER_BAND)
    return bands


def _finite_non_nodata(arrays: np.ndarray, nodata: float) -> np.ndarray:
    return np.isfinite(arrays) & (arrays != nodata)


def semantic_tile_checks(
    arrays: np.ndarray,
    land: np.ndarray,
    nodata: float = DEFAULT_NODATA,
    band_names: list[str] | tuple[str, ...] | None = None,
    thresholds: Mapping[str, float | None] | None = None,
    tolerance: float = SEMANTIC_TOLERANCE,
) -> dict:
    """Calculate shared production-band semantic checks without raising.

    The checks describe the current censored production product. Numeric acoustic
    values use the existing 0--150 dB dataset sanity range; this is not a claim
    that every physical sound level must lie in that interval.
    """
    arrays = np.asarray(arrays, dtype="float64")
    land = np.asarray(land, dtype=bool)
    if arrays.ndim != 3 or arrays.shape[1:] != land.shape:
        raise ValueError("Tile arrays and land mask must have compatible shapes")
    if arrays.shape[0] not in (len(STANDARD_TILE_BANDS), len(STANDARD_TILE_BANDS) + 1):
        raise ValueError("Production semantic checks expect four or five output bands")

    names = list(band_names or STANDARD_TILE_BANDS)
    if len(names) != arrays.shape[0]:
        raise ValueError("Band names and tile arrays have different counts")
    if tuple(names[:4]) != STANDARD_TILE_BANDS:
        raise ValueError("Production bands are not in the documented order")
    if len(names) == 5 and names[4] != COMBINED_UPPER_BAND:
        raise ValueError("The optional fifth production band must be combined_upper_db")

    thresholds = dict(DEFAULT_REPORTING_THRESHOLDS) | dict(thresholds or {})
    if thresholds.get("road") is None or thresholds.get("rail") is None:
        raise ValueError("Road and rail reporting thresholds are required for production validation")
    road_threshold = float(thresholds["road"])
    rail_threshold = float(thresholds["rail"])
    if not math.isfinite(road_threshold) or not math.isfinite(rail_threshold):
        raise ValueError("Road and rail reporting thresholds must be finite")

    finite_non_nodata = _finite_non_nodata(arrays, float(nodata))
    outside = ~land
    acoustic_valid = finite_non_nodata & land[None, :, :]
    acoustic_valid[3] = False
    impossible_acoustic = acoustic_valid & (
        (arrays < ACOUSTIC_MIN_DB) | (arrays > ACOUSTIC_MAX_DB)
    )
    valid_zero_acoustic = acoustic_valid & (arrays == 0.0)

    fraction = arrays[3]
    fraction_valid = finite_non_nodata[3] & land
    fraction_invalid = land & ~fraction_valid
    fraction_out_of_range = land & fraction_valid & (
        (fraction < -tolerance) | (fraction > 1.0 + tolerance)
    )

    combined_valid = finite_non_nodata[0] & land
    road_rail_valid = finite_non_nodata[1] & land
    airport_valid = finite_non_nodata[2] & land
    both_lower_valid = combined_valid & airport_valid
    floor = 10.0 * math.log10(
        math.pow(10.0, road_threshold / 10.0)
        + math.pow(10.0, rail_threshold / 10.0)
    )

    checks = {
        "outside_non_nodata_cells": int(np.sum(arrays[:, outside] != nodata)),
        "nonfinite_cells": int(np.sum(~np.isfinite(arrays))),
        "sentinel_in_land_cells": int(np.sum((arrays == nodata)[:, land])),
        "fraction_invalid_cells": int(np.sum(fraction_invalid)),
        "fraction_out_of_range_cells": int(np.sum(fraction_out_of_range)),
        "fraction_zero_with_airport_reported": int(
            np.sum(land & (fraction == 0.0) & airport_valid)
        ),
        "positive_fraction_without_airport_lower_cells": int(
            np.sum(land & fraction_valid & (fraction > 0.0) & ~airport_valid)
        ),
        "airport_lower_without_combined_lower_cells": int(
            np.sum(land & airport_valid & ~combined_valid)
        ),
        "combined_below_airport_cells": int(
            np.sum(both_lower_valid & (arrays[0] + tolerance < arrays[2]))
        ),
        "missing_road_rail_upper_cells": int(np.sum(land & ~road_rail_valid)),
        "road_rail_upper_below_floor_cells": int(
            np.sum(road_rail_valid & (arrays[1] + tolerance < floor))
        ),
        "impossible_acoustic_cells": int(np.sum(impossible_acoustic)),
        "valid_zero_acoustic_cells": int(np.sum(valid_zero_acoustic)),
        "road_rail_reporting_floor_db": floor,
        "combined_upper_missing_cells": 0,
        "combined_upper_below_combined_lower_cells": 0,
        "combined_upper_below_road_rail_upper_cells": 0,
    }
    if arrays.shape[0] == 5:
        combined_upper_valid = finite_non_nodata[4] & land
        checks["combined_upper_missing_cells"] = int(np.sum(land & ~combined_upper_valid))
        checks["combined_upper_below_combined_lower_cells"] = int(
            np.sum(combined_upper_valid & combined_valid & (arrays[4] + tolerance < arrays[0]))
        )
        checks["combined_upper_below_road_rail_upper_cells"] = int(
            np.sum(combined_upper_valid & road_rail_valid & (arrays[4] + tolerance < arrays[1]))
        )

    checks.update({
        "outside_is_nodata": checks["outside_non_nodata_cells"] == 0,
        "fraction_in_range": (
            checks["fraction_invalid_cells"] == 0
            and checks["fraction_out_of_range_cells"] == 0
        ),
        "combined_lower_ge_airport_lower": checks["combined_below_airport_cells"] == 0,
        "road_rail_upper_present": checks["missing_road_rail_upper_cells"] == 0,
        "no_nonfinite_values": checks["nonfinite_cells"] == 0,
        "acoustic_values_plausible": checks["impossible_acoustic_cells"] == 0,
    })
    checks["acoustic_valid_values"] = int(np.sum(acoustic_valid))
    return checks


def validate_production_arrays(
    arrays: np.ndarray,
    land: np.ndarray,
    nodata: float = DEFAULT_NODATA,
    band_names: list[str] | tuple[str, ...] | None = None,
    config: Mapping | None = None,
    tolerance: float = SEMANTIC_TOLERANCE,
) -> dict:
    """Raise a concise error when a production tile violates band semantics."""
    thresholds = effective_reporting_thresholds(config or {})
    checks = semantic_tile_checks(
        arrays,
        land,
        nodata=nodata,
        band_names=band_names,
        thresholds=thresholds,
        tolerance=tolerance,
    )
    failures: list[str] = []
    if checks["nonfinite_cells"]:
        failures.append(f"stored arrays contain {checks['nonfinite_cells']} NaN/infinite cells")
    if checks["outside_non_nodata_cells"]:
        failures.append(
            f"{checks['outside_non_nodata_cells']} cells outside land mask are not nodata"
        )
    if checks["missing_road_rail_upper_cells"]:
        failures.append(
            f"road_rail_upper_db is missing on {checks['missing_road_rail_upper_cells']} land cells"
        )
    if checks["fraction_invalid_cells"]:
        failures.append(
            f"airport_reported_fraction is missing on {checks['fraction_invalid_cells']} land cells"
        )
    if checks["fraction_out_of_range_cells"]:
        failures.append(
            f"airport_reported_fraction is outside [0,1] on "
            f"{checks['fraction_out_of_range_cells']} cells"
        )
    if checks["fraction_zero_with_airport_reported"]:
        failures.append(
            f"airport fraction is zero with a reported lower bound on "
            f"{checks['fraction_zero_with_airport_reported']} cells"
        )
    if checks["positive_fraction_without_airport_lower_cells"]:
        failures.append(
            f"positive airport fraction lacks a lower bound on "
            f"{checks['positive_fraction_without_airport_lower_cells']} cells"
        )
    if checks["airport_lower_without_combined_lower_cells"]:
        failures.append(
            f"airport lower bound lacks a combined lower bound on "
            f"{checks['airport_lower_without_combined_lower_cells']} cells"
        )
    if checks["combined_below_airport_cells"]:
        failures.append(
            f"combined lower bound is below airport lower bound on "
            f"{checks['combined_below_airport_cells']} cells"
        )
    if checks["road_rail_upper_below_floor_cells"]:
        failures.append(
            f"road/rail upper bound is below the {checks['road_rail_reporting_floor_db']:.5f} dB "
            f"censor floor on {checks['road_rail_upper_below_floor_cells']} cells"
        )
    if checks["impossible_acoustic_cells"]:
        failures.append(
            f"{checks['impossible_acoustic_cells']} acoustic cells fall outside the "
            f"{ACOUSTIC_MIN_DB:g}--{ACOUSTIC_MAX_DB:g} dB dataset sanity range"
        )
    if checks["valid_zero_acoustic_cells"]:
        failures.append(
            f"{checks['valid_zero_acoustic_cells']} numeric acoustic cells are zero "
            "under the production QA convention"
        )
    if checks["combined_upper_missing_cells"]:
        failures.append(
            f"combined_upper_db is missing on {checks['combined_upper_missing_cells']} land cells"
        )
    if checks["combined_upper_below_combined_lower_cells"]:
        failures.append(
            f"combined_upper_db is below combined lower bound on "
            f"{checks['combined_upper_below_combined_lower_cells']} cells"
        )
    if checks["combined_upper_below_road_rail_upper_cells"]:
        failures.append(
            f"combined_upper_db is below road/rail upper bound on "
            f"{checks['combined_upper_below_road_rail_upper_cells']} cells"
        )
    if failures:
        raise ValueError("Production tile semantic validation failed: " + "; ".join(failures))
    return checks
