import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from quiet_uk.raster import align_array_to_grid, align_support_to_grid, grids_match, read_single_band_db


def _memory_raster(data, mask=None, **profile_overrides):
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": data.dtype,
        "crs": "EPSG:27700",
        "transform": from_origin(503000, 181000, 10, 10),
    }
    profile.update(profile_overrides)
    mem = MemoryFile()
    with mem.open(**profile) as dst:
        dst.write(data, 1)
        if "scales" in profile_overrides:
            dst.scales = [profile_overrides["scales"][0]]
        if "offsets" in profile_overrides:
            dst.offsets = [profile_overrides["offsets"][0]]
        if mask is not None:
            dst.write_mask(mask)
    return mem


def test_zero_and_declared_nodata_are_censored():
    mem = _memory_raster(
        np.array([[0.0, -96.0, 42.5]], dtype="float32"),
        nodata=-96.0,
    )
    with mem.open() as ds:
        values, diag = read_single_band_db(ds)
    assert np.isnan(values[0, 0])
    assert np.isnan(values[0, 1])
    assert values[0, 2] == pytest.approx(42.5)
    assert diag["zero_censored_cells"] == 1


def test_integer_scaled_encoding_is_decoded_before_censoring():
    mem = _memory_raster(
        np.array([[0, 4000, 65535]], dtype="uint16"),
        nodata=65535,
        scales=[0.01],
        offsets=[0.0],
    )
    with mem.open() as ds:
        values, _ = read_single_band_db(ds)
    assert np.isnan(values[0, 0])
    assert values[0, 1] == pytest.approx(40.0)
    assert np.isnan(values[0, 2])


def test_zero_censoring_is_applied_after_scale_and_offset():
    mem = _memory_raster(
        np.array([[100, 101]], dtype="uint16"),
        scales=[0.01],
        offsets=[-1.0],
    )
    with mem.open() as ds:
        values, diag = read_single_band_db(ds)
    assert np.isnan(values[0, 0])
    assert values[0, 1] == pytest.approx(0.01)
    assert diag["literal_zero_censored_cells"] == 1


@pytest.mark.parametrize(
    ("data", "scale", "offset"),
    [
        (np.array([[1e308]], dtype="float64"), 10.0, 0.0),
        (np.array([[1e308]], dtype="float64"), 1.0, 1e308),
        (np.array([[-1e308]], dtype="float64"), 10.0, 0.0),
    ],
)
def test_decoded_scale_offset_nonfinite_values_are_rejected(data, scale, offset):
    mem = _memory_raster(data, scales=[scale], offsets=[offset])
    with mem.open() as ds:
        with pytest.raises(ValueError, match=r"Decoded source values are non-finite.*eligible_cell_count=1"):
            read_single_band_db(ds)


def test_excluded_nodata_is_not_decoded_or_rejected_for_overflow():
    mem = _memory_raster(
        np.array([[1e308, 42.0]], dtype="float64"),
        nodata=-96.0,
        scales=[10.0],
        offsets=[0.0],
        mask=np.array([[0, 255]], dtype="uint8"),
    )
    with mem.open() as ds:
        values, diagnostics = read_single_band_db(ds)
    assert np.isnan(values[0, 0])
    assert values[0, 1] == pytest.approx(420.0)
    assert diagnostics["raster_mask_cells"] == 1


def test_large_but_finite_decoded_values_are_not_reclassified_as_censored():
    mem = _memory_raster(
        np.array([[100.0]], dtype="float64"), scales=[1.0], offsets=[0.0]
    )
    with mem.open() as ds:
        values, _ = read_single_band_db(ds)
    assert values[0, 0] == pytest.approx(100.0)


def test_unexpected_multiband_raster_is_rejected():
    mem = MemoryFile()
    with mem.open(
        driver="GTiff", height=1, width=1, count=2, dtype="float32",
        crs="EPSG:27700", transform=from_origin(503000, 181000, 10, 10)
    ) as dst:
        dst.write(np.ones((2, 1, 1), dtype="float32"))
    with mem.open() as ds:
        with pytest.raises(ValueError, match="one raster band"):
            read_single_band_db(ds)


def test_grid_match_tolerates_serialization_noise_but_not_pixel_shift():
    base = {
        "shape": (1000, 1000),
        "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 503000.0, 0.0, -10.0, 181000.0, 0.0, 0.0, 1.0),
        "bounds": (503000.0, 171000.0, 513000.0, 181000.0),
    }
    near = {**base, "transform": (*base["transform"][:2], 503000.0000001, *base["transform"][3:])}
    shifted = {**base, "bounds": (503010.0, 171000.0, 513020.0, 181000.0)}
    assert grids_match(base, near)
    assert not grids_match(base, shifted)


def test_alignment_reprojects_shifted_grid_to_reference_shape():
    source = {
        "shape": (2, 2), "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 503005.0, 0.0, -10.0, 171995.0, 0.0, 0.0, 1.0),
        "bounds": (503005.0, 171975.0, 503025.0, 171995.0),
    }
    target = {
        "shape": (2, 2), "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 503000.0, 0.0, -10.0, 172000.0, 0.0, 0.0, 1.0),
        "bounds": (503000.0, 171980.0, 503020.0, 172000.0),
    }
    out = align_array_to_grid(np.array([[50.0, 51.0], [52.0, 53.0]]), source, target)
    assert out.shape == (2, 2)
    assert np.isfinite(out).all()


def test_encoding_diagnostics_are_explicit_and_zero_count_has_exclusive_semantics():
    mem = _memory_raster(
        np.array([[0.0, -96.0, 42.5]], dtype="float32"),
        nodata=-96.0,
    )
    with mem.open() as ds:
        values, diag = read_single_band_db(ds)
    assert np.isnan(values[0, 0]) and np.isnan(values[0, 1])
    assert values[0, 2] == pytest.approx(42.5)
    assert diag["reported_finite_cells"] == 1
    assert diag["literal_zero_censored_cells"] == 1
    assert diag["zero_censored_cells"] == 1
    assert diag["declared_nodata_cells_overlapping"] == 1
    assert diag["provider_nodata_policy"].startswith("declared nodata")


@pytest.mark.parametrize("value", [np.nan, np.inf, -np.inf])
def test_unexpected_raw_nonfinite_values_are_rejected(value):
    mem = _memory_raster(np.array([[value]], dtype="float32"))
    with mem.open() as ds:
        with pytest.raises(ValueError, match=r"Unexpected raw non-finite.*count=1"):
            read_single_band_db(ds)


def test_declared_nan_nodata_is_supported_explicitly():
    mem = _memory_raster(
        np.array([[np.nan, 42.5]], dtype="float32"),
        nodata=np.nan,
    )
    with mem.open() as ds:
        values, diag = read_single_band_db(ds)
    assert np.isnan(values[0, 0])
    assert values[0, 1] == pytest.approx(42.5)
    assert diag["raw_nonfinite_cells"] == 1
    assert diag["unexpected_raw_nonfinite_cells"] == 0
    assert diag["declared_nodata_cells_overlapping"] == 1


def test_support_alignment_is_independent_of_all_nodata_values():
    source = {
        "shape": (3, 3), "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 0.0, 0.0, -10.0, 30.0, 0.0, 0.0, 1.0),
        "bounds": (0.0, 0.0, 30.0, 30.0),
    }
    target = {
        "shape": (2, 2), "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 5.0, 0.0, -10.0, 25.0, 0.0, 0.0, 1.0),
        "bounds": (5.0, 5.0, 25.0, 25.0),
    }
    support = align_support_to_grid(source, target)
    assert support.shape == target["shape"]
    assert support.all()
