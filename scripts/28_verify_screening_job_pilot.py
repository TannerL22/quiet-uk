#!/usr/bin/env python3
"""Submit and compare the preserved bounded screening pilot through HTTP."""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import numpy as np
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.warp import transform_geom


REQUEST = {
    "bbox_bng": [400000, 550000, 420000, 570000],
    "road_rail_upper_threshold_db": 45,
    "minimum_component_cells": 10,
}


def _json_request(url: str, method: str, payload: dict | None = None) -> tuple[int, dict]:
    body = None if payload is None else json.dumps(payload, sort_keys=True).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Accept": "application/json", **({"Content-Type": "application/json"} if body is not None else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return int(exc.code), json.loads(exc.read().decode("utf-8"))


def _output_request(url: str, filename: str) -> bytes:
    request = urllib.request.Request(f"{url}/outputs/{filename}", method="GET")
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"output request failed: HTTP {response.status}")
        return response.read()


def _component_ids(report: dict) -> list[str]:
    return [str(component["component_id"]) for component in report["components"]]


def _compare_geometry_membership(expected_report: dict, expected_geojson: dict, actual_geojson: dict) -> int:
    west, south, east, north = expected_report["parameters"]["bbox_bng"]
    shape = (int(round((north - south) / 100)), int(round((east - west) / 100)))
    transform = Affine(100, 0, west, 0, -100, north)
    expected_by_id = {feature["properties"]["component_id"]: feature for feature in expected_geojson["features"]}
    actual_by_id = {feature["properties"]["component_id"]: feature for feature in actual_geojson["features"]}
    differences = 0
    for component_id in expected_by_id:
        expected_geometry = transform_geom("EPSG:4326", "EPSG:27700", expected_by_id[component_id]["geometry"], precision=12)
        actual_geometry = transform_geom("EPSG:4326", "EPSG:27700", actual_by_id[component_id]["geometry"], precision=12)
        expected_mask = rasterize([(expected_geometry, 1)], out_shape=shape, transform=transform, fill=0, all_touched=False)
        actual_mask = rasterize([(actual_geometry, 1)], out_shape=shape, transform=transform, fill=0, all_touched=False)
        differences += int(np.count_nonzero(expected_mask != actual_mask))
    return differences


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--pilot-dir", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    args = parser.parse_args()
    base_url = args.base_url.rstrip("/")
    expected_dir = args.pilot_dir.resolve()
    evidence_dir = args.evidence_dir.resolve()
    retrieved_dir = evidence_dir / "retrieved"
    retrieved_dir.mkdir(parents=True, exist_ok=True)

    health_status, health = _json_request(f"{base_url}/api/health", "GET")
    if health_status != 200 or health.get("available") is not True:
        raise RuntimeError(f"service is not healthy: HTTP {health_status} {health}")
    submit_status, accepted = _json_request(f"{base_url}/api/jobs", "POST", REQUEST)
    if submit_status != 202:
        raise RuntimeError(f"submission failed: HTTP {submit_status} {accepted}")
    job_id = str(accepted["job_id"])
    statuses = []
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        status_code, status = _json_request(f"{base_url}/api/jobs/{job_id}", "GET")
        if status_code != 200:
            raise RuntimeError(f"status failed: HTTP {status_code} {status}")
        statuses.append(status)
        if status["state"] != "running":
            break
        time.sleep(0.1)
    final = statuses[-1]
    if final["state"] != "succeeded":
        raise RuntimeError(f"screening job did not succeed: {final}")

    retrieved = {}
    for filename in ("candidates.json", "candidates.geojson", "candidates.md"):
        payload = _output_request(f"{base_url}/api/jobs/{job_id}", filename)
        (retrieved_dir / filename).write_bytes(payload)
        retrieved[filename] = {"http_status": 200, "bytes": len(payload)}

    expected_report = json.loads((expected_dir / "candidates.json").read_text(encoding="utf-8"))
    actual_report = json.loads((retrieved_dir / "candidates.json").read_text(encoding="utf-8"))
    expected_geojson = json.loads((expected_dir / "candidates.geojson").read_text(encoding="utf-8"))
    actual_geojson = json.loads((retrieved_dir / "candidates.geojson").read_text(encoding="utf-8"))
    summary_comparison = {
        key: actual_report["summary"][key] == expected_report["summary"][key]
        for key in (
            "requested_cells",
            "retained_component_count",
            "retained_component_cells",
            "excluded_small_component_cells",
            "empty_result",
        )
    }
    airport_comparison = [
        actual["airport"] == expected["airport"]
        for actual, expected in zip(actual_report["components"], expected_report["components"], strict=True)
    ]
    component_summary_comparison = [
        {
            "component_id": actual["component_id"],
            "matches": {
                "id": actual["component_id"] == expected["component_id"],
                "order": actual["area_order"] == expected["area_order"],
                "cell_count": actual["cell_count"] == expected["cell_count"],
                "bounds_bng": actual["bounds_bng"] == expected["bounds_bng"],
                "airport": actual["airport"] == expected["airport"],
            },
        }
        for actual, expected in zip(actual_report["components"], expected_report["components"], strict=True)
    ]
    geometry_differences = _compare_geometry_membership(expected_report, expected_geojson, actual_geojson)
    checks = {
        "deterministic_run_id": actual_report["run_id"] == expected_report["run_id"] == final["screening_run_id"],
        "component_ids_and_order": _component_ids(actual_report) == _component_ids(expected_report),
        "summary": all(summary_comparison.values()),
        "airport_partitions": all(airport_comparison),
        "component_summaries": all(all(item["matches"].values()) for item in component_summary_comparison),
        "geometry_membership_difference_cells": geometry_differences,
        "retained_components": actual_report["summary"]["retained_component_count"] == 128,
        "retained_cells": actual_report["summary"]["retained_component_cells"] == 17305,
        "excluded_small_component_cells": actual_report["summary"]["excluded_small_component_cells"] == 275,
    }
    if not all(value is True for key, value in checks.items() if key != "geometry_membership_difference_cells") or geometry_differences != 0:
        raise RuntimeError(f"pilot comparison failed: {checks}")

    record = {
        "record_type": "screening_job_http_pilot_verification",
        "base_url": base_url,
        "request": REQUEST,
        "health": {"status": health_status, "response": health},
        "submission": {"status": submit_status, "response": accepted},
        "status_history_count": len(statuses),
        "final_status": final,
        "retrieved_outputs": retrieved,
        "comparison": checks,
        "expected_pilot": str(expected_dir),
        "retrieved_dir": str(retrieved_dir),
        "notes": "Job ID, timestamps, duration, and output root are execution-specific; the run ID and screening outputs are deterministic.",
    }
    (evidence_dir / "pilot_verification.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown = [
        "# Screening-job HTTP pilot verification",
        "",
        f"- Service: `{base_url}`",
        f"- Job ID: `{job_id}`",
        f"- Status polls: `{len(statuses)}`",
        f"- Deterministic screening run ID: `{final['screening_run_id']}`",
        "",
        "## Request",
        "",
        "```json",
        json.dumps(REQUEST, indent=2),
        "```",
        "",
        "## Results",
        "",
        f"- Accepted with HTTP `{submit_status}`; final state `{final['state']}`.",
        f"- Retrieved all three outputs through HTTP: `{', '.join(retrieved)}`.",
        f"- Preserved pilot comparison: `{sum(1 for value in checks.values() if value is True)}` checks passed; geometry membership differences: `{geometry_differences}` cells.",
        f"- Retained components: `{actual_report['summary']['retained_component_count']}`; retained cells: `{actual_report['summary']['retained_component_cells']}`; excluded small-component cells: `{actual_report['summary']['excluded_small_component_cells']}`.",
        "",
        "## Retrieved files",
        "",
        *[f"- `retrieved/{filename}` ({details['bytes']} bytes)" for filename, details in retrieved.items()],
        "",
    ]
    (evidence_dir / "pilot_verification.md").write_text("\n".join(markdown), encoding="utf-8")
    print(json.dumps({"job_id": job_id, "run_id": final["screening_run_id"], "checks": checks}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
