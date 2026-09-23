"""Read-only reconciliation audit for historical source-grid records.

This module deliberately does not import or call the batch runner.  It reads
manifest metadata, reconstructs target grids, and applies the current source
grid contract without downloading, processing, cleaning up, or changing the
historical manifest.
"""
from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import rasterio

from .config import DEFAULT_CRS, DEFAULT_SOURCE_RESOLUTION_M, DEFAULT_WCS_VERSION
from .land_mask import read_tile_land_mask
from .source_grid import (
    AIRPORT_PADDED_GRID_POLICY,
    EXACT_GRID_POLICY,
    GRID_POLICY_VERSION,
    target_pixel_centres_mask,
    validate_source_grid,
)
from .tiling import Tile, tile_grid


SOURCES = ("road", "rail", "airport")
GRID_TOLERANCE = 1e-6
JSON_NAME = "source_grid_reconciliation.json"
CSV_NAME = "source_grid_reconciliation.csv"


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read JSON input {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON input must be an object: {path}")
    return value


def _tile_from_record(tile_id: str, record: Mapping) -> Tile:
    tile = record.get("tile") if isinstance(record.get("tile"), Mapping) else record
    try:
        return Tile(
            tile_id=str(tile["tile_id"]),
            row=int(tile["row"]),
            col=int(tile["col"]),
            bbox=tuple(float(value) for value in tile["bbox_epsg27700"]),
            source_resolution_m=int(tile["source_resolution_m"]),
            output_resolution_m=int(tile["output_resolution_m"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Manifest tile {tile_id} has an invalid tile definition") from exc


def _tile_records(manifest: Mapping) -> list[tuple[str, Mapping]]:
    records = manifest.get("tiles")
    if isinstance(records, Mapping):
        return [(str(tile_id), records[tile_id]) for tile_id in sorted(records, key=str)]
    if isinstance(records, list):
        result = []
        for record in records:
            if not isinstance(record, Mapping):
                raise ValueError("Manifest tile list contains a non-object record")
            tile = record.get("tile") if isinstance(record.get("tile"), Mapping) else record
            result.append((str(tile.get("tile_id")), record))
        return sorted(result, key=lambda item: item[0])
    raise ValueError("Manifest must contain a tiles object or list")


def _finite_tuple(values, length: int) -> tuple[float, ...] | None:
    try:
        result = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return None
    return result if len(result) == length and all(math.isfinite(value) for value in result) else None


def _request_bounds(tile: Tile, source: str, version: str) -> tuple[float, float, float, float]:
    minx, miny, maxx, maxy = tile.bbox
    padding = 1 if source == "airport" and version == "2.0.1" else 0
    cell_x = (maxx - minx) / tile.source_shape[1]
    cell_y = (maxy - miny) / tile.source_shape[0]
    return (
        minx - cell_x * padding / 2.0,
        miny - cell_y * padding / 2.0,
        maxx + cell_x * padding / 2.0,
        maxy + cell_y * padding / 2.0,
    )


def _clip_bounds(bounds, domain):
    if domain is None:
        return None
    return (
        max(bounds[0], domain[0]),
        max(bounds[1], domain[1]),
        min(bounds[2], domain[2]),
        min(bounds[3], domain[3]),
    )


def _bounds_match(left, right) -> bool:
    return left is not None and right is not None and np.allclose(
        left, right, rtol=0.0, atol=GRID_TOLERANCE
    )


def _response_support(target_grid: dict, raw_grid: Mapping) -> tuple[int | None, int | None]:
    """Return target-centre support counts from recorded response bounds."""
    if raw_grid.get("crs") != target_grid.get("crs"):
        return None, None
    bounds = _finite_tuple(raw_grid.get("bounds"), 4)
    if bounds is None:
        return None, None
    try:
        supported = target_pixel_centres_mask(target_grid, bounds)
    except ValueError:
        return None, None
    count = int(supported.sum())
    return count, int(supported.size - count)


def _land_evidence(
    tile: Tile,
    target_grid: dict,
    support_missing_count: int | None,
    support_missing_mask: np.ndarray | None,
    mask_path: Path | None,
) -> dict:
    if mask_path is None or not mask_path.is_file():
        return {
            "england_land_cells_100m": None,
            "missing_support_cells_over_land": None,
            "missing_support_cells_outside_land": None,
            "land_mask_provenance": "unavailable",
        }
    try:
        land = read_tile_land_mask(mask_path, tile.bbox, tile.output_shape)
    except (OSError, ValueError, rasterio.errors.RasterioIOError) as exc:
        return {
            "england_land_cells_100m": None,
            "missing_support_cells_over_land": None,
            "missing_support_cells_outside_land": None,
            "land_mask_provenance": f"unreadable: {exc}",
        }
    evidence = {
        "england_land_cells_100m": int(land.sum()),
        "missing_support_cells_over_land": None,
        "missing_support_cells_outside_land": None,
        "land_mask_provenance": "supplied config land mask; 100 m cells expanded to the 10 m target for coarse evidence",
    }
    if support_missing_mask is None:
        return evidence
    factor_y = target_grid["shape"][0] // tile.output_shape[0]
    factor_x = target_grid["shape"][1] // tile.output_shape[1]
    if factor_y <= 0 or factor_x <= 0 or (
        factor_y * tile.output_shape[0], factor_x * tile.output_shape[1]
    ) != tuple(target_grid["shape"]):
        evidence["land_mask_provenance"] += "; fine-grid expansion unavailable for this tile shape"
        return evidence
    fine_land = np.repeat(np.repeat(land, factor_y, axis=0), factor_x, axis=1)
    if fine_land.shape != support_missing_mask.shape:
        evidence["land_mask_provenance"] += "; fine-grid expansion shape mismatch"
        return evidence
    evidence["missing_support_cells_over_land"] = int((support_missing_mask & fine_land).sum())
    evidence["missing_support_cells_outside_land"] = int((support_missing_mask & ~fine_land).sum())
    return evidence


def _category_from_error(message: str) -> str:
    for category in ("CRS", "dimensions", "resolution", "orientation/rotation", "transform", "bounds"):
        if category in message:
            return category
    return "other"


def _rejection_category(source: str, raw_grid: Mapping, target_grid: dict, message: str) -> str:
    """Prefer the material geometry failure over validator nesting order."""
    category = _category_from_error(message)
    if source != "airport":
        return category
    try:
        raw_shape = tuple(int(value) for value in raw_grid.get("shape", ()))
        target_shape = tuple(int(value) for value in target_grid.get("shape", ()))
    except (TypeError, ValueError):
        return category
    if len(raw_shape) != 2 or len(target_shape) != 2:
        return category
    raw_transform = _finite_tuple(raw_grid.get("transform"), 9)
    target_transform = _finite_tuple(target_grid.get("transform"), 9)
    if (
        raw_shape == (target_shape[0] + 1, target_shape[1] + 1)
        and raw_transform is not None
        and target_transform is not None
        and not np.allclose(
            (raw_transform[0], abs(raw_transform[4])),
            (target_transform[0], abs(target_transform[4])),
            rtol=0.0,
            atol=GRID_TOLERANCE,
        )
    ):
        return "resolution"
    return category


def _geometric_pattern(
    source: str,
    raw_grid: Mapping,
    target_grid: dict,
    tile: Tile,
    version: str,
    declared_bounds,
) -> str:
    """Give each recorded response a deterministic, evidence-scoped geometry label."""
    response_shape = raw_grid.get("shape")
    try:
        response_shape = tuple(int(value) for value in response_shape)
        target_shape = tuple(int(value) for value in target_grid["shape"])
    except (KeyError, TypeError, ValueError):
        return "incomplete-response-geometry"
    response_bounds = _finite_tuple(raw_grid.get("bounds"), 4)
    response_transform = _finite_tuple(raw_grid.get("transform"), 9)
    target_bounds = _finite_tuple(target_grid.get("bounds"), 4)
    target_transform = _finite_tuple(target_grid.get("transform"), 9)
    if any(value is None for value in (response_bounds, response_transform, target_bounds, target_transform)):
        return "incomplete-response-geometry"
    if (
        response_shape == target_shape
        and _bounds_match(response_bounds, target_bounds)
        and np.allclose(response_transform, target_transform, rtol=0.0, atol=GRID_TOLERANCE)
    ):
        return "exact-requested-grid"

    request = _request_bounds(tile, source, version)
    if source == "airport" and response_shape == (target_shape[0] + 1, target_shape[1] + 1):
        resolution = (target_transform[0], abs(target_transform[4]))
        expected_padding_transform = (
            resolution[0], 0.0, request[0],
            0.0, -resolution[1], request[3],
            0.0, 0.0, 1.0,
        )
        if (
            _bounds_match(response_bounds, request)
            and np.allclose(response_transform, expected_padding_transform, rtol=0.0, atol=GRID_TOLERANCE)
        ):
            return "documented-airport-padding-grid"

    edges = []
    if response_bounds[0] > request[0] + GRID_TOLERANCE:
        edges.append("west")
    if response_bounds[1] > request[1] + GRID_TOLERANCE:
        edges.append("south")
    if response_bounds[2] < request[2] - GRID_TOLERANCE:
        edges.append("east")
    if response_bounds[3] < request[3] - GRID_TOLERANCE:
        edges.append("north")
    changed_axes = []
    if not np.isclose(response_transform[0], target_transform[0], rtol=0.0, atol=GRID_TOLERANCE):
        changed_axes.append("x")
    if not np.isclose(abs(response_transform[4]), abs(target_transform[4]), rtol=0.0, atol=GRID_TOLERANCE):
        changed_axes.append("y")
    domain_clipped = False
    clipped = _clip_bounds(request, declared_bounds)
    if clipped is not None and _bounds_match(response_bounds, clipped):
        domain_clipped = True
    extent_kind = "domain-clipped" if domain_clipped else "response-extent-change"
    edge_label = "-".join(edges) if edges else "none"
    resolution_label = "rescaled-" + "-".join(changed_axes) if changed_axes else "resolution-preserved"
    return f"{extent_kind}-edges-{edge_label}-{resolution_label}"


def _source_metadata(config: Mapping, info: Mapping, source: str) -> tuple[str | None, str | None, list[str]]:
    provenance = []
    coverage_id = info.get("coverage_id")
    if coverage_id is None:
        coverage_ids = config.get("coverage_ids", {})
        coverage_id = coverage_ids.get(source) if isinstance(coverage_ids, Mapping) else None
        provenance.append("coverage_id from supplied config; absent from historical source_info")
    else:
        provenance.append("coverage_id from historical source_info")
    version = info.get("wcs_version")
    if version is None:
        version = config.get("wcs_versions", {}).get(source, DEFAULT_WCS_VERSION)
        provenance.append("wcs_version from supplied config; absent from historical source_info")
    else:
        provenance.append("wcs_version from historical source_info")
    return coverage_id, version, provenance


def _classify_rejection(
    source: str,
    raw_grid: Mapping,
    target_grid: dict,
    tile: Tile,
    version: str,
    declared_bounds,
    rejection_category: str,
) -> tuple[str, str, str | None]:
    request = _request_bounds(tile, source, version)
    raw_bounds = _finite_tuple(raw_grid.get("bounds"), 4)
    clipped = _clip_bounds(request, declared_bounds)
    if clipped is not None and _bounds_match(raw_bounds, clipped):
        return (
            "Response consistent with clipping to documented coverage bounds (inference)",
            "C. requires future request-planning change; remains rejected",
            "recorded bounds equal the requested bounds clipped to the configured domain, but service rescaling/value semantics are not proven",
        )
    if source in ("road", "rail") and rejection_category == "resolution":
        return (
            "Unexpected resolution change; response appears clipped/rescaled (inference)",
            "C. requires future request-planning change; remains rejected",
            "road/rail bounds are not declared in the supplied config, so clipping cannot be reconciled to a configured domain",
        )
    return (
        f"Unexpected {rejection_category}",
        "D. insufficient evidence; remains rejected",
        None,
    )


CSV_FIELDS = [
    "tile_id", "source", "coverage_id", "wcs_version", "metadata_provenance",
    "requested_bounds_epsg27700", "requested_shape", "recorded_response_bounds_epsg27700",
    "recorded_response_shape", "recorded_response_transform", "policy_version",
    "policy_acceptance", "policy_name", "rejection_category", "coverage_relationship",
    "geometric_pattern", "resolution_changed", "target_support_cells", "target_cells_without_recorded_support",
    "england_land_cells_100m", "missing_support_cells_over_land",
    "missing_support_cells_outside_land", "historical_alignment_performed",
    "historical_alignment_method", "historical_reprojection_evidence",
    "evidence_based_classification", "proposed_disposition",
]


def _row_for_source(
    tile_id: str,
    record: Mapping,
    tile: Tile,
    source: str,
    config: Mapping,
    manifest: Mapping,
    land_mask_path: Path | None,
) -> dict:
    target_grid = tile_grid(tile, config.get("crs", manifest.get("crs", DEFAULT_CRS)))
    info = record.get("source_info", {}).get(source, {})
    if not isinstance(info, Mapping):
        info = {}
    coverage_id, version, provenance = _source_metadata(config, info, source)
    raw_grid = info.get("raw_grid")
    declared_bounds = config.get("coverage_bounds_epsg27700", {}).get(source)
    requested_bounds = _request_bounds(tile, source, version or DEFAULT_WCS_VERSION)
    base = {
        "tile_id": tile_id,
        "source": source,
        "coverage_id": coverage_id,
        "wcs_version": version,
        "metadata_provenance": "; ".join(provenance),
        "requested_bounds_epsg27700": list(requested_bounds),
        "requested_shape": list(tile.source_shape),
        "recorded_response_bounds_epsg27700": None,
        "recorded_response_shape": None,
        "recorded_response_transform": None,
        "policy_version": GRID_POLICY_VERSION,
        "policy_acceptance": "not_evaluated",
        "policy_name": None,
        "rejection_category": None,
        "coverage_relationship": "not_declared" if declared_bounds is None else "declared_domain_not_compared",
        "geometric_pattern": None,
        "resolution_changed": None,
        "target_support_cells": None,
        "target_cells_without_recorded_support": None,
        "england_land_cells_100m": None,
        "missing_support_cells_over_land": None,
        "missing_support_cells_outside_land": None,
        "historical_alignment_performed": info.get("alignment", {}).get("performed") if isinstance(info.get("alignment"), Mapping) else None,
        "historical_alignment_method": info.get("alignment", {}).get("method") if isinstance(info.get("alignment"), Mapping) else None,
        "historical_reprojection_evidence": "not recorded",
        "evidence_based_classification": None,
        "proposed_disposition": None,
    }
    if raw_grid is None:
        if info.get("skipped_outside_declared_coverage"):
            base.update(
                policy_acceptance="skipped",
                geometric_pattern="skipped-no-raw-grid",
                evidence_based_classification="Deliberately skipped source request",
                proposed_disposition="Retain explicit airport outside-domain skip; no acoustic presence is asserted",
                coverage_relationship="outside declared coverage",
            )
        else:
            base.update(
                policy_acceptance="missing_evidence",
                geometric_pattern="missing-raw-grid-metadata",
                evidence_based_classification="Missing raw-grid metadata",
                proposed_disposition="D. insufficient evidence; remains unsupported",
                coverage_relationship="not available",
            )
        return base

    if not isinstance(raw_grid, Mapping):
        base.update(
            policy_acceptance="missing_evidence",
            rejection_category="raw_grid_record",
            geometric_pattern="incomplete-response-geometry",
            evidence_based_classification="Incomplete evidence",
            proposed_disposition="D. insufficient evidence; remains rejected",
            coverage_relationship="not available",
        )
        return base

    response_bounds = _finite_tuple(raw_grid.get("bounds"), 4)
    response_transform = _finite_tuple(raw_grid.get("transform"), 9)
    response_shape = raw_grid.get("shape")
    base.update(
        recorded_response_bounds_epsg27700=None if response_bounds is None else list(response_bounds),
        recorded_response_shape=None if response_shape is None else list(response_shape),
        recorded_response_transform=None if response_transform is None else list(response_transform),
        resolution_changed=None if response_transform is None else not np.allclose(
            (response_transform[0], abs(response_transform[4])),
            (tile.source_resolution_m, tile.source_resolution_m),
            rtol=0.0,
            atol=GRID_TOLERANCE,
        ),
        geometric_pattern=_geometric_pattern(
            source,
            raw_grid,
            target_grid,
            tile,
            version or DEFAULT_WCS_VERSION,
            declared_bounds,
        ),
    )
    target_support, target_missing = _response_support(target_grid, raw_grid)
    base["target_support_cells"] = target_support
    base["target_cells_without_recorded_support"] = target_missing
    missing_support_mask = None
    if target_support is not None and target_missing:
        supported = target_pixel_centres_mask(target_grid, response_bounds)
        missing_support_mask = ~supported
    land = _land_evidence(tile, target_grid, target_missing, missing_support_mask, land_mask_path)
    base.update(land)

    request = _request_bounds(tile, source, version or DEFAULT_WCS_VERSION)
    clipped = _clip_bounds(request, declared_bounds)
    if declared_bounds is not None and response_bounds is not None:
        if _bounds_match(response_bounds, request):
            base["coverage_relationship"] = "matches_unclipped_request_bounds"
        elif _bounds_match(response_bounds, clipped):
            base["coverage_relationship"] = "matches_request_intersection_with_declared_domain (inference)"
        else:
            base["coverage_relationship"] = "does_not_match_request_or_declared-domain_intersection"
    elif declared_bounds is None:
        base["coverage_relationship"] = "no_declared_domain_in_supplied_config"

    try:
        decision = validate_source_grid(
            source,
            tile_id,
            target_grid,
            dict(raw_grid),
            version or DEFAULT_WCS_VERSION,
            declared_bounds=declared_bounds,
        )
    except ValueError as exc:
        message = str(exc)
        category = _rejection_category(source, raw_grid, target_grid, message)
        classification, disposition, inference = _classify_rejection(
            source, raw_grid, target_grid, tile, version or DEFAULT_WCS_VERSION,
            declared_bounds, category,
        )
        base.update(
            policy_acceptance="rejected",
            policy_name=(
                EXACT_GRID_POLICY["name"]
                if source in ("road", "rail")
                else f"{EXACT_GRID_POLICY['name']} or {AIRPORT_PADDED_GRID_POLICY['name']}"
            ),
            rejection_category=category,
            evidence_based_classification=classification,
            proposed_disposition=disposition,
        )
        if inference:
            base["historical_reprojection_evidence"] = inference
        return base

    policy_name = decision["policy_name"]
    base.update(
        policy_acceptance="accepted",
        policy_name=policy_name,
        evidence_based_classification=(
            "Supported padded airport grid" if policy_name == AIRPORT_PADDED_GRID_POLICY["name"]
            else "Exact requested grid"
        ),
        proposed_disposition=(
            "B. supported through the documented airport padding/alignment policy"
            if decision["alignment_required"]
            else "A. supported without alignment because it satisfies the exact contract"
        ),
    )
    if decision["alignment_required"]:
        base["historical_reprojection_evidence"] = "documented current policy; historical manifest records the earlier alignment method"
    return base


def _json_cell(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def _prepare_output_dir(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"Audit output path is not a directory: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(
            f"Audit output directory is not empty; pass overwrite=True to replace audit outputs: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        for name in (JSON_NAME, CSV_NAME):
            if (output_dir / name).exists():
                raise FileExistsError(f"Audit output already exists: {output_dir / name}")


def audit_manifest(
    manifest_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> dict:
    """Audit a historical manifest into a new, explicitly supplied directory."""
    manifest_path = Path(manifest_path).resolve()
    config_path = Path(config_path).resolve()
    output_dir = Path(output_dir).resolve()
    _prepare_output_dir(output_dir, overwrite)
    manifest = _read_json(manifest_path)
    config = _read_json(config_path)

    configured_crs = config.get("crs")
    manifest_crs = manifest.get("crs")
    mask_value = config.get("england_mask_100m_path")
    mask_path = None
    if mask_value:
        candidate = Path(mask_value)
        mask_path = (config_path.parent / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    rows = []
    counts = {source: Counter() for source in SOURCES}
    classifications = {source: Counter() for source in SOURCES}
    patterns = {source: Counter() for source in SOURCES}
    for tile_id, record in _tile_records(manifest):
        if not isinstance(record, Mapping):
            raise ValueError(f"Manifest tile {tile_id} record is not an object")
        tile = _tile_from_record(tile_id, record)
        for source in SOURCES:
            row = _row_for_source(tile_id, record, tile, source, config, manifest, mask_path)
            rows.append(row)
            counts[source][row["policy_acceptance"]] += 1
            classifications[source][row["evidence_based_classification"]] += 1
            patterns[source][row["geometric_pattern"]] += 1

    summary = {
        "manifest_tile_count": len(_tile_records(manifest)),
        "row_count": len(rows),
        "policy_version": GRID_POLICY_VERSION,
        "source_counts": {source: dict(sorted(counts[source].items())) for source in SOURCES},
        "classification_counts": {
            source: dict(sorted(classifications[source].items())) for source in SOURCES
        },
        "geometric_pattern_counts": {
            source: dict(sorted(patterns[source].items())) for source in SOURCES
        },
        "provenance": {
            "manifest": "historical manifest read-only evidence",
            "configuration": "supplied configuration; historical manifest does not record complete WCS metadata",
            "target_crs": "supplied config" if configured_crs else "manifest/default fallback",
            "land_mask": "supplied config path read-only" if mask_path and mask_path.is_file() else "unavailable",
        },
    }
    report = {
        "audit_schema_version": 2,
        "summary": summary,
        "rows": rows,
    }
    json_path = output_dir / JSON_NAME
    csv_path = output_dir / CSV_NAME
    json_path.write_text(json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n", encoding="utf-8")
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _json_cell(row.get(field)) for field in CSV_FIELDS})
    return {
        "output_dir": str(output_dir),
        "json": str(json_path),
        "csv": str(csv_path),
        "summary": summary,
    }


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit historical Quiet UK source-grid metadata without downloads")
    parser.add_argument("manifest")
    parser.add_argument("config")
    parser.add_argument("output_dir")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = audit_manifest(args.manifest, args.config, args.output_dir, overwrite=args.overwrite)
    print(json.dumps(result, indent=2))
    return 0
