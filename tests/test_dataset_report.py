import hashlib
import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

import quiet_uk.dataset_report as dataset_report_module
from quiet_uk.catalogue import build_catalogue
from quiet_uk.dataset_report import (
    DatasetReportError,
    DatasetReportIntegrityError,
    generate_dataset_report,
)


BASE_EASTING = 1000.0
BASE_NORTHING = 1000.0
SOURCES = ("road", "rail", "airport")
BANDS = (
    "combined_reported_lower_db",
    "road_rail_upper_db",
    "airport_reported_lower_db",
    "airport_reported_fraction",
)


def _tile(tile_id, col):
    left = BASE_EASTING + col * 200.0
    return {
        "tile_id": tile_id,
        "row": 0,
        "col": col,
        "bbox_epsg27700": [left, BASE_NORTHING, left + 200.0, BASE_NORTHING + 200.0],
        "source_resolution_m": 10,
        "output_resolution_m": 100,
        "source_shape": [20, 20],
        "output_shape": [2, 2],
    }


def _raw_grid(tile, shift=0.0):
    left, bottom, right, top = tile["bbox_epsg27700"]
    left += shift
    right += shift
    return {
        "shape": [20, 20],
        "crs": "EPSG:27700",
        "transform": [10.0, 0.0, left, 0.0, -10.0, top, 0.0, 0.0, 1.0],
        "bounds": [left, bottom, right, top],
    }


def _write_tile(path, tile, land, base_value):
    values = np.stack([
        np.full((2, 2), base_value, dtype="float32"),
        np.full((2, 2), base_value + 10.0, dtype="float32"),
        np.full((2, 2), base_value - 5.0, dtype="float32"),
        np.full((2, 2), 0.5, dtype="float32"),
    ])
    values[:, ~land] = -9999.0
    land_positions = np.argwhere(land)
    if len(land_positions):
        row, column = land_positions[0]
        values[0, row, column] = -9999.0
        values[2, row, column] = -9999.0
        values[3, row, column] = 0.0
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
        for index, name in enumerate(BANDS, start=1):
            dataset.write(values[index - 1], index)
            dataset.set_band_description(index, name)


def _report_row(tile, source, *, acceptance="accepted", skip=False, shift=0.0):
    raw = None if skip else _raw_grid(tile, shift=shift)
    left, bottom, right, top = tile["bbox_epsg27700"]
    padding = 5.0 if source == "airport" else 0.0
    return {
        "tile_id": tile["tile_id"],
        "source": source,
        "coverage_id": f"{source}-coverage",
        "wcs_version": "2.0.1" if source == "airport" else "1.0.0",
        "metadata_provenance": "synthetic fixture metadata",
        "requested_bounds_epsg27700": [left - padding, bottom - padding, right + padding, top + padding],
        "requested_shape": [20, 20],
        "recorded_response_bounds_epsg27700": None if raw is None else raw["bounds"],
        "recorded_response_shape": None if raw is None else raw["shape"],
        "recorded_response_transform": None if raw is None else raw["transform"],
        "policy_version": "1",
        "policy_acceptance": "skipped" if skip else acceptance,
        "policy_name": "exact-target-grid" if not skip else None,
        "rejection_category": None if acceptance == "accepted" else "resolution",
        "coverage_relationship": "outside declared coverage" if skip else "matches request",
        "geometric_pattern": "skipped-no-raw-grid" if skip else "exact-requested-grid",
        "resolution_changed": False if raw is not None else None,
        "target_support_cells": None if raw is None else 400,
        "target_cells_without_recorded_support": None if raw is None else 0,
        "historical_alignment_performed": False if raw is not None else None,
        "historical_alignment_method": None,
        "historical_reprojection_evidence": "synthetic metadata",
        "evidence_based_classification": "Deliberately skipped source request" if skip else "Exact requested grid",
        "proposed_disposition": "Retain explicit airport outside-domain skip" if skip else "A. supported",
    }


def _make_fixture(
    tmp_path,
    *,
    historical_fields=None,
    historical_fields_by_tile=None,
    zero_land=False,
    config_metric="Lden",
    config_coverage_ids=None,
):
    tile_dir = tmp_path / "tiles"
    tile_dir.mkdir(parents=True)
    tiles = [_tile(f"r0000c{col:04d}", col) for col in range(3)]
    mask_values = np.zeros((2, 8), dtype="uint8") if zero_land else np.array(
        [
            [1, 1, 1, 0, 1, 1, 1, 0],
            [1, 0, 1, 1, 0, 0, 1, 1],
        ],
        dtype="uint8",
    )
    mask_path = tmp_path / "mask.tif"
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
        dataset.write(mask_values, 1)
    for col, tile in enumerate(tiles):
        _write_tile(tile_dir / f"{tile['tile_id']}.tif", tile, mask_values[:, col * 2:col * 2 + 2] > 0, 50.0 + col)

    manifest_tiles = {}
    report_rows = []
    for col, tile in enumerate(tiles):
        source_info = {}
        for source in SOURCES:
            skip = col == 1 and source == "airport"
            rejected = col == 2 and source == "airport"
            row = _report_row(tile, source, acceptance="rejected" if rejected else "accepted", skip=skip, shift=10.0 if rejected else 0.0)
            report_rows.append(row)
            source_info[source] = {"skipped_outside_declared_coverage": True} if skip else {"raw_grid": _raw_grid(tile, shift=10.0 if rejected else 0.0)}
        for source, fields in (historical_fields or {}).items():
            if source in source_info:
                source_info[source].update(fields)
        for source, fields in (historical_fields_by_tile or {}).get(tile["tile_id"], {}).items():
            if source in source_info:
                source_info[source].update(fields)
        manifest_tiles[tile["tile_id"]] = {"tile": tile, "source_info": source_info}

    manifest_path = tmp_path / "manifest.json"
    report_path = tmp_path / "reconciliation.json"
    config_path = tmp_path / "config.json"
    manifest_path.write_text(json.dumps({"crs": "EPSG:27700", "tiles": manifest_tiles}), encoding="utf-8")
    report_path.write_text(json.dumps({"audit_schema_version": 2, "rows": report_rows}), encoding="utf-8")
    config = {
        "crs": "EPSG:27700",
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs_versions": {"road": "1.0.0", "rail": "1.0.0", "airport": "2.0.1"},
        "coverage_ids": config_coverage_ids or {source: f"configured-{source}" for source in SOURCES},
        "wcs": {source: f"https://example.test/{source}" for source in SOURCES},
    }
    if config_metric is not None:
        config["metric"] = config_metric
    config_path.write_text(json.dumps(config), encoding="utf-8")
    catalogue_dir = tmp_path / "catalogue"
    build = build_catalogue(
        tile_dir,
        manifest_path,
        report_path,
        mask_path,
        catalogue_dir,
        config_path=config_path,
    )
    return {
        "tile_dir": tile_dir,
        "manifest": manifest_path,
        "report": report_path,
        "mask": mask_path,
        "config": config_path,
        "catalogue": Path(build["catalogue_dir"]),
        "database": Path(build["database"]),
    }


def _generate(paths, output):
    return generate_dataset_report(
        paths["catalogue"],
        paths["tile_dir"],
        paths["mask"],
        paths["manifest"],
        paths["report"],
        paths["config"],
        output,
    )


def test_repro_contradictory_scalar_aliases_are_conflicting(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"metric": "Lden", "noise_metric": "Lnight"}},
        config_metric=None,
    )

    report = _generate(paths, tmp_path / "dataset-report")['report_payload']
    field = report["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "conflicting"
    assert field["value"] == {"historical": ["Lden", "Lnight"], "configured": None}
    assert {item["field_path"] for item in field["observations"]} >= {
        "tiles.r0000c0000.source_info.road.metric",
        "tiles.r0000c0000.source_info.road.noise_metric",
    }


def test_repro_matching_historical_and_configured_coverage_lists_are_equivalent(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"coverage_ids": ["A", "B"]}},
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    report = _generate(paths, tmp_path / "dataset-report")['report_payload']
    field = report["evidence_register"]["road"]["coverage_identifiers"]

    assert field["value"] == ["A", "B"]
    assert field["evidence_status"] == "recorded_in_historical_input"


def test_scalar_coverage_id_and_singleton_list_are_equivalent(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"coverage_id": "A"}},
        config_coverage_ids={"road": ["A"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["coverage_identifiers"]

    assert field["value"] == ["A"]
    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["varies_across_tiles"] is False


def test_matching_multi_id_lists_ignore_order_and_duplicates(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"coverage_ids": ["B", "A", "A"]}},
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["coverage_identifiers"]

    assert field["value"] == ["A", "B"]
    assert field["evidence_status"] == "recorded_in_historical_input"


@pytest.mark.parametrize("malformed", [["A", ["B"]], {"id": "A"}, "", [], ["A", 3], [""]])
def test_malformed_coverage_identifiers_are_rejected_clearly(tmp_path, malformed):
    paths = _make_fixture(tmp_path, historical_fields={"road": {"coverage_ids": malformed}})

    with pytest.raises(DatasetReportIntegrityError, match="Invalid coverage identifiers"):
        _generate(paths, tmp_path / "dataset-report")


def test_malformed_configured_coverage_identifiers_are_rejected_clearly(tmp_path):
    paths = _make_fixture(
        tmp_path,
        config_coverage_ids={"road": {"nested": "A"}, "rail": "configured-rail", "airport": "configured-airport"},
    )

    with pytest.raises(DatasetReportIntegrityError, match="Invalid coverage identifiers"):
        _generate(paths, tmp_path / "dataset-report")


def test_equivalent_coverage_aliases_within_one_record_are_not_conflicting(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"coverage_id": "A", "coverage_ids": ["A", "A"]}},
        config_coverage_ids={"road": "A", "rail": "configured-rail", "airport": "configured-airport"},
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["coverage_identifiers"]

    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["value"] == ["A"]
    assert {item["field_path"] for item in field["observations"]} >= {
        "tiles.r0000c0000.source_info.road.coverage_id",
        "tiles.r0000c0000.source_info.road.coverage_ids",
    }


def test_incompatible_coverage_aliases_within_one_record_fail_with_references(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"coverage_id": "A", "coverage_ids": ["B"]}},
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    with pytest.raises(DatasetReportIntegrityError, match="identity-critical.*sets=.*A.*B.*evidence_references"):
        _generate(paths, tmp_path / "dataset-report")


def test_coverage_variation_across_tiles_is_retained_without_conflict(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields_by_tile={
            "r0000c0000": {"road": {"coverage_id": "A"}},
            "r0000c0001": {"road": {"coverage_id": "B"}},
            "r0000c0002": {"road": {"coverage_id": "A"}},
        },
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["coverage_identifiers"]

    assert field["value"] == ["A", "B"]
    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["varies_across_tiles"] is True
    assert {item["tile_id"] for item in field["observations"]} == {
        "r0000c0000", "r0000c0001", "r0000c0002", None,
    }


def test_reference_year_variation_across_tiles_is_not_labelled_conflicting(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields_by_tile={
            "r0000c0000": {"road": {"reference_year": 2020}},
            "r0000c0001": {"road": {"reference_year": 2021}},
            "r0000c0002": {"road": {"reference_year": 2020}},
        },
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["reference_year_or_period"]

    assert field["value"] == [2020, 2021]
    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["varies_across_tiles"] is True


def test_repeated_equal_observations_keep_all_supporting_references(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"metric": "Lden"}},
        config_metric=None,
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["noise_metric"]
    references = [item for item in field["evidence_references"] if item["tile_id"] is not None]

    assert field["value"] == "Lden"
    assert len(references) == 3
    assert {item["tile_id"] for item in references} == {"r0000c0000", "r0000c0001", "r0000c0002"}


def test_explicit_null_is_distinguished_from_absence(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"metric": None}},
        config_metric=None,
    )

    report = _generate(paths, tmp_path / "dataset-report")["report_payload"]
    null_field = report["evidence_register"]["road"]["noise_metric"]
    absent_field = report["evidence_register"]["road"]["reference_year_or_period"]

    assert null_field["value"] is None
    assert null_field["evidence_status"] == "recorded_in_historical_input"
    assert null_field["evidence_context"]["historical_present"] is True
    assert null_field["observations"][0]["original_value"] is None
    assert absent_field["value"] is None
    assert absent_field["evidence_status"] == "not_established"
    assert absent_field["observations"] == []


def test_evidence_register_ordering_is_deterministic(tmp_path):
    paths = _make_fixture(
        tmp_path / "inputs",
        historical_fields={"road": {"metric": "Lden", "coverage_ids": ["B", "A"]}},
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    first = _generate(paths, tmp_path / "first-report")["report_payload"]
    second = _generate(paths, tmp_path / "second-report")["report_payload"]

    assert first["evidence_register"] == second["evidence_register"]
    assert first["unresolved_evidence"] == second["unresolved_evidence"]


def test_json_and_markdown_expose_conflicts_and_variation_consistently(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields={"road": {"metric": "Lden", "noise_metric": "Lnight"}},
        historical_fields_by_tile={
            "r0000c0000": {"road": {"coverage_id": "A"}},
            "r0000c0001": {"road": {"coverage_id": "B"}},
        },
        config_metric=None,
        config_coverage_ids={"road": ["A", "B"], "rail": "configured-rail", "airport": "configured-airport"},
    )

    result = _generate(paths, tmp_path / "dataset-report")
    report = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
    markdown = Path(result["markdown"]).read_text(encoding="utf-8")
    metric = report["evidence_register"]["road"]["noise_metric"]
    coverage = report["evidence_register"]["road"]["coverage_identifiers"]

    assert metric["evidence_status"] == "conflicting"
    assert coverage["varies_across_tiles"] is True
    assert "`conflicting`" in markdown
    assert "`True`" in markdown
    assert "vary across tiles" in markdown
    assert "Historical/configured context" in markdown


def test_partial_historical_configuration_match_is_conflicting(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields_by_tile={
            "r0000c0000": {"road": {"metric": "Lden"}},
            "r0000c0001": {"road": {"metric": "Lnight"}},
        },
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "conflicting"
    assert field["varies_across_tiles"] is True
    assert field["value"] == {"historical": ["Lden", "Lnight"], "configured": "Lden"}


def test_configuration_matching_none_of_the_historical_values_is_conflicting(tmp_path):
    paths = _make_fixture(tmp_path, historical_fields={"road": {"metric": "Lnight"}})

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "conflicting"
    assert field["varies_across_tiles"] is False
    assert field["value"] == {"historical": "Lnight", "configured": "Lden"}


def test_configuration_matching_all_repeated_historical_values_is_recorded(tmp_path):
    paths = _make_fixture(tmp_path, historical_fields={"road": {"metric": "Lden"}})

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["varies_across_tiles"] is False
    assert field["value"] == "Lden"


def test_historical_variation_without_configuration_remains_recorded(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields_by_tile={
            "r0000c0000": {"road": {"metric": "Lden"}},
            "r0000c0001": {"road": {"metric": "Lnight"}},
        },
        config_metric=None,
    )

    field = _generate(paths, tmp_path / "dataset-report")["report_payload"]["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "recorded_in_historical_input"
    assert field["varies_across_tiles"] is True
    assert field["value"] == ["Lden", "Lnight"]


def test_partial_match_conflict_is_visible_in_json_and_markdown(tmp_path):
    paths = _make_fixture(
        tmp_path,
        historical_fields_by_tile={
            "r0000c0000": {"road": {"metric": "Lden"}},
            "r0000c0001": {"road": {"metric": "Lnight"}},
        },
    )

    result = _generate(paths, tmp_path / "dataset-report")
    report = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
    markdown = Path(result["markdown"]).read_text(encoding="utf-8")
    field = report["evidence_register"]["road"]["noise_metric"]

    assert field["evidence_status"] == "conflicting"
    assert field["varies_across_tiles"] is True
    assert field["evidence_context"]["configured_value"] == "Lden"
    assert "`conflicting`" in markdown
    assert "Lden" in markdown and "Lnight" in markdown
    assert "`True`" in markdown


def test_report_uses_whole_mask_denominator_and_reconciles_all_tables(tmp_path):
    paths = _make_fixture(tmp_path)
    result = _generate(paths, tmp_path / "dataset-report")
    report = result["report_payload"]

    assert report["mask_denominator"]["total_authoritative_land"]["cell_count"] == 11
    assert report["mask_denominator"]["owned_by_catalogue_tile"]["cell_count"] == 8
    assert report["mask_denominator"]["no_catalogue_tile"]["cell_count"] == 3
    assert report["mask_denominator"]["no_catalogue_tile"]["percentage_of_authoritative_land_cells"] == pytest.approx(3 / 11 * 100)
    assert report["mask_denominator"]["no_catalogue_tile"]["mask_cell_footprint_area_km2"] == pytest.approx(0.03)

    combined = report["per_band_coverage"]["combined_reported_lower_db"]["categories"]
    assert {key: value["cell_count"] for key, value in combined.items()} == {
        "qualified": 3,
        "qualified_with_coverage_limitation": 3,
        "withheld_due_to_source_quality": 2,
        "no_dataset_coverage": 3,
    }
    road_rail = report["per_band_coverage"]["road_rail_upper_db"]["categories"]
    assert {key: value["cell_count"] for key, value in road_rail.items()} == {
        "qualified": 8,
        "qualified_with_coverage_limitation": 0,
        "withheld_due_to_source_quality": 0,
        "no_dataset_coverage": 3,
    }
    for band in BANDS:
        assert sum(item["cell_count"] for item in report["per_band_coverage"][band]["categories"].values()) == 11
        assert report["per_band_coverage"][band]["reconciles_to_authoritative_land"] is True

    airport_states = report["source_state_land_cells"]["airport"]["categories"]
    assert {key: value["cell_count"] for key, value in airport_states.items()} == {
        "grid_accepted": 3,
        "grid_rejected": 2,
        "outside_declared_coverage": 3,
        "missing_evidence": 0,
        "no_dataset_coverage": 3,
    }


def test_report_separates_availability_and_cross_tabulation(tmp_path):
    paths = _make_fixture(tmp_path)
    report = _generate(paths, tmp_path / "dataset-report")["report_payload"]

    lower = report["value_availability"]["combined_reported_lower_db"]
    assert {key: value["cell_count"] for key, value in lower["categories"].items()} == {
        "finite_reported_value": 5,
        "legitimate_censored_or_unreported_lower_bound": 3,
        "no_dataset_coverage": 3,
    }
    upper = report["value_availability"]["road_rail_upper_db"]
    assert {key: value["cell_count"] for key, value in upper["categories"].items()} == {
        "finite_stored_upper_bound": 8,
        "no_dataset_coverage": 3,
    }
    fraction = report["value_availability"]["airport_reported_fraction"]
    assert {key: value["cell_count"] for key, value in fraction["categories"].items()} == {
        "zero_reported_fraction": 3,
        "positive_reported_fraction": 5,
        "no_dataset_coverage": 3,
    }
    cross = lower["by_qualification"]
    assert cross["withheld_due_to_source_quality"]["finite_reported_value"] == 1
    assert cross["withheld_due_to_source_quality"]["legitimate_censored_or_unreported_lower_bound"] == 1
    assert sum(cross["withheld_due_to_source_quality"].values()) == 2


def test_report_detects_overlap_before_publishing(tmp_path):
    paths = _make_fixture(tmp_path)
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute(
            "UPDATE tiles SET minx = 1100, maxx = 1300, transform_json = ? WHERE tile_id = ?",
            (json.dumps([100.0, 0.0, 1100.0, 0.0, -100.0, 1200.0, 0.0, 0.0, 1.0]), "r0000c0001"),
        )
    output = tmp_path / "dataset-report"
    with pytest.raises(DatasetReportIntegrityError, match="Overlapping.*r0000c0000.*r0000c0001"):
        _generate(paths, output)
    assert not (output / "dataset_report.json").exists()
    assert not (output / "dataset_report.md").exists()


def test_report_rejects_misaligned_tile_grid(tmp_path):
    paths = _make_fixture(tmp_path)
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute(
            "UPDATE tiles SET minx = 1250, maxx = 1450, transform_json = ? WHERE tile_id = ?",
            (json.dumps([100.0, 0.0, 1250.0, 0.0, -100.0, 1200.0, 0.0, 0.0, 1.0]), "r0000c0001"),
        )
    with pytest.raises(DatasetReportIntegrityError, match="integer mask-cell boundary"):
        _generate(paths, tmp_path / "dataset-report")


def test_report_includes_missing_evidence_state_without_rederiving_band_qualification(tmp_path):
    paths = _make_fixture(tmp_path)
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute(
            "UPDATE source_quality SET state = 'missing_evidence' WHERE tile_id = ? AND source = 'road'",
            ("r0000c0000",),
        )
    report = _generate(paths, tmp_path / "dataset-report")["report_payload"]
    road = report["source_state_land_cells"]["road"]["categories"]
    assert road["missing_evidence"]["cell_count"] == 3
    assert road["grid_accepted"]["cell_count"] == 5
    assert report["per_band_coverage"]["road_rail_upper_db"]["categories"]["qualified"]["cell_count"] == 8


@pytest.mark.parametrize("band_index,value", [(2, -9999.0), (1, np.nan)])
def test_report_rejects_semantic_invalid_values_even_with_matching_checksum(tmp_path, band_index, value):
    paths = _make_fixture(tmp_path)
    tile_path = paths["tile_dir"] / "r0000c0000.tif"
    with rasterio.open(tile_path, "r+") as dataset:
        values = dataset.read(band_index)
        values[0, 0] = value
        dataset.write(values, band_index)
    with sqlite3.connect(paths["database"]) as connection:
        connection.execute(
            "UPDATE tiles SET file_size = ?, sha256 = ? WHERE tile_id = ?",
            (tile_path.stat().st_size, hashlib.sha256(tile_path.read_bytes()).hexdigest(), "r0000c0000"),
        )
    with pytest.raises(DatasetReportIntegrityError, match="semantic integrity validation"):
        _generate(paths, tmp_path / "dataset-report")


def test_report_rejects_input_hash_mismatch(tmp_path):
    paths = _make_fixture(tmp_path)
    paths["manifest"].write_text(paths["manifest"].read_text(encoding="utf-8") + "\n", encoding="utf-8")
    output = tmp_path / "dataset-report"
    with pytest.raises(DatasetReportIntegrityError, match="checksum does not match catalogue identity"):
        _generate(paths, output)
    assert not (output / "dataset_report.json").exists()
    assert not (output / "dataset_report.md").exists()


def test_report_evidence_distinguishes_configured_only_and_not_established(tmp_path):
    paths = _make_fixture(tmp_path)
    report = _generate(paths, tmp_path / "dataset-report")["report_payload"]
    road = report["evidence_register"]["road"]
    assert road["coverage_identifiers"]["evidence_status"] == "configured_only"
    assert road["coverage_identifiers"]["value"] == ["configured-road"]
    assert road["noise_metric"]["evidence_status"] == "configured_only"
    assert road["reporting_threshold_db"]["evidence_status"] == "configured_only"
    assert road["reference_year_or_period"]["evidence_status"] == "not_established"
    assert road["declared_coverage_bounds_epsg27700"]["evidence_status"] == "not_established"
    assert road["source_grid_policy"]["evidence_status"] == "recorded_in_historical_input"
    assert any("historical" in item for item in report["unresolved_evidence"])


def test_report_surfaces_descriptive_conflict_but_rejects_identity_conflict(tmp_path):
    descriptive = _make_fixture(tmp_path / "descriptive", historical_fields={"road": {"metric": "Leq"}})
    report = _generate(descriptive, tmp_path / "descriptive-report")["report_payload"]
    metric = report["evidence_register"]["road"]["noise_metric"]
    assert metric["evidence_status"] == "conflicting"
    assert metric["value"] == {"historical": "Leq", "configured": "Lden"}

    identity = _make_fixture(tmp_path / "identity", historical_fields={"road": {"coverage_id": "historical-road"}})
    with pytest.raises(DatasetReportIntegrityError, match="identity-critical evidence"):
        _generate(identity, tmp_path / "identity-report")


def test_zero_land_report_has_explicit_zero_percentages(tmp_path):
    paths = _make_fixture(tmp_path, zero_land=True)
    report = _generate(paths, tmp_path / "dataset-report")["report_payload"]
    assert report["mask_denominator"]["total_authoritative_land"]["cell_count"] == 0
    assert report["mask_denominator"]["owned_by_catalogue_tile"]["cell_count"] == 0
    assert report["mask_denominator"]["no_catalogue_tile"]["cell_count"] == 0
    for band in BANDS:
        for item in report["per_band_coverage"][band]["categories"].values():
            assert item["percentage_of_authoritative_land_cells"] == 0.0


def test_report_serializes_json_and_markdown_from_one_object_and_refuses_nonempty_output(tmp_path):
    paths = _make_fixture(tmp_path)
    output = tmp_path / "dataset-report"
    result = _generate(paths, output)
    parsed = json.loads((output / "dataset_report.json").read_text(encoding="utf-8"))
    markdown = (output / "dataset_report.md").read_text(encoding="utf-8")
    assert parsed == result["report_payload"]
    assert result["report_payload"]["report_id"] in markdown
    assert result["report_payload"]["catalogue"]["build_id"] in markdown
    with pytest.raises(FileExistsError, match="not empty"):
        _generate(paths, output)


def test_report_serialization_failure_leaves_no_completed_pair(tmp_path, monkeypatch):
    paths = _make_fixture(tmp_path)
    output = tmp_path / "dataset-report"

    def fail_serialization(report):
        raise OSError("injected report serialization failure")

    monkeypatch.setattr(dataset_report_module, "_serialize_report_json", fail_serialization)
    with pytest.raises(OSError, match="injected report serialization failure"):
        _generate(paths, output)
    assert not (output / "dataset_report.json").exists()
    assert not (output / "dataset_report.md").exists()
    assert not list(output.glob(".dataset-report-build-*"))


def test_report_publication_failure_cleans_up_partial_pair(tmp_path, monkeypatch):
    paths = _make_fixture(tmp_path)
    output = tmp_path / "dataset-report"
    original_replace = dataset_report_module.os.replace

    def fail_markdown_publication(staged_path, published_path):
        if published_path == output / "dataset_report.md":
            raise OSError("injected Markdown publication failure")
        return original_replace(staged_path, published_path)

    monkeypatch.setattr(dataset_report_module.os, "replace", fail_markdown_publication)
    with pytest.raises(DatasetReportError, match="no completed report pair"):
        _generate(paths, output)
    assert not (output / "dataset_report.json").exists()
    assert not (output / "dataset_report.md").exists()
    assert not list(output.glob(".dataset-report-build-*"))
