"""Bounded, read-only road/rail candidate screening for Quiet UK.

The screening product is deliberately downstream of the reviewed catalogue.
It reads only the requested 100 m window, uses the authoritative land mask,
and never changes source rasters, catalogue files, or acoustic calculations.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import shutil
import tempfile
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.features import shapes
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from rasterio.windows import Window

from .catalogue import (
    CatalogueError,
    CatalogueIntegrityError,
    DatasetCatalogue,
    SOURCES,
    SOURCE_STATES,
    TARGET_CRS,
    WGS84_CRS,
)
from .tiling import TILE_BANDS
from .validation import (
    ACOUSTIC_MAX_DB,
    ACOUSTIC_MIN_DB,
    DEFAULT_NODATA,
    validate_production_arrays,
)


CANDIDATE_SCREENING_SCHEMA_VERSION = 1
CANDIDATE_SCREENING_POLICY_VERSION = "1"
CANDIDATE_SCREENING_POLICY_NAME = "bounded-road-rail-upper-four-neighbour"
GRID_RESOLUTION_M = 100
MAX_REQUESTED_CELLS = 160_000
_GRID_ALIGNMENT_ABS_TOLERANCE = 1e-9


class CandidateScreeningError(CatalogueError):
    """Raised when a candidate-screening request is invalid or unsafe."""


@dataclass(frozen=True)
class CandidateScreening:
    """In-memory screening result before publication."""

    report: dict[str, Any]
    geojson: dict[str, Any]


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_text(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _finite_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise CandidateScreeningError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise CandidateScreeningError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise CandidateScreeningError(f"{label} must be a finite number")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise CandidateScreeningError(f"{label} must be a positive integer")
    try:
        integer = int(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise CandidateScreeningError(f"{label} must be a positive integer") from exc
    if isinstance(value, float) and value != integer:
        raise CandidateScreeningError(f"{label} must be a positive integer")
    if integer <= 0:
        raise CandidateScreeningError(f"{label} must be a positive integer")
    return integer


def _aligned_index(value: float, origin: float, label: str) -> int:
    raw = (value - origin) / GRID_RESOLUTION_M
    rounded = round(raw)
    if not math.isclose(raw, rounded, rel_tol=0.0, abs_tol=_GRID_ALIGNMENT_ABS_TOLERANCE):
        raise CandidateScreeningError(
            f"{label} is not exactly aligned to the authoritative 100 m BNG grid; no snapping is performed"
        )
    return int(rounded)


def _validate_request(
    bbox_bng: Sequence[Any],
    threshold_db: Any,
    minimum_component_cells: Any,
    mask_header: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        bbox_length = len(bbox_bng)
    except TypeError as exc:
        raise CandidateScreeningError("bbox_bng must contain west, south, east, north") from exc
    if isinstance(bbox_bng, (str, bytes)) or bbox_length != 4:
        raise CandidateScreeningError("bbox_bng must contain west, south, east, north")
    west, south, east, north = (
        _finite_float(value, label)
        for value, label in zip(bbox_bng, ("west", "south", "east", "north"), strict=True)
    )
    if not west < east or not south < north:
        raise CandidateScreeningError("bbox_bng must be strictly ordered west < east and south < north")
    threshold = _finite_float(threshold_db, "road_rail_upper_db threshold")
    if not ACOUSTIC_MIN_DB <= threshold <= ACOUSTIC_MAX_DB:
        raise CandidateScreeningError(
            f"road_rail_upper_db threshold must be within the existing {ACOUSTIC_MIN_DB:g}--{ACOUSTIC_MAX_DB:g} dB sanity range"
        )
    minimum = _positive_int(minimum_component_cells, "minimum_component_cells")

    transform = tuple(float(value) for value in mask_header["transform"])
    minx, miny, maxx, maxy = (float(value) for value in mask_header["bounds"])
    if west < minx or south < miny or east > maxx or north > maxy:
        raise CandidateScreeningError(
            "bbox_bng must lie within the authoritative England mask extent; no clipping or snapping is performed"
        )
    if not np.allclose(
        transform,
        (GRID_RESOLUTION_M, 0.0, transform[2], 0.0, -GRID_RESOLUTION_M, transform[5], 0.0, 0.0, 1.0),
        rtol=0.0,
        atol=1e-6,
    ):
        raise CandidateScreeningError("authoritative England mask is not an unrotated 100 m grid")

    col_start = _aligned_index(west, minx, "bbox west")
    col_stop = _aligned_index(east, minx, "bbox east")
    row_start = _aligned_index(maxy - north, 0.0, "bbox north")
    row_stop = _aligned_index(maxy - south, 0.0, "bbox south")
    width = col_stop - col_start
    height = row_stop - row_start
    if width <= 0 or height <= 0:
        raise CandidateScreeningError("bbox_bng must contain at least one 100 m grid cell")
    requested_cells = width * height
    if requested_cells > MAX_REQUESTED_CELLS:
        raise CandidateScreeningError(
            f"requested bbox contains {requested_cells} cells, exceeding the {MAX_REQUESTED_CELLS}-cell limit"
        )
    mask_height, mask_width = (int(value) for value in mask_header["shape"])
    if not (0 <= col_start <= col_stop <= mask_width and 0 <= row_start <= row_stop <= mask_height):
        raise CandidateScreeningError("bbox_bng indexing is outside the authoritative mask raster extent")
    return {
        "bbox_bng": [west, south, east, north],
        "threshold_db": threshold,
        "minimum_component_cells": minimum,
        "mask_window": {
            "row_start": row_start,
            "col_start": col_start,
            "height": height,
            "width": width,
        },
        "requested_cells": requested_cells,
        "mask_bounds_bng": [minx, miny, maxx, maxy],
        "mask_transform": list(transform),
    }


def _relative_or_absolute(path: Path) -> str:
    path = path.resolve()
    try:
        return path.relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return str(path)


def _mask_header(path: Path) -> dict[str, Any]:
    try:
        with rasterio.open(path) as dataset:
            if dataset.count != 1:
                raise CandidateScreeningError(f"authoritative England mask must have one band, got {dataset.count}")
            if str(dataset.crs) != TARGET_CRS:
                raise CandidateScreeningError(
                    f"authoritative England mask CRS must be {TARGET_CRS}, got {dataset.crs}"
                )
            return {
                "shape": [int(dataset.height), int(dataset.width)],
                "crs": str(dataset.crs),
                "transform": list(tuple(dataset.transform)),
                "bounds": [float(value) for value in dataset.bounds],
                "dtype": dataset.dtypes[0],
                "nodata": None if dataset.nodata is None else float(dataset.nodata),
            }
    except CandidateScreeningError:
        raise
    except (OSError, rasterio.errors.RasterioIOError) as exc:
        raise CandidateScreeningError(f"cannot read authoritative England mask {path}: {exc}") from exc


def read_screening_mask_header(path: str | Path) -> dict[str, Any]:
    """Read the authoritative mask header used by bounded request validation."""
    return _mask_header(Path(path).resolve())


def validate_screening_request(
    bbox_bng: Sequence[Any],
    threshold_db: Any,
    minimum_component_cells: Any,
    mask_header: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply the same bounded request rules used by extraction."""
    return _validate_request(bbox_bng, threshold_db, minimum_component_cells, mask_header)


def _tile_window(
    tile_row: Mapping[str, Any],
    bbox: tuple[float, float, float, float],
) -> tuple[Window, tuple[int, int, int, int]] | None:
    west, south, east, north = bbox
    tile_minx = _finite_float(tile_row["minx"], "catalogue tile minx")
    tile_miny = _finite_float(tile_row["miny"], "catalogue tile miny")
    tile_maxx = _finite_float(tile_row["maxx"], "catalogue tile maxx")
    tile_maxy = _finite_float(tile_row["maxy"], "catalogue tile maxy")
    if not (tile_minx < tile_maxx and tile_miny < tile_maxy):
        raise CatalogueIntegrityError(f"Catalogue tile {tile_row['tile_id']} has invalid bounds")
    inter_minx = max(west, tile_minx)
    inter_maxx = min(east, tile_maxx)
    inter_miny = max(south, tile_miny)
    inter_maxy = min(north, tile_maxy)
    if inter_minx >= inter_maxx or inter_miny >= inter_maxy:
        return None

    region_col0 = _aligned_index(inter_minx, west, f"tile {tile_row['tile_id']} intersection west")
    region_col1 = _aligned_index(inter_maxx, west, f"tile {tile_row['tile_id']} intersection east")
    region_row0 = _aligned_index(north - inter_maxy, 0.0, f"tile {tile_row['tile_id']} intersection north")
    region_row1 = _aligned_index(north - inter_miny, 0.0, f"tile {tile_row['tile_id']} intersection south")
    tile_col0 = _aligned_index(inter_minx, tile_minx, f"tile {tile_row['tile_id']} local west")
    tile_row0 = _aligned_index(tile_maxy - inter_maxy, 0.0, f"tile {tile_row['tile_id']} local north")
    width = region_col1 - region_col0
    height = region_row1 - region_row0
    if width <= 0 or height <= 0:
        raise CatalogueIntegrityError(f"Catalogue tile {tile_row['tile_id']} has an empty aligned intersection")
    expected_width = int(tile_row["width"])
    expected_height = int(tile_row["height"])
    if not (0 <= tile_col0 < expected_width and 0 <= tile_row0 < expected_height):
        raise CatalogueIntegrityError(f"Catalogue tile {tile_row['tile_id']} local window is outside its dimensions")
    if tile_col0 + width > expected_width or tile_row0 + height > expected_height:
        raise CatalogueIntegrityError(f"Catalogue tile {tile_row['tile_id']} local window exceeds its dimensions")
    return (
        Window(tile_col0, tile_row0, width, height),
        (region_row0, region_row1, region_col0, region_col1),
    )


def _validate_tile_header(dataset: rasterio.io.DatasetReader, tile_row: Mapping[str, Any]) -> None:
    tile_id = str(tile_row["tile_id"])
    if str(dataset.crs) != TARGET_CRS:
        raise CatalogueIntegrityError(f"Selected tile {tile_id} CRS mismatch")
    if dataset.count != len(TILE_BANDS) or list(dataset.descriptions) != list(TILE_BANDS):
        raise CatalogueIntegrityError(f"Selected tile {tile_id} band schema mismatch")
    if dataset.shape != (int(tile_row["height"]), int(tile_row["width"])):
        raise CatalogueIntegrityError(f"Selected tile {tile_id} dimensions mismatch catalogue")
    if list(dataset.dtypes) != ["float32"] * len(TILE_BANDS):
        raise CatalogueIntegrityError(f"Selected tile {tile_id} dtype schema mismatch")
    nodata_values = [None if value is None else float(value) for value in dataset.nodatavals]
    if any(value is None or value != DEFAULT_NODATA for value in nodata_values):
        raise CatalogueIntegrityError(f"Selected tile {tile_id} nodata schema mismatch")
    expected = tuple(float(value) for value in json.loads(str(tile_row["transform_json"])))
    if not np.allclose(tuple(dataset.transform), expected, rtol=0.0, atol=1e-6):
        raise CatalogueIntegrityError(f"Selected tile {tile_id} transform mismatch catalogue")


def _component_airport_summary(
    cells: Sequence[tuple[int, int]],
    owner_grid: np.ndarray,
    tile_infos: Sequence[Mapping[str, Any]],
    values: np.ndarray,
) -> dict[str, Any]:
    source_counts = {state: 0 for state in SOURCE_STATES}
    fraction_counts = {"zero": 0, "positive": 0, "unavailable": 0}
    fraction_by_qualification: dict[str, dict[str, int]] = {}
    lower_by_qualification: dict[str, dict[str, int]] = {}

    def bucket(target: dict[str, dict[str, int]], qualification: str) -> dict[str, int]:
        if qualification not in target:
            target[qualification] = {
                "cells": 0,
                "finite": 0,
                "censored_or_unreported": 0,
                "zero": 0,
                "positive": 0,
            }
        return target[qualification]

    for row, col in cells:
        info = tile_infos[int(owner_grid[row, col])]
        source_state = str(info["source_by_name"]["airport"]["state"])
        source_counts[source_state] = source_counts.get(source_state, 0) + 1

        fraction_quality = str(info["band_by_name"]["airport_reported_fraction"]["qualification"])
        fraction_bucket = bucket(fraction_by_qualification, fraction_quality)
        fraction_bucket["cells"] += 1
        fraction_value = float(values[TILE_BANDS.index("airport_reported_fraction"), row, col])
        fraction_available = math.isfinite(fraction_value) and fraction_value != DEFAULT_NODATA
        if fraction_available:
            fraction_bucket["finite"] += 1
            if fraction_value == 0.0:
                fraction_bucket["zero"] += 1
                fraction_counts["zero"] += 1
            elif fraction_value > 0.0:
                fraction_bucket["positive"] += 1
                fraction_counts["positive"] += 1
            else:
                raise CatalogueIntegrityError("Airport fraction has an invalid negative value after semantic validation")
        else:
            fraction_bucket["censored_or_unreported"] += 1
            fraction_counts["unavailable"] += 1

        lower_quality = str(info["band_by_name"]["airport_reported_lower_db"]["qualification"])
        lower_bucket = bucket(lower_by_qualification, lower_quality)
        lower_bucket["cells"] += 1
        lower_value = float(values[TILE_BANDS.index("airport_reported_lower_db"), row, col])
        lower_available = math.isfinite(lower_value) and lower_value != DEFAULT_NODATA
        if lower_available:
            lower_bucket["finite"] += 1
        else:
            lower_bucket["censored_or_unreported"] += 1

    cell_count = len(cells)
    partition_total = sum(source_counts.values())
    fraction_partition_total = sum(item["cells"] for item in fraction_by_qualification.values())
    lower_partition_total = sum(item["cells"] for item in lower_by_qualification.values())
    return {
        "source_quality_state_counts": dict(sorted(source_counts.items())),
        "fraction_counts": fraction_counts,
        "fraction_availability_by_qualification": {
            key: fraction_by_qualification[key] for key in sorted(fraction_by_qualification)
        },
        "lower_bound_availability_by_qualification": {
            key: lower_by_qualification[key] for key in sorted(lower_by_qualification)
        },
        "cell_count": cell_count,
        "partition_reconciles_to_component_cells": (
            partition_total == cell_count
            and fraction_partition_total == cell_count
            and lower_partition_total == cell_count
            and sum(fraction_counts.values()) == cell_count
        ),
        "interpretation": "A zero airport fraction means no reported airport pixels, not no aircraft noise.",
    }


def _polygonize_labelled_region(
    labels: np.ndarray,
    retained_labels: set[int],
    transform: Affine,
) -> dict[int, dict[str, Any]]:
    """Polygonize all retained labels in one bounded regional pass.

    The mask excludes filtered labels and background while the integer raster
    value keeps each component identity available for grouping.  No
    component-sized copies of the regional raster are created.
    """
    if not retained_labels:
        return {}
    labels = np.asarray(labels, dtype=np.int32)
    retained_mask = np.isin(labels, np.fromiter(sorted(retained_labels), dtype=np.int32))
    parts_by_label: dict[int, list[dict[str, Any]]] = {}
    for geometry, value in shapes(
        labels,
        mask=retained_mask,
        transform=transform,
        connectivity=4,
    ):
        label = int(value)
        if label in retained_labels:
            parts_by_label.setdefault(label, []).append(geometry)
    geometries: dict[int, dict[str, Any]] = {}
    for label in sorted(retained_labels):
        parts = parts_by_label.get(label, [])
        if not parts:
            raise CandidateScreeningError(f"Cannot polygonize retained candidate component label {label}")
        if len(parts) == 1:
            bng_geometry = parts[0]
        else:
            bng_geometry = {
                "type": "MultiPolygon",
                "coordinates": [part["coordinates"] for part in parts],
            }
        geometries[label] = transform_geom(TARGET_CRS, WGS84_CRS, bng_geometry, precision=12)
    return geometries


def _clean_component(component: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(component))
    result.pop("geometry", None)
    return result


def _markdown(report: Mapping[str, Any]) -> str:
    parameters = report["parameters"]
    summary = report["summary"]
    lines = [
        "# Quiet UK bounded road/rail candidate screening",
        "",
        f"- Run ID: `{report['run_id']}`",
        f"- Catalogue build ID: `{report['catalogue']['build_id']}`",
        f"- BNG bbox: `{parameters['bbox_bng']}`",
        f"- Road/rail upper threshold: `{parameters['road_rail_upper_threshold_db']} dB` (inclusive)",
        f"- Minimum component size: `{parameters['minimum_component_cells']} cells`",
        f"- Requested cells: `{summary['requested_cells']}`; retained components: `{summary['retained_component_count']}`",
        f"- Retained candidate cells: `{summary['retained_component_cells']}`; excluded small-component cells: `{summary['excluded_small_component_cells']}`",
        "",
        "Component order is descending cell count with a stable spatial tie-breaker; it is an area order, not an acoustic ranking.",
        "",
        "| Area order | Component | Cells | Area km² | Upper min–max dB | Airport zero / positive |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for component in report["components"]:
        fraction = component["airport"]["fraction_counts"]
        upper = component["road_rail_upper_db"]
        lines.append(
            f"| {component['area_order']} | `{component['component_id']}` | {component['cell_count']} | "
            f"{component['area_km2']:.2f} | {upper['min']:.3f}–{upper['max']:.3f} | "
            f"{fraction['zero']} / {fraction['positive']} |"
        )
    lines.extend([
        "",
        "## Status and limitations",
        "",
        f"- Public access status: `{report['public_access_status']}`.",
        f"- Historical acquisition linkage: `{report['historical_acquisition_linkage']}`.",
        "- No provider reference year is assigned by this screening pass.",
        "- Airport fraction zeros are not evidence of no aircraft noise.",
        "- Geometry is WGS84 GeoJSON for the actual eligible 100 m cells; reported area is cell-count area (`cells × 0.01 km²`).",
        "- This is a bounded screening result, not a public release, noise-health assessment, or acoustic ranking.",
        "",
    ])
    return "\n".join(lines)


def _build_geojson(report: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "run_id": report["run_id"],
        "schema_version": CANDIDATE_SCREENING_SCHEMA_VERSION,
        "screening_policy_version": CANDIDATE_SCREENING_POLICY_VERSION,
        "public_access_status": report["public_access_status"],
        "historical_acquisition_linkage": report["historical_acquisition_linkage"],
        "features": [
            {
                "type": "Feature",
                "geometry": component["geometry"],
                "properties": _clean_component(component),
            }
            for component in report["components"]
        ],
    }


def _screen_with_catalogue(
    catalogue: DatasetCatalogue,
    bbox_bng: Sequence[Any],
    threshold_db: Any,
    minimum_component_cells: Any,
) -> CandidateScreening:
    catalogue._verify_mask()
    if catalogue.land_mask_path is None:
        raise CatalogueIntegrityError("No authoritative England land mask was configured")
    mask_path = catalogue.land_mask_path
    header = _mask_header(mask_path)
    request = _validate_request(bbox_bng, threshold_db, minimum_component_cells, header)
    bbox = tuple(request["bbox_bng"])
    window_info = request["mask_window"]
    height = int(window_info["height"])
    width = int(window_info["width"])
    # The cell limit is checked above before any bounded raster arrays are allocated.
    with rasterio.open(mask_path) as mask_dataset:
        mask_values = mask_dataset.read(
            1,
            window=Window(
                int(window_info["col_start"]),
                int(window_info["row_start"]),
                width,
                height,
            ),
            boundless=False,
        )
    if mask_values.shape != (height, width):
        raise CatalogueIntegrityError("Authoritative England mask read shape disagrees with the requested bbox")
    land = np.asarray(mask_values != 0, dtype=bool)

    owner_grid = np.full((height, width), -1, dtype=np.int32)
    values = np.full((len(TILE_BANDS), height, width), np.nan, dtype="float64")
    tile_infos: list[dict[str, Any]] = []
    selected_tile_ids: list[str] = []

    for tile_row, source_rows, band_rows in catalogue.iter_tile_records():
        intersection = _tile_window(tile_row, bbox)
        if intersection is None:
            continue
        tile_id = str(tile_row["tile_id"])
        path = catalogue.verified_tile_path(tile_row)
        source_by_name = {str(row["source"]): row for row in source_rows}
        band_by_name = {str(row["band_name"]): row for row in band_rows}
        if set(source_by_name) != set(SOURCES) or set(band_by_name) != set(TILE_BANDS):
            raise CatalogueIntegrityError(f"Catalogue quality rows are incomplete for selected tile {tile_id}")
        tile_info_index = len(tile_infos)
        tile_infos.append({
            "tile_id": tile_id,
            "source_by_name": source_by_name,
            "band_by_name": band_by_name,
        })
        selected_tile_ids.append(tile_id)
        tile_window, (row0, row1, col0, col1) = intersection
        if np.any(owner_grid[row0:row1, col0:col1] != -1):
            raise CatalogueIntegrityError(
                f"Selected bbox has ambiguous tile ownership involving tile {tile_id}"
            )
        with rasterio.open(path) as dataset:
            _validate_tile_header(dataset, tile_row)
            tile_arrays = dataset.read(
                indexes=list(range(1, len(TILE_BANDS) + 1)),
                window=tile_window,
                boundless=False,
            )
        # Re-check the catalogue identity after reading so a replacement or
        # in-place mutation during the bounded read is also an integrity error.
        catalogue.verified_tile_path(tile_row)
        land_window = land[row0:row1, col0:col1]
        try:
            validate_production_arrays(
                tile_arrays,
                land_window,
                nodata=DEFAULT_NODATA,
                band_names=TILE_BANDS,
                config=catalogue.semantic_config,
            )
        except (TypeError, ValueError) as exc:
            raise CatalogueIntegrityError(
                f"Selected tile {tile_id} semantic integrity validation failed on the read window: {exc}"
            ) from exc
        owner_grid[row0:row1, col0:col1] = tile_info_index
        values[:, row0:row1, col0:col1] = np.asarray(tile_arrays, dtype="float64")

    # The mask identity is checked before and after the bounded read. The
    # second check prevents a changed authoritative mask being published.
    catalogue._verify_mask()

    owned = owner_grid >= 0
    land_owned = land & owned
    uncovered_land = land & ~owned
    road_rail_qualified = np.zeros((height, width), dtype=bool)
    for index, info in enumerate(tile_infos):
        road_rail_qualified[owner_grid == index] = (
            str(info["band_by_name"]["road_rail_upper_db"]["qualification"]) == "qualified"
        )
    road_rail_withheld = land_owned & ~road_rail_qualified
    road_rail_upper = values[TILE_BANDS.index("road_rail_upper_db")]
    if np.any(land_owned & ~np.isfinite(road_rail_upper)):
        raise CatalogueIntegrityError("Selected land cells contain invalid road_rail_upper_db values")
    threshold = float(request["threshold_db"])
    eligible = land_owned & road_rail_qualified & (road_rail_upper <= threshold)

    labels = np.zeros((height, width), dtype=np.int32)
    visited = np.zeros((height, width), dtype=bool)
    components_cells: list[list[tuple[int, int]]] = []
    for start_row in range(height):
        for start_col in range(width):
            if not eligible[start_row, start_col] or visited[start_row, start_col]:
                continue
            label = len(components_cells) + 1
            cells: list[tuple[int, int]] = []
            queue = deque([(start_row, start_col)])
            visited[start_row, start_col] = True
            labels[start_row, start_col] = label
            while queue:
                row, col = queue.popleft()
                cells.append((row, col))
                for next_row, next_col in (
                    (row - 1, col),
                    (row + 1, col),
                    (row, col - 1),
                    (row, col + 1),
                ):
                    if (
                        0 <= next_row < height
                        and 0 <= next_col < width
                        and eligible[next_row, next_col]
                        and not visited[next_row, next_col]
                    ):
                        visited[next_row, next_col] = True
                        labels[next_row, next_col] = label
                        queue.append((next_row, next_col))
            components_cells.append(cells)

    global_row_origin = int(window_info["row_start"])
    global_col_origin = int(window_info["col_start"])
    mask_minx, _, _, mask_maxy = (float(value) for value in header["bounds"])
    requested_west, requested_south, requested_east, requested_north = bbox
    region_transform = Affine(GRID_RESOLUTION_M, 0.0, requested_west, 0.0, -GRID_RESOLUTION_M, requested_north)
    retained_labels = {
        component_label
        for component_label, cells in enumerate(components_cells, start=1)
        if len(cells) >= int(request["minimum_component_cells"])
    }
    component_geometries = _polygonize_labelled_region(labels, retained_labels, region_transform)
    identity_parameters = {
        "catalogue_build_id": str(catalogue.metadata["build_id"]),
        "screening_policy_version": CANDIDATE_SCREENING_POLICY_VERSION,
        "screening_policy_name": CANDIDATE_SCREENING_POLICY_NAME,
        "bbox_bng": list(bbox),
        "road_rail_upper_threshold_db": threshold,
        "minimum_component_cells": int(request["minimum_component_cells"]),
    }
    run_id = "screen-" + _sha256_text(identity_parameters)[:32]

    summaries: list[dict[str, Any]] = []
    excluded_small_cells = 0
    for component_label, cells in enumerate(components_cells, start=1):
        if len(cells) < int(request["minimum_component_cells"]):
            excluded_small_cells += len(cells)
            continue
        global_cells = [
            (global_row_origin + row, global_col_origin + col)
            for row, col in cells
        ]
        global_cells.sort()
        min_global_row = min(row for row, _ in global_cells)
        max_global_row = max(row for row, _ in global_cells)
        min_global_col = min(col for _, col in global_cells)
        max_global_col = max(col for _, col in global_cells)
        component_id_payload = {
            **identity_parameters,
            "global_grid_cell_membership": [[row, col] for row, col in global_cells],
        }
        component_id = "candidate-" + _sha256_text(component_id_payload)[:24]
        upper_values = np.asarray([road_rail_upper[row, col] for row, col in cells], dtype="float64")
        representative_row, representative_col = global_cells[0]
        representative_easting = mask_minx + (representative_col + 0.5) * GRID_RESOLUTION_M
        representative_northing = mask_maxy - (representative_row + 0.5) * GRID_RESOLUTION_M
        airport = _component_airport_summary(cells, owner_grid, tile_infos, values)
        touches_boundary = any(
            row in {0, height - 1} or col in {0, width - 1}
            for row, col in cells
        )
        adjoins_uncovered = False
        adjoins_withheld = False
        for row, col in cells:
            for next_row, next_col in (
                (row - 1, col),
                (row + 1, col),
                (row, col - 1),
                (row, col + 1),
            ):
                if not (0 <= next_row < height and 0 <= next_col < width):
                    continue
                if land[next_row, next_col] and owner_grid[next_row, next_col] < 0:
                    adjoins_uncovered = True
                if land[next_row, next_col] and owner_grid[next_row, next_col] >= 0 and not road_rail_qualified[next_row, next_col]:
                    adjoins_withheld = True
        source_tile_ids = sorted({tile_infos[int(owner_grid[row, col])]["tile_id"] for row, col in cells})
        component = {
            "component_id": component_id,
            "area_order": None,
            "cell_count": len(cells),
            "area_km2": len(cells) * 0.01,
            "bounds_bng": [
                requested_west + min(col for row, col in cells) * GRID_RESOLUTION_M,
                requested_north - (max(row for row, col in cells) + 1) * GRID_RESOLUTION_M,
                requested_west + (max(col for row, col in cells) + 1) * GRID_RESOLUTION_M,
                requested_north - min(row for row, col in cells) * GRID_RESOLUTION_M,
            ],
            "representative_cell": {
                "global_grid_row": representative_row,
                "global_grid_column": representative_col,
                "center_bng": {
                    "easting_m": representative_easting,
                    "northing_m": representative_northing,
                },
            },
            "road_rail_upper_db": {
                "min": float(np.min(upper_values)),
                "max": float(np.max(upper_values)),
                "requested_threshold_db": threshold,
            },
            "touches_requested_bbox_boundary": touches_boundary,
            "adjoins_uncovered_land": adjoins_uncovered,
            "adjoins_road_rail_withheld_land": adjoins_withheld,
            "source_tile_ids": source_tile_ids,
            "airport": airport,
            "geometry": component_geometries[component_label],
        }
        summaries.append(component)

    summaries.sort(
        key=lambda component: (
            -int(component["cell_count"]),
            int(component["representative_cell"]["global_grid_row"]),
            int(component["representative_cell"]["global_grid_column"]),
            str(component["component_id"]),
        )
    )
    for order, component in enumerate(summaries, start=1):
        component["area_order"] = order

    # Recompute the retained membership from the final component IDs without relying
    # on list ordering or a label helper; this keeps the published order independent
    # of flood-fill discovery order.
    retained_mask = np.zeros((height, width), dtype=bool)
    for cells in components_cells:
        if len(cells) >= int(request["minimum_component_cells"]):
            retained_mask[tuple(zip(*cells))] = True
    aggregate_airport = _component_airport_summary(
        list(zip(*np.nonzero(retained_mask))), owner_grid, tile_infos, values
    )
    report = {
        "schema_version": CANDIDATE_SCREENING_SCHEMA_VERSION,
        "record_type": "bounded_road_rail_candidate_screening",
        "run_id": run_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "screening_policy": {
            "name": CANDIDATE_SCREENING_POLICY_NAME,
            "version": CANDIDATE_SCREENING_POLICY_VERSION,
            "connectivity": 4,
            "eligible_land_only": True,
            "threshold_comparison": "road_rail_upper_db <= supplied threshold (inclusive, exact; no tolerance)",
            "withheld_or_uncovered_break_components": True,
        },
        "catalogue": {
            "directory": _relative_or_absolute(catalogue.catalogue_dir),
            "build_id": str(catalogue.metadata["build_id"]),
            "schema_version": catalogue.metadata.get("catalogue_schema_version"),
            "tile_count": int(catalogue.metadata.get("tile_count", 0)),
        },
        "parameters": {
            "bbox_bng": list(bbox),
            "road_rail_upper_threshold_db": threshold,
            "minimum_component_cells": int(request["minimum_component_cells"]),
            "grid_resolution_m": GRID_RESOLUTION_M,
        },
        "selection_window": {
            "shape": [height, width],
            "requested_cells": int(request["requested_cells"]),
            "transform": list(tuple(region_transform)),
            "crs": TARGET_CRS,
            "mask_extent_bng": request["mask_bounds_bng"],
            "mask_identity": {
                "path": _relative_or_absolute(mask_path),
                "file_size": int(mask_path.stat().st_size),
                "sha256": str(catalogue.metadata["inputs"]["land_mask"]["sha256"]),
            },
            "selected_tile_ids": selected_tile_ids,
        },
        "summary": {
            "requested_cells": int(request["requested_cells"]),
            "land_cells": int(np.sum(land)),
            "owned_land_cells": int(np.sum(land_owned)),
            "uncovered_land_cells": int(np.sum(uncovered_land)),
            "road_rail_withheld_or_nonqualified_land_cells": int(np.sum(road_rail_withheld)),
            "road_rail_above_threshold_cells": int(np.sum(land_owned & road_rail_qualified & (road_rail_upper > threshold))),
            "eligible_cells_before_filter": int(np.sum(eligible)),
            "retained_component_count": len(summaries),
            "retained_component_cells": int(sum(component["cell_count"] for component in summaries)),
            "excluded_small_component_cells": int(excluded_small_cells),
            "empty_result": not summaries,
        },
        "airport_summary": aggregate_airport,
        "components": summaries,
        "public_access_status": "not_assessed",
        "historical_acquisition_linkage": "unresolved",
        "reference_years": {
            source: {
                "value": None,
                "status": "not_assessed",
                "provenance": "No provider reference year assigned by bounded candidate screening.",
            }
            for source in SOURCES
        },
        "limitations": [
            "Candidate eligibility is a bounded road/rail upper-bound screen, not an acoustic ranking or public release.",
            "Airport information is reported separately; a zero airport fraction means no reported airport pixels, not no aircraft noise.",
            "Area is calculated from eligible 100 m cell count, not from lon/lat geometry.",
            "Public access has not been assessed and historical acquisition linkage remains unresolved.",
        ],
    }
    return CandidateScreening(report=report, geojson=_build_geojson(report))


def screen_candidates(
    catalogue_dir: str | Path,
    tile_root: str | Path,
    land_mask_path: str | Path,
    bbox_bng: Sequence[Any],
    threshold_db: Any,
    minimum_component_cells: Any,
) -> CandidateScreening:
    """Read and screen one bounded BNG window without publishing files."""
    with DatasetCatalogue(catalogue_dir, tile_root, land_mask_path) as catalogue:
        return _screen_with_catalogue(catalogue, bbox_bng, threshold_db, minimum_component_cells)


def _publication_payload(report: Mapping[str, Any]) -> dict[str, Any]:
    payload = copy.deepcopy(dict(report))
    payload["components"] = [_clean_component(component) for component in payload["components"]]
    return payload


def write_candidate_outputs(
    screening: CandidateScreening,
    output_dir: str | Path,
) -> dict[str, str]:
    """Publish JSON, GeoJSON and Markdown atomically into a new output directory."""
    output = Path(output_dir).resolve()
    _check_output_destination(output)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.mkdir(parents=True, exist_ok=True)
    temp_dir = Path(tempfile.mkdtemp(prefix=f".{output.name}.tmp-", dir=str(output.parent)))
    try:
        report_payload = _publication_payload(screening.report)
        (temp_dir / "candidates.json").write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp_dir / "candidates.geojson").write_text(
            json.dumps(screening.geojson, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (temp_dir / "candidates.md").write_text(_markdown(screening.report), encoding="utf-8")
        # The destination was required to be empty. Replacing that empty
        # directory only after all serialisation succeeds prevents partial
        # published outputs.
        output.rmdir()
        os.replace(temp_dir, output)
    except Exception:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise
    return {
        "json": str(output / "candidates.json"),
        "geojson": str(output / "candidates.geojson"),
        "markdown": str(output / "candidates.md"),
    }


def _check_output_destination(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise CandidateScreeningError(f"Candidate output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Candidate output directory is not empty: {output}")


def extract_candidates(
    catalogue_dir: str | Path,
    tile_root: str | Path,
    land_mask_path: str | Path,
    bbox_bng: Sequence[Any],
    threshold_db: Any,
    minimum_component_cells: Any,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Screen one bounded BNG window and publish the three candidate outputs."""
    output = Path(output_dir).resolve()
    _check_output_destination(output)
    started = time.perf_counter()
    screening = screen_candidates(
        catalogue_dir,
        tile_root,
        land_mask_path,
        bbox_bng,
        threshold_db,
        minimum_component_cells,
    )
    screening.report["processing"] = {
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "read_only": True,
        "published_after_serialization": True,
    }
    paths = write_candidate_outputs(screening, output)
    published_report = _publication_payload(screening.report)
    published_report["output_paths"] = paths
    return published_report


__all__ = [
    "CANDIDATE_SCREENING_POLICY_NAME",
    "CANDIDATE_SCREENING_POLICY_VERSION",
    "CANDIDATE_SCREENING_SCHEMA_VERSION",
    "CandidateScreening",
    "CandidateScreeningError",
    "extract_candidates",
    "read_screening_mask_header",
    "screen_candidates",
    "validate_screening_request",
    "write_candidate_outputs",
]
