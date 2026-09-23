import csv
import json
from pathlib import Path

import pytest

from quiet_uk.source_grid_audit import audit_manifest
from quiet_uk.source_grid_audit import (
    _classify_rejection,
    _geometric_pattern,
    _rejection_category,
)
from quiet_uk.source_grid import validate_source_grid
from quiet_uk.tiling import Tile, tile_grid


def _config():
    return {
        "crs": "EPSG:27700",
        "coverage_ids": {"road": "road", "rail": "rail", "airport": "airport"},
        "wcs_versions": {"road": "1.0.0", "rail": "1.0.0", "airport": "2.0.1"},
        "coverage_bounds_epsg27700": {"airport": [-1000, -1000, 1000, 1000]},
    }


def _tile(tile_id, col):
    return {
        "tile_id": tile_id,
        "row": 0,
        "col": col,
        "bbox_epsg27700": [float(col * 200), 0.0, float((col + 1) * 200), 200.0],
        "source_resolution_m": 10,
        "output_resolution_m": 100,
    }


def _grid(tile, *, shifted=False):
    left, bottom, right, top = tile["bbox_epsg27700"]
    if shifted:
        left += 1.0
        right += 1.0
    return {
        "shape": [20, 20],
        "crs": "EPSG:27700",
        "transform": [10.0, 0.0, left, 0.0, -10.0, top, 0.0, 0.0, 1.0],
        "bounds": [left, bottom, right, top],
    }


def _manifest():
    first = _tile("r0000c0000", 0)
    second = _tile("r0000c0001", 1)
    return {
        "crs": "EPSG:27700",
        "tiles": {
            first["tile_id"]: {
                "tile": first,
                "source_info": {
                    "road": {"raw_grid": _grid(first)},
                    "rail": {"raw_grid": _grid(first)},
                    "airport": {"raw_grid": _grid(first)},
                },
            },
            second["tile_id"]: {
                "tile": second,
                "source_info": {
                    "road": {"raw_grid": _grid(second, shifted=True)},
                    "rail": {"raw_grid": _grid(second)},
                    "airport": {"skipped_outside_declared_coverage": True},
                },
            },
        },
    }


def test_audit_reconstructs_grids_in_deterministic_source_order(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "audit"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    config_path.write_text(json.dumps(_config()), encoding="utf-8")

    result = audit_manifest(manifest_path, config_path, output_dir)
    assert result["summary"]["manifest_tile_count"] == 2
    assert result["summary"]["row_count"] == 6
    assert result["summary"]["source_counts"] == {
        "road": {"accepted": 1, "rejected": 1},
        "rail": {"accepted": 2},
        "airport": {"accepted": 1, "skipped": 1},
    }
    assert result["summary"]["geometric_pattern_counts"]["airport"] == {
        "exact-requested-grid": 1,
        "skipped-no-raw-grid": 1,
    }

    report = json.loads((output_dir / "source_grid_reconciliation.json").read_text(encoding="utf-8"))
    assert report["audit_schema_version"] == 2
    assert [(row["tile_id"], row["source"]) for row in report["rows"]] == [
        ("r0000c0000", "road"),
        ("r0000c0000", "rail"),
        ("r0000c0000", "airport"),
        ("r0000c0001", "road"),
        ("r0000c0001", "rail"),
        ("r0000c0001", "airport"),
    ]
    rejected = report["rows"][3]
    assert rejected["policy_acceptance"] == "rejected"
    assert rejected["rejection_category"] == "transform"
    assert rejected["geometric_pattern"] == "response-extent-change-edges-west-resolution-preserved"
    assert "supplied config" in rejected["metadata_provenance"]

    with (output_dir / "source_grid_reconciliation.csv").open(newline="", encoding="utf-8") as handle:
        assert "geometric_pattern" in next(csv.reader(handle))


def test_audit_refuses_nonempty_output_without_explicit_overwrite(tmp_path):
    manifest_path = tmp_path / "manifest.json"
    config_path = tmp_path / "config.json"
    output_dir = tmp_path / "audit"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    config_path.write_text(json.dumps(_config()), encoding="utf-8")
    audit_manifest(manifest_path, config_path, output_dir)

    with pytest.raises(FileExistsError, match="not empty"):
        audit_manifest(manifest_path, config_path, output_dir)
    overwritten = audit_manifest(manifest_path, config_path, output_dir, overwrite=True)
    assert overwritten["summary"]["row_count"] == 6


def test_metadata_derived_fixture_patterns_remain_rejected_with_narrow_disposition():
    fixture_path = Path(__file__).parent / "fixtures" / "source_grid_reconciliation_patterns.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    airport_bounds = [333485, 93815, 594465, 574285]
    expected_patterns = {
        "west-edge clipping with fixed dimensions and rescaled x resolution":
            "response-extent-change-edges-west-rescaled-x",
        "west-and-south edge clipping with fixed dimensions and rescaled x/y resolution":
            "response-extent-change-edges-west-south-rescaled-x-y",
        "west-and-north domain clipping with fixed 1001x1001 dimensions and rescaled x/y resolution":
            "domain-clipped-edges-west-north-rescaled-x-y",
        "east domain clipping with fixed 1001x1001 dimensions and rescaled x resolution":
            "domain-clipped-edges-east-rescaled-x",
        "south domain clipping with fixed 1001x1001 dimensions and rescaled y resolution":
            "domain-clipped-edges-south-rescaled-y",
    }

    assert fixture["fixture_type"].startswith("metadata-derived")
    for entry in fixture["patterns"]:
        bbox = tuple(float(value) for value in entry["target_bbox_epsg27700"])
        tile = Tile(
            tile_id=entry["tile_id"], row=0, col=0, bbox=bbox,
            source_resolution_m=10, output_resolution_m=100,
        )
        target = tile_grid(tile, "EPSG:27700")
        declared_bounds = airport_bounds if entry["source"] == "airport" else None
        version = "2.0.1" if entry["source"] == "airport" else "1.0.0"
        raw_grid = entry["raw_grid"]
        assert _geometric_pattern(
            entry["source"], raw_grid, target, tile, version, declared_bounds
        ) == expected_patterns[entry["pattern"]]
        with pytest.raises(ValueError) as error:
            validate_source_grid(
                entry["source"], entry["tile_id"], target, raw_grid, version,
                declared_bounds=declared_bounds,
            )
        category = _rejection_category(
            entry["source"], raw_grid, target, str(error.value)
        )
        _, disposition, _ = _classify_rejection(
            entry["source"], raw_grid, target, tile, version, declared_bounds, category
        )
        assert disposition.startswith("C.")
