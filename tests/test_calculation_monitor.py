"""Execution-control checks only; no new acoustic scenarios or model changes."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

PATH = Path(__file__).resolve().parents[1] / "scripts/calculation_monitor.py"
spec = importlib.util.spec_from_file_location("calculation_monitor", PATH)
monitor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = monitor
spec.loader.exec_module(monitor)


def test_pause_resume_stop_between_points(tmp_path):
    gate = monitor.WorkerGate(tmp_path)
    monitor.write_json(tmp_path / "command.json", {"action": "pause"})
    returned = threading.Event()
    thread = threading.Thread(target=lambda: (gate.check(), returned.set()), daemon=True)
    thread.start()
    time.sleep(.15)
    assert not returned.is_set()
    monitor.write_json(tmp_path / "command.json", {"action": "run"})
    thread.join(2)
    assert returned.is_set()
    gate.last_check = 0
    monitor.write_json(tmp_path / "command.json", {"action": "stop"})
    with pytest.raises(monitor.StopRequested):
        gate.check()


def test_stop_overrides_memory_pause(tmp_path):
    monitor.write_json(tmp_path / "command.json", {"action": "stop"})
    monitor.write_json(tmp_path / "memory_pause.json", {})
    with pytest.raises(monitor.StopRequested):
        monitor.WorkerGate(tmp_path).check()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop lock")
def test_lock_excludes_second_process_and_recovers(tmp_path):
    lock = monitor.ServiceLock(tmp_path / "service.lock")
    assert lock.acquire()
    code = "import importlib.util,sys; s=importlib.util.spec_from_file_location('m',sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); lock=m.ServiceLock(sys.argv[2]); print(lock.acquire()); lock.release()"
    try:
        result = subprocess.run([sys.executable, "-c", code, str(PATH), str(lock.path)], capture_output=True, text=True, check=True)
        assert result.stdout.strip() == "False"
    finally:
        lock.release()
    assert lock.acquire()
    lock.release()


def test_stale_running_label_is_not_live(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "CONTROL", tmp_path)
    monkeypatch.setattr(monitor, "OUTPUT", tmp_path / "run")
    monkeypatch.setattr(monitor, "service_alive", lambda: False)
    monitor.write_json(tmp_path / "state.json", {"status": "running", "completed_roads": 14})
    assert monitor.snapshot()["status"] == "stopped"
    assert monitor.snapshot()["completed_roads"] == 14


def test_clean_stop_can_restart_immediately(tmp_path, monkeypatch):
    control, output = tmp_path / "control", tmp_path / "run"
    monkeypatch.setattr(monitor, "CONTROL", control)
    monkeypatch.setattr(monitor, "OUTPUT", output)
    monkeypatch.setattr(monitor, "service_alive", lambda: False)
    calls = []
    monkeypatch.setattr(monitor.subprocess, "Popen", lambda *a, **kw: calls.append(kw))
    monitor.write_json(output / "progress.json", {"status": "running"})
    monitor.write_json(control / "state.json", {"status": "stopped"})
    monitor.launch(4)
    assert len(calls) == 1
    assert json.loads((control / "command.json").read_text())["workers"] == 4


def test_refuses_recent_unmanaged_calculation(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "CONTROL", tmp_path / "control")
    monkeypatch.setattr(monitor, "OUTPUT", tmp_path / "run")
    monkeypatch.setattr(monitor, "service_alive", lambda: False)
    monitor.write_json(monitor.OUTPUT / "progress.json", {"status": "running"})
    with pytest.raises(ValueError, match="Recent calculation"):
        monitor.launch(4)


def test_worker_limit_uses_available_cpus(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor, "CONTROL", tmp_path)
    monkeypatch.setattr(monitor.os, "cpu_count", lambda: 12)
    assert monitor.worker_limit() == 12
    monitor.command("run", 12)
    assert monitor.read_json(tmp_path / "command.json")["workers"] == 12
    with pytest.raises(ValueError, match="between 1 and 12"):
        monitor.command("run", 13)


def test_worker_limit_handles_unknown_cpu_count(monkeypatch):
    monkeypatch.setattr(monitor.os, "cpu_count", lambda: None)
    assert monitor.worker_limit() == 1
