import copy
import json

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from quiet_uk import tiling
from quiet_uk.tiling import make_tiles, mosaic_tiles, process_tile, tile_grid, validate_tile_output


def _geo_tiff_bytes(data, transform, nodata=None, scales=None, offsets=None):
    mem = MemoryFile()
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": str(data.dtype),
        "crs": "EPSG:27700",
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with mem.open(**profile) as dst:
        dst.write(data, 1)
        if scales is not None:
            dst.scales = list(scales)
        if offsets is not None:
            dst.offsets = list(offsets)
    payload = mem.read()
    mem.close()
    return payload


def test_make_tiles_has_100m_aligned_2x2_core_grid():
    tiles = make_tiles((503000, 171000, 513000, 181000), tile_size_m=5000)
    assert len(tiles) == 4
    assert [tile.tile_id for tile in tiles] == ["r0000c0000", "r0000c0001", "r0001c0000", "r0001c0001"]
    assert all(tile.output_shape == (50, 50) for tile in tiles)
    assert all(all(value % 100 == 0 for value in tile.bbox) for tile in tiles)
    assert tiles[0].bbox == (503000.0, 176000.0, 508000.0, 181000.0)


def test_make_tiles_snaps_non_100m_extent_to_aligned_edge():
    tiles = make_tiles((503000, 171000, 513050, 181000), tile_size_m=5000)
    assert max(tile.bbox[2] for tile in tiles) == 513100.0
    assert all(tile.width_m % 100 == 0 for tile in tiles)


def _write_synthetic_tile(path, left, bottom, value):
    profile = {
        "driver": "GTiff", "height": 2, "width": 2, "count": 4,
        "dtype": "float32", "crs": "EPSG:27700",
        "transform": Affine(100, 0, left, 0, -100, bottom + 200),
        "nodata": -9999.0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        for band in range(1, 5):
            dst.write(np.full((2, 2), value + band, dtype="float32"), band)
            dst.set_band_description(band, tiling.TILE_BANDS[band - 1])


def test_mosaic_rejects_gaps_and_accepts_exact_nonoverlap(tmp_path):
    paths = []
    for name, left, bottom, value in [
        ("nw", 0, 200, 10), ("ne", 200, 200, 20),
        ("sw", 0, 0, 30), ("se", 200, 0, 40),
    ]:
        path = tmp_path / f"{name}.tif"
        _write_synthetic_tile(path, left, bottom, value)
        paths.append(path)
    out = tmp_path / "mosaic.tif"
    result = mosaic_tiles(paths, out, expected_extent=(0, 0, 400, 400))
    assert result["gap_cells"] == 0
    assert result["overlap_cells"] == 0
    with rasterio.open(out) as ds:
        assert ds.shape == (4, 4)
        assert tuple(ds.transform) == (100.0, 0.0, 0.0, 0.0, -100.0, 400.0, 0.0, 0.0, 1.0)

    with pytest.raises(ValueError, match="uncovered"):
        mosaic_tiles(paths[:3], tmp_path / "gap.tif", expected_extent=(0, 0, 400, 400))


def test_process_tile_omits_fake_airport_upper_bound(monkeypatch, tmp_path):
    tile = make_tiles((503000, 171000, 504000, 172000), tile_size_m=1000)[0]
    road = np.full(tile.source_shape, 50.0, dtype="float32")
    rail = np.zeros(tile.source_shape, dtype="float32")
    airport = np.full((tile.source_shape[0] + 1, tile.source_shape[1] + 1), 60.0, dtype="float32")
    airport_transform = Affine(10, 0, tile.bbox[0] - 5, 0, -10, tile.bbox[3] + 5)
    payloads = {
        "road": _geo_tiff_bytes(road, Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3]), nodata=-96.0),
        "rail": _geo_tiff_bytes(rail, Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3]), nodata=-96.0),
        "airport": _geo_tiff_bytes(airport, airport_transform, nodata=3.4e38),
    }

    def fake_get_coverage(url, coverage_id, *args, **kwargs):
        return payloads[coverage_id]

    monkeypatch.setattr(tiling, "get_coverage", fake_get_coverage)
    config = {
        "crs": "EPSG:27700", "pilot_resolution_m": 10, "output_resolution_m": 100,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs": {name: name for name in ("road", "rail", "airport")},
        "coverage_ids": {name: name for name in ("road", "rail", "airport")},
        "wcs_versions": {"road": "1.0.0", "rail": "1.0.0", "airport": "2.0.1"},
    }
    result = process_tile(tile, config, tmp_path / "tile.tif", temp_root=tmp_path / "temp")
    assert result["temporary_10m_discarded"] is True
    with rasterio.open(tmp_path / "tile.tif") as ds:
        assert list(ds.descriptions) == list(tiling.TILE_BANDS)
        assert "combined_upper_db" not in ds.descriptions
        assert ds.read(4).min() == pytest.approx(1.0)
    assert not any((tmp_path / "temp").iterdir())


def test_process_tile_uses_shared_default_thresholds_when_omitted(monkeypatch, tmp_path):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    source_transform = Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3])
    payloads = {
        "road": _geo_tiff_bytes(np.zeros(tile.source_shape, dtype="float32"), source_transform, nodata=-96.0),
        "rail": _geo_tiff_bytes(np.zeros(tile.source_shape, dtype="float32"), source_transform, nodata=-96.0),
        "airport": _geo_tiff_bytes(np.zeros(tile.source_shape, dtype="float32"), source_transform, nodata=3.4e38),
    }

    def fake_get_coverage(url, coverage_id, *args, **kwargs):
        return payloads[coverage_id.split("_", 1)[0]]

    monkeypatch.setattr(tiling, "get_coverage", fake_get_coverage)
    explicit = _semantic_config()
    omitted = copy.deepcopy(explicit)
    omitted.pop("reporting_threshold_db")
    explicit_output = tmp_path / "explicit.tif"
    omitted_output = tmp_path / "omitted.tif"
    process_tile(tile, explicit, explicit_output, temp_root=tmp_path / "explicit-temp")
    process_tile(tile, omitted, omitted_output, temp_root=tmp_path / "omitted-temp")
    with rasterio.open(explicit_output) as left, rasterio.open(omitted_output) as right:
        np.testing.assert_array_equal(left.read(), right.read())


def _semantic_tile():
    return make_tiles((0, 0, 200, 200), tile_size_m=200, source_resolution_m=10, output_resolution_m=100)[0]


def _semantic_config(mask_path=None, airport_threshold=None):
    config = {
        "crs": "EPSG:27700",
        "pilot_resolution_m": 10,
        "output_resolution_m": 100,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": airport_threshold},
        "wcs": {name: f"https://example.test/{name}" for name in ("road", "rail", "airport")},
        "coverage_ids": {name: f"{name}_Lden" for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
    }
    if mask_path is not None:
        config["england_mask_100m_path"] = str(mask_path)
    return config


def _exact_source_transform(tile):
    return Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3])


def test_loader_accepts_geometrically_valid_all_nodata_road_response(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    payload = _geo_tiff_bytes(
        np.full(tile.source_shape, -96.0, dtype="float32"),
        _exact_source_transform(tile),
        nodata=-96.0,
    )
    monkeypatch.setattr(tiling, "get_coverage", lambda *args, **kwargs: payload)
    _, info = tiling._load_source_for_tile(
        "road", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
    )
    assert info["grid_policy_name"] == "exact-target-grid"
    assert info["valid_cells_after_alignment"] == 0
    assert info["geometric_support_cells"] == tile.source_shape[0] * tile.source_shape[1]
    assert info["read_diagnostics"]["reported_finite_cells"] == 0


def test_airport_wholly_outside_declared_coverage_skips_request(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["coverage_bounds_epsg27700"] = {"airport": [2000, 2000, 3000, 3000]}

    def unexpected_request(*args, **kwargs):
        raise AssertionError("outside airport coverage must not request WCS")

    monkeypatch.setattr(tiling, "get_coverage", unexpected_request)
    values, info = tiling._load_source_for_tile(
        "airport", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
    )
    assert np.isnan(values).all()
    assert info["skipped_outside_declared_coverage"] is True
    assert info["coverage_status"] == "outside_declared_coverage"
    assert info["inside_declared_coverage_cells"] == 0
    assert info["outside_declared_coverage_cells"] == values.size
    assert "not an inaudible" in info["read_diagnostics"]["provider_nodata_policy"]


def test_airport_partial_coverage_counts_centres_and_accepts_nodata_outside(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["coverage_bounds_epsg27700"] = {"airport": [0, 0, 500, 1000]}
    values = np.full(tile.source_shape, -96.0, dtype="float32")
    values[:, :50] = 60.0
    payload = _geo_tiff_bytes(values, _exact_source_transform(tile), nodata=-96.0)
    monkeypatch.setattr(tiling, "get_coverage", lambda *args, **kwargs: payload)
    aligned, info = tiling._load_source_for_tile(
        "airport", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
    )
    assert info["coverage_status"] == "partially_outside_declared_coverage"
    assert info["inside_declared_coverage_cells"] == 5000
    assert info["outside_declared_coverage_cells"] == 5000
    assert np.isfinite(aligned[:, :50]).all()
    assert np.isnan(aligned[:, 50:]).all()


def test_airport_finite_values_outside_declared_coverage_are_rejected(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["coverage_bounds_epsg27700"] = {"airport": [0, 0, 500, 1000]}
    payload = _geo_tiff_bytes(
        np.full(tile.source_shape, 60.0, dtype="float32"),
        _exact_source_transform(tile),
    )
    monkeypatch.setattr(tiling, "get_coverage", lambda *args, **kwargs: payload)
    with pytest.raises(ValueError, match="finite cells outside declared coverage"):
        tiling._load_source_for_tile(
            "airport", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
        )


def test_road_partial_declared_coverage_is_rejected_before_request(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["coverage_bounds_epsg27700"] = {"road": [0, 0, 505, 1000]}

    def unexpected_request(*args, **kwargs):
        raise AssertionError("incomplete road coverage must fail before WCS")

    monkeypatch.setattr(tiling, "get_coverage", unexpected_request)
    with pytest.raises(ValueError, match="incomplete road/rail domain"):
        tiling._load_source_for_tile(
            "road", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
        )


def test_missing_aligned_support_fails_without_using_acoustic_nodata(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["wcs_versions"]["airport"] = "2.0.1"
    raw = np.full((tile.source_shape[0] + 1, tile.source_shape[1] + 1), -96.0, dtype="float32")
    padded_transform = Affine(10, 0, tile.bbox[0] - 5, 0, -10, tile.bbox[3] + 5)
    payload = _geo_tiff_bytes(raw, padded_transform, nodata=-96.0)
    monkeypatch.setattr(tiling, "get_coverage", lambda *args, **kwargs: payload)
    original_support = tiling.align_support_to_grid

    def support_with_hole(source_grid, target_grid):
        support = original_support(source_grid, target_grid)
        support[0, 0] = False
        return support

    monkeypatch.setattr(tiling, "align_support_to_grid", support_with_hole)
    with pytest.raises(ValueError, match="lacks geometric support.*missing footprint"):
        tiling._load_source_for_tile(
            "airport", tile, config, tile_grid(tile, "EPSG:27700"), tmp_path
        )


def test_shifted_production_source_fails_before_output_publish(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    shifted = _geo_tiff_bytes(
        np.full(tile.source_shape, 50.0, dtype="float32"),
        Affine(10, 0, tile.bbox[0] + 5, 0, -10, tile.bbox[3]),
    )
    monkeypatch.setattr(tiling, "get_coverage", lambda *args, **kwargs: shifted)
    output = tmp_path / "shifted.tif"
    with pytest.raises(ValueError, match="Unsupported source grid for road"):
        process_tile(tile, config, output, temp_root=tmp_path / "temp")
    assert not output.exists()


def test_decoding_overflow_fails_before_acoustic_combination_or_publish(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    overflow = _geo_tiff_bytes(
        np.full(tile.source_shape, 1e308, dtype="float64"),
        _exact_source_transform(tile),
        scales=[10.0],
        offsets=[0.0],
    )
    calls = []

    def fake_get_coverage(url, coverage_id, *args, **kwargs):
        calls.append(coverage_id)
        return overflow

    monkeypatch.setattr(tiling, "get_coverage", fake_get_coverage)
    output = tmp_path / "overflow.tif"
    with pytest.raises(ValueError, match=r"Source road tile .*decoding failed: Decoded source values are non-finite"):
        process_tile(tile, config, output, temp_root=tmp_path / "temp")
    assert calls == ["road_Lden"]
    assert not output.exists()


def test_valid_three_source_tile_numeric_output_contract_is_unchanged(tmp_path, monkeypatch):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    config = _semantic_config()
    config["wcs_versions"]["airport"] = "2.0.1"
    transform = _exact_source_transform(tile)
    padded_transform = Affine(10, 0, tile.bbox[0] - 5, 0, -10, tile.bbox[3] + 5)
    payloads = {
        "road": _geo_tiff_bytes(np.full(tile.source_shape, 50.0, dtype="float32"), transform),
        "rail": _geo_tiff_bytes(np.full(tile.source_shape, 0.0, dtype="float32"), transform),
        "airport": _geo_tiff_bytes(
            np.full((tile.source_shape[0] + 1, tile.source_shape[1] + 1), 60.0, dtype="float32"),
            padded_transform,
        ),
    }
    monkeypatch.setattr(tiling, "get_coverage", lambda url, coverage_id, *args, **kwargs: payloads[coverage_id.split("_", 1)[0]])
    output = tmp_path / "valid.tif"
    process_tile(tile, config, output, temp_root=tmp_path / "temp")
    expected = np.array([
        10.0 * np.log10(10.0 ** 5 + 10.0 ** 6),
        10.0 * np.log10(10.0 ** 5 + 10.0 ** 4),
        60.0,
        1.0,
    ], dtype="float32")[:, None, None]
    with rasterio.open(output) as dataset:
        np.testing.assert_allclose(
            dataset.read(), np.broadcast_to(expected, (4, 10, 10)), rtol=0.0, atol=1e-5
        )


def _write_semantic_tile(path, arrays, names=None, nodata=-9999.0):
    names = names or list(tiling.TILE_BANDS)
    profile = {
        "driver": "GTiff", "height": arrays.shape[1], "width": arrays.shape[2],
        "count": arrays.shape[0], "dtype": "float32", "crs": "EPSG:27700",
        "transform": Affine(100, 0, 0, 0, -100, 200), "nodata": nodata,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        for index, (name, values) in enumerate(zip(names, arrays), start=1):
            dataset.write(np.asarray(values, dtype="float32"), index)
            dataset.set_band_description(index, name)


def _valid_standard_arrays():
    arrays = np.full((4, 2, 2), -9999.0, dtype="float64")
    arrays[0] = 50.0
    arrays[1] = 45.0
    arrays[2] = 48.0
    arrays[3] = 1.0
    return arrays


def test_validate_tile_output_accepts_valid_standard_and_all_censored_tiles(tmp_path):
    tile = _semantic_tile()
    standard = tmp_path / "standard.tif"
    _write_semantic_tile(standard, _valid_standard_arrays())
    assert validate_tile_output(standard, tile, _semantic_config())["valid"] is True

    censored = _valid_standard_arrays()
    censored[0] = -9999.0
    censored[2] = -9999.0
    censored[3] = 0.0
    censored_path = tmp_path / "censored.tif"
    _write_semantic_tile(censored_path, censored)
    assert validate_tile_output(censored_path, tile, _semantic_config())["valid"] is True


@pytest.mark.parametrize("value", [-0.1, 1.1])
def test_validate_tile_output_rejects_fraction_outside_range(tmp_path, value):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    arrays[3, 0, 0] = value
    path = tmp_path / "bad_fraction.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match="airport_reported_fraction is outside"):
        validate_tile_output(path, tile, _semantic_config())


@pytest.mark.parametrize(
    ("band", "message"),
    [(3, "airport_reported_fraction is missing"), (1, "road_rail_upper_db is missing")],
)
def test_validate_tile_output_rejects_required_land_band_missing(tmp_path, band, message):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    arrays[band, 0, 0] = -9999.0
    path = tmp_path / "missing.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match=message):
        validate_tile_output(path, tile, _semantic_config())


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_validate_tile_output_rejects_nonfinite_stored_values(tmp_path, value):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    arrays[0, 0, 0] = value
    path = tmp_path / "nonfinite.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match="NaN/infinite"):
        validate_tile_output(path, tile, _semantic_config())


def test_validate_tile_output_requires_exact_nodata_outside_land(tmp_path):
    tile = _semantic_tile()
    mask = tmp_path / "mask.tif"
    profile = {
        "driver": "GTiff", "height": 2, "width": 2, "count": 1, "dtype": "uint8",
        "crs": "EPSG:27700", "transform": Affine(100, 0, 0, 0, -100, 200), "nodata": 0,
    }
    with rasterio.open(mask, "w", **profile) as dataset:
        dataset.write(np.array([[1, 0], [1, 1]], dtype="uint8"), 1)
    arrays = _valid_standard_arrays()
    arrays[:, 0, 1] = -10000.0
    path = tmp_path / "outside.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match="outside land mask"):
        validate_tile_output(path, tile, _semantic_config(mask))


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda a: a.__setitem__((3, 0, 0), 0.0), "fraction is zero"),
        (lambda a: a.__setitem__((2, 0, 0), -9999.0), "positive airport fraction lacks"),
        (lambda a: a.__setitem__((0, 0, 0), -9999.0), "airport lower bound lacks"),
        (lambda a: a.__setitem__((0, 0, 0), 45.0), "combined lower bound is below"),
        (lambda a: a.__setitem__((1, 0, 0), 40.0), "road/rail upper bound is below"),
    ],
)
def test_validate_tile_output_rejects_cross_band_invariants(tmp_path, mutate, message):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    mutate(arrays)
    path = tmp_path / "inconsistent.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match=message):
        validate_tile_output(path, tile, _semantic_config())


def test_validate_tile_output_accepts_airport_inclusive_lower_above_road_rail_upper(tmp_path):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    arrays[0] = 60.0
    arrays[2] = 55.0
    arrays[1] = 45.0
    path = tmp_path / "airport_inclusive.tif"
    _write_semantic_tile(path, arrays)
    assert validate_tile_output(path, tile, _semantic_config())["valid"] is True


def test_validate_tile_output_supports_valid_and_invalid_combined_upper(tmp_path):
    tile = _semantic_tile()
    names = list(tiling.TILE_BANDS) + ["combined_upper_db"]
    arrays = np.concatenate([_valid_standard_arrays(), np.full((1, 2, 2), 62.0)])
    valid_path = tmp_path / "combined_upper.tif"
    _write_semantic_tile(valid_path, arrays, names)
    assert validate_tile_output(valid_path, tile, _semantic_config(airport_threshold=40.0), names)["valid"] is True

    arrays[4, 0, 0] = 44.0
    invalid_path = tmp_path / "combined_upper_bad.tif"
    _write_semantic_tile(invalid_path, arrays, names)
    with pytest.raises(ValueError, match="combined_upper_db is below"):
        validate_tile_output(invalid_path, tile, _semantic_config(airport_threshold=40.0), names)


def test_validate_tile_output_without_land_mask_validates_complete_tile(tmp_path):
    tile = _semantic_tile()
    arrays = _valid_standard_arrays()
    arrays[3, 0, 0] = -9999.0
    path = tmp_path / "no_mask_missing_fraction.tif"
    _write_semantic_tile(path, arrays)
    with pytest.raises(ValueError, match="airport_reported_fraction is missing"):
        validate_tile_output(path, tile, _semantic_config())
