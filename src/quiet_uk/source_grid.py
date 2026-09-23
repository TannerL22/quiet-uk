"""Supported source-grid policies for the England production tile loader."""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np


GRID_POLICY_VERSION = "1"
EXPECTED_SOURCE_CRS = "EPSG:27700"
GRID_TOLERANCE = 1e-6
AIRPORT_PADDING_CELLS = 1

EXACT_GRID_POLICY = {
    "name": "exact-target-grid",
    "version": GRID_POLICY_VERSION,
    "alignment": "none",
}
AIRPORT_PADDED_GRID_POLICY = {
    "name": "airport-wcs20-padded-native-grid",
    "version": GRID_POLICY_VERSION,
    "alignment": "nearest-neighbour-to-target-grid",
    "padding_cells": AIRPORT_PADDING_CELLS,
}


def source_grid_policy_identity() -> dict:
    """Return the readable policy contract included in build identity."""
    return {
        "version": GRID_POLICY_VERSION,
        "road": {"accepted": [deepcopy(EXACT_GRID_POLICY)]},
        "rail": {"accepted": [deepcopy(EXACT_GRID_POLICY)]},
        "airport": {
            "accepted": [
                deepcopy(EXACT_GRID_POLICY),
                deepcopy(AIRPORT_PADDED_GRID_POLICY),
            ]
        },
    }


def _grid_error(source, tile_id, policy_name, property_name, expected, actual):
    raise ValueError(
        f"Unsupported source grid for {source} tile {tile_id} under policy "
        f"{policy_name} v{GRID_POLICY_VERSION}: {property_name} expected "
        f"{expected!r}, got {actual!r}"
    )


def _as_transform(grid):
    try:
        transform = tuple(float(value) for value in grid["transform"])
    except (KeyError, TypeError, ValueError):
        return None
    if len(transform) != 9 or not all(math.isfinite(value) for value in transform):
        return None
    return transform


def _validate_common_geometry(source, tile_id, grid, policy_name, *, label):
    if grid.get("crs") != EXPECTED_SOURCE_CRS:
        _grid_error(source, tile_id, policy_name, f"{label} CRS", EXPECTED_SOURCE_CRS, grid.get("crs"))
    transform = _as_transform(grid)
    if transform is None:
        _grid_error(source, tile_id, policy_name, f"{label} transform", "finite 3x3 affine", grid.get("transform"))
    a, b, _, d, e, _, g, h, i = transform
    if not (
        abs(b) <= GRID_TOLERANCE
        and abs(d) <= GRID_TOLERANCE
        and abs(g) <= GRID_TOLERANCE
        and abs(h) <= GRID_TOLERANCE
        and abs(i - 1.0) <= GRID_TOLERANCE
        and a > 0
        and e < 0
    ):
        _grid_error(
            source,
            tile_id,
            policy_name,
            f"{label} orientation/rotation",
            "unrotated east-positive, north-up affine",
            transform,
        )
    return transform


def _validate_target_geometry(source, tile_id, target_grid, policy_name):
    if target_grid.get("crs") != EXPECTED_SOURCE_CRS:
        raise ValueError(
            f"Unsupported production CRS/domain combination for {source} tile {tile_id}: "
            f"declared EPSG:27700 bounds and source-grid policy require {EXPECTED_SOURCE_CRS}, "
            f"got target CRS {target_grid.get('crs')!r}"
        )
    transform = _validate_common_geometry(
        source, tile_id, target_grid, policy_name, label="target"
    )
    shape = target_grid.get("shape")
    if (
        not isinstance(shape, (tuple, list))
        or len(shape) != 2
        or any(not isinstance(value, (int, np.integer)) or int(value) <= 0 for value in shape)
    ):
        _grid_error(source, tile_id, policy_name, "target dimensions", "two positive integers", shape)
    try:
        bounds = tuple(float(value) for value in target_grid["bounds"])
    except (KeyError, TypeError, ValueError):
        _grid_error(source, tile_id, policy_name, "target bounds", "four finite values", target_grid.get("bounds"))
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or not (bounds[2] > bounds[0] and bounds[3] > bounds[1]):
        _grid_error(source, tile_id, policy_name, "target bounds", "finite minx,miny,maxx,maxy", target_grid.get("bounds"))
    return transform


def _check_resolution(source, tile_id, actual_transform, expected_transform, policy_name):
    actual_resolution = (actual_transform[0], abs(actual_transform[4]))
    expected_resolution = (expected_transform[0], abs(expected_transform[4]))
    if not np.allclose(actual_resolution, expected_resolution, rtol=0.0, atol=GRID_TOLERANCE):
        _grid_error(
            source,
            tile_id,
            policy_name,
            "resolution",
            expected_resolution,
            actual_resolution,
        )


def _matches_expected(source, tile_id, raw_grid, target_grid, policy_name):
    _validate_target_geometry(source, tile_id, target_grid, policy_name)
    raw_transform = _validate_common_geometry(source, tile_id, raw_grid, policy_name, label="source")
    target_transform = _as_transform(target_grid)
    if target_transform is None:
        raise ValueError(f"Target grid for {source} tile {tile_id} has an invalid transform")
    if tuple(raw_grid.get("shape", ())) != tuple(target_grid.get("shape", ())):
        _grid_error(source, tile_id, policy_name, "dimensions", target_grid["shape"], raw_grid.get("shape"))
    _check_resolution(source, tile_id, raw_transform, target_transform, policy_name)
    if not np.allclose(raw_transform, target_transform, rtol=0.0, atol=GRID_TOLERANCE):
        _grid_error(source, tile_id, policy_name, "transform", target_transform, raw_transform)
    try:
        raw_bounds = tuple(float(value) for value in raw_grid["bounds"])
    except (KeyError, TypeError, ValueError):
        raw_bounds = None
    if raw_bounds is None or len(raw_bounds) != 4 or not np.allclose(raw_bounds, target_grid["bounds"], rtol=0.0, atol=GRID_TOLERANCE):
        _grid_error(source, tile_id, policy_name, "bounds", target_grid["bounds"], raw_grid.get("bounds"))


def _airport_padded_target(target_grid):
    height, width = target_grid["shape"]
    target_transform = _as_transform(target_grid)
    if target_transform is None:
        raise ValueError("Target grid has an invalid transform")
    resolution_x = target_transform[0]
    resolution_y = abs(target_transform[4])
    minx, miny, maxx, maxy = target_grid["bounds"]
    half_x = resolution_x * AIRPORT_PADDING_CELLS / 2.0
    half_y = resolution_y * AIRPORT_PADDING_CELLS / 2.0
    return {
        "shape": (height + AIRPORT_PADDING_CELLS, width + AIRPORT_PADDING_CELLS),
        "crs": EXPECTED_SOURCE_CRS,
        "transform": (
            resolution_x, 0.0, minx - half_x,
            0.0, -resolution_y, maxy + half_y,
            0.0, 0.0, 1.0,
        ),
        "bounds": (
            minx - half_x, miny - half_y,
            maxx + half_x, maxy + half_y,
        ),
    }


def _matches_grid(source, tile_id, raw_grid, expected_grid, policy_name):
    """Validate a response against a complete expected grid description."""
    _validate_common_geometry(source, tile_id, raw_grid, policy_name, label="source")
    if tuple(raw_grid.get("shape", ())) != tuple(expected_grid["shape"]):
        _grid_error(source, tile_id, policy_name, "dimensions", expected_grid["shape"], raw_grid.get("shape"))
    raw_transform = _as_transform(raw_grid)
    _check_resolution(source, tile_id, raw_transform, expected_grid["transform"], policy_name)
    if not np.allclose(raw_transform, expected_grid["transform"], rtol=0.0, atol=GRID_TOLERANCE):
        _grid_error(source, tile_id, policy_name, "transform", expected_grid["transform"], raw_grid.get("transform"))
    try:
        raw_bounds = tuple(float(value) for value in raw_grid["bounds"])
    except (KeyError, TypeError, ValueError):
        raw_bounds = None
    if raw_bounds is None or len(raw_bounds) != 4 or not np.allclose(raw_bounds, expected_grid["bounds"], rtol=0.0, atol=GRID_TOLERANCE):
        _grid_error(source, tile_id, policy_name, "bounds", expected_grid["bounds"], raw_grid.get("bounds"))


def target_pixel_centres_mask(target_grid: dict, declared_bounds) -> np.ndarray:
    """Classify target cells by centre against EPSG:27700 coverage bounds.

    Bounds are inclusive so a centre exactly on a declared edge remains inside
    the provider domain.
    """
    if target_grid.get("crs") != EXPECTED_SOURCE_CRS:
        raise ValueError(
            f"Unsupported CRS/domain combination: declared coverage bounds are {EXPECTED_SOURCE_CRS}, "
            f"got target CRS {target_grid.get('crs')!r}"
        )
    transform = _validate_common_geometry(
        "coverage", "target", target_grid, EXACT_GRID_POLICY["name"], label="target"
    )
    try:
        bounds = tuple(float(value) for value in declared_bounds)
    except (TypeError, ValueError):
        raise ValueError("Declared coverage bounds must contain four finite EPSG:27700 values") from None
    if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or not (bounds[2] > bounds[0] and bounds[3] > bounds[1]):
        raise ValueError(f"Declared coverage bounds are invalid: {declared_bounds!r}")
    height, width = (int(value) for value in target_grid["shape"])
    xs = transform[2] + (np.arange(width, dtype="float64") + 0.5) * transform[0]
    ys = transform[5] + (np.arange(height, dtype="float64") + 0.5) * transform[4]
    return (
        (xs[None, :] >= bounds[0])
        & (xs[None, :] <= bounds[2])
        & (ys[:, None] >= bounds[1])
        & (ys[:, None] <= bounds[3])
    )


def validate_source_grid(
    source: str,
    tile_id: str,
    target_grid: dict,
    raw_grid: dict,
    wcs_version: str,
    declared_bounds=None,
) -> dict:
    """Validate a source response and return its explicit permitted alignment."""
    if declared_bounds is not None and target_grid.get("crs") != EXPECTED_SOURCE_CRS:
        raise ValueError(
            f"Unsupported CRS/domain combination for {source} tile {tile_id}: "
            "declared coverage bounds are EPSG:27700"
        )

    exact = EXACT_GRID_POLICY["name"]
    _validate_target_geometry(source, tile_id, target_grid, exact)
    if declared_bounds is not None:
        try:
            bounds = tuple(float(value) for value in declared_bounds)
        except (TypeError, ValueError):
            raise ValueError(
                f"Invalid declared coverage bounds for {source} tile {tile_id}: {declared_bounds!r}"
            ) from None
        if len(bounds) != 4 or not all(math.isfinite(value) for value in bounds) or not (bounds[2] > bounds[0] and bounds[3] > bounds[1]):
            raise ValueError(
                f"Invalid declared coverage bounds for {source} tile {tile_id}: {declared_bounds!r}"
            )

    if source not in ("road", "rail", "airport"):
        raise ValueError(f"Unknown source-grid policy source: {source}")

    try:
        _matches_expected(source, tile_id, raw_grid, target_grid, exact)
    except ValueError as exact_error:
        if source != "airport" or wcs_version != "2.0.1":
            raise
        padded = AIRPORT_PADDED_GRID_POLICY["name"]
        expected_padded = _airport_padded_target(target_grid)
        try:
            _matches_grid(source, tile_id, raw_grid, expected_padded, padded)
        except ValueError as padded_error:
            raise ValueError(
                f"Unsupported source grid for airport tile {tile_id}: neither policy "
                f"{exact} v{GRID_POLICY_VERSION} nor permitted {padded} v{GRID_POLICY_VERSION} "
                f"matched; exact failure: {exact_error}; padded failure: {padded_error}"
            ) from padded_error
        return {
            "policy_name": padded,
            "policy_version": GRID_POLICY_VERSION,
            "alignment_required": True,
            "alignment_reason": "documented WCS 2.0.1 one-cell padded native grid",
        }

    if source in ("road", "rail"):
        return {
            "policy_name": exact,
            "policy_version": GRID_POLICY_VERSION,
            "alignment_required": False,
            "alignment_reason": "response is the exact requested tile grid",
        }

    return {
        "policy_name": exact,
        "policy_version": GRID_POLICY_VERSION,
        "alignment_required": False,
        "alignment_reason": "response is the exact requested tile grid",
    }
