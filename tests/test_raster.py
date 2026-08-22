import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from quiet_uk.raster import align_array_to_grid, grids_match, read_single_band_db


def _memory_raster(data, **profile_overrides):
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
