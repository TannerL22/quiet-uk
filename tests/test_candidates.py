import json
import sqlite3
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform_geom
from shapely.geometry import shape as shapely_shape

import quiet_uk.candidates as candidates_module
from quiet_uk.candidates import (
    CandidateScreeningError,
    _polygonize_labelled_region,
    extract_candidates,
    read_screening_mask_header,
    screen_candidates,
    validate_screening_request,
    write_candidate_outputs,
)
from quiet_uk.catalogue import CatalogueIntegrityError, build_catalogue

from test_catalogue import (
    _boundary_fixture,
    _build,
    _fixture,
    _rewrite_band_cell,
    _update_catalogue_tile_checksum,
)


BBOX = (503000.0, 171000.0, 503800.0, 171200.0)
SYNTHETIC_TRANSFORM = Affine(100, 0, 500000, 0, -100, 600000)


def _screen(tmp_path, *, threshold=61.0, minimum=1):
    paths, catalogue = _build(tmp_path / "fixture")
    screening = screen_candidates(
        catalogue["catalogue_dir"],
        paths[0],
        paths[3],
        BBOX,
        threshold,
        minimum,
    )
    return paths, catalogue, screening


def _assert_exact_geometry_membership(labels, retained_labels, *, hole=False):
    labels = np.asarray(labels, dtype=np.int32)
    geometries = _polygonize_labelled_region(labels, set(retained_labels), SYNTHETIC_TRANSFORM)
    expected_union = np.isin(labels, list(retained_labels))
    actual_union = np.zeros(labels.shape, dtype=bool)
    first_cells = {}
    for label in retained_labels:
        expected = labels == label
        geometry = geometries[label]
        polygon = shapely_shape(geometry)
        assert polygon.is_valid
        if hole and label == next(iter(retained_labels)):
            interior_count = (
                sum(len(part.interiors) for part in polygon.geoms)
                if geometry["type"] == "MultiPolygon"
                else len(polygon.interiors)
            )
            assert interior_count > 0
        bng_geometry = transform_geom("EPSG:4326", "EPSG:27700", geometry, precision=12)
        rasterized = rasterize(
            [(bng_geometry, 1)],
            out_shape=labels.shape,
            transform=SYNTHETIC_TRANSFORM,
            fill=0,
            all_touched=False,
            dtype="uint8",
        ).astype(bool)
        assert np.array_equal(rasterized, expected)
        assert not np.any(actual_union & rasterized)
        actual_union |= rasterized
        first_cells[label] = tuple(np.argwhere(expected)[0])
    assert np.array_equal(actual_union, expected_union)
    for label, (row, col) in first_cells.items():
        assert actual_union[row, col]
    return geometries


def test_screen_joins_four_neighbour_components_across_tile_seams_and_reports_airport_separately(tmp_path):
    _, _, screening = _screen(tmp_path, threshold=61.0, minimum=5)
    report = screening.report

    assert report["summary"]["requested_cells"] == 16
    assert report["summary"]["land_cells"] == 15
    assert report["summary"]["eligible_cells_before_filter"] == 8
    assert report["summary"]["retained_component_count"] == 1
    assert report["summary"]["retained_component_cells"] == 8
    assert report["summary"]["road_rail_withheld_or_nonqualified_land_cells"] == 4
    assert report["summary"]["road_rail_above_threshold_cells"] == 3

    component = report["components"][0]
    assert component["cell_count"] == 8
    assert component["area_km2"] == 0.08
    assert component["road_rail_upper_db"] == {
        "min": 60.0,
        "max": 61.0,
        "requested_threshold_db": 61.0,
    }
    assert component["source_tile_ids"] == ["r0000c0000", "r0000c0001"]
    assert component["representative_cell"]["center_bng"] == {
        "easting_m": 503050.0,
        "northing_m": 171150.0,
    }
    assert component["touches_requested_bbox_boundary"] is True
    assert component["adjoins_road_rail_withheld_land"] is True
    assert component["adjoins_uncovered_land"] is False
    assert "average" not in json.dumps(component["road_rail_upper_db"]).lower()

    airport = component["airport"]
    assert airport["cell_count"] == component["cell_count"]
    assert airport["source_quality_state_counts"] == {
        "grid_accepted": 4,
        "grid_rejected": 4,
        "missing_evidence": 0,
        "outside_declared_coverage": 0,
    }
    assert airport["fraction_counts"] == {"zero": 2, "positive": 6, "unavailable": 0}
    assert airport["partition_reconciles_to_component_cells"] is True
    assert "not no aircraft noise" in airport["interpretation"]


def test_threshold_is_inclusive_and_component_order_is_area_order_not_acoustic_order(tmp_path):
    _, _, screening = _screen(tmp_path, threshold=61.0, minimum=1)
    components = screening.report["components"]
    assert components[0]["road_rail_upper_db"]["max"] == 61.0
    assert [item["area_order"] for item in components] == list(range(1, len(components) + 1))
    assert all("min" in item["road_rail_upper_db"] and "max" in item["road_rail_upper_db"] for item in components)


def test_minimum_component_filter_can_return_a_valid_empty_result(tmp_path):
    _, _, screening = _screen(tmp_path, threshold=61.0, minimum=9)
    report = screening.report
    assert report["components"] == []
    assert report["summary"]["empty_result"] is True
    assert report["summary"]["excluded_small_component_cells"] == 8
    assert report["airport_summary"]["cell_count"] == 0
    assert report["airport_summary"]["partition_reconciles_to_component_cells"] is True


def test_component_ids_and_run_id_are_stable_for_repeated_identical_requests(tmp_path):
    paths, catalogue, first = _screen(tmp_path / "same-catalogue", threshold=61.0, minimum=5)
    second = screen_candidates(
        catalogue["catalogue_dir"], paths[0], paths[3], BBOX, 61.0, 5
    )
    assert first.report["run_id"] == second.report["run_id"]
    assert [item["component_id"] for item in first.report["components"]] == [
        item["component_id"] for item in second.report["components"]
    ]


@pytest.mark.parametrize(
    ("bbox", "threshold", "minimum", "message"),
    [
        ((503050.0, 171000.0, 503800.0, 171200.0), 61.0, 1, "aligned"),
        ((503000.0, 171000.0, 502900.0, 171200.0), 61.0, 1, "ordered"),
        ((503000.0, 171000.0, 503800.0, 171200.0), 151.0, 1, "sanity range"),
        ((503000.0, 171000.0, 503800.0, 171200.0), 61.0, 0, "positive integer"),
        ((502900.0, 171000.0, 503800.0, 171200.0), 61.0, 1, "within"),
    ],
)
def test_request_validation_rejects_misaligned_ordered_threshold_and_minimum_inputs(
    tmp_path, bbox, threshold, minimum, message
):
    paths, catalogue = _build(tmp_path / "fixture")
    with pytest.raises(CandidateScreeningError, match=message):
        screen_candidates(catalogue["catalogue_dir"], paths[0], paths[3], bbox, threshold, minimum)


def test_request_validation_rejects_oversized_window_before_raster_allocation():
    from quiet_uk.candidates import _validate_request

    header = {
        "transform": [100.0, 0.0, 0.0, 0.0, -100.0, 200000.0, 0.0, 0.0, 1.0],
        "bounds": [0.0, 0.0, 200000.0, 200000.0],
        "shape": [2000, 2000],
    }
    with pytest.raises(CandidateScreeningError, match="160000"):
        _validate_request((0.0, 0.0, 40100.0, 40100.0), 45.0, 1, header)


@pytest.mark.parametrize(
    ("bbox", "threshold"),
    [
        ([10**400, 171000, 503800, 171200], 61),
        ([-10**400, 171000, 503800, 171200], 61),
        ([503000, 171000, 503800, 171200], 10**400),
        ([503000, 171000, 503800, 171200], -(10**400)),
    ],
)
def test_request_validation_converts_oversized_numbers_to_finite_number_errors(
    tmp_path, bbox, threshold
):
    paths, catalogue = _build(tmp_path / "fixture")
    header = read_screening_mask_header(paths[3])
    with pytest.raises(CandidateScreeningError, match="finite number"):
        validate_screening_request(bbox, threshold, 1, header)


def test_outputs_share_run_id_have_actual_cell_geometry_and_no_geojson_crs(tmp_path):
    paths, catalogue, screening = _screen(tmp_path / "fixture", threshold=61.0, minimum=5)
    output = tmp_path / "published"
    result = write_candidate_outputs(screening, output)
    json_payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
    geojson = json.loads(Path(result["geojson"]).read_text(encoding="utf-8"))
    assert json_payload["run_id"] == screening.report["run_id"] == geojson["run_id"]
    assert "crs" not in geojson
    assert geojson["features"][0]["geometry"]["type"] in {"Polygon", "MultiPolygon"}
    assert len(geojson["features"]) == json_payload["summary"]["retained_component_count"]
    assert "Public access status: `not_assessed`" in Path(result["markdown"]).read_text(encoding="utf-8")
    assert catalogue["catalogue_dir"] == str(Path(catalogue["catalogue_dir"]))
    assert paths[3].is_file()


def test_output_directory_must_be_empty_and_publication_is_reusable_only_with_a_new_directory(tmp_path):
    _, _, screening = _screen(tmp_path / "fixture")
    output = tmp_path / "published"
    write_candidate_outputs(screening, output)
    with pytest.raises(FileExistsError, match="not empty"):
        write_candidate_outputs(screening, output)
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("user data", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        write_candidate_outputs(screening, nonempty)


def test_selected_tile_checksum_failure_is_integrity_failure(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    _rewrite_band_cell(paths[0] / "r0000c0000.tif", 2, 0, 0, 60.5)
    with pytest.raises(CatalogueIntegrityError, match="checksum mismatch"):
        screen_candidates(catalogue["catalogue_dir"], paths[0], paths[3], BBOX, 61.0, 1)


def test_read_window_semantic_failure_is_integrity_failure_after_catalogue_identity_is_updated(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    tile_path = paths[0] / "r0000c0000.tif"
    _rewrite_band_cell(tile_path, 4, 0, 0, 1.5)
    _update_catalogue_tile_checksum(catalogue, "r0000c0000", tile_path)
    with pytest.raises(CatalogueIntegrityError, match="semantic integrity validation"):
        screen_candidates(catalogue["catalogue_dir"], paths[0], paths[3], BBOX, 61.0, 1)


def test_uncovered_land_is_excluded_and_breaks_components(tmp_path):
    paths = _fixture(tmp_path / "fixture")
    expanded_mask = tmp_path / "fixture" / "expanded-mask.tif"
    with rasterio.open(paths[3]) as source:
        mask = source.read(1)
        expanded = np.concatenate([mask, np.ones((mask.shape[0], 2), dtype=mask.dtype)], axis=1)
        with rasterio.open(
            expanded_mask,
            "w",
            driver="GTiff",
            height=expanded.shape[0],
            width=expanded.shape[1],
            count=1,
            dtype="uint8",
            crs="EPSG:27700",
            transform=Affine(100, 0, 503000, 0, -100, 171200),
            nodata=0,
        ) as destination:
            destination.write(expanded, 1)
    catalogue = build_catalogue(
        paths[0], paths[1], paths[2], expanded_mask, tmp_path / "fixture" / "catalogue"
    )
    screening = screen_candidates(
        catalogue["catalogue_dir"], paths[0], expanded_mask, (503600, 171000, 504000, 171200), 100, 1
    )
    assert screening.report["summary"]["uncovered_land_cells"] == 4
    assert screening.report["summary"]["retained_component_cells"] == 3
    assert screening.report["components"][0]["adjoins_uncovered_land"] is True


def test_tile_ownership_overlap_is_integrity_failure(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    with sqlite3.connect(catalogue["database"]) as connection:
        connection.execute("UPDATE tiles SET minx = ? WHERE tile_id = ?", (503100.0, "r0000c0001"))
        connection.commit()
    with pytest.raises(CatalogueIntegrityError, match="ambiguous tile ownership"):
        screen_candidates(catalogue["catalogue_dir"], paths[0], paths[3], (503100, 171000, 503300, 171200), 100, 1)


def test_extract_publishes_all_files_without_modifying_catalogue_inputs(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    before = {
        "database": Path(catalogue["database"]).read_bytes(),
        "metadata": Path(catalogue["metadata"]).read_bytes(),
        "mask": paths[3].read_bytes(),
    }
    output = tmp_path / "candidate-output"
    report = extract_candidates(
        catalogue["catalogue_dir"],
        paths[0],
        paths[3],
        BBOX,
        61.0,
        5,
        output,
    )
    assert {path.name for path in output.iterdir()} == {"candidates.json", "candidates.geojson", "candidates.md"}
    assert report["processing"]["read_only"] is True
    assert Path(catalogue["database"]).read_bytes() == before["database"]
    assert Path(catalogue["metadata"]).read_bytes() == before["metadata"]
    assert paths[3].read_bytes() == before["mask"]


def test_empty_result_outputs_are_valid_and_have_zero_airport_partition(tmp_path):
    _, catalogue, screening = _screen(tmp_path / "fixture", threshold=0.0, minimum=1)
    output = tmp_path / "empty-output"
    result = write_candidate_outputs(screening, output)
    payload = json.loads(Path(result["json"]).read_text(encoding="utf-8"))
    assert payload["components"] == []
    assert payload["airport_summary"]["cell_count"] == 0
    assert payload["airport_summary"]["partition_reconciles_to_component_cells"] is True
    assert catalogue["metadata_payload"]["build_id"] == screening.report["catalogue"]["build_id"]


def test_diagonal_only_contact_remains_two_components_with_nonoverlapping_footprints():
    labels = np.array([[1, 0], [0, 2]], dtype=np.int32)
    geometries = _assert_exact_geometry_membership(labels, (1, 2))
    assert set(geometries) == {1, 2}


def test_hole_remains_excluded_and_is_present_as_a_geometry_interior():
    labels = np.ones((7, 7), dtype=np.int32)
    labels[2:5, 2:5] = 0
    _assert_exact_geometry_membership(labels, (1,), hole=True)


def test_narrow_one_cell_connection_remains_one_component():
    labels = np.zeros((9, 17), dtype=np.int32)
    labels[2:7, 1:6] = 1
    labels[2:7, 11:16] = 1
    labels[4, 5:12] = 1
    geometries = _assert_exact_geometry_membership(labels, (1,))
    assert geometries[1]["type"] in {"Polygon", "MultiPolygon"}


def test_horizontal_tile_seam_is_preserved_by_labelled_polygonization():
    labels = np.zeros((6, 6), dtype=np.int32)
    labels[0:3, 1:5] = 1
    labels[3:6, 1:5] = 1
    _assert_exact_geometry_membership(labels, (1,))


def test_vertical_tile_seam_is_preserved_by_labelled_polygonization():
    labels = np.zeros((6, 6), dtype=np.int32)
    labels[1:5, 0:3] = 1
    labels[1:5, 3:6] = 1
    _assert_exact_geometry_membership(labels, (1,))


def test_four_tile_junction_keeps_edge_connections_and_diagonal_labels_separate():
    labels = np.zeros((6, 6), dtype=np.int32)
    labels[0:3, 0:3] = 1
    labels[0:3, 3:6] = 2
    labels[3:6, 0:3] = 3
    labels[3:6, 3:6] = 4
    _assert_exact_geometry_membership(labels, (1, 2, 3, 4))


def test_withheld_or_uncovered_interruption_stays_outside_both_polygons():
    labels = np.zeros((5, 9), dtype=np.int32)
    labels[1:4, 1:4] = 1
    labels[1:4, 5:8] = 2
    geometries = _assert_exact_geometry_membership(labels, (1, 2))
    assert set(geometries) == {1, 2}
    assert not np.any(labels[:, 4] == 1)


def test_complex_concave_boundary_and_corner_contact_preserve_membership():
    labels = np.zeros((8, 10), dtype=np.int32)
    labels[1:7, 1:3] = 1
    labels[1:3, 1:7] = 1
    labels[5:7, 1:8] = 1
    labels[3:5, 6:8] = 1
    labels[0, 9] = 2
    labels[1, 8] = 2
    _assert_exact_geometry_membership(labels, (1, 2))


def test_highly_fragmented_labels_preserve_each_footprint():
    labels = np.zeros((15, 15), dtype=np.int32)
    label = 1
    for row in range(0, 15, 2):
        for col in range(0, 15, 2):
            labels[row, col] = label
            label += 1
    geometries = _assert_exact_geometry_membership(labels, tuple(range(1, label)))
    assert len(geometries) == 64


def test_empty_retained_labels_do_not_call_raster_polygonization(monkeypatch):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("polygonization should not run for an empty result")

    monkeypatch.setattr(candidates_module, "shapes", unexpected_call)
    assert _polygonize_labelled_region(np.zeros((4, 4), dtype=np.int32), set(), SYNTHETIC_TRANSFORM) == {}


def test_empty_screening_result_skips_raster_polygonization(monkeypatch, tmp_path):
    def unexpected_call(*args, **kwargs):
        raise AssertionError("empty screening results should not polygonize")

    monkeypatch.setattr(candidates_module, "shapes", unexpected_call)
    _, _, screening = _screen(tmp_path, threshold=0.0, minimum=1)
    assert screening.report["summary"]["empty_result"] is True
    assert screening.report["components"] == []


def test_nonempty_screening_polygonizes_once_over_one_regional_grid(monkeypatch, tmp_path):
    calls = []
    original = candidates_module.shapes

    def counted(*args, **kwargs):
        calls.append(np.asarray(args[0]).shape)
        return original(*args, **kwargs)

    monkeypatch.setattr(candidates_module, "shapes", counted)
    _, _, screening = _screen(tmp_path, threshold=61.0, minimum=5)
    assert screening.report["summary"]["retained_component_count"] == 1
    assert calls == [(2, 8)]


@pytest.mark.parametrize("failure", ("json", "geojson", "markdown", "replace"))
def test_publication_failure_cleans_owned_temp_and_leaves_destination_unpublished(
    monkeypatch, tmp_path, failure
):
    _, _, screening = _screen(tmp_path / "fixture")
    output = tmp_path / "failed-output"
    if failure == "json":
        def fail_json(*args, **kwargs):
            raise RuntimeError("json serialization sentinel")

        monkeypatch.setattr(candidates_module.json, "dumps", fail_json)
        expected = "json serialization sentinel"
    elif failure == "geojson":
        original_dumps = candidates_module.json.dumps
        dump_calls = 0

        def fail_geojson(value, *args, **kwargs):
            nonlocal dump_calls
            dump_calls += 1
            if dump_calls == 2:
                raise RuntimeError("geojson serialization sentinel")
            return original_dumps(value, *args, **kwargs)

        monkeypatch.setattr(candidates_module.json, "dumps", fail_geojson)
        expected = "geojson serialization sentinel"
    elif failure == "markdown":
        monkeypatch.setattr(candidates_module, "_markdown", lambda report: (_ for _ in ()).throw(RuntimeError("markdown sentinel")))
        expected = "markdown sentinel"
    else:
        monkeypatch.setattr(candidates_module.os, "replace", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("replace sentinel")))
        expected = "replace sentinel"

    with pytest.raises((RuntimeError, OSError), match=expected):
        write_candidate_outputs(screening, output)
    assert not output.exists() or list(output.iterdir()) == []
    assert list(output.parent.glob(f".{output.name}.tmp-*")) == []


def test_nonempty_destination_is_untouched_on_publication_refusal(tmp_path):
    _, _, screening = _screen(tmp_path / "fixture")
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("preserve me", encoding="utf-8")
    with pytest.raises(FileExistsError, match="not empty"):
        write_candidate_outputs(screening, output)
    assert sentinel.read_text(encoding="utf-8") == "preserve me"


def _rewrite_road_rail_band(tile_path, values):
    """Rewrite only the screened road/rail upper band in a fixture tile."""
    with rasterio.open(tile_path, "r+") as dataset:
        current = dataset.read(2)
        requested = np.asarray(values, dtype="float32")
        current[current != -9999.0] = requested[current != -9999.0]
        dataset.write(current, 2)


def _assert_screened_footprints_match_cells(screening, published, expected_by_component):
    """Back-check each exported footprint against the synthetic 100 m cells."""
    geojson = json.loads(Path(published["geojson"]).read_text(encoding="utf-8"))
    features = {feature["properties"]["component_id"]: feature for feature in geojson["features"]}
    bbox = screening.report["parameters"]["bbox_bng"]
    shape = tuple(screening.report["selection_window"]["shape"])
    transform = Affine(*screening.report["selection_window"]["transform"])
    for component in screening.report["components"]:
        component_id = component["component_id"]
        feature = features[component_id]
        bng_geometry = transform_geom("EPSG:4326", "EPSG:27700", feature["geometry"], precision=12)
        actual = rasterize(
            [(bng_geometry, 1)],
            out_shape=shape,
            transform=transform,
            fill=0,
            all_touched=False,
            dtype="uint8",
        )
        expected = np.zeros(shape, dtype="uint8")
        for row, col in expected_by_component[component_id]:
            expected[row, col] = 1
        assert np.array_equal(actual, expected)
        assert component["cell_count"] == int(expected.sum())
        assert component["area_km2"] == float(expected.sum()) * 0.01
        assert bbox[0] <= component["representative_cell"]["center_bng"]["easting_m"] <= bbox[2]
        assert bbox[1] <= component["representative_cell"]["center_bng"]["northing_m"] <= bbox[3]


def test_screen_candidates_diagonal_cells_remain_separate_and_export_exact_footprints(tmp_path):
    """Exercise real tile reads, catalogue qualification, component labels and GeoJSON."""
    paths = _fixture(tmp_path / "fixture")
    tile_dir, manifest_path, report_path, mask_path, tiles = paths
    tile0 = tile_dir / f"{tiles[0]['tile_id']}.tif"
    _rewrite_road_rail_band(tile0, [[60.0, 62.0], [62.0, 60.0]])
    catalogue = build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "catalogue")

    screening = screen_candidates(
        catalogue["catalogue_dir"], tile_dir, mask_path,
        (503000.0, 171000.0, 503200.0, 171200.0), 61.0, 1,
    )
    report = screening.report
    assert report["summary"]["requested_cells"] == 4
    assert report["summary"]["eligible_cells_before_filter"] == 2
    assert report["summary"]["retained_component_count"] == 2
    assert report["summary"]["retained_component_cells"] == 2
    assert [item["cell_count"] for item in report["components"]] == [1, 1]
    assert [item["representative_cell"]["center_bng"] for item in report["components"]] == [
        {"easting_m": 503050.0, "northing_m": 171150.0},
        {"easting_m": 503150.0, "northing_m": 171050.0},
    ]
    expected = {
        report["components"][0]["component_id"]: [(0, 0)],
        report["components"][1]["component_id"]: [(1, 1)],
    }
    published = write_candidate_outputs(screening, tmp_path / "published")
    _assert_screened_footprints_match_cells(screening, published, expected)


def test_screen_candidates_joins_eligible_cells_across_north_south_tile_seam(tmp_path):
    """North/south tiles must form one 4-neighbour component at their shared edge."""
    paths = _boundary_fixture(tmp_path / "fixture")
    tile_dir, manifest_path, report_path, mask_path, tiles = paths
    for tile in tiles:
        _rewrite_road_rail_band(tile_dir / f"{tile['tile_id']}.tif", np.full((2, 2), 60.0))
    catalogue = build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "catalogue")

    screening = screen_candidates(
        catalogue["catalogue_dir"], tile_dir, mask_path,
        (503000.0, 171000.0, 503400.0, 171400.0), 61.0, 1,
    )
    report = screening.report
    assert report["summary"]["requested_cells"] == 16
    assert report["summary"]["eligible_cells_before_filter"] == 14
    assert report["summary"]["retained_component_count"] == 1
    component = report["components"][0]
    assert component["cell_count"] == 14
    assert component["source_tile_ids"] == [tile["tile_id"] for tile in tiles]
    assert component["representative_cell"]["center_bng"] == {
        "easting_m": 503050.0,
        "northing_m": 171350.0,
    }
    assert component["touches_requested_bbox_boundary"] is True
    published = write_candidate_outputs(screening, tmp_path / "published")
    expected = {component["component_id"]: [(row, col) for row in range(4) for col in range(4)]}
    expected[component["component_id"]].remove((0, 2))
    expected[component["component_id"]].remove((3, 1))
    _assert_screened_footprints_match_cells(screening, published, expected)


def test_screen_candidates_connects_four_tile_junction_without_diagonal_shortcut(tmp_path):
    """The four cells around a tile junction connect through shared edges."""
    paths = _boundary_fixture(tmp_path / "fixture")
    tile_dir, manifest_path, report_path, mask_path, tiles = paths
    for tile in tiles:
        _rewrite_road_rail_band(tile_dir / f"{tile['tile_id']}.tif", np.full((2, 2), 100.0))
    junction_cells = {
        "r0000c0000": [(1, 1)],
        "r0000c0001": [(1, 0)],
        "r0001c0000": [(0, 1)],
        "r0001c0001": [(0, 0)],
    }
    for tile_id, cells in junction_cells.items():
        path = tile_dir / f"{tile_id}.tif"
        with rasterio.open(path, "r+") as dataset:
            values = dataset.read(2)
            for row, col in cells:
                values[row, col] = 60.0
            dataset.write(values, 2)
    catalogue = build_catalogue(tile_dir, manifest_path, report_path, mask_path, tmp_path / "catalogue")

    screening = screen_candidates(
        catalogue["catalogue_dir"], tile_dir, mask_path,
        (503000.0, 171000.0, 503400.0, 171400.0), 61.0, 1,
    )
    report = screening.report
    assert report["summary"]["eligible_cells_before_filter"] == 4
    assert report["summary"]["retained_component_count"] == 1
    component = report["components"][0]
    assert component["cell_count"] == 4
    assert component["representative_cell"]["center_bng"] == {
        "easting_m": 503150.0,
        "northing_m": 171250.0,
    }
    assert component["source_tile_ids"] == [tile["tile_id"] for tile in tiles]
    published = write_candidate_outputs(screening, tmp_path / "published")
    _assert_screened_footprints_match_cells(
        screening,
        published,
        {component["component_id"]: [(1, 1), (1, 2), (2, 1), (2, 2)]},
    )
