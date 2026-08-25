"""Bounded, censor-aware Phase 2A road-noise prototype.

This module deliberately stays separate from the validated Phase 1 tile
pipeline.  It builds receptor-level predictors from DfT road links/count
points, optionally samples an Environment Agency terrain raster, and fits
interpretable left-censored (Tobit-style) models.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import requests
import shapefile
from scipy.optimize import minimize
from scipy.special import ndtr
from shapely import distance as shapely_distance
from shapely import points as shapely_points
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

import rasterio
from rasterio.transform import Affine

from .raster import read_single_band_db
from .wcs import get_coverage, get_coverage_wcs20


ROAD_WCS_URL = "https://environment.data.gov.uk/spatialdata/road-noise-all-metrics-england-round-4/wcs"
ROAD_COVERAGE_ID = "562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All"
DTM_WCS_URL = "https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-2m/wcs"
DTM_COVERAGE_ID = "09ea3b37-df3a-4e8b-ac69-fb0842227b04__Lidar_Composite_Elevation_DTM_2m"
DFT_AADF_URL = "https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_aadf.zip"
DFT_MRDB_URL = "https://storage.googleapis.com/dft-statistics/road-traffic/mrdb-2025.zip"
DFT_DOWNLOAD_PAGE = "https://roadtraffic.dft.gov.uk/downloads"
DFT_API_DOCUMENTATION = "https://roadtraffic.dft.gov.uk/api-documentation"
OS_OPEN_ROADS_PAGE = "https://osdatahub.os.uk/downloads/open/OpenRoads"
EA_DTM_PAGE = "https://environment.data.gov.uk/dataset/ce8fe7e7-bed0-4889-8825-19b042e128d2"
EA_DTM_2M_WCS_NOTICE = "https://environment.data.gov.uk/support/announcements/275811447/275811543"

REPORTING_THRESHOLD_DB = 40.0


@dataclass(frozen=True)
class PrototypeRegion:
    region_id: str
    label: str
    bbox: tuple[int, int, int, int]
    landscape: str


PROTOTYPE_REGIONS = (
    PrototypeRegion("heathrow_london", "Heathrow / outer London", (503000, 171000, 513000, 181000), "urban, motorway, aviation-context QA"),
    PrototypeRegion("birmingham_m42", "Birmingham / M42", (400000, 275000, 410000, 285000), "dense urban and motorway"),
    PrototypeRegion("manchester_m60", "Manchester / M60", (380000, 390000, 390000, 400000), "dense urban and motorway"),
    PrototypeRegion("norfolk_flat", "Norfolk flat rural", (620000, 300000, 630000, 310000), "flat countryside and rural minor roads"),
    PrototypeRegion("south_downs", "South Downs fringe", (520000, 110000, 530000, 120000), "rural A-road and rolling countryside"),
    PrototypeRegion("peak_district", "Peak District", (410000, 385000, 420000, 395000), "hilly countryside and National Park context"),
    PrototypeRegion("north_york_moors", "North York Moors fringe", (480000, 470000, 490000, 480000), "hilly rural and remote roads"),
)


BASE_FEATURES = (
    "log1p_nearest_distance_m",
    "log10_traffic_energy_250m",
    "log10_traffic_energy_1000m",
    "log10_hgv_energy_1000m",
    "log1p_nearest_motorway_m",
    "log1p_nearest_a_road_m",
    "log1p_nearest_b_road_m",
    "log1p_nearest_minor_m",
    "nearest_hgv_share",
    "nearest_is_counted",
)
TERRAIN_FEATURES = (
    "receptor_elevation_m",
    "elevation_difference_m",
    "terrain_obstruction_max_m",
    "terrain_los_blocked",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_extract(zip_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        root = output_dir.resolve()
        for member in archive.infolist():
            target = (output_dir / member.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe archive member: {member.filename}")
        archive.extractall(output_dir)


def _download(url: str, path: Path, timeout: int = 180) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    path.write_bytes(response.content)


def ensure_dft_inputs(raw_root: str | Path, timeout: int = 180) -> dict:
    """Download and unpack the official DfT 2025 traffic/geometry inputs once."""
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    aadf_zip = raw_root / "dft_traffic_counts_aadf.zip"
    mrdb_zip = raw_root / "dft_mrdb_2025.zip"
    if not aadf_zip.exists():
        _download(DFT_AADF_URL, aadf_zip, timeout)
    if not mrdb_zip.exists():
        _download(DFT_MRDB_URL, mrdb_zip, timeout)
    aadf_dir = raw_root / "aadf"
    mrdb_dir = raw_root / "mrdb_2025"
    aadf_csv = aadf_dir / "dft_traffic_counts_aadf.csv"
    mrdb_shp = mrdb_dir / "MRDB_2025_published2.shp"
    if not aadf_csv.exists():
        _safe_extract(aadf_zip, aadf_dir)
    if not mrdb_shp.exists():
        _safe_extract(mrdb_zip, mrdb_dir)
    if not aadf_csv.exists() or not mrdb_shp.exists():
        raise FileNotFoundError("DfT archive did not contain expected traffic/geometry files")
    return {
        "aadf_csv": str(aadf_csv),
        "mrdb_shp": str(mrdb_shp),
        "aadf_url": DFT_AADF_URL,
        "mrdb_url": DFT_MRDB_URL,
        "retrieved_at": utc_now(),
    }


def load_dft_aadf_2025(csv_path: str | Path) -> list[dict]:
    """Load one current DfT annual-flow record per 2025 road link."""
    rows: list[dict] = []
    with Path(csv_path).open(newline="", encoding="utf-8-sig") as stream:
        for raw in csv.DictReader(stream):
            if str(raw.get("year", "")) != "2025":
                continue
            def number(name: str, default: float = 0.0) -> float:
                value = raw.get(name, "")
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return default
            row = dict(raw)
            row.update({
                "count_point_id": str(raw["count_point_id"]),
                "easting": number("easting"),
                "northing": number("northing"),
                "all_motor_vehicles": number("all_motor_vehicles"),
                "all_HGVs": number("all_HGVs"),
                "cars_and_taxis": number("cars_and_taxis"),
                "LGVs": number("LGVs"),
                "buses_and_coaches": number("buses_and_coaches"),
                "estimation_method": raw.get("estimation_method", "Unknown"),
            })
            rows.append(row)
    if not rows:
        raise ValueError("No 2025 DfT AADF rows were found")
    return rows


def _road_class(road_name: str, road_type: str) -> str:
    name = (road_name or "").strip().upper()
    if name.startswith("M"):
        return "motorway"
    if name.startswith("A"):
        return "a_road"
    if name.startswith("B"):
        return "b_road"
    return "major_other" if (road_type or "").lower() == "major" else "minor"


def _source_record(row: dict, geometry, geometry_kind: str) -> dict:
    flow = max(0.0, float(row.get("all_motor_vehicles", 0.0)))
    hgv = max(0.0, float(row.get("all_HGVs", 0.0)))
    return {
        "count_point_id": str(row["count_point_id"]),
        "geometry": geometry,
        "geometry_kind": geometry_kind,
        "road_name": row.get("road_name", ""),
        "road_type": row.get("road_type", ""),
        "road_class": _road_class(row.get("road_name", ""), row.get("road_type", "")),
        "flow": flow,
        "hgv_flow": hgv,
        "hgv_share": hgv / flow if flow > 0 else 0.0,
        "estimation_method": row.get("estimation_method", "Unknown"),
        "is_counted": 1.0 if row.get("estimation_method", "").lower() == "counted" else 0.0,
    }


def load_road_sources(aadf_rows: list[dict], mrdb_path: str | Path,
                      bbox: tuple[float, float, float, float],
                      margin_m: float = 1_500.0) -> list[dict]:
    """Join DfT 2025 traffic rows to MRDB major-link geometry.

    Major links use the official DfT geometry keyed by count-point number.
    Minor links have no corresponding MRDB geometry in the published file, so
    their DfT count-point coordinates are retained as explicit point sources
    rather than silently dropping rural/minor-road traffic.
    """
    minx, miny, maxx, maxy = bbox
    expanded = (minx - margin_m, miny - margin_m, maxx + margin_m, maxy + margin_m)
    by_id = {row["count_point_id"]: row for row in aadf_rows}
    major_ids: set[str] = set()
    sources: list[dict] = []
    with shapefile.Reader(str(mrdb_path)) as reader:
        for shape, record in zip(reader.iterShapes(), reader.iterRecords()):
            cp = str(int(float(record[0])))
            row = by_id.get(cp)
            if row is None:
                continue
            sx0, sy0, sx1, sy1 = shape.bbox
            if sx1 < expanded[0] or sx0 > expanded[2] or sy1 < expanded[1] or sy0 > expanded[3]:
                continue
            if len(shape.points) < 2:
                continue
            geometry = LineString(shape.points)
            sources.append(_source_record(row, geometry, "MRDB_2025_line"))
            major_ids.add(cp)
    for row in aadf_rows:
        if row["count_point_id"] in major_ids or row.get("road_type", "").lower() == "major":
            if row["count_point_id"] in major_ids:
                continue
        x, y = row["easting"], row["northing"]
        if expanded[0] <= x <= expanded[2] and expanded[1] <= y <= expanded[3]:
            sources.append(_source_record(row, Point(x, y), "DfT_count_point"))
    if not sources:
        raise ValueError(f"No DfT road sources found near bbox {bbox}")
    return sources


def _sample_raster(array: np.ndarray, transform: Affine, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    cols = np.floor((xs - transform.c) / transform.a).astype(int)
    rows = np.floor((transform.f - ys) / abs(transform.e)).astype(int)
    out = np.full(xs.shape, np.nan, dtype="float64")
    inside = (rows >= 0) & (rows < array.shape[0]) & (cols >= 0) & (cols < array.shape[1])
    out[inside] = array[rows[inside], cols[inside]]
    return out


def _nearest_for_class(points, sources: list[dict], classes: np.ndarray, class_name: str,
                       default_distance: float) -> tuple[np.ndarray, np.ndarray]:
    selected = np.flatnonzero(classes == class_name)
    n = len(points)
    distances = np.full(n, default_distance, dtype="float64")
    source_indices = np.full(n, -1, dtype=int)
    if len(selected) == 0:
        return distances, source_indices
    tree = STRtree([sources[i]["geometry"] for i in selected])
    pairs, dists = tree.query_nearest(points, all_matches=False, return_distance=True)
    query_index = pairs[0]
    local_index = pairs[1]
    distances[query_index] = dists
    source_indices[query_index] = selected[local_index]
    return distances, source_indices


def _energy_sums(points, geometries: np.ndarray, flows: np.ndarray,
                 hgv_flows: np.ndarray, radius: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    tree = STRtree(list(geometries))
    n = len(points)
    result = np.zeros(n, dtype="float64")
    hgv_result = np.zeros(n, dtype="float64")
    counts = np.zeros(n, dtype="float64")
    if len(geometries) == 0:
        return result, hgv_result, counts
    pairs = tree.query(points, predicate="dwithin", distance=float(radius))
    if pairs.size == 0:
        return result, hgv_result, counts
    receptor_index, source_index = pairs
    dists = np.asarray(shapely_distance(points[receptor_index], geometries[source_index]), dtype="float64")
    denominator = np.square(np.maximum(dists, 10.0))
    np.add.at(result, receptor_index, flows[source_index] / denominator)
    np.add.at(hgv_result, receptor_index, hgv_flows[source_index] / denominator)
    np.add.at(counts, receptor_index, 1.0)
    return result, hgv_result, counts


def build_features(xs: np.ndarray, ys: np.ndarray, sources: list[dict],
                   dtm: np.ndarray | None = None,
                   dtm_transform: Affine | None = None,
                   include_terrain: bool = True) -> dict[str, np.ndarray]:
    """Build physically motivated road-source features for receptor points."""
    xs = np.asarray(xs, dtype="float64")
    ys = np.asarray(ys, dtype="float64")
    if xs.shape != ys.shape:
        raise ValueError("xs and ys must have identical shapes")
    if not sources:
        raise ValueError("At least one road source is required")
    points = shapely_points(xs, ys)
    geometries = np.asarray([source["geometry"] for source in sources], dtype=object)
    flows = np.asarray([source["flow"] for source in sources], dtype="float64")
    hgv_flows = np.asarray([source["hgv_flow"] for source in sources], dtype="float64")
    hgv_share = np.asarray([source["hgv_share"] for source in sources], dtype="float64")
    counted = np.asarray([source["is_counted"] for source in sources], dtype="float64")
    classes = np.asarray([source["road_class"] for source in sources], dtype=object)
    tree = STRtree(list(geometries))
    nearest_pairs, nearest_dists = tree.query_nearest(points, all_matches=False, return_distance=True)
    nearest_source = np.full(len(points), -1, dtype=int)
    nearest_source[nearest_pairs[0]] = nearest_pairs[1]
    nearest_distance = np.full(len(points), 5_000.0, dtype="float64")
    nearest_distance[nearest_pairs[0]] = nearest_dists

    features: dict[str, np.ndarray] = {
        "log1p_nearest_distance_m": np.log1p(nearest_distance),
        "nearest_hgv_share": hgv_share[nearest_source.clip(min=0)],
        "nearest_is_counted": counted[nearest_source.clip(min=0)],
        "nearest_road_class": classes[nearest_source.clip(min=0)],
        "nearest_flow_aadf": flows[nearest_source.clip(min=0)],
    }
    for class_name, feature_name in (
        ("motorway", "motorway"), ("a_road", "a_road"),
        ("b_road", "b_road"), ("minor", "minor"),
    ):
        distance_values, _ = _nearest_for_class(points, sources, classes, class_name, 5_000.0)
        features[f"log1p_nearest_{feature_name}_m"] = np.log1p(distance_values)

    for radius in (250, 1_000, 5_000, 10_000):
        flow_energy, hgv_energy, counts = _energy_sums(points, geometries, flows, hgv_flows, radius)
        features[f"log10_traffic_energy_{radius}m"] = np.log10(np.maximum(flow_energy, 1e-9))
        if radius == 1_000:
            features["log10_hgv_energy_1000m"] = np.log10(np.maximum(hgv_energy, 1e-9))
        if radius == 5_000:
            features["log10_hgv_energy_5000m"] = np.log10(np.maximum(hgv_energy, 1e-9))
        if radius == 10_000:
            features["log10_hgv_energy_10000m"] = np.log10(np.maximum(hgv_energy, 1e-9))
        features[f"road_count_{radius}m"] = counts

    if include_terrain:
        if dtm is None or dtm_transform is None:
            raise ValueError("Terrain features requested without a DTM raster/transform")
        receptor_elevation = _sample_raster(dtm, dtm_transform, xs, ys)
        mid_x = np.asarray([
            sources[index]["geometry"].centroid.x for index in nearest_source.clip(min=0)
        ], dtype="float64")
        mid_y = np.asarray([
            sources[index]["geometry"].centroid.y for index in nearest_source.clip(min=0)
        ], dtype="float64")
        road_elevation = _sample_raster(dtm, dtm_transform, mid_x, mid_y)
        features["receptor_elevation_m"] = receptor_elevation
        features["elevation_difference_m"] = receptor_elevation - road_elevation
        fractions = np.linspace(0.1, 0.9, 9)
        obstruction = np.full(len(points), np.nan, dtype="float64")
        for fraction in fractions:
            line_x = xs * (1.0 - fraction) + mid_x * fraction
            line_y = ys * (1.0 - fraction) + mid_y * fraction
            terrain = _sample_raster(dtm, dtm_transform, line_x, line_y)
            straight = receptor_elevation * (1.0 - fraction) + road_elevation * fraction
            excess = terrain - straight
            obstruction = np.fmax(obstruction, excess)
        features["terrain_obstruction_max_m"] = obstruction
        features["terrain_los_blocked"] = (obstruction > 2.0).astype("float64")
    return features


def _feature_matrix(features: dict[str, np.ndarray], names: Iterable[str]) -> tuple[np.ndarray, np.ndarray]:
    names = list(names)
    columns = []
    valid = np.ones(len(next(iter(features.values()))), dtype=bool)
    for name in names:
        if name not in features:
            raise KeyError(f"Missing feature: {name}")
        values = np.asarray(features[name], dtype="float64")
        valid &= np.isfinite(values)
        columns.append(values)
    return np.column_stack(columns), valid


def standardize_train_test(x_train: np.ndarray, x_test: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    mean = np.nanmean(x_train, axis=0)
    scale = np.nanstd(x_train, axis=0)
    scale[~np.isfinite(scale) | (scale < 1e-8)] = 1.0
    return (np.column_stack([np.ones(len(x_train)), (x_train - mean) / scale]),
            np.column_stack([np.ones(len(x_test)), (x_test - mean) / scale]), mean, scale)


def fit_tobit(x: np.ndarray, y: np.ndarray, censored: np.ndarray,
              threshold: float = REPORTING_THRESHOLD_DB) -> dict:
    """Fit a Gaussian left-censored regression by maximum likelihood."""
    x = np.asarray(x, dtype="float64")
    y = np.asarray(y, dtype="float64")
    censored = np.asarray(censored, dtype=bool)
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(censored):
        raise ValueError("x, y and censored shapes do not agree")
    observed = ~censored & np.isfinite(y)
    if observed.sum() < max(20, x.shape[1] + 2):
        raise ValueError("Too few observed cells for censored regression")
    beta0 = np.zeros(x.shape[1], dtype="float64")
    beta0[0] = float(np.nanmean(y[observed]))
    if x.shape[1]:
        beta0[1:] = 0.0
    sigma0 = max(1.0, float(np.nanstd(y[observed])))
    start = np.r_[beta0, math.log(sigma0)]
    log2pi = math.log(2.0 * math.pi)

    def objective(params):
        beta = params[:-1]
        sigma = math.exp(float(np.clip(params[-1], -5.0, 5.0)))
        mu = x @ beta
        z = (threshold - mu) / sigma
        ll = np.zeros(len(y), dtype="float64")
        residual = (y[observed] - mu[observed]) / sigma
        ll[observed] = -0.5 * (residual * residual + log2pi) - math.log(sigma)
        cens = ~observed
        ll[cens] = np.log(np.maximum(ndtr(z[cens]), 1e-15))
        value = -float(np.sum(ll))
        return value if np.isfinite(value) else 1e100

    result = minimize(objective, start, method="L-BFGS-B",
                      bounds=[(None, None)] * x.shape[1] + [(-5.0, 5.0)])
    if not result.success:
        raise RuntimeError(f"Tobit optimization failed: {result.message}")
    return {
        "beta": result.x[:-1],
        "sigma_db": math.exp(float(result.x[-1])),
        "threshold_db": float(threshold),
        "nll": float(result.fun),
        "success": bool(result.success),
        "iterations": int(result.nit),
    }


def predict_tobit(model: dict, x: np.ndarray) -> dict[str, np.ndarray]:
    x = np.asarray(x, dtype="float64")
    mu = x @ np.asarray(model["beta"], dtype="float64")
    sigma = float(model["sigma_db"])
    threshold = float(model["threshold_db"])
    probability_below = ndtr((threshold - mu) / sigma)
    return {
        "mu_db": mu,
        "sigma_db": np.full(len(mu), sigma, dtype="float64"),
        "probability_below_threshold": probability_below,
        "interval80_low_db": mu - 1.2815515655 * sigma,
        "interval80_high_db": mu + 1.2815515655 * sigma,
    }


def fit_observed_only_ols(x: np.ndarray, y: np.ndarray, censored: np.ndarray) -> dict:
    """Diagnostic-only truncated-target OLS; never use as the production model."""
    observed = ~np.asarray(censored, dtype=bool) & np.isfinite(y)
    beta, *_ = np.linalg.lstsq(np.asarray(x)[observed], np.asarray(y)[observed], rcond=None)
    residuals = np.asarray(y)[observed] - np.asarray(x)[observed] @ beta
    return {"beta": beta, "sigma_db": float(np.std(residuals)), "n_observed": int(observed.sum())}


def metrics_for_predictions(y: np.ndarray, censored: np.ndarray, prediction: dict) -> dict:
    y = np.asarray(y, dtype="float64")
    censored = np.asarray(censored, dtype=bool)
    mu = np.asarray(prediction["mu_db"], dtype="float64")
    observed = ~censored & np.isfinite(y)
    if observed.any():
        errors = mu[observed] - y[observed]
        near = observed & (y >= 40.0) & (y < 45.0)
        near_errors = mu[near] - y[near]
        reported = {
            "n": int(observed.sum()),
            "mae_db": float(np.mean(np.abs(errors))),
            "rmse_db": float(np.sqrt(np.mean(errors * errors))),
            "median_absolute_error_db": float(np.median(np.abs(errors))),
            "bias_db": float(np.mean(errors)),
            "near_40_45_n": int(near.sum()),
            "near_40_45_mae_db": float(np.mean(np.abs(near_errors))) if near.any() else None,
            "near_40_45_rmse_db": float(np.sqrt(np.mean(near_errors * near_errors))) if near.any() else None,
            "near_40_45_bias_db": float(np.mean(near_errors)) if near.any() else None,
        }
    else:
        reported = {"n": 0, "mae_db": None, "rmse_db": None, "median_absolute_error_db": None, "bias_db": None,
                    "near_40_45_n": 0, "near_40_45_mae_db": None, "near_40_45_rmse_db": None, "near_40_45_bias_db": None}
    cens = censored & np.isfinite(mu)
    reported.update({
        "censored_n": int(cens.sum()),
        "censored_predicted_below_fraction": float(np.mean(mu[cens] < 40.0)) if cens.any() else None,
        "censored_violation_fraction": float(np.mean(mu[cens] >= 40.0)) if cens.any() else None,
        "censored_p_below_median": float(np.median(prediction["probability_below_threshold"][cens])) if cens.any() else None,
    })
    return reported


def write_csv(path: str | Path, fieldnames: list[str], rows: Iterable[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    def default(value):
        if isinstance(value, np.ndarray):
            return value.tolist()
        if isinstance(value, np.generic):
            return value.item()
        return str(value)
    path.write_text(json.dumps(payload, indent=2, default=default), encoding="utf-8")


def load_dtm(path: str | Path) -> tuple[np.ndarray, Affine, dict]:
    with rasterio.open(path) as dataset:
        raw = dataset.read(1).astype("float64")
        nodata = dataset.nodata
        if nodata is not None:
            raw[raw == float(nodata)] = np.nan
        raw[~np.isfinite(raw)] = np.nan
        return raw, dataset.transform, {
            "shape": list(raw.shape), "crs": str(dataset.crs),
            "transform": list(dataset.transform), "nodata": nodata,
            "valid_cells": int(np.isfinite(raw).sum()),
        }


def download_region_rasters(output_dir: str | Path, regions: Iterable[PrototypeRegion],
                            timeout: int = 180, refresh: bool = False) -> dict:
    """Retrieve road Lden and 10 m-output EA DTM rasters for prototype regions."""
    output_dir = Path(output_dir)
    road_dir = output_dir / "raw" / "road_10m"
    dtm_dir = output_dir / "raw" / "dtm_10m_from_ea_2m_wcs"
    road_dir.mkdir(parents=True, exist_ok=True)
    dtm_dir.mkdir(parents=True, exist_ok=True)
    inventory = {}
    for region in regions:
        road_path = road_dir / f"{region.region_id}.tif"
        dtm_path = dtm_dir / f"{region.region_id}.tif"
        if refresh or not road_path.exists():
            road_bytes = get_coverage(ROAD_WCS_URL, ROAD_COVERAGE_ID, region.bbox, 1000, 1000,
                                      crs="EPSG:27700", version="1.0.0", format_="GeoTIFF", timeout=timeout)
            road_path.write_bytes(road_bytes)
        if refresh or not dtm_path.exists():
            dtm_bytes = get_coverage_wcs20(DTM_WCS_URL, DTM_COVERAGE_ID, region.bbox, 1000, 1000,
                                           crs="EPSG:27700", format_="image/tiff", timeout=timeout,
                                           padding_cells=0)
            dtm_path.write_bytes(dtm_bytes)
        with rasterio.open(road_path) as road:
            road_grid = {"shape": list(road.shape), "crs": str(road.crs), "transform": list(road.transform),
                         "bounds": list(road.bounds), "nodata": road.nodata}
        with rasterio.open(dtm_path) as dtm:
            dtm_grid = {"shape": list(dtm.shape), "crs": str(dtm.crs), "transform": list(dtm.transform),
                        "bounds": list(dtm.bounds), "nodata": dtm.nodata}
        inventory[region.region_id] = {
            "label": region.label, "bbox_epsg27700": list(region.bbox),
            "road_path": str(road_path), "dtm_path": str(dtm_path),
            "road_grid": road_grid, "dtm_grid": dtm_grid,
        }
    return inventory
