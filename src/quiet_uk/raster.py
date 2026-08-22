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

    invalid = mask | ~np.isfinite(raw_float)
    if nodata is not None:
        if np.isnan(nodata):
            invalid |= np.isnan(raw_float)
        else:
            invalid |= raw_float == float(nodata)

    scale = float(dataset.scales[0]) if dataset.scales else 1.0
    offset = float(dataset.offsets[0]) if dataset.offsets else 0.0
    values = raw_float * scale + offset
    zero_censored = np.isclose(values, 0.0, rtol=0.0, atol=0.0)
    if zero_is_censored:
        invalid |= zero_censored

    values[invalid] = np.nan
    diagnostics = {
        "raw_dtype": str(raw.dtype),
        "nodata": None if nodata is None else float(nodata),
        "scale": scale,
        "offset": offset,
        "masked_cells": int(mask.sum()),
        "nodata_cells": int((mask | (
            np.isnan(raw_float) if nodata is not None and np.isnan(nodata)
            else raw_float == float(nodata) if nodata is not None else False
        )).sum()),
        "zero_censored_cells": int(zero_censored.sum()) if zero_is_censored else 0,
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
