from __future__ import annotations
import json
from pathlib import Path
import sys
import warnings
import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import Affine

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.acoustics import combine_censored_sources, aggregate_energy_bounds
from quiet_uk.raster import align_array_to_grid, grids_match, read_single_band_db

cfg = json.loads((ROOT / "config.json").read_text())
pilot = ROOT / "data" / "raw" / "pilot"
levels = {}
profile = None
reference_grid = None
source_stats = {}

for source in ("road", "rail", "airport"):
    path = pilot / f"{source}_pilot.tif"
    if not path.exists():
        continue
    with rasterio.open(path) as ds:
        arr, read_diag = read_single_band_db(ds)
        vals = arr[np.isfinite(arr)]
        raw_grid = {
            "shape": (ds.height, ds.width),
            "crs": str(ds.crs),
            "transform": tuple(ds.transform),
            "bounds": tuple(ds.bounds),
        }
        if reference_grid is None:
            reference_grid = raw_grid
            alignment = {"performed": False, "method": None}
        else:
            if grids_match(raw_grid, reference_grid):
                alignment = {"performed": False, "method": None}
            else:
                arr = align_array_to_grid(arr, raw_grid, reference_grid)
                alignment = {
                    "performed": True,
                    "method": "nearest-neighbour reproject to reference grid",
                    "source_grid": raw_grid,
                    "target_grid": reference_grid,
                }

        levels[source] = arr
        vals = arr[np.isfinite(arr)]
        source_stats[source] = {
            "valid_cells": int(vals.size),
            "min_reported_db": float(vals.min()) if vals.size else None,
            "max_reported_db": float(vals.max()) if vals.size else None,
            "read_diagnostics": read_diag,
            "raw_grid": raw_grid,
            "alignment": alignment,
        }
        if profile is None:
            profile = ds.profile.copy()

if not levels:
    raise SystemExit("No pilot source rasters found.")

thresholds = {}
for source in levels:
    configured = cfg["reporting_threshold_db"].get(source)
    if configured is not None:
        thresholds[source] = float(configured)
    else:
        # Airport thresholds vary by competent authority. For the pilot, use the
        # minimum reported value in the downloaded coverage as an empirical
        # censor ceiling and record it explicitly rather than silently assuming 40.
        m = source_stats[source]["min_reported_db"]
        if m is None:
            raise SystemExit(f"Cannot infer reporting threshold for {source}: no valid cells")
        thresholds[source] = float(m)

combined = combine_censored_sources(levels, thresholds)

reported_max = np.full_like(combined["lower_db"], np.nan, dtype=float)
for arr in levels.values():
    reported_max = np.fmax(reported_max, arr)
reported_mask = np.isfinite(reported_max)
lower_ge_loudest = bool(
    np.all(combined["lower_db"][reported_mask] + 1e-6 >= reported_max[reported_mask])
)
bounds_ordered = bool(
    np.all(combined["upper_db"][np.isfinite(combined["lower_db"])] + 1e-6
           >= combined["lower_db"][np.isfinite(combined["lower_db"])])
)
factor = int(cfg["output_resolution_m"] // cfg["pilot_resolution_m"])
agg = aggregate_energy_bounds(combined["lower_energy"], combined["upper_energy"], factor=factor)

out_dir = ROOT / "data" / "processed" / "pilot"
out_dir.mkdir(parents=True, exist_ok=True)

# 10 m diagnostic bounds
p10 = profile.copy()
p10.update(dtype="float32", count=2, nodata=-9999.0, compress="deflate")
out10 = out_dir / "combined_bounds_10m.tif"
with rasterio.open(out10, "w", **p10) as dst:
    for band, key in enumerate(("lower_db", "upper_db"), start=1):
        a = np.asarray(combined[key], dtype="float32")
        dst.write(np.where(np.isfinite(a), a, -9999.0), band)
        dst.set_band_description(band, key)

# 100 m headline bounds
p100 = profile.copy()
p100.update(
    width=agg["lower_db"].shape[1],
    height=agg["lower_db"].shape[0],
    transform=profile["transform"] * Affine.scale(factor, factor),
    dtype="float32", count=3, nodata=-9999.0, compress="deflate",
)
out100 = out_dir / "combined_bounds_100m.tif"
unc = agg["upper_db"] - agg["lower_db"]
with rasterio.open(out100, "w", **p100) as dst:
    for band, (name, a) in enumerate([
        ("combined_lower_db", agg["lower_db"]),
        ("combined_upper_db", agg["upper_db"]),
        ("uncertainty_db", unc),
    ], start=1):
        a = np.asarray(a, dtype="float32")
        dst.write(np.where(np.isfinite(a), a, -9999.0), band)
        dst.set_band_description(band, name)

# Simple RGB diagnostic: conservative combined upper bound, with censored
# lower-bound-only cells shown as black. This PNG is for visual QA, not GIS use.
upper = agg["upper_db"]
valid = np.isfinite(upper)
lo, hi = 35.0, max(75.0, float(np.nanpercentile(upper[valid], 99.5))) if valid.any() else 75.0
scaled = np.clip((np.nan_to_num(upper, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
r = (255.0 * scaled).astype("uint8")
g = (255.0 * (1.0 - np.abs(scaled - 0.5) * 2.0)).astype("uint8")
b = (255.0 * (1.0 - scaled)).astype("uint8")
r[~valid] = g[~valid] = b[~valid] = 0
png_profile = {
    "driver": "PNG", "width": upper.shape[1], "height": upper.shape[0],
    "count": 3, "dtype": "uint8"
}
out_png = out_dir / "combined_upper_100m_diagnostic.png"
with warnings.catch_warnings():
    warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
    with rasterio.open(out_png, "w", **png_profile) as dst:
        dst.write(r, 1)
        dst.write(g, 2)
        dst.write(b, 3)

manifest = {
    "metric": cfg["metric"],
    "pilot_bbox_epsg27700": cfg["pilot_bbox_epsg27700"],
    "input_resolution_m": cfg["pilot_resolution_m"],
    "output_resolution_m": cfg["output_resolution_m"],
    "thresholds_used_db": thresholds,
    "reference_grid": reference_grid,
    "source_stats": source_stats,
    "checks": {
        "lower_bound_ge_loudest_reported": lower_ge_loudest,
        "upper_bound_ge_lower_bound": bounds_ordered,
        "all_source_grids_aligned": all(
            stat["alignment"]["performed"] or grids_match(stat["raw_grid"], reference_grid)
            for stat in source_stats.values()
        ),
        "zero_values_treated_as_censored": True,
        "aggregation": "arithmetic mean in acoustic-energy space",
    },
    "outputs": [str(out10.relative_to(ROOT)), str(out100.relative_to(ROOT))],
    "diagnostic_png": str(out_png.relative_to(ROOT)),
}
(out_dir / "pilot_manifest.json").write_text(json.dumps(manifest, indent=2))
print(json.dumps(manifest, indent=2))
