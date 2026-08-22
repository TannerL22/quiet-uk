"""Estimate England-wide rectangular processing scale without downloading data."""
from __future__ import annotations

import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.tiling import make_tiles, snap_extent_to_grid


# Live road/rail WCS DescribeCoverage rectangle. This is a conservative planning
# rectangle, not an England land mask; it includes water and non-England area.
SOURCE_COVERAGE_EXTENT = (82645.0, 5335.0, 655995.0, 657605.0)


def estimate(tile_size_m: int, source_resolution_m: int, output_resolution_m: int) -> dict:
    snapped = snap_extent_to_grid(SOURCE_COVERAGE_EXTENT, output_resolution_m)
    minx, miny, maxx, maxy = snapped
    output_width = int(round((maxx - minx) / output_resolution_m))
    output_height = int(round((maxy - miny) / output_resolution_m))
    tiles = make_tiles(
        SOURCE_COVERAGE_EXTENT,
        tile_size_m=tile_size_m,
        source_resolution_m=source_resolution_m,
        output_resolution_m=output_resolution_m,
    )
    output_cells = output_width * output_height
    source_cells_per_source = output_cells * (output_resolution_m // source_resolution_m) ** 2
    # Three float32 source bands are the conservative raw-data estimate. The
    # actual WCS GeoTIFF byte size depends on service compression/metadata.
    raw_bytes_all_sources = source_cells_per_source * 4 * 3
    final_bytes_4band = output_cells * 4 * 4
    final_bytes_3band = output_cells * 3 * 4
    tile_source_cells = max(tile.source_shape[0] * tile.source_shape[1] for tile in tiles)
    tile_raw_bytes = tile_source_cells * 4 * 3
    pilot_raw_bytes = sum(
        (ROOT / "data" / "raw" / "pilot" / f"{source}_pilot.tif").stat().st_size
        for source in ("road", "rail", "airport")
        if (ROOT / "data" / "raw" / "pilot" / f"{source}_pilot.tif").exists()
    )
    area_scale = (tile_size_m / 10000.0) ** 2
    return {
        "planning_extent_source_coverage_epsg27700": list(SOURCE_COVERAGE_EXTENT),
        "snapped_core_extent_epsg27700": list(snapped),
        "tile_size_m": tile_size_m,
        "source_resolution_m": source_resolution_m,
        "output_resolution_m": output_resolution_m,
        "tile_count": len(tiles),
        "output_shape": [output_height, output_width],
        "expected_100m_cells": output_cells,
        "source_cells_per_source_band": source_cells_per_source,
        "source_cells_all_three_bands": source_cells_per_source * 3,
        "uncompressed_raw_bytes_all_three_sources": raw_bytes_all_sources,
        "uncompressed_final_4band_bytes": final_bytes_4band,
        "uncompressed_final_3band_bytes": final_bytes_3band,
        "approx_peak_temporary_raw_bytes_per_tile": tile_raw_bytes,
        "observed_10km_pilot_raw_bytes_all_three_sources": pilot_raw_bytes,
        "observed_size_scaled_peak_raw_bytes": int(round(pilot_raw_bytes * area_scale)),
        "temporary_raw_policy": "download one tile, validate/process, write 100 m output, delete temporary 10 m files",
        "land_mask_note": "rectangle is an upper-bound planning extent and is not a land-clipped England count",
    }


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    result = {
        "default_10km": estimate(
            int(cfg.get("tile_size_m", 10000)),
            int(cfg["pilot_resolution_m"]),
            int(cfg["output_resolution_m"]),
        ),
        "alternative_20km": estimate(
            20000,
            int(cfg["pilot_resolution_m"]),
            int(cfg["output_resolution_m"]),
        ),
    }
    output = ROOT / "data" / "processed" / "national_scale_estimate.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
