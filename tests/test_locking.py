import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from quiet_uk.locking import (
    ResourceLockError,
    acquire_resource_locks,
    lock_file_path,
    resource_lock,
)


_CHILD_LOCK_SCRIPT = r'''
import sys
from quiet_uk.locking import acquire_resource_locks

try:
    with acquire_resource_locks([("output root", sys.argv[1]), ("manifest", sys.argv[2])]):
        print("READY", flush=True)
        sys.stdin.readline()
except Exception as exc:
    print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
'''


_CHILD_BATCH_SCRIPT = r'''
import json
import sys
from pathlib import Path

from quiet_uk import runner
from quiet_uk.tiling import Tile

config = json.loads(sys.argv[4])
tile = Tile("r0000c0000", 0, 0, (0.0, 0.0, 1000.0, 1000.0), 10, 100)
runner.plan_england_tiles = lambda config, mask_path: [tile]

def blocked_process(tile, config, output_path, temp_root=None):
    print("PROCESSING", flush=True)
    sys.stdin.readline()
    raise RuntimeError("controlled lock integration failure")

runner.process_tile = blocked_process
try:
    result = runner.run_batch(
        config, Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    )
    print("DONE", json.dumps(result), flush=True)
except Exception as exc:
    print(f"ERROR {type(exc).__name__}: {exc}", flush=True)
'''


def _child_environment():
    project_src = str(Path(__file__).resolve().parents[1] / "src")
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = project_src if not existing else project_src + os.pathsep + existing
    return environment


def _start_lock_child(root, manifest):
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [sys.executable, "-c", _CHILD_LOCK_SCRIPT, str(root), str(manifest)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=no_window,
        env=_child_environment(),
    )


def _line_reader(process):
    lines = queue.Queue()

    def read():
        for line in process.stdout:
            lines.put(line.rstrip())

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    return lines


def _wait_for_line(lines, timeout=10):
    try:
        return lines.get(timeout=timeout)
    except queue.Empty as exc:
        raise AssertionError("child process did not emit a bounded synchronization line") from exc


def _wait_for_prefix(lines, prefix, timeout=10):
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssertionError(f"child process did not emit a line beginning with {prefix!r}")
        line = _wait_for_line(lines, remaining)
        if line.startswith(prefix):
            return line


def _finish_child(process, send_input=True):
    try:
        if send_input and process.stdin is not None:
            process.stdin.write("continue\n")
            process.stdin.flush()
        process.wait(timeout=10)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _write_mask(path):
    profile = {
        "driver": "GTiff", "height": 10, "width": 10, "count": 1,
        "dtype": "uint8", "crs": "EPSG:27700",
        "transform": Affine(100, 0, 0, 0, -100, 1000), "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(np.ones((1, 10, 10), dtype="uint8"))


def _batch_config():
    return {
        "crs": "EPSG:27700", "metric": "Lden", "pilot_resolution_m": 10,
        "output_resolution_m": 100, "tile_size_m": 1000,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs": {name: f"https://example.test/{name}" for name in ("road", "rail", "airport")},
        "coverage_ids": {name: f"{name}_Lden" for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
        "runner": {"max_workers": 1, "min_tile_start_interval_s": 0,
                    "max_attempts": 1, "retry_base_backoff_s": 0,
                    "retry_max_backoff_s": 0},
    }


def _start_batch_child(root, manifest, mask, config):
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [
            sys.executable, "-c", _CHILD_BATCH_SCRIPT,
            str(root), str(manifest), str(mask), json.dumps(config),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        creationflags=no_window,
        env=_child_environment(),
    )


def test_same_root_different_manifests_are_exclusive_across_processes(tmp_path):
    root = tmp_path / "root"
    first = _start_lock_child(root, tmp_path / "first.json")
    first_lines = _line_reader(first)
    second = None
    try:
        assert _wait_for_line(first_lines) == "READY"
        second = _start_lock_child(root, tmp_path / "second.json")
        second_lines = _line_reader(second)
        assert "already locked" in _wait_for_line(second_lines).lower()
    finally:
        if second is not None:
            _finish_child(second, send_input=False)
        _finish_child(first)


def test_different_roots_same_manifest_are_exclusive_across_processes(tmp_path):
    manifest = tmp_path / "shared.json"
    first = _start_lock_child(tmp_path / "first-root", manifest)
    first_lines = _line_reader(first)
    second = None
    try:
        assert _wait_for_line(first_lines) == "READY"
        second = _start_lock_child(tmp_path / "second-root", manifest)
        second_lines = _line_reader(second)
        assert "already locked" in _wait_for_line(second_lines).lower()
    finally:
        if second is not None:
            _finish_child(second, send_input=False)
        _finish_child(first)


def test_equivalent_resource_aliases_contend_across_processes(tmp_path):
    root = tmp_path / "root"
    alias = tmp_path / "nested" / ".." / "root"
    first = _start_lock_child(root, tmp_path / "first.json")
    first_lines = _line_reader(first)
    second = None
    try:
        assert _wait_for_line(first_lines) == "READY"
        second = _start_lock_child(alias, tmp_path / "second.json")
        second_lines = _line_reader(second)
        assert "already locked" in _wait_for_line(second_lines).lower()
    finally:
        if second is not None:
            _finish_child(second, send_input=False)
        _finish_child(first)


def test_distinct_roots_and_manifests_can_be_held_simultaneously(tmp_path):
    first = _start_lock_child(tmp_path / "first-root", tmp_path / "first.json")
    second = None
    first_lines = _line_reader(first)
    try:
        assert _wait_for_line(first_lines) == "READY"
        second = _start_lock_child(tmp_path / "second-root", tmp_path / "second.json")
        second_lines = _line_reader(second)
        assert _wait_for_line(second_lines) == "READY"
    finally:
        if second is not None:
            _finish_child(second)
        _finish_child(first)


def test_same_resource_cannot_be_claimed_twice_in_one_process(tmp_path):
    with resource_lock(tmp_path / "root", "output root"):
        with pytest.raises(ResourceLockError, match="already locked"):
            with resource_lock(tmp_path / "root", "output root"):
                pass


def test_second_resource_failure_releases_first(tmp_path):
    first_path = tmp_path / "a-first"
    blocked = tmp_path / "z-blocked"
    holder = _start_lock_child(blocked, tmp_path / "holder.json")
    holder_lines = _line_reader(holder)
    try:
        assert _wait_for_line(holder_lines) == "READY"
        with pytest.raises(ResourceLockError, match="already locked"):
            with acquire_resource_locks([
                ("first", first_path),
                ("blocked", blocked),
            ]):
                pass
        with resource_lock(first_path, "first"):
            pass
    finally:
        _finish_child(holder)


def test_normal_and_exception_release_locks(tmp_path):
    path = tmp_path / "resource"
    with resource_lock(path):
        pass
    with resource_lock(path):
        pass
    with pytest.raises(RuntimeError):
        with resource_lock(path):
            raise RuntimeError("controlled")
    with resource_lock(path):
        pass


def test_forced_child_termination_releases_os_lock(tmp_path):
    first = _start_lock_child(tmp_path / "root", tmp_path / "manifest.json")
    first_lines = _line_reader(first)
    second = None
    try:
        assert _wait_for_line(first_lines) == "READY"
        first.kill()
        first.wait(timeout=10)
        second = _start_lock_child(tmp_path / "root", tmp_path / "manifest.json")
        second_lines = _line_reader(second)
        assert _wait_for_line(second_lines) == "READY"
    finally:
        if second is not None:
            _finish_child(second)
        if first.poll() is None:
            _finish_child(first, send_input=False)


def test_existing_lock_file_without_active_lock_does_not_block(tmp_path):
    path = tmp_path / "resource"
    lock_path = lock_file_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_bytes(b"leftover")
    with resource_lock(path):
        pass


def test_run_batch_lock_contention_rejects_before_processor_or_second_manifest(tmp_path):
    mask = tmp_path / "mask.tif"
    _write_mask(mask)
    config = _batch_config()
    root = tmp_path / "root"
    first_manifest = tmp_path / "first.json"
    second_manifest = tmp_path / "second.json"
    first = _start_batch_child(root, first_manifest, mask, config)
    first_lines = _line_reader(first)
    second = None
    try:
        assert _wait_for_prefix(first_lines, "PROCESSING") == "PROCESSING"
        second = _start_batch_child(root, second_manifest, mask, config)
        second_lines = _line_reader(second)
        error = _wait_for_prefix(second_lines, "ERROR")
        assert "locked" in error.lower()
        assert not second_manifest.exists()
    finally:
        if second is not None:
            _finish_child(second, send_input=False)
        _finish_child(first)
