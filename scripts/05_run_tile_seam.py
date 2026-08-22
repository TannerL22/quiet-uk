"""Run a live 2x2 Heathrow tile seam test and a one-tile reference."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.tiling import (
    make_tiles,
    mosaic_tiles,
    process_tile,
    write_tile_boundary_png,
)


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    extent = tuple(cfg["pilot_bbox_epsg27700"])
    out_dir = ROOT / "data" / "processed" / "tile_validation"
    tile_dir = out_dir / "tiles_5km"
    tile_dir.mkdir(parents=True, exist_ok=True)

    tiles = make_tiles(
        extent,
        tile_size_m=5000,
        source_resolution_m=int(cfg["pilot_resolution_m"]),
        output_resolution_m=int(cfg["output_resolution_m"]),
    )
    if len(tiles) != 4:
        raise SystemExit(f"Expected 2x2 tiles, got {len(tiles)}")

    tile_results = []
    tile_paths = []
    for tile in tiles:
        path = tile_dir / f"{tile.tile_id}.tif"
        tile_results.append(process_tile(tile, cfg, path))
        tile_paths.append(path)

    mosaic_path = out_dir / "mosaic_2x2_100m.tif"
    mosaic_result = mosaic_tiles(tile_paths, mosaic_path, expected_extent=extent)
    png_result = write_tile_boundary_png(
        mosaic_path,
        tile_paths,
        out_dir / "mosaic_2x2_tile_boundaries.png",
        band=1,
    )

    large_tile = make_tiles(
        extent,
        tile_size_m=10000,
        source_resolution_m=int(cfg["pilot_resolution_m"]),
        output_resolution_m=int(cfg["output_resolution_m"]),
    )[0]
    large_path = out_dir / "single_10km_reference.tif"
    large_result = process_tile(large_tile, cfg, large_path)

    with rasterio.open(mosaic_path) as mosaic_ds, rasterio.open(large_path) as reference_ds:
        mosaic = mosaic_ds.read().astype("float64")
        reference = reference_ds.read().astype("float64")
        if mosaic.shape != reference.shape:
            raise SystemExit(f"Mosaic/reference shape mismatch: {mosaic.shape} vs {reference.shape}")
        if str(mosaic_ds.crs) != str(reference_ds.crs):
            raise SystemExit("Mosaic/reference CRS mismatch")
        if tuple(mosaic_ds.transform) != tuple(reference_ds.transform):
            raise SystemExit("Mosaic/reference transform mismatch")
        descriptions = list(mosaic_ds.descriptions)
        if descriptions != list(reference_ds.descriptions):
            raise SystemExit("Mosaic/reference band descriptions mismatch")

        differences = []
        edge_differences = []
        edge_row = mosaic.shape[1] // 2
        edge_col = mosaic.shape[2] // 2
        edge_mask = np.zeros(mosaic.shape[1:], dtype=bool)
        edge_mask[max(0, edge_row - 1):min(mosaic.shape[1], edge_row + 1), :] = True
        edge_mask[:, max(0, edge_col - 1):min(mosaic.shape[2], edge_col + 1)] = True
        for band in range(mosaic.shape[0]):
            valid = (mosaic[band] != mosaic_ds.nodata) & (reference[band] != reference_ds.nodata)
            diff = np.abs(mosaic[band] - reference[band])
            differences.append(float(diff[valid].max()) if valid.any() else 0.0)
            edge_valid = valid & edge_mask
            edge_differences.append(float(diff[edge_valid].max()) if edge_valid.any() else 0.0)

    manifest = {
        "extent_epsg27700": list(extent),
        "tile_size_m": 5000,
        "tile_count": len(tiles),
        "tiles": [result["tile"] for result in tile_results],
        "tile_outputs": [str(path.relative_to(ROOT)) for path in tile_paths],
        "mosaic": mosaic_result,
        "boundary_png": png_result,
        "single_tile_reference": large_result,
        "reference_output": str(large_path.relative_to(ROOT)),
        "max_abs_difference_by_band_db": dict(zip(descriptions, differences)),
        "max_abs_difference_along_shared_edges_db": dict(zip(descriptions, edge_differences)),
        "checks": {
            "is_2x2": len(tiles) == 4,
            "no_gaps": mosaic_result["gap_cells"] == 0,
            "no_overlaps_or_duplicates": mosaic_result["overlap_cells"] == 0,
            "crs_and_transform_match_reference": True,
            "airport_alignment_performed_per_tile": all(
                result["source_info"]["airport"]["alignment"]["performed"]
                for result in tile_results
            ),
            "temporary_10m_discarded": all(
                result["temporary_10m_discarded"] for result in tile_results
            ),
        },
    }
    (out_dir / "seam_test_manifest.json").write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
