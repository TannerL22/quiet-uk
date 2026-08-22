"""Estimate England processing scale from the prepared ONS land mask."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.runner import plan_england_tiles


def main() -> None:
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    mask_path = ROOT / config["england_mask_100m_path"]
    metadata = json.loads((ROOT / config["england_mask_metadata_path"]).read_text(encoding="utf-8"))
    tiles = plan_england_tiles(config, mask_path)
    output_cells = sum(tile.output_shape[0] * tile.output_shape[1] for tile in tiles)
    source_cells = sum(tile.source_shape[0] * tile.source_shape[1] for tile in tiles)
    land_cells = int(metadata["england_land_cells_100m"])
    result = {
        "crs": config.get("crs", "EPSG:27700"),
        "tile_size_m": int(config.get("tile_size_m", 10000)),
        "scheduled_tile_count": len(tiles),
        "scheduled_output_cells_in_tile_products": output_cells,
        "england_land_cells_100m": land_cells,
        "scheduled_source_cells_per_source_band": source_cells,
        "scheduled_source_cells_all_three_sources": source_cells * 3,
        "uncompressed_source_bytes_all_three_core_cells": source_cells * 3 * 4,
        "uncompressed_four_band_scheduled_tile_bytes": output_cells * 4 * 4,
        "uncompressed_four_band_land_cell_bytes": land_cells * 4 * 4,
        "observed_10km_three_source_raw_bytes": 25843534,
        "expected_peak_temporary_raw_bytes_workers_1": 25843534,
        "temporary_policy": "one tile at a time by default; raw source files are deleted after validated 100 m publication",
        "tile_id_first": tiles[0].tile_id,
        "tile_id_last": tiles[-1].tile_id,
        "mask_extent_epsg27700": metadata["mask_extent_epsg27700"],
        "scheduled_tiles": [tile.to_dict() for tile in tiles],
    }
    output = ROOT / "data" / "processed" / "national_scale_estimate_masked.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
