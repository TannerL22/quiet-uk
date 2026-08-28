"""Recompute Phase 2B models from an existing receptor feature table.

This is useful after changing statistical design (for example adding an
intercept) without repeating the expensive OS Open Roads feature extraction.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
from shapely.geometry import LineString

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location("phase2b_runner", ROOT / "scripts" / "15_run_phase2b_road_reconstruction.py")
runner = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(runner)


STRING_FIELDS = {"region_id", "region_label", "landscape", "urban_rural", "sample_design",
                 "nearest_road_class", "nearest_traffic_source"}


def load_records(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            row = {}
            for key, value in raw.items():
                if key in STRING_FIELDS or key.startswith("y2021_nearest_") or key.startswith("y2025_nearest_"):
                    row[key] = value
                elif value == "":
                    row[key] = np.nan
                else:
                    try:
                        row[key] = float(value)
                    except ValueError:
                        row[key] = value
            records.append(row)
    return records


def main() -> None:
    output_root = ROOT / "data/processed/phase2b_road"
    raw_root = ROOT / "data/raw/phase2b_road"
    records = load_records(output_root / "receptor_sample.csv")
    inputs = runner.ensure_phase2b_inputs(raw_root, timeout=240)
    aadf = {year: runner.load_dft_aadf_year(inputs["aadf_csv"], year) for year in (2021, 2025)}
    raster_rows = list(csv.DictReader((output_root / "raster_inventory.csv").open(encoding="utf-8")))
    thresholds = runner.infer_thresholds([
        {"lden_min_positive_db": float(row["lden_min_positive_db"]), "lnight_min_positive_db": float(row["lnight_min_positive_db"])}
        for row in raster_rows
    ])
    lden_threshold = thresholds["lden_threshold_inferred_db"]
    lnight_threshold = thresholds["lnight_threshold_inferred_db"]

    source_cache = {}
    for region in runner.PHASE2B_REGIONS:
        source_cache[region.region_id] = {}
        for year in (2021, 2025):
            dft_sources = runner.load_dft_mrdb_sources(aadf[year], inputs["mrdb"][year]["shp"], region.bbox, margin_m=5_500.0)
            source_cache[region.region_id][year], _ = runner.load_os_open_roads_sources(
                inputs["os_gpkg"], dft_sources,
                (region.bbox[0] - 5_000, region.bbox[1] - 5_000, region.bbox[2] + 5_000, region.bbox[3] + 5_000),
                region.urban_rural,
            )

    validation_rows = []
    detail_rows = []
    calibration_rows = []
    models = {}
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for year in (2021, 2025):
            for model_name in runner.MODEL_NAMES:
                result, extras, full_model = runner.run_spatial_cv(records, year, target, threshold, model_name, "representative")
                validation_rows.extend(result)
                detail_rows.extend(extras["details"])
                calibration_rows.extend(extras["calibration"])
                models[f"{target}_{year}_{model_name}"] = full_model
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for model_name in ("phase2a_proxy", "cnossos_inspired"):
            result, extras, full_model = runner.run_spatial_cv(records, 2021, target, threshold, model_name, "balanced")
            validation_rows.extend(result)
            detail_rows.extend(extras["details"])
            calibration_rows.extend(extras["calibration"])
            models[f"{target}_2021_balanced_{model_name}"] = full_model

    temporal = [row for row in validation_rows if row.get("design") == "representative" and row.get("fold") == "all_spatial_holdouts"
                and row.get("model") in ("phase2a_proxy", "cnossos_inspired")]
    runner.write_csv(output_root / "temporal_ablation_2021_vs_2025.csv", list(temporal[0]), temporal)
    runner.write_csv(output_root / "validation_metrics.csv", sorted({key for row in validation_rows for key in row}), validation_rows)
    runner.write_csv(output_root / "validation_detail_sample.csv", sorted({key for row in detail_rows for key in row}), detail_rows)
    runner.write_csv(output_root / "calibration_bins.csv", list(calibration_rows[0]), calibration_rows)
    runner.write_json(output_root / "models_full.json", models)

    representative = [row for row in records if row["sample_design"] == "representative"]
    bias_rows = []
    for model_name in ("phase2a_proxy", "cnossos_inspired"):
        for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
            weighted, _, _ = runner.run_spatial_cv(records, 2021, target, threshold, model_name, "balanced")
            for row in records:
                if row["sample_design"] == "balanced":
                    row["unweighted_test_weight"] = 1.0
            unweighted, _, _ = runner.run_spatial_cv(records, 2021, target, threshold, model_name, "balanced", weights_key="unweighted_test_weight")
            for label, result in (("inverse_probability_weighted", weighted), ("unweighted_balanced", unweighted)):
                overall = next(row for row in result if row["fold"] == "all_spatial_holdouts")
                bias_rows.append({"target": target, "model": model_name, "fit": label,
                                  "predicted_below_fraction": overall["predicted_below_fraction"],
                                  "actual_censor_fraction": overall["actual_censor_fraction"],
                                  "censor_probability_bias": overall["censor_probability_bias"],
                                  "rmse_db": overall["rmse_db"]})
    runner.write_csv(output_root / "sampling_bias_comparison.csv", list(bias_rows[0]), bias_rows)

    speed_rows = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        for speed_mode in ("nospeed", "speed"):
            result, _, _ = runner.run_spatial_cv(records, 2021, target, threshold, "cnossos_inspired",
                                                 "representative", speed_mode=speed_mode)
            overall = next(row for row in result if row["fold"] == "all_spatial_holdouts")
            speed_rows.append({"target": target, "speed_mode": speed_mode,
                               "speed_observation_fraction": 0.0,
                               "speed_input": "fixed 70 km/h reference" if speed_mode == "nospeed" else "class-reference imputation",
                               "rmse_db": overall["rmse_db"], "mae_db": overall["mae_db"],
                               "bias_db": overall["bias_db"],
                               "censor_probability_bias": overall["censor_probability_bias"]})
    runner.write_csv(output_root / "speed_ablation.csv", list(speed_rows[0]), speed_rows)

    # Controlled physical response and model disagreement are kept separate
    # from the spatial holdout metrics: they are diagnostics, not validation.
    physical_rows = []
    low_source = {"geometry": LineString([(-2000, 0), (2000, 0)]),
                  "flow": 1000.0, "hgv_flow": 50.0, "hgv_share": 0.05, "road_class": "a_road",
                  "traffic_source": "counted", "traffic_confidence": "Counted", "speed_kmh": 80.0}
    high_source = {**low_source, "flow": 10000.0, "hgv_flow": 500.0}
    xs = np.zeros(100)
    ys = np.linspace(20.0, 2000.0, 100)
    low_features = runner.build_phase2b_features(xs, ys, [low_source], use_speed=True)
    high_features = runner.build_phase2b_features(xs, ys, [high_source], use_speed=True)
    model = models["lden_2021_cnossos_inspired"]
    def controlled_prediction(features):
        names = [name.replace("y2021_", "", 1).replace("_speed", "").replace("5000m", "10000m") for name in model["feature_names"]]
        matrix = np.asarray([[features[name][i] for name in names] for i in range(len(xs))])
        mean = np.asarray(model["standardization_mean"], dtype=float)
        scale = np.asarray(model["standardization_scale"], dtype=float)
        return runner.predict_tobit(model, np.column_stack([np.ones(len(matrix)), (matrix - mean) / scale]))
    low_prediction = controlled_prediction(low_features)
    high_prediction = controlled_prediction(high_features)
    physical_summary = {
        "higher_flow_non_decrease_fraction": float(np.mean(high_prediction["mu_db"] >= low_prediction["mu_db"] - 1e-9)),
        "low_flow_distance_decrease_fraction": float(np.mean(np.diff(low_prediction["mu_db"]) <= 0)),
        "high_flow_distance_decrease_fraction": float(np.mean(np.diff(high_prediction["mu_db"]) <= 0)),
        "note": "Controlled response check only; not an independent validation set.",
    }
    for i, distance in enumerate(ys):
        physical_rows.append({"distance_m": float(distance), "low_flow_predicted_db": float(low_prediction["mu_db"][i]),
                              "high_flow_predicted_db": float(high_prediction["mu_db"][i])})
    runner.write_csv(output_root / "physical_sanity.csv", list(physical_rows[0]), physical_rows)
    runner.write_json(output_root / "physical_sanity.json", physical_summary)

    uncertainty_rows = []
    uncertainty_summary = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        prediction_stack = []
        for model_name in runner.MODEL_NAMES:
            prediction_stack.append(runner._predict_records(models[f"{target}_2021_{model_name}"], representative)["mu_db"])
        stack = np.column_stack(prediction_stack)
        disagreement = np.nanstd(stack, axis=1)
        primary = stack[:, runner.MODEL_NAMES.index("cnossos_inspired")]
        for cutoff in (35.0, 30.0):
            mask = primary < cutoff
            uncertainty_summary.append({"target": target, "primary_mean_cutoff_db": cutoff, "n": int(mask.sum()),
                                        "median_model_disagreement_sd_db": float(np.median(disagreement[mask])) if mask.any() else None,
                                        "p90_model_disagreement_sd_db": float(np.quantile(disagreement[mask], 0.90)) if mask.any() else None,
                                        "primary_residual_sigma_db": float(models[f"{target}_2021_cnossos_inspired"]["sigma_db"]),
                                        "interval80_half_width_db": float(1.2815515655 * models[f"{target}_2021_cnossos_inspired"]["sigma_db"])})
        for i, row in enumerate(representative):
            uncertainty_rows.append({"target": target, "row_index": i, "region_id": row["region_id"],
                                     "primary_mean_db": float(primary[i]), "model_disagreement_sd_db": float(disagreement[i]),
                                     "probability_below_threshold": float(runner._predict_records(models[f"{target}_2021_cnossos_inspired"], [row])["probability_below_threshold"][0]),
                                     "censored": int(row[f"{target}_censored"])})
    runner.write_csv(output_root / "uncertainty_sample.csv", list(uncertainty_rows[0]), uncertainty_rows)
    runner.write_json(output_root / "uncertainty_summary.json", {"summary": uncertainty_summary,
                                                                    "note": "Model disagreement and fitted residual intervals are uncertainty diagnostics, not calibrated measurement error."})

    home_rows = []
    home_summaries = []
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        pred = runner._predict_records(models[f"{target}_2021_cnossos_inspired"], representative)
        for radius in ("exact", "100m", "250m"):
            rows, summary = runner._rank_summary(representative, pred, target, threshold, radius)
            home_rows.extend(rows)
            summary.update({"model": "cnossos_inspired", "target": target})
            home_summaries.append(summary)
    runner.write_csv(output_root / "quiet_home_cases.csv", list(home_rows[0]) if home_rows else ["radius"], home_rows)
    runner.write_json(output_root / "quiet_home_summary.json", {"summaries": home_summaries,
                                                                   "note": "This is a ranking diagnostic on reported cells, not property-level validation."})

    hiking_rows = []
    hiking_summary = []
    profile_features = {}
    for region in runner.PHASE2B_REGIONS:
        for profile in ("horizontal", "vertical", "diagonal"):
            xs, ys = runner._profile_coordinates(region, profile)
            profile_features[(region.region_id, profile)] = (xs, ys, runner.build_phase2b_features(
                xs, ys, source_cache[region.region_id][2021], use_speed=True, radius=5_000.0))
    for target, threshold in (("lden", lden_threshold), ("lnight", lnight_threshold)):
        model = models[f"{target}_2021_cnossos_inspired"]
        for region in runner.PHASE2B_REGIONS:
            for profile in ("horizontal", "vertical", "diagonal"):
                rows = runner._profile_rows(region, source_cache[region.region_id][2021], model, target, threshold, profile,
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
                                       "longest_quiet_segment_m": int(longest * 100), "threshold_db": threshold})
    runner.write_csv(output_root / "quiet_hiking_profiles.csv", list(hiking_rows[0]), hiking_rows)
    runner.write_csv(output_root / "quiet_hiking_summary.csv", list(hiking_summary[0]), hiking_summary)

    figure_rows = [row for row in validation_rows if row.get("fold") == "all_spatial_holdouts" and row.get("target") == "lden" and row.get("design") == "representative" and row.get("year") == 2021]
    fig, ax = plt.subplots(figsize=(10, 5), constrained_layout=True)
    ax.bar([row["model"] for row in figure_rows], [row["rmse_db"] for row in figure_rows], color="tab:blue")
    ax.set_ylabel("Weighted spatial-holdout RMSE (dB)")
    ax.set_title("Phase 2B Lden model comparison; representative 2021 traffic")
    ax.tick_params(axis="x", rotation=30)
    fig.savefig(output_root / "model_comparison.png", dpi=150)
    plt.close(fig)

    manifest_path = output_root / "phase2b_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest["model_recalibrated_with_explicit_intercept"] = True
    manifest["thresholds"] = thresholds
    manifest["outputs"] = {"receptor_sample": str(output_root / "receptor_sample.csv"),
                            "validation_metrics": str(output_root / "validation_metrics.csv"),
                            "temporal_ablation": str(output_root / "temporal_ablation_2021_vs_2025.csv"),
                            "model_comparison": str(output_root / "model_comparison.png")}
    runner.write_json(manifest_path, manifest)
    print(json.dumps({"records": len(records), "recalibrated": True, "lden_threshold_db": lden_threshold,
                      "lnight_threshold_db": lnight_threshold}, indent=2))


if __name__ == "__main__":
    main()
