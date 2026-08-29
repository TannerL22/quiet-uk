"""Run the bounded Phase 2C road-source integrity experiment.

This runner reuses cached Phase 2B Defra/DfT/OS inputs, applies the existing
England mask to receptor sampling, and compares the old Phase 2B assignment
and propagation proxy with the deterministic Phase 2C implementation.  It
does not modify Phase 1 tiles or create a national Phase 2 raster.
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import requests
import rasterio
from rasterio.warp import transform as warp_transform
from scipy.stats import spearmanr
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk import phase2b_road as phase2b  # noqa: E402
from quiet_uk.phase2c_road import (  # noqa: E402
    PHASE2C_REGIONS,
    assign_traffic_two_pass,
    build_phase2c_features,
    fit_bounded_tobit,
    load_dft_mrdb_sources_phase2c,
    load_os_open_roads_links,
    phase2c_metrics,
    predict_bounded_tobit,
    read_land_window,
    sample_land_aware_indices,
    traffic_assignment_summary,
    utc_manifest,
    write_csv,
    write_json,
)


MODEL_FEATURES = {
    "phase2b_proxy": ("p2b_log1p_nearest_distance_m", "p2b_log10_inverse_square_energy_10000m"),
    "phase2c_proxy": ("p2c_log1p_nearest_distance_m", "p2c_log10_inverse_square_energy_5000m"),
    "phase2c_finite_line": ("p2c_log10_finite_line_energy_5000m", "p2c_log1p_nearest_distance_m"),
    "phase2c_complete": (
        "p2c_log10_finite_line_energy_5000m", "p2c_log10_inverse_square_energy_5000m",
        "p2c_log10_hgv_energy_5000m", "p2c_imputed_source_energy_fraction",
    ),
    "phase2c_bounded_finite_line": ("p2c_log10_finite_line_energy_5000m", "p2c_log1p_nearest_distance_m"),
}


def _float_or_none(value):
    if value in (None, ""):
        return None
    return float(value)


def _target_neighbourhood_means(array: np.ndarray, selected: np.ndarray, radius_cells: int) -> np.ndarray:
    """Calculate means from all target cells in a square neighbourhood."""
    values = np.asarray(array, dtype=float)
    finite = np.isfinite(values)
    safe = np.where(finite, values, 0.0)
    summed = np.pad(safe, ((1, 1), (1, 1)), mode="constant").cumsum(0).cumsum(1)
    counted = np.pad(finite.astype(float), ((1, 1), (1, 1)), mode="constant").cumsum(0).cumsum(1)
    rows, cols = np.unravel_index(selected, values.shape)
    output = np.full(len(selected), np.nan, dtype=float)
    for index, (row, col) in enumerate(zip(rows, cols)):
        r0, r1 = max(0, int(row) - radius_cells), min(values.shape[0], int(row) + radius_cells + 1)
        c0, c1 = max(0, int(col) - radius_cells), min(values.shape[1], int(col) + radius_cells + 1)
        total = summed[r1, c1] - summed[r0, c1] - summed[r1, c0] + summed[r0, c0]
        count = counted[r1, c1] - counted[r0, c1] - counted[r1, c0] + counted[r0, c0]
        if count > 0:
            output[index] = total / count
    return output


def _prepare_model_matrix(records: list[dict], names: tuple[str, ...]):
    return np.asarray([[float(record[name]) for name in names] for record in records], dtype=float)


def _fit_and_predict(train: list[dict], test: list[dict], target: str,
                     threshold: float, model_name: str, weights_key: str = "sampling_weight"):
    names = MODEL_FEATURES[model_name]
    x_train = _prepare_model_matrix(train, names)
    x_test = _prepare_model_matrix(test, names)
    y_train = np.asarray([float(row[target]) for row in train])
    c_train = np.asarray([bool(row[f"{target}_censored"]) for row in train])
    w_train = np.asarray([float(row[weights_key]) for row in train])
    y_test = np.asarray([float(row[target]) for row in test])
    c_test = np.asarray([bool(row[f"{target}_censored"]) for row in test])
    w_test = np.asarray([float(row[weights_key]) for row in test])
    x_train_s, x_test_s, mean, scale = phase2b.standardize_train_test(x_train, x_test)
    x_train_s = np.column_stack([np.ones(len(x_train_s)), x_train_s])
    x_test_s = np.column_stack([np.ones(len(x_test_s)), x_test_s])
    if model_name == "phase2c_bounded_finite_line":
        model = fit_bounded_tobit(x_train_s, y_train, c_train, w_train, threshold, floor_db=0.0)
        prediction = predict_bounded_tobit(model, x_test_s)
    else:
        model = phase2b.fit_tobit_weighted(x_train_s, y_train, c_train, w_train, threshold)
        prediction = phase2b.predict_tobit(model, x_test_s)
    model.update({"feature_names": list(names), "standardization_mean": mean.tolist(),
                  "standardization_scale": scale.tolist(), "model_name": model_name})
    return model, prediction, y_test, c_test, w_test


def _group_rows(test: list[dict], y, c, w, prediction, target, threshold, model_name, fold):
    rows = []
    groups = [("all", np.ones(len(test), dtype=bool))]
    for field in ("urban_rural", "nearest_road_class", "nearest_assignment"):
        for value in sorted({str(row[field]) for row in test}):
            groups.append((f"{field}:{value}", np.asarray([str(row[field]) == value for row in test])))
    distances = np.expm1(np.asarray([float(row["p2c_log1p_nearest_distance_m"]) for row in test]))
    for label, lo, hi in (("distance:0-100m", 0, 100), ("distance:100-500m", 100, 500),
                          ("distance:500-1000m", 500, 1000), ("distance:1000m+", 1000, np.inf)):
        groups.append((label, (distances >= lo) & (distances < hi)))
    for group, mask in groups:
        if not mask.any():
            continue
        subprediction = {key: np.asarray(value)[mask] for key, value in prediction.items()}
        metric = phase2c_metrics(np.asarray(y)[mask], np.asarray(c)[mask], subprediction,
                                 np.asarray(w)[mask], threshold)
        metric.update({"target": target, "model": model_name, "fold": fold, "group": group})
        rows.append(metric)
    return rows


def run_spatial_cv(records: list[dict], target: str, threshold: float, model_name: str,
                   design: str = "representative", weights_key: str = "sampling_weight"):
    subset = [row for row in records if row["sample_design"] == design]
    regions = sorted({row["region_id"] for row in subset})
    validation = []
    out_mu = np.full(len(subset), np.nan)
    out_probability = np.full(len(subset), np.nan)
    full_model = None
    for holdout in regions:
        train = [row for row in subset if row["region_id"] != holdout]
        test = [row for row in subset if row["region_id"] == holdout]
        model, prediction, y, c, w = _fit_and_predict(train, test, target, threshold, model_name, weights_key)
        metric = phase2c_metrics(y, c, prediction, w, threshold)
        metric.update({"target": target, "model": model_name, "design": design, "fold": holdout,
                       "group": "all", "feature_names": ";".join(MODEL_FEATURES[model_name])})
        validation.append(metric)
        validation.extend(_group_rows(test, y, c, w, prediction, target, threshold, model_name, holdout))
        indices = [index for index, row in enumerate(subset) if row["region_id"] == holdout]
        out_mu[indices] = prediction["mu_db"]
        out_probability[indices] = prediction["probability_below_threshold"]

    y_all = np.asarray([float(row[target]) for row in subset])
    c_all = np.asarray([bool(row[f"{target}_censored"]) for row in subset])
    w_all = np.asarray([float(row[weights_key]) for row in subset])
    out_prediction = {"mu_db": out_mu, "probability_below_threshold": out_probability,
                      "interval80_low_db": np.full(len(subset), np.nan),
                      "interval80_high_db": np.full(len(subset), np.nan)}
    overall = phase2c_metrics(y_all, c_all, out_prediction, w_all, threshold)
    overall.update({"target": target, "model": model_name, "design": design,
                    "fold": "all_spatial_holdouts", "group": "all",
                    "feature_names": ";".join(MODEL_FEATURES[model_name])})
    validation.append(overall)

    x_all = _prepare_model_matrix(subset, MODEL_FEATURES[model_name])
    x_all_s, _, mean_all, scale_all = phase2b.standardize_train_test(x_all, x_all)
    x_all_s = np.column_stack([np.ones(len(x_all_s)), x_all_s])
    if model_name == "phase2c_bounded_finite_line":
        full_model = fit_bounded_tobit(x_all_s, y_all, c_all, w_all, threshold, floor_db=0.0)
    else:
        full_model = phase2b.fit_tobit_weighted(x_all_s, y_all, c_all, w_all, threshold)
    full_model.update({"feature_names": list(MODEL_FEATURES[model_name]),
                       "standardization_mean": mean_all.tolist(),
                       "standardization_scale": scale_all.tolist(), "model_name": model_name,
                       "target": target, "design": design})
    return validation, out_prediction, full_model, subset


def _predict_full(model: dict, records: list[dict]):
    x = _prepare_model_matrix(records, tuple(model["feature_names"]))
    mean = np.asarray(model["standardization_mean"], dtype=float)
    scale = np.asarray(model["standardization_scale"], dtype=float)
    x = np.column_stack([np.ones(len(x)), (x - mean) / scale])
    if model["model_name"] == "phase2c_bounded_finite_line":
        return predict_bounded_tobit(model, x)
    return phase2b.predict_tobit(model, x)


def _write_imputation_summary(sources_by_region: dict[str, list[dict]], output_root: Path):
    grouped = {}
    for region_id, sources in sources_by_region.items():
        for source in sources:
            method = str(source["imputation_method"])
            item = grouped.setdefault(method, {"imputation_method": method, "n": 0, "support": [], "radius": []})
            item["n"] += 1
            item["support"].append(int(source["imputation_support_n"]))
            item["radius"].append(float(source["imputation_radius_m"]))
    rows = []
    for method, item in sorted(grouped.items()):
        rows.append({"imputation_method": method, "n": item["n"],
                     "fraction": item["n"] / max(sum(value["n"] for value in grouped.values()), 1),
                     "median_support_n": float(np.median(item["support"])),
                     "median_radius_m": float(np.median(item["radius"]))})
    write_csv(output_root / "imputation_method_summary.csv", list(rows[0]), rows)


def _load_osm_paths(regions, timeout: int) -> tuple[list[dict], dict]:
    """Fetch a few small OSM way geometries; failure is recorded, not fatal."""
    endpoint = "https://overpass-api.de/api/interpreter"
    selected = [region for region in regions if region.region_id in {
        "heathrow_london", "peak_district", "south_downs", "east_anglia_coast"
    }]
    paths = []
    errors = []
    for region in selected:
        x = (region.bbox[0] + region.bbox[2]) / 2.0
        y = (region.bbox[1] + region.bbox[3]) / 2.0
        lon, lat = warp_transform("EPSG:27700", "EPSG:4326", [x], [y])
        query = f"[out:json][timeout:25];way[highway~\"footway|path|track|pedestrian|residential|tertiary|secondary|primary\"](around:1500,{lat[0]},{lon[0]});out geom;"
        try:
            response = requests.post(endpoint, data=query, headers={
                "User-Agent": "QuietUK/0.3 Phase2C research diagnostic (contact: github.com/TannerL22/quiet-uk)",
                "Accept": "application/json",
            }, timeout=timeout)
            response.raise_for_status()
            elements = response.json().get("elements", [])
            candidates = []
            for element in elements:
                geometry = element.get("geometry", [])
                if len(geometry) < 2:
                    continue
                xs, ys = warp_transform("EPSG:4326", "EPSG:27700",
                                        [point["lon"] for point in geometry],
                                        [point["lat"] for point in geometry])
                line = LineString(list(zip(xs, ys)))
                if line.length > 150:
                    candidates.append((line.length, element, line))
            candidates.sort(key=lambda item: (-item[0], int(item[1].get("id", 0))))
            if candidates:
                length, element, line = candidates[0]
                paths.append({"path_id": f"osm_{region.region_id}_{element['id']}",
                              "region_id": region.region_id, "geometry": line,
                              "highway": element.get("tags", {}).get("highway", "unknown"),
                              "length_m": float(length)})
            else:
                errors.append({"region_id": region.region_id, "error": "no suitable OSM way returned"})
        except Exception as exc:
            errors.append({"region_id": region.region_id, "error": f"{type(exc).__name__}: {exc}"})
    return paths, {"endpoint": endpoint, "attempted_regions": [r.region_id for r in selected],
                   "paths_retrieved": len(paths), "errors": errors,
                   "licence": "OpenStreetMap data, © OpenStreetMap contributors, ODbL"}


def _sample_line(line: LineString, spacing_m: float = 100.0):
    distances = np.arange(0.0, max(line.length, 0.0) + 0.1, spacing_m)
    if not len(distances) or distances[-1] < line.length:
        distances = np.r_[distances, line.length]
    return np.asarray([line.interpolate(float(distance)) for distance in distances], dtype=object)


def _longest_segment(mask: np.ndarray, spacing_m: float) -> float:
    current = longest = 0
    for value in mask:
        current = current + 1 if bool(value) else 0
        longest = max(longest, current)
    return float(longest * spacing_m)


def _run_path_diagnostics(paths, source_cache, model, threshold):
    rows = []
    for path in paths:
        points = _sample_line(path["geometry"])
        xs = np.asarray([point.x for point in points])
        ys = np.asarray([point.y for point in points])
        features = build_phase2c_features(xs, ys, source_cache[path["region_id"]], radius=5_000.0)
        records = []
        names = tuple(model["feature_names"])
        feature_names = {name.replace("p2c_", "") for name in names}
        for index in range(len(xs)):
            row = {}
            for name in names:
                row[name] = float(features[name.replace("p2c_", "")][index])
            records.append(row)
        prediction = _predict_full(model, records)
        values = prediction["mu_db"]
        rows.append({"path_id": path["path_id"], "region_id": path["region_id"],
                     "highway": path["highway"], "length_m": path["length_m"],
                     "sample_spacing_m": 100.0, "sample_n": len(values),
                     "median_predicted_db": float(np.median(values)),
                     "p90_predicted_db": float(np.quantile(values, 0.90)),
                     "share_below_threshold": float(np.mean(values < threshold)),
                     "longest_noisy_section_m": _longest_segment(values >= threshold, 100.0),
                     "longest_quiet_section_m": _longest_segment(values < threshold, 100.0),
                     "threshold_db": threshold})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", default="data/processed/phase2c_road")
    parser.add_argument("--results-root", default="results/phase2c")
    parser.add_argument("--raw-root", default="data/raw/phase2b_road")
    parser.add_argument("--mask", default="data/processed/england_mask/england_100m_mask.tif")
    parser.add_argument("--sample-n", type=int, default=800)
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--no-osm", action="store_true")
    args = parser.parse_args()
    output_root = ROOT / args.output_root
    results_root = ROOT / args.results_root
    output_root.mkdir(parents=True, exist_ok=True)
    results_root.mkdir(parents=True, exist_ok=True)

    inputs = phase2b.ensure_phase2b_inputs(ROOT / args.raw_root, timeout=args.timeout)
    aadf = phase2b.load_dft_aadf_year(inputs["aadf_csv"], 2021)
    raw_root = ROOT / args.raw_root
    raw_lden = ROOT / "data/processed/phase2b_road/raw/road_lden_10m"
    raw_lnight = ROOT / "data/processed/phase2b_road/raw/road_lnight_10m"
    mask_path = ROOT / args.mask

    records = []
    assignment_by_region = {}
    source_cache = {}
    land_rows = []
    raster_rows = []
    for index, region in enumerate(PHASE2C_REGIONS):
        lden, lden_transform, lden_info = phase2b.read_target(raw_lden / f"{region.region_id}.tif")
        lnight, _, lnight_info = phase2b.read_target(raw_lnight / f"{region.region_id}.tif")
        with rasterio.open(raw_lden / f"{region.region_id}.tif") as dataset:
            land100 = read_land_window(mask_path, region.bbox, (dataset.shape[0] // 10, dataset.shape[1] // 10))
            land10 = np.repeat(np.repeat(land100, 10, axis=0), 10, axis=1)
        representative, balanced, land_info = sample_land_aware_indices(lden, land10, args.sample_n, 2030 + index)
        selected = np.unique(np.r_[representative, balanced])
        rows, cols = np.unravel_index(selected, lden.shape)
        xs = lden_transform.c + (cols + 0.5) * lden_transform.a
        ys = lden_transform.f + (rows + 0.5) * lden_transform.e

        dft_sources = load_dft_mrdb_sources_phase2c(aadf, inputs["mrdb"][2021]["shp"], region.bbox, margin_m=5_500.0)
        os_bbox = (region.bbox[0] - 5_000, region.bbox[1] - 5_000,
                   region.bbox[2] + 5_000, region.bbox[3] + 5_000)
        os_links = load_os_open_roads_links(inputs["os_gpkg"], os_bbox)
        phase2c_sources, counts = assign_traffic_two_pass(os_links, dft_sources, region.urban_rural)
        assignment_by_region[region.region_id] = phase2c_sources
        source_cache[region.region_id] = phase2c_sources

        old_dft_sources = phase2b.load_dft_mrdb_sources(aadf, inputs["mrdb"][2021]["shp"], region.bbox, margin_m=5_500.0)
        phase2b_sources, old_counts = phase2b.load_os_open_roads_sources(inputs["os_gpkg"], old_dft_sources, os_bbox, region.urban_rural)
        old_features = phase2b.build_phase2b_features(xs, ys, phase2b_sources, use_speed=True, radius=5_000.0)
        new_features = build_phase2c_features(xs, ys, phase2c_sources, use_speed=True, radius=5_000.0)

        for radius_cells, radius_label in ((10, "100m"), (25, "250m")):
            neighbourhood = _target_neighbourhood_means(lden, selected, radius_cells)
            for local_index, flat_index in enumerate(selected):
                pass
            # Stored below per receptor; the loop is kept outside feature creation
            # so each neighbourhood uses the full raster, not sampled neighbours.
            if radius_label == "100m":
                lden_100m_neighbourhood = neighbourhood
            else:
                lden_250m_neighbourhood = neighbourhood
        lnight_100m_neighbourhood = _target_neighbourhood_means(lnight, selected, 10)
        lnight_250m_neighbourhood = _target_neighbourhood_means(lnight, selected, 25)
        reported = np.isfinite(lden.ravel())
        rep_set = set(int(value) for value in representative)
        bal_set = set(int(value) for value in balanced)
        for local, flat_index in enumerate(selected):
            flat_index = int(flat_index)
            if flat_index in rep_set:
                design = "representative"
                weight = float(land_info["eligible_land_cells"] / len(representative))
            else:
                design = "balanced"
                cls = bool(reported[flat_index])
                class_population = int(np.sum(land10.ravel() & reported) if cls else np.sum(land10.ravel() & ~reported))
                class_sample = int(np.sum(reported[balanced]) if cls else np.sum(~reported[balanced]))
                weight = float(class_population / max(class_sample, 1))
            row = {
                "region_id": region.region_id, "region_label": region.label,
                "urban_rural": region.urban_rural, "x": float(xs[local]), "y_coord": float(ys[local]),
                "sample_design": design, "sampling_weight": weight,
                "land_mask_valid": 1, "population_n": int(land_info["eligible_land_cells"]),
                "excluded_non_land_cells": int(land_info["excluded_non_land_cells"]),
                "lden": float(lden.ravel()[flat_index]) if reported[flat_index] else 40.0,
                "lden_censored": int(not reported[flat_index]),
                "lnight": float(lnight.ravel()[flat_index]) if np.isfinite(lnight.ravel()[flat_index]) else 35.0,
                "lnight_censored": int(not np.isfinite(lnight.ravel()[flat_index])),
                "lden_100m_target_mean": float(lden_100m_neighbourhood[local]),
                "lden_250m_target_mean": float(lden_250m_neighbourhood[local]),
                "lnight_100m_target_mean": float(lnight_100m_neighbourhood[local]),
                "lnight_250m_target_mean": float(lnight_250m_neighbourhood[local]),
            }
            for name, value in old_features.items():
                if np.asarray(value).dtype.kind in "fc":
                    row[f"p2b_{name}"] = float(value[local])
            for name, value in new_features.items():
                if np.asarray(value).dtype.kind in "fc":
                    row[f"p2c_{name}"] = float(value[local])
                else:
                    row[f"p2c_{name}"] = str(value[local])
            row["nearest_road_class"] = str(new_features["nearest_road_class"][local])
            row["nearest_assignment"] = str(new_features["nearest_traffic_source"][local])
            row["nearest_match_category"] = str(new_features["nearest_match_category"][local])
            row["nearest_traffic_confidence"] = str(new_features["nearest_traffic_confidence"][local])
            records.append(row)
        land_rows.append({"region_id": region.region_id, "label": region.label, **land_info,
                          "land_fraction_of_10m_receptors": land_info["eligible_land_cells"] / land_info["total_cells"]})
        raster_rows.append({"region_id": region.region_id, "lden_min_positive_db": lden_info["min_positive_db"],
                            "lnight_min_positive_db": lnight_info["min_positive_db"],
                            "lden_censored_fraction": float((~np.isfinite(lden)).mean()),
                            "lnight_censored_fraction": float((~np.isfinite(lnight)).mean()),
                            "source_shape": str(lden.shape), "source_crs": lden_info["crs"],
                            "source_transform": str(lden_info["transform"]),
                            "raw_lden_diagnostics": json.dumps(lden_info["diagnostics"]),
                            "raw_lnight_diagnostics": json.dumps(lnight_info["diagnostics"])})

    thresholds = phase2b.infer_thresholds(raster_rows)
    lden_threshold = float(thresholds["lden_threshold_inferred_db"])
    lnight_threshold = float(thresholds["lnight_threshold_inferred_db"])
    if thresholds["lden_range_db"][1] - thresholds["lden_range_db"][0] > 0.5 or thresholds["lnight_range_db"][1] - thresholds["lnight_range_db"][0] > 0.5:
        raise ValueError(f"Thresholds are not stable: {thresholds}")

    fields = sorted({key for row in records for key in row})
    write_csv(output_root / "receptor_sample.csv", fields, records)
    write_csv(output_root / "land_mask_sampling_summary.csv", list(land_rows[0]), land_rows)
    write_csv(output_root / "raster_inventory.csv", list(raster_rows[0]), raster_rows)
    assignment_rows = []
    for region_id, sources in assignment_by_region.items():
        counts = {"region_id": region_id, "os_links": len(sources),
                  "direct_traffic_links": sum(row["traffic_assignment_source"] == "direct" for row in sources),
                  "imputed_traffic_links": sum(row["traffic_assignment_source"] == "imputed" for row in sources),
                  "direct_fraction": sum(row["traffic_assignment_source"] == "direct" for row in sources) / max(len(sources), 1),
                  "geometry_only_fraction": sum(row["match_category"] == "geometry_only" for row in sources) / max(len(sources), 1)}
        assignment_rows.append(counts)
    write_csv(output_root / "traffic_attribution_summary.csv", list(assignment_rows[0]), assignment_rows)
    write_csv(output_root / "match_qa_sample.csv", [
        "region_id", "road_class", "match_category", "link_id", "road_number", "road_name",
        "matched_dft_id", "match_distance_m", "match_score", "road_number_match", "road_name_match",
        "road_class_match", "orientation_difference_deg", "match_candidate_count", "flow", "hgv_share",
    ], [
        {"region_id": region_id, "road_class": source["road_class"], "match_category": source["match_category"],
         "link_id": source["link_id"], "road_number": source.get("road_number", ""), "road_name": source.get("road_name", ""),
         "matched_dft_id": source.get("matched_dft_id", ""), "match_distance_m": source.get("match_distance_m", ""),
         "match_score": source.get("match_score", ""), "road_number_match": source.get("road_number_match", 0),
         "road_name_match": source.get("road_name_match", 0), "road_class_match": source.get("road_class_match", 0),
         "orientation_difference_deg": source.get("orientation_difference_deg", ""),
         "match_candidate_count": source.get("match_candidate_count", 0), "flow": source["flow"], "hgv_share": source["hgv_share"]}
        for region_id, sources in assignment_by_region.items()
        for road_class in ("motorway", "a_road", "b_road", "minor")
        for source in [next((item for item in sources if item["road_class"] == road_class and item["traffic_assignment_source"] == "direct"), None)]
        if source is not None
    ])
    write_csv(output_root / "traffic_match_by_class.csv", ["region_id", "road_class", "match_category", "n", "fraction_of_class", "median_match_distance_m", "median_match_score"],
              [dict(region_id=region_id, **row) for region_id, sources in assignment_by_region.items() for row in traffic_assignment_summary(sources)])
    _write_imputation_summary(assignment_by_region, output_root)

    validation_rows = []
    models = {}
    predictions = {}
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for model_name in MODEL_FEATURES:
            rows, prediction, model, subset = run_spatial_cv(records, target, threshold, model_name)
            validation_rows.extend(rows)
            models[f"{target}_{model_name}"] = model
            predictions[f"{target}_{model_name}"] = (prediction, subset)
    write_csv(output_root / "validation_metrics.csv", sorted({key for row in validation_rows for key in row}), validation_rows)
    write_json(output_root / "models_full.json", models)

    comparison_rows = []
    for row in validation_rows:
        if row.get("fold") != "all_spatial_holdouts" or row.get("group") != "all":
            continue
        comparison_rows.append({"phase": "Phase 2B" if row["model"] == "phase2b_proxy" else "Phase 2C",
                                "target": row["target"], "model": row["model"],
                                "rmse_db": row.get("rmse_db"), "mae_db": row.get("mae_db"),
                                "bias_db": row.get("bias_db"), "p90_absolute_error_db": row.get("p90_absolute_error_db"),
                                "censor_brier": row.get("censor_brier"),
                                "censor_probability_bias": row.get("censor_probability_bias"),
                                "spearman_rank_reported": row.get("spearman_rank_reported")})
    write_csv(output_root / "phase2b_vs_phase2c_comparison.csv", list(comparison_rows[0]), comparison_rows)

    calibration_rows = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        prediction, subset = predictions[f"{target}_phase2c_bounded_finite_line"]
        order = np.argsort(prediction["probability_below_threshold"])
        for bin_index, chunk in enumerate(np.array_split(order, 10)):
            if len(chunk) == 0:
                continue
            weights = np.asarray([float(subset[i]["sampling_weight"]) for i in chunk])
            actual = np.asarray([bool(subset[i][f"{target}_censored"]) for i in chunk], dtype=float)
            probabilities = prediction["probability_below_threshold"][chunk]
            calibration_rows.append({"target": target, "model": "phase2c_bounded_finite_line", "bin": bin_index,
                                     "n": len(chunk), "predicted_probability": float(np.average(probabilities, weights=weights)),
                                     "observed_fraction": float(np.average(actual, weights=weights)), "threshold_db": threshold})
    write_csv(output_root / "censor_calibration_summary.csv", list(calibration_rows[0]), calibration_rows)

    uncertainty_summary = []
    rural_summary = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        prediction_arrays = {name: predictions[f"{target}_{name}"][0]["mu_db"] for name in MODEL_FEATURES}
        stack = np.column_stack(list(prediction_arrays.values()))
        rural = np.asarray([row["urban_rural"] == "rural" for row in predictions[f"{target}_phase2c_bounded_finite_line"][1]])
        uncertainty_summary.append({"target": target, "median_model_disagreement_sd_db": float(np.median(np.nanstd(stack, axis=1))),
                                    "p90_model_disagreement_sd_db": float(np.quantile(np.nanstd(stack, axis=1), 0.90)),
                                    "bounded_min_db": float(np.nanmin(prediction_arrays["phase2c_bounded_finite_line"])),
                                    "linear_min_db": float(np.nanmin(prediction_arrays["phase2c_finite_line"])),
                                    "linear_negative_fraction": float(np.mean(prediction_arrays["phase2c_finite_line"] < 0)),
                                    "bounded_negative_fraction": float(np.mean(prediction_arrays["phase2c_bounded_finite_line"] < 0)),
                                    "rural_linear_negative_fraction": float(np.mean(prediction_arrays["phase2c_finite_line"][rural] < 0)) if rural.any() else 0.0,
                                    "rural_bounded_negative_fraction": float(np.mean(prediction_arrays["phase2c_bounded_finite_line"][rural] < 0)) if rural.any() else 0.0,
                                    "threshold_db": threshold,
                                    "note": "Model disagreement and residual scale are diagnostics, not calibrated measurement error."})
        for model_name in ("phase2c_finite_line", "phase2c_bounded_finite_line"):
            values = prediction_arrays[model_name]
            rural_summary.append({"target": target, "model": model_name, "n": len(values),
                                  "min_db": float(np.nanmin(values)), "p01_db": float(np.nanquantile(values, 0.01)),
                                  "median_db": float(np.nanmedian(values)), "max_db": float(np.nanmax(values)),
                                  "rural_min_db": float(np.nanmin(values[rural])) if rural.any() else None})
    write_json(output_root / "uncertainty_summary.json", {"summary": uncertainty_summary,
                                                             "note": "Model disagreement and fitted residual scale are not real-world confidence intervals."})
    write_csv(output_root / "rural_extrapolation_summary.csv", list(rural_summary[0]), rural_summary)

    neighbourhood_rows = []
    for target in ("lden", "lnight"):
        prediction, subset = predictions[f"{target}_phase2c_bounded_finite_line"]
        for radius_label in ("exact", "100m", "250m"):
            field = target if radius_label == "exact" else f"{target}_{radius_label}_target_mean"
            mask = np.isfinite([float(row[field]) for row in subset]) & np.asarray([not bool(row[f"{target}_censored"]) for row in subset])
            observed = np.asarray([float(row[field]) for row in subset])[mask]
            predicted = prediction["mu_db"][mask]
            neighbourhood_rows.append({"target": target, "radius": radius_label, "n": int(mask.sum()),
                                       "spearman_predicted_vs_full_target_grid": float(spearmanr(observed, predicted).statistic) if mask.sum() >= 3 else None,
                                       "mean_target_db": float(np.mean(observed)) if len(observed) else None,
                                       "note": "100 m/250 m values are full target-grid neighbourhood means; censored centres are excluded from rank evaluation."})
    write_csv(output_root / "quiet_home_neighbourhood_summary.csv", list(neighbourhood_rows[0]), neighbourhood_rows)

    if args.no_osm:
        paths, osm_info = [], {"skipped": True, "reason": "--no-osm"}
    else:
        paths, osm_info = _load_osm_paths(PHASE2C_REGIONS, args.timeout)
    osm_model = models["lden_phase2c_bounded_finite_line"]
    path_rows = _run_path_diagnostics(paths, source_cache, osm_model, lden_threshold) if paths else []
    write_csv(output_root / "hiking_path_summary.csv", ["path_id", "region_id", "highway", "length_m", "sample_spacing_m", "sample_n", "median_predicted_db", "p90_predicted_db", "share_below_threshold", "longest_noisy_section_m", "longest_quiet_section_m", "threshold_db"], path_rows)
    write_json(output_root / "osm_path_inventory.json", osm_info)

    figure_rows = [row for row in comparison_rows if row["target"] == "lden"]
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    ax.bar([row["model"] for row in figure_rows], [row["rmse_db"] for row in figure_rows], color=["tab:gray" if row["phase"] == "Phase 2B" else "tab:blue" for row in figure_rows])
    ax.set_ylabel("Weighted spatial-holdout RMSE (dB)")
    ax.set_title("Phase 2B / 2C Lden road-source comparison; 2021 traffic")
    ax.tick_params(axis="x", rotation=30)
    fig.savefig(output_root / "phase2c_model_comparison.png", dpi=150)
    fig.savefig(results_root / "phase2c_model_comparison.png", dpi=120)
    plt.close(fig)

    categories = ["direct_high_confidence", "direct_medium_confidence", "imputed", "geometry_only"]
    category_totals = {category: sum(sum(source["match_category"] == category for source in sources) for sources in assignment_by_region.values()) for category in categories}
    fig, ax = plt.subplots(figsize=(8, 4), constrained_layout=True)
    ax.bar(list(category_totals), list(category_totals.values()), color="tab:green")
    ax.set_ylabel("OS links")
    ax.set_title("Phase 2C traffic assignment categories")
    ax.tick_params(axis="x", rotation=25)
    fig.savefig(output_root / "phase2c_match_categories.png", dpi=150)
    fig.savefig(results_root / "phase2c_match_categories.png", dpi=120)
    plt.close(fig)

    lightweight_names = [
        "phase2b_vs_phase2c_comparison.csv", "validation_metrics.csv", "traffic_attribution_summary.csv",
        "imputation_method_summary.csv", "traffic_match_by_class.csv", "match_qa_sample.csv",
        "censor_calibration_summary.csv", "uncertainty_summary.json", "quiet_home_neighbourhood_summary.csv",
        "hiking_path_summary.csv", "osm_path_inventory.json", "rural_extrapolation_summary.csv",
        "land_mask_sampling_summary.csv",
    ]
    for name in lightweight_names:
        shutil.copy2(output_root / name, results_root / name)

    manifest = utc_manifest({
        "prototype": "Quiet UK Phase 2C road-source integrity and stability pass",
        "phase2b_commit": "4a07a21",
        "regions": [region.__dict__ for region in PHASE2C_REGIONS],
        "sample_n_per_region": args.sample_n,
        "records": len(records),
        "primary_year": 2021,
        "thresholds": thresholds,
        "land_mask": str(mask_path),
        "models": list(MODEL_FEATURES),
        "source_provenance": {
            "dft_aadf": {"url": phase2b.DFT_AADF_ALL_YEARS_URL, "year": 2021, "rows": len(aadf)},
            "dft_mrdb": {"url": phase2b.DFT_MRDB_URLS[2021], "year": 2021},
            "os_open_roads": {"url": phase2b.OS_OPEN_ROADS_API, "release": phase2b.OS_OPEN_ROADS_RELEASE, "crs": phase2b.EPSG},
            "defra_road_lden_coverage_id": phase2b.ROAD_LDEN_COVERAGE_ID,
            "defra_road_lnight_coverage_id": phase2b.ROAD_LNIGHT_COVERAGE_ID,
        },
        "outputs": {"validation_metrics": str(output_root / "validation_metrics.csv"),
                    "comparison": str(output_root / "phase2b_vs_phase2c_comparison.csv"),
                    "lightweight_results": str(results_root)},
        "osm_path_inventory": osm_info,
    })
    write_json(output_root / "phase2c_manifest.json", manifest)
    shutil.copy2(output_root / "phase2c_manifest.json", results_root / "phase2c_manifest.json")
    print(json.dumps({"phase": "2C", "records": len(records), "regions": len(PHASE2C_REGIONS),
                      "lden_threshold_db": lden_threshold, "lnight_threshold_db": lnight_threshold,
                      "output_root": str(output_root), "results_root": str(results_root),
                      "osm_paths": len(paths)}, indent=2))


if __name__ == "__main__":
    main()
