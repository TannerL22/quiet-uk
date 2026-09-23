import threading
import time
from pathlib import Path

import pytest

import quiet_uk.screening_jobs as screening_jobs
from quiet_uk.screening_jobs import (
    ScreeningJobBusyError,
    ScreeningJobManager,
    ScreeningJobNotReadyError,
    ScreeningJobStartupError,
)

from test_catalogue import _build


BBOX = [503000.0, 171000.0, 503800.0, 171200.0]


def _request(*, minimum=5, threshold=61.0):
    return {
        "bbox_bng": list(BBOX),
        "road_rail_upper_threshold_db": threshold,
        "minimum_component_cells": minimum,
    }


def _manager(tmp_path):
    paths, catalogue = _build(tmp_path / "fixture")
    manager = ScreeningJobManager(
        catalogue["catalogue_dir"],
        paths[0],
        paths[3],
        tmp_path / "jobs",
    )
    return paths, catalogue, manager


def _wait_for_state(manager, job_id, state, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = manager.status(job_id)
        if status["state"] == state:
            return status
        time.sleep(0.01)
    raise AssertionError(f"job did not reach {state}: {manager.status(job_id)}")


def test_manager_admits_one_job_and_publishes_owned_outputs(tmp_path):
    _, catalogue, manager = _manager(tmp_path)
    try:
        record = manager.submit(_request())
        assert record.job_id
        assert record.output_dir == Path(tmp_path / "jobs" / record.job_id).resolve()
        status = _wait_for_state(manager, record.job_id, "succeeded")
        assert status["catalogue_build_id"] == catalogue["metadata_payload"]["build_id"]
        assert status["screening_run_id"].startswith("screen-")
        assert status["outputs"] == ["candidates.json", "candidates.geojson", "candidates.md"]
        assert status["summary"]["retained_component_count"] == 1
        assert {path.name for path in record.output_dir.iterdir()} == {
            "candidates.json", "candidates.geojson", "candidates.md"
        }
    finally:
        manager.close()


def test_manager_busy_admission_and_status_remain_responsive(tmp_path, monkeypatch):
    _, _, manager = _manager(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    try:
        first = manager.submit(_request())
        assert started.wait(5)
        assert manager.status(first.job_id)["state"] == "running"
        with pytest.raises(ScreeningJobBusyError, match="already running"):
            manager.submit(_request())
        release.set()
        assert _wait_for_state(manager, first.job_id, "succeeded")["state"] == "succeeded"
    finally:
        release.set()
        manager.close()


def test_failed_job_releases_slot_and_next_job_succeeds(tmp_path, monkeypatch):
    _, _, manager = _manager(tmp_path)
    calls = {"count": 0}
    original = screening_jobs.extract_candidates

    def fail_once(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("controlled extraction failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", fail_once)
    try:
        failed = manager.submit(_request())
        failed_status = _wait_for_state(manager, failed.job_id, "failed")
        assert failed_status["error"]["code"] == "worker_error"
        assert "controlled extraction failure" in failed_status["error"]["message"]
        second = manager.submit(_request())
        assert _wait_for_state(manager, second.job_id, "succeeded")["state"] == "succeeded"
    finally:
        manager.close()


def test_empty_screening_is_successful_and_distinct_from_failure(tmp_path):
    _, _, manager = _manager(tmp_path)
    try:
        record = manager.submit(_request(threshold=0.0, minimum=1))
        status = _wait_for_state(manager, record.job_id, "succeeded")
        assert status["summary"]["empty_result"] is True
        assert status["summary"]["retained_component_count"] == 0
        assert status.get("error") is None
    finally:
        manager.close()


def test_second_manager_cannot_own_same_job_root_and_close_releases_lock(tmp_path):
    paths, catalogue, first = _manager(tmp_path)
    keep = Path(tmp_path / "jobs" / "unrelated.txt")
    keep.write_text("preserve me\n", encoding="utf-8")
    second = None
    with pytest.raises(ScreeningJobStartupError, match="already locked"):
        ScreeningJobManager(
            catalogue["catalogue_dir"],
            paths[0],
            paths[3],
            tmp_path / "jobs",
        )
    try:
        first.close()
        first.close()
        second = ScreeningJobManager(
            catalogue["catalogue_dir"], paths[0], paths[3], tmp_path / "jobs"
        )
        assert keep.read_text(encoding="utf-8") == "preserve me\n"
        second.close()
        second.close()
    finally:
        first.close()
        if second is not None:
            second.close()


def test_shutdown_waits_for_active_job_and_removes_only_own_marker(tmp_path, monkeypatch):
    _, _, manager = _manager(tmp_path)
    started = threading.Event()
    release = threading.Event()
    original = screening_jobs.extract_candidates

    def blocked_extract(*args, **kwargs):
        started.set()
        assert release.wait(10)
        return original(*args, **kwargs)

    monkeypatch.setattr(screening_jobs, "extract_candidates", blocked_extract)
    close_thread = None
    try:
        record = manager.submit(_request())
        assert started.wait(5)
        manager.begin_shutdown()
        with pytest.raises(ScreeningJobBusyError, match="shutting down"):
            manager.submit(_request())
        close_thread = threading.Thread(target=manager.close)
        close_thread.start()
        assert close_thread.is_alive()
        release.set()
        close_thread.join(10)
        assert not close_thread.is_alive()
        manager.close()
        assert not any(path.name.startswith(".screening-service-") for path in (tmp_path / "jobs").iterdir())
        assert (tmp_path / "jobs" / record.job_id).is_dir()
    finally:
        release.set()
        if close_thread is not None:
            close_thread.join(10)
        manager.close()


def test_output_access_is_only_available_after_success(tmp_path):
    _, _, manager = _manager(tmp_path)
    try:
        record = manager.submit(_request())
        while manager.status(record.job_id)["state"] == "running":
            with pytest.raises(ScreeningJobNotReadyError):
                manager.output_path(record.job_id, "candidates.json")
            time.sleep(0.01)
        path, filename = manager.output_path(record.job_id, "candidates.json")
        assert filename == "candidates.json"
        assert path.is_file()
    finally:
        manager.close()
