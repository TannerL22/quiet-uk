import copy

import numpy as np
import pytest

from quiet_uk.source_grid import (
    AIRPORT_PADDED_GRID_POLICY,
    EXACT_GRID_POLICY,
    GRID_POLICY_VERSION,
    target_pixel_centres_mask,
    validate_source_grid,
)
from quiet_uk.tiling import make_tiles, tile_grid


def _tile_and_target():
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    return tile, tile_grid(tile, "EPSG:27700")


def _raw_from_target(target):
    return copy.deepcopy(target)


def _padded_from_target(target):
    height, width = target["shape"]
    minx, miny, maxx, maxy = target["bounds"]
    return {
        "shape": (height + 1, width + 1),
        "crs": "EPSG:27700",
        "transform": (10.0, 0.0, minx - 5.0, 0.0, -10.0, maxy + 5.0, 0.0, 0.0, 1.0),
        "bounds": (minx - 5.0, miny - 5.0, maxx + 5.0, maxy + 5.0),
    }


def test_exact_road_and_rail_grids_are_accepted():
    tile, target = _tile_and_target()
    for source in ("road", "rail"):
        decision = validate_source_grid(source, tile.tile_id, target, _raw_from_target(target), "1.0.0")
        assert decision["policy_name"] == EXACT_GRID_POLICY["name"]
        assert decision["policy_version"] == GRID_POLICY_VERSION
        assert decision["alignment_required"] is False


@pytest.mark.parametrize(
    ("property_name", "mutate"),
    [
        ("CRS", lambda grid: grid.update(crs="EPSG:4326")),
        ("resolution", lambda grid: grid.update(transform=(11.0, 0.0, 0.0, 0.0, -11.0, 1000.0, 0.0, 0.0, 1.0))),
        ("dimensions", lambda grid: grid.update(shape=(99, 100))),
        ("transform", lambda grid: grid.update(transform=(10.0, 0.0, 5.0, 0.0, -10.0, 1000.0, 0.0, 0.0, 1.0))),
        ("transform", lambda grid: grid.update(transform=(10.0, 0.0, 10.0, 0.0, -10.0, 1000.0, 0.0, 0.0, 1.0))),
        ("orientation/rotation", lambda grid: grid.update(transform=(10.0, 0.25, 0.0, 0.0, -10.0, 1000.0, 0.0, 0.0, 1.0))),
        ("orientation/rotation", lambda grid: grid.update(transform=(-10.0, 0.0, 1000.0, 0.0, 10.0, 0.0, 0.0, 0.0, 1.0))),
        ("dimensions", lambda grid: grid.update(shape=(100, 99))),
    ],
)
def test_road_grid_geometry_errors_are_descriptive(property_name, mutate):
    tile, target = _tile_and_target()
    raw = _raw_from_target(target)
    mutate(raw)
    with pytest.raises(ValueError, match=rf"road tile {tile.tile_id}.*{property_name}"):
        validate_source_grid("road", tile.tile_id, target, raw, "1.0.0")


def test_serialization_noise_within_tolerance_is_accepted():
    tile, target = _tile_and_target()
    raw = _raw_from_target(target)
    raw["transform"] = (*raw["transform"][:2], 0.0000001, *raw["transform"][3:])
    raw["bounds"] = (0.0000001, 0.0, 1000.0000001, 1000.0)
    decision = validate_source_grid("rail", tile.tile_id, target, raw, "1.0.0")
    assert decision["alignment_required"] is False


def test_all_nodata_response_is_a_valid_exact_grid():
    tile, target = _tile_and_target()
    raw = _raw_from_target(target)
    decision = validate_source_grid("road", tile.tile_id, target, raw, "1.0.0")
    assert decision["policy_name"] == EXACT_GRID_POLICY["name"]


def test_airport_accepts_exact_and_documented_padded_wcs_grid():
    tile, target = _tile_and_target()
    exact = validate_source_grid("airport", tile.tile_id, target, target, "1.0.0")
    assert exact["alignment_required"] is False
    padded = _padded_from_target(target)
    aligned = validate_source_grid("airport", tile.tile_id, target, padded, "2.0.1")
    assert aligned["policy_name"] == AIRPORT_PADDED_GRID_POLICY["name"]
    assert aligned["alignment_required"] is True


@pytest.mark.parametrize(
    "mutate",
    [
        lambda grid: grid.update(shape=(101, 102)),
        lambda grid: grid.update(transform=(10.0, 0.0, -10.0, 0.0, -10.0, 1005.0, 0.0, 0.0, 1.0)),
        lambda grid: grid.update(transform=(11.0, 0.0, -5.0, 0.0, -10.0, 1005.0, 0.0, 0.0, 1.0)),
    ],
)
def test_airport_rejects_incorrect_padded_geometry(mutate):
    tile, target = _tile_and_target()
    raw = _padded_from_target(target)
    mutate(raw)
    with pytest.raises(ValueError, match=rf"airport tile {tile.tile_id}"):
        validate_source_grid("airport", tile.tile_id, target, raw, "2.0.1")


def test_airport_padding_is_not_allowed_for_other_wcs_versions():
    tile, target = _tile_and_target()
    with pytest.raises(ValueError, match="airport tile"):
        validate_source_grid("airport", tile.tile_id, target, _padded_from_target(target), "1.0.0")


def test_unsupported_target_crs_with_declared_epsg27700_bounds_is_rejected():
    tile, target = _tile_and_target()
    target["crs"] = "EPSG:4326"
    with pytest.raises(ValueError, match="CRS/domain combination"):
        validate_source_grid("airport", tile.tile_id, target, target, "1.0.0", declared_bounds=(0, 0, 10, 10))


def test_target_pixel_centres_include_declared_boundaries():
    target = {
        "shape": (4, 4),
        "crs": "EPSG:27700",
        "transform": (10.0, 0.0, 0.0, 0.0, -10.0, 40.0, 0.0, 0.0, 1.0),
        "bounds": (0.0, 0.0, 40.0, 40.0),
    }
    mask = target_pixel_centres_mask(target, (15.0, 0.0, 25.0, 40.0))
    assert mask.sum() == 8
    assert mask[:, 1].all() and mask[:, 2].all()
    assert not mask[:, 0].any() and not mask[:, 3].any()
