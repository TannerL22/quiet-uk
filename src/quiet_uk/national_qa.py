from __future__ import annotations

import csv
import json
import math
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import transform as transform_coords

from .land_mask import read_tile_land_mask
from .tiling import TILE_BANDS, Tile, validate_tile_output


TILE_FILENAME = re.compile(r"^(r\d{4}c\d{4})\.tif$")
ACOUSTIC_BANDS = (
    "combined_reported_lower_db",
    "road_rail_upper_db",
    "airport_reported_lower_db",
)
HISTOGRAM_EDGES_DB = np.array(
    [-np.inf, 20, 30, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 100, np.inf],
    dtype="float64",
)


def tile_from_record(record: dict) -> Tile:
    value = record["tile"]
    return Tile(
        tile_id=value["tile_id"],
        row=int(value["row"]),
        col=int(value["col"]),
        bbox=tuple(float(v) for v in value["bbox_epsg27700"]),
        source_resolution_m=int(value["source_resolution_m"]),
        output_resolution_m=int(value["output_resolution_m"]),
    )


def inventory_issues(expected_ids: set[str], file_names: list[str]) -> dict:
    """Compare regular tile filenames with the deterministic expected IDs."""
    seen: set[str] = set()
    duplicate_ids: list[str] = []
    unexpected: list[str] = []
    regular_ids: list[str] = []
    for name in file_names:
        if name.startswith("."):
            continue
        match = TILE_FILENAME.fullmatch(name)
        if not match:
            unexpected.append(name)
            continue
        tile_id = match.group(1)
        regular_ids.append(tile_id)
        if tile_id in seen:
            duplicate_ids.append(tile_id)
        seen.add(tile_id)
    return {
        "missing_ids": sorted(expected_ids - seen),
        "unexpected_files": sorted(unexpected),
        "duplicate_ids": sorted(set(duplicate_ids)),
        "regular_file_count": len(regular_ids),
    }


def check_tile_layout(tiles: list[Tile], resolution_m: int = 100,
                      tolerance: float = 1e-6) -> dict:
    """Check exact shared edges for neighbouring row/column tile pairs."""
    by_position = {(tile.row, tile.col): tile for tile in tiles}
    horizontal_checks = 0
    vertical_checks = 0
    gaps = 0
    overlaps = 0
    misaligned = 0
    bad_edges: list[dict] = []

    for tile in tiles:
        if any(abs((value / resolution_m) - round(value / resolution_m)) > tolerance
               for value in tile.bbox):
            misaligned += 1
        right = by_position.get((tile.row, tile.col + 1))
        if right is not None:
            horizontal_checks += 1
            delta = right.bbox[0] - tile.bbox[2]
            if abs(delta) > tolerance or abs(right.bbox[1] - tile.bbox[1]) > tolerance or abs(right.bbox[3] - tile.bbox[3]) > tolerance:
                if delta > tolerance:
                    gaps += 1
                elif delta < -tolerance:
                    overlaps += 1
                bad_edges.append({
                    "orientation": "horizontal",
                    "left": tile.tile_id,
                    "right": right.tile_id,
                    "edge_delta_m": delta,
                })
        south = by_position.get((tile.row + 1, tile.col))
        if south is not None:
            vertical_checks += 1
            delta = tile.bbox[1] - south.bbox[3]
            if abs(delta) > tolerance or abs(south.bbox[0] - tile.bbox[0]) > tolerance or abs(south.bbox[2] - tile.bbox[2]) > tolerance:
                if delta > tolerance:
                    gaps += 1
                elif delta < -tolerance:
                    overlaps += 1
                bad_edges.append({
                    "orientation": "vertical",
                    "north": tile.tile_id,
                    "south": south.tile_id,
                    "edge_delta_m": delta,
                })

    return {
        "horizontal_shared_edges": horizontal_checks,
        "vertical_shared_edges": vertical_checks,
        "gap_edges": gaps,
        "overlap_edges": overlaps,
        "misaligned_tiles": misaligned,
        "bad_edges": bad_edges,
    }


def check_band_arrays(arrays: np.ndarray, land: np.ndarray,
                      nodata: float = -9999.0,
                      tolerance: float = 1e-5) -> dict:
    """Return cell-level integrity checks for one four-band tile."""
    arrays = np.asarray(arrays, dtype="float64")
    if arrays.shape[0] != 4:
        raise ValueError("National QA expects four output bands")
    land = np.asarray(land, dtype=bool)
    if arrays.shape[1:] != land.shape:
        raise ValueError("Land mask and tile arrays have different shapes")

    nodata_mask = arrays == nodata
    finite_non_nodata = np.isfinite(arrays) & ~nodata_mask
    outside = ~land
    fraction = arrays[3]
    fraction_valid = finite_non_nodata[3] & land
    combined = arrays[0]
    airport = arrays[2]
    both = finite_non_nodata[0] & finite_non_nodata[2] & land
    acoustic_valid = finite_non_nodata[:3] & land[None, :, :]
    impossible_acoustic = acoustic_valid & ((arrays[:3] < 0.0) | (arrays[:3] > 150.0))
    return {
        "outside_is_nodata": bool(np.all(nodata_mask[:, outside])),
        "outside_non_nodata_cells": int(np.sum(~nodata_mask[:, outside])),
        "fraction_in_range": bool(np.all((fraction[fraction_valid] >= -tolerance) & (fraction[fraction_valid] <= 1 + tolerance))),
        "fraction_invalid_cells": int(np.sum(land & ~fraction_valid)),
        "fraction_zero_with_airport_reported": int(np.sum(land & (fraction == 0.0) & finite_non_nodata[2])),
        "combined_lower_ge_airport_lower": bool(np.all(combined[both] + tolerance >= airport[both])),
        "combined_below_airport_cells": int(np.sum(both & (combined + tolerance < airport))),
        "inf_cells": int(np.sum(~np.isfinite(arrays))),
        "sentinel_in_land_cells": int(np.sum(nodata_mask[:, land])),
        "valid_zero_acoustic_cells": int(np.sum(acoustic_valid & (arrays[:3] == 0.0))),
        "impossible_acoustic_cells": int(np.sum(impossible_acoustic)),
        "road_rail_upper_min": float(np.min(arrays[1][finite_non_nodata[1] & land])) if np.any(finite_non_nodata[1] & land) else None,
    }


def _json_load_with_duplicate_keys(path: Path) -> tuple[dict, list[str]]:
    duplicates: list[str] = []

    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(key)
            result[key] = value
        return result

    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=hook), duplicates


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _safe_float(value):
    return None if value is None else float(value)


def _extreme_record(tile: Tile, arrays: np.ndarray, nodata: float,
                    target_band: str, flat_index: int, direction: str,
                    transform) -> dict:
    row, col = divmod(int(flat_index), arrays.shape[2])
    easting = float(transform.c + (col + 0.5) * transform.a)
    northing = float(transform.f + (row + 0.5) * transform.e)
    longitude, latitude = transform_coords(
        "EPSG:27700", "EPSG:4326", [easting], [northing]
    )
    values = {}
    for index, name in enumerate(TILE_BANDS):
        value = float(arrays[index, row, col])
        values[name] = None if value == nodata or not math.isfinite(value) else value
    target_value = values[target_band]
    return {
        "direction": direction,
        "target_band": target_band,
        "target_value": target_value,
        "tile_id": tile.tile_id,
        "row": row,
        "col": col,
        "easting_m": easting,
        "northing_m": northing,
        "longitude": float(longitude[0]),
        "latitude": float(latitude[0]),
        **values,
    }


def _candidate_indices(values: np.ndarray, valid: np.ndarray, direction: str,
                       limit: int = 20) -> np.ndarray:
    indices = np.flatnonzero(valid)
    if indices.size <= limit:
        return indices
    selected_values = values[indices]
    pivot = limit - 1
    if direction == "low":
        return indices[np.argpartition(selected_values, pivot)[:limit]]
    return indices[np.argpartition(selected_values, -limit)[-limit:]]


def _update_extremes(extremes: dict, tile: Tile, arrays: np.ndarray,
                     land: np.ndarray, nodata: float, transform) -> None:
    definitions = [
        ("combined_reported_lower_db", "low"),
        ("road_rail_upper_db", "low"),
        ("combined_reported_lower_db", "high"),
        ("road_rail_upper_db", "high"),
        ("airport_reported_lower_db", "high"),
    ]
    for target_band, direction in definitions:
        band_index = TILE_BANDS.index(target_band)
        values = arrays[band_index]
        valid = land & np.isfinite(values) & (values != nodata)
        for flat_index in _candidate_indices(values.ravel(), valid.ravel(), direction):
            extremes[(target_band, direction)].append(
                _extreme_record(tile, arrays, nodata, target_band, int(flat_index), direction, transform)
            )


def _finalise_extremes(extremes: dict, limit: int = 20) -> list[dict]:
    rows = []
    for (target_band, direction), candidates in extremes.items():
        reverse = direction == "high"
        ordered = sorted(candidates, key=lambda row: row["target_value"], reverse=reverse)
        for rank, row in enumerate(ordered[:limit], start=1):
            row = dict(row)
            row["rank"] = rank
            if direction == "low":
                row["geographic_plausibility"] = (
                    "Finite reported-energy lower-bound QA point; not evidence of a quiet location."
                )
            elif target_band == "airport_reported_lower_db":
                row["geographic_plausibility"] = (
                    "Consistent with an airport-reported footprint when airport fraction is positive; inspect map."
                ) if (row["airport_reported_fraction"] or 0) > 0 else "Check: high airport band with zero reported fraction."
            elif target_band == "road_rail_upper_db":
                row["geographic_plausibility"] = "Potential major road/rail corridor; inspect map."
            else:
                row["geographic_plausibility"] = "Potential strong reported source area; inspect map."
            rows.append(row)
    return rows


def _error_summary(manifest_records: list[dict], error_log: Path) -> dict:
    manifest_errors = [error for record in manifest_records for error in record.get("errors", [])]
    error_lines = []
    if error_log.exists():
        error_lines = [line.rstrip("\n") for line in error_log.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
    type_counts = Counter()
    http_counts = Counter()
    for item in manifest_errors:
        message = str(item.get("error", ""))
        type_counts[message.split(":", 1)[0]] += 1
        match = re.search(r"\b([45]\d\d) Server Error", message)
        if match:
            http_counts[match.group(1)] += 1
    tile_error_counts = Counter(record["tile"]["tile_id"] for record in manifest_records if record.get("errors"))
    retry_tiles = [record["tile"] for record in manifest_records if int(record.get("attempts", 0)) > 1 or record.get("errors")]
    rows = [int(tile["row"]) for tile in retry_tiles]
    cols = [int(tile["col"]) for tile in retry_tiles]
    return {
        "tiles_requiring_more_than_one_attempt": int(sum(int(record.get("attempts", 0)) > 1 for record in manifest_records)),
        "maximum_attempts": int(max((int(record.get("attempts", 0)) for record in manifest_records), default=0)),
        "manifest_recorded_error_count": len(manifest_errors),
        "tile_errors_log_line_count": len(error_lines),
        "error_type_counts": dict(type_counts),
        "http_status_counts": dict(http_counts),
        "tiles_with_recorded_errors": dict(tile_error_counts),
        "retry_tile_row_range": [min(rows), max(rows)] if rows else None,
        "retry_tile_col_range": [min(cols), max(cols)] if cols else None,
        "retry_tile_row_counts": dict(Counter(rows)),
        "retry_tile_col_counts": dict(Counter(cols)),
        "all_resolved": not any(record.get("status") in {"failed", "pending", "running"} for record in manifest_records),
    }


def _outline_segments(path: Path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    geometry = payload["features"][0]["geometry"]

    def add_geometry(value):
        kind = value["type"]
        coordinates = value["coordinates"]
        if kind == "Polygon":
            for ring in coordinates:
                yield np.asarray(ring, dtype="float64")
        elif kind == "MultiPolygon":
            for polygon in coordinates:
                for ring in polygon:
                    yield np.asarray(ring, dtype="float64")

    return list(add_geometry(geometry))


def _plot_outline(ax, boundary_path: Path) -> None:
    if not boundary_path.exists():
        return
    for segment in _outline_segments(boundary_path):
        if len(segment):
            ax.plot(segment[:, 0], segment[:, 1], color="black", linewidth=0.35, alpha=0.65)


def _save_national_map(display_values: np.ndarray, extent: tuple[float, float, float, float],
                       boundary_path: Path, output: Path, title: str,
                       vmin: float, vmax: float, cmap: str, colorbar_label: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    output.parent.mkdir(parents=True, exist_ok=True)
    masked = np.ma.masked_invalid(display_values)
    fig, ax = plt.subplots(figsize=(9, 9), dpi=140)
    plot_extent = (extent[0], extent[2], extent[1], extent[3])
    image = ax.imshow(masked, origin="upper", extent=plot_extent, vmin=vmin, vmax=vmax, cmap=cmap)
    _plot_outline(ax, boundary_path)
    ax.set_title(title)
    ax.set_xlabel("British National Grid easting (m)")
    ax.set_ylabel("British National Grid northing (m)")
    fig.colorbar(image, ax=ax, shrink=0.78, label=colorbar_label)
    fig.tight_layout()
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _save_histogram_plot(hist_rows: list[dict], output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    bands = [
        "combined_reported_lower_db",
        "road_rail_upper_db",
        "airport_reported_lower_db",
        "airport_reported_fraction",
    ]
    fig, axes = plt.subplots(2, 2, figsize=(11, 8), dpi=140)
    for ax, band in zip(axes.flat, bands):
        rows = [row for row in hist_rows if row["band"] == band]
        labels = [row["bin_label"] for row in rows]
        counts = [int(row["count"]) for row in rows]
        ax.bar(np.arange(len(labels)), counts, color="#35608d", width=0.85)
        ax.set_title(band)
        ax.set_ylabel("England land cells")
        ax.set_yscale("log")
        ax.set_xticks(np.arange(len(labels)))
        ax.set_xticklabels(labels, rotation=70, ha="right", fontsize=7)
        ax.grid(axis="y", alpha=0.25)
    fig.suptitle("Quiet UK England national QA distributions")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _save_retry_map(records: list[dict], extent: tuple[float, float, float, float],
                    boundary_path: Path, output: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import cm, colors
    from matplotlib.patches import Rectangle

    retry_records = [record for record in records if int(record.get("attempts", 0)) > 1 or record.get("errors")]
    max_attempts = max((int(record.get("attempts", 0)) for record in retry_records), default=1)
    norm = colors.Normalize(vmin=1, vmax=max(1, max_attempts))
    cmap = plt.get_cmap("YlOrRd")
    fig, ax = plt.subplots(figsize=(9, 9), dpi=140)
    for record in retry_records:
        tile = tile_from_record(record)
        ax.add_patch(Rectangle(
            (tile.bbox[0], tile.bbox[1]), tile.width_m, tile.height_m,
            facecolor=cmap(norm(int(record.get("attempts", 0)))),
            edgecolor="none", alpha=0.75,
        ))
    _plot_outline(ax, boundary_path)
    ax.set_xlim(extent[0], extent[2])
    ax.set_ylim(extent[1], extent[3])
    ax.set_title(f"Tiles requiring retries/errors (n={len(retry_records)}; colour = attempts)")
    ax.set_xlabel("British National Grid easting (m)")
    ax.set_ylabel("British National Grid northing (m)")
    fig.colorbar(cm.ScalarMappable(norm=norm, cmap=cmap), ax=ax, shrink=0.78, label="Total attempts")
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def _display_accumulate(sums: dict[str, np.ndarray], counts: dict[str, np.ndarray],
                        arrays: np.ndarray, tile: Tile, nodata: float,
                        minx: float, maxy: float, factor: int, display_width: int) -> None:
    global_row0 = int(round((maxy - tile.bbox[3]) / 100.0))
    global_col0 = int(round((tile.bbox[0] - minx) / 100.0))
    rows = (global_row0 + np.arange(arrays.shape[1])) // factor
    cols = (global_col0 + np.arange(arrays.shape[2])) // factor
    indexes = (rows[:, None] * display_width + cols[None, :]).ravel()
    for index, band in ((0, "combined_reported_lower_db"), (1, "road_rail_upper_db"), (3, "airport_reported_fraction")):
        values = arrays[index]
        valid = np.isfinite(values) & (values != nodata)
        flat_valid = valid.ravel()
        if not np.any(flat_valid):
            continue
        sums[band].ravel()[:] += np.bincount(indexes[flat_valid], weights=values.ravel()[flat_valid], minlength=sums[band].size)
        counts[band].ravel()[:] += np.bincount(indexes[flat_valid], minlength=counts[band].size)


def _display_values(sums: dict[str, np.ndarray], counts: dict[str, np.ndarray], band: str) -> np.ndarray:
    result = np.full(sums[band].shape, np.nan, dtype="float64")
    np.divide(sums[band], counts[band], out=result, where=counts[band] > 0)
    return result


def run_national_qa(config: dict, output_root: str | Path,
                    manifest_path: str | Path, qa_root: str | Path,
                    expected_tile_count: int = 1498) -> dict:
    """Run windowed national QA and write machine-readable and visual outputs."""
    output_root = Path(output_root)
    manifest_path = Path(manifest_path)
    qa_root = Path(qa_root)
    qa_root.mkdir(parents=True, exist_ok=True)
    tile_dir = output_root / "tiles"
    mask_path = Path(config["england_mask_100m_path"])
    metadata_path = Path(config["england_mask_metadata_path"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    mask_land_count = int(metadata["england_land_cells_100m"])
    manifest, duplicate_keys = _json_load_with_duplicate_keys(manifest_path)
    manifest_records = list(manifest.get("tiles", {}).values())
    records_by_id = {record["tile"]["tile_id"]: record for record in manifest_records}
    tiles = [tile_from_record(record) for record in manifest_records]

    # Rebuild the deterministic schedule from the official mask, so a complete
    # manifest cannot conceal a missing or extra scheduled tile.
    from .runner import plan_england_tiles
    expected_tiles = plan_england_tiles(config, mask_path)
    expected_ids = {tile.tile_id for tile in expected_tiles}
    regular_files = [path for path in tile_dir.iterdir() if path.is_file() and not path.name.startswith(".") and path.suffix.lower() == ".tif"]
    staged_files = [path.name for path in tile_dir.iterdir() if path.is_file() and path.name.startswith(".") and ".attempt-" in path.name]
    inventory = inventory_issues(expected_ids, [path.name for path in regular_files])
    manifest_id_issues = {
        "missing_manifest_ids": sorted(expected_ids - set(records_by_id)),
        "unexpected_manifest_ids": sorted(set(records_by_id) - expected_ids),
        "duplicate_json_keys": sorted(set(duplicate_keys)),
    }
    layout = check_tile_layout(expected_tiles, int(config.get("output_resolution_m", 100)))

    with rasterio.open(mask_path) as mask_dataset:
        mask_bounds = tuple(mask_dataset.bounds)
        mask_shape = mask_dataset.shape
        actual_mask_land_count = int(np.sum(mask_dataset.read(1) > 0))
    display_factor = max(1, int(math.ceil(max(mask_shape) / 800)))
    display_shape = (
        int(math.ceil(mask_shape[0] / display_factor)),
        int(math.ceil(mask_shape[1] / display_factor)),
    )
    display_bands = ("combined_reported_lower_db", "road_rail_upper_db", "airport_reported_fraction")
    display_sums = {band: np.zeros(display_shape, dtype="float64") for band in display_bands}
    display_counts = {band: np.zeros(display_shape, dtype="int64") for band in display_bands}

    for stale_work_dir in qa_root.glob("national-qa-*"):
        if stale_work_dir.is_dir():
            shutil.rmtree(stale_work_dir, ignore_errors=True)
    work_dir = Path(tempfile.mkdtemp(prefix="national-qa-", dir=str(qa_root)))
    value_maps = {band: np.memmap(work_dir / f"{band}.bin", mode="w+", dtype="float32", shape=(mask_land_count,)) for band in TILE_BANDS}
    value_counts = Counter()
    tile_rows: list[dict] = []
    extremes = {(band, direction): [] for band, direction in (
        ("combined_reported_lower_db", "low"),
        ("road_rail_upper_db", "low"),
        ("combined_reported_lower_db", "high"),
        ("road_rail_upper_db", "high"),
        ("airport_reported_lower_db", "high"),
    )}
    per_band_flags = Counter()

    try:
        for expected_tile in expected_tiles:
            record = records_by_id.get(expected_tile.tile_id)
            path = tile_dir / f"{expected_tile.tile_id}.tif"
            if record is None or not path.exists():
                continue
            validation = validate_tile_output(path, expected_tile, config, record.get("bands") or list(TILE_BANDS))
            with rasterio.open(path) as dataset:
                arrays = dataset.read().astype("float64")
                nodata = float(dataset.nodata if dataset.nodata is not None else -9999.0)
                tile_transform = dataset.transform
                crs = str(dataset.crs)
            land = read_tile_land_mask(mask_path, expected_tile.bbox, expected_tile.output_shape)
            checks = check_band_arrays(arrays, land, nodata)
            if not checks["outside_is_nodata"]:
                per_band_flags["outside_not_nodata"] += 1
            if not checks["fraction_in_range"]:
                per_band_flags["fraction_out_of_range"] += 1
            if checks["fraction_zero_with_airport_reported"]:
                per_band_flags["fraction_zero_with_airport_reported"] += checks["fraction_zero_with_airport_reported"]
            if not checks["combined_lower_ge_airport_lower"]:
                per_band_flags["combined_below_airport"] += 1
            if checks["inf_cells"]:
                per_band_flags["nonfinite_cells"] += checks["inf_cells"]
            if checks["valid_zero_acoustic_cells"]:
                per_band_flags["valid_zero_acoustic_cells"] += checks["valid_zero_acoustic_cells"]
            if checks["impossible_acoustic_cells"]:
                per_band_flags["impossible_acoustic_cells"] += checks["impossible_acoustic_cells"]
            if checks["road_rail_upper_min"] is not None and checks["road_rail_upper_min"] < 43.0102 - 1e-4:
                per_band_flags["road_rail_upper_below_two_40db_sources"] += 1

            record_copy = {
                "tile_id": expected_tile.tile_id,
                "status": record.get("status"),
                "attempts": int(record.get("attempts", 0)),
                "retry_count": int(record.get("retry_count", 0)),
                "error_count": len(record.get("errors", [])),
                "land_cells": int(land.sum()),
                "outside_cells": int((~land).sum()),
                "crs": crs,
                "transform": list(tile_transform),
                "validation_land_cells": validation.get("land_cells"),
                **checks,
            }
            tile_rows.append(record_copy)
            _display_accumulate(display_sums, display_counts, arrays, expected_tile, nodata,
                                mask_bounds[0], mask_bounds[3], display_factor, display_shape[1])
            _update_extremes(extremes, expected_tile, arrays, land, nodata, tile_transform)

            for index, band in enumerate(TILE_BANDS):
                values = arrays[index]
                valid = land & np.isfinite(values) & (values != nodata)
                count = int(valid.sum())
                start = int(value_counts[band])
                value_maps[band][start:start + count] = values[valid].astype("float32")
                value_counts[band] += count
    finally:
        for mmap in value_maps.values():
            mmap.flush()

    statistics_rows = []
    histogram_rows = []
    fraction_rows = []
    for band in TILE_BANDS:
        count = int(value_counts[band])
        values = np.asarray(value_maps[band][:count], dtype="float64")
        if count:
            percentiles = np.percentile(values, [1, 5, 10, 25, 50, 75, 90, 95, 99])
            row = {
                "band": band,
                "valid_cell_count": count,
                "nodata_cell_count_over_england_land": mask_land_count - count,
                "minimum": float(values.min()),
                "p01": float(percentiles[0]),
                "p05": float(percentiles[1]),
                "p10": float(percentiles[2]),
                "p25": float(percentiles[3]),
                "median": float(percentiles[4]),
                "p75": float(percentiles[5]),
                "p90": float(percentiles[6]),
                "p95": float(percentiles[7]),
                "p99": float(percentiles[8]),
                "maximum": float(values.max()),
                "mean": float(values.mean()),
            }
        else:
            row = {"band": band, "valid_cell_count": 0, "nodata_cell_count_over_england_land": mask_land_count,
                   **{key: None for key in ("minimum", "p01", "p05", "p10", "p25", "median", "p75", "p90", "p95", "p99", "maximum", "mean")}}
        statistics_rows.append(row)
        if band in ACOUSTIC_BANDS:
            counts, _ = np.histogram(values, bins=HISTOGRAM_EDGES_DB)
            for index, hist_count in enumerate(counts):
                lower, upper = HISTOGRAM_EDGES_DB[index:index + 2]
                lower_label = "-inf" if not np.isfinite(lower) else f"{lower:g}"
                upper_label = "inf" if not np.isfinite(upper) else f"{upper:g}"
                histogram_rows.append({
                    "band": band, "bin_label": f"[{lower_label},{upper_label})",
                    "lower": None if not np.isfinite(lower) else float(lower),
                    "upper": None if not np.isfinite(upper) else float(upper),
                    "count": int(hist_count),
                })
        else:
            counts, _ = np.histogram(values, bins=np.array([0, 0.25, 0.5, 0.75, 1.0000001]))
            labels = ["[0,0.25)", "[0.25,0.5)", "[0.5,0.75)", "[0.75,1]"]
            for label, hist_count in zip(labels, counts):
                histogram_rows.append({"band": band, "bin_label": label, "lower": None, "upper": None, "count": int(hist_count)})
            for label, predicate in (
                ("zero", values == 0),
                (">0", values > 0),
                (">=0.25", values >= 0.25),
                (">=0.50", values >= 0.50),
                (">=0.75", values >= 0.75),
                ("1.0", values == 1.0),
            ):
                count_value = int(np.sum(predicate))
                fraction_rows.append({"metric": label, "count": count_value, "share": count_value / mask_land_count})

    extremes_rows = _finalise_extremes(extremes)
    error_summary = _error_summary(manifest_records, output_root / "tile_errors.log")
    retry_rows = []
    for record in manifest_records:
        if int(record.get("attempts", 0)) > 1 or record.get("errors"):
            retry_rows.append({
                "tile_id": record["tile"]["tile_id"],
                "row": record["tile"]["row"],
                "col": record["tile"]["col"],
                "easting_min": record["tile"]["bbox_epsg27700"][0],
                "northing_min": record["tile"]["bbox_epsg27700"][1],
                "attempts": record.get("attempts", 0),
                "retry_count": record.get("retry_count", 0),
                "error_count": len(record.get("errors", [])),
                "last_error": record.get("last_error"),
            })

    _write_csv(qa_root / "national_band_statistics.csv", list(statistics_rows[0].keys()), statistics_rows)
    _write_csv(qa_root / "national_histograms.csv", ["band", "bin_label", "lower", "upper", "count"], histogram_rows)
    _write_csv(qa_root / "airport_fraction_summary.csv", ["metric", "count", "share"], fraction_rows)
    _write_csv(qa_root / "national_extremes.csv", [
        "direction", "target_band", "rank", "target_value", "tile_id", "row", "col",
        "easting_m", "northing_m", "longitude", "latitude", *TILE_BANDS,
        "geographic_plausibility",
    ], extremes_rows)
    _write_csv(qa_root / "tile_retry_summary.csv", list(retry_rows[0].keys()) if retry_rows else ["tile_id"], retry_rows)
    _write_csv(qa_root / "tile_qa_summary.csv", list(tile_rows[0].keys()) if tile_rows else ["tile_id"], tile_rows)

    boundary_path = mask_path.parent / "england_boundary_epsg27700.geojson"
    for band, title, vmin, vmax, cmap, label, filename in (
        ("road_rail_upper_db", "England road + rail conservative upper bound", 40, 90, "viridis", "Lden dB", "national_road_rail_upper_db.png"),
        ("combined_reported_lower_db", "England combined reported lower bound", 20, 90, "magma", "reported lower-bound dB", "national_combined_reported_lower_db.png"),
        ("airport_reported_fraction", "England reported airport fraction", 0, 1, "Blues", "fraction of 10 m cells reported", "national_airport_reported_fraction.png"),
    ):
        _save_national_map(_display_values(display_sums, display_counts, band), mask_bounds,
                           boundary_path, qa_root / filename, title, vmin, vmax, cmap, label)
    _save_histogram_plot(histogram_rows, qa_root / "national_distributions.png")
    _save_retry_map(manifest_records, mask_bounds, boundary_path, qa_root / "national_retry_tiles.png")

    checks = {
        "expected_scheduled_tile_count": len(expected_tiles) == expected_tile_count,
        "manifest_scheduled_tile_count": int(manifest.get("tile_count", -1)) == expected_tile_count,
        "all_manifest_tiles_complete": all(record.get("status") == "complete" for record in manifest_records),
        "all_expected_tile_files_present": not inventory["missing_ids"],
        "no_unexpected_tile_files": not inventory["unexpected_files"],
        "no_duplicate_tile_files": not inventory["duplicate_ids"],
        "no_manifest_id_mismatch": not manifest_id_issues["missing_manifest_ids"] and not manifest_id_issues["unexpected_manifest_ids"],
        "no_duplicate_manifest_keys": not manifest_id_issues["duplicate_json_keys"],
        "all_tile_outputs_validate": len(tile_rows) == len(expected_tiles) and all(row["status"] == "complete" for row in tile_rows),
        "no_staged_outputs": not staged_files,
        "no_temporary_10m_directories": not any((output_root / "temporary_10m").iterdir()) if (output_root / "temporary_10m").exists() else True,
        "all_tile_crs_epsg27700": all(row["crs"] == "EPSG:27700" for row in tile_rows),
        "tile_layout_has_no_gaps_or_overlaps": layout["gap_edges"] == 0 and layout["overlap_edges"] == 0 and layout["misaligned_tiles"] == 0,
        "mask_land_count_matches_metadata": actual_mask_land_count == mask_land_count,
        "every_land_cell_reconciles_once": sum(row["land_cells"] for row in tile_rows) == mask_land_count,
        "all_outside_cells_nodata": all(row["outside_is_nodata"] for row in tile_rows),
        "airport_fraction_in_range": per_band_flags["fraction_out_of_range"] == 0,
        "airport_zero_fraction_has_no_reported_energy": per_band_flags["fraction_zero_with_airport_reported"] == 0,
        "combined_lower_ge_airport_lower": per_band_flags["combined_below_airport"] == 0,
        "no_nonfinite_values": per_band_flags["nonfinite_cells"] == 0,
        "no_valid_zero_acoustic_values": per_band_flags["valid_zero_acoustic_cells"] == 0,
        "no_impossible_acoustic_values": per_band_flags["impossible_acoustic_cells"] == 0,
        "road_rail_upper_respects_40db_censor_floor": per_band_flags["road_rail_upper_below_two_40db_sources"] == 0,
        "all_recorded_errors_resolved": error_summary["all_resolved"],
    }
    summary = {
        "qa_version": 1,
        "expected_tile_count": expected_tile_count,
        "manifest_tile_count": int(manifest.get("tile_count", -1)),
        "validated_tile_count": len(tile_rows),
        "tile_inventory": inventory,
        "manifest_id_issues": manifest_id_issues,
        "staged_files": staged_files,
        "layout": layout,
        "mask": {
            "path": str(mask_path),
            "extent_epsg27700": list(mask_bounds),
            "shape": list(mask_shape),
            "metadata_land_cells_100m": mask_land_count,
            "raster_land_cells_100m": actual_mask_land_count,
            "tile_reconciled_land_cells_100m": int(sum(row["land_cells"] for row in tile_rows)),
            "outside_cells_in_scheduled_products": int(sum(row["outside_cells"] for row in tile_rows)),
        },
        "band_statistics_csv": str(qa_root / "national_band_statistics.csv"),
        "fraction_summary_csv": str(qa_root / "airport_fraction_summary.csv"),
        "extremes_csv": str(qa_root / "national_extremes.csv"),
        "retry_error_summary": error_summary,
        "qa_flags": dict(per_band_flags),
        "checks": checks,
        "visuals": {
            "road_rail": str(qa_root / "national_road_rail_upper_db.png"),
            "combined": str(qa_root / "national_combined_reported_lower_db.png"),
            "airport_fraction": str(qa_root / "national_airport_reported_fraction.png"),
            "distributions": str(qa_root / "national_distributions.png"),
            "retry_tiles": str(qa_root / "national_retry_tiles.png"),
        },
    }
    (qa_root / "national_qa_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    shutil.rmtree(work_dir, ignore_errors=True)
    return summary
