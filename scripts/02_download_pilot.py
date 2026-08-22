from __future__ import annotations
import json
from pathlib import Path
import sys
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.wcs import get_coverage

cfg = json.loads((ROOT / "config.json").read_text())
bbox = tuple(cfg["pilot_bbox_epsg27700"])
res = int(cfg["pilot_resolution_m"])
minx, miny, maxx, maxy = bbox
width = int(round((maxx - minx) / res))
height = int(round((maxy - miny) / res))

out_dir = ROOT / "data" / "raw" / "pilot"
out_dir.mkdir(parents=True, exist_ok=True)

for source, url in cfg["wcs"].items():
    cov = cfg["coverage_ids"].get(source)
    if not cov:
        print(f"Skipping {source}: no coverage ID. Run 01_discover_coverages.py first.")
        continue
    print(f"Downloading {source}: {cov}")
    version = cfg.get("wcs_versions", {}).get(source, "1.0.0")
    format_ = cfg.get("wcs_formats", {}).get(source)
    data = get_coverage(
        url, cov, bbox, width, height, crs=cfg["crs"],
        version=version, format_=format_
    )
    out = out_dir / f"{source}_pilot.tif"
    out.write_bytes(data)
    with rasterio.open(out) as ds:
        a = ds.read(1, masked=True)
        finite = a.compressed()
        print(source, {
            "shape": [ds.height, ds.width],
            "crs": str(ds.crs),
            "bounds": list(ds.bounds),
            "nodata": ds.nodata,
            "valid_cells": int(finite.size),
            "min": float(finite.min()) if finite.size else None,
            "max": float(finite.max()) if finite.size else None,
        })
print("Pilot download complete.")
