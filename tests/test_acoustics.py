import numpy as np
from quiet_uk.acoustics import db_sum, db_energy_mean, combine_censored_sources, aggregate_energy_bounds
from quiet_uk.wcs import choose_lden_identifier


def test_two_equal_sources_add_about_3db():
    out = db_sum(np.array([40.0]), np.array([40.0]))
    assert np.isclose(out[0], 43.0103, atol=1e-3)


def test_three_40db_sources_are_about_44_77db():
    out = db_sum(np.array([40.0]), np.array([40.0]), np.array([40.0]))
    assert np.isclose(out[0], 44.7712, atol=1e-3)


def test_energy_mean_not_arithmetic_mean():
    x = np.array([40.0, 50.0])
    assert db_energy_mean(x) > x.mean()


def test_censored_bounds():
    r = combine_censored_sources(
        {"road": np.array([50.0, np.nan]), "rail": np.array([np.nan, np.nan]), "airport": np.array([np.nan, np.nan])},
        {"road": 40.0, "rail": 40.0, "airport": 40.0},
    )
    assert np.isclose(r["lower_db"][0], 50.0, atol=1e-6)
    assert r["upper_db"][0] > 50.0
    assert np.isnan(r["lower_db"][1])
    assert np.isclose(r["upper_db"][1], 44.7712, atol=1e-3)


def test_aggregation_keeps_censored_cells_in_denominator():
    # 2x2 block with one known 50 dB fine cell and three cells with zero known
    # lower-bound energy. Ignoring blanks would return 50 dB; correct lower-bound
    # energy averaging over all 4 cells is ~43.98 dB.
    known = 10 ** (50 / 10)
    lo = np.array([[known, 0.0], [0.0, 0.0]])
    hi = np.full((2, 2), known)
    r = aggregate_energy_bounds(lo, hi, factor=2)
    assert np.isclose(r["lower_db"][0, 0], 43.9794, atol=1e-3)
    assert np.isclose(r["upper_db"][0, 0], 50.0, atol=1e-6)


def test_choose_lden_identifier_prefers_non_octave_all_layer():
    ids = ["road_Lnight_all", "road_Lden_63Hz", "road_Lden_all", "road_LAeq16h_all"]
    assert choose_lden_identifier(ids, "road") == "road_Lden_all"
