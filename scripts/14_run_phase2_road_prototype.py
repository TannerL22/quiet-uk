"""Run the bounded Phase 2A road-noise prototype.

This downloads only seven 10 km prototype regions.  It never touches the
validated Phase 1 England tiles.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.phase2_road import (  # noqa: E402
    BASE_FEATURES,
    DFT_API_DOCUMENTATION,
    DFT_DOWNLOAD_PAGE,
    DFT_AADF_URL,
    DFT_MRDB_URL,
    DTM_COVERAGE_ID,
    DTM_WCS_URL,
    EA_DTM_2M_WCS_NOTICE,
    EA_DTM_PAGE,
    PROTOTYPE_REGIONS,
    REPORTING_THRESHOLD_DB,
    ROAD_COVERAGE_ID,
    ROAD_WCS_URL,
    TERRAIN_FEATURES,
    build_features,
    download_region_rasters,
    ensure_dft_inputs,
    fit_tobit,
    load_dft_aadf_2025,
    load_dtm,
    load_road_sources,
    metrics_for_predictions,
    predict_tobit,
    standardize_train_test,
    write_csv,
    write_json,
)
from quiet_uk.raster import read_single_band_db  # noqa: E402


MODEL_FEATURES = {
    "distance_only": ("log1p_nearest_distance_m",),
    "traffic_distance": ("log1p_nearest_distance_m", "log10_traffic_energy_10000m"),
    "multi_road_energy": (
        "log10_traffic_energy_250m", "log10_traffic_energy_1000m",
        "log10_hgv_energy_1000m",
    ),
    "road_traffic_base": BASE_FEATURES,
    "road_traffic_terrain": BASE_FEATURES + TERRAIN_FEATURES,
}


def _sample_indices(indices: np.ndarray, count: int, rng: np.random.Generator) -> np.ndarray:
    if len(indices) <= count:
        return indices
    return rng.choice(indices, size=count, replace=False)


def sample_region(region, raster_info, aadf_rows, mrdb_path, max_each, seed):
    with rasterio.open(raster_info["road_path"]) as road_dataset:
        road, road_diag = read_single_band_db(road_dataset)
        road_transform = road_dataset.transform
    dtm, dtm_transform, dtm_diag = load_dtm(raster_info["dtm_path"])
    sources = load_road_sources(aadf_rows, mrdb_path, region.bbox, margin_m=10_500.0)
    finite = np.isfinite(road)
    flat_finite = np.flatnonzero(finite.ravel())
    flat_censored = np.flatnonzero(~finite.ravel())
    rng = np.random.default_rng(seed)
    selected = np.r_[_sample_indices(flat_finite, max_each, rng),
                     _sample_indices(flat_censored, max_each, rng)]
    if len(selected) == 0:
        raise ValueError(f"No road observations or censored cells in {region.region_id}")
    rows, cols = np.unravel_index(selected, road.shape)
    xs = road_transform.c + (cols + 0.5) * road_transform.a
    ys = road_transform.f + (rows + 0.5) * road_transform.e
    features = build_features(xs, ys, sources, dtm, dtm_transform, include_terrain=True)
    numeric_names = sorted(
        name for name, value in features.items()
        if np.asarray(value).dtype.kind in "fc"
    )
    valid = np.ones(len(selected), dtype=bool)
    for name in numeric_names:
        valid &= np.isfinite(np.asarray(features[name], dtype=float))
    y = np.where(finite.ravel()[selected], road.ravel()[selected], REPORTING_THRESHOLD_DB)
    censored = ~finite.ravel()[selected]
    valid &= np.isfinite(y)
    records = []
    for i in np.flatnonzero(valid):
        record = {
            "region_id": region.region_id,
            "region_label": region.label,
            "landscape": region.landscape,
            "x": float(xs[i]),
            "y_coord": float(ys[i]),
            "road_lden_db": float(y[i]),
            "road_censored_below_40": int(censored[i]),
            "nearest_road_class": str(features["nearest_road_class"][i]),
        }
        for name in numeric_names:
            record[name] = float(features[name][i])
        records.append(record)
    return records, {
        "region_id": region.region_id,
        "label": region.label,
        "bbox_epsg27700": list(region.bbox),
        "landscape": region.landscape,
        "source_count": len(sources),
        "major_geometry_sources": int(sum(s["geometry_kind"] == "MRDB_2025_line" for s in sources)),
        "minor_count_point_sources": int(sum(s["geometry_kind"] == "DfT_count_point" for s in sources)),
        "road_raw_valid_cells": int(finite.sum()),
        "road_raw_censored_cells": int((~finite).sum()),
        "road_raw_valid_fraction": float(finite.mean()),
        "sampled_valid_rows": int(len(records)),
        "sampled_reported_rows": int(sum(not row["road_censored_below_40"] for row in records)),
        "sampled_censored_rows": int(sum(row["road_censored_below_40"] for row in records)),
        "road_read_diagnostics": road_diag,
        "dtm_diagnostics": dtm_diag,
    }


def _records_to_arrays(records, feature_names):
    x = np.asarray([[row[name] for name in feature_names] for row in records], dtype=float)
    y = np.asarray([row["road_lden_db"] for row in records], dtype=float)
    censored = np.asarray([bool(row["road_censored_below_40"]) for row in records], dtype=bool)
    region = np.asarray([row["region_id"] for row in records], dtype=object)
    road_class = np.asarray([row["nearest_road_class"] for row in records], dtype=object)
    distance = np.expm1(np.asarray(
        [row["log1p_nearest_distance_m"] for row in records], dtype=float
    ))
    return x, y, censored, region, road_class, distance


def _group_metrics(model_name, fold_name, y, censored, prediction, road_class, distance):
    rows = []
    groups = [("all", np.ones(len(y), dtype=bool))]
    for value in sorted(set(road_class)):
        groups.append((f"road_class:{value}", road_class == value))
    for label, lo, hi in (("distance:0-100m", 0, 100), ("distance:100-500m", 100, 500),
                          ("distance:500-1000m", 500, 1000), ("distance:1000m+", 1000, np.inf)):
        groups.append((label, (distance >= lo) & (distance < hi)))
    for group, mask in groups:
        if not mask.any():
            continue
        row = metrics_for_predictions(y[mask], censored[mask], {
            key: value[mask] for key, value in prediction.items()
        })
        row.update({"model": model_name, "fold": fold_name, "group": group})
        rows.append(row)
    return rows


def fit_and_validate(records, output_root):
    x_by_model = {}
    y, censored, regions, road_class, distance = (None, None, None, None, None)
    all_predictions = {}
    validation_rows = []
    detail_rows = []
    full_models = {}
    for model_name, feature_names in MODEL_FEATURES.items():
        x, y, censored, regions, road_class, distance = _records_to_arrays(records, feature_names)
        model_predictions = []
        fold_rows = []
        for holdout in sorted(set(regions)):
            train = regions != holdout
            test = regions == holdout
            x_train, x_test, mean, scale = standardize_train_test(x[train], x[test])
            model = fit_tobit(x_train, y[train], censored[train], REPORTING_THRESHOLD_DB)
            prediction = predict_tobit(model, x_test)
            fold_metric = metrics_for_predictions(y[test], censored[test], prediction)
            fold_metric.update({"model": model_name, "fold": holdout, "feature_count": len(feature_names)})
            validation_rows.append(fold_metric)
            fold_rows.extend(_group_metrics(model_name, holdout, y[test], censored[test], prediction,
                                             road_class[test], distance[test]))
            for i, global_index in enumerate(np.flatnonzero(test)):
                if i < 500:
                    detail_rows.append({
                        "model": model_name, "fold": holdout, "row_index": int(global_index),
                        "region_id": str(regions[global_index]), "road_class": str(road_class[global_index]),
                        "distance_m": float(distance[global_index]),
                        "reported_lden_db": "" if censored[global_index] else float(y[global_index]),
                        "censored": int(censored[global_index]),
                        "predicted_mu_db": float(prediction["mu_db"][i]),
                        "predicted_p_below_40": float(prediction["probability_below_threshold"][i]),
                    })
            model_predictions.append((test, prediction))
        overall_mu = np.full(len(records), np.nan)
        overall_p = np.full(len(records), np.nan)
        overall_lo = np.full(len(records), np.nan)
        overall_hi = np.full(len(records), np.nan)
        for test, prediction in model_predictions:
            overall_mu[test] = prediction["mu_db"]
            overall_p[test] = prediction["probability_below_threshold"]
            overall_lo[test] = prediction["interval80_low_db"]
            overall_hi[test] = prediction["interval80_high_db"]
        overall_prediction = {
            "mu_db": overall_mu, "probability_below_threshold": overall_p,
            "interval80_low_db": overall_lo, "interval80_high_db": overall_hi,
        }
        overall_metric = metrics_for_predictions(y, censored, overall_prediction)
        overall_metric.update({"model": model_name, "fold": "all_spatial_holdouts", "feature_count": len(feature_names)})
        validation_rows.append(overall_metric)
        validation_rows.extend(fold_rows)
        all_predictions[model_name] = overall_prediction

        x_all, _, mean_all, scale_all = standardize_train_test(x, x)
        full_model = fit_tobit(x_all, y, censored, REPORTING_THRESHOLD_DB)
        full_models[model_name] = {
            **full_model,
            "feature_names": list(feature_names),
            "standardization_mean": mean_all.tolist(),
            "standardization_scale": scale_all.tolist(),
        }

    primary_name = "traffic_distance"
    base = all_predictions["distance_only"]["mu_db"]
    primary = all_predictions[primary_name]["mu_db"]
    terrain = all_predictions["road_traffic_terrain"]["mu_db"]
    disagreement = np.nanstd(np.column_stack([all_predictions[name]["mu_db"] for name in MODEL_FEATURES]), axis=1)
    uncertainty_rows = []
    for i, row in enumerate(records):
        below35 = float(primary[i] < 35.0)
        confidence = "low"
        if below35 and disagreement[i] <= 2.0 and row["log1p_nearest_distance_m"] <= np.log1p(500):
            confidence = "medium"
        if below35 and disagreement[i] <= 1.0 and row["log1p_nearest_distance_m"] <= np.log1p(250):
            confidence = "high"
        uncertainty_rows.append({
            "row_index": i, "region_id": row["region_id"],
            "primary_model": primary_name,
            "primary_model_mu_db": float(primary[i]),
            "terrain_ablation_mu_db": float(terrain[i]),
            "base_model_mu_db": float(base[i]),
            "model_disagreement_sd_db": float(disagreement[i]),
            "probability_below_40": float(all_predictions[primary_name]["probability_below_threshold"][i]),
            "confidence": confidence,
            "road_censored": row["road_censored_below_40"],
        })

    write_csv(output_root / "validation_detail_sample.csv", [
        "model", "fold", "row_index", "region_id", "road_class", "distance_m",
        "reported_lden_db", "censored", "predicted_mu_db", "predicted_p_below_40",
    ], detail_rows)
    write_csv(output_root / "validation_metrics.csv", sorted({key for row in validation_rows for key in row}), validation_rows)
    write_csv(output_root / "uncertainty_sample.csv", list(uncertainty_rows[0]), uncertainty_rows)
    write_json(output_root / "models_full.json", full_models)

    return {
        "validation_rows": validation_rows,
        "detail_rows": detail_rows,
        "uncertainty_rows": uncertainty_rows,
        "full_models": full_models,
        "all_predictions": all_predictions,
    }


def _predict_full(model, features):
    names = model["feature_names"]
    raw = np.column_stack([features[name] for name in names]).astype(float)
    mean = np.asarray(model["standardization_mean"], dtype=float)
    scale = np.asarray(model["standardization_scale"], dtype=float)
    x = np.column_stack([np.ones(len(raw)), (raw - mean) / scale])
    return predict_tobit(model, x)


def make_maps_and_profiles(output_root, raster_inventory, aadf_rows, mrdb_path, full_model):
    plot_regions = [next(r for r in PROTOTYPE_REGIONS if r.region_id == name)
                    for name in ("heathrow_london", "norfolk_flat", "peak_district", "south_downs")]
    fig, axes = plt.subplots(2, 4, figsize=(16, 8), constrained_layout=True)
    for col, region in enumerate(plot_regions):
        info = raster_inventory[region.region_id]
        with rasterio.open(info["road_path"]) as dataset:
            road, _ = read_single_band_db(dataset)
            road_transform = dataset.transform
        dtm, dtm_transform, _ = load_dtm(info["dtm_path"])
        step = 10
        rows, cols = np.indices((100, 100))
        full_rows = rows * step + step // 2
        full_cols = cols * step + step // 2
        xs = road_transform.c + (full_cols.ravel() + 0.5) * road_transform.a
        ys = road_transform.f + (full_rows.ravel() + 0.5) * road_transform.e
        sources = load_road_sources(aadf_rows, mrdb_path, region.bbox, margin_m=10_500.0)
        features = build_features(xs, ys, sources, dtm, dtm_transform, include_terrain=True)
        pred = _predict_full(full_model, features)
        target = road[:1000, :1000].reshape(100, 10, 100, 10)
        target_display = np.full((100, 100), np.nan, dtype=float)
        for rr in range(100):
            for cc in range(100):
                values = target[rr, :, cc, :].ravel()
                values = values[np.isfinite(values)]
                if len(values):
                    target_display[rr, cc] = float(np.median(values))
        target_mask = np.isfinite(target_display)
        target_display = np.ma.masked_where(~target_mask, target_display)
        display_extent = (region.bbox[0], region.bbox[2], region.bbox[1], region.bbox[3])
        axes[0, col].imshow(target_display, origin="upper", extent=display_extent, cmap="inferno", vmin=40, vmax=75)
        axes[0, col].set_title(f"{region.label}\nreported road Lden")
        axes[0, col].set_xlabel("Easting (m)")
        axes[0, col].set_ylabel("Northing (m)")
        axes[1, col].imshow(pred["mu_db"].reshape(100, 100), origin="upper", extent=display_extent, cmap="viridis", vmin=25, vmax=65)
        axes[1, col].set_title("Tobit mean; sub-40 is modelled")
        axes[1, col].set_xlabel("Easting (m)")
        axes[1, col].set_ylabel("Northing (m)")
    fig.suptitle("Phase 2A road-noise prototype: censoring and modelled sub-threshold structure")
    fig.savefig(output_root / "example_maps.png", dpi=150)
    plt.close(fig)

    profile_rows = []
    for region_name in ("heathrow_london", "norfolk_flat"):
        region = next(r for r in PROTOTYPE_REGIONS if r.region_id == region_name)
        info = raster_inventory[region_name]
        with rasterio.open(info["road_path"]) as dataset:
            road, _ = read_single_band_db(dataset)
            transform = dataset.transform
        dtm, dtm_transform, _ = load_dtm(info["dtm_path"])
        sources = load_road_sources(aadf_rows, mrdb_path, region.bbox, margin_m=10_500.0)
        n = 1001
        xs = np.linspace(region.bbox[0], region.bbox[2], n)
        ys = np.full(n, (region.bbox[1] + region.bbox[3]) / 2.0)
        features = build_features(xs, ys, sources, dtm, dtm_transform, include_terrain=True)
        pred = _predict_full(full_model, features)
        col = np.clip(((xs - transform.c) / transform.a).astype(int), 0, road.shape[1] - 1)
        row = np.clip(((transform.f - ys) / abs(transform.e)).astype(int), 0, road.shape[0] - 1)
        observed = road[row, col]
        for i in range(n):
            profile_rows.append({
                "region_id": region_name, "distance_along_profile_m": float(i * 10),
                "easting": float(xs[i]), "northing": float(ys[i]),
                "reported_road_lden_db": "" if not np.isfinite(observed[i]) else float(observed[i]),
                "predicted_mean_db": float(pred["mu_db"][i]),
                "interval80_low_db": float(pred["interval80_low_db"][i]),
                "interval80_high_db": float(pred["interval80_high_db"][i]),
                "probability_below_40": float(pred["probability_below_threshold"][i]),
            })
    write_csv(output_root / "predicted_profiles.csv", list(profile_rows[0]), profile_rows)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=False, constrained_layout=True)
    for ax, region_name in zip(axes, ("heathrow_london", "norfolk_flat")):
        subset = [r for r in profile_rows if r["region_id"] == region_name]
        x = np.asarray([r["distance_along_profile_m"] for r in subset])
        mu = np.asarray([r["predicted_mean_db"] for r in subset])
        lo = np.asarray([r["interval80_low_db"] for r in subset])
        hi = np.asarray([r["interval80_high_db"] for r in subset])
        observed = np.asarray([np.nan if r["reported_road_lden_db"] == "" else r["reported_road_lden_db"] for r in subset], dtype=float)
        ax.fill_between(x, lo, hi, color="tab:blue", alpha=0.18, label="80% model interval")
        ax.plot(x, mu, color="tab:blue", label="Tobit mean")
        ax.scatter(x[np.isfinite(observed)], observed[np.isfinite(observed)], s=3, color="tab:red", label="reported Defra cells")
        ax.axhline(40, color="black", linestyle="--", linewidth=0.8, label="Defra reporting threshold")
        ax.set_title(f"{next(r.label for r in PROTOTYPE_REGIONS if r.region_id == region_name)} profile")
        ax.set_ylabel("Lden (dB)")
        ax.set_ylim(20, max(80, float(np.nanmax(hi)) + 2))
        ax.legend(loc="best", fontsize=8)
    axes[-1].set_xlabel("Profile distance (m)")
    fig.suptitle("Phase 2A predicted road-noise profiles: values below 40 dB are estimates with intervals")
    fig.savefig(output_root / "predicted_profiles.png", dpi=150)
    plt.close(fig)


def make_physical_sanity(output_root, full_model):
    """Check fitted-model response to controlled distance and traffic changes."""
    from rasterio.transform import from_origin
    from shapely.geometry import LineString
    dtm = np.zeros((500, 500), dtype=float)
    transform = from_origin(-2500, 2500, 10, 10)
    distances = np.arange(20.0, 2_001.0, 20.0)
    rows = []
    for flow, hgv in ((1_000.0, 50.0), (10_000.0, 500.0)):
        sources = [{
            "geometry": LineString([(-2_000.0, 0.0), (2_000.0, 0.0)]),
            "flow": flow, "hgv_flow": hgv, "hgv_share": hgv / flow,
            "is_counted": 1.0, "road_class": "a_road",
        }]
        xs = np.zeros(len(distances), dtype=float)
        ys = distances.copy()
        features = build_features(xs, ys, sources, dtm, transform, include_terrain=True)
        prediction = _predict_full(full_model, features)
        for i, distance in enumerate(distances):
            rows.append({
                "flow_aadf": flow, "distance_m": float(distance),
                "predicted_mean_db": float(prediction["mu_db"][i]),
                "probability_below_40": float(prediction["probability_below_threshold"][i]),
            })
    write_csv(output_root / "physical_sanity.csv", list(rows[0]), rows)
    low = np.asarray([r["predicted_mean_db"] for r in rows if r["flow_aadf"] == 1_000.0])
    high = np.asarray([r["predicted_mean_db"] for r in rows if r["flow_aadf"] == 10_000.0])
    within_radius = distances <= 1_000.0
    summary = {
        "higher_flow_non_decrease_fraction": float(np.mean(high >= low - 1e-9)),
        "higher_flow_strict_increase_within_1000m_fraction": float(np.mean(high[within_radius] > low[within_radius] + 1e-6)),
        "lower_flow_distance_decrease_fraction": float(np.mean(np.diff(low) <= 0)),
        "higher_flow_distance_decrease_fraction": float(np.mean(np.diff(high) <= 0)),
        "note": "This is a controlled response check, not an independent validation set.",
    }
    write_json(output_root / "physical_sanity.json", summary)
    fig, ax = plt.subplots(figsize=(9, 5), constrained_layout=True)
    for flow, color in ((1_000.0, "tab:green"), (10_000.0, "tab:red")):
        subset = [r for r in rows if r["flow_aadf"] == flow]
        ax.plot([r["distance_m"] for r in subset], [r["predicted_mean_db"] for r in subset],
                color=color, label=f"AADF {int(flow):,}")
    ax.axhline(40, color="black", linestyle="--", linewidth=0.8, label="40 dB censor threshold")
    ax.set_xlabel("Distance from controlled A-road (m)")
    ax.set_ylabel("Fitted Tobit mean (dB)")
    ax.set_title("Controlled physical-response check")
    ax.legend()
    fig.savefig(output_root / "physical_sanity.png", dpi=150)
    plt.close(fig)
    return summary



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="data/processed/phase2_road_prototype")
    parser.add_argument("--raw-root", default="data/raw/phase2_road")
    parser.add_argument("--max-each", type=int, default=4000,
                        help="Maximum reported and censored receptor cells per region")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    output_root = ROOT / args.output_root
    raw_root = ROOT / args.raw_root
    output_root.mkdir(parents=True, exist_ok=True)

    dft = ensure_dft_inputs(raw_root, timeout=args.timeout)
    aadf_rows = load_dft_aadf_2025(dft["aadf_csv"])
    rasters = download_region_rasters(output_root, PROTOTYPE_REGIONS, timeout=args.timeout, refresh=args.refresh)

    all_records = []
    region_rows = []
    for index, region in enumerate(PROTOTYPE_REGIONS):
        records, info = sample_region(region, rasters[region.region_id], aadf_rows, dft["mrdb_shp"], args.max_each, 202600 + index)
        all_records.extend(records)
        region_rows.append(info)
    feature_names = sorted({key for row in all_records for key in row if key not in {
        "region_id", "region_label", "landscape", "x", "y_coord", "road_lden_db",
        "road_censored_below_40", "nearest_road_class",
    }})
    sample_fields = ["region_id", "region_label", "landscape", "x", "y_coord", "road_lden_db",
                     "road_censored_below_40", "nearest_road_class"] + feature_names
    write_csv(output_root / "receptor_sample.csv", sample_fields, all_records)
    write_csv(output_root / "region_inventory.csv", list(region_rows[0]), region_rows)

    fit = fit_and_validate(all_records, output_root)
    recommended_model_name = "traffic_distance"
    physical_sanity = make_physical_sanity(output_root, fit["full_models"][recommended_model_name])
    make_maps_and_profiles(output_root, rasters, aadf_rows, dft["mrdb_shp"], fit["full_models"][recommended_model_name])

    censored = np.asarray([row["road_censored_below_40"] for row in all_records], dtype=bool)
    terrain_mu = np.asarray([row["terrain_obstruction_max_m"] for row in all_records], dtype=float)
    manifest = {
        "prototype": "Quiet UK Phase 2A sub-threshold road noise",
        "created_at": fit.get("created_at", None) or __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "validated_phase1_untouched": True,
        "regions": [region.__dict__ for region in PROTOTYPE_REGIONS],
        "sample_rows": len(all_records),
        "reported_rows": int((~censored).sum()),
        "censored_rows": int(censored.sum()),
        "censored_fraction": float(censored.mean()),
        "sources": {
            "dft_aadf_2025": {"url": DFT_AADF_URL, "downloads_page": DFT_DOWNLOAD_PAGE, "api_documentation": DFT_API_DOCUMENTATION,
                              "licence": "Open Government Licence v3.0", "rows_loaded": len(aadf_rows)},
            "dft_mrdb_2025": {"url": DFT_MRDB_URL, "downloads_page": DFT_DOWNLOAD_PAGE,
                              "licence": "Open Government Licence v3.0", "geometry_join": "CP_Number to count_point_id"},
            "os_open_roads": {"url": "https://osdatahub.os.uk/downloads/open/OpenRoads", "licence": "Open Government Licence",
                              "status": "investigated; not duplicated because DfT MRDB supplies the traffic-linked major geometries and DfT minor links are retained as point sources"},
            "environment_agency_dtm": {"dataset_page": EA_DTM_PAGE, "wcs_url": DTM_WCS_URL, "coverage_id": DTM_COVERAGE_ID,
                                        "wcs_version": "2.0.1", "licence": "Open Government Licence",
                                        "attribution": "© Environment Agency copyright and/or database right 2022. All rights reserved.",
                                        "status": "official 2 m DTM WCS requested at 10 m output resolution; advertised 10 m composite WCS endpoint returned 404",
                                        "notice": EA_DTM_2M_WCS_NOTICE},
            "defra_road": {"wcs_url": ROAD_WCS_URL, "coverage_id": ROAD_COVERAGE_ID, "wcs_version": "1.0.0",
                           "format": "GeoTIFF", "crs": "EPSG:27700", "reporting_threshold_db": 40.0},
        },
        "feature_definitions": {
            "distance": "nearest source geometry distance, with class-specific nearest distances",
            "traffic_energy": "sum(AADF / max(distance,10m)^2) over DfT sources within radius; log10 transformed",
            "hgv_energy": "same distance-weighted form using all_HGVs",
            "terrain": "receptor elevation, receptor-minus-road elevation, maximum excess above straight line over nine samples, obstruction flag >2m",
            "quality": "nearest source estimation_method Counted vs other; DfT warns individual link estimates are less robust",
        },
        "models": list(MODEL_FEATURES),
        "recommended_model": recommended_model_name,
        "terrain_ablation_model": "road_traffic_terrain",
        "physical_sanity": physical_sanity,
        "outputs": {
            "receptor_sample": str(output_root / "receptor_sample.csv"),
            "region_inventory": str(output_root / "region_inventory.csv"),
            "validation_metrics": str(output_root / "validation_metrics.csv"),
            "models_full": str(output_root / "models_full.json"),
            "uncertainty_sample": str(output_root / "uncertainty_sample.csv"),
            "example_maps": str(output_root / "example_maps.png"),
            "predicted_profiles": str(output_root / "predicted_profiles.png"),
            "physical_sanity": str(output_root / "physical_sanity.png"),
        },
    }
    write_json(output_root / "prototype_manifest.json", manifest)
    print(json.dumps({
        "output_root": str(output_root), "sample_rows": len(all_records),
        "reported_rows": int((~censored).sum()), "censored_rows": int(censored.sum()),
        "regions": len(PROTOTYPE_REGIONS), "models": list(MODEL_FEATURES),
        "terrain_obstruction_finite_fraction": float(np.isfinite(terrain_mu).mean()),
    }, indent=2))


if __name__ == "__main__":
    main()
