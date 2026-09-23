#!/usr/bin/env python3
"""Serve the bounded candidate-output viewer and exactly two local data files.

The server deliberately has no directory listing and no repository-wide static
root.  It exposes the viewer assets plus the selected JSON and GeoJSON files
under ``/data/`` only.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from rasterio.warp import transform_geom


ROOT = Path(__file__).resolve().parents[1]
VIEWER_ROOT = ROOT / "candidate_viewer"
DEFAULT_OUTPUT = ROOT / "artifacts" / "candidate_screening_pilot_v2"
WGS84_CRS = "EPSG:4326"
BNG_CRS = "EPSG:27700"


class ViewerDataError(ValueError):
    """Raised when the two candidate-output inputs cannot be joined safely."""


class ViewerSnapshot(NamedTuple):
    """Immutable serialized viewer data captured during server startup."""

    json_bytes: bytes
    geojson_bytes: bytes
    bng_bytes: bytes
    run_id: str
    component_count: int


def _finite(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ViewerDataError(f"{label} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ViewerDataError(f"{label} must be a finite number") from exc
    if not math.isfinite(result):
        raise ViewerDataError(f"{label} must be a finite number")
    return result


def _positive_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ViewerDataError(f"{label} must be a positive integer")
    return value


def _json_load(path: Path, label: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text, parse_constant=lambda token: (_ for _ in ()).throw(ValueError(token)))
    except (OSError, UnicodeError) as exc:
        raise ViewerDataError(f"Cannot read {label} {path}: {exc}") from exc
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ViewerDataError(f"Cannot parse {label} {path}: invalid JSON") from exc
    if not isinstance(value, dict):
        raise ViewerDataError(f"{label} must be a JSON object")
    return value


def _validate_ring(ring: Any, label: str) -> None:
    if not isinstance(ring, list) or len(ring) < 4:
        raise ViewerDataError(f"{label} must contain at least four coordinate pairs")
    for index, pair in enumerate(ring):
        if not isinstance(pair, list) or len(pair) < 2:
            raise ViewerDataError(f"{label}[{index}] is not a coordinate pair")
        _finite(pair[0], f"{label}[{index}][0]")
        _finite(pair[1], f"{label}[{index}][1]")


def _validate_geometry(geometry: Any, label: str) -> None:
    if not isinstance(geometry, dict):
        raise ViewerDataError(f"{label} must be a GeoJSON geometry object")
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if geometry_type == "Polygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ViewerDataError(f"{label} Polygon has no rings")
        for index, ring in enumerate(coordinates):
            _validate_ring(ring, f"{label}.coordinates[{index}]")
    elif geometry_type == "MultiPolygon":
        if not isinstance(coordinates, list) or not coordinates:
            raise ViewerDataError(f"{label} MultiPolygon has no polygons")
        for polygon_index, polygon in enumerate(coordinates):
            if not isinstance(polygon, list) or not polygon:
                raise ViewerDataError(f"{label}.coordinates[{polygon_index}] has no rings")
            for ring_index, ring in enumerate(polygon):
                _validate_ring(ring, f"{label}.coordinates[{polygon_index}][{ring_index}]")
    else:
        raise ViewerDataError(f"{label} geometry type must be Polygon or MultiPolygon")


def validate_candidate_payloads(report: dict[str, Any], geojson: dict[str, Any]) -> None:
    """Validate the join contract before a browser is allowed to display data."""
    run_id = report.get("run_id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ViewerDataError("candidates.json has no valid run_id")
    if geojson.get("type") != "FeatureCollection":
        raise ViewerDataError("candidates.geojson must be a GeoJSON FeatureCollection")
    if geojson.get("run_id") != run_id:
        raise ViewerDataError(
            f"run_id mismatch: candidates.json={run_id!r}, candidates.geojson={geojson.get('run_id')!r}"
        )
    components = report.get("components")
    features = geojson.get("features")
    if not isinstance(components, list) or not isinstance(features, list):
        raise ViewerDataError("both outputs must contain component/feature arrays")
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ViewerDataError("candidates.json has no summary object")
    if summary.get("retained_component_count") != len(components):
        raise ViewerDataError("summary retained_component_count disagrees with components")
    parameters = report.get("parameters")
    if not isinstance(parameters, dict) or not isinstance(parameters.get("bbox_bng"), list) or len(parameters["bbox_bng"]) != 4:
        raise ViewerDataError("screening parameters have no valid bbox_bng")
    bbox = [_finite(bound, f"parameters bbox_bng[{index}]") for index, bound in enumerate(parameters["bbox_bng"])]
    west, south, east, north = bbox
    if not (west < east and south < north):
        raise ViewerDataError("parameters bbox_bng must be ordered west < east and south < north")
    _finite(parameters.get("road_rail_upper_threshold_db"), "parameters road_rail_upper_threshold_db")
    _positive_int(parameters.get("minimum_component_cells"), "parameters minimum_component_cells")
    if not isinstance(summary.get("requested_cells"), int) or summary["requested_cells"] < 0:
        raise ViewerDataError("summary requested_cells must be a non-negative integer")
    if not isinstance(summary.get("retained_component_cells"), int) or summary["retained_component_cells"] < 0:
        raise ViewerDataError("summary retained_component_cells must be a non-negative integer")
    component_ids: list[str] = []
    component_id_set: set[str] = set()
    for index, component in enumerate(components):
        if not isinstance(component, dict) or not isinstance(component.get("component_id"), str):
            raise ViewerDataError(f"component {index} has no valid component_id")
        component_id = component["component_id"]
        if component_id in component_id_set:
            raise ViewerDataError(f"duplicate component_id in candidates.json: {component_id}")
        component_ids.append(component_id)
        component_id_set.add(component_id)
        if not isinstance(component.get("area_order"), int) or component["area_order"] != index + 1:
            raise ViewerDataError(f"component {component_id} has invalid area_order")
        if not isinstance(component.get("bounds_bng"), list) or len(component["bounds_bng"]) != 4:
            raise ViewerDataError(f"component {component_id} has malformed bounds_bng")
        for bound_index, bound in enumerate(component["bounds_bng"]):
            _finite(bound, f"component {component_id} bounds_bng[{bound_index}]")
        _positive_int(component.get("cell_count"), f"component {component_id} cell_count")
        _finite(component.get("area_km2"), f"component {component_id} area_km2")
        representative = component.get("representative_cell")
        if not isinstance(representative, dict) or not isinstance(representative.get("center_bng"), dict):
            raise ViewerDataError(f"component {component_id} has no representative point")
        _finite(representative["center_bng"].get("easting_m"), f"component {component_id} representative easting")
        _finite(representative["center_bng"].get("northing_m"), f"component {component_id} representative northing")
        upper = component.get("road_rail_upper_db")
        if not isinstance(upper, dict):
            raise ViewerDataError(f"component {component_id} has malformed road/rail upper detail")
        for field in ("min", "max", "requested_threshold_db"):
            _finite(upper.get(field), f"component {component_id} road_rail_upper_db.{field}")
        if not isinstance(component.get("airport"), dict):
            raise ViewerDataError(f"component {component_id} has no airport detail")
        source_tile_ids = component.get("source_tile_ids")
        if not isinstance(source_tile_ids, list) or any(not isinstance(tile_id, str) for tile_id in source_tile_ids):
            raise ViewerDataError(f"component {component_id} has malformed source_tile_ids")
    feature_ids: list[str] = []
    feature_id_set: set[str] = set()
    for index, feature in enumerate(features):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ViewerDataError(f"GeoJSON feature {index} is malformed")
        properties = feature.get("properties")
        if not isinstance(properties, dict) or not isinstance(properties.get("component_id"), str):
            raise ViewerDataError(f"GeoJSON feature {index} has no valid component_id property")
        component_id = properties["component_id"]
        if component_id in feature_id_set:
            raise ViewerDataError(f"duplicate component_id in candidates.geojson: {component_id}")
        feature_ids.append(component_id)
        feature_id_set.add(component_id)
        _validate_geometry(feature.get("geometry"), f"GeoJSON feature {index}")
    if component_ids != feature_ids:
        missing = sorted(component_id_set - feature_id_set)
        extra = sorted(feature_id_set - component_id_set)
        raise ViewerDataError(f"component_id mismatch: missing={missing}, extra={extra}")
    if geojson.get("schema_version") != report.get("schema_version"):
        raise ViewerDataError("schema_version mismatch between candidates.json and candidates.geojson")
    policy = report.get("screening_policy")
    if not isinstance(policy, dict) or geojson.get("screening_policy_version") != policy.get("version"):
        raise ViewerDataError("screening policy version mismatch between candidate outputs")


def read_candidate_payloads(json_path: Path, geojson_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load and validate candidate JSON/GeoJSON before starting the server."""
    for path, label in ((json_path, "candidates.json"), (geojson_path, "candidates.geojson")):
        if not path.is_file():
            raise ViewerDataError(f"{label} is not a regular file: {path}")
    report = _json_load(json_path, "candidates.json")
    geojson = _json_load(geojson_path, "candidates.geojson")
    validate_candidate_payloads(report, geojson)
    return report, geojson


def _json_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ViewerDataError(f"Viewer payload cannot be serialized safely: {exc}") from exc


def _derive_bng_payload(report: dict[str, Any], geojson: dict[str, Any]) -> dict[str, Any]:
    """Create the viewer-only BNG geometry snapshot through Rasterio/PROJ."""
    features = []
    for component, source_feature in zip(report["components"], geojson["features"]):
        component_id = component["component_id"]
        try:
            geometry = transform_geom(
                WGS84_CRS,
                BNG_CRS,
                source_feature["geometry"],
                precision=12,
            )
        except (TypeError, ValueError) as exc:
            raise ViewerDataError(f"Cannot transform geometry for component {component_id}: {exc}") from exc
        _validate_geometry(geometry, f"BNG geometry for component {component_id}")
        center = component["representative_cell"]["center_bng"]
        representative_center = {
            "easting_m": _finite(center["easting_m"], f"component {component_id} representative easting"),
            "northing_m": _finite(center["northing_m"], f"component {component_id} representative northing"),
        }
        properties = dict(source_feature["properties"])
        properties["representative_center_bng"] = representative_center
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": properties,
        })
    return {
        "type": "FeatureCollection",
        "run_id": report["run_id"],
        "schema_version": report["schema_version"],
        "screening_policy_version": report["screening_policy"]["version"],
        "display_crs": BNG_CRS,
        "source_crs": WGS84_CRS,
        "bbox_bng": list(report["parameters"]["bbox_bng"]),
        "features": features,
    }


def _validate_bng_payload(report: dict[str, Any], bng_payload: dict[str, Any]) -> None:
    if bng_payload.get("type") != "FeatureCollection":
        raise ViewerDataError("derived BNG payload must be a GeoJSON FeatureCollection")
    if bng_payload.get("display_crs") != BNG_CRS or bng_payload.get("source_crs") != WGS84_CRS:
        raise ViewerDataError("derived BNG payload has an unexpected coordinate reference system")
    if bng_payload.get("run_id") != report["run_id"]:
        raise ViewerDataError("run_id mismatch in derived BNG payload")
    if bng_payload.get("schema_version") != report["schema_version"]:
        raise ViewerDataError("schema_version mismatch in derived BNG payload")
    if bng_payload.get("screening_policy_version") != report["screening_policy"]["version"]:
        raise ViewerDataError("screening policy version mismatch in derived BNG payload")
    if bng_payload.get("bbox_bng") != report["parameters"]["bbox_bng"]:
        raise ViewerDataError("bbox_bng mismatch in derived BNG payload")
    features = bng_payload.get("features")
    if not isinstance(features, list) or len(features) != len(report["components"]):
        raise ViewerDataError("derived BNG feature count disagrees with candidates.json")
    for index, (component, feature) in enumerate(zip(report["components"], features)):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            raise ViewerDataError(f"derived BNG feature {index} is malformed")
        properties = feature.get("properties")
        if not isinstance(properties, dict) or properties.get("component_id") != component["component_id"]:
            raise ViewerDataError(f"derived BNG feature {index} has a mismatched component_id")
        if properties.get("representative_center_bng") != component["representative_cell"]["center_bng"]:
            raise ViewerDataError(f"derived BNG feature {index} has a mismatched representative point")
        _validate_geometry(feature.get("geometry"), f"derived BNG feature {index}")


def build_viewer_snapshot(report: dict[str, Any], geojson: dict[str, Any]) -> ViewerSnapshot:
    """Validate both source outputs and serialize one immutable viewer snapshot."""
    validate_candidate_payloads(report, geojson)
    bng_payload = _derive_bng_payload(report, geojson)
    _validate_bng_payload(report, bng_payload)
    return ViewerSnapshot(
        json_bytes=_json_bytes(report),
        geojson_bytes=_json_bytes(geojson),
        bng_bytes=_json_bytes(bng_payload),
        run_id=report["run_id"],
        component_count=len(report["components"]),
    )


def load_viewer_snapshot(json_path: Path, geojson_path: Path) -> ViewerSnapshot:
    """Read, validate, transform, and freeze the two source files once."""
    report, geojson = read_candidate_payloads(json_path, geojson_path)
    return build_viewer_snapshot(report, geojson)


class _ViewerHandler(BaseHTTPRequestHandler):
    server_version = "QuietUKCandidateViewer/1.0"

    def do_GET(self) -> None:  # noqa: N802
        route = urlsplit(self.path).path
        route_map = {
            "/": (VIEWER_ROOT / "index.html", "text/html; charset=utf-8"),
            "/index.html": (VIEWER_ROOT / "index.html", "text/html; charset=utf-8"),
            "/app.js": (VIEWER_ROOT / "app.js", "text/javascript; charset=utf-8"),
            "/styles.css": (VIEWER_ROOT / "styles.css", "text/css; charset=utf-8"),
        }
        if self.server.snapshot is not None:
            route_map.update({
                "/data/candidates.json": (self.server.snapshot.json_bytes, "application/json; charset=utf-8"),
                "/data/candidates.geojson": (self.server.snapshot.geojson_bytes, "application/geo+json; charset=utf-8"),
                "/data/candidates-bng.json": (self.server.snapshot.bng_bytes, "application/geo+json; charset=utf-8"),
            })
        selected = route_map.get(route)
        if selected is None:
            if route in {"/data/candidates.json", "/data/candidates.geojson", "/data/candidates-bng.json"} and self.server.data_error:
                payload = f"Candidate viewer data error: {self.server.data_error}\n".encode("utf-8")
                self.send_response(422)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return
            self.send_error(404, "The candidate viewer exposes only its UI and selected outputs")
            return
        payload_or_path, content_type = selected
        if isinstance(payload_or_path, bytes):
            payload = payload_or_path
        else:
            try:
                payload = payload_or_path.read_bytes()
            except OSError as exc:
                self.send_error(500, str(exc))
                return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write(f"candidate-viewer: {format % args}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve the local read-only Quiet UK candidate viewer.")
    parser.add_argument("--json", type=Path, help="candidate JSON file (default: v2 pilot artifact)")
    parser.add_argument("--geojson", type=Path, help="candidate GeoJSON file (default: v2 pilot artifact)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT, help="artifact directory for default file names")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    parser.add_argument("--serve-invalid", action="store_true", help="serve the viewer error state when inputs fail validation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    json_path = (args.json or args.output_dir / "candidates.json").resolve()
    geojson_path = (args.geojson or args.output_dir / "candidates.geojson").resolve()
    snapshot: ViewerSnapshot | None = None
    data_error: str | None = None
    try:
        snapshot = load_viewer_snapshot(json_path, geojson_path)
    except ViewerDataError as exc:
        print(f"Candidate viewer data error: {exc}", file=sys.stderr)
        if not args.serve_invalid:
            return 2
        data_error = str(exc)
    server = ThreadingHTTPServer((args.host, args.port), _ViewerHandler)
    server.snapshot = snapshot
    server.data_error = data_error
    print(f"Candidate viewer: http://{args.host}:{args.port}/")
    if snapshot is not None:
        print(f"Run ID: {snapshot.run_id} ({snapshot.component_count} components)")
    else:
        print("Viewer data validation failed; the browser will show the error state.")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCandidate viewer stopped.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
