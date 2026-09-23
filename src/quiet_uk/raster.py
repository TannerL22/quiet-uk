from __future__ import annotations

import numpy as np
from rasterio.transform import Affine
from rasterio.warp import Resampling, reproject


def read_single_band_db(dataset, zero_is_censored: bool = True):
    """Read a Defra-style single-band dB raster with encoding diagnostics.

    The live Round 4 road and rail GeoTIFFs use ``-96`` as the declared
    nodata value but also use literal zeroes for unreported/below-threshold
    cells.  Values are returned as dB floats with both representations mapped
    to NaN.  GDAL scale/offset metadata is applied before the zero check.
    """
    if dataset.count != 1:
        raise ValueError(
            f"Expected one raster band, got {dataset.count}; "
            "do not silently combine an unexpected WCS range structure."
        )

    raw = np.asarray(dataset.read(1, masked=False))
    raw_float = raw.astype("float64", copy=False)
    mask = dataset.read_masks(1) == 0
    nodata = dataset.nodatavals[0]

    raw_finite = np.isfinite(raw_float)
    raw_nonfinite = ~raw_finite
    if nodata is not None and np.isnan(nodata):
        declared_nodata = np.isnan(raw_float)
    elif nodata is not None:
        declared_nodata = raw_float == float(nodata)
    else:
        declared_nodata = np.zeros(raw_float.shape, dtype=bool)
    unexpected_raw_nonfinite = raw_nonfinite & ~declared_nodata
    raster_mask_cells = mask
    declared_nodata_exclusive = declared_nodata & ~mask

    if unexpected_raw_nonfinite.any():
        indices = np.argwhere(unexpected_raw_nonfinite)
        evidence = [tuple(int(value) for value in row) for row in indices[:5]]
        raise ValueError(
            "Unexpected raw non-finite source values: "
            f"count={int(unexpected_raw_nonfinite.sum())}, "
            f"raw_nonfinite_cells={int(raw_nonfinite.sum())}, "
            f"declared_nodata_cells={int(declared_nodata.sum())}, "
            f"raster_mask_cells={int(mask.sum())}, "
            f"sample_indices={evidence}; declare NaN as nodata if supported"
        )

    invalid = mask | declared_nodata | raw_nonfinite
    scale = float(dataset.scales[0]) if dataset.scales else 1.0
    offset = float(dataset.offsets[0]) if dataset.offsets else 0.0
    if not np.isfinite(scale) or not np.isfinite(offset):
        raise ValueError(f"Source raster scale/offset must be finite, got scale={scale}, offset={offset}")
    eligible = raw_finite & ~mask & ~declared_nodata
    values = np.full(raw_float.shape, np.nan, dtype="float64")
    with np.errstate(over="ignore", invalid="ignore"):
        values[eligible] = raw_float[eligible] * scale + offset
    decoded_nonfinite = eligible & ~np.isfinite(values)
    if decoded_nonfinite.any():
        indices = np.argwhere(decoded_nonfinite)
        evidence = [tuple(int(value) for value in row) for row in indices[:5]]
        raise ValueError(
            "Decoded source values are non-finite after scale/offset decoding: "
            f"eligible_cell_count={int(eligible.sum())}, "
            f"nonfinite_decoded_cells={int(decoded_nonfinite.sum())}, "
            f"scale={scale}, offset={offset}, sample_indices={evidence}"
        )
    zero_censored = np.isclose(values, 0.0, rtol=0.0, atol=0.0)
    literal_zero_censored = zero_censored & raw_finite & ~mask & ~declared_nodata
    if zero_is_censored:
        invalid |= zero_censored

    values[invalid] = np.nan
    diagnostics = {
        "raw_dtype": str(raw.dtype),
        "nodata": None if nodata is None else float(nodata),
        "scale": scale,
        "offset": offset,
        "masked_cells": int(mask.sum()),
        "raster_mask_cells": int(raster_mask_cells.sum()),
        "nodata_cells": int((mask | declared_nodata).sum()),
        "declared_nodata_cells": int(declared_nodata_exclusive.sum()),
        "declared_nodata_cells_overlapping": int(declared_nodata.sum()),
        "raw_nonfinite_cells": int(raw_nonfinite.sum()),
        "unexpected_raw_nonfinite_cells": int(unexpected_raw_nonfinite.sum()),
        "reported_finite_cells": int(np.isfinite(values).sum()),
        "zero_censored_cells": int(zero_censored.sum()) if zero_is_censored else 0,
        "literal_zero_censored_cells": int(literal_zero_censored.sum()) if zero_is_censored else 0,
        "zero_censored_cells_semantics": "overlapping decoded-zero count; see literal_zero_censored_cells for exclusive count",
        "provider_nodata_policy": "declared nodata and raster mask are interpreted as unreported/censored for this dataset; this is not a universal nodata meaning",
    }
    return values, diagnostics


def grids_match(left: dict, right: dict, tolerance: float = 1e-6) -> bool:
    """Return whether two rasters share the same pixel grid.

    A sub-micrometre tolerance absorbs serialization noise in affine values,
    while any real pixel offset, shape, CRS, or extent mismatch still fails.
    """
    if left["shape"] != right["shape"] or left["crs"] != right["crs"]:
        return False
    return (
        np.allclose(left["transform"], right["transform"], rtol=0.0, atol=tolerance)
        and np.allclose(left["bounds"], right["bounds"], rtol=0.0, atol=tolerance)
    )


def align_array_to_grid(array: np.ndarray, source_grid: dict, target_grid: dict):
    """Nearest-neighbour align a dB array to an explicitly chosen grid."""
    if source_grid["crs"] is None or target_grid["crs"] is None:
        raise ValueError("Cannot align a raster without CRS metadata")
    destination = np.full(target_grid["shape"], np.nan, dtype="float64")
    reproject(
        source=np.asarray(array, dtype="float64"),
        destination=destination,
        src_transform=Affine(*source_grid["transform"]),
        src_crs=source_grid["crs"],
        src_nodata=np.nan,
        dst_transform=Affine(*target_grid["transform"]),
        dst_crs=target_grid["crs"],
        dst_nodata=np.nan,
        resampling=Resampling.nearest,
    )
    return destination


def align_support_to_grid(source_grid: dict, target_grid: dict) -> np.ndarray:
    """Align source footprint support independently of acoustic values."""
    if source_grid["crs"] is None or target_grid["crs"] is None:
        raise ValueError("Cannot align geometric support without CRS metadata")
    source_support = np.ones(source_grid["shape"], dtype="uint8")
    destination = np.zeros(target_grid["shape"], dtype="uint8")
    reproject(
        source=source_support,
        destination=destination,
        src_transform=Affine(*source_grid["transform"]),
        src_crs=source_grid["crs"],
        src_nodata=None,
        dst_transform=Affine(*target_grid["transform"]),
        dst_crs=target_grid["crs"],
        dst_nodata=0,
        resampling=Resampling.nearest,
    )
    return destination.astype(bool)
