#!/usr/bin/env python3
"""Create temporary synthetic inputs for local candidate-viewer verification."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from rasterio.warp import transform


ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "artifacts" / "candidate_screening_pilot_v2"
BNG_CRS = "EPSG:27700"
WGS84_CRS = "EPSG:4326"


def _read_pilot() -> tuple[dict[str, Any], dict[str, Any]]:
    return (
        json.loads((PILOT / "candidates.json").read_text(encoding="utf-8")),
        json.loads((PILOT / "candidates.geojson").read_text(encoding="utf-8")),
    )


def _write(output: Path, report: dict[str, Any], geojson: dict[str, Any]) -> None:
    if output.exists() and any(output.iterdir()):
        raise SystemExit(f"Output directory must be new or empty: {output}")
    output.mkdir(parents=True, exist_ok=True)
    (output / "candidates.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output / "candidates.geojson").write_text(json.dumps(geojson, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _empty(output: Path) -> None:
    report, geojson = _read_pilot()
    report["components"] = []
    report["summary"].update({
        "retained_component_count": 0,
        "retained_component_cells": 0,
        "excluded_small_component_cells": 0,
        "empty_result": True,
    })
    report["airport_summary"]["cell_count"] = 0
    report["airport_summary"]["partition_reconciles_to_component_cells"] = True
    geojson["features"] = []
    _write(output, report, geojson)


def _mismatch(output: Path) -> None:
    report, geojson = _read_pilot()
    geojson["run_id"] = "screen-mismatched-generation"
    _write(output, report, geojson)


def _fragmented(output: Path) -> None:
    report, _ = _read_pilot()
    west, south, east, north = 400000.0, 550000.0, 440000.0, 590000.0
    width = height = 400
    bng_rings: list[list[tuple[float, float]]] = []
    components: list[dict[str, Any]] = []
    features: list[dict[str, Any]] = []
    index = 0
    for row in range(0, height, 2):
        for col in range(0, width, 2):
            x0 = west + col * 100
            x1 = x0 + 100
            y0 = south + row * 100
            y1 = y0 + 100
            bng_rings.append([(x0, y1), (x1, y1), (x1, y0), (x0, y0), (x0, y1)])
            index += 1
            component_id = f"synthetic-{index:05d}"
            components.append({
                "component_id": component_id,
                "area_order": index,
                "cell_count": 1,
                "area_km2": 0.01,
                "bounds_bng": [x0, y0, x1, y1],
                "representative_cell": {
                    "center_bng": {"easting_m": x0 + 50, "northing_m": y0 + 50},
                },
                "road_rail_upper_db": {"min": 40.0, "max": 40.0, "requested_threshold_db": 45.0},
                "touches_requested_bbox_boundary": row in {0, height - 1} or col in {0, width - 1},
                "adjoins_uncovered_land": False,
                "adjoins_road_rail_withheld_land": False,
                "source_tile_ids": ["synthetic"],
                "airport": {
                    "cell_count": 1,
                    "source_quality_state_counts": {"grid_accepted": 1},
                    "fraction_counts": {"zero": 1, "positive": 0, "unavailable": 0},
                    "fraction_availability_by_qualification": {},
                    "lower_bound_availability_by_qualification": {},
                    "partition_reconciles_to_component_cells": True,
                },
            })

    flat_eastings = [point[0] for ring in bng_rings for point in ring]
    flat_northings = [point[1] for ring in bng_rings for point in ring]
    transformed_eastings, transformed_northings = transform(
        BNG_CRS,
        WGS84_CRS,
        flat_eastings,
        flat_northings,
    )
    cursor = 0
    for component, ring in zip(components, bng_rings):
        point_count = len(ring)
        wgs_ring = [
            [transformed_eastings[cursor + offset], transformed_northings[cursor + offset]]
            for offset in range(point_count)
        ]
        cursor += point_count
        features.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [wgs_ring]},
            "properties": {"component_id": component["component_id"]},
        })

    synthetic_run = "screen-synthetic-fragmented"
    report = copy.deepcopy(report)
    report.update({
        "run_id": synthetic_run,
        "components": components,
        "parameters": {
            "bbox_bng": [west, south, east, north],
            "road_rail_upper_threshold_db": 45.0,
            "minimum_component_cells": 1,
            "grid_resolution_m": 100.0,
        },
        "summary": {
            "requested_cells": width * height,
            "land_cells": width * height,
            "owned_land_cells": width * height,
            "uncovered_land_cells": 0,
            "road_rail_withheld_or_nonqualified_land_cells": 0,
            "road_rail_above_threshold_cells": 0,
            "eligible_cells_before_filter": len(components),
            "retained_component_count": len(components),
            "retained_component_cells": len(components),
            "excluded_small_component_cells": 0,
            "empty_result": False,
        },
    })
    report["catalogue"] = {**report["catalogue"], "build_id": "synthetic-fragmented"}
    report["selection_window"] = {
        "shape": [height, width],
        "requested_cells": width * height,
        "transform": [100.0, 0.0, west, 0.0, -100.0, north, 0.0, 0.0, 1.0],
        "crs": BNG_CRS,
        "mask_extent_bng": [west, south, east, north],
        "mask_identity": {"path": "synthetic", "file_size": 0, "sha256": "synthetic"},
        "selected_tile_ids": ["synthetic"],
    }
    report["airport_summary"] = {
        "cell_count": len(components),
        "partition_reconciles_to_component_cells": True,
    }
    geojson = {
        "type": "FeatureCollection",
        "run_id": synthetic_run,
        "schema_version": report["schema_version"],
        "screening_policy_version": report["screening_policy"]["version"],
        "features": features,
    }
    _write(output, report, geojson)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("empty", "mismatch", "fragmented"))
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    if args.mode == "empty":
        _empty(args.output_dir)
    elif args.mode == "mismatch":
        _mismatch(args.output_dir)
    else:
        _fragmented(args.output_dir)
    print(args.output_dir.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
