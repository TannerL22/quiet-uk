import csv
import struct

import numpy as np
from shapely import wkb
from shapely.geometry import LineString

from quiet_uk.phase2b_road import fit_tobit_weighted, infer_thresholds, load_dft_aadf_year
from quiet_uk.phase2c_road import (
    assign_traffic_two_pass,
    build_phase2c_features,
    combine_rolling_propulsion_db,
    fit_bounded_tobit,
    predict_bounded_tobit,
    sample_land_aware_indices,
    source_emission_energy,
)


def _dft(dft_id, line, road_number="A1", flow=1000.0, hgv_flow=50.0):
    return {"dft_id": dft_id, "geometry": line, "flow": flow, "hgv_flow": hgv_flow,
            "hgv_share": hgv_flow / flow, "road_class": "a_road", "road_name": road_number,
            "road_number": road_number, "road_function": "Major", "traffic_source": "counted",
            "traffic_confidence": "Counted", "hgv_confidence": "direct_DfT"}


def _os(link_id, line, road_number="A1", road_class="a_road"):
    return {"link_id": link_id, "fid": int(link_id[1:]), "geometry": line,
            "road_class": road_class, "road_classification": "A Road", "road_function": "A Road",
            "road_number": road_number, "road_name": road_number, "length_m": line.length,
            "orientation_deg": 0.0}


def _assignment_values(rows):
    return {row["link_id"]: (row["flow"], row["hgv_flow"], row["traffic_confidence"],
                             row["match_category"], row["imputation_method"])
            for row in rows}


def test_two_pass_assignment_is_invariant_to_os_link_order():
    dft = [_dft("d1", LineString([(0, 0), (1000, 0)]), "A1"),
           _dft("d2", LineString([(0, 2000), (1000, 2000)]), "A2", 4000, 100)]
    links = [_os("l1", LineString([(0, 5), (1000, 5)]), "A1"),
             _os("l2", LineString([(0, 2005), (1000, 2005)]), "A2"),
             _os("l3", LineString([(0, 1000), (1000, 1000)]), "A1")]
    baseline, _ = assign_traffic_two_pass(links, dft, "rural")
    for seed in range(5):
        shuffled = list(np.random.default_rng(seed).permutation(links))
        result, _ = assign_traffic_two_pass(shuffled, dft, "rural")
        assert _assignment_values(result) == _assignment_values(baseline)


def test_road_number_match_is_preferred_over_nearer_wrong_number():
    dft = [_dft("wrong", LineString([(0, 0), (1000, 0)]), "A2"),
           _dft("right", LineString([(0, 60), (1000, 60)]), "A1")]
    links = [_os("l1", LineString([(0, 50), (1000, 50)]), "A1")]
    result, _ = assign_traffic_two_pass(links, dft, "urban")
    assert result[0]["matched_dft_id"] == "right"
    assert result[0]["road_number_match"] == 1
    assert result[0]["match_category"] == "direct_high_confidence"


def test_hierarchical_imputation_uses_same_number_after_direct_pass():
    dft = [_dft("d1", LineString([(0, 0), (1000, 0)]), "A1", flow=1234.0, hgv_flow=123.0)]
    links = [_os("l1", LineString([(0, 0), (1000, 0)]), "A1"),
             _os("l2", LineString([(0, 1000), (1000, 1000)]), "A1")]
    result, counts = assign_traffic_two_pass(links, dft, "rural")
    imputed = next(row for row in result if row["link_id"] == "l2")
    assert imputed["imputation_method"] == "same_road_number_direct"
    assert imputed["flow"] == 1234.0
    assert imputed["imputation_support_n"] == 1
    assert counts["direct_traffic_links"] == 1


def test_land_aware_sampling_excludes_non_land_cells():
    lden = np.array([[40.0, np.nan], [np.nan, np.nan]])
    land = np.array([[True, False], [True, False]])
    representative, balanced, info = sample_land_aware_indices(lden, land, 2, 2)
    assert set(representative).issubset({0, 2})
    assert set(balanced) == {0, 2}
    assert info["excluded_non_land_cells"] == 2


def test_rolling_and_propulsion_are_combined_logarithmically():
    expected = 10.0 * np.log10(10.0 ** (60.0 / 10.0) + 10.0 ** (70.0 / 10.0))
    assert combine_rolling_propulsion_db(60.0, 70.0) == np.float64(expected)
    assert combine_rolling_propulsion_db(60.0, 70.0) > 70.0


def test_hgv_is_not_double_counted_by_share_multiplier():
    left = {"road_class": "a_road", "flow": 1000.0, "hgv_flow": 100.0, "hgv_share": 0.1, "speed_kmh": 80.0}
    right = {**left, "hgv_share": 0.9}
    assert source_emission_energy(left) == source_emission_energy(right)


def test_finite_line_source_is_segmentation_invariant():
    def source(line):
        return {"geometry": line, "flow": 1000.0, "hgv_flow": 50.0, "hgv_share": 0.05,
                "road_class": "a_road", "traffic_assignment_source": "direct",
                "traffic_confidence": "direct_high_confidence", "speed_kmh": 80.0,
                "match_category": "direct_high_confidence"}
    one = [source(LineString([(0, 0), (2000, 0)]))]
    two = [source(LineString([(0, 0), (1000, 0)])), source(LineString([(1000, 0), (2000, 0)]))]
    many = [source(LineString([(i, 0), (i + 100, 0)])) for i in range(0, 2000, 100)]
    for distance in (20.0, 100.0, 500.0, 1500.0):
        xs = np.array([1000.0])
        ys = np.array([distance])
        values = [build_phase2c_features(xs, ys, roads)["log10_finite_line_energy_5000m"][0]
                  for roads in (one, two, many)]
        assert max(values) - min(values) < 1e-10


def test_finite_line_traffic_and_distance_are_monotonic():
    low = {"geometry": LineString([(-1000, 0), (1000, 0)]), "flow": 1000.0, "hgv_flow": 50.0,
           "hgv_share": 0.05, "road_class": "a_road", "traffic_assignment_source": "direct",
           "traffic_confidence": "direct_high_confidence", "speed_kmh": 80.0,
           "match_category": "direct_high_confidence"}
    high = {**low, "flow": 10000.0, "hgv_flow": 500.0}
    distances = np.linspace(20.0, 2000.0, 20)
    low_values = build_phase2c_features(np.zeros(len(distances)), distances, [low])["log10_finite_line_energy_5000m"]
    high_values = build_phase2c_features(np.zeros(len(distances)), distances, [high])["log10_finite_line_energy_5000m"]
    assert np.all(np.diff(low_values) < 0)
    assert np.all(high_values > low_values)


def test_bounded_primary_formulation_cannot_predict_negative_db():
    rng = np.random.default_rng(3)
    x = np.column_stack([np.ones(200), rng.normal(size=200)])
    latent = 38.0 + 8.0 * x[:, 1]
    censored = latent < 40.0
    y = np.where(censored, 40.0, latent)
    weights = np.ones(len(y))
    model = fit_bounded_tobit(x, y, censored, weights, 40.0, floor_db=0.0)
    prediction = predict_bounded_tobit(model, np.array([[1.0, -100.0], [1.0, 100.0]]))
    assert np.all(prediction["mu_db"] >= 0.0)


def test_thresholds_and_exact_year_selection_remain_separate(tmp_path):
    path = tmp_path / "aadf.csv"
    fields = ["count_point_id", "year", "all_motor_vehicles", "all_HGVs", "easting", "northing", "estimation_method"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerow({"count_point_id": "1", "year": "2021", "all_motor_vehicles": "1000", "all_HGVs": "50", "easting": "500000", "northing": "200000", "estimation_method": "Counted"})
        writer.writerow({"count_point_id": "1", "year": "2025", "all_motor_vehicles": "2000", "all_HGVs": "100", "easting": "500000", "northing": "200000", "estimation_method": "Estimated"})
    assert load_dft_aadf_year(path, 2021)[0]["year"] == 2021
    thresholds = infer_thresholds([{"lden_min_positive_db": 40.0, "lnight_min_positive_db": 35.0}])
    assert thresholds["lden_threshold_inferred_db"] == 40.0
    assert thresholds["lnight_threshold_inferred_db"] == 35.0
