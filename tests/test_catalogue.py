import hashlib
import json
import os
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine
from rasterio.warp import transform as transform_coords

import quiet_uk.catalogue as catalogue_module
from quiet_uk.catalogue import (
    CatalogueError,
    CatalogueIntegrityError,
    CataloguePublicationError,
    DatasetCatalogue,
    build_catalogue,
    lookup_location,
)
from quiet_uk.locking import resource_lock


SOURCES = ("road", "rail", "airport")
BASE_EASTING = 503000.0
BASE_NORTHING = 171000.0


def _tile(tile_id, col):
    return {
        "tile_id": tile_id,
        "row": 0,
        "col": col,
        "bbox_epsg27700": [
            BASE_EASTING + float(col * 200), BASE_NORTHING,
            BASE_EASTING + float((col + 1) * 200), BASE_NORTHING + 200.0,
        ],
        "source_resolution_m": 10,
        "output_resolution_m": 100,
        "source_shape": [20, 20],
        "output_shape": [2, 2],
    }


def _grid(tile, *, shift=0.0):
    left, bottom, right, top = tile["bbox_epsg27700"]
    left += shift
    right += shift
    return {
        "shape": [20, 20],
        "crs": "EPSG:27700",
        "transform": [10.0, 0.0, left, 0.0, -10.0, top, 0.0, 0.0, 1.0],
        "bounds": [left, bottom, right, top],
    }


def _request_bounds(tile, source):
    minx, miny, maxx, maxy = tile["bbox_epsg27700"]
    padding = 5.0 if source == "airport" else 0.0
    return [minx - padding, miny - padding, maxx + padding, maxy + padding]


def _report_row(tile, source, *, acceptance="accepted", shift=0.0, skip=False):
    raw = None if skip else _grid(tile, shift=shift)
    return {
        "tile_id": tile["tile_id"],
        "source": source,
        "coverage_id": f"{source}-coverage",
        "wcs_version": "2.0.1" if source == "airport" else "1.0.0",
        "metadata_provenance": "synthetic fixture metadata",
        "requested_bounds_epsg27700": _request_bounds(tile, source),
        "requested_shape": [20, 20],
        "recorded_response_bounds_epsg27700": None if raw is None else raw["bounds"],
        "recorded_response_shape": None if raw is None else raw["shape"],
        "recorded_response_transform": None if raw is None else raw["transform"],
        "policy_version": "1",
        "policy_acceptance": "skipped" if skip else acceptance,
        "policy_name": "exact-target-grid",
        "rejection_category": None if acceptance == "accepted" else "transform",
        "coverage_relationship": "outside declared coverage" if skip else "matches request",
        "geometric_pattern": "skipped-no-raw-grid" if skip else "exact-requested-grid",
        "resolution_changed": False,
        "target_support_cells": None if raw is None else 400,
        "target_cells_without_recorded_support": None if raw is None else 0,
        "historical_alignment_performed": False,
        "historical_alignment_method": None,
        "historical_reprojection_evidence": "synthetic metadata",
        "evidence_based_classification": "Deliberately skipped source request" if skip else (
            "Exact requested grid" if acceptance == "accepted" else "Unexpected shift"
        ),
        "proposed_disposition": "Retain explicit airport outside-domain skip" if skip else (
            "A. supported" if acceptance == "accepted" else "D. remains rejected"
        ),
    }, raw


def _write_tile(path, tile, base_value):
    values = np.stack([
        np.full((2, 2), base_value, dtype="float32"),
        np.full((2, 2), base_value + 10, dtype="float32"),
        np.full((2, 2), base_value - 5, dtype="float32"),
        np.full((2, 2), 0.5, dtype="float32"),
    ])
    values[2, 1, 1] = -9999.0
    values[3, 1, 1] = 0.0
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=2,
        width=2,
        count=4,
        dtype="float32",
        crs="EPSG:27700",
        transform=Affine(100, 0, tile["bbox_epsg27700"][0], 0, -100, tile["bbox_epsg27700"][3]),
        nodata=-9999.0,
    ) as dataset:
        for index, name in enumerate(
            ("combined_reported_lower_db", "road_rail_upper_db", "airport_reported_lower_db", "airport_reported_fraction"),
            start=1,
        ):
            dataset.write(values[index - 1], index)
            dataset.set_band_description(index, name)


def _fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    mask_path = tmp_path / "mask.tif"
    tiles = [_tile(f"r0000c{col:04d}", col) for col in range(4)]
    for col, tile in enumerate(tiles):
        _write_tile(tile_dir / f"{tile['tile_id']}.tif", tile, 50.0 + col)
    mask = np.ones((2, 8), dtype="uint8")
    mask[0, 6] = 0
    with rasterio.open(
        mask_path,
        "w",
        driver="GTiff",
        height=2,
        width=8,
        count=1,
        dtype="uint8",
        crs="EPSG:27700",
        transform=Affine(100, 0, BASE_EASTING, 0, -100, BASE_NORTHING + 200.0),
        nodata=0,
    ) as dataset:
        dataset.write(mask, 1)
    _set_nodata_cell(tile_dir / "r0000c0003.tif", 0, 0)

    report_rows = []
    source_info = {}
    for index, tile in enumerate(tiles):
        source_info[tile["tile_id"]] = {"tile": tile, "source_info": {}}
        for source in SOURCES:
            if index == 1 and source == "airport":
                row, raw = _report_row(tile, source, acceptance="rejected", shift=10.0)
            elif index == 2 and source == "road":
                row, raw = _report_row(tile, source, acceptance="rejected", shift=10.0)
            elif index == 3 and source == "airport":
                row, raw = _report_row(tile, source, skip=True)
            else:
                row, raw = _report_row(tile, source)
            report_rows.append(row)
            source_info[tile["tile_id"]]["source_info"][source] = (
                {"skipped_outside_declared_coverage": True} if raw is None else {"raw_grid": raw}
            )
    manifest = {"crs": "EPSG:27700", "tiles": source_info}
    report = {"audit_schema_version": 2, "rows": report_rows}
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return tile_dir, manifest_path, report_path, mask_path, tiles


def _build(tmp_path):
    paths = _fixture(tmp_path)
    output = tmp_path / "catalogue"
    result = build_catalogue(*paths[:4], output)
    return paths, result


def _boundary_tile(tile_id, row, col):
    return {
        "tile_id": tile_id,
        "row": row,
        "col": col,
        "bbox_epsg27700": [
            BASE_EASTING + float(col * 200),
            BASE_NORTHING + float((1 - row) * 200),
            BASE_EASTING + float((col + 1) * 200),
            BASE_NORTHING + float((2 - row) * 200),
        ],
        "source_resolution_m": 10,
        "output_resolution_m": 100,
        "source_shape": [20, 20],
        "output_shape": [2, 2],
    }


def _boundary_fixture(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir(parents=True, exist_ok=True)
    mask_path = tmp_path / "mask.tif"
    tiles = []
    for row in range(2):
        for col in range(2):
            tile = _boundary_tile(f"r{row:04d}c{col:04d}", row, col)
            tiles.append(tile)
            _write_tile(tile_dir / f"{tile['tile_id']}.tif", tile, 50.0 + row * 20.0 + col * 10.0)

    mask = np.array(
        [
            [1, 1, 0, 1],
            [1, 1, 1, 1],
            [1, 1, 1, 1],
            [1, 0, 1, 1],
        ],
        dtype="uint8",
    )
    with rasterio.open(
        mask_path,
        "w",
        driver="GTiff",
        height=4,
        width=4,
        count=1,
        dtype="uint8",
        crs="EPSG:27700",
        transform=Affine(100, 0, BASE_EASTING, 0, -100, BASE_NORTHING + 400.0),
        nodata=0,
    ) as dataset:
        dataset.write(mask, 1)
    _set_nodata_cell(tile_dir / "r0000c0001.tif", 0, 0)
    _set_nodata_cell(tile_dir / "r0001c0000.tif", 1, 1)

    report_rows = []
    source_info = {}
    for tile in tiles:
        source_info[tile["tile_id"]] = {"tile": tile, "source_info": {}}
        for source in SOURCES:
            row, raw = _report_row(tile, source)
            report_rows.append(row)
            source_info[tile["tile_id"]]["source_info"][source] = {"raw_grid": raw}
    manifest = {"crs": "EPSG:27700", "tiles": source_info}
    report = {"audit_schema_version": 2, "rows": report_rows}
    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "report.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    report_path.write_text(json.dumps(report), encoding="utf-8")
    return tile_dir, manifest_path, report_path, mask_path, tiles


def _rewrite_band_cell(tile_path, band_index, row, column, value):
    with rasterio.open(tile_path, "r+") as dataset:
        values = dataset.read(band_index)
        values[row, column] = value
        dataset.write(values, band_index)


def _set_nodata_cell(tile_path, row, column):
    for band_index in range(1, 5):
        _rewrite_band_cell(tile_path, band_index, row, column, -9999.0)


def _rewrite_report_row(report_path, tile_id, source, **changes):
    report = json.loads(report_path.read_text(encoding="utf-8"))
    row = next(item for item in report["rows"] if item["tile_id"] == tile_id and item["source"] == source)
    row.update(changes)
    report_path.write_text(json.dumps(report), encoding="utf-8")


def _published_pair_bytes(result):
    return {
        "database": Path(result["database"]).read_bytes(),
        "metadata": Path(result["metadata"]).read_bytes(),
    }


def _update_catalogue_tile_checksum(result, tile_id, tile_path):
    with sqlite3.connect(result["database"]) as connection:
        connection.execute(
            "UPDATE tiles SET file_size = ?, sha256 = ? WHERE tile_id = ?",
            (tile_path.stat().st_size, catalogue_module._sha256_file(tile_path), tile_id),
        )


def test_catalogue_build_records_integrity_and_quality_counts(tmp_path):
    paths, result = _build(tmp_path)
    metadata = result["metadata_payload"]
    assert metadata["catalogue_schema_version"] == 2
    assert metadata["catalogue_validation_contract_version"] == 2
    assert metadata["tile_count"] == 4
    assert metadata["quality_counts"]["source_states"]["airport"] == {
        "grid_accepted": 2,
        "grid_rejected": 1,
        "outside_declared_coverage": 1,
    }
    assert metadata["quality_counts"]["band_qualifications"]["road_rail_upper_db"] == {
        "qualified": 3,
        "withheld_due_to_source_quality": 1,
    }
    assert metadata["inputs"]["manifest"]["sha256"] == hashlib.sha256(paths[1].read_bytes()).hexdigest()
    assert all(item["status"] == "not_established" for item in metadata["reference_years"].values())
    assert Path(result["database"]).is_file()
    assert Path(result["metadata"]).is_file()


def test_catalogue_records_only_explicit_reference_year_provenance(tmp_path):
    paths = _fixture(tmp_path)
    reference = tmp_path / "reference.json"
    reference.write_text(
        json.dumps({"reference_years": {"road": {"year": 2021, "provenance": "supplied test metadata"}}}),
        encoding="utf-8",
    )
    result = build_catalogue(*paths[:4], tmp_path / "catalogue", reference_metadata_path=reference)
    assert result["metadata_payload"]["reference_years"]["road"] == {
        "value": 2021,
        "status": "established",
        "provenance": "supplied test metadata",
    }
    assert result["metadata_payload"]["reference_years"]["rail"]["status"] == "not_established"


def test_catalogue_rejects_missing_extra_and_conflicting_inputs(tmp_path):
    paths = _fixture(tmp_path)
    tile_dir, manifest_path, report_path, mask_path, tiles = paths
    (tile_dir / f"{tiles[-1]['tile_id']}.tif").unlink()
    with pytest.raises(CatalogueError, match="missing"):
        build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "missing")

    _write_tile(tile_dir / f"{tiles[-1]['tile_id']}.tif", tiles[-1], 60.0)
    extra = tile_dir / "r9999c9999.tif"
    _write_tile(extra, tiles[-1], 60.0)
    with pytest.raises(CatalogueError, match="unexpected"):
        build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "extra")
    extra.unlink()

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tiles"][tiles[0]["tile_id"]]["tile"]["output_shape"] = [3, 2]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogueError, match="output dimensions"):
        build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "manifest-mismatch")

    paths = _fixture(tmp_path / "duplicate-manifest")
    manifest = json.loads(paths[1].read_text(encoding="utf-8"))
    manifest["tiles"] = [manifest["tiles"][tiles[0]["tile_id"]], manifest["tiles"][tiles[0]["tile_id"]]]
    paths[1].write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(CatalogueError, match="duplicate tile ID"):
        build_catalogue(*paths[:4], tmp_path / "duplicate-manifest-output")


def test_catalogue_rejects_duplicate_or_incomplete_report_and_header_mismatch(tmp_path):
    paths = _fixture(tmp_path)
    tile_dir, manifest_path, report_path, mask_path, tiles = paths
    report = json.loads(report_path.read_text(encoding="utf-8"))
    report["rows"] = report["rows"][:-1]
    report_path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(CatalogueError, match="missing tile/source"):
        build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "missing-report")

    paths = _fixture(tmp_path / "duplicate")
    report = json.loads(paths[2].read_text(encoding="utf-8"))
    report["rows"].append(report["rows"][0])
    paths[2].write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(CatalogueError, match="duplicate"):
        build_catalogue(*paths[:4], tmp_path / "duplicate-report")

    paths = _fixture(tmp_path / "header")
    _write_tile(paths[0] / f"{paths[4][0]['tile_id']}.tif", {**paths[4][0], "bbox_epsg27700": [1, 0, 201, 200]}, 50.0)
    with pytest.raises(CatalogueError, match="transform mismatch"):
        build_catalogue(*paths[:4], tmp_path / "header-mismatch")

    paths = _fixture(tmp_path / "geometry")
    report = json.loads(paths[2].read_text(encoding="utf-8"))
    report["rows"][0]["requested_bounds_epsg27700"][0] += 1
    paths[2].write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(CatalogueError, match="requested bounds"):
        build_catalogue(*paths[:4], tmp_path / "geometry-mismatch")


def test_missing_source_evidence_remains_unknown_and_withheld(tmp_path):
    paths = _fixture(tmp_path)
    manifest = json.loads(paths[1].read_text(encoding="utf-8"))
    tile_id = "r0000c0002"
    manifest["tiles"][tile_id]["source_info"]["road"] = {}
    paths[1].write_text(json.dumps(manifest), encoding="utf-8")
    report = json.loads(paths[2].read_text(encoding="utf-8"))
    row = next(item for item in report["rows"] if item["tile_id"] == tile_id and item["source"] == "road")
    row.update(
        policy_acceptance="missing_evidence",
        policy_name=None,
        rejection_category=None,
        recorded_response_bounds_epsg27700=None,
        recorded_response_shape=None,
        recorded_response_transform=None,
        geometric_pattern="missing-raw-grid-metadata",
        evidence_based_classification="Missing raw-grid metadata",
        proposed_disposition="D. insufficient evidence; remains unsupported",
    )
    paths[2].write_text(json.dumps(report), encoding="utf-8")
    output = tmp_path / "missing-evidence-catalogue"
    result = build_catalogue(*paths[:4], output)
    result = lookup_location(result["catalogue_dir"], paths[0], 503450, 171150, land_mask_path=paths[3])
    assert result["bands"]["road_rail_upper_db"]["qualification"] == "withheld_due_to_source_quality"
    assert result["bands"]["combined_reported_lower_db"]["qualification"] == "withheld_due_to_source_quality"
    assert result["bands"]["airport_reported_lower_db"]["value"] == pytest.approx(47.0)


def test_catalogue_refuses_overwrite_and_does_not_change_source_files(tmp_path):
    paths, result = _build(tmp_path)
    before = {path: path.read_bytes() for path in paths[0].iterdir()}
    with pytest.raises(FileExistsError, match="not empty"):
        build_catalogue(*paths[:4], result["catalogue_dir"])
    assert {path: path.read_bytes() for path in paths[0].iterdir()} == before


def test_lookup_quality_withholding_and_diagnostic_mode(tmp_path):
    paths, result = _build(tmp_path)
    tile_dir, _, _, mask_path, _ = paths
    catalogue_dir = result["catalogue_dir"]

    accepted = lookup_location(catalogue_dir, tile_dir, 503050, 171150, land_mask_path=mask_path)
    assert accepted["tile_id"] == "r0000c0000"
    assert accepted["cell"]["row"] == 0 and accepted["cell"]["column"] == 0
    assert accepted["bands"]["combined_reported_lower_db"]["value"] == pytest.approx(50.0)
    assert accepted["bands"]["combined_reported_lower_db"]["value_status"] == "reported_stored_value"

    airport_rejected = lookup_location(catalogue_dir, tile_dir, 503250, 171150, land_mask_path=mask_path)
    assert airport_rejected["tile_id"] == "r0000c0001"
    assert airport_rejected["bands"]["airport_reported_lower_db"]["value"] is None
    assert airport_rejected["bands"]["airport_reported_lower_db"]["qualification"] == "withheld_due_to_source_quality"
    assert airport_rejected["bands"]["road_rail_upper_db"]["value"] == pytest.approx(61.0)
    assert airport_rejected["bands"]["road_rail_upper_db"]["qualification"] == "qualified"
    diagnostic = lookup_location(catalogue_dir, tile_dir, 503250, 171150, land_mask_path=mask_path, diagnostic=True)
    assert diagnostic["bands"]["airport_reported_lower_db"]["value"] == pytest.approx(46.0)
    assert "diagnostic" in diagnostic["bands"]["airport_reported_lower_db"]["value_status"]
    assert "not qualified" in diagnostic["bands"]["airport_reported_lower_db"]["warning"]

    road_rejected = lookup_location(catalogue_dir, tile_dir, 503450, 171150, land_mask_path=mask_path)
    assert road_rejected["bands"]["road_rail_upper_db"]["value"] is None
    assert road_rejected["bands"]["combined_reported_lower_db"]["value"] is None
    assert road_rejected["bands"]["airport_reported_lower_db"]["value"] == pytest.approx(47.0)

    airport_skipped = lookup_location(catalogue_dir, tile_dir, 503750, 171050, land_mask_path=mask_path)
    assert airport_skipped["bands"]["combined_reported_lower_db"]["qualification"] == "qualified_with_coverage_limitation"
    assert airport_skipped["bands"]["airport_reported_fraction"]["value"] == pytest.approx(0.0)
    assert airport_skipped["bands"]["airport_reported_fraction"]["value_status"] == "zero_reported_airport_pixels_not_silence"
    assert airport_skipped["bands"]["airport_reported_lower_db"]["value"] is None

    outside_land = lookup_location(catalogue_dir, tile_dir, 503650, 171150, land_mask_path=mask_path)
    assert outside_land["land_status"] == "outside_england_land"
    assert all(item["value"] is None for item in outside_land["bands"].values())


def test_lookup_coordinates_edges_wgs84_and_no_coverage(tmp_path):
    paths, result = _build(tmp_path)
    tile_dir, _, _, mask_path, _ = paths
    catalogue_dir = result["catalogue_dir"]
    bng = lookup_location(catalogue_dir, tile_dir, 503150, 171150, land_mask_path=mask_path)
    lon, lat = transform_coords("EPSG:27700", "EPSG:4326", [503150], [171150])
    wgs = lookup_location(catalogue_dir, tile_dir, lon[0], lat[0], "EPSG:4326", land_mask_path=mask_path)
    assert wgs["tile_id"] == bng["tile_id"]
    assert wgs["cell"] == bng["cell"]
    assert lookup_location(catalogue_dir, tile_dir, 503200, 171150, land_mask_path=mask_path)["tile_id"] == "r0000c0001"
    assert lookup_location(catalogue_dir, tile_dir, 503100, 171150, land_mask_path=mask_path)["cell"]["column"] == 1
    assert lookup_location(catalogue_dir, tile_dir, 503100, 171100, land_mask_path=mask_path)["cell"]["row"] == 1
    no_coverage = lookup_location(catalogue_dir, tile_dir, 503800, 171150, land_mask_path=mask_path)
    assert no_coverage["land_status"] == "no_dataset_coverage"
    assert all(item["value"] is None for item in no_coverage["bands"].values())

    with pytest.raises(CatalogueError, match="finite"):
        lookup_location(catalogue_dir, tile_dir, float("nan"), 171150, land_mask_path=mask_path)
    with pytest.raises(CatalogueError, match="longitude"):
        lookup_location(catalogue_dir, tile_dir, 181, 50, "EPSG:4326", land_mask_path=mask_path)
    with pytest.raises(CatalogueError, match="requires easting"):
        lookup_location(catalogue_dir, tile_dir, coordinate_crs="EPSG:27700", longitude=1, latitude=2, land_mask_path=mask_path)


def test_lookup_verifies_once_per_session_refuses_tampering_and_returns_json_safe_values(tmp_path, monkeypatch):
    paths, result = _build(tmp_path)
    tile_dir, _, _, mask_path, _ = paths
    original = catalogue_module._sha256_file
    calls = []

    def counted(path):
        calls.append(str(path))
        return original(path)

    monkeypatch.setattr(catalogue_module, "_sha256_file", counted)
    with DatasetCatalogue(result["catalogue_dir"], tile_dir, mask_path) as catalogue:
        first = catalogue.lookup(503050, 171150)
        second = catalogue.lookup(503050, 171150)
        assert first["bands"] == second["bands"]
    assert calls.count(str(tile_dir / "r0000c0000.tif")) == 1
    assert calls.count(str(mask_path)) == 1
    assert json.dumps(first, allow_nan=False)
    assert "-9999" not in json.dumps(first)

    tile_path = tile_dir / "r0000c0000.tif"
    data = bytearray(tile_path.read_bytes())
    data[-1] ^= 1
    tile_path.write_bytes(data)
    with pytest.raises(CatalogueIntegrityError, match="checksum mismatch"):
        lookup_location(result["catalogue_dir"], tile_dir, 503050, 171150, land_mask_path=mask_path)


def test_boundary_ownership_matches_north_up_raster_indexing(tmp_path):
    paths = _boundary_fixture(tmp_path)
    result = build_catalogue(*paths[:4], tmp_path / "catalogue")
    tile_dir, _, _, mask_path, _ = paths
    catalogue_dir = result["catalogue_dir"]

    def value_at(easting, northing):
        lookup = lookup_location(catalogue_dir, tile_dir, easting, northing, land_mask_path=mask_path)
        return lookup["tile_id"], lookup["cell"], lookup["land_status"], lookup["bands"]["combined_reported_lower_db"]["value"]

    assert value_at(503200.0, 171300.0)[0] == "r0000c0001"
    assert value_at(503100.0, 171200.0)[0] == "r0001c0000"
    assert value_at(503200.0, 171200.0)[0] == "r0001c0001"
    assert value_at(503199.999, 171200.0)[0] == "r0001c0000"
    assert value_at(503200.001, 171200.0)[0] == "r0001c0001"
    assert value_at(503100.0, 171199.999)[0] == "r0001c0000"
    assert value_at(503100.0, 171200.001)[0] == "r0000c0000"

    assert value_at(503000.0, 171350.0)[0] == "r0000c0000"
    assert value_at(503050.0, 171400.0)[0] == "r0000c0000"
    assert value_at(503400.0, 171350.0)[2] == "no_dataset_coverage"
    assert value_at(503050.0, 171000.0)[2] == "no_dataset_coverage"
    assert value_at(503400.0, 171400.0)[2] == "no_dataset_coverage"
    assert value_at(503000.0, 171000.0)[2] == "no_dataset_coverage"

    outside_land = value_at(503250.0, 171350.0)
    assert outside_land[0] == "r0000c0001"
    assert outside_land[2] == "outside_england_land"

    lon, lat = transform_coords("EPSG:27700", "EPSG:4326", [503050.0], [171350.0])
    wgs = value_at(503050.0, 171350.0)
    wgs_lookup = lookup_location(
        catalogue_dir,
        tile_dir,
        lon[0],
        lat[0],
        "EPSG:4326",
        land_mask_path=mask_path,
    )
    assert wgs_lookup["tile_id"] == wgs[0]
    assert wgs_lookup["cell"] == wgs[1]


@pytest.mark.parametrize(
    ("band_index", "row", "column", "value"),
    [
        pytest.param(2, 0, 0, -9999.0, id="upper-band-finite-nodata"),
        pytest.param(2, 0, 0, np.nan, id="upper-band-nan"),
        pytest.param(2, 0, 0, np.inf, id="upper-band-positive-infinity"),
        pytest.param(4, 0, 0, -9999.0, id="fraction-nodata"),
        pytest.param(4, 0, 0, 1.5, id="fraction-out-of-range"),
    ],
)
def test_catalogue_rejects_invalid_required_band_values_before_publish(
    tmp_path, band_index, row, column, value
):
    paths = _fixture(tmp_path)
    _rewrite_band_cell(paths[0] / "r0000c0000.tif", band_index, row, column, value)
    with pytest.raises(CatalogueIntegrityError, match="semantic|integrity|invalid|required"):
        build_catalogue(*paths[:4], tmp_path / "catalogue")
    assert not (tmp_path / "catalogue" / "catalogue.json").exists()


@pytest.mark.parametrize(
    ("band_index", "row", "column", "value"),
    [
        pytest.param(4, 0, 0, 0.0, id="zero-fraction-with-reported-airport-lower"),
        pytest.param(4, 1, 1, 0.5, id="positive-fraction-without-airport-lower"),
    ],
)
def test_catalogue_rejects_existing_cross_band_invariant_violations(
    tmp_path, band_index, row, column, value
):
    paths = _fixture(tmp_path)
    _rewrite_band_cell(paths[0] / "r0000c0000.tif", band_index, row, column, value)
    with pytest.raises(CatalogueIntegrityError, match="semantic|integrity|airport"):
        build_catalogue(*paths[:4], tmp_path / "catalogue")


def test_valid_censored_lower_band_is_not_rejected(tmp_path):
    paths, result = _build(tmp_path)
    lookup = lookup_location(
        result["catalogue_dir"],
        paths[0],
        503150.0,
        171050.0,
        land_mask_path=paths[3],
    )
    airport_lower = lookup["bands"]["airport_reported_lower_db"]
    assert airport_lower["value"] is None
    assert airport_lower["value_status"] == "legitimate_censored_or_unreported_lower_bound"


@pytest.mark.parametrize(
    ("band_index", "value"),
    [
        pytest.param(2, -9999.0, id="upper-band-finite-nodata"),
        pytest.param(2, np.nan, id="upper-band-nonfinite"),
        pytest.param(4, 1.5, id="invalid-airport-fraction"),
    ],
)
def test_selected_cell_semantics_are_checked_after_checksum_verification(tmp_path, band_index, value):
    paths, result = _build(tmp_path)
    tile_path = paths[0] / "r0000c0000.tif"
    _rewrite_band_cell(tile_path, band_index, 0, 0, value)
    _update_catalogue_tile_checksum(result, "r0000c0000", tile_path)
    for diagnostic in (False, True):
        with pytest.raises(CatalogueIntegrityError, match="semantic integrity validation"):
            lookup_location(
                result["catalogue_dir"],
                paths[0],
                503050.0,
                171150.0,
                land_mask_path=paths[3],
                diagnostic=diagnostic,
            )


@pytest.mark.parametrize(
    ("tile_id", "source", "acceptance"),
    [
        pytest.param("r0000c0001", "airport", "accepted", id="rejected-airport-relabelled-accepted"),
        pytest.param("r0000c0000", "road", "rejected", id="accepted-road-relabelled-rejected"),
        pytest.param("r0000c0002", "road", "accepted", id="rejected-road-relabelled-accepted"),
    ],
)
def test_catalogue_recomputes_source_grid_acceptance_before_qualification(
    tmp_path, tile_id, source, acceptance
):
    paths = _fixture(tmp_path)
    _rewrite_report_row(paths[2], tile_id, source, policy_acceptance=acceptance)
    with pytest.raises(CatalogueIntegrityError, match=f"{tile_id}/{source}"):
        build_catalogue(*paths[:4], tmp_path / "catalogue")


def test_catalogue_rejects_unsupported_reconciliation_policy_metadata(tmp_path):
    paths = _fixture(tmp_path)
    _rewrite_report_row(paths[2], "r0000c0000", "road", policy_version="999")
    with pytest.raises(CatalogueError, match="policy version"):
        build_catalogue(*paths[:4], tmp_path / "catalogue")


def test_catalogue_rejects_old_json_validation_contract_with_rebuild_guidance(tmp_path):
    paths, result = _build(tmp_path)
    metadata_path = Path(result["metadata"])
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["catalogue_schema_version"] = 1
    metadata.pop("catalogue_validation_contract_version")
    metadata.pop("semantic_validation")
    metadata_path.write_text(json.dumps(metadata), encoding="utf-8")
    with pytest.raises(CatalogueError, match="rebuild"):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])


def test_catalogue_rejects_old_sqlite_validation_contract_with_rebuild_guidance(tmp_path):
    paths, result = _build(tmp_path)
    with sqlite3.connect(result["database"]) as connection:
        connection.execute("PRAGMA user_version = 1")
    with pytest.raises(CatalogueError, match="database schema version.*rebuild"):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])


def test_catalogue_rejects_old_publication_contract_with_rebuild_guidance(tmp_path):
    paths, result = _build(tmp_path)
    with sqlite3.connect(result["database"]) as connection:
        metadata = json.loads(
            connection.execute(
                "SELECT value_json FROM catalogue_info WHERE key = 'metadata'"
            ).fetchone()[0]
        )
        metadata.pop("catalogue_publication_contract_version")
        metadata.pop("build_id")
        connection.execute(
            "UPDATE catalogue_info SET value_json = ? WHERE key = 'metadata'",
            (json.dumps(metadata),),
        )
    with pytest.raises(CatalogueError, match="publication metadata|rebuild"):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])


def test_interrupted_overwrite_does_not_allow_mixed_metadata_generations(tmp_path, monkeypatch):
    paths, result = _build(tmp_path)
    output = Path(result["catalogue_dir"])
    before = _published_pair_bytes(result)
    original_replace = catalogue_module._replace_published_file

    def fail_json_publication(staged_path, published_path):
        if published_path == output / "catalogue.json":
            raise OSError("injected JSON publication failure")
        return original_replace(staged_path, published_path)

    monkeypatch.setattr(catalogue_module, "_replace_published_file", fail_json_publication)
    with pytest.raises(CataloguePublicationError, match="JSON publication failed"):
        build_catalogue(*paths[:4], output, dataset_label="new generation", overwrite=True)

    assert Path(result["metadata"]).read_bytes() == before["metadata"]
    assert Path(result["database"]).read_bytes() != before["database"]
    with pytest.raises(CatalogueIntegrityError, match="build identifiers differ"):
        DatasetCatalogue(output, paths[0], paths[3])

    monkeypatch.setattr(catalogue_module, "_replace_published_file", original_replace)
    recovered = build_catalogue(*paths[:4], output, dataset_label="recovered generation", overwrite=True)
    with DatasetCatalogue(output, paths[0], paths[3]) as catalogue:
        assert catalogue.metadata["build_id"] == recovered["metadata_payload"]["build_id"]
        assert catalogue.metadata["dataset_label"] == "recovered generation"


def test_construction_failure_preserves_previous_published_pair_byte_for_byte(tmp_path):
    paths, result = _build(tmp_path)
    before = _published_pair_bytes(result)
    _rewrite_band_cell(paths[0] / "r0000c0000.tif", 2, 0, 0, -9999.0)

    with pytest.raises(CatalogueIntegrityError, match="semantic integrity"):
        build_catalogue(*paths[:4], result["catalogue_dir"], overwrite=True)

    assert _published_pair_bytes(result) == before
    assert not any(Path(result["catalogue_dir"]).glob(".catalogue-build-*"))


def test_json_serialization_failure_preserves_previous_published_pair(tmp_path, monkeypatch):
    paths, result = _build(tmp_path)
    before = _published_pair_bytes(result)

    def fail_serialization(path, metadata):
        raise OSError("injected JSON serialization failure")

    monkeypatch.setattr(catalogue_module, "_serialize_metadata_export", fail_serialization)
    with pytest.raises(OSError, match="injected JSON serialization failure"):
        build_catalogue(*paths[:4], result["catalogue_dir"], dataset_label="new generation", overwrite=True)

    assert _published_pair_bytes(result) == before
    assert not any(Path(result["catalogue_dir"]).glob(".catalogue-build-*"))


def test_database_replacement_failure_preserves_previous_published_pair(tmp_path, monkeypatch):
    paths, result = _build(tmp_path)
    output = Path(result["catalogue_dir"])
    before = _published_pair_bytes(result)
    original_replace = catalogue_module._replace_published_file

    def fail_database_replacement(staged_path, published_path):
        if published_path == output / "catalogue.sqlite3":
            raise OSError("injected database replacement failure")
        return original_replace(staged_path, published_path)

    monkeypatch.setattr(catalogue_module, "_replace_published_file", fail_database_replacement)
    with pytest.raises(CataloguePublicationError, match="database publication failed"):
        build_catalogue(*paths[:4], output, dataset_label="new generation", overwrite=True)

    assert _published_pair_bytes(result) == before


def test_initial_construction_failure_publishes_no_catalogue_pair(tmp_path):
    paths = _fixture(tmp_path)
    _rewrite_band_cell(paths[0] / "r0000c0000.tif", 2, 0, 0, -9999.0)
    output = tmp_path / "catalogue"

    with pytest.raises(CatalogueIntegrityError, match="semantic integrity"):
        build_catalogue(*paths[:4], output)

    assert not (output / "catalogue.sqlite3").exists()
    assert not (output / "catalogue.json").exists()
    assert not any(output.glob(".catalogue-build-*"))


def test_successful_overwrite_publishes_matching_new_generation(tmp_path):
    paths, result = _build(tmp_path)
    old_id = result["metadata_payload"]["build_id"]
    rebuilt = build_catalogue(
        *paths[:4],
        result["catalogue_dir"],
        dataset_label="new generation",
        overwrite=True,
    )

    assert rebuilt["metadata_payload"]["build_id"] != old_id
    assert rebuilt["metadata_payload"]["dataset_label"] == "new generation"
    with DatasetCatalogue(rebuilt["catalogue_dir"], paths[0], paths[3]) as catalogue:
        assert catalogue.metadata["build_id"] == rebuilt["metadata_payload"]["build_id"]
        assert catalogue.metadata["dataset_label"] == "new generation"


@pytest.mark.parametrize("mutation", ["missing", "malformed"])
def test_reader_rejects_missing_or_malformed_authoritative_metadata(tmp_path, mutation):
    paths, result = _build(tmp_path)
    with sqlite3.connect(result["database"]) as connection:
        if mutation == "missing":
            connection.execute("DELETE FROM catalogue_info WHERE key = 'metadata'")
        else:
            connection.execute(
                "UPDATE catalogue_info SET value_json = ? WHERE key = 'metadata'",
                ("not-json",),
            )
    with pytest.raises(CatalogueError, match="authoritative|metadata|rebuild"):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])


@pytest.mark.parametrize("field", ["build_id", "dataset_label"])
def test_reader_rejects_json_export_disagreement(tmp_path, field):
    paths, result = _build(tmp_path)
    metadata_path = Path(result["metadata"])
    exported = json.loads(metadata_path.read_text(encoding="utf-8"))
    if field == "build_id":
        exported[field] = "different-generation"
    else:
        exported[field] = "different metadata"
    metadata_path.write_text(json.dumps(exported), encoding="utf-8")

    expected = "build identifiers differ" if field == "build_id" else "metadata objects differ"
    with pytest.raises(CatalogueIntegrityError, match=expected):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])


def test_reader_closes_connection_when_initialisation_fails(tmp_path, monkeypatch):
    paths, result = _build(tmp_path)
    metadata_path = Path(result["metadata"])
    exported = json.loads(metadata_path.read_text(encoding="utf-8"))
    exported["dataset_label"] = "different metadata"
    metadata_path.write_text(json.dumps(exported), encoding="utf-8")

    connections = []
    original_connect = catalogue_module.sqlite3.connect

    def tracking_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(catalogue_module.sqlite3, "connect", tracking_connect)
    with pytest.raises(CatalogueIntegrityError, match="metadata objects differ"):
        DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3])

    assert len(connections) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


def test_competing_catalogue_writer_fails_without_waiting(tmp_path):
    paths = _fixture(tmp_path)
    output = tmp_path / "catalogue"
    with resource_lock(output, "held test writer"):
        with pytest.raises(CatalogueError, match="locked"):
            build_catalogue(*paths[:4], output)


def test_open_reader_keeps_its_generation_when_replacement_is_allowed(tmp_path):
    paths, result = _build(tmp_path)
    output = result["catalogue_dir"]
    old_id = result["metadata_payload"]["build_id"]
    before = _published_pair_bytes(result)

    reader = DatasetCatalogue(output, paths[0], paths[3])
    try:
        try:
            rebuilt = build_catalogue(*paths[:4], output, dataset_label="new generation", overwrite=True)
        except CataloguePublicationError as exc:
            if os.name != "nt":
                raise
            assert "database publication failed" in str(exc)
            assert "JSON publication failed" not in str(exc)
            cause = exc.__cause__
            assert isinstance(cause, OSError)
            cause_text = str(cause).lower()
            assert any(
                phrase in cause_text
                for phrase in ("being used by another process", "access is denied", "permission denied")
            ), f"unexpected Windows publication cause: {cause!r}"
            assert _published_pair_bytes(result) == before
            assert reader.metadata["build_id"] == old_id
            old_lookup = reader.lookup(503050, 171150)
            assert old_lookup["catalogue"]["schema_version"] == 2
            reader.close()
            build_catalogue(*paths[:4], output, overwrite=True)
            pytest.skip("Windows may prohibit replacing an open SQLite database")
        assert reader.metadata["build_id"] == old_id
        assert reader.metadata["dataset_label"] != "new generation"
        old_lookup = reader.lookup(503050, 171150)
        assert old_lookup["catalogue"]["schema_version"] == 2
    finally:
        reader.close()

    with DatasetCatalogue(output, paths[0], paths[3]) as new_reader:
        assert new_reader.metadata["build_id"] == rebuilt["metadata_payload"]["build_id"]
        assert new_reader.metadata["dataset_label"] == "new generation"
