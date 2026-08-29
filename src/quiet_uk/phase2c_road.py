"""Phase 2C road-source integrity and stability utilities.

This module is intentionally separate from the validated Phase 1 pipeline and
from the Phase 2B experiment.  It supplies deterministic two-pass traffic
assignment, finite-line source integration, a bounded censored model, and
England-land-aware sampling for a directly comparable ten-region experiment.

The acoustic source model is a deliberately simplified relative proxy.  It is
not a CNOSSOS-EU implementation and must not be used as a regulatory model.
"""

from __future__ import annotations

import math
import re
import sqlite3
from collections import defaultdict
from pathlib import Path

import numpy as np
import shapefile
from scipy.optimize import minimize
from scipy.special import ndtr
from scipy.stats import spearmanr
from shapely import distance as shapely_distance
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

import rasterio

from .phase2b_road import (
    CLASS_REFERENCE_SPEED_KMH,
    CNOSSOS_PROXY_PARAMETERS,
    EPSG,
    PHASE2B_REGIONS,
    _gpkg_geometry,
    _os_classification,
    _road_class,
    ensure_phase2b_inputs,
    infer_thresholds,
    load_dft_aadf_year,
    read_target,
    standardize_train_test,
    utc_now,
    weighted_metrics,
    write_csv,
    write_json,
)


PHASE2C_REGIONS = PHASE2B_REGIONS
OS_LINK_QUERY_RADIUS_M = 75.0
NEARBY_DIRECT_RADIUS_M = 5_000.0
LINE_INTEGRAL_DISTANCE_FLOOR_M = 10.0
ROAD_NUMBER_RE = re.compile(r"[^A-Z0-9]+")


def _normalise_identifier(value: object) -> str:
    return ROAD_NUMBER_RE.sub("", str(value or "").upper())


def _normalise_name(value: object) -> str:
    return " ".join(re.findall(r"[A-Z0-9]+", str(value or "").upper()))


def _road_name_match(left: object, right: object) -> bool:
    a = _normalise_name(left)
    b = _normalise_name(right)
    if not a or not b:
        return False
    if a == b or a in b or b in a:
        return True
    return bool(set(a.split()) & set(b.split())) and len(set(a.split()) & set(b.split())) >= 2


def _orientation_degrees(geometry) -> float:
    coordinates = list(geometry.coords)
    if len(coordinates) < 2:
        return 0.0
    best = (0.0, 0.0)
    for first, second in zip(coordinates[:-1], coordinates[1:]):
        dx = float(second[0] - first[0])
        dy = float(second[1] - first[1])
        length = math.hypot(dx, dy)
        if length > best[0]:
            best = (length, math.degrees(math.atan2(dy, dx)) % 180.0)
    return best[1]


def _orientation_difference(left: float, right: float) -> float:
    difference = abs(left - right) % 180.0
    return min(difference, 180.0 - difference)


def load_os_open_roads_links(gpkg_path: str | Path,
                             bbox: tuple[float, float, float, float]) -> list[dict]:
    """Read spatially filtered OS Open Roads links in stable identifier order."""
    minx, miny, maxx, maxy = bbox
    connection = sqlite3.connect(str(gpkg_path))
    sql = ("SELECT r.fid,r.geometry,r.id,r.road_classification,r.road_function,"
           "r.road_classification_number,r.name_1,r.length "
           "FROM road_link r JOIN rtree_road_link_geometry idx ON r.fid=idx.id "
           "WHERE idx.maxx>=? AND idx.minx<=? AND idx.maxy>=? AND idx.miny<=?")
    raw_rows = connection.execute(sql, (minx, maxx, miny, maxy)).fetchall()
    connection.close()
    links = []
    for fid, blob, link_id, classification, road_function, road_number, name, length in raw_rows:
        geometry = _gpkg_geometry(blob)
        if geometry is None or geometry.is_empty or not geometry.is_valid:
            continue
        links.append({
            "fid": int(fid),
            "link_id": str(link_id),
            "geometry": geometry,
            "road_class": _os_classification(classification),
            "road_classification": classification or "",
            "road_function": road_function or "",
            "road_number": road_number or "",
            "road_name": name or "",
            "length_m": float(length or geometry.length),
            "orientation_deg": _orientation_degrees(geometry),
        })
    links.sort(key=lambda row: (row["link_id"], row["fid"]))
    if not links:
        raise ValueError(f"No OS Open Roads links near {bbox}")
    return links


def load_dft_mrdb_sources_phase2c(aadf_rows: list[dict], mrdb_path: str | Path,
                                  bbox: tuple[float, float, float, float],
                                  margin_m: float = 5_500.0) -> list[dict]:
    """Load same-year DfT MRDB lines with road-number and local metadata."""
    minx, miny, maxx, maxy = bbox
    by_id = {row["count_point_id"]: row for row in aadf_rows}
    sources = []
    with shapefile.Reader(str(mrdb_path)) as reader:
        fields = [field[0] for field in reader.fields[1:]]
        for shape_record in reader.iterShapeRecords():
            values = dict(zip(fields, shape_record.record))
            points = shape_record.shape.points
            if not points:
                continue
            sx = [point[0] for point in points]
            sy = [point[1] for point in points]
            if (max(sx) < minx - margin_m or min(sx) > maxx + margin_m or
                    max(sy) < miny - margin_m or min(sy) > maxy + margin_m):
                continue
            count_point_id = str(int(float(values.get("CP_Number"))))
            row = by_id.get(count_point_id)
            if row is None or len(points) < 2:
                continue
            line = LineString(points)
            if line.is_empty:
                continue
            road_number = str(values.get("RoadNumber") or row.get("road_name", ""))
            sources.append({
                "dft_id": count_point_id,
                "geometry": line,
                "flow": float(row["flow"]),
                "hgv_flow": float(row["hgv_flow"]),
                "hgv_share": float(row["hgv_share"]),
                "road_class": _road_class(road_number, row.get("road_category", "")),
                "road_name": row.get("road_name", ""),
                "road_number": road_number,
                "road_function": row.get("road_type", ""),
                "local_authority_name": row.get("local_authority_name", ""),
                "traffic_source": "counted" if row["estimation_method"].lower() == "counted" else "estimated",
                "traffic_confidence": row.get("estimation_method", "Unknown"),
                "hgv_confidence": "direct_DfT",
                "geometry_kind": "DfT_MRDB_line",
                "orientation_deg": _orientation_degrees(line),
            })
    if not sources:
        raise ValueError(f"No DfT MRDB sources near {bbox}")
    sources.sort(key=lambda row: row["dft_id"])
    return sources


def _candidate_diagnostics(os_link: dict, dft_source: dict) -> dict:
    distance_m = float(os_link["geometry"].distance(dft_source["geometry"]))
    road_number_match = bool(
        _normalise_identifier(os_link.get("road_number")) and
        _normalise_identifier(os_link.get("road_number")) == _normalise_identifier(dft_source.get("road_number"))
    )
    road_name_match = _road_name_match(os_link.get("road_name"), dft_source.get("road_name"))
    road_class_match = os_link.get("road_class") in {dft_source.get("road_class"), "unknown"}
    orientation_difference_deg = _orientation_difference(
        float(os_link.get("orientation_deg", _orientation_degrees(os_link["geometry"]))),
        float(dft_source.get("orientation_deg", _orientation_degrees(dft_source["geometry"]))),
    )
    score = max(0.0, 1.0 - distance_m / OS_LINK_QUERY_RADIUS_M)
    score += 6.0 if road_number_match else 0.0
    score += 2.0 if road_name_match else 0.0
    score += 2.0 if road_class_match else -2.0
    if orientation_difference_deg <= 15.0:
        score += 1.5
    elif orientation_difference_deg <= 35.0:
        score += 0.5
    if distance_m <= 20.0:
        score += 0.5
    return {
        "distance_m": distance_m,
        "road_number_match": int(road_number_match),
        "road_name_match": int(road_name_match),
        "road_class_match": int(road_class_match),
        "orientation_difference_deg": orientation_difference_deg,
        "score": score,
    }


def _select_direct_match(os_link: dict, dft_sources: list[dict], tree: STRtree) -> tuple[dict | None, dict]:
    candidate_indices = tree.query(os_link["geometry"], predicate="dwithin", distance=OS_LINK_QUERY_RADIUS_M)
    candidates = []
    for index in np.asarray(candidate_indices, dtype=int).ravel():
        diagnostics = _candidate_diagnostics(os_link, dft_sources[int(index)])
        if diagnostics["distance_m"] <= OS_LINK_QUERY_RADIUS_M:
            candidates.append((diagnostics, dft_sources[int(index)]))
    candidates.sort(key=lambda item: (-item[0]["score"], item[0]["distance_m"], item[1]["dft_id"]))
    if not candidates:
        return None, {"candidate_count": 0, "match_category": "imputed"}
    diagnostics, source = candidates[0]
    # Precision-first acceptance.  A road-number match is strong evidence;
    # without one we require close, class-compatible, similarly oriented lines.
    high = diagnostics["road_number_match"] and diagnostics["road_class_match"] and diagnostics["score"] >= 8.0
    medium = (diagnostics["road_class_match"] and diagnostics["distance_m"] <= 25.0 and
              diagnostics["orientation_difference_deg"] <= 45.0 and diagnostics["score"] >= 3.0)
    if not (high or medium):
        return None, {"candidate_count": len(candidates), **diagnostics, "match_category": "imputed"}
    category = "direct_high_confidence" if high else "direct_medium_confidence"
    return source, {"candidate_count": len(candidates), **diagnostics, "match_category": category}


def _median(values: list[float], fallback: float) -> float:
    return float(np.median(np.asarray(values, dtype=float))) if values else float(fallback)


def _stable_group_stats(records: list[dict], key: str) -> dict[str, dict]:
    grouped = defaultdict(list)
    for record in records:
        value = _normalise_identifier(record.get(key)) if key == "road_number" else str(record.get(key, ""))
        if value:
            grouped[value].append(record)
    return {
        key_value: {
            "flow": _median([row["flow"] for row in values], 100.0),
            "hgv_share": _median([row["hgv_share"] for row in values], 0.05),
            "n": len(values),
        }
        for key_value, values in sorted(grouped.items())
    }


def assign_traffic_two_pass(os_links: list[dict], dft_sources: list[dict],
                            region_urban_rural: str,
                            nearby_radius_m: float = NEARBY_DIRECT_RADIUS_M) -> tuple[list[dict], dict]:
    """Assign traffic in a deterministic direct-match pass then imputation pass."""
    links = sorted(os_links, key=lambda row: (str(row.get("link_id", "")), int(row.get("fid", 0))))
    dft_sources = sorted(dft_sources, key=lambda row: str(row.get("dft_id", "")))
    dft_tree = STRtree([source["geometry"] for source in dft_sources])

    # Query all OS links in one vectorized STRtree operation.  Candidate
    # scoring is still deterministic per link, but repeated Python-level tree
    # queries are avoided for the national-sized OS link density.
    candidate_pairs = dft_tree.query([link["geometry"] for link in links],
                                     predicate="dwithin", distance=OS_LINK_QUERY_RADIUS_M)
    candidates_by_link: dict[int, list[int]] = defaultdict(list)
    if np.asarray(candidate_pairs).size:
        for link_index, dft_index in zip(np.asarray(candidate_pairs)[0], np.asarray(candidate_pairs)[1]):
            candidates_by_link[int(link_index)].append(int(dft_index))

    accepted: dict[str, tuple[dict, dict]] = {}
    for link_index, link in enumerate(links):
        candidates = []
        for dft_index in candidates_by_link.get(link_index, []):
            diagnostics = _candidate_diagnostics(link, dft_sources[dft_index])
            if diagnostics["distance_m"] <= OS_LINK_QUERY_RADIUS_M:
                candidates.append((diagnostics, dft_sources[dft_index]))
        candidates.sort(key=lambda item: (-item[0]["score"], item[0]["distance_m"], item[1]["dft_id"]))
        if candidates:
            diagnostics, matched = candidates[0]
            high = diagnostics["road_number_match"] and diagnostics["road_class_match"] and diagnostics["score"] >= 8.0
            medium = (diagnostics["road_class_match"] and diagnostics["distance_m"] <= 25.0 and
                      diagnostics["orientation_difference_deg"] <= 45.0 and diagnostics["score"] >= 3.0)
            if high or medium:
                diagnostics = {"candidate_count": len(candidates), **diagnostics,
                               "match_category": "direct_high_confidence" if high else "direct_medium_confidence"}
                accepted[link["link_id"]] = (matched, diagnostics)
                continue
        # No accepted direct match is recorded in pass 1; pass 2 below is the
        # only place where an unmatched link receives imputed traffic.

    direct_records = []
    for link in links:
        if link["link_id"] in accepted:
            source, diagnostics = accepted[link["link_id"]]
            direct_records.append({**source, "os_link_id": link["link_id"], "match": diagnostics,
                                   "road_class": link["road_class"]})

    direct_by_number = _stable_group_stats(direct_records, "road_number")
    direct_by_class = _stable_group_stats(direct_records, "road_class")
    direct_by_class_urban = _stable_group_stats(
        [{**record, "road_class": f"{record['road_class']}|{region_urban_rural}"} for record in direct_records],
        "road_class",
    )
    all_by_class = _stable_group_stats(dft_sources, "road_class")
    all_global = {
        "flow": _median([row["flow"] for row in dft_sources], 100.0),
        "hgv_share": _median([row["hgv_share"] for row in dft_sources], 0.05),
        "n": len(dft_sources),
    }

    # A deterministic spatial index of accepted links supports the local
    # fallback without depending on the order in which links were read.
    direct_buckets: dict[tuple[int, int], list[dict]] = defaultdict(list)
    bucket_size = nearby_radius_m
    for record in direct_records:
        centroid = record["geometry"].centroid
        bucket = (math.floor(centroid.x / bucket_size), math.floor(centroid.y / bucket_size))
        direct_buckets[bucket].append(record)
    for values in direct_buckets.values():
        values.sort(key=lambda row: str(row["dft_id"]))

    output = []
    method_counts = defaultdict(int)
    for link in links:
        link_id = link["link_id"]
        if link_id in accepted:
            source, diagnostics = accepted[link_id]
            output.append({
                **link,
                "flow": float(source["flow"]),
                "hgv_flow": float(source["hgv_flow"]),
                "hgv_share": float(source["hgv_share"]),
                "traffic_source": source["traffic_source"],
                "traffic_assignment_source": "direct",
                "traffic_confidence": diagnostics["match_category"],
                "match_category": diagnostics["match_category"],
                "matched_dft_id": source["dft_id"],
                "match_distance_m": float(diagnostics["distance_m"]),
                "match_score": float(diagnostics["score"]),
                "road_number_match": int(diagnostics["road_number_match"]),
                "road_name_match": int(diagnostics["road_name_match"]),
                "road_class_match": int(diagnostics["road_class_match"]),
                "orientation_difference_deg": float(diagnostics["orientation_difference_deg"]),
                "match_candidate_count": int(diagnostics["candidate_count"]),
                "imputation_method": "none",
                "imputation_support_n": 1,
                "imputation_radius_m": 0.0,
                "hgv_confidence": source["hgv_confidence"],
                "geometry_kind": "OS_Open_Roads_line",
                "geometry_only": 0,
                "urban_rural": region_urban_rural,
                "speed_kmh": float(CLASS_REFERENCE_SPEED_KMH.get(link["road_class"], 50.0)),
                "speed_source": "class_reference_imputed",
            })
            method_counts[diagnostics["match_category"]] += 1
            continue

        road_number = _normalise_identifier(link.get("road_number"))
        method = ""
        support_n = 0
        radius = 0.0
        if road_number and road_number in direct_by_number:
            stats = direct_by_number[road_number]
            method = "same_road_number_direct"
            support_n = int(stats["n"])
            flow = stats["flow"]
            hgv_share = stats["hgv_share"]
            confidence = "imputed_medium"
        else:
            centroid = link["geometry"].centroid
            bucket = (math.floor(centroid.x / bucket_size), math.floor(centroid.y / bucket_size))
            nearby = []
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for record in direct_buckets.get((bucket[0] + dx, bucket[1] + dy), []):
                        if record["road_class"] != link["road_class"]:
                            continue
                        distance_m = float(link["geometry"].distance(record["geometry"]))
                        if distance_m <= nearby_radius_m:
                            nearby.append((distance_m, str(record["dft_id"]), record))
            if nearby:
                nearby.sort(key=lambda item: (item[0], item[1]))
                local_values = [item[2] for item in nearby[:25]]
                flow = _median([row["flow"] for row in local_values], 100.0)
                hgv_share = _median([row["hgv_share"] for row in local_values], 0.05)
                method = "nearby_same_class_direct"
                support_n = len(local_values)
                radius = float(nearby[0][0])
                confidence = "imputed_medium"
            else:
                class_key = str(link["road_class"])
                urban_key = f"{class_key}|{region_urban_rural}"
                stats = direct_by_class_urban.get(urban_key) or direct_by_class.get(class_key)
                if stats:
                    flow = stats["flow"]
                    hgv_share = stats["hgv_share"]
                    method = "class_urban_rural_direct_median" if urban_key in direct_by_class_urban else "class_direct_median"
                    support_n = int(stats["n"])
                    confidence = "imputed_low"
                elif class_key in all_by_class:
                    stats = all_by_class[class_key]
                    flow = stats["flow"]
                    hgv_share = stats["hgv_share"]
                    method = "class_median_all_dft"
                    support_n = int(stats["n"])
                    confidence = "imputed_low"
                else:
                    flow = all_global["flow"]
                    hgv_share = all_global["hgv_share"]
                    method = "global_median_all_dft"
                    support_n = int(all_global["n"])
                    confidence = "imputed_global"
        hgv_flow = float(max(0.0, flow * hgv_share))
        output.append({
            **link,
            "flow": float(max(flow, 1.0)),
            "hgv_flow": hgv_flow,
            "hgv_share": float(min(1.0, max(0.0, hgv_share))),
            "traffic_source": "imputed",
            "traffic_assignment_source": "imputed",
            "traffic_confidence": confidence,
            "match_category": "imputed" if confidence != "imputed_global" else "geometry_only",
            "matched_dft_id": "",
            "match_distance_m": "",
            "match_score": "",
            "road_number_match": 0,
            "road_name_match": 0,
            "road_class_match": 0,
            "orientation_difference_deg": "",
            "match_candidate_count": 0,
            "imputation_method": method,
            "imputation_support_n": int(support_n),
            "imputation_radius_m": radius,
            "hgv_confidence": f"{method}",
            "geometry_kind": "OS_Open_Roads_line",
            "geometry_only": 1,
            "urban_rural": region_urban_rural,
            "speed_kmh": float(CLASS_REFERENCE_SPEED_KMH.get(link["road_class"], 50.0)),
            "speed_source": "class_reference_imputed",
        })
        method_counts[method] += 1

    output.sort(key=lambda row: (str(row.get("link_id", "")), int(row.get("fid", 0))))
    counts = {
        "os_links": len(output),
        "direct_traffic_links": sum(row["traffic_assignment_source"] == "direct" for row in output),
        "imputed_traffic_links": sum(row["traffic_assignment_source"] == "imputed" for row in output),
        "direct_high_confidence": sum(row["match_category"] == "direct_high_confidence" for row in output),
        "direct_medium_confidence": sum(row["match_category"] == "direct_medium_confidence" for row in output),
        "direct_fraction": sum(row["traffic_assignment_source"] == "direct" for row in output) / max(len(output), 1),
        "geometry_only_fraction": sum(row["match_category"] == "geometry_only" for row in output) / max(len(output), 1),
        "class_counts": {name: sum(row["road_class"] == name for row in output)
                         for name in ("motorway", "a_road", "b_road", "minor", "unknown")},
        "assignment_method_counts": dict(sorted(method_counts.items())),
        "assignment_order": "sorted by OS link_id and fid; all imputation statistics calculated after direct pass",
    }
    return output, counts


def combine_rolling_propulsion_db(rolling_db: float, propulsion_db: float) -> float:
    """Combine two level components in acoustic-energy space."""
    return float(10.0 * math.log10(10.0 ** (rolling_db / 10.0) + 10.0 ** (propulsion_db / 10.0)))


def source_emission_energy(source: dict, use_speed: bool = True) -> float:
    """Return a relative per-link source energy without redundant HGV weighting."""
    road_class = source.get("road_class", "unknown")
    speed = float(source.get("speed_kmh", 70.0)) if use_speed else 70.0
    speed = max(20.0, min(130.0, speed))
    class_correction = {"motorway": 1.5, "a_road": 0.5, "b_road": 0.0}.get(road_class, -1.0)
    total = 0.0
    for flow, category in ((max(0.0, float(source.get("flow", 0.0)) - float(source.get("hgv_flow", 0.0))), "light"),
                           (max(0.0, float(source.get("hgv_flow", 0.0))), "hgv")):
        if flow <= 0:
            continue
        params = CNOSSOS_PROXY_PARAMETERS[category]
        rolling = params["A_R"] + params["B_R"] * math.log10(speed / 70.0) + class_correction
        propulsion = params["A_P"] + params["B_P"] * ((speed - 70.0) / 70.0) + class_correction
        combined = combine_rolling_propulsion_db(rolling, propulsion)
        total += flow * 10.0 ** (combined / 10.0)
    return max(total, 1e-12)


def finite_line_integral(geometry, x: float, y: float,
                         distance_floor_m: float = LINE_INTEGRAL_DISTANCE_FLOOR_M) -> float:
    """Integrate regularised inverse distance along every straight line segment.

    The integral is additive over segments, so splitting a collinear line does
    not change the result except for floating-point roundoff.  The distance
    floor is a near-field regularisation, not a claim about full propagation.
    """
    point_x = float(x)
    point_y = float(y)
    coordinates = list(geometry.coords)
    total = 0.0
    floor = max(float(distance_floor_m), 0.0)
    for first, second in zip(coordinates[:-1], coordinates[1:]):
        ax, ay = float(first[0]), float(first[1])
        bx, by = float(second[0]), float(second[1])
        dx, dy = bx - ax, by - ay
        length = math.hypot(dx, dy)
        if length <= 0:
            continue
        projection = ((point_x - ax) * dx + (point_y - ay) * dy) / length
        cross = ((point_x - ax) * dy - (point_y - ay) * dx) / length
        regularised = math.sqrt(cross * cross + floor * floor)
        total += math.asinh((length - projection) / regularised) - math.asinh(-projection / regularised)
    return max(float(total), 0.0)


def _energy_pairs(points, geometries, radius: float):
    tree = STRtree(geometries)
    pairs = tree.query(points, predicate="dwithin", distance=radius)
    if pairs.size == 0:
        return np.empty((0, 2), dtype=int)
    return np.asarray(pairs, dtype=int)


def build_phase2c_features(xs: np.ndarray, ys: np.ndarray, sources: list[dict],
                           use_speed: bool = True, radius: float = 5_000.0) -> dict[str, np.ndarray]:
    """Build finite-line and benchmark features from assigned OS road links."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    points = np.asarray([Point(x, y) for x, y in zip(xs, ys)], dtype=object)
    geometries = [source["geometry"] for source in sources]
    tree = STRtree(geometries)
    nearest = np.asarray(tree.nearest(points), dtype=int)
    nearest_dist = np.asarray(shapely_distance(points, np.asarray(geometries, dtype=object)[nearest]), dtype=float)
    flows = np.asarray([float(source["flow"]) for source in sources], dtype=float)
    hgv_flows = np.asarray([float(source["hgv_flow"]) for source in sources], dtype=float)
    emissions = np.asarray([source_emission_energy(source, use_speed) for source in sources], dtype=float)
    lengths = np.asarray([max(1.0, float(source["geometry"].length)) for source in sources], dtype=float)
    pairs = _energy_pairs(points, geometries, radius)
    line_energy = np.zeros(len(points), dtype=float)
    benchmark_energy = np.zeros(len(points), dtype=float)
    hgv_energy = np.zeros(len(points), dtype=float)
    imputed_energy = np.zeros(len(points), dtype=float)
    if len(pairs):
        receptor_indices, source_indices = pairs
        for receptor_index, source_index in zip(receptor_indices, source_indices):
            source_index = int(source_index)
            distance_m = max(float(points[int(receptor_index)].distance(geometries[source_index])), LINE_INTEGRAL_DISTANCE_FLOOR_M)
            # ``source_emission_energy`` is a relative per-metre intensity for
            # the assigned AADF flow.  Multiplying by the finite-line integral
            # makes equivalent collinear segmentation additive.
            contribution = emissions[source_index] * finite_line_integral(
                geometries[source_index], xs[int(receptor_index)], ys[int(receptor_index)])
            line_energy[int(receptor_index)] += contribution
            benchmark_energy[int(receptor_index)] += flows[source_index] / distance_m ** 2
            hgv_energy[int(receptor_index)] += hgv_flows[source_index] / distance_m ** 2
            if sources[source_index].get("traffic_assignment_source") != "direct":
                imputed_energy[int(receptor_index)] += contribution
    classes = np.asarray([source.get("road_class", "unknown") for source in sources], dtype=object)
    traffic_source = np.asarray([source.get("traffic_assignment_source", source.get("traffic_source", "unknown")) for source in sources], dtype=object)
    confidence = np.asarray([source.get("traffic_confidence", "unknown") for source in sources], dtype=object)
    match_categories = np.asarray([source.get("match_category", "unknown") for source in sources], dtype=object)
    return {
        "log1p_nearest_distance_m": np.log1p(nearest_dist),
        "log10_finite_line_energy_5000m": np.log10(np.maximum(line_energy, 1e-12)),
        "log10_inverse_square_energy_5000m": np.log10(np.maximum(benchmark_energy, 1e-12)),
        "log10_hgv_energy_5000m": np.log10(np.maximum(hgv_energy, 1e-12)),
        "imputed_source_energy_fraction": imputed_energy / np.maximum(line_energy, 1e-12),
        "nearest_flow_aadf": flows[nearest],
        "nearest_hgv_share": hgv_flows[nearest] / np.maximum(flows[nearest], 1.0),
        "nearest_road_class": classes[nearest],
        "nearest_traffic_source": traffic_source[nearest],
        "nearest_traffic_confidence": confidence[nearest],
        "nearest_match_category": match_categories[nearest],
        "speed_coverage": np.full(len(points), 0.0),
        "speed_imputed_kmh": np.asarray([sources[int(i)].get("speed_kmh", 50.0) for i in nearest]),
    }


def _softplus(values: np.ndarray) -> np.ndarray:
    return np.logaddexp(0.0, np.asarray(values, dtype=float))


def fit_bounded_tobit(x: np.ndarray, y: np.ndarray, censored: np.ndarray,
                      weights: np.ndarray, threshold: float, floor_db: float = 0.0) -> dict:
    """Fit a left-censored model with ``mu=floor+softplus(X beta)``."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    censored = np.asarray(censored, dtype=bool)
    weights = np.asarray(weights, dtype=float)
    observed = ~censored & np.isfinite(y)
    if x.ndim != 2 or len(x) != len(y) or len(y) != len(censored) or len(y) != len(weights):
        raise ValueError("bounded Tobit shapes do not agree")
    if observed.sum() < max(20, x.shape[1] + 2) or np.any(weights <= 0):
        raise ValueError("insufficient observations or invalid sampling weights")
    mean_observed = max(float(np.nanmean(y[observed])) - floor_db, 1.0)
    start_intercept = math.log(max(math.expm1(min(mean_observed, 100.0)), 1e-6))
    start = np.zeros(x.shape[1] + 1, dtype=float)
    start[0] = start_intercept
    start[-1] = math.log(max(1.0, float(np.nanstd(y[observed]))))
    log2pi = math.log(2.0 * math.pi)

    def objective(params):
        beta = params[:-1]
        sigma = math.exp(float(np.clip(params[-1], -5.0, 5.0)))
        mu = floor_db + _softplus(x @ beta)
        z = (threshold - mu) / sigma
        log_likelihood = np.zeros(len(y), dtype=float)
        residual = (y[observed] - mu[observed]) / sigma
        log_likelihood[observed] = -0.5 * (residual * residual + log2pi) - math.log(sigma)
        log_likelihood[~observed] = np.log(np.maximum(ndtr(z[~observed]), 1e-15))
        value = -float(np.sum(weights * log_likelihood) / np.sum(weights))
        return value if np.isfinite(value) else 1e100

    result = minimize(objective, start, method="L-BFGS-B", bounds=[(None, None)] * x.shape[1] + [(-5.0, 5.0)])
    if not result.success:
        raise RuntimeError(f"Bounded Tobit optimization failed: {result.message}")
    return {
        "beta": result.x[:-1], "sigma_db": math.exp(float(result.x[-1])),
        "threshold_db": float(threshold), "floor_db": float(floor_db),
        "weighted_nll": float(result.fun), "success": bool(result.success),
        "iterations": int(result.nit),
        "effective_n": float(np.sum(weights) ** 2 / np.sum(weights ** 2)),
        "link": "floor_db + softplus(X beta)",
    }


def predict_bounded_tobit(model: dict, x: np.ndarray) -> dict[str, np.ndarray]:
    x = np.asarray(x, dtype=float)
    mu = float(model.get("floor_db", 0.0)) + _softplus(x @ np.asarray(model["beta"], dtype=float))
    sigma = float(model["sigma_db"])
    threshold = float(model["threshold_db"])
    p = ndtr((threshold - mu) / sigma)
    return {"mu_db": mu, "sigma_db": np.full(len(mu), sigma),
            "probability_below_threshold": p,
            "interval80_low_db": mu - 1.2815515655 * sigma,
            "interval80_high_db": mu + 1.2815515655 * sigma}


def sample_land_aware_indices(lden: np.ndarray, land_mask: np.ndarray,
                              sample_n: int, seed: int) -> tuple[np.ndarray, np.ndarray, dict]:
    """Sample representative and balanced cells only from England land."""
    land_mask = np.asarray(land_mask, dtype=bool).ravel()
    finite = np.isfinite(np.asarray(lden).ravel())
    if len(land_mask) != len(finite):
        raise ValueError("land mask and target raster shapes do not agree")
    eligible = np.flatnonzero(land_mask)
    reported = eligible[finite[eligible]]
    censored = eligible[~finite[eligible]]
    if not len(eligible) or not len(reported) or not len(censored):
        raise ValueError("land-aware sample needs both reported and censored England cells")
    rng = np.random.default_rng(seed)
    representative = rng.choice(eligible, size=min(sample_n, len(eligible)), replace=False)
    per_class = min(sample_n // 2, len(reported), len(censored))
    balanced = np.r_[rng.choice(reported, size=per_class, replace=False),
                     rng.choice(censored, size=per_class, replace=False)]
    return representative, balanced, {
        "total_cells": int(len(land_mask)), "eligible_land_cells": int(len(eligible)),
        "excluded_non_land_cells": int((~land_mask).sum()),
        "reported_land_cells": int(len(reported)), "censored_land_cells": int(len(censored)),
    }


def phase2c_metrics(y: np.ndarray, censored: np.ndarray, prediction: dict,
                    weights: np.ndarray, threshold: float) -> dict:
    """Censor-aware metrics including boundary bands and reported-cell rank."""
    result = weighted_metrics(y, censored, prediction, weights, threshold)
    observed = ~np.asarray(censored, dtype=bool) & np.isfinite(y)
    mu = np.asarray(prediction["mu_db"], dtype=float)
    if observed.sum() >= 3:
        result["spearman_rank_reported"] = float(spearmanr(np.asarray(y)[observed], mu[observed]).statistic)
    else:
        result["spearman_rank_reported"] = None
    bands = ([("40_42", 40.0, 42.0), ("42_45", 42.0, 45.0), ("45_50", 45.0, 50.0)]
             if threshold >= 40.0 else [("35_37", 35.0, 37.0), ("37_40", 37.0, 40.0), ("40_45", 40.0, 45.0)])
    for label, lo, hi in bands:
        mask = observed & (np.asarray(y) >= lo) & (np.asarray(y) < hi)
        result[f"{label}_n"] = int(mask.sum())
        if mask.any():
            error = mu[mask] - np.asarray(y)[mask]
            result[f"{label}_mae_db"] = float(np.average(np.abs(error), weights=np.asarray(weights)[mask]))
            result[f"{label}_bias_db"] = float(np.average(error, weights=np.asarray(weights)[mask]))
            result[f"{label}_spearman"] = float(spearmanr(np.asarray(y)[mask], mu[mask]).statistic) if mask.sum() >= 3 else None
        else:
            result[f"{label}_mae_db"] = None
            result[f"{label}_bias_db"] = None
            result[f"{label}_spearman"] = None
    return result


def read_land_window(mask_path: str | Path, bbox: tuple[float, float, float, float],
                     shape: tuple[int, int]) -> np.ndarray:
    """Read an exact 100 m England mask window for a 10 km target."""
    with rasterio.open(mask_path) as dataset:
        values = dataset.read(1, window=rasterio.windows.from_bounds(*bbox, transform=dataset.transform),
                              out_shape=shape, resampling=rasterio.enums.Resampling.nearest,
                              boundless=True, fill_value=0)
    return values > 0


def traffic_assignment_summary(records: list[dict]) -> list[dict]:
    """Summarise match quality by road class and category."""
    rows = []
    classes = sorted({str(record.get("road_class", "unknown")) for record in records})
    categories = sorted({str(record.get("match_category", "unknown")) for record in records})
    for road_class in classes:
        subset = [record for record in records if str(record.get("road_class")) == road_class]
        for category in categories:
            category_rows = [record for record in subset if str(record.get("match_category")) == category]
            if not category_rows:
                continue
            distances = [float(record["match_distance_m"]) for record in category_rows if record.get("match_distance_m") not in ("", None)]
            rows.append({"road_class": road_class, "match_category": category, "n": len(category_rows),
                         "fraction_of_class": len(category_rows) / max(len(subset), 1),
                         "median_match_distance_m": float(np.median(distances)) if distances else None,
                         "median_match_score": float(np.median([float(record["match_score"]) for record in category_rows if record.get("match_score") not in ("", None)])) if any(record.get("match_score") not in ("", None) for record in category_rows) else None})
    return rows


def utc_manifest(base: dict | None = None) -> dict:
    payload = dict(base or {})
    payload.update({"phase": "2C", "created_at": payload.get("created_at", utc_now()), "updated_at": utc_now(),
                    "phase1_national_tiles_modified": False, "national_phase2_run": False})
    return payload
