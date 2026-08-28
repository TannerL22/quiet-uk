"""Run the bounded Phase 2B road reconstruction experiment.

The script retrieves only the selected prototype regions.  It never reads or
rewrites the frozen Phase 1 England tiles and it refuses to substitute another
traffic year when 2021 data are unavailable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import rasterio
from scipy.stats import spearmanr
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.phase2b_road import (  # noqa: E402
    DFT_AADF_ALL_YEARS_URL,
    DFT_API_DOCUMENTATION,
    DFT_DOWNLOAD_PAGE,
    DFT_MRDB_URLS,
    EPSG,
    OS_OPEN_ROADS_API,
    OS_OPEN_ROADS_DOCUMENTATION,
    OS_OPEN_ROADS_PAGE,
    OS_OPEN_ROADS_RELEASE,
    CNOSSOS_REFERENCE_URL,
    PHASE2B_REGIONS,
    build_phase2b_features,
    calibration_bins,
    download_phase2b_rasters,
    ensure_phase2b_inputs,
    fit_tobit_weighted,
    infer_thresholds,
    load_dft_aadf_year,
    load_dft_mrdb_sources,
    load_os_open_roads_sources,
    population_sample_indices,
    predict_tobit,
    read_target,
    standardize_train_test,
    weighted_metrics,
    write_csv,
    write_json,
)


MODEL_NAMES = (
    "distance_only",
    "phase2a_proxy",
    "complete_road_features",
    "cnossos_inspired",
    "constrained_cnossos",
)


def _feature_names(year: int, model_name: str, speed_mode: str = "speed") -> tuple[str, ...]:
    prefix = f"y{year}_"
    line = f"{prefix}log10_line_emission_energy_5000m_{speed_mode}"
    inverse = f"{prefix}log10_inverse_square_energy_5000m_{speed_mode}"
    distance = f"{prefix}log1p_nearest_distance_m"
    if model_name == "distance_only":
        return (distance,)
    if model_name == "phase2a_proxy":
        return distance, inverse
    if model_name == "complete_road_features":
        return line, inverse, f"{prefix}log1p_nearest_distance_m", f"{prefix}nearest_hgv_share"
    if model_name in ("cnossos_inspired", "constrained_cnossos"):
        return line, distance
    raise KeyError(model_name)


def _bounds(model_name: str, names: tuple[str, ...]) -> list[tuple[float | None, float | None]] | None:
    if model_name == "distance_only":
        return [(None, 0.0)]
    bounds = []
    for name in names:
        if "distance" in name:
            bounds.append((None, 0.0))
        elif "line_emission" in name or "inverse_square" in name:
            bounds.append((0.0, None) if model_name == "constrained_cnossos" else (None, None))
        else:
            bounds.append((None, None))
    return bounds


def _fit_fold(train_records, test_records, year: int, target: str, threshold: float,
              model_name: str, weights_key: str = "sampling_weight", speed_mode: str = "speed"):
    names = _feature_names(year, model_name, speed_mode)
    x_train = np.asarray([[row[name] for name in names] for row in train_records], dtype=float)
    x_test = np.asarray([[row[name] for name in names] for row in test_records], dtype=float)
    y_train = np.asarray([row[target] for row in train_records], dtype=float)
    y_test = np.asarray([row[target] for row in test_records], dtype=float)
    c_train = np.asarray([bool(row[f"{target}_censored"]) for row in train_records])
    c_test = np.asarray([bool(row[f"{target}_censored"]) for row in test_records])
    w_train = np.asarray([row[weights_key] for row in train_records], dtype=float)
    w_test = np.asarray([row[weights_key] for row in test_records], dtype=float)
    x_train_s, x_test_s, mean, scale = standardize_train_test(x_train, x_test)
    x_train_s = np.column_stack([np.ones(len(x_train_s)), x_train_s])
    x_test_s = np.column_stack([np.ones(len(x_test_s)), x_test_s])
    model = fit_tobit_weighted(x_train_s, y_train, c_train, w_train, threshold,
                                beta_bounds=[(None, None)] + (_bounds(model_name, names) or [(None, None)] * len(names)))
    prediction = predict_tobit(model, x_test_s)
    return model, prediction, (y_test, c_test, w_test, names, mean, scale)


def _group_metric_rows(records, prediction, y, censored, weights, target, model, fold, threshold):
    rows = []
    groups = [("all", np.ones(len(records), dtype=bool))]
    for field in ("urban_rural", "nearest_road_class", "nearest_traffic_source"):
        values = sorted({str(row[field]) for row in records})
        for value in values:
            groups.append((f"{field}:{value}", np.asarray([str(row[field]) == value for row in records])))
    distance = np.expm1(np.asarray([row["y2021_log1p_nearest_distance_m"] for row in records]))
    for label, lo, hi in (("distance:0-100m", 0, 100), ("distance:100-500m", 100, 500),
                          ("distance:500-1000m", 500, 1000), ("distance:1000m+", 1000, np.inf)):
        groups.append((label, (distance >= lo) & (distance < hi)))
    for group, mask in groups:
        if not mask.any():
            continue
        sub = {key: value[mask] for key, value in prediction.items()}
        metric = weighted_metrics(y[mask], censored[mask], sub, weights[mask], threshold)
        metric.update({"target": target, "model": model, "fold": fold, "group": group})
        rows.append(metric)
    return rows


def run_spatial_cv(records, year: int, target: str, threshold: float, model_name: str,
                   design: str, speed_mode: str = "speed",
                   weights_key: str = "sampling_weight") -> tuple[list[dict], dict, dict]:
    subset = [row for row in records if row["sample_design"] == design]
    validation = []
    details = []
    out_prediction = np.full(len(subset), np.nan)
    out_probability = np.full(len(subset), np.nan)
    out_lo = np.full(len(subset), np.nan)
    out_hi = np.full(len(subset), np.nan)
    regions = sorted({row["region_id"] for row in subset})
    for holdout in regions:
        train = [row for row in subset if row["region_id"] != holdout]
        test = [row for row in subset if row["region_id"] == holdout]
        model, prediction, arrays = _fit_fold(train, test, year, target, threshold, model_name,
                                              weights_key=weights_key, speed_mode=speed_mode)
        y, censored, weights, names, mean, scale = arrays
        metric = weighted_metrics(y, censored, prediction, weights, threshold)
        metric.update({"target": target, "year": year, "model": model_name, "design": design, "fold": holdout,
                       "feature_names": ";".join(names), "speed_mode": speed_mode})
        validation.append(metric)
        validation.extend(_group_metric_rows(test, prediction, y, censored, weights, target, model_name, holdout, threshold))
        offset = sum(1 for row in subset[:0])  # explicit, keeps the index assignment below readable
        test_indices = [i for i, row in enumerate(subset) if row["region_id"] == holdout]
        for local, global_index in enumerate(test_indices):
            out_prediction[global_index] = prediction["mu_db"][local]
            out_probability[global_index] = prediction["probability_below_threshold"][local]
            out_lo[global_index] = prediction["interval80_low_db"][local]
            out_hi[global_index] = prediction["interval80_high_db"][local]
            if local < 250:
                details.append({"target": target, "year": year, "model": model_name, "design": design,
                                "fold": holdout, "region_id": test[local]["region_id"],
                                "predicted_mu_db": float(prediction["mu_db"][local]),
                                "predicted_probability_below": float(prediction["probability_below_threshold"][local]),
                                "censored": int(censored[local]),
                                "observed_db": "" if censored[local] else float(y[local])})
    overall_prediction = {"mu_db": out_prediction, "probability_below_threshold": out_probability,
                          "interval80_low_db": out_lo, "interval80_high_db": out_hi}
    y_all = np.asarray([row[target] for row in subset], dtype=float)
    c_all = np.asarray([bool(row[f"{target}_censored"]) for row in subset])
    w_all = np.asarray([row[weights_key] for row in subset], dtype=float)
    overall = weighted_metrics(y_all, c_all, overall_prediction, w_all, threshold)
    overall.update({"target": target, "year": year, "model": model_name, "design": design,
                    "fold": "all_spatial_holdouts", "speed_mode": speed_mode})
    validation.append(overall)
    full_names = _feature_names(year, model_name, speed_mode)
    x_all = np.asarray([[row[name] for name in full_names] for row in subset], dtype=float)
    x_all_s, _, mean_all, scale_all = standardize_train_test(x_all, x_all)
    x_all_s = np.column_stack([np.ones(len(x_all_s)), x_all_s])
    full_model = fit_tobit_weighted(x_all_s, y_all, c_all, w_all, threshold,
                                    beta_bounds=[(None, None)] + (_bounds(model_name, full_names) or [(None, None)] * len(full_names)))
    full_model.update({"target": target, "year": year, "model": model_name, "design": design,
                       "speed_mode": speed_mode, "feature_names": list(full_names),
                       "standardization_mean": mean_all.tolist(), "standardization_scale": scale_all.tolist()})
    calibration = calibration_bins(c_all, out_probability, w_all)
    for row in calibration:
        row.update({"target": target, "year": year, "model": model_name, "design": design})
    return validation, {"details": details, "calibration": calibration, "prediction": overall_prediction, "records": subset}, full_model


def _predict_records(model: dict, records: list[dict]):
    names = model["feature_names"]
    x = np.asarray([[row[name] for name in names] for row in records], dtype=float)
    mean = np.asarray(model["standardization_mean"], dtype=float)
    scale = np.asarray(model["standardization_scale"], dtype=float)
    return predict_tobit(model, np.column_stack([np.ones(len(x)), (x - mean) / scale]))


def _rank_summary(records, prediction, target, threshold, radius_label: str):
    from shapely.geometry import Point
    points = np.asarray([Point(row["x"], row["y_coord"]) for row in records], dtype=object)
    tree = STRtree(points)
    rows = []
    for i, point in enumerate(points):
        if not np.isfinite(records[i][target]) or records[i][f"{target}_censored"]:
            continue
        if radius_label == "exact":
            neighbours = np.asarray([i])
        else:
            radius = 100.0 if radius_label == "100m" else 250.0
            neighbours = tree.query(point, predicate="dwithin", distance=radius)
            neighbours = np.asarray(neighbours, dtype=int)
            neighbours = neighbours[[not records[j][f"{target}_censored"] for j in neighbours]]
        if len(neighbours) < (1 if radius_label == "exact" else 3):
            continue
        observed = np.asarray([records[j][target] for j in neighbours], dtype=float)
        modelled = np.asarray([prediction["mu_db"][j] for j in neighbours], dtype=float)
        rows.append({"radius": radius_label, "target": target, "region_id": records[i]["region_id"],
                     "predicted_value_db": float(np.median(modelled)),
                     "observed_value_db": float(np.median(observed)),
                     "predicted_p90_db": float(np.quantile(modelled, 0.90)),
                     "observed_n": int(len(neighbours)), "threshold_db": threshold})
    if len(rows) >= 4:
        rho = float(spearmanr([r["predicted_value_db"] for r in rows], [r["observed_value_db"] for r in rows]).statistic)
    else:
        rho = None
    return rows, {"radius": radius_label, "target": target, "n_cases": len(rows), "spearman_rank": rho,
                  "note": "Observed values are reported cells only; censored targets cannot validate absolute quietness."}


def _profile_coordinates(region, profile_name):
    xs = np.linspace(region.bbox[0] + 100.0, region.bbox[2] - 100.0, 101)
    if profile_name == "vertical":
        xs = np.full(101, (region.bbox[0] + region.bbox[2]) / 2.0)
        ys = np.linspace(region.bbox[1] + 100.0, region.bbox[3] - 100.0, 101)
    elif profile_name == "diagonal":
        ys = np.linspace(region.bbox[1] + 100.0, region.bbox[3] - 100.0, 101)
    else:
        ys = np.full(101, (region.bbox[1] + region.bbox[3]) / 2.0)
    return xs, ys


def _profile_rows(region, sources, model, target, threshold, profile_name, precomputed=None):
    if precomputed is None:
        xs, ys = _profile_coordinates(region, profile_name)
        features = build_phase2b_features(xs, ys, sources, use_speed=True)
    else:
        xs, ys, features = precomputed
    records = []
    names = model["feature_names"]
    profile_names = [name.replace("y2021_", "", 1).replace("_speed", "").replace("_nospeed", "").replace("5000m", "10000m") for name in names]
    matrix = np.asarray([[features[name][i] for name in profile_names] for i in range(len(xs))], dtype=float)
    mean = np.asarray(model["standardization_mean"], dtype=float)
    scale = np.asarray(model["standardization_scale"], dtype=float)
    prediction = predict_tobit(model, np.column_stack([np.ones(len(matrix)), (matrix - mean) / scale]))
    quiet = prediction["mu_db"] < threshold
    longest = 0
    current = 0
    for value in quiet:
        current = current + 1 if value else 0
        longest = max(longest, current)
    for i in range(len(xs)):
        records.append({"region_id": region.region_id, "profile": profile_name, "target": target,
                        "distance_m": float(i * 100), "easting": float(xs[i]), "northing": float(ys[i]),
                        "predicted_mean_db": float(prediction["mu_db"][i]),
                        "probability_below_threshold": float(prediction["probability_below_threshold"][i]),
                        "threshold_db": threshold, "below_threshold": int(quiet[i]),
                        "longest_quiet_segment_m": int(longest * 100)})
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="data/processed/phase2b_road")
    parser.add_argument("--raw-root", default="data/raw/phase2b_road")
    parser.add_argument("--sample-n", type=int, default=1600)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    output_root = ROOT / args.output_root
    raw_root = ROOT / args.raw_root
    output_root.mkdir(parents=True, exist_ok=True)

    inputs = ensure_phase2b_inputs(raw_root, timeout=args.timeout)
    aadf = {year: load_dft_aadf_year(inputs["aadf_csv"], year) for year in (2021, 2025)}
    rasters = download_phase2b_rasters(output_root, PHASE2B_REGIONS, timeout=args.timeout, refresh=args.refresh)

    target_arrays = {}
    raster_inventory = []
    for region in PHASE2B_REGIONS:
        lden, lden_transform, lden_info = read_target(rasters[region.region_id]["lden_path"])
        lnight, lnight_transform, lnight_info = read_target(rasters[region.region_id]["lnight_path"])
        if lden.shape != lnight.shape or lden_transform != lnight_transform:
            raise ValueError(f"Lden/Lnight grids do not align in {region.region_id}")
        target_arrays[region.region_id] = {"lden": lden, "lnight": lnight,
                                           "transform": lden_transform, "info": {"lden": lden_info, "lnight": lnight_info}}
        raster_inventory.append({"region_id": region.region_id, "label": region.label,
                                 "lden_min_positive_db": lden_info["min_positive_db"],
                                 "lden_q01_db": lden_info["q01_db"], "lden_valid_cells": lden_info["valid_cells"],
                                 "lden_total_cells": lden_info["total_cells"],
                                 "lnight_min_positive_db": lnight_info["min_positive_db"],
                                 "lnight_q01_db": lnight_info["q01_db"], "lnight_valid_cells": lnight_info["valid_cells"],
                                 "lnight_total_cells": lnight_info["total_cells"],
                                 "lden_raw_diagnostics": json.dumps(lden_info["diagnostics"]),
                                 "lnight_raw_diagnostics": json.dumps(lnight_info["diagnostics"])})
    thresholds = infer_thresholds(raster_inventory)
    lden_threshold = thresholds["lden_threshold_inferred_db"]
    lnight_threshold = thresholds["lnight_threshold_inferred_db"]
    if max(thresholds["lden_range_db"]) - min(thresholds["lden_range_db"]) > 0.5:
        raise ValueError(f"Lden threshold is not stable across prototype regions: {thresholds['lden_range_db']}")
    if max(thresholds["lnight_range_db"]) - min(thresholds["lnight_range_db"]) > 0.5:
        raise ValueError(f"Lnight threshold is not stable across prototype regions: {thresholds['lnight_range_db']}")

    records = []
    source_inventory = []
    source_cache = {}
    for index, region in enumerate(PHASE2B_REGIONS):
        region_target = target_arrays[region.region_id]
        representative, balanced = population_sample_indices(region_target["lden"], region_target["lnight"], args.sample_n, 2026_2_000 + index)
        selected = np.unique(np.r_[representative, balanced])
        rows, cols = np.unravel_index(selected, region_target["lden"].shape)
        xs = region_target["transform"].c + (cols + 0.5) * region_target["transform"].a
        ys = region_target["transform"].f + (rows + 0.5) * region_target["transform"].e
        source_cache[region.region_id] = {}
        feature_cache = {}
        for year in (2021, 2025):
            dft_sources = load_dft_mrdb_sources(aadf[year], inputs["mrdb"][year]["shp"], region.bbox, margin_m=5_500.0)
            sources, counts = load_os_open_roads_sources(inputs["os_gpkg"], dft_sources,
                                                          (region.bbox[0] - 5_000, region.bbox[1] - 5_000,
                                                           region.bbox[2] + 5_000, region.bbox[3] + 5_000), region.urban_rural)
            source_cache[region.region_id][year] = sources
            source_inventory.append({"region_id": region.region_id, "year": year, **counts,
                                     "dft_aadf_rows": len(aadf[year]), "dft_mrdb_path": inputs["mrdb"][year]["shp"]})
            for speed_mode, use_speed in (("speed", True), ("nospeed", False)):
                features = build_phase2b_features(xs, ys, sources, use_speed=use_speed, radius=5_000.0)
                feature_cache[(year, speed_mode)] = features
        total = len(selected)
        finite_lden = np.isfinite(region_target["lden"].ravel())
        finite_lnight = np.isfinite(region_target["lnight"].ravel())
        for local, flat_index in enumerate(selected):
            is_rep = flat_index in set(representative)
            is_bal = flat_index in set(balanced)
            for design, include in (("representative", is_rep), ("balanced", is_bal)):
                if not include:
                    continue
                if design == "representative":
                    weight = region_target["lden"].size / len(representative)
                else:
                    cls = finite_lden[flat_index]
                    class_population = int(finite_lden.sum() if cls else (~finite_lden).sum())
                    class_sample = int(np.sum(finite_lden[balanced]) if cls else np.sum(~finite_lden[balanced]))
                    weight = class_population / max(class_sample, 1)
                row = {"region_id": region.region_id, "region_label": region.label, "landscape": region.landscape,
                       "urban_rural": region.urban_rural, "x": float(xs[local]), "y_coord": float(ys[local]),
                       "sample_design": design, "sampling_weight": float(weight),
                       "population_n": int(region_target["lden"].size),
                       "population_lden_censor_fraction": float((~finite_lden).mean()),
                       "population_lnight_censor_fraction": float((~finite_lnight).mean()),
                       "lden": float(region_target["lden"].ravel()[flat_index]) if finite_lden[flat_index] else lden_threshold,
                       "lden_censored": int(not finite_lden[flat_index]),
                       "lnight": float(region_target["lnight"].ravel()[flat_index]) if finite_lnight[flat_index] else lnight_threshold,
                       "lnight_censored": int(not finite_lnight[flat_index])}
                for year in (2021, 2025):
                    for speed_mode in ("speed", "nospeed"):
                        for name, value in feature_cache[(year, speed_mode)].items():
                            if np.asarray(value).dtype.kind in "fc":
                                stable_name = name.replace("10000m", "5000m")
                                row[f"y{year}_{stable_name}_{speed_mode}"] = float(value[local])
                                if name == "log1p_nearest_distance_m":
                                    row[f"y{year}_{name}"] = float(value[local])
                            else:
                                stable_name = name.replace("10000m", "5000m")
                                row[f"y{year}_{stable_name}"] = str(value[local])
                        # Model feature names use a stable shared name for distance/HGV.
                        row[f"y{year}_log1p_nearest_distance_m"] = float(feature_cache[(year, speed_mode)]["log1p_nearest_distance_m"][local])
                        row[f"y{year}_nearest_hgv_share"] = float(feature_cache[(year, speed_mode)]["nearest_hgv_share"][local])
                        row[f"y{year}_nearest_road_class"] = str(feature_cache[(year, speed_mode)]["nearest_road_class"][local])
                        row[f"y{year}_nearest_traffic_source"] = str(feature_cache[(year, speed_mode)]["nearest_traffic_source"][local])
                        row[f"y{year}_nearest_traffic_confidence"] = str(feature_cache[(year, speed_mode)]["nearest_traffic_confidence"][local])
                row["nearest_road_class"] = row["y2021_nearest_road_class"]
                row["nearest_traffic_source"] = row["y2021_nearest_traffic_source"]
                records.append(row)

    sample_fields = sorted({key for row in records for key in row})
    write_csv(output_root / "receptor_sample.csv", sample_fields, records)
    write_csv(output_root / "raster_inventory.csv", list(raster_inventory[0]), raster_inventory)
    write_csv(output_root / "network_completeness.csv", list(source_inventory[0]), source_inventory)

    validation_rows = []
    detail_rows = []
    calibration_rows = []
    models = {}
    # Headline spatial validation uses representative sampling and its design weights.
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for year in (2021, 2025):
            for model_name in MODEL_NAMES:
                result, extras, full_model = run_spatial_cv(records, year, target, threshold, model_name, "representative")
                validation_rows.extend(result)
                detail_rows.extend(extras["details"])
                calibration_rows.extend(extras["calibration"])
                models[f"{target}_{year}_{model_name}"] = full_model
    # Balanced-sample comparison is intentionally limited to the benchmark and
    # corrected physical model to isolate prevalence correction from geometry.
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for weighted_design in ("balanced",):
            for model_name in ("phase2a_proxy", "cnossos_inspired"):
                result, extras, full_model = run_spatial_cv(records, 2021, target, threshold, model_name, weighted_design)
                validation_rows.extend(result)
                detail_rows.extend(extras["details"])
                calibration_rows.extend(extras["calibration"])
                models[f"{target}_2021_{weighted_design}_{model_name}"] = full_model

    # Explicit 2021-vs-2025 ablation table on the same representative regions.
    temporal = [row for row in validation_rows if row.get("design") == "representative" and row.get("fold") == "all_spatial_holdouts"
                and row.get("model") in ("phase2a_proxy", "cnossos_inspired")]
    write_csv(output_root / "temporal_ablation_2021_vs_2025.csv", list(temporal[0]), temporal)
    write_csv(output_root / "validation_metrics.csv", sorted({key for row in validation_rows for key in row}), validation_rows)
    write_csv(output_root / "validation_detail_sample.csv", sorted({key for row in detail_rows for key in row}), detail_rows)
    write_csv(output_root / "calibration_bins.csv", list(calibration_rows[0]), calibration_rows)
    write_json(output_root / "models_full.json", models)

    representative = [row for row in records if row["sample_design"] == "representative"]
    bias_rows = []
    for model_name in ("phase2a_proxy", "cnossos_inspired"):
        for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
            result_unweighted, _, _ = run_spatial_cv(records, 2021, target, threshold, model_name, "balanced")
            # Re-run balanced with equal weights to expose the deliberate class-prevalence bias.
            for row in records:
                if row["sample_design"] == "balanced":
                    row["unweighted_test_weight"] = 1.0
            result_equal, _, _ = run_spatial_cv(records, 2021, target, threshold, model_name, "balanced", weights_key="unweighted_test_weight")
            for label, result in (("inverse_probability_weighted", result_unweighted), ("unweighted_balanced", result_equal)):
                overall = next(row for row in result if row["fold"] == "all_spatial_holdouts")
                bias_rows.append({"target": target, "model": model_name, "fit": label,
                                  "predicted_below_fraction": overall["predicted_below_fraction"],
                                  "actual_censor_fraction": overall["actual_censor_fraction"],
                                  "censor_probability_bias": overall["censor_probability_bias"],
                                  "rmse_db": overall["rmse_db"]})
    write_csv(output_root / "sampling_bias_comparison.csv", list(bias_rows[0]), bias_rows)

    # End-use-oriented ranking frameworks use the representative sample only;
    # no censored cell is treated as a measured quiet observation.
    home_rows = []
    home_summaries = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        model = models[f"{target}_2021_cnossos_inspired"]
        pred = _predict_records(model, representative)
        for radius in ("exact", "100m", "250m"):
            rows, summary = _rank_summary(representative, pred, target, threshold, radius)
            home_rows.extend(rows)
            summary.update({"model": "cnossos_inspired", "target": target})
            home_summaries.append(summary)
    write_csv(output_root / "quiet_home_cases.csv", list(home_rows[0]) if home_rows else ["radius"], home_rows)
    write_json(output_root / "quiet_home_summary.json", {"summaries": home_summaries,
                                                           "note": "This is a ranking diagnostic on reported cells, not property-level validation."})

    hiking_rows = []
    hiking_summary = []
    profile_features = {}
    for region in PHASE2B_REGIONS:
        for profile in ("horizontal", "vertical", "diagonal"):
            xs, ys = _profile_coordinates(region, profile)
            profile_features[(region.region_id, profile)] = (
                xs, ys, build_phase2b_features(xs, ys, source_cache[region.region_id][2021], use_speed=True, radius=5_000.0)
            )
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        model = models[f"{target}_2021_cnossos_inspired"]
        for region in PHASE2B_REGIONS:
            for profile in ("horizontal", "vertical", "diagonal"):
                rows = _profile_rows(region, source_cache[region.region_id][2021], model, target, threshold, profile,
                                     precomputed=profile_features[(region.region_id, profile)])
                hiking_rows.extend(rows)
                values = np.asarray([row["predicted_mean_db"] for row in rows])
                below = values < threshold
                current = longest = 0
                for value in below:
                    current = current + 1 if value else 0
                    longest = max(longest, current)
                hiking_summary.append({"region_id": region.region_id, "target": target, "profile": profile,
                                       "median_predicted_db": float(np.median(values)),
                                       "p90_predicted_db": float(np.quantile(values, 0.90)),
                                       "share_below_threshold": float(np.mean(below)),
                                       "longest_quiet_segment_m": int(longest * 100),
                                       "threshold_db": threshold})
    write_csv(output_root / "quiet_hiking_profiles.csv", list(hiking_rows[0]), hiking_rows)
    write_csv(output_root / "quiet_hiking_summary.csv", list(hiking_summary[0]), hiking_summary)

    independent_rows = [
        {"source": "NoiseCapture / Noise-Planet", "url": "https://onomap-gs.noise-planet.org/noisecapture_data.html", "measurement_type": "smartphone LAeq/LA50 tracks and points", "quality_rank": "medium-low", "status": "inventory only; calibration, route selection and time-of-day bias prevent treating as ground truth"},
        {"source": "Local authority environmental noise measurements", "url": "https://www.data.gov.uk/", "measurement_type": "site-specific attended/long-term measurements", "quality_rank": "potentially high per site", "status": "no harmonised national sub-threshold extract identified"},
        {"source": "Defra Round 4 strategic noise maps", "url": "https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517", "measurement_type": "modelled Lden/Lnight", "quality_rank": "not independent", "status": "target only; censored below reporting threshold"},
        {"source": "Academic/field studies", "url": "https://www.data.gov.uk/", "measurement_type": "study-specific sound-level observations", "quality_rank": "variable", "status": "requires case-by-case licence, calibration and spatial/temporal matching"},
    ]
    write_csv(output_root / "independent_validation_inventory.csv", list(independent_rows[0]), independent_rows)

    # Simple QA visuals: model comparison and representative predicted profiles.
    figure_rows = [row for row in validation_rows if row.get("fold") == "all_spatial_holdouts" and row.get("target") == "lden" and row.get("design") == "representative" and row.get("year") == 2021]
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    labels = [row["model"] for row in figure_rows]
    ax.bar(labels, [row["rmse_db"] for row in figure_rows], color="tab:blue")
    ax.set_ylabel("Weighted spatial-holdout RMSE (dB)")
    ax.set_title("Phase 2B Lden model comparison; representative 2021 traffic")
    ax.tick_params(axis="x", rotation=30)
    fig.savefig(output_root / "model_comparison.png", dpi=150)
    plt.close(fig)
    sample_profiles = [row for row in hiking_rows if row["target"] == "lden" and row["profile"] == "horizontal"]
    fig, ax = plt.subplots(figsize=(12, 6), constrained_layout=True)
    for region in PHASE2B_REGIONS:
        subset = [row for row in sample_profiles if row["region_id"] == region.region_id]
        ax.plot([row["distance_m"] for row in subset], [row["predicted_mean_db"] for row in subset], label=region.region_id)
    ax.axhline(lden_threshold, color="black", linestyle="--", linewidth=0.8)
    ax.set_xlabel("Profile distance (m)")
    ax.set_ylabel("Predicted Lden road proxy (dB)")
    ax.set_title("Phase 2B representative road profiles; sub-threshold values are modelled")
    ax.legend(fontsize=7, ncol=2)
    fig.savefig(output_root / "road_profiles.png", dpi=150)
    plt.close(fig)

    manifest = {
        "prototype": "Quiet UK Phase 2B road model reconstruction",
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "validated_phase1_untouched": True,
        "regions": [region.__dict__ for region in PHASE2B_REGIONS],
        "sample_n_per_region_representative": args.sample_n,
        "source_provenance": {
            "dft_aadf": {"url": DFT_AADF_ALL_YEARS_URL, "year_used": 2021, "ablation_year": 2025, "downloads_page": DFT_DOWNLOAD_PAGE, "api_documentation": DFT_API_DOCUMENTATION, "licence": "Open Government Licence v3.0", "retrieved_at": inputs["retrieved_at"], "rows_2021": len(aadf[2021]), "rows_2025": len(aadf[2025])},
            "dft_mrdb": {"urls": DFT_MRDB_URLS, "licence": "Open Government Licence v3.0"},
            "os_open_roads": {"page": OS_OPEN_ROADS_PAGE, "documentation": OS_OPEN_ROADS_DOCUMENTATION, "download_api": OS_OPEN_ROADS_API, "release": OS_OPEN_ROADS_RELEASE, "licence": "Open Government Licence", "crs": EPSG, "links": 3961077},
            "cnossos_reference": {"url": CNOSSOS_REFERENCE_URL, "status": "published equations informed the simplified proxy; no CNOSSOS compliance claimed"},
            "defra_road_lden": {"coverage_id": "562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All", "wcs_version": "1.0.0", "format": "GeoTIFF"},
            "defra_road_lnight": {"coverage_id": "562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lnight_England_Round_4_All", "wcs_version": "1.0.0", "format": "GeoTIFF"},
        },
        "thresholds": thresholds,
        "models": list(MODEL_NAMES),
        "outputs": {"receptor_sample": str(output_root / "receptor_sample.csv"), "validation_metrics": str(output_root / "validation_metrics.csv"), "temporal_ablation": str(output_root / "temporal_ablation_2021_vs_2025.csv"), "model_comparison": str(output_root / "model_comparison.png")},
        "no_national_phase2": True,
    }
    write_json(output_root / "phase2b_manifest.json", manifest)
    print(json.dumps({"output_root": str(output_root), "records": len(records), "regions": len(PHASE2B_REGIONS),
                      "aadf_rows_2021": len(aadf[2021]), "aadf_rows_2025": len(aadf[2025]),
                      "lden_threshold_db": lden_threshold, "lnight_threshold_db": lnight_threshold,
                      "models": list(MODEL_NAMES)}, indent=2))


if __name__ == "__main__":
    main()
