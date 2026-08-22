import json

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import Affine

from quiet_uk import tiling
from quiet_uk.tiling import make_tiles, mosaic_tiles, process_tile


def _geo_tiff_bytes(data, transform, nodata=None):
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
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
    }
    result = process_tile(tile, config, tmp_path / "tile.tif", temp_root=tmp_path / "temp")
    assert result["temporary_10m_discarded"] is True
    with rasterio.open(tmp_path / "tile.tif") as ds:
        assert list(ds.descriptions) == list(tiling.TILE_BANDS)
        assert "combined_upper_db" not in ds.descriptions
        assert ds.read(4).min() == pytest.approx(1.0)
    assert not any((tmp_path / "temp").iterdir())
