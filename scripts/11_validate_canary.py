"""Validate completed canary tiles and write a compact contact-sheet diagnostic."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import warnings

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.land_mask import read_tile_land_mask
from quiet_uk.tiling import TILE_BANDS, Tile, validate_tile_output


def _tile(record):
    value = record["tile"]
    return Tile(
        value["tile_id"], value["row"], value["col"],
        tuple(value["bbox_epsg27700"]), value["source_resolution_m"],
        value["output_resolution_m"],
    )


def _stats(values, nodata):
    valid = np.isfinite(values) & (values != nodata)
    return {
        "valid_cells": int(valid.sum()),
        "min_db_or_value": float(values[valid].min()) if valid.any() else None,
        "max_db_or_value": float(values[valid].max()) if valid.any() else None,
    }


def main() -> None:
    canary = ROOT / "data" / "processed" / "canary"
    selection = json.loads((canary / "canary_selection.json").read_text(encoding="utf-8"))
    manifest = json.loads((canary / "tile_status_manifest.json").read_text(encoding="utf-8"))
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    mask_path = ROOT / config["england_mask_100m_path"]
    labels = {item["tile"]["tile_id"]: item["label"] for item in selection["anchors"]}
    records = []
    for tile_id, record in manifest["tiles"].items():
        if record.get("status") != "complete":
            raise SystemExit(f"Canary tile is not complete: {tile_id} ({record.get('status')})")
        tile = _tile(record)
        validation = validate_tile_output(record["output"], tile, config, record["bands"])
        path = Path(record["output"])
        if not path.is_absolute():
            path = ROOT / path
        with rasterio.open(path) as dataset:
            arrays = dataset.read().astype("float64")
            nodata = dataset.nodata if dataset.nodata is not None else -9999.0
        land = read_tile_land_mask(mask_path, tile.bbox, tile.output_shape)
        outside_is_nodata = bool(np.all(arrays[:, ~land] == nodata))
        fraction = arrays[3]
        fraction_valid = fraction != nodata
        fraction_in_range = bool(np.all((fraction[ fraction_valid] >= 0) & (fraction[fraction_valid] <= 1)))
        combined = arrays[0]
        airport = arrays[2]
        both = (combined != nodata) & (airport != nodata)
        combined_dominates_airport = bool(np.all(combined[both] + 1e-5 >= airport[both]))
        source_info = record.get("source_info", {})
        records.append({
            "tile_id": tile_id,
            "label": labels.get(tile_id),
            "status": record["status"],
            "attempts": record.get("attempts", 0),
            "airport_outside_declared_coverage": bool(
                source_info.get("airport", {}).get("skipped_outside_declared_coverage", False)
            ),
            "airport_reported_fraction_nonzero_cells": int(np.sum(fraction > 0)),
            "land_cells": int(land.sum()),
            "outside_masked_cells": int((~land).sum()),
            "outside_is_nodata": outside_is_nodata,
            "fraction_in_range": fraction_in_range,
            "combined_lower_ge_airport_lower": combined_dominates_airport,
            "bands": {name: _stats(values, nodata) for name, values in zip(TILE_BANDS, arrays)},
            "validation": validation,
        })

    all_stats = {}
    for band_index, name in enumerate(TILE_BANDS):
        vals = []
        for record in records:
            stats = record["bands"][name]
            if stats["min_db_or_value"] is not None:
                vals.extend([stats["min_db_or_value"], stats["max_db_or_value"]])
        all_stats[name] = {
            "min_across_tiles": min(vals) if vals else None,
            "max_across_tiles": max(vals) if vals else None,
        }

    checks = {
        "all_complete": all(record["status"] == "complete" for record in records),
        "all_land_masked_outside": all(record["outside_is_nodata"] for record in records),
        "all_fraction_values_in_range": all(record["fraction_in_range"] for record in records),
        "combined_lower_dominates_airport_lower": all(
            record["combined_lower_ge_airport_lower"] for record in records
        ),
        "temporary_10m_directory_empty": not any((canary / "temporary_10m").iterdir()),
    }
    if not all(checks.values()):
        raise SystemExit(f"Canary validation failed: {checks}")

    # A small contact sheet keeps each geographically separated tile distinct;
    # canary tiles are intentionally not mosaicked across intervening space.
    ordered = [record for item in selection["anchors"]
               for record in records if record["tile_id"] == item["tile"]["tile_id"]]
    thumb = 100
    columns = 4
    rows = int(np.ceil(len(ordered) / columns))
    red = np.zeros((rows * thumb, columns * thumb), dtype="uint8")
    green = np.zeros_like(red)
    blue = np.zeros_like(red)
    all_mins = [record["bands"][TILE_BANDS[0]]["min_db_or_value"] for record in ordered]
    all_maxs = [record["bands"][TILE_BANDS[0]]["max_db_or_value"] for record in ordered]
    lo = min(v for v in all_mins if v is not None)
    hi = max(v for v in all_maxs if v is not None)
    if hi <= lo:
        hi = lo + 1
    for index, item in enumerate(selection["anchors"]):
        record = next(record for record in ordered if record["tile_id"] == item["tile"]["tile_id"])
        path = Path(manifest["tiles"][record["tile_id"]]["output"])
        with rasterio.open(path) as dataset:
            array = dataset.read(1).astype("float64")
            nodata = dataset.nodata if dataset.nodata is not None else -9999.0
        scaled = np.clip((np.nan_to_num(array, nan=lo) - lo) / (hi - lo), 0, 1)
        valid = array != nodata
        r, c = divmod(index, columns)
        red[r * thumb:(r + 1) * thumb, c * thumb:(c + 1) * thumb] = (255 * scaled).astype("uint8")
        green[r * thumb:(r + 1) * thumb, c * thumb:(c + 1) * thumb] = (255 * (1 - scaled)).astype("uint8")
        blue[r * thumb:(r + 1) * thumb, c * thumb:(c + 1) * thumb] = 64
        for channel in (red, green, blue):
            channel[r * thumb:(r + 1) * thumb, c * thumb:(c + 1) * thumb][~valid] = 0
    png_path = canary / "canary_combined_lower_contact_sheet.png"
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
        with rasterio.open(png_path, "w", driver="PNG", width=red.shape[1], height=red.shape[0], count=3, dtype="uint8") as dst:
            dst.write(red, 1)
            dst.write(green, 2)
            dst.write(blue, 3)

    output = {"checks": checks, "tile_count": len(records), "all_band_stats": all_stats,
              "tiles": records, "contact_sheet": str(png_path),
              "contact_sheet_order": [item["label"] for item in selection["anchors"]]}
    (canary / "canary_validation.json").write_text(json.dumps(output, indent=2), encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
