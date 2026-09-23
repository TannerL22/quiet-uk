"""Bounded local screening jobs and their loopback-only HTTP contract.

This module deliberately orchestrates :func:`quiet_uk.candidates.extract_candidates`
instead of implementing another screening path.  The registry is process-local,
there is one active worker, and completed job directories are never removed by
this layer.
"""
from __future__ import annotations

import errno
import json
import math
import os
import re
import secrets
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .candidates import (
    CandidateScreeningError,
    extract_candidates,
    read_screening_mask_header,
    validate_screening_request,
)
from .catalogue import CatalogueError, CatalogueIntegrityError, DatasetCatalogue
from .locking import ResourceLock, ResourceLockError, resource_lock


JOB_OUTPUT_FILENAMES = ("candidates.json", "candidates.geojson", "candidates.md")
JOB_OUTPUT_CONTENT_TYPES = {
    "candidates.json": "application/json; charset=utf-8",
    "candidates.geojson": "application/geo+json; charset=utf-8",
    "candidates.md": "text/markdown; charset=utf-8",
}
ALLOWED_REQUEST_FIELDS = frozenset(
    {"bbox_bng", "road_rail_upper_threshold_db", "minimum_component_cells"}
)
MAX_REQUEST_BODY_BYTES = 16 * 1024
DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS = 1.0
HTTP_SHUTDOWN_DRAIN_TIMEOUT_SECONDS = 5.0
_JOB_ID_RE = re.compile(r"^[a-f0-9]{32}$")

# These are expected consequences of a client disappearing or of the server
# closing a request socket during shutdown.  Other exceptions remain visible
# through the normal HTTP-server error handling.
_EXPECTED_CLIENT_ERRORS = (ConnectionError, EOFError, socket.timeout, TimeoutError)
_EXPECTED_CLIENT_ERRNOS = frozenset(
    {errno.EBADF, errno.ECONNABORTED, errno.ECONNRESET, errno.EPIPE, errno.ENOTCONN}
)


def _is_expected_client_error(exc: BaseException | None) -> bool:
    if isinstance(exc, _EXPECTED_CLIENT_ERRORS):
        return True
    if not isinstance(exc, OSError):
        return False
    return exc.errno in _EXPECTED_CLIENT_ERRNOS or getattr(exc, "winerror", None) in {
        10038,  # WSAENOTSOCK: request socket was closed during shutdown.
        10053,  # WSAECONNABORTED.
        10054,  # WSAECONNRESET.
        10057,  # WSAENOTCONN.
    }


class ScreeningJobError(RuntimeError):
    """Base class for service and job-registry errors."""


class ScreeningJobRequestError(ScreeningJobError):
    """Raised when a request cannot be normalized into a screening request."""


class ScreeningJobBusyError(ScreeningJobError):
    """Raised when a second job is submitted while one is active."""


class ScreeningJobUnknownError(ScreeningJobError):
    """Raised for an unknown in-memory job ID."""


class ScreeningJobNotReadyError(ScreeningJobError):
    """Raised when output is requested before successful completion."""


class ScreeningJobOutputError(ScreeningJobError):
    """Raised when extraction did not produce a complete, matching output set."""


class ScreeningJobStartupError(ScreeningJobError):
    """Raised when the configured service cannot safely start."""


class _DuplicateJSONKey(ValueError):
    pass


def _duplicate_rejector(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJSONKey(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    raise ValueError(f"non-finite JSON value is not allowed: {value}")


def parse_request_body(raw: bytes) -> dict[str, Any]:
    """Parse one strict JSON request body without accepting duplicate keys."""
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_duplicate_rejector,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKey, ValueError) as exc:
        raise ScreeningJobRequestError(f"request body must be valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ScreeningJobRequestError("request body must be a JSON object")
    return value


def normalize_request(payload: Mapping[str, Any], mask_header: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the client-facing request and return extraction parameters."""
    if not isinstance(payload, Mapping):
        raise ScreeningJobRequestError("request body must be a JSON object")
    unknown = sorted(set(payload) - ALLOWED_REQUEST_FIELDS)
    missing = sorted(ALLOWED_REQUEST_FIELDS - set(payload))
    if unknown:
        raise ScreeningJobRequestError(f"unknown request fields: {unknown}")
    if missing:
        raise ScreeningJobRequestError(f"missing request fields: {missing}")

    bbox = payload["bbox_bng"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ScreeningJobRequestError("bbox_bng must be a JSON array of four finite numbers")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in bbox):
        raise ScreeningJobRequestError("bbox_bng must contain only finite numbers")
    threshold = payload["road_rail_upper_threshold_db"]
    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ScreeningJobRequestError("road_rail_upper_threshold_db must be a finite number")
    minimum = payload["minimum_component_cells"]
    if isinstance(minimum, bool) or not isinstance(minimum, int):
        raise ScreeningJobRequestError("minimum_component_cells must be a positive integer")

    try:
        validated = validate_screening_request(bbox, threshold, minimum, mask_header)
    except CandidateScreeningError as exc:
        raise ScreeningJobRequestError(str(exc)) from exc
    return {
        "bbox_bng": list(validated["bbox_bng"]),
        "road_rail_upper_threshold_db": float(validated["threshold_db"]),
        "minimum_component_cells": int(validated["minimum_component_cells"]),
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_json_load(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_duplicate_rejector,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJSONKey, ValueError) as exc:
        raise ScreeningJobOutputError(f"{label} is not readable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ScreeningJobOutputError(f"{label} must contain a JSON object")
    return value


def _summary_for_status(report: Mapping[str, Any]) -> dict[str, Any]:
    summary = report.get("summary")
    if not isinstance(summary, Mapping):
        raise ScreeningJobOutputError("candidates.json has no readable summary")
    required = (
        "requested_cells",
        "retained_component_count",
        "retained_component_cells",
        "excluded_small_component_cells",
        "empty_result",
    )
    if any(key not in summary for key in required):
        raise ScreeningJobOutputError("candidates.json summary is incomplete")
    return {key: summary[key] for key in required}


def _validate_published_pair(report: Mapping[str, Any], geojson: Mapping[str, Any]) -> None:
    """Check the publication identity contract before marking a job complete."""
    if geojson.get("type") != "FeatureCollection":
        raise ScreeningJobOutputError("candidates.geojson is not a FeatureCollection")
    if geojson.get("run_id") != report.get("run_id"):
        raise ScreeningJobOutputError("published JSON and GeoJSON run IDs do not match")
    if geojson.get("schema_version") != report.get("schema_version"):
        raise ScreeningJobOutputError("published JSON and GeoJSON schema versions do not match")
    policy = report.get("screening_policy")
    if not isinstance(policy, Mapping) or geojson.get("screening_policy_version") != policy.get("version"):
        raise ScreeningJobOutputError("published JSON and GeoJSON screening policies do not match")
    components = report.get("components")
    features = geojson.get("features")
    if not isinstance(components, list) or not isinstance(features, list):
        raise ScreeningJobOutputError("published outputs do not contain component and feature arrays")
    component_ids = [item.get("component_id") if isinstance(item, Mapping) else None for item in components]
    feature_ids = [
        feature.get("properties", {}).get("component_id")
        if isinstance(feature, Mapping) and isinstance(feature.get("properties"), Mapping)
        else None
        for feature in features
    ]
    if any(not isinstance(value, str) or not value for value in component_ids + feature_ids):
        raise ScreeningJobOutputError("published outputs contain malformed component identities")
    if len(set(component_ids)) != len(component_ids) or len(set(feature_ids)) != len(feature_ids):
        raise ScreeningJobOutputError("published outputs contain duplicate component identities")
    if component_ids != feature_ids:
        raise ScreeningJobOutputError("published JSON and GeoJSON component identities do not match")


@dataclass
class ScreeningJobRecord:
    job_id: str
    parameters: dict[str, Any]
    catalogue_build_id: str
    output_dir: Path
    state: str = "running"
    submitted_at: str = field(default_factory=_utc_now)
    started_at: str | None = None
    completed_at: str | None = None
    elapsed_seconds: float | None = None
    screening_run_id: str | None = None
    summary: dict[str, Any] | None = None
    error: dict[str, str] | None = None

    def public(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "job_id": self.job_id,
            "state": self.state,
            "parameters": dict(self.parameters),
            "catalogue_build_id": self.catalogue_build_id,
            "submitted_at": self.submitted_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "elapsed_seconds": self.elapsed_seconds,
        }
        if self.state == "succeeded":
            result.update({
                "screening_run_id": self.screening_run_id,
                "outputs": list(JOB_OUTPUT_FILENAMES),
                "summary": dict(self.summary or {}),
            })
        if self.state == "failed":
            result["error"] = dict(self.error or {"code": "job_failed", "message": "screening job failed"})
        return result


class ScreeningJobManager:
    """Own one configured job root and admit at most one active extraction."""

    def __init__(
        self,
        catalogue_dir: str | Path,
        tile_root: str | Path,
        land_mask_path: str | Path,
        job_root: str | Path,
    ):
        self.catalogue_dir = Path(catalogue_dir).resolve()
        self.tile_root = Path(tile_root).resolve()
        self.land_mask_path = Path(land_mask_path).resolve()
        self.job_root = Path(job_root).resolve()
        self.job_root.mkdir(parents=True, exist_ok=True)
        if not self.job_root.is_dir():
            raise ScreeningJobStartupError(f"configured job root is not a directory: {self.job_root}")
        self._lock: ResourceLock = resource_lock(self.job_root, "screening job root")
        try:
            self._lock.acquire()
        except ResourceLockError as exc:
            raise ScreeningJobStartupError(str(exc)) from exc

        self._state_lock = threading.RLock()
        self._jobs: dict[str, ScreeningJobRecord] = {}
        self._workers: set[threading.Thread] = set()
        self._active_job_id: str | None = None
        self._accepting = True
        self._closed = False
        self._closing = False
        self._close_condition = threading.Condition(self._state_lock)
        self.mask_header: dict[str, Any]
        self.catalogue_build_id: str
        self._marker_path: Path | None = None
        try:
            with DatasetCatalogue(self.catalogue_dir, self.tile_root, self.land_mask_path) as catalogue:
                catalogue._verify_mask()
                self.catalogue_build_id = str(catalogue.metadata["build_id"])
                if catalogue.land_mask_path is None:
                    raise ScreeningJobStartupError("configured catalogue has no authoritative land mask")
                self.mask_header = read_screening_mask_header(catalogue.land_mask_path)
            self._marker_path = self.job_root / f".screening-service-{os.getpid()}-{secrets.token_hex(8)}.json"
            self._marker_path.write_text(
                json.dumps({
                    "pid": os.getpid(),
                    "started_at": _utc_now(),
                    "catalogue_build_id": self.catalogue_build_id,
                }, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception:
            self._lock.release()
            raise

    @property
    def active_job_id(self) -> str | None:
        with self._state_lock:
            return self._active_job_id

    @property
    def accepting_submissions(self) -> bool:
        with self._state_lock:
            return self._accepting and not self._closed

    def health(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "available": not self._closed,
                "accepting_submissions": self._accepting and not self._closed,
                "job_active": self._active_job_id is not None,
            }

    def submit(self, payload: Mapping[str, Any]) -> ScreeningJobRecord:
        parameters = normalize_request(payload, self.mask_header)
        with self._state_lock:
            if self._closed or not self._accepting:
                raise ScreeningJobBusyError("screening service is shutting down")
            if self._active_job_id is not None:
                raise ScreeningJobBusyError("another screening job is already running")
            job_id = self._new_job_id_locked()
            output_dir = self.job_root / job_id
            output_dir.mkdir()
            record = ScreeningJobRecord(
                job_id=job_id,
                parameters=parameters,
                catalogue_build_id=self.catalogue_build_id,
                output_dir=output_dir,
            )
            self._jobs[job_id] = record
            self._active_job_id = job_id
            worker = threading.Thread(
                target=self._run_job,
                args=(record,),
                name=f"quiet-uk-screening-{job_id[:8]}",
                daemon=False,
            )
            self._workers.add(worker)
            try:
                worker.start()
            except Exception:
                self._workers.discard(worker)
                self._jobs.pop(job_id, None)
                self._active_job_id = None
                output_dir.rmdir()
                raise
            return record

    def _new_job_id_locked(self) -> str:
        for _ in range(16):
            candidate = secrets.token_hex(16)
            if candidate not in self._jobs and not (self.job_root / candidate).exists():
                return candidate
        raise ScreeningJobError("could not allocate a unique screening job ID")

    def _run_job(self, record: ScreeningJobRecord) -> None:
        with self._state_lock:
            record.started_at = _utc_now()
        started = time.perf_counter()
        succeeded: tuple[Mapping[str, Any], dict[str, Any]] | None = None
        failure: Exception | None = None
        try:
            report = extract_candidates(
                self.catalogue_dir,
                self.tile_root,
                self.land_mask_path,
                record.parameters["bbox_bng"],
                record.parameters["road_rail_upper_threshold_db"],
                record.parameters["minimum_component_cells"],
                record.output_dir,
            )
            summary = self._verify_completed_outputs(record, report)
            succeeded = (report, summary)
        except Exception as exc:  # worker boundary: preserve failure and release admission
            failure = exc
        finally:
            with self._state_lock:
                if succeeded is not None:
                    report, summary = succeeded
                    record.state = "succeeded"
                    record.screening_run_id = str(report["run_id"])
                    record.summary = summary
                else:
                    assert failure is not None
                    record.state = "failed"
                    record.error = {
                        "code": self._error_code(failure),
                        "message": self._safe_error_message(failure),
                    }
                record.completed_at = _utc_now()
                record.elapsed_seconds = round(time.perf_counter() - started, 6)
                if self._active_job_id == record.job_id:
                    self._active_job_id = None
                self._workers.discard(threading.current_thread())

    def _verify_completed_outputs(
        self,
        record: ScreeningJobRecord,
        returned_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        paths = {name: record.output_dir / name for name in JOB_OUTPUT_FILENAMES}
        if any(not path.is_file() or path.is_symlink() for path in paths.values()):
            raise ScreeningJobOutputError("screening did not publish all required output files")
        report = _safe_json_load(paths["candidates.json"], "candidates.json")
        geojson = _safe_json_load(paths["candidates.geojson"], "candidates.geojson")
        _validate_published_pair(report, geojson)
        if report.get("run_id") != returned_report.get("run_id"):
            raise ScreeningJobOutputError("published JSON run ID disagrees with extraction metadata")
        if report.get("catalogue", {}).get("build_id") != record.catalogue_build_id:
            raise ScreeningJobOutputError("published catalogue build ID disagrees with the configured catalogue")
        parameters = report.get("parameters", {})
        if (
            parameters.get("bbox_bng") != record.parameters["bbox_bng"]
            or float(parameters.get("road_rail_upper_threshold_db")) != record.parameters["road_rail_upper_threshold_db"]
            or parameters.get("minimum_component_cells") != record.parameters["minimum_component_cells"]
        ):
            raise ScreeningJobOutputError("published parameters disagree with the accepted request")
        if not isinstance(report.get("processing"), Mapping):
            raise ScreeningJobOutputError("published JSON has no readable processing metadata")
        try:
            markdown = paths["candidates.md"].read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ScreeningJobOutputError(f"published candidates.md is not readable: {exc}") from exc
        if not markdown.strip():
            raise ScreeningJobOutputError("published candidates.md is empty")
        return _summary_for_status(report)

    def _error_code(self, exc: Exception) -> str:
        if isinstance(exc, ScreeningJobOutputError):
            return "output_verification_failed"
        if isinstance(exc, CatalogueIntegrityError):
            return "catalogue_integrity_error"
        if isinstance(exc, CandidateScreeningError):
            return "screening_error"
        if isinstance(exc, CatalogueError):
            return "catalogue_error"
        if isinstance(exc, FileExistsError):
            return "output_ownership_error"
        return "worker_error"

    def _safe_error_message(self, exc: Exception) -> str:
        message = str(exc) or exc.__class__.__name__
        for path in (self.catalogue_dir, self.tile_root, self.land_mask_path, self.job_root):
            message = message.replace(str(path), "<configured>")
        return message[:600]

    def status(self, job_id: str) -> dict[str, Any]:
        with self._state_lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise ScreeningJobUnknownError("unknown screening job")
            return record.public()

    def output_path(self, job_id: str, filename: str) -> tuple[Path, str]:
        if filename not in JOB_OUTPUT_FILENAMES:
            raise ScreeningJobUnknownError("unknown job output")
        with self._state_lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise ScreeningJobUnknownError("unknown screening job")
            if record.state != "succeeded":
                raise ScreeningJobNotReadyError(
                    "screening outputs are available only after successful completion"
                )
            path = (record.output_dir / filename).resolve()
            if path.parent != record.output_dir.resolve() or not path.is_file() or path.is_symlink():
                raise ScreeningJobOutputError("screening output is no longer available")
            return path, filename

    def begin_shutdown(self) -> None:
        with self._state_lock:
            self._accepting = False

    def close(self) -> None:
        """Stop admission, wait for extraction, then release root ownership."""
        with self._close_condition:
            while self._closing and not self._closed:
                self._close_condition.wait()
            if self._closed:
                return
            self._closing = True
            self._accepting = False
            workers = list(self._workers)

        try:
            for worker in workers:
                if worker is threading.current_thread():
                    raise RuntimeError("ScreeningJobManager.close() cannot run in its worker")
                worker.join()
        except BaseException:
            # Keep the root lock held if waiting was interrupted.  A later
            # close() call may retry the wait and complete cleanup.
            with self._close_condition:
                self._closing = False
                self._close_condition.notify_all()
            raise

        marker = self._marker_path
        cleanup_error: Exception | None = None
        try:
            if marker is not None:
                try:
                    marker.unlink()
                except FileNotFoundError:
                    pass
                except Exception as exc:
                    cleanup_error = exc
        finally:
            try:
                self._lock.release()
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            finally:
                with self._close_condition:
                    self._marker_path = None
                    self._closed = True
                    self._closing = False
                    self._close_condition.notify_all()
        if cleanup_error is not None:
            raise cleanup_error

    def __enter__(self) -> "ScreeningJobManager":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


class ScreeningAPIHandler(BaseHTTPRequestHandler):
    """Strict, allow-listed HTTP handler for the local screening service."""

    protocol_version = "HTTP/1.1"
    server_version = "QuietUKScreeningJobs/1.0"

    @property
    def manager(self) -> ScreeningJobManager:
        return self.server.manager

    def _allowed_host(self) -> bool:
        host = self.headers.get("Host")
        if not host:
            return False
        port = int(self.server.server_address[1])
        return host.strip().lower() in {f"127.0.0.1:{port}", f"localhost:{port}"}

    def _allowed_origin(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = int(self.server.server_address[1])
        return origin.strip().lower() in {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    def _send_json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = _json_bytes(payload)
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send_json(status, {"error": {"code": code, "message": message}})

    def _check_request_metadata(self, *, post: bool = False) -> bool:
        if not self._allowed_host():
            self._error(400, "invalid_host", "Host must identify this loopback service")
            return False
        if post and not self._allowed_origin():
            self._error(403, "invalid_origin", "Origin must identify this loopback service")
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        if not self._check_request_metadata():
            return
        path = urlsplit(self.path).path
        if path == "/api/health":
            self._send_json(200, self.manager.health())
            return
        status_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)", path)
        if status_match:
            try:
                self._send_json(200, self.manager.status(status_match.group(1)))
            except ScreeningJobUnknownError as exc:
                self._error(404, "job_not_found", str(exc))
            return
        output_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)/outputs/([^/]+)", path)
        if output_match:
            job_id, filename = output_match.groups()
            try:
                output_path, output_name = self.manager.output_path(job_id, filename)
            except ScreeningJobUnknownError as exc:
                self._error(404, "output_not_found", str(exc))
            except ScreeningJobNotReadyError as exc:
                self._error(409, "job_not_succeeded", str(exc))
            except ScreeningJobOutputError as exc:
                self._error(409, "output_unavailable", str(exc))
            else:
                payload = output_path.read_bytes()
                self.close_connection = True
                self.send_response(200)
                self.send_header("Content-Type", JOB_OUTPUT_CONTENT_TYPES[output_name])
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
            return
        self._error(404, "not_found", "endpoint not found")

    def do_POST(self) -> None:  # noqa: N802
        if not self._check_request_metadata(post=True):
            return
        if urlsplit(self.path).path != "/api/jobs":
            self._error(404, "not_found", "endpoint not found")
            return
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            self._error(415, "unsupported_media_type", "Content-Type must be application/json")
            return
        try:
            content_length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            content_length = -1
        if content_length < 0:
            self._error(400, "invalid_request", "Content-Length is required")
            return
        if content_length > MAX_REQUEST_BODY_BYTES:
            self._error(413, "request_too_large", "request body exceeds the 16 KiB limit")
            return
        try:
            raw = self.rfile.read(content_length)
        except socket.timeout:
            try:
                self._error(408, "request_timeout", "request body was not received before the read timeout")
            except OSError as exc:
                if not _is_expected_client_error(exc):
                    raise
                self.close_connection = True
            return
        except OSError as exc:
            if not _is_expected_client_error(exc):
                raise
            self.close_connection = True
            return
        if len(raw) != content_length:
            self._error(400, "invalid_request", "request body is incomplete")
            return
        try:
            payload = parse_request_body(raw)
            record = self.manager.submit(payload)
        except ScreeningJobRequestError as exc:
            self._error(400, "invalid_request", str(exc))
            return
        except ScreeningJobBusyError as exc:
            self._error(409, "job_active", str(exc))
            return
        except ScreeningJobError as exc:
            self._error(500, "job_admission_failed", str(exc))
            return
        status_url = f"/api/jobs/{record.job_id}"
        self._send_json(202, {"job_id": record.job_id, "state": "running", "status_url": status_url})

    def do_PUT(self) -> None:  # noqa: N802
        if self._check_request_metadata():
            self._error(405, "method_not_allowed", "method not allowed")

    do_PATCH = do_PUT
    do_DELETE = do_PUT

    def log_message(self, format: str, *args: Any) -> None:
        return

    def handle(self) -> None:
        try:
            super().handle()
        except _EXPECTED_CLIENT_ERRORS:
            self.close_connection = True
        except OSError as exc:
            if not _is_expected_client_error(exc):
                raise
            self.close_connection = True

    def finish(self) -> None:
        try:
            super().finish()
        except _EXPECTED_CLIENT_ERRORS:
            self.close_connection = True
        except OSError as exc:
            if not _is_expected_client_error(exc):
                raise
            self.close_connection = True

    def handle_error(self, request: Any, client_address: Any) -> None:
        if _is_expected_client_error(sys.exc_info()[1]):
            return
        super().handle_error(request, client_address)


class ScreeningAPIServer(ThreadingHTTPServer):
    """Threaded local server with an explicitly injected job manager."""

    allow_reuse_address = True
    # Socket tracking and explicit close/join below are the primary shutdown
    # mechanism.  Daemon request threads are only a final safety net for an
    # unexpected handler that remains stuck after its socket is closed.
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        manager: ScreeningJobManager,
        *,
        request_timeout_seconds: float = DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS,
    ):
        try:
            timeout = float(request_timeout_seconds)
        except (OverflowError, TypeError, ValueError) as exc:
            raise ValueError("request_timeout_seconds must be a positive finite number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("request_timeout_seconds must be a positive finite number")
        self.request_timeout_seconds = timeout
        self._connection_lock = threading.RLock()
        self._connections: set[socket.socket] = set()
        self._request_threads: set[threading.Thread] = set()
        self._closing = False
        self._socket_closed = False
        super().__init__(address, ScreeningAPIHandler)
        self.manager = manager

    def get_request(self):
        request, client_address = super().get_request()
        try:
            request.settimeout(self.request_timeout_seconds)
            with self._connection_lock:
                if self._closing:
                    raise OSError("screening HTTP server is shutting down")
                self._connections.add(request)
        except Exception:
            try:
                request.close()
            except OSError:
                pass
            raise
        return request, client_address

    def close_request(self, request) -> None:
        try:
            super().close_request(request)
        finally:
            with self._connection_lock:
                self._connections.discard(request)

    def process_request(self, request, client_address) -> None:
        with self._connection_lock:
            if self._closing:
                should_close = True
            else:
                should_close = False
                thread = threading.Thread(
                    target=self._process_request_thread,
                    args=(request, client_address),
                    name="quiet-uk-screening-http",
                    daemon=self.daemon_threads,
                )
                self._request_threads.add(thread)
        if should_close:
            self.shutdown_request(request)
            return
        try:
            thread.start()
        except BaseException:
            with self._connection_lock:
                self._request_threads.discard(thread)
            self.shutdown_request(request)
            raise

    def _process_request_thread(self, request, client_address) -> None:
        try:
            self.process_request_thread(request, client_address)
        finally:
            with self._connection_lock:
                self._request_threads.discard(threading.current_thread())

    def shutdown(self) -> None:
        """Stop admission and the accept loop; server_close drains connections."""
        self.manager.begin_shutdown()
        with self._connection_lock:
            self._closing = True
        super().shutdown()

    def _close_active_connections(self) -> None:
        with self._connection_lock:
            connections = list(self._connections)
        for request in connections:
            try:
                request.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
            try:
                request.close()
            except (OSError, ValueError):
                pass

    def _join_request_threads(self) -> None:
        deadline = time.monotonic() + HTTP_SHUTDOWN_DRAIN_TIMEOUT_SECONDS
        while True:
            with self._connection_lock:
                threads = [
                    thread
                    for thread in self._request_threads
                    if thread is not threading.current_thread() and thread.is_alive()
                ]
            if not threads:
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            for thread in threads:
                thread.join(min(remaining, 0.25))
            self._close_active_connections()

    def server_close(self) -> None:
        self.manager.begin_shutdown()
        with self._connection_lock:
            self._closing = True
            socket_closed = self._socket_closed
            self._socket_closed = True
        try:
            if not socket_closed:
                # shutdown() has already stopped the accept loop on the normal
                # lifecycle path.  Call HTTPServer directly to avoid the
                # ThreadingMixIn unbounded join implementation.
                HTTPServer.server_close(self)
        finally:
            self._close_active_connections()
            self._join_request_threads()


__all__ = [
    "ALLOWED_REQUEST_FIELDS",
    "DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS",
    "JOB_OUTPUT_FILENAMES",
    "MAX_REQUEST_BODY_BYTES",
    "ScreeningAPIHandler",
    "ScreeningAPIServer",
    "ScreeningJobBusyError",
    "ScreeningJobError",
    "ScreeningJobManager",
    "ScreeningJobNotReadyError",
    "ScreeningJobOutputError",
    "ScreeningJobRecord",
    "ScreeningJobRequestError",
    "ScreeningJobStartupError",
    "ScreeningJobUnknownError",
    "normalize_request",
    "parse_request_body",
]
