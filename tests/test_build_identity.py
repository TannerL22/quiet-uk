import copy
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

import quiet_uk.build_identity as build_identity_module
from quiet_uk.build_identity import IMPLEMENTATION_FILES, compute_build_identity


def _mask(path):
    profile = {
        "driver": "GTiff", "height": 1, "width": 1, "count": 1, "dtype": "uint8",
        "crs": "EPSG:27700", "transform": Affine(100, 0, 0, 0, -100, 100), "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.ones((1, 1), dtype="uint8"), 1)


def _config():
    return {
        "crs": "EPSG:27700", "metric": "Lden", "pilot_resolution_m": 10.0,
        "output_resolution_m": 100, "tile_size_m": 10000,
        "reporting_threshold_db": {"road": 40, "rail": 40.0, "airport": None},
        "wcs": {"road": "road", "rail": "rail", "airport": "airport"},
        "coverage_ids": {"road": "road_Lden", "rail": "rail_Lden", "airport": "airport_Lden"},
        "wcs_versions": {"road": "1.0.0", "rail": "1.0.0", "airport": "2.0.1"},
        "wcs_formats": {"rail": "GeoTIFF"},
        "coverage_bounds_epsg27700": {"airport": [0, 0, 100, 100]},
        "runner": {"max_workers": 1, "max_attempts": 1, "progress": True},
    }


def test_equivalent_dictionary_order_and_defaults_have_same_fingerprint(tmp_path):
    mask = tmp_path / "mask.tif"
    _mask(mask)
    first = _config()
    second = copy.deepcopy(first)
    second["wcs"] = {"airport": "airport", "road": "road", "rail": "rail"}
    second["coverage_ids"] = {"airport": "airport_Lden", "road": "road_Lden", "rail": "rail_Lden"}
    second["wcs_versions"] = {"airport": "2.0.1", "road": "1.0.0", "rail": "1.0.0"}
    second["wcs_formats"] = {"airport": "image/tiff", "road": "GeoTIFF", "rail": "GeoTIFF"}
    second["coverage_bounds_epsg27700"] = {"airport": [0.0, 0.0, 100.0, 100.0]}
    second["runner"].update(max_workers=8, max_attempts=9, progress=False)
    left = compute_build_identity(first, mask)
    right = compute_build_identity(second, mask)
    assert left["fingerprint"] == right["fingerprint"]


def test_nonfinite_configuration_numbers_are_rejected(tmp_path):
    mask = tmp_path / "mask.tif"
    _mask(mask)
    config = _config()
    config["reporting_threshold_db"]["road"] = math.nan
    with pytest.raises(ValueError, match="non-finite"):
        compute_build_identity(config, mask)


def test_implementation_identity_is_explicit_and_excludes_unrelated_research_files():
    assert "src/quiet_uk/acoustics.py" in IMPLEMENTATION_FILES
    assert "src/quiet_uk/validation.py" in IMPLEMENTATION_FILES
    assert "src/quiet_uk/build_identity.py" in IMPLEMENTATION_FILES
    assert "src/quiet_uk/locking.py" in IMPLEMENTATION_FILES
    assert "src/quiet_uk/source_grid.py" in IMPLEMENTATION_FILES
    assert "README.md" not in IMPLEMENTATION_FILES
    assert "src/quiet_uk/phase2c_road.py" not in IMPLEMENTATION_FILES


def test_implementation_identity_supports_installed_package_layout(tmp_path, monkeypatch):
    source_package = Path(build_identity_module.__file__).resolve().parent
    installed_package = tmp_path / "site-packages" / "quiet_uk"
    shutil.copytree(source_package, installed_package)
    monkeypatch.setattr(build_identity_module, "__file__", str(installed_package / "build_identity.py"))

    hashes = build_identity_module._implementation_hashes()

    assert set(hashes) == set(IMPLEMENTATION_FILES)
    assert all(len(value) == 64 for value in hashes.values())


def test_identity_exposes_named_source_grid_policies(tmp_path):
    mask = tmp_path / "mask.tif"
    _mask(mask)
    identity = compute_build_identity(_config(), mask)
    policies = identity["payload"]["source_grid_policies"]
    assert policies["version"] == "1"
    assert policies["road"]["accepted"][0]["name"] == "exact-target-grid"
    assert [item["name"] for item in policies["airport"]["accepted"]] == [
        "exact-target-grid", "airport-wcs20-padded-native-grid"
    ]
    assert identity["payload"]["alignment"]["permitted_alignment"].startswith("only named")
    assert identity["payload"]["source_decoding"]["decoded_nonfinite_values_rejected"] is True
