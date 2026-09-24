import importlib.util
import json
import copy
import threading
import urllib.request
from pathlib import Path

import pytest
from rasterio.features import rasterize
from rasterio.transform import Affine

from quiet_uk.catalogue import DatasetCatalogue
from test_candidates import _screen


SCRIPT = Path(__file__).parents[1] / "scripts" / "24_serve_candidate_viewer.py"
SPEC = importlib.util.spec_from_file_location("candidate_viewer_server", SCRIPT)
viewer_server = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(viewer_server)


APP_JS = Path(__file__).parents[1] / "candidate_viewer" / "app.js"


@pytest.fixture(scope="module")
def candidate_data(tmp_path_factory):
    # Two components separated by withheld road evidence, plus one sea cell.
    # Build through the actual catalogue/screening pipeline, without local data.
    return _screen(tmp_path_factory.mktemp("candidate-viewer"), threshold=63.0)


@pytest.fixture
def payloads(candidate_data):
    screening = candidate_data[2]
    return copy.deepcopy((screening.report, screening.geojson))


def test_candidate_viewer_validates_generated_component_join(payloads):
    report, geojson = payloads
    viewer_server.validate_candidate_payloads(report, geojson)
    assert [component["cell_count"] for component in report["components"]] == [8, 3]
    assert len(geojson["features"]) == 2


def assert_representative_points(report, catalogue):
    for component in report["components"]:
        point = component["representative_cell"]["center_bng"]
        lookup = catalogue.lookup(point["easting_m"], point["northing_m"])
        assert lookup["land_status"] == "england_land"
        assert lookup["tile_id"] in component["source_tile_ids"]
        assert lookup["cell"]["center_bng"] == point
        assert lookup["bands"]["road_rail_upper_db"]["value"] <= component["road_rail_upper_db"]["requested_threshold_db"]


def test_candidate_viewer_representative_points_resolve_against_generated_catalogue(candidate_data, payloads):
    paths, result, _ = candidate_data
    with DatasetCatalogue(result["catalogue_dir"], paths[0], paths[3]) as catalogue:
        assert_representative_points(payloads[0], catalogue)


def test_candidate_viewer_rejects_run_id_mismatch_before_display(payloads):
    report, geojson = payloads
    geojson["run_id"] = "different-screening-run"
    with pytest.raises(viewer_server.ViewerDataError, match="run_id mismatch"):
        viewer_server.validate_candidate_payloads(report, geojson)


def test_candidate_viewer_rejects_component_id_mismatch_before_display(payloads):
    report, geojson = payloads
    geojson["features"][0]["properties"]["component_id"] = "candidate-not-in-json"
    with pytest.raises(viewer_server.ViewerDataError, match="component_id mismatch"):
        viewer_server.validate_candidate_payloads(report, geojson)


def test_candidate_viewer_rejects_malformed_geometry_before_display(payloads):
    report, geojson = payloads
    geojson["features"][0]["geometry"] = {"type": "Point", "coordinates": [0, 0]}
    with pytest.raises(viewer_server.ViewerDataError, match="Polygon or MultiPolygon"):
        viewer_server.validate_candidate_payloads(report, geojson)


def test_candidate_viewer_rejects_duplicate_component_ids_before_display(payloads):
    report, geojson = payloads
    geojson["features"][1]["properties"]["component_id"] = geojson["features"][0]["properties"]["component_id"]
    with pytest.raises(viewer_server.ViewerDataError, match="duplicate component_id"):
        viewer_server.validate_candidate_payloads(report, geojson)


def test_candidate_viewer_rejects_malformed_request_parameters_before_display(payloads):
    report, geojson = payloads
    report["parameters"]["bbox_bng"] = [410000.0, 562000.0, 409000.0, 563000.0]
    with pytest.raises(viewer_server.ViewerDataError, match="bbox_bng must be ordered"):
        viewer_server.validate_candidate_payloads(report, geojson)


def test_candidate_viewer_rejects_missing_input_file(tmp_path):
    with pytest.raises(viewer_server.ViewerDataError, match="regular file"):
        viewer_server.read_candidate_payloads(tmp_path / "missing.json", tmp_path / "missing.geojson")


def _serve_snapshot(snapshot):
    server = viewer_server.ThreadingHTTPServer(("127.0.0.1", 0), viewer_server._ViewerHandler)
    server.snapshot = snapshot
    server.data_error = None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _get(server, route):
    with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}{route}") as response:
        return response.status, response.read()


def test_candidate_viewer_derives_bng_geometry_and_retains_grid_membership(payloads):
    assert_geometry_membership(payloads)


def assert_geometry_membership(payloads):
    report, geojson = payloads
    snapshot = viewer_server.build_viewer_snapshot(report, geojson)
    derived = json.loads(snapshot.bng_bytes)

    assert derived["display_crs"] == "EPSG:27700"
    assert derived["run_id"] == report["run_id"]
    assert derived["schema_version"] == report["schema_version"]
    assert derived["screening_policy_version"] == report["screening_policy"]["version"]
    assert derived["bbox_bng"] == report["parameters"]["bbox_bng"]
    assert [feature["properties"]["component_id"] for feature in derived["features"]] == [
        component["component_id"] for component in report["components"]
    ]

    bbox = report["parameters"]["bbox_bng"]
    west, south, east, north = bbox
    width = int((east - west) / 100)
    height = int((north - south) / 100)
    transform = Affine(100, 0, west, 0, -100, north)
    for component, feature in zip(report["components"], derived["features"]):
        geometry = feature["geometry"]
        mask = rasterize([(geometry, 1)], out_shape=(height, width), transform=transform, all_touched=False)
        assert int(mask.sum()) == component["cell_count"]
        representative = feature["properties"]["representative_center_bng"]
        assert representative == component["representative_cell"]["center_bng"]
        assert west <= representative["easting_m"] <= east
        assert south <= representative["northing_m"] <= north


def test_candidate_viewer_serves_one_immutable_snapshot_after_source_files_change(tmp_path, payloads):
    json_path = tmp_path / "candidates.json"
    geojson_path = tmp_path / "candidates.geojson"
    json_path.write_text(json.dumps(payloads[0]), encoding="utf-8")
    geojson_path.write_text(json.dumps(payloads[1]), encoding="utf-8")
    snapshot = viewer_server.load_viewer_snapshot(json_path, geojson_path)
    server, thread = _serve_snapshot(snapshot)
    try:
        changed_report = json.loads(json_path.read_text(encoding="utf-8"))
        changed_report["run_id"] = "different-generation"
        json_path.write_text(json.dumps(changed_report), encoding="utf-8")
        changed_geojson = json.loads(geojson_path.read_text(encoding="utf-8"))
        changed_geojson["run_id"] = "different-generation"
        geojson_path.write_text(json.dumps(changed_geojson), encoding="utf-8")

        assert _get(server, "/data/candidates.json") == (200, snapshot.json_bytes)
        assert _get(server, "/data/candidates.geojson") == (200, snapshot.geojson_bytes)
        assert _get(server, "/data/candidates-bng.json") == (200, snapshot.bng_bytes)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_candidate_viewer_can_serve_a_clear_browser_error_without_serving_invalid_data():
    server = viewer_server.ThreadingHTTPServer(("127.0.0.1", 0), viewer_server._ViewerHandler)
    server.snapshot = None
    server.data_error = "run_id mismatch between candidate inputs"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(Exception) as error:
            urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/data/candidates.json")
        assert "422" in str(error.value)
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_candidate_viewer_source_has_no_unbounded_coordinate_spreads_or_handwritten_projection():
    source = APP_JS.read_text(encoding="utf-8")
    assert "Math.min(...lons)" not in source
    assert "Math.max(...lons)" not in source
    assert "componentIds.includes" not in source
    assert "osgb36ToWgs84" not in source
