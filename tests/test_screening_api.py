import http.client
import json
import socket
import threading
import time
from pathlib import Path

import pytest

import quiet_uk.screening_jobs as screening_jobs
from quiet_uk.candidates import extract_candidates
from quiet_uk.screening_jobs import (
    ScreeningAPIServer,
    ScreeningJobBusyError,
    ScreeningJobManager,
    ScreeningJobStartupError,
)

from test_catalogue import _build


BBOX = [503000.0, 171000.0, 503800.0, 171200.0]


def _request_payload(**overrides):
    payload = {
        "bbox_bng": list(BBOX),
        "road_rail_upper_threshold_db": 61,
        "minimum_component_cells": 5,
    }
    payload.update(overrides)
    return payload


def _http(server, method, path, body=None, *, headers=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=10)
    request_headers = dict(headers or {})
    if body is not None and isinstance(body, bytes):
        request_body = body
    elif body is None:
        request_body = None
    else:
        request_body = body.encode("utf-8")
    if request_body is not None:
        request_headers.setdefault("Content-Length", str(len(request_body)))
    connection.request(method, path, body=request_body, headers=request_headers)
    response = connection.getresponse()
    data = response.read()
    result = (response.status, response.getheader("Content-Type", ""), data)
    connection.close()
    return result


def _json_response(server, method, path, payload, *, headers=None):
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    return _http(server, method, path, json.dumps(payload), headers=request_headers)


def _open_raw_connection(server):
    connection = socket.create_connection(("127.0.0.1", server.server_address[1]), timeout=3)
    connection.settimeout(0.25)
    return connection


def _read_until_closed(connection, timeout=5):
    deadline = time.monotonic() + timeout
    received = bytearray()
    while time.monotonic() < deadline:
        try:
            chunk = connection.recv(4096)
        except socket.timeout:
            continue
        except ConnectionError:
            return bytes(received)
        if not chunk:
            return bytes(received)
        received.extend(chunk)
    raise AssertionError("server did not close the stalled HTTP connection")


def _wait_for_state(server, job_id, state, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, _, data = _http(server, "GET", f"/api/jobs/{job_id}")
        assert status == 200
        payload = json.loads(data)
        if payload["state"] == state:
            return payload
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {state}")


@pytest.fixture
def service(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    manager = ScreeningJobManager(
        catalogue["catalogue_dir"], paths[0], paths[3], tmp_path / "jobs"
    )
    server = ScreeningAPIServer(("127.0.0.1", 0), manager, request_timeout_seconds=0.2)
    thread = threading.Thread(target=server.serve_forever, daemon=False)
    server._test_thread = thread
    thread.start()
    try:
        yield server, manager, paths, catalogue
    finally:
        manager.begin_shutdown()
        server.shutdown()
        server.server_close()
        manager.close()
        thread.join(10)


def test_health_and_valid_submission_return_before_controlled_worker_completion(service, monkeypatch):
    server, manager, _, _ = service
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    try:
        status, _, data = _http(server, "GET", "/api/health")
        assert status == 200
        assert json.loads(data)["available"] is True
        started_at = time.monotonic()
        status, _, data = _json_response(server, "POST", "/api/jobs", _request_payload())
        assert status == 202
        assert time.monotonic() - started_at < 2
        accepted = json.loads(data)
        assert accepted["state"] == "running"
        assert started.wait(5)
        status, _, data = _http(server, "GET", accepted["status_url"])
        assert status == 200
        assert json.loads(data)["state"] == "running"
        release.set()
        assert _wait_for_state(server, accepted["job_id"], "succeeded")["state"] == "succeeded"
        assert manager.health()["job_active"] is False
    finally:
        release.set()


def test_two_concurrent_submissions_admit_exactly_one(service, monkeypatch):
    server, _, _, _ = service
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    responses = []

    def submit():
        responses.append(_json_response(server, "POST", "/api/jobs", _request_payload())[0])

    first = threading.Thread(target=submit)
    second = threading.Thread(target=submit)
    first.start()
    assert started.wait(5)
    second.start()
    first.join(10)
    second.join(10)
    try:
        assert sorted(responses) == [202, 409]
    finally:
        release.set()


@pytest.mark.parametrize(
    ("raw", "content_type", "expected_status"),
    [
        (json.dumps({"bbox_bng": BBOX, "road_rail_upper_threshold_db": 61, "minimum_component_cells": 5, "extra": 1}), "application/json", 400),
        (b'{"bbox_bng":[503000,171000,503800,171200],"road_rail_upper_threshold_db":61,"minimum_component_cells":5,"minimum_component_cells":5}', "application/json", 400),
        (b'{"bbox_bng":[503000,171000,503800,171200],"road_rail_upper_threshold_db":NaN,"minimum_component_cells":5}', "application/json", 400),
        (json.dumps({"bbox_bng": "503000,171000,503800,171200", "road_rail_upper_threshold_db": 61, "minimum_component_cells": 5}), "application/json", 400),
        (json.dumps(_request_payload()), "text/plain", 415),
    ],
)
def test_request_validation_and_content_type_create_no_job_directory(service, raw, content_type, expected_status):
    server, _, _, _ = service
    before = {path.name for path in Path(server.manager.job_root).iterdir() if path.is_dir()}
    status, _, _ = _http(
        server,
        "POST",
        "/api/jobs",
        raw.encode("utf-8") if isinstance(raw, str) else raw,
        headers={"Content-Type": content_type},
    )
    assert status == expected_status
    after = {path.name for path in Path(server.manager.job_root).iterdir() if path.is_dir()}
    assert after == before


def test_incomplete_headers_timeout_without_job_or_handler_traceback(service, capsys):
    server, manager, _, _ = service
    connection = _open_raw_connection(server)
    try:
        connection.sendall(
            f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n".encode()
        )
        received = _read_until_closed(connection)
    finally:
        connection.close()
    assert b"Traceback" not in received
    assert not any(path.is_dir() for path in Path(manager.job_root).iterdir())
    status, _, _ = _http(server, "GET", "/api/health")
    assert status == 200
    assert "Traceback" not in capsys.readouterr().err


def test_incomplete_post_body_returns_timeout_without_job_directory(service):
    server, manager, _, _ = service
    body = b'{"bbox_bng":[503000,171000,503800,171200]}'
    connection = _open_raw_connection(server)
    try:
        connection.sendall(
            (
                f"POST /api/jobs HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body) + 40}\r\n\r\n"
            ).encode()
            + body
        )
        received = _read_until_closed(connection)
    finally:
        connection.close()
    assert b"HTTP/1.1 408" in received
    assert b'"code": "request_timeout"' in received
    assert not any(path.is_dir() for path in Path(manager.job_root).iterdir())
    assert manager.active_job_id is None


def test_idle_connection_is_closed_and_health_remains_responsive(service):
    server, manager, _, _ = service
    connection = _open_raw_connection(server)
    try:
        received = _read_until_closed(connection)
    finally:
        connection.close()
    assert received == b""
    status, _, _ = _http(server, "GET", "/api/health")
    assert status == 200
    assert manager.active_job_id is None


def test_oversized_numeric_requests_are_400_and_do_not_consume_admission(service):
    server, manager, _, _ = service
    invalid_payloads = [
        {**_request_payload(), "bbox_bng": [10**400, 171000, 503800, 171200]},
        {**_request_payload(), "bbox_bng": [-10**400, 171000, 503800, 171200]},
        {**_request_payload(), "road_rail_upper_threshold_db": 10**400},
        {**_request_payload(), "road_rail_upper_threshold_db": -(10**400)},
    ]
    for payload in invalid_payloads:
        before = {path.name for path in Path(manager.job_root).iterdir() if path.is_dir()}
        status, _, data = _json_response(server, "POST", "/api/jobs", payload)
        assert status == 400
        assert json.loads(data)["error"]["code"] == "invalid_request"
        assert {path.name for path in Path(manager.job_root).iterdir() if path.is_dir()} == before
        assert manager.active_job_id is None
        assert _http(server, "GET", "/api/health")[0] == 200

    status, _, data = _json_response(server, "POST", "/api/jobs", _request_payload())
    assert status == 202
    assert _wait_for_state(server, json.loads(data)["job_id"], "succeeded")["state"] == "succeeded"


def test_body_limit_host_origin_and_path_allowlisting(service):
    server, _, _, _ = service
    oversized = b"{" + b"a" * (16 * 1024) + b"}"
    status, _, _ = _http(server, "POST", "/api/jobs", oversized, headers={"Content-Type": "application/json"})
    assert status == 413
    status, _, data = _json_response(
        server, "POST", "/api/jobs", _request_payload(), headers={"Host": "evil.example"}
    )
    assert status == 400
    assert json.loads(data)["error"]["code"] == "invalid_host"
    status, _, data = _json_response(
        server, "POST", "/api/jobs", _request_payload(), headers={"Origin": "http://evil.example"}
    )
    assert status == 403
    assert json.loads(data)["error"]["code"] == "invalid_origin"
    status, _, _ = _http(server, "GET", "/api/jobs/../../etc/passwd")
    assert status == 404
    status, _, _ = _http(server, "GET", "/api/jobs/does-not-exist")
    assert status == 404


def test_successful_outputs_and_running_or_unapproved_outputs_are_controlled(service, monkeypatch):
    server, _, _, _ = service
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    try:
        status, _, data = _json_response(server, "POST", "/api/jobs", _request_payload())
        assert status == 202
        job_id = json.loads(data)["job_id"]
        assert started.wait(5)
        status, _, data = _http(server, "GET", f"/api/jobs/{job_id}/outputs/candidates.json")
        assert status == 409
        assert json.loads(data)["error"]["code"] == "job_not_succeeded"
        release.set()
        final = _wait_for_state(server, job_id, "succeeded")
        assert final["summary"]["retained_component_count"] == 1
        for filename, prefix in (
            ("candidates.json", b"{"),
            ("candidates.geojson", b"{"),
            ("candidates.md", b"# Quiet UK"),
        ):
            status, content_type, data = _http(server, "GET", f"/api/jobs/{job_id}/outputs/{filename}")
            assert status == 200
            assert data.startswith(prefix)
            assert "charset=utf-8" in content_type
        status, _, data = _http(server, "GET", f"/api/jobs/{job_id}/outputs/candidates.txt")
        assert status == 404
        assert json.loads(data)["error"]["code"] == "output_not_found"
    finally:
        release.set()


def test_job_and_cli_execution_have_matching_deterministic_outputs(service):
    server, _, paths, catalogue = service
    status, _, data = _json_response(server, "POST", "/api/jobs", _request_payload())
    assert status == 202
    job = _wait_for_state(server, json.loads(data)["job_id"], "succeeded")
    direct = extract_candidates(
        catalogue["catalogue_dir"], paths[0], paths[3], BBOX, 61, 5, Path(server.manager.job_root) / "direct"
    )
    assert job["screening_run_id"] == direct["run_id"]
    assert job["summary"]["retained_component_count"] == direct["summary"]["retained_component_count"]
    assert job["summary"]["retained_component_cells"] == direct["summary"]["retained_component_cells"]
    job_json_status, _, job_json_bytes = _http(
        server, "GET", f"/api/jobs/{job['job_id']}/outputs/candidates.json"
    )
    assert job_json_status == 200
    direct_json = json.loads(Path(direct["output_paths"]["json"]).read_text(encoding="utf-8"))
    job_json = json.loads(job_json_bytes)
    assert [item["component_id"] for item in job_json["components"]] == [
        item["component_id"] for item in direct_json["components"]
    ]


def test_shutdown_closes_unfinished_http_request_while_worker_and_root_lock_drain(
    service, monkeypatch
):
    server, manager, paths, catalogue = service
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    unfinished = _open_raw_connection(server)
    close_thread = None
    fresh = None
    keep = Path(manager.job_root) / "unrelated.txt"
    keep.write_text("preserve me\n", encoding="utf-8")
    try:
        status, _, data = _json_response(server, "POST", "/api/jobs", _request_payload())
        assert status == 202
        assert started.wait(5)
        unfinished.sendall(
            f"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1:{server.server_address[1]}\r\n".encode()
        )

        shutdown_thread = threading.Thread(target=server.shutdown)
        shutdown_thread.start()
        shutdown_thread.join(5)
        assert not shutdown_thread.is_alive()

        close_server_thread = threading.Thread(target=server.server_close)
        close_server_thread.start()
        close_server_thread.join(5)
        assert not close_server_thread.is_alive()
        assert _read_until_closed(unfinished) == b""

        with pytest.raises(ScreeningJobBusyError, match="shutting down"):
            manager.submit(_request_payload())
        with pytest.raises(ScreeningJobStartupError, match="already locked"):
            ScreeningJobManager(catalogue["catalogue_dir"], paths[0], paths[3], manager.job_root)

        close_thread = threading.Thread(target=manager.close)
        close_thread.start()
        assert close_thread.is_alive()
        release.set()
        close_thread.join(10)
        assert not close_thread.is_alive()
        assert keep.read_text(encoding="utf-8") == "preserve me\n"

        fresh = ScreeningJobManager(catalogue["catalogue_dir"], paths[0], paths[3], manager.job_root)
        fresh.close()
        fresh.close()
    finally:
        unfinished.close()
        release.set()
        if close_thread is not None:
            close_thread.join(10)
        if fresh is not None:
            fresh.close()
