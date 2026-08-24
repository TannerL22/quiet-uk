import numpy as np

from quiet_uk.national_qa import check_band_arrays, check_tile_layout, inventory_issues
from quiet_uk.tiling import Tile


def _tile(tile_id, row, col, bbox):
    return Tile(tile_id, row, col, tuple(float(value) for value in bbox), 10, 100)


def test_inventory_detects_missing_unexpected_and_duplicate_files():
    issues = inventory_issues(
        {"r0000c0000", "r0000c0001"},
        ["r0000c0000.tif", "r0000c0000.tif", "extra.tif", ".r0000c0001.attempt-1.tif"],
    )
    assert issues["duplicate_ids"] == ["r0000c0000"]
    assert issues["missing_ids"] == ["r0000c0001"]
    assert issues["unexpected_files"] == ["extra.tif"]


def test_tile_layout_detects_a_shared_edge_gap():
    tiles = [
        _tile("r0000c0000", 0, 0, (0, 0, 1000, 1000)),
        _tile("r0000c0001", 0, 1, (1100, 0, 2100, 1000)),
    ]
    result = check_tile_layout(tiles)
    assert result["horizontal_shared_edges"] == 1
    assert result["gap_edges"] == 1
    assert result["overlap_edges"] == 0


def test_band_checks_reject_outside_values_and_fraction_mismatch():
    arrays = np.full((4, 2, 2), -9999.0, dtype="float64")
    land = np.array([[True, False], [True, True]])
    arrays[0, land] = 50.0
    arrays[1, land] = 43.1
    arrays[3, land] = 0.0
    arrays[2, 0, 0] = 49.0
    arrays[3, 0, 0] = 0.0
    result = check_band_arrays(arrays, land)
    assert result["outside_is_nodata"] is True
    assert result["fraction_in_range"] is True
    assert result["fraction_zero_with_airport_reported"] == 1
    assert result["impossible_acoustic_cells"] == 0

    arrays[0, 0, 1] = 55.0
    result = check_band_arrays(arrays, land)
    assert result["outside_is_nodata"] is False
