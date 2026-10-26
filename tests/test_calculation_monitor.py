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


def test_shed_and_road_cancel_markers_stop_only_their_targets(tmp_path):
    monitor.write_json(tmp_path / "command.json", {"action": "run"})
    gate = monitor.WorkerGate(tmp_path)
    gate.link, gate.kind, gate.position = "road-a", "road", (0, 9)
    monitor.write_json(tmp_path / "cancel" / "road-a.json", {"position": [0, 5]})
    gate.check()  # A cancel marker only stops point chunks, never the engine road call.
    gate.kind, gate.position, gate.last_check = "points", (0, 4), 0
    gate.check()  # Points before the hold are still needed by the engine.
    gate.position, gate.last_check = (0, 6), 0
    with pytest.raises(monitor.StopRequested):
        gate.check()
    gate.link, gate.kind, gate.last_check = "road-b", "road", 0
    monitor.write_json(tmp_path / "shed" / f"{monitor.os.getpid()}.json", {})
    with pytest.raises(monitor.StopRequested):
        gate.check()


def fake_run(tmp_path, monkeypatch, lengths):
    """A tiny output directory with straight roads and cached trivial partitions."""
    import gzip
    import math
    from types import SimpleNamespace
    from quiet_uk.adaptive_road_integration import DEFAULTS
    control, output = tmp_path / "control", tmp_path / "run"
    monkeypatch.setattr(monitor, "CONTROL", control)
    (output / "roads").mkdir(parents=True)
    (output / "source_partitions").mkdir()
    sources, rows = [], []
    for index, length in enumerate(lengths):
        link = f"road-{index}"
        cell = dict(segment_id=link + ":0", start_xy_bng=[0., float(index)], end_xy_bng=[float(length), float(index)], length_m=float(length))
        sources.append(dict(link_id=link, source_cells=[cell], prepared_length_m=float(length)))
        partition = dict(cells={cell["segment_id"]: [dict(fraction=0.), dict(fraction=1.)]})
        (output / "source_partitions" / f"{link}.json.gz").write_bytes(gzip.compress(json.dumps(
            dict(link_id=link, fingerprint="run", partition=partition)).encode()))
        count = 5 * math.ceil(length / DEFAULTS["maximum_panel_m"])
        rows.append(dict(link_id=link, minimum_primary_observations=count, minimum_primary_plus_finer_observations=count * 9 // 5))
    sources.append(dict(link_id="held-input", source_cells=[], prepared_length_m=1.))
    monitor.write_json(output / "source_lines.json", {"source_roads": sources})
    monitor.write_json(output / "workload_plan.json", {"fingerprint": "run", "roads": rows})
    engine = SimpleNamespace(read=lambda p: json.loads(Path(p).read_bytes()), DEFAULTS=DEFAULTS,
                             minimum_initial=lambda s, p: 5 * sum(math.ceil(c["length_m"] / DEFAULTS["maximum_panel_m"]) for c in s["source_cells"]))
    monkeypatch.setattr(monitor, "PREFETCH_MIN_POINTS", 100)
    monkeypatch.setattr(monitor, "CHUNK_POINTS", 10)
    return monitor.Scheduler(engine, output, {"fingerprint": "run"})


def test_scheduler_orders_instant_holds_then_longest(tmp_path, monkeypatch):
    scheduler = fake_run(tmp_path, monkeypatch, [20., 400., 80.])
    assert list(scheduler.queue) == ["held-input", "road-1", "road-2", "road-0"]
    assert scheduler.next_task(8., False)[0] == ("road", scheduler.sources["held-input"])
    kind, task = scheduler.next_task(8., False)[0]
    assert kind == "points" and task["link_id"] == "road-1" and len(task["points"]) == 10


def test_scheduler_hold_cancels_chunks_then_runs_engine_road(tmp_path, monkeypatch):
    scheduler = fake_run(tmp_path, monkeypatch, [400.])
    scheduler.queue.remove("held-input")
    first = scheduler.next_task(8., False)[0][1]
    second = scheduler.next_task(8., False)[0][1]
    scheduler.finished_points(first, {"computed": 4, "held_index": 3, "finished": True, "seconds": 1.})
    assert (monitor.CONTROL / "cancel" / "road-0.json").exists()
    assert scheduler.next_task(8., False) == (None, None)  # in-flight chunk still saving
    scheduler.finished_points(second, {"computed": 2, "held_index": None, "finished": False, "seconds": 1.})
    assert not (monitor.CONTROL / "cancel" / "road-0.json").exists()
    assert scheduler.next_task(8., False)[0] == ("road", scheduler.sources["road-0"])
    monitor.write_json(scheduler.road_directory("road-0") / "result.json", {"new_native_points": 1})
    scheduler.finished_road("road-0", {"finished": True}, False)
    assert scheduler.idle() and "road-0" in scheduler.done


def test_scheduler_requeues_unfinished_chunk_remainder_and_runs_finer_pass(tmp_path, monkeypatch):
    scheduler = fake_run(tmp_path, monkeypatch, [80.])
    scheduler.queue.remove("held-input")
    tasks = [scheduler.next_task(8., False)[0][1] for _ in range(10)]
    assert scheduler.next_task(8., False) == (None, None)
    scheduler.finished_points(tasks[0], {"computed": 3, "held_index": None, "finished": False, "seconds": 1.})
    retry = scheduler.next_task(8., False)[0][1]
    assert retry["points"] == tasks[0]["points"][3:]
    for task in [retry, *tasks[1:]]:
        scheduler.finished_points(task, {"computed": len(task["points"]), "held_index": None, "finished": True, "seconds": 1.})
    job = scheduler.jobs["road-0"]
    assert job.phase == "fine" and sum(len(points) for _, points in job.pending) == 80


def test_large_final_road_waits_for_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor.plan, "OBSERVATION_BYTES", 2**30 // 100)
    scheduler = fake_run(tmp_path, monkeypatch, [400.])
    scheduler.queue.clear()
    job = monitor.RoadJob("road-0", [], [], set())
    job.phase = "final"
    scheduler.jobs["road-0"] = job
    task, need = scheduler.next_task(2., False)
    assert task is None and need > 1.
    assert scheduler.next_task(2., True)[0][0] == "road"


def test_repeated_road_failures_set_road_aside(tmp_path, monkeypatch):
    scheduler = fake_run(tmp_path, monkeypatch, [20.])
    scheduler.queue.clear()
    for attempt in range(monitor.MAX_ROAD_FAILURES):
        scheduler.failure("road", {"link_id": "road-0"}, "Traceback\nAssertionError: boom")
    assert scheduler.failed == {"road-0"}
    assert list(scheduler.queue) == ["road-0"] * (monitor.MAX_ROAD_FAILURES - 1)
    assert monitor.read_json(monitor.CONTROL / "failures.json")["roads"]["road-0"]["count"] == monitor.MAX_ROAD_FAILURES


def test_hold_keeps_earlier_chunks_and_promotes_earliest_witness(tmp_path, monkeypatch):
    scheduler = fake_run(tmp_path, monkeypatch, [400.])
    scheduler.queue.remove("held-input")
    first, second, third = [scheduler.next_task(8., False)[0][1] for _ in range(3)]
    staging = scheduler.staging / "road-0"
    staging.mkdir(parents=True)
    for name in (monitor.plan.witness_name("base", 23), monitor.plan.witness_name("base", 14)):
        (staging / name).write_text(name)
    scheduler.finished_points(third, {"computed": 4, "held_index": 3, "finished": True, "seconds": 1.})
    assert monitor.read_json(monitor.CONTROL / "cancel" / "road-0.json")["position"] == [0, 23]
    assert [c[0] for c in scheduler.jobs["road-0"].pending] == []  # only later chunks were pending
    scheduler.finished_points(second, {"computed": 5, "held_index": 4, "finished": True, "seconds": 1.})
    assert monitor.read_json(monitor.CONTROL / "cancel" / "road-0.json")["position"] == [0, 14]
    scheduler.finished_points(first, {"computed": 10, "held_index": None, "finished": True, "seconds": 1.})
    assert scheduler.jobs["road-0"].phase == "final"
    witness = scheduler.road_directory("road-0") / "first_held_profile.json.gz"
    assert witness.read_text() == monitor.plan.witness_name("base", 14)
