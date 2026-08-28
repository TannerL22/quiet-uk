import csv
import sqlite3
import struct

import numpy as np
import pytest
from shapely import wkb
from shapely.geometry import LineString

from quiet_uk.phase2b_road import (
    PHASE2B_REGIONS,
    build_phase2b_features,
    fit_tobit_weighted,
    infer_thresholds,
    load_dft_aadf_year,
    load_os_open_roads_sources,
    predict_tobit,
)


def _gpkg_blob(geometry):
    return b"GP" + bytes([0, 1]) + struct.pack("<i", 27700) + wkb.dumps(geometry)


def test_exact_2021_selection_and_no_fallback(tmp_path):
    path = tmp_path / "aadf.csv"
    fields = ["count_point_id", "year", "all_motor_vehicles", "all_HGVs", "easting", "northing", "estimation_method"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"count_point_id": "1", "year": "2021", "all_motor_vehicles": "1000", "all_HGVs": "50", "easting": "500000", "northing": "200000", "estimation_method": "Counted"})
        writer.writerow({"count_point_id": "1", "year": "2025", "all_motor_vehicles": "2000", "all_HGVs": "100", "easting": "500000", "northing": "200000", "estimation_method": "Estimated"})
    rows = load_dft_aadf_year(path, 2021)
    assert len(rows) == 1
    assert rows[0]["year"] == 2021
    assert rows[0]["flow"] == 1000
    with pytest.raises(ValueError, match="year=2019"):
        load_dft_aadf_year(path, 2019)


def test_weighted_censored_likelihood_corrects_deliberate_class_oversampling():
    rng = np.random.default_rng(21)
    latent = rng.normal(38.0, 5.0, 20_000)
    censored = latent < 40.0
    observed = latent[~censored]
    below = latent[censored]
    # Deliberately retain nearly all censored values but only a small fraction
    # of reported values, then restore their population sampling probability.
    keep_observed = rng.choice(len(observed), size=1500, replace=False)
    keep_censored = rng.choice(len(below), size=1500, replace=False)
    y = np.r_[observed[keep_observed], np.full(len(keep_censored), 40.0)]
    c = np.r_[np.zeros(len(keep_observed), dtype=bool), np.ones(len(keep_censored), dtype=bool)]
    w = np.r_[np.full(len(keep_observed), len(observed) / len(keep_observed)),
              np.full(len(keep_censored), len(below) / len(keep_censored))]
    x = np.ones((len(y), 1))
    model = fit_tobit_weighted(x, y, c, w, 40.0)
    pred = predict_tobit(model, x)
    population_fraction = float(censored.mean())
    assert abs(float(pred["probability_below_threshold"][0]) - population_fraction) < 0.08


def test_os_continuous_linkage_labels_direct_and_imputed(tmp_path):
    path = tmp_path / "roads.gpkg"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE road_link (fid INTEGER PRIMARY KEY, geometry BLOB, id TEXT, road_classification TEXT, road_function TEXT, road_classification_number TEXT, name_1 TEXT, length REAL);"
        "CREATE VIRTUAL TABLE rtree_road_link_geometry USING rtree(id,minx,maxx,miny,maxy);"
    )
    direct = LineString([(0, 0), (1000, 0)])
    imputed = LineString([(0, 1000), (1000, 1000)])
    for fid, geometry, classification in ((1, direct, "A Road"), (2, imputed, "B Road")):
        con.execute("INSERT INTO road_link VALUES (?,?,?,?,?,?,?,?)", (fid, _gpkg_blob(geometry), f"r{fid}", classification, classification, None, None, geometry.length))
        con.execute("INSERT INTO rtree_road_link_geometry VALUES (?,?,?,?,?)", (fid, 0, 1000, geometry.bounds[1], geometry.bounds[3]))
    con.commit()
    con.close()
    dft = [{"geometry": direct, "flow": 1000.0, "hgv_flow": 50.0, "hgv_share": 0.05,
            "road_class": "a_road", "road_name": "A1", "road_id": "1",
            "traffic_source": "counted", "traffic_confidence": "Counted",
            "hgv_confidence": "direct_DfT", "geometry_kind": "DfT_MRDB_line"}]
    sources, counts = load_os_open_roads_sources(path, dft, (-10, -10, 1010, 1010), "rural")
    assert len(sources) == 2
    assert counts["direct_traffic_links"] == 1
    assert counts["imputed_traffic_links"] == 1
    assert {source["traffic_source"] for source in sources} == {"counted", "imputed"}
    assert all(source["geometry_kind"] == "OS_Open_Roads_line" for source in sources)


def test_line_source_energy_and_distance_are_monotonic():
    low = {"geometry": LineString([(-1000, 0), (1000, 0)]), "flow": 1000.0, "hgv_flow": 50.0, "hgv_share": 0.05,
           "road_class": "a_road", "traffic_source": "counted", "traffic_confidence": "Counted", "speed_kmh": 80.0}
    high = {**low, "flow": 10000.0, "hgv_flow": 500.0}
    xs = np.zeros(4)
    ys = np.array([20.0, 100.0, 20.0, 100.0])
    low_features = build_phase2b_features(xs, ys, [low], radius=5000)
    high_features = build_phase2b_features(xs, ys, [high], radius=5000)
    assert np.all(high_features["log10_line_emission_energy_10000m"] > low_features["log10_line_emission_energy_10000m"])
    assert low_features["log10_line_emission_energy_10000m"][0] > low_features["log10_line_emission_energy_10000m"][1]


def test_speed_missingness_is_explicit_and_imputed_speed_is_not_observed():
    source = {"geometry": LineString([(0, 0), (1000, 0)]), "flow": 1000.0, "hgv_flow": 50.0, "hgv_share": 0.05,
              "road_class": "b_road", "traffic_source": "imputed", "traffic_confidence": "imputed_class_median", "speed_kmh": 60.0}
    features = build_phase2b_features(np.array([0.0]), np.array([100.0]), [source])
    assert features["speed_coverage"].tolist() == [0.0]
    assert features["speed_imputed_kmh"].tolist() == [60.0]


def test_lden_and_lnight_thresholds_are_separate():
    result = infer_thresholds([
        {"lden_min_positive_db": 40.0, "lnight_min_positive_db": 35.0},
        {"lden_min_positive_db": 40.0, "lnight_min_positive_db": 35.0},
    ])
    assert result["lden_threshold_inferred_db"] == 40.0
    assert result["lnight_threshold_inferred_db"] == 35.0


def test_phase2b_holdout_regions_are_unique():
    ids = [region.region_id for region in PHASE2B_REGIONS]
    assert len(ids) == len(set(ids))
    assert len(ids) >= 9
