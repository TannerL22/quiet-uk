import copy
import math
from pathlib import Path
import runpy

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from quiet_uk.build_identity import compute_build_identity
from quiet_uk.config import normalize_production_config
from quiet_uk.tiling import _source_config


def _write_mask(path):
    profile = {
        "driver": "GTiff", "height": 1, "width": 1, "count": 1, "dtype": "uint8",
        "crs": "EPSG:27700", "transform": Affine(100, 0, 0, 0, -100, 100), "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.ones((1, 1), dtype="uint8"), 1)


def _config():
    return {
        "wcs": {source: f"https://example.test/{source}" for source in ("road", "rail", "airport")},
        "coverage_ids": {source: f"opaque-{source}" for source in ("road", "rail", "airport")},
        "wcs_versions": {source: "1.0.0" for source in ("road", "rail", "airport")},
    }


def test_normalization_returns_copy_and_applies_defaults_without_mutating_input(tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    original = copy.deepcopy(config)
    effective = normalize_production_config(config, mask_path=mask, require_mask=True)
    assert config == original
    assert effective is not config
    assert effective["england_mask_100m_path"] == str(mask.resolve())
    assert effective["reporting_threshold_db"] == {"road": 40.0, "rail": 40.0, "airport": None}
    assert effective["wcs_formats"] == {"road": "GeoTIFF", "rail": "GeoTIFF", "airport": "GeoTIFF"}


def test_empty_and_relative_mask_contract_is_resolved_against_current_directory(monkeypatch, tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    monkeypatch.chdir(tmp_path)
    config = _config()
    config["england_mask_100m_path"] = "mask.tif"
    effective = normalize_production_config(config, mask_path=Path("mask.tif"), require_mask=True)
    assert effective["england_mask_100m_path"] == str(mask.resolve())


@pytest.mark.parametrize("field", ["road", "rail", "airport"])
def test_missing_endpoint_or_coverage_id_is_rejected(tmp_path, field):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    for section in ("wcs", "coverage_ids"):
        config = _config()
        config[section][field] = "   "
        with pytest.raises(ValueError, match="non-empty string"):
            compute_build_identity(config, mask)


@pytest.mark.parametrize("value", [None, "", "   ", "3.0"])
def test_invalid_wcs_version_is_rejected(tmp_path, value):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config["wcs_versions"]["road"] = value
    with pytest.raises(ValueError, match="wcs_versions.road"):
        compute_build_identity(config, mask)


@pytest.mark.parametrize("source", ["road", "rail"])
def test_explicit_null_road_or_rail_threshold_is_rejected(tmp_path, source):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config["reporting_threshold_db"] = {source: None}
    with pytest.raises(ValueError, match=f"reporting_threshold_db.{source}"):
        compute_build_identity(config, mask)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_threshold_including_airport_is_rejected(tmp_path, value):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config["reporting_threshold_db"] = {"road": 40.0, "rail": 40.0, "airport": value}
    with pytest.raises(ValueError, match="non-finite"):
        compute_build_identity(config, mask)


def test_boolean_numeric_configuration_is_rejected(tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config["reporting_threshold_db"] = {"road": True, "rail": 40.0, "airport": None}
    with pytest.raises(ValueError, match="numeric"):
        compute_build_identity(config, mask)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("pilot_resolution_m", True), ("pilot_resolution_m", 0), ("pilot_resolution_m", -1),
        ("pilot_resolution_m", 10.5), ("output_resolution_m", 101),
        ("output_resolution_m", 0), ("tile_size_m", 150), ("tile_size_m", False),
    ],
)
def test_invalid_grid_numbers_are_rejected(tmp_path, field, value):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config[field] = value
    with pytest.raises(ValueError):
        compute_build_identity(config, mask)


@pytest.mark.parametrize(
    "bounds",
    [None, [], [0, 0, 1], [0, 0, 0, 1], [0, 1, 1, 1], [0, 0, math.nan, 1]],
)
def test_invalid_declared_coverage_bounds_are_rejected(tmp_path, bounds):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    config["coverage_bounds_epsg27700"] = {"airport": bounds}
    with pytest.raises(ValueError, match="coverage_bounds_epsg27700.airport"):
        compute_build_identity(config, mask)


def test_custom_format_is_preserved_and_protocol_defaults_are_effective(tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _config()
    del config["wcs_versions"]["airport"]
    config["wcs_formats"] = {"road": "application/x-custom"}
    effective = normalize_production_config(config, mask_path=mask, require_mask=True)
    assert _source_config(effective, "road") == (
        "https://example.test/road", "opaque-road", "1.0.0", "application/x-custom"
    )
    assert _source_config(effective, "airport") == (
        "https://example.test/airport", "opaque-airport", "1.0.0", "GeoTIFF"
    )
    effective["wcs_versions"]["airport"] = "2.0.1"
    effective["wcs_formats"]["airport"] = "image/tiff"
    assert _source_config(effective, "airport")[2:] == ("2.0.1", "image/tiff")


def test_airport_null_is_valid_and_keeps_four_band_schema(tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    identity = compute_build_identity(_config(), mask)
    assert identity["payload"]["reporting_threshold_db"]["airport"] is None
    assert identity["payload"]["output_band_schema"] == [
        "combined_reported_lower_db", "road_rail_upper_db",
        "airport_reported_lower_db", "airport_reported_fraction",
    ]


def test_batch_script_resolves_project_relative_mask_outside_project_directory(monkeypatch, tmp_path):
    project_root = Path(__file__).resolve().parents[1]
    namespace = runpy.run_path(str(project_root / "scripts" / "10_run_england.py"))
    raw = {"england_mask_100m_path": "data/processed/example-mask.tif"}
    monkeypatch.chdir(tmp_path)
    effective, resolved = namespace["_resolve_project_mask"](raw)
    assert resolved == (project_root / "data/processed/example-mask.tif").resolve()
    assert effective["england_mask_100m_path"] == str(resolved)
    assert raw["england_mask_100m_path"] == "data/processed/example-mask.tif"
