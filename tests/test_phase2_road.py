import numpy as np
from rasterio.transform import from_origin
from shapely.geometry import LineString, Point

from quiet_uk.phase2_road import build_features, fit_tobit, metrics_for_predictions, predict_tobit


def _sources():
    return [
        {
            "geometry": LineString([(0, 10), (1000, 10)]),
            "flow": 10000.0,
            "hgv_flow": 500.0,
            "hgv_share": 0.05,
            "is_counted": 1.0,
            "road_class": "a_road",
        },
        {
            "geometry": Point(500, 500),
            "flow": 500.0,
            "hgv_flow": 20.0,
            "hgv_share": 0.04,
            "is_counted": 0.0,
            "road_class": "minor",
        },
    ]


def test_phase2_features_have_multi_road_energy_and_terrain_fields():
    xs = np.array([100.0, 900.0])
    ys = np.array([100.0, 100.0])
    dtm = np.add.outer(np.arange(100, dtype=float), np.arange(100, dtype=float))
    features = build_features(xs, ys, _sources(), dtm, from_origin(0, 1000, 10, 10), include_terrain=True)
    assert np.isfinite(features["log10_traffic_energy_1000m"]).all()
    assert np.isfinite(features["log10_hgv_energy_1000m"]).all()
    assert np.isfinite(features["terrain_obstruction_max_m"]).all()
    assert list(features["nearest_road_class"]) == ["a_road", "a_road"]


def test_tobit_respects_left_censoring_without_treating_censored_as_zero():
    rng = np.random.default_rng(4)
    x = np.column_stack([np.ones(160), rng.normal(size=160)])
    latent = 36.0 + 5.0 * x[:, 1]
    censored = latent < 40.0
    y = np.where(censored, 40.0, latent)
    model = fit_tobit(x, y, censored)
    prediction = predict_tobit(model, x)
    assert model["success"]
    assert np.mean(prediction["mu_db"][censored] < 40.0) > 0.6
    metrics = metrics_for_predictions(y, censored, prediction)
    assert metrics["censored_violation_fraction"] < 0.4
