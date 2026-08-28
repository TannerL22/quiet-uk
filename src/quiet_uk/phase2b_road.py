"""Bounded Phase 2B road-model reconstruction experiment.

This module is deliberately separate from :mod:`quiet_uk.phase2_road`.
It adds year-controlled traffic inputs, representative/weighted censoring,
OS Open Roads continuous geometry, an explicitly simplified CNOSSOS-inspired
line-source proxy, and the parallel Defra Lnight target.  It is research code,
not a claim of CNOSSOS-EU compliance and not a national production pipeline.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import struct
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
from shapely import wkb
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

import rasterio
from rasterio.transform import Affine

from .phase2_road import PROTOTYPE_REGIONS, PrototypeRegion
from .raster import read_single_band_db
from .wcs import get_coverage


ROAD_WCS_URL = "https://environment.data.gov.uk/spatialdata/road-noise-all-metrics-england-round-4/wcs"
ROAD_LDEN_COVERAGE_ID = "562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All"
ROAD_LNIGHT_COVERAGE_ID = "562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lnight_England_Round_4_All"
DFT_AADF_ALL_YEARS_URL = "https://storage.googleapis.com/dft-statistics/road-traffic/downloads/data-gov-uk/dft_traffic_counts_aadf.zip"
DFT_MRDB_URLS = {
    2021: "https://storage.googleapis.com/dft-statistics/road-traffic/mrdb-2021.zip",
    2025: "https://storage.googleapis.com/dft-statistics/road-traffic/mrdb-2025.zip",
}
DFT_DOWNLOAD_PAGE = "https://roadtraffic.dft.gov.uk/downloads"
DFT_API_DOCUMENTATION = "https://roadtraffic.dft.gov.uk/api-documentation"
OS_OPEN_ROADS_PAGE = "https://osdatahub.os.uk/downloads/open/OpenRoads"
OS_OPEN_ROADS_DOCUMENTATION = "https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads"
OS_OPEN_ROADS_API = "https://api.os.uk/downloads/v1/products/OpenRoads/downloads?area=GB&format=GeoPackage&redirect"
CNOSSOS_REFERENCE_URL = "https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf"
OS_OPEN_ROADS_RELEASE = "2026-04"
EPSG = "EPSG:27700"


@dataclass(frozen=True)
class Phase2BRegion:
    region_id: str
    label: str
    bbox: tuple[int, int, int, int]
    landscape: str
    urban_rural: str


PHASE2B_REGIONS = (
    Phase2BRegion("heathrow_london", "Heathrow / outer London", (503000, 171000, 513000, 181000), "dense London, motorway and aviation context", "urban"),
    Phase2BRegion("birmingham_m42", "Birmingham / M42", (400000, 275000, 410000, 285000), "dense urban and motorway", "urban"),
    Phase2BRegion("manchester_m60", "Manchester / M60", (380000, 390000, 390000, 400000), "dense urban and motorway", "urban"),
    Phase2BRegion("suburban_leeds", "Leeds suburban fringe", (425000, 430000, 435000, 440000), "suburban arterial and local roads", "suburban"),
    Phase2BRegion("norfolk_flat", "Norfolk flat rural", (620000, 300000, 630000, 310000), "flat rural minor-road countryside", "rural"),
    Phase2BRegion("south_downs", "South Downs fringe", (520000, 110000, 530000, 120000), "rural A/B roads and rolling terrain", "rural"),
    Phase2BRegion("peak_district", "Peak District", (410000, 385000, 420000, 395000), "hilly National Park context", "rural"),
    Phase2BRegion("north_york_moors", "North York Moors fringe", (480000, 470000, 490000, 480000), "hilly rural and remote roads", "rural"),
    Phase2BRegion("east_anglia_coast", "East Anglia coastal rural", (640000, 250000, 650000, 260000), "coastal rural roads", "rural"),
    Phase2BRegion("northumberland_remote", "Northumberland remote", (390000, 600000, 400000, 610000), "remote northern countryside", "rural"),
)


# These are intentionally labelled as simplified proxy parameters.  The
# published CNOSSOS road equations use category/speed/frequency coefficients;
# this prototype keeps only the rolling logarithmic speed and propulsion
# speed-response structure and uses uncalibrated relative A-weighted constants.
CNOSSOS_PROXY_PARAMETERS = {
    "light": {"A_R": 75.0, "B_R": 30.0, "A_P": 70.0, "B_P": 0.50},
    "hgv": {"A_R": 80.0, "B_R": 30.0, "A_P": 85.0, "B_P": 0.50},
}
CLASS_REFERENCE_SPEED_KMH = {
    "motorway": 100.0,
    "a_road": 80.0,
    "b_road": 60.0,
    "minor": 40.0,
    "unknown": 50.0,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _download(url: str, path: Path, timeout: int = 300) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    with requests.get(url, stream=True, timeout=timeout) as response:
        response.raise_for_status()
        with temporary.open("wb") as stream:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    stream.write(chunk)
    temporary.replace(path)


def _safe_extract(zip_path: Path, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    root = output_dir.resolve()
    with zipfile.ZipFile(zip_path) as archive:
        for member in archive.infolist():
            target = (output_dir / member.filename).resolve()
            if root not in target.parents and target != root:
                raise ValueError(f"Unsafe archive member: {member.filename}")
        archive.extractall(output_dir)


def ensure_phase2b_inputs(raw_root: str | Path, timeout: int = 300) -> dict:
    """Resolve exact year-controlled DfT and OS inputs; never fall back by year."""
    raw_root = Path(raw_root)
    raw_root.mkdir(parents=True, exist_ok=True)
    aadf_zip = raw_root / "dft_traffic_counts_aadf.zip"
    if not aadf_zip.exists():
        # The current DfT all-years archive is the official file containing the
        # historical 2021 rows.  The loader below requires year 2021 explicitly.
        _download(DFT_AADF_ALL_YEARS_URL, aadf_zip, timeout)
    aadf_dir = raw_root / "aadf"
    aadf_csv = next(aadf_dir.glob("**/*aadf*.csv"), None) if aadf_dir.exists() else None
    if aadf_csv is None:
        _safe_extract(aadf_zip, aadf_dir)
        aadf_csv = next(aadf_dir.glob("**/*aadf*.csv"), None)
    if aadf_csv is None:
        raise FileNotFoundError("The DfT AADF archive did not contain an AADF CSV")

    mrdb = {}
    for year, url in DFT_MRDB_URLS.items():
        archive = raw_root / f"dft_mrdb_{year}.zip"
        if not archive.exists():
            _download(url, archive, timeout)
        directory = raw_root / f"mrdb_{year}"
        shp = next(directory.glob("**/*.shp"), None) if directory.exists() else None
        if shp is None:
            _safe_extract(archive, directory)
            shp = next(directory.glob("**/*.shp"), None)
        if shp is None:
            raise FileNotFoundError(f"No shapefile in DfT MRDB {year} archive")
        mrdb[year] = {"url": url, "archive": str(archive), "shp": str(shp)}

    os_zip = raw_root / f"os_open_roads_gb_{OS_OPEN_ROADS_RELEASE}.zip"
    if not os_zip.exists():
        existing_os_archives = sorted(raw_root.glob(f"os_open_roads_gb_{OS_OPEN_ROADS_RELEASE}*.zip"))
        if existing_os_archives:
            os_zip = existing_os_archives[-1]
    os_dir = raw_root / "os_open_roads"
    os_gpkg = os_dir / "Data" / "oproad_gb.gpkg"
    if not os_gpkg.exists():
        if not os_zip.exists():
            _download(OS_OPEN_ROADS_API, os_zip, timeout)
        _safe_extract(os_zip, os_dir)
    if not os_gpkg.exists():
        raise FileNotFoundError("OS Open Roads archive did not contain Data/oproad_gb.gpkg")
    return {"aadf_csv": str(aadf_csv), "mrdb": mrdb, "os_gpkg": str(os_gpkg), "retrieved_at": utc_now()}


def load_dft_aadf_year(csv_path: str | Path, year: int) -> list[dict]:
    """Load one exact DfT AADF year and fail if it is not present."""
    rows: list[dict] = []
    with Path(csv_path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"count_point_id", "year", "all_motor_vehicles", "all_HGVs", "easting", "northing", "estimation_method"}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"DfT AADF is missing required fields: {sorted(missing)}")
        for raw in reader:
            if str(raw["year"]).strip() != str(year):
                continue
            flow = float(raw["all_motor_vehicles"] or 0.0)
            hgv = float(raw["all_HGVs"] or 0.0)
            if flow <= 0 or not raw["easting"] or not raw["northing"]:
                continue
            rows.append({
                "year": year,
                "count_point_id": str(raw["count_point_id"]).strip(),
                "road_name": raw.get("road_name", ""),
                "road_category": raw.get("road_category", ""),
                "road_type": raw.get("road_type", ""),
                "easting": float(raw["easting"]),
                "northing": float(raw["northing"]),
                "flow": flow,
                "hgv_flow": hgv,
                "hgv_share": min(1.0, max(0.0, hgv / flow)),
                "estimation_method": raw.get("estimation_method", "Unknown"),
                "estimation_method_detailed": raw.get("estimation_method_detailed", ""),
            })
    if not rows:
        raise ValueError(f"No exact DfT AADF rows found for year={year}; refusing fallback")
    return rows


def _road_class(name: str, category: str = "") -> str:
    value = (name or "").strip().upper().replace(" ", "")
    if value.startswith("M") and len(value) > 1 and value[1:].replace("A", "").isdigit():
        return "motorway"
    if value.startswith("A"):
        return "a_road"
    if value.startswith("B"):
        return "b_road"
    cat = (category or "").lower()
    if "motor" in cat:
        return "motorway"
    if "major" in cat or cat.startswith("a"):
        return "a_road"
    return "minor"


def _shape_line(shape) -> LineString:
    points = list(shape.points)
    if len(points) < 2:
        return LineString()
    return LineString(points)


def load_dft_mrdb_sources(aadf_rows: list[dict], mrdb_path: str | Path,
                          bbox: tuple[float, float, float, float], margin_m: float = 15_000.0) -> list[dict]:
    """Join one DfT year to its same-year MRDB geometry."""
    minx, miny, maxx, maxy = bbox
    index = {row["count_point_id"]: row for row in aadf_rows}
    sources = []
    reader = shapefile.Reader(str(mrdb_path))
    fields = [field[0] for field in reader.fields[1:]]
    for shape_record in reader.iterShapeRecords():
        values = dict(zip(fields, shape_record.record))
        points = shape_record.shape.points
        if not points:
            continue
        sx = [p[0] for p in points]
        sy = [p[1] for p in points]
        if max(sx) < minx - margin_m or min(sx) > maxx + margin_m or max(sy) < miny - margin_m or min(sy) > maxy + margin_m:
            continue
        cp = str(int(float(values.get("CP_Number"))))
        row = index.get(cp)
        if row is None:
            continue
        geometry = _shape_line(shape_record.shape)
        if geometry.is_empty:
            continue
        sources.append({
            "geometry": geometry,
            "flow": row["flow"],
            "hgv_flow": row["hgv_flow"],
            "hgv_share": row["hgv_share"],
            "road_class": _road_class(row["road_name"], row["road_category"]),
            "road_name": row["road_name"],
            "road_id": row["count_point_id"],
            "traffic_source": "counted" if row["estimation_method"].lower() == "counted" else "estimated",
            "traffic_confidence": row["estimation_method"],
            "hgv_confidence": "direct_DfT",
            "geometry_kind": "DfT_MRDB_line",
        })
    if not sources:
        raise ValueError(f"No DfT MRDB sources near {bbox}")
    return sources


def _gpkg_geometry(blob: bytes):
    if blob is None or len(blob) < 8 or blob[:2] != b"GP":
        return None
    flags = blob[3]
    envelope_code = (flags >> 1) & 0b111
    envelope_bytes = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}.get(envelope_code)
    if envelope_bytes is None:
        return None
    return wkb.loads(blob[8 + envelope_bytes:])


def _os_classification(value: str | None) -> str:
    value = (value or "").lower()
    if "motorway" in value:
        return "motorway"
    if value == "a road" or value.startswith("a road"):
        return "a_road"
    if value == "b road" or value.startswith("b road"):
        return "b_road"
    if "classified" in value or "unclassified" in value or "minor" in value or "local" in value or "unknown" in value:
        return "minor"
    return "unknown"


def load_os_open_roads_sources(gpkg_path: str | Path, dft_sources: list[dict],
                               bbox: tuple[float, float, float, float],
                               region_urban_rural: str) -> tuple[list[dict], dict]:
    """Load continuous OS road links and attach direct or imputed traffic."""
    minx, miny, maxx, maxy = bbox
    connection = sqlite3.connect(str(gpkg_path))
    sql = ("SELECT r.fid,r.geometry,r.id,r.road_classification,r.road_function,"
           "r.road_classification_number,r.name_1,r.length "
           "FROM road_link r JOIN rtree_road_link_geometry idx ON r.fid=idx.id "
           "WHERE idx.maxx>=? AND idx.minx<=? AND idx.maxy>=? AND idx.miny<=?")
    raw_rows = connection.execute(sql, (minx, maxx, miny, maxy)).fetchall()
    connection.close()
    geometries = []
    attributes = []
    for row in raw_rows:
        geometry = _gpkg_geometry(row[1])
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            continue
        geometries.append(geometry)
        attributes.append(row)
    if not geometries:
        raise ValueError(f"No OS Open Roads links near {bbox}")

    dft_geometries = [source["geometry"] for source in dft_sources]
    dft_tree = STRtree(dft_geometries)
    direct_flows: dict[str, list[float]] = {}
    direct_hgv: dict[str, list[float]] = {}
    matched = 0
    traffic_sources = []
    for geometry, row in zip(geometries, attributes):
        road_class = _os_classification(row[3])
        nearest_index = int(dft_tree.nearest(geometry)) if dft_geometries else -1
        nearest_distance = float(geometry.distance(dft_geometries[nearest_index])) if nearest_index >= 0 else math.inf
        direct = nearest_index >= 0 and nearest_distance <= 75.0 and (
            road_class == dft_sources[nearest_index]["road_class"] or road_class == "unknown"
        )
        if direct:
            base = dft_sources[nearest_index]
            traffic_source = base["traffic_source"]
            confidence = f"direct_{base['traffic_source']}"
            flow = base["flow"]
            hgv_flow = base["hgv_flow"]
            hgv_confidence = base["hgv_confidence"]
            matched += 1
            direct_flows.setdefault(road_class, []).append(flow)
            direct_hgv.setdefault(road_class, []).append(base["hgv_share"])
        else:
            traffic_source = "imputed"
            confidence = "imputed_class_median"
            class_values = direct_flows.get(road_class, [])
            if class_values:
                flow = float(np.median(class_values))
                hgv_share = float(np.median(direct_hgv[road_class]))
            else:
                all_values = [source["flow"] for source in dft_sources]
                all_hgv = [source["hgv_share"] for source in dft_sources]
                flow = float(np.median(all_values)) if all_values else 100.0
                hgv_share = float(np.median(all_hgv)) if all_hgv else 0.05
                confidence = "imputed_global_median"
            hgv_flow = flow * hgv_share
            hgv_confidence = "imputed_from_class_median"
        traffic_sources.append({
            "geometry": geometry,
            "flow": float(max(flow, 1.0)),
            "hgv_flow": float(max(hgv_flow, 0.0)),
            "hgv_share": float(min(1.0, max(0.0, hgv_flow / max(flow, 1.0)))),
            "road_class": road_class,
            "road_name": row[6] or "",
            "road_id": row[2],
            "traffic_source": traffic_source,
            "traffic_confidence": confidence,
            "hgv_confidence": hgv_confidence,
            "geometry_kind": "OS_Open_Roads_line",
            "geometry_only": int(traffic_source == "imputed"),
            "urban_rural": region_urban_rural,
            "speed_kmh": float(CLASS_REFERENCE_SPEED_KMH.get(road_class, 50.0)),
            "speed_source": "class_reference_imputed",
        })
    counts = {"os_links": len(traffic_sources), "direct_traffic_links": matched,
              "imputed_traffic_links": len(traffic_sources) - matched}
    counts["direct_fraction"] = matched / len(traffic_sources) if traffic_sources else 0.0
    counts["geometry_only_fraction"] = counts["imputed_traffic_links"] / len(traffic_sources) if traffic_sources else 0.0
    counts["class_counts"] = {name: sum(source["road_class"] == name for source in traffic_sources)
                               for name in ("motorway", "a_road", "b_road", "minor", "unknown")}
    return traffic_sources, counts


def _sample_raster(array: np.ndarray, transform: Affine, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    cols = np.floor((xs - transform.c) / transform.a).astype(int)
    rows = np.floor((ys - transform.f) / transform.e).astype(int)
    cols = np.clip(cols, 0, array.shape[1] - 1)
    rows = np.clip(rows, 0, array.shape[0] - 1)
    return array[rows, cols]


def _source_emission(source: dict, use_speed: bool) -> float:
    road_class = source.get("road_class", "unknown")
    speed = float(source.get("speed_kmh", 70.0)) if use_speed else 70.0
    speed = max(20.0, min(130.0, speed))
    hgv_share = float(source.get("hgv_share", 0.05))
    light = max(0.0, source["flow"] - source["hgv_flow"])
    hgv = max(0.0, source["hgv_flow"])
    if road_class == "motorway":
        class_correction = 1.5
    elif road_class == "a_road":
        class_correction = 0.5
    elif road_class == "b_road":
        class_correction = 0.0
    else:
        class_correction = -1.0
    values = []
    for flow, category in ((light, "light"), (hgv, "hgv")):
        p = CNOSSOS_PROXY_PARAMETERS[category]
        rolling = p["A_R"] + p["B_R"] * math.log10(speed / 70.0)
        propulsion = p["A_P"] + p["B_P"] * ((speed - 70.0) / 70.0)
        values.append(flow * 10.0 ** ((rolling + propulsion + class_correction) / 10.0))
    # A tiny positive floor keeps geometry-only links present without turning
    # a zero-flow record into an unlabelled reported source.
    return max(sum(values), 1e-12) * (1.0 + 0.1 * hgv_share)


def _energy_pairs(points, geometries, radius: float):
    tree = STRtree(geometries)
    pairs = tree.query(points, predicate="dwithin", distance=radius)
    if pairs.size == 0:
        return np.empty((0, 2), dtype=int)
    # Shapely 2 returns a two-row array: receptor indices, source indices.
    return np.asarray(pairs, dtype=int)


def build_phase2b_features(xs: np.ndarray, ys: np.ndarray, sources: list[dict],
                           use_speed: bool = True, radius: float = 10_000.0) -> dict[str, np.ndarray]:
    """Build continuous-road and simplified line-source features."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    points = np.asarray([Point(x, y) for x, y in zip(xs, ys)], dtype=object)
    geometries = [source["geometry"] for source in sources]
    tree = STRtree(geometries)
    nearest = tree.nearest(points)
    nearest = np.asarray(nearest, dtype=int)
    nearest_dist = np.asarray(shapely_distance(points, np.asarray(geometries, dtype=object)[nearest]), dtype=float)
    flows = np.asarray([source["flow"] for source in sources], dtype=float)
    hgv_flows = np.asarray([source["hgv_flow"] for source in sources], dtype=float)
    emissions = np.asarray([_source_emission(source, use_speed) for source in sources], dtype=float)
    lengths = np.asarray([max(1.0, float(source["geometry"].length)) for source in sources], dtype=float)
    pairs = _energy_pairs(points, geometries, radius)
    inverse_square = np.zeros(len(points), dtype=float)
    line_energy = np.zeros(len(points), dtype=float)
    hgv_energy = np.zeros(len(points), dtype=float)
    if len(pairs):
        receptor_index, source_index = pairs
        distances = np.asarray(shapely_distance(points[receptor_index], np.asarray(geometries, dtype=object)[source_index]), dtype=float)
        distances = np.maximum(distances, 10.0)
        np.add.at(inverse_square, receptor_index, flows[source_index] / distances ** 2)
        np.add.at(hgv_energy, receptor_index, hgv_flows[source_index] / distances ** 2)
        np.add.at(line_energy, receptor_index, emissions[source_index] * lengths[source_index] / distances)
    classes = np.asarray([source.get("road_class", "unknown") for source in sources], dtype=object)
    traffic_source = np.asarray([source.get("traffic_source", "unknown") for source in sources], dtype=object)
    confidence = np.asarray([source.get("traffic_confidence", "unknown") for source in sources], dtype=object)
    return {
        "log1p_nearest_distance_m": np.log1p(nearest_dist),
        "log10_inverse_square_energy_10000m": np.log10(np.maximum(inverse_square, 1e-12)),
        "log10_line_emission_energy_10000m": np.log10(np.maximum(line_energy, 1e-12)),
        "log10_hgv_energy_10000m": np.log10(np.maximum(hgv_energy, 1e-12)),
        "nearest_flow_aadf": flows[nearest],
        "nearest_hgv_share": hgv_flows[nearest] / np.maximum(flows[nearest], 1.0),
        "nearest_road_class": classes[nearest],
        "nearest_traffic_source": traffic_source[nearest],
        "nearest_traffic_confidence": confidence[nearest],
        "speed_coverage": np.full(len(points), 0.0),
        "speed_imputed_kmh": np.asarray([sources[int(i)].get("speed_kmh", 50.0) for i in nearest]),
    }


def fit_tobit_weighted(x: np.ndarray, y: np.ndarray, censored: np.ndarray,
                       weights: np.ndarray, threshold: float,
                       beta_bounds: list[tuple[float | None, float | None]] | None = None) -> dict:
    """Fit a weighted Gaussian left-censored model."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    censored = np.asarray(censored, dtype=bool)
    weights = np.asarray(weights, dtype=float)
    observed = ~censored & np.isfinite(y)
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(censored) or len(y) != len(weights):
        raise ValueError("weighted Tobit shapes do not agree")
    if observed.sum() < max(20, x.shape[1] + 2) or np.any(weights <= 0):
        raise ValueError("insufficient observations or invalid sampling weights")
    start = np.zeros(x.shape[1] + 1, dtype=float)
    start[0] = float(np.nanmean(y[observed]))
    start[-1] = math.log(max(1.0, float(np.nanstd(y[observed]))))
    log2pi = math.log(2.0 * math.pi)
    def objective(params):
        beta = params[:-1]
        sigma = math.exp(float(np.clip(params[-1], -5.0, 5.0)))
        mu = x @ beta
        z = (threshold - mu) / sigma
        ll = np.zeros(len(y), dtype=float)
        residual = (y[observed] - mu[observed]) / sigma
        ll[observed] = -0.5 * (residual * residual + log2pi) - math.log(sigma)
        ll[~observed] = np.log(np.maximum(ndtr(z[~observed]), 1e-15))
        value = -float(np.sum(weights * ll) / np.sum(weights))
        return value if np.isfinite(value) else 1e100
    bounds = list(beta_bounds or [(None, None)] * x.shape[1]) + [(-5.0, 5.0)]
    result = minimize(objective, start, method="L-BFGS-B", bounds=bounds)
    if not result.success:
        raise RuntimeError(f"Weighted Tobit optimization failed: {result.message}")
    return {"beta": result.x[:-1], "sigma_db": math.exp(float(result.x[-1])),
            "threshold_db": float(threshold), "weighted_nll": float(result.fun),
            "success": bool(result.success), "iterations": int(result.nit),
            "effective_n": float(np.sum(weights) ** 2 / np.sum(weights ** 2))}


def predict_tobit(model: dict, x: np.ndarray) -> dict[str, np.ndarray]:
    x = np.asarray(x, dtype=float)
    mu = x @ np.asarray(model["beta"], dtype=float)
    sigma = float(model["sigma_db"])
    threshold = float(model["threshold_db"])
    p = ndtr((threshold - mu) / sigma)
    return {"mu_db": mu, "sigma_db": np.full(len(mu), sigma),
            "probability_below_threshold": p,
            "interval80_low_db": mu - 1.2815515655 * sigma,
            "interval80_high_db": mu + 1.2815515655 * sigma}


def standardize_train_test(x_train: np.ndarray, x_test: np.ndarray):
    mean = np.nanmean(x_train, axis=0)
    scale = np.nanstd(x_train, axis=0)
    scale[scale < 1e-9] = 1.0
    return (x_train - mean) / scale, (x_test - mean) / scale, mean, scale


def weighted_metrics(y: np.ndarray, censored: np.ndarray, prediction: dict,
                     weights: np.ndarray, threshold: float) -> dict:
    y = np.asarray(y, dtype=float)
    censored = np.asarray(censored, dtype=bool)
    weights = np.asarray(weights, dtype=float)
    mu = np.asarray(prediction["mu_db"], dtype=float)
    observed = ~censored & np.isfinite(y)
    result = {"n": int(len(y)), "effective_n": float(weights.sum() ** 2 / np.sum(weights ** 2)),
              "censored_n": int(censored.sum()), "threshold_db": float(threshold)}
    if observed.any():
        error = mu[observed] - y[observed]
        w = weights[observed]
        result.update({"reported_n": int(observed.sum()),
                       "mae_db": float(np.average(np.abs(error), weights=w)),
                       "rmse_db": float(np.sqrt(np.average(error ** 2, weights=w))),
                       "median_absolute_error_db": float(np.median(np.abs(error))),
                       "bias_db": float(np.average(error, weights=w)),
                       "p90_absolute_error_db": float(np.quantile(np.abs(error), 0.90))})
    else:
        result.update({"reported_n": 0, "mae_db": None, "rmse_db": None,
                       "median_absolute_error_db": None, "bias_db": None,
                       "p90_absolute_error_db": None})
    p = np.asarray(prediction["probability_below_threshold"], dtype=float)
    actual = censored.astype(float)
    result.update({"predicted_below_fraction": float(np.average(mu < threshold, weights=weights)),
                   "actual_censor_fraction": float(np.average(actual, weights=weights)),
                   "mean_predicted_censor_probability": float(np.average(p, weights=weights)),
                   "censor_brier": float(np.average((p - actual) ** 2, weights=weights)),
                   "censor_probability_bias": float(np.average(p - actual, weights=weights))})
    for label, lo, hi in (("35_40", 35.0, 40.0), ("40_45", 40.0, 45.0), ("45_55", 45.0, 55.0), ("55_plus", 55.0, np.inf)):
        mask = observed & (y >= lo) & (y < hi)
        if mask.any():
            error = mu[mask] - y[mask]
            result[f"{label}_n"] = int(mask.sum())
            result[f"{label}_mae_db"] = float(np.average(np.abs(error), weights=weights[mask]))
            result[f"{label}_rmse_db"] = float(np.sqrt(np.average(error ** 2, weights=weights[mask])))
        else:
            result[f"{label}_n"] = 0
            result[f"{label}_mae_db"] = None
            result[f"{label}_rmse_db"] = None
    return result


def calibration_bins(censored: np.ndarray, probability: np.ndarray, weights: np.ndarray, bins: int = 10) -> list[dict]:
    order = np.argsort(probability)
    rows = []
    for index, chunk in enumerate(np.array_split(order, bins)):
        if len(chunk) == 0:
            continue
        w = weights[chunk]
        rows.append({"bin": index, "n": int(len(chunk)),
                     "predicted_probability": float(np.average(probability[chunk], weights=w)),
                     "observed_fraction": float(np.average(censored[chunk], weights=w))})
    return rows


def download_phase2b_rasters(output_root: str | Path, regions: Iterable[Phase2BRegion], timeout: int = 240,
                             refresh: bool = False) -> dict:
    output_root = Path(output_root)
    lden_dir = output_root / "raw" / "road_lden_10m"
    lnight_dir = output_root / "raw" / "road_lnight_10m"
    inventory = {}
    for region in regions:
        lden_path = lden_dir / f"{region.region_id}.tif"
        lnight_path = lnight_dir / f"{region.region_id}.tif"
        if refresh or not lden_path.exists():
            lden_path.parent.mkdir(parents=True, exist_ok=True)
            lden_path.write_bytes(get_coverage(ROAD_WCS_URL, ROAD_LDEN_COVERAGE_ID, region.bbox, 1000, 1000,
                                               crs=EPSG, version="1.0.0", format_="GeoTIFF", timeout=timeout))
        if refresh or not lnight_path.exists():
            lnight_path.parent.mkdir(parents=True, exist_ok=True)
            lnight_path.write_bytes(get_coverage(ROAD_WCS_URL, ROAD_LNIGHT_COVERAGE_ID, region.bbox, 1000, 1000,
                                                crs=EPSG, version="1.0.0", format_="GeoTIFF", timeout=timeout))
        with rasterio.open(lden_path) as dataset:
            lden_grid = {"shape": list(dataset.shape), "crs": str(dataset.crs), "transform": list(dataset.transform),
                         "bounds": list(dataset.bounds), "nodata": dataset.nodata}
        with rasterio.open(lnight_path) as dataset:
            lnight_grid = {"shape": list(dataset.shape), "crs": str(dataset.crs), "transform": list(dataset.transform),
                           "bounds": list(dataset.bounds), "nodata": dataset.nodata}
        inventory[region.region_id] = {"lden_path": str(lden_path), "lnight_path": str(lnight_path),
                                       "bbox_epsg27700": list(region.bbox), "lden_grid": lden_grid,
                                       "lnight_grid": lnight_grid}
    return inventory


def read_target(path: str | Path) -> tuple[np.ndarray, Affine, dict]:
    with rasterio.open(path) as dataset:
        values, diagnostics = read_single_band_db(dataset)
        return values, dataset.transform, {"shape": list(dataset.shape), "crs": str(dataset.crs),
                                           "transform": list(dataset.transform), "bounds": list(dataset.bounds),
                                           "nodata": dataset.nodata, "diagnostics": diagnostics,
                                           "min_positive_db": float(np.nanmin(values)) if np.isfinite(values).any() else None,
                                           "q01_db": float(np.nanquantile(values, 0.01)) if np.isfinite(values).any() else None,
                                           "q05_db": float(np.nanquantile(values, 0.05)) if np.isfinite(values).any() else None,
                                           "valid_cells": int(np.isfinite(values).sum()),
                                           "total_cells": int(values.size)}


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


def population_sample_indices(lden: np.ndarray, lnight: np.ndarray, sample_n: int, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Return representative and deliberately balanced designs for comparison."""
    eligible = np.flatnonzero(np.ones(lden.size, dtype=bool))
    rng = np.random.default_rng(seed)
    representative = rng.choice(eligible, size=min(sample_n, len(eligible)), replace=False)
    finite = np.isfinite(lden.ravel())
    reported = np.flatnonzero(finite)
    censored = np.flatnonzero(~finite)
    per_class = max(1, min(len(reported), len(censored), sample_n // 2))
    balanced = np.r_[rng.choice(reported, size=per_class, replace=False),
                     rng.choice(censored, size=per_class, replace=False)]
    return representative, balanced


def infer_thresholds(inventories: list[dict]) -> dict:
    lden = [r["lden_min_positive_db"] for r in inventories if r["lden_min_positive_db"] is not None]
    lnight = [r["lnight_min_positive_db"] for r in inventories if r["lnight_min_positive_db"] is not None]
    if not lden or not lnight:
        raise ValueError("Cannot infer thresholds from empty raster inventory")
    return {"lden_min_positive_values": lden, "lnight_min_positive_values": lnight,
            "lden_threshold_inferred_db": float(np.median(lden)),
            "lnight_threshold_inferred_db": float(np.median(lnight)),
            "lden_range_db": [float(min(lden)), float(max(lden))],
            "lnight_range_db": [float(min(lnight)), float(max(lnight))],
            "method": "minimum finite reported value per region; threshold used only when all regional minima agree within 0.5 dB"}
