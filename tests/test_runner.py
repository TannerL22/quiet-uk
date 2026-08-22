import json

import numpy as np
import rasterio
from rasterio.transform import Affine

from quiet_uk import runner, tiling
from quiet_uk.tiling import make_tiles, process_tile


def _geo_tiff_bytes(data, transform, nodata=None):
    from rasterio.io import MemoryFile

    mem = MemoryFile()
    profile = {
        "driver": "GTiff", "height": data.shape[0], "width": data.shape[1],
        "count": 1, "dtype": str(data.dtype), "crs": "EPSG:27700",
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with mem.open(**profile) as dst:
        dst.write(data, 1)
    payload = mem.read()
    mem.close()
    return payload


def _synthetic_source_payloads(tile):
    source_transform = Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3])
    road = np.full(tile.source_shape, 50.0, dtype="float32")
    rail = np.full(tile.source_shape, 45.0, dtype="float32")
    airport = np.full(tile.source_shape, 55.0, dtype="float32")
    return {
        "road": _geo_tiff_bytes(road, source_transform, nodata=-96.0),
        "rail": _geo_tiff_bytes(rail, source_transform, nodata=-96.0),
        "airport": _geo_tiff_bytes(airport, source_transform, nodata=3.4e38),
    }


def _write_mask(path, bbox=(0, 0, 10000, 10000), value=1):
    height = int((bbox[3] - bbox[1]) / 100)
    width = int((bbox[2] - bbox[0]) / 100)
    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 1,
        "dtype": "uint8", "crs": "EPSG:27700",
        "transform": Affine(100, 0, bbox[0], 0, -100, bbox[3]), "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((height, width), value, dtype="uint8"), 1)


def test_outside_declared_coverage_skips_wcs_request(monkeypatch, tmp_path):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    called = []

    def fail_if_called(*args, **kwargs):
        called.append(True)
        raise AssertionError("out-of-coverage source should not be requested")

    monkeypatch.setattr(tiling, "get_coverage", fail_if_called)
    target = tiling.tile_grid(tile, "EPSG:27700")
    array, info = tiling._load_source_for_tile(
        "airport", tile,
        {
            "wcs": {"airport": "unused"},
            "coverage_ids": {"airport": "unused"},
            "coverage_bounds_epsg27700": {"airport": [5000, 5000, 6000, 6000]},
        }, target, tmp_path,
    )
    assert not called
    assert np.isnan(array).all()
    assert info["skipped_outside_declared_coverage"] is True


def test_process_tile_masks_final_cells_with_land_mask(monkeypatch, tmp_path):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    payloads = _synthetic_source_payloads(tile)

    def fake_get_coverage(url, coverage_id, *args, **kwargs):
        return payloads[coverage_id]

    monkeypatch.setattr(tiling, "get_coverage", fake_get_coverage)
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path, bbox=(0, 0, 1000, 1000))
    config = {
        "crs": "EPSG:27700", "pilot_resolution_m": 10, "output_resolution_m": 100,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs": {name: name for name in ("road", "rail", "airport")},
        "coverage_ids": {name: name for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
        "england_mask_100m_path": str(mask_path),
    }
    output = tmp_path / "tile.tif"
    result = process_tile(tile, config, output, temp_root=tmp_path / "temp")
    assert result["land_mask_applied"] is True
    with rasterio.open(output) as dataset:
        assert np.all(dataset.read() != -9999.0)

    # A second mask with only the north-west quarter as land must blank the rest.
    with rasterio.open(mask_path, "r+") as dataset:
        values = np.zeros((100, 100), dtype="uint8")
        values[:50, :50] = 1
        dataset.write(values, 1)
    output2 = tmp_path / "tile_masked.tif"
    process_tile(tile, config, output2, temp_root=tmp_path / "temp2")
    with rasterio.open(output2) as dataset:
        values = dataset.read(1)
        assert np.all(values[:5, :5] != -9999.0)
        assert np.all(values[5:, :] == -9999.0)
        assert np.all(values[:, 5:] == -9999.0)


def test_runner_retries_then_skips_valid_complete_tile(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = {
        "crs": "EPSG:27700", "pilot_resolution_m": 10, "output_resolution_m": 100,
        "tile_size_m": 10000,
        "england_mask_100m_path": str(mask_path),
        "runner": {"max_workers": 1, "min_tile_start_interval_s": 0,
                    "max_attempts": 2, "retry_base_backoff_s": 0,
                    "retry_max_backoff_s": 0},
    }
    calls = []

    def fake_process(tile, config, path, temp_root=None):
        calls.append(tile.tile_id)
        if len(calls) == 1:
            raise RuntimeError("synthetic WCS failure")
        arrays = [np.full(tile.output_shape, 50.0, dtype="float32") for _ in range(4)]
        arrays[3][:] = 1.0
        tiling._write_float_bands(path, arrays, list(tiling.TILE_BANDS), tile, "EPSG:27700")
        return {"bands": list(tiling.TILE_BANDS), "source_info": {},
                "temporary_10m_discarded": True, "land_mask_applied": True}

    monkeypatch.setattr(runner, "process_tile", fake_process)
    manifest_path = tmp_path / "manifest.json"
    first = runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert first["processed"] == 1
    assert first["failed"] == 0
    assert len(calls) == 2
    manifest = json.loads(manifest_path.read_text())
    record = next(iter(manifest["tiles"].values()))
    assert record["status"] == "complete"
    assert record["attempts"] == 2
    second = runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert second["processed"] == 0
    assert second["skipped_complete"] == 1
    assert len(calls) == 2
