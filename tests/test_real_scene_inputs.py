"""Shared memory-mapped terrain must hold the GeoTIFF's own pixels and detect damage."""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from quiet_uk import real_scene_inputs


@pytest.fixture
def terrain(tmp_path, monkeypatch):
    monkeypatch.setattr(real_scene_inputs, 'RASTER_CACHE', tmp_path / 'cache')
    monkeypatch.setattr(real_scene_inputs, 'assert_native_grid', lambda dataset, bounds: None)
    values = np.arange(12, dtype='float32').reshape(3, 4) + .25
    values[1, 2] = np.nan
    path = tmp_path / 'terrain.tif'
    with rasterio.open(path, 'w', driver='GTiff', width=4, height=3, count=1, dtype='float32',
                       crs='EPSG:27700', transform=from_origin(0, 6, 2, 2), nodata=-9999) as d:
        d.write(values, 1)
    with rasterio.open(path) as d:
        return path, d.read(1), d.read_masks(1)


def test_shared_band_matches_rasterio_read_and_is_read_only(terrain):
    path, values, mask = terrain
    shared_values, shared_mask = real_scene_inputs.shared_band(path, None)
    assert isinstance(shared_values, np.memmap)
    np.testing.assert_array_equal(shared_values, values)
    np.testing.assert_array_equal(shared_mask, mask)
    assert shared_values.dtype == values.dtype and not shared_values.flags.writeable
    again, _ = real_scene_inputs.shared_band(path, None)  # reuses the cache
    np.testing.assert_array_equal(again, values)


def test_damaged_cache_is_rejected(terrain):
    path, _, _ = terrain
    real_scene_inputs.shared_band(path, None)
    cached = next(real_scene_inputs.RASTER_CACHE.glob('*/values.npy'))
    data = bytearray(cached.read_bytes()); data[-1] ^= 1; cached.write_bytes(bytes(data))
    with pytest.raises(ValueError, match='damaged'):
        real_scene_inputs.shared_band(path, None)
