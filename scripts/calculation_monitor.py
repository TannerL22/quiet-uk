"""Desktop controls and a detached scheduler for the unchanged, pinned road model.

The scheduler changes execution only. Existing protocol/code/runtime validation,
road calculations, exact-coordinate journals and final sealing stay in script 119.
Long roads first have their engine-determined coordinates calculated in parallel
chunks by the engine's own point functions; the engine then reuses those journal
records and completes the road unchanged. Control files live outside the run
generation, so controls also work after sealing.
"""
from __future__ import annotations

import argparse
from collections import deque
import ctypes
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import multiprocessing
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk import calculation_scheduling as plan  # noqa: E402

OUTPUT = ROOT / "artifacts/real_road_reflection_calculation_v5"
CONTROL = ROOT / "artifacts/local_calculation_control_v5"
ENGINE_PATH = ROOT / "scripts/119_calculate_real_road_reflections.py"
ENGINE = None
TOTAL_ROADS = 722

# Roads with at least this many planned observations are split into chunks.
PREFETCH_MIN_POINTS = 400
CHUNK_POINTS = 48
# Memory policy (GiB). Pausing never frees memory, so low memory retires a
# worker instead; its road or chunk is saved and requeued.
SHED_BELOW_GIB = .6
RESTORE_ABOVE_GIB = 2.
SPAWN_ABOVE_GIB = 1.25
RESERVE_GIB = .75
RECYCLE_ABOVE_GIB = 1.5
MAX_ROAD_FAILURES = 3
PHASES = ("base", "fine")


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_bytes())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    try:
        for attempt in range(10):
            try:
                os.replace(temporary, path)
                return
            except PermissionError:
                if attempt == 9:
                    raise
                time.sleep(.03)
    finally:
        temporary.unlink(missing_ok=True)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Memory(ctypes.Structure):
    _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong)] + [
        (name, ctypes.c_ulonglong) for name in
        ("total", "available", "page", "page_available", "virtual", "virtual_available", "extended")
    ]


def memory():
    if os.name != "nt":
        return {"total_gib": None, "available_gib": None}
    value = Memory()
    value.length = ctypes.sizeof(value)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(value)):
        return {"total_gib": None, "available_gib": None}
    return {"total_gib": value.total / 2**30, "available_gib": value.available / 2**30}


class ProcessMemory(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong)] + [
        (name, ctypes.c_size_t) for name in
        ("peak_working_set", "working_set", "peak_paged", "paged", "peak_nonpaged", "nonpaged",
         "pagefile", "peak_pagefile", "private")
    ]


def private_gib():
    """This process's committed private memory."""
    if os.name != "nt":
        return None
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(ProcessMemory), ctypes.c_ulong]
    value = ProcessMemory()
    value.cb = ctypes.sizeof(value)
    if not kernel32.K32GetProcessMemoryInfo(kernel32.GetCurrentProcess(), ctypes.byref(value), value.cb):
        return None
    return value.private / 2**30


def keep_awake(active):
    """Stop idle sleep while calculating. Lid close or Start > Sleep still sleeps."""
    if os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.SetThreadExecutionState.argtypes = [ctypes.c_uint]
        kernel32.SetThreadExecutionState(0x80000000 | (0x1 if active else 0))


class ServiceLock:
    """An OS-held lock, released automatically even when the process crashes."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def acquire(self):
        import msvcrt
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            self.stream.close()
            self.stream = None
            return False

    def release(self):
        if self.stream:
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            self.stream.close()
            self.stream = None


def service_alive():
    lock = ServiceLock(CONTROL / "service.lock")
    acquired = lock.acquire()
    if acquired:
        lock.release()
    return not acquired


class StopRequested(BaseException):
    pass


class WorkerGate:
    """Check controls between source points; never interrupt an artifact write.

    Stops on the global stop command, on a shed marker for this process, or,
    for point chunks, on a cancel marker for the chunk's road that names an
    earlier held position. Chunks before a hold are still needed and continue.
    """
    def __init__(self, directory):
        self.directory = Path(directory)
        self.link = None
        self.kind = None
        self.position = None
        self.last_check = 0.
        self.last_heartbeat = 0.

    def heartbeat(self, phase, force=False):
        now = time.monotonic()
        if force or now - self.last_heartbeat >= 2:
            write_json(self.directory / "workers" / f"{os.getpid()}.json", {
                "pid": os.getpid(), "link_id": self.link, "task": self.kind, "phase": phase,
                "private_gib": private_gib(), "updated": utc_now(),
            })
            self.last_heartbeat = now

    def stopping(self, action):
        if action == "stop" or (self.directory / "shed" / f"{os.getpid()}.json").exists():
            return True
        if self.kind != "points" or self.link is None:
            return False
        held = read_json(self.directory / "cancel" / f"{self.link}.json")
        return held is not None and self.position is not None and tuple(self.position) > tuple(held["position"])

    def check(self):
        if time.monotonic() - self.last_check < .25:
            return
        while True:
            command = read_json(self.directory / "command.json", {})
            action = command.get("action", "pause")
            if self.stopping(action):
                self.heartbeat("saving", force=True)
                raise StopRequested()
            paused = action != "run" or (self.directory / "memory_pause.json").exists()
            self.heartbeat("paused" if paused else "calculating")
            if not paused:
                self.last_check = time.monotonic()
                return
            time.sleep(.25)


def load_engine():
    global ENGINE
    if ENGINE is None:
        spec = importlib.util.spec_from_file_location("pinned_road_calculation", ENGINE_PATH)
        ENGINE = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ENGINE
        spec.loader.exec_module(ENGINE)
    return ENGINE


GATE = None


def initialise_worker(output, control):
    global GATE
    if os.name == "nt":
        ctypes.windll.kernel32.SetPriorityClass(ctypes.windll.kernel32.GetCurrentProcess(), 0x4000)
    GATE = WorkerGate(control)
    GATE.heartbeat("loading inputs", force=True)
    engine = load_engine()
    engine.initialise(output)
    original = engine.PointJournal

    class ControlledJournal(original):
        def key(self, xy):
            GATE.check()
            return original.key(xy)

    # Scheduling hook only: values, coordinates, cache format and equations are unchanged.
    engine.PointJournal = ControlledJournal
    GATE.heartbeat("ready", force=True)


def evaluate_point(engine, state, policy, road, xy):
    """The body of script 119's compute.evaluate for one coordinate, unchanged."""
    prepared = None
    try:
        prepared = state["builder"].prepare_point(xy, engine.RECEIVER, reflections=True)
        r = engine.road_point_energy(prepared, state["features"], engine.PHYSICAL, policy, engine.FACADE,
            lambda distance: engine.source_joint_power(road, state["coefficients"], state["calendar"], state["air"], distance))
        value = engine.thin_point(r)
        value["reflector_contact_normalizations"] = [dict(facet_id=f["facet_id"], changes=f["contact_normalization"])
            for f in prepared["reflections"] if f.get("contact_normalization")]
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        value = dict(status="withheld", reasons=["unsupported_prepared_geometry:" + str(exc)],
                     relative_energy_per_m=None, paths=[], scope=engine.SCOPE)
    return value, prepared


def calculate_points(task):
    """Calculate one ordered chunk of a road's coordinates into a staged journal file.

    Stops at the first held point: the engine stops at that panel too.
    """
    engine = ENGINE
    state = engine.prior.STATE
    link = task["link_id"]
    GATE.link, GATE.kind = link, "points"
    policy = state["policies"][link]["physical_source"]
    road = state["roads"][link]
    staging = Path(task["staging"])
    staging.mkdir(parents=True, exist_ok=True)
    rank = PHASES.index(task["phase"])
    records, witness, held, stopped = [], None, None, False
    started = time.perf_counter()
    try:
        for index, xy in enumerate(task["points"]):
            GATE.position = (rank, task["positions"][index])
            try:
                GATE.check()
            except StopRequested:
                stopped = True
                break
            value, prepared = evaluate_point(engine, state, policy, road, xy)
            records.append(dict(fingerprint=engine.RUN["fingerprint"], xy_bng=list(xy), value=engine.plain(value)))
            if value["status"] == "withheld":
                held = index
                if prepared is not None:
                    witness = dict(xy_bng=xy, reasons=value["reasons"], prepared=prepared)
                break
    finally:
        if records:
            plan.write_staged(staging / f"{task['task_id']}.jsonl.gz", records)
        if witness is not None:
            path = staging / plan.witness_name(task["phase"], task["positions"][held])
            temporary = path.with_name(path.name + ".writing")
            with gzip.open(temporary, "wt", encoding="utf-8", compresslevel=1) as stream:
                json.dump(witness, stream, separators=(",", ":"), allow_nan=False)
            os.replace(temporary, path)
        GATE.link = GATE.kind = GATE.position = None
        GATE.heartbeat("ready", force=True)
    return {"finished": not stopped, "computed": len(records), "held_index": held,
            "seconds": time.perf_counter() - started, "private_gib": private_gib()}


def calculate_road(source):
    GATE.link, GATE.kind = source["link_id"], "road"
    try:
        GATE.check()
        result = ENGINE.compute(source)
        return {"finished": True, "result": result, "private_gib": private_gib()}
    except StopRequested:
        return {"finished": False, "link_id": source["link_id"], "private_gib": private_gib()}
    finally:
        GATE.link = GATE.kind = None
        GATE.heartbeat("ready", force=True)


def worker_main(connection, output, control):
    try:
        initialise_worker(output, control)
    except BaseException:
        connection.send(("init_error", traceback.format_exc()))
        return
    connection.send(("ready", os.getpid()))
    while True:
        try:
            kind, task = connection.recv()
        except (EOFError, OSError):
            return
        if kind == "exit":
            return
        try:
            reply = calculate_road(task) if kind == "road" else calculate_points(task)
            connection.send(("done", reply))
        except BaseException as exc:
            connection.send(("error", {"error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()}))


def worker_limit():
    return os.cpu_count() or 1


def command(action, workers=4):
    if action not in ("run", "pause", "stop"):
        raise ValueError("Unknown control action")
    workers = int(workers)
    if not 1 <= workers <= worker_limit():
        raise ValueError(f"Choose between 1 and {worker_limit()} workers, within the CPU count")
    write_json(CONTROL / "command.json", {"action": action, "workers": workers, "updated": utc_now()})


def launch(workers, roads=None):
    if (OUTPUT / "inventory.json").exists():
        raise ValueError("This calculation is already complete")
    if not service_alive():
        # Refuse to overlap an independently launched legacy scheduler.
        recent = [OUTPUT / "progress.json", *OUTPUT.glob("roads/*/activity.json")]
        previous = read_json(CONTROL / "state.json", {})
        clean_stop = previous.get("status") in ("stopped", "selected roads complete") or previous.get("status", "").startswith("finished")
        stopped_at = (CONTROL / "state.json").stat().st_mtime if clean_stop else 0
        if any(p.exists() and time.time() - p.stat().st_mtime < 120
               and p.stat().st_mtime > stopped_at for p in recent):
            raise ValueError("Recent calculation activity detected. Wait two minutes or use its existing controller.")
    command("run", workers)
    if service_alive():
        return
    CONTROL.mkdir(parents=True, exist_ok=True)
    python = ROOT / ".venv/Scripts/python.exe"
    if not python.exists():
        python = Path(sys.executable)
    arguments = [str(python), "-u", str(Path(__file__).resolve()), "--service",
                 "--output", str(OUTPUT), "--control", str(CONTROL)]
    if roads:
        arguments += ["--roads", ",".join(roads)]
    with (CONTROL / "calculation.log").open("ab", buffering=0) as log:
        subprocess.Popen(arguments, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)


class Worker:
    """One engine process. Retiring it is the only way to return its memory."""
    def __init__(self, output):
        parent, child = multiprocessing.Pipe()
        self.process = multiprocessing.Process(target=worker_main, args=(child, str(output), str(CONTROL)), daemon=True)
        self.process.start()
        child.close()
        self.connection = parent
        self.pid = self.process.pid
        self.ready = False
        self.task = None
        self.retiring = False

    def messages(self):
        result = []
        try:
            while self.connection.poll():
                result.append(self.connection.recv())
        except (EOFError, OSError):
            pass
        return result

    def send(self, kind, task=None):
        try:
            self.connection.send((kind, task))
            return True
        except OSError:
            return False

    @property
    def exited(self):
        return self.process.exitcode is not None

    def retire(self):
        self.retiring = True
        if self.task is None:
            self.send("exit")


def positioned_chunks(points, known):
    """(positions, points) chunks of not-yet-saved points, in engine order.

    Positions index the phase's full coordinate list, so they stay comparable
    across restarts when more points are already saved.
    """
    todo = [(i, p) for i, p in enumerate(points) if plan.coordinate_key(p) not in known]
    return deque(([i for i, _ in todo[k:k + CHUNK_POINTS]], [p for _, p in todo[k:k + CHUNK_POINTS]])
                 for k in range(0, len(todo), CHUNK_POINTS))


class RoadJob:
    """A long road: ordered point chunks, then the unchanged engine road call."""
    def __init__(self, link, base, leaves, known):
        self.link = link
        self.leaves = leaves
        self.known = known
        self.phase = "base"
        self.pending = positioned_chunks(base, known)
        self.total = len(base)
        self.inflight = 0
        self.held = None
        self.computed = 0
        self.seconds = 0.
        self.dispatched = False


class Scheduler:
    def __init__(self, engine, output, protocol, selected=None):
        self.engine, self.output, self.protocol = engine, output, protocol
        self.fingerprint = protocol["fingerprint"]
        self.sources = {r["link_id"]: r for r in engine.read(output / "source_lines.json")["source_roads"]}
        self.done = {p.parent.name for p in (output / "roads").glob("*/result.json")}
        for link in self.done:
            assert engine.read(output / "roads" / link / "result.json")["fingerprint"] == self.fingerprint
        workload = read_json(output / "workload_plan.json", {})
        if workload.get("fingerprint") != self.fingerprint:
            raise ValueError("workload_plan.json is missing or belongs to another calculation")
        self.planned = {r["link_id"]: r["minimum_primary_plus_finer_observations"] for r in workload["roads"]}
        self.primary = {r["link_id"]: r["minimum_primary_observations"] for r in workload["roads"]}
        estimate = read_json(CONTROL / "yield_estimate.json", {})
        self.holds = {}
        if estimate.get("fingerprint") == self.fingerprint:
            self.holds = {link: row["certain_hold_index"] for link, row in estimate["roads"].items()
                          if row.get("certain_hold_index") is not None}
        self.staging = CONTROL / "staging"
        # Error history is kept; every new start retries previously failed roads.
        self.failures = {link: dict(row, count=0) for link, row in
                         read_json(CONTROL / "failures.json", {}).get("roads", {}).items()}
        self.failed = set()
        self.selected = selected
        self.jobs = {}
        self.session_points = 0
        self.recover_staging()
        remaining = [link for link in self.sources if link not in self.done and link not in self.failed
                     and (selected is None or link in selected)]
        # Instant input holds first, then longest predicted work first, so the
        # longest road is not left running alone at the end.
        self.queue = deque(sorted(remaining, key=lambda link: (self.cost(link) > 0, -self.cost(link), link)))

    def cost(self, link):
        if link not in self.planned:
            return 0
        if link in self.holds:
            return min(self.primary[link], self.holds[link] // 5 * 5 + 5)
        return self.planned[link]

    def road_directory(self, link):
        return self.output / "roads" / link

    def recover_staging(self):
        """Staged chunks are complete records; move any left by a stop or crash."""
        if self.staging.exists():
            for directory in self.staging.iterdir():
                if directory.is_dir():
                    plan.merge_staged(directory, self.road_directory(directory.name) / "observations")
        for marker in (CONTROL / "cancel").glob("*.json"):
            marker.unlink()

    def predicted_gib(self, link):
        return self.cost(link) * plan.OBSERVATION_BYTES / 2**30 + .3

    def start_job(self, link):
        """Plan a long road; None means run it as one ordinary engine call."""
        if self.cost(link) < PREFETCH_MIN_POINTS:
            return None
        cached = self.output / "source_partitions" / (link + ".json.gz")
        if not cached.exists():
            return None
        record = json.loads(gzip.decompress(cached.read_bytes()))
        if record["fingerprint"] != self.fingerprint or record["link_id"] != link:
            return None
        source = self.sources[link]
        base, leaves = plan.primary_plan(source, record["partition"], self.engine.DEFAULTS["maximum_panel_m"])
        if len(base) != self.engine.minimum_initial(source, record["partition"]):
            return None
        if link in self.holds:
            base = plan.truncate_to_panel(base, self.holds[link])
        observations = self.road_directory(link) / "observations"
        known = plan.journal_keys(observations, self.fingerprint) if observations.exists() else set()
        job = RoadJob(link, base, leaves, known)
        self.jobs[link] = job
        self.advance(job)
        return job

    def advance(self, job):
        if job.inflight or job.pending or job.phase == "final":
            return
        if job.phase == "base" and job.held is None and job.link not in self.holds:
            job.phase = "fine"
            fine = plan.finer_plan(self.sources[job.link], job.leaves)
            job.total += len(fine)
            job.pending = positioned_chunks(fine, job.known)
            if job.pending:
                return
        (CONTROL / "cancel" / f"{job.link}.json").unlink(missing_ok=True)
        plan.merge_staged(self.staging / job.link, self.road_directory(job.link) / "observations")
        plan.promote_first_witness(self.staging / job.link, self.road_directory(job.link))
        job.phase = "final"

    def points_task(self, job):
        job.inflight += 1
        positions, points = job.pending.popleft()
        return ("points", {"link_id": job.link, "points": points, "positions": positions, "phase": job.phase,
                           "task_id": f"{int(time.time() * 1000):013d}-{uuid.uuid4().hex[:8]}",
                           "staging": str(self.staging / job.link)})

    def next_task(self, available, sole_worker):
        """Return (task, blocked_need_gib). Final road calls wait for memory."""
        def fits(link):
            return available is None or sole_worker or available - self.predicted_gib(link) >= RESERVE_GIB
        for job in self.jobs.values():
            if job.phase == "final" and not job.dispatched:
                if not fits(job.link):
                    return None, self.predicted_gib(job.link)
                job.dispatched = True
                return ("road", self.sources[job.link]), None
        for job in self.jobs.values():
            if job.pending:
                return self.points_task(job), None
        while self.queue:
            link = self.queue.popleft()
            job = self.start_job(link)
            if job is None:
                if not fits(link):
                    self.queue.appendleft(link)
                    return None, self.predicted_gib(link)
                return ("road", self.sources[link]), None
            if job.pending:
                return self.points_task(job), None
            return self.next_task(available, sole_worker)
        return None, None

    def finished_road(self, link, reply, stopping):
        job = self.jobs.pop(link, None)
        if not reply["finished"]:
            if not stopping:
                self.queue.appendleft(link)
            return
        self.done.add(link)
        result = read_json(self.road_directory(link) / "result.json", {})
        self.session_points += result.get("new_native_points", 0)
        if job is not None:
            with (CONTROL / "prefetch_receipts.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"link_id": link, "updated": utc_now(), "prefetched_points": job.computed,
                    "prefetch_worker_seconds": job.seconds, "held": job.held is not None,
                    "engine_status": result.get("status")}) + "\n")
        shutil.rmtree(self.staging / link, ignore_errors=True)

    def finished_points(self, task, reply):
        self.session_points += reply["computed"]
        job = self.jobs.get(task["link_id"])
        if job is None:
            # The road was set aside after repeated failures; its staged records stay valid.
            return
        job.inflight -= 1
        job.computed += reply["computed"]
        job.seconds += reply["seconds"]
        if reply["held_index"] is not None:
            # The engine stops at this panel: later chunks are not needed, earlier ones are.
            position = task["positions"][reply["held_index"]]
            if job.held is None or position < job.held:
                job.held = position
                job.pending = deque(c for c in job.pending if c[0][0] < position)
                write_json(CONTROL / "cancel" / f"{job.link}.json",
                           {"position": [PHASES.index(task["phase"]), position], "updated": utc_now()})
        elif not reply["finished"] and reply["computed"] < len(task["points"]):
            rest = reply["computed"]
            if job.held is None or task["positions"][rest] < job.held:
                job.pending.appendleft((task["positions"][rest:], task["points"][rest:]))
        self.advance(job)

    def failure(self, kind, task, error):
        link = task["link_id"]
        row = self.failures.setdefault(link, {"count": 0, "errors": []})
        row["count"] += 1
        row["errors"] = (row["errors"] + [{"time": utc_now(), "task": kind, "error": error[-4000:]}])[-5:]
        write_json(CONTROL / "failures.json", {"roads": self.failures, "updated": utc_now()})
        print(f"{utc_now()} road {link} {kind} failed ({row['count']}/{MAX_ROAD_FAILURES}): {error.splitlines()[-1] if error else ''}", flush=True)
        job = self.jobs.get(link)
        if kind == "points" and job is not None:
            job.inflight -= 1
            if row["count"] < MAX_ROAD_FAILURES:
                job.pending.appendleft((task["positions"], task["points"]))
                return
        if job is not None and job.inflight:
            return
        self.jobs.pop(link, None)
        plan.merge_staged(self.staging / link, self.road_directory(link) / "observations")
        if row["count"] < MAX_ROAD_FAILURES:
            self.queue.append(link)
        else:
            self.failed.add(link)

    def idle(self):
        return not self.queue and not self.jobs

    def has_work(self):
        return bool(self.queue) or any(j.pending or (j.phase == "final" and not j.dispatched) for j in self.jobs.values())

    def work(self):
        total = sum(self.planned.values())
        done = sum(self.planned[link] for link in self.done if link in self.planned)
        active = sum(min(self.planned.get(job.link, 0), len(job.known) + job.computed) for job in self.jobs.values())
        return {"planned_observations": total, "estimated_observations_done": done + active,
                "fraction": (done + active) / total if total else 1.}


def run_service(selected=None):
    lock = ServiceLock(CONTROL / "service.lock")
    if not lock.acquire():
        return
    started = time.monotonic()
    state = {"pid": os.getpid(), "status": "checking saved inputs", "started": utc_now(), "workers": 0}

    def publish(**updates):
        changed = any(state.get(k) != v for k, v in updates.items() if k in ("status", "completed_roads"))
        state.update(updates, updated=utc_now(), memory=memory())
        write_json(CONTROL / "state.json", state)
        if changed:
            print(f"{utc_now()} {state['status']}; {state.get('completed_roads', 0)}/{TOTAL_ROADS} saved roads; "
                  f"{state.get('active_roads', 0)} active", flush=True)

    workers = []
    awake = None
    try:
        publish()
        if (OUTPUT / "inventory.json").exists():
            publish(status="complete")
            return
        for directory in ("workers", "shed", "cancel"):
            for path in (CONTROL / directory).glob("*.json"):
                path.unlink()
        (CONTROL / "memory_pause.json").unlink(missing_ok=True)
        engine = load_engine()
        engine_started = time.perf_counter()
        output, protocol = engine.prepare(OUTPUT, True)
        write_json(CONTROL / "execution_receipt.json", {
            "started": utc_now(), "calculation_fingerprint": protocol["fingerprint"],
            "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "scheduling_sha256": hashlib.sha256(Path(plan.__file__).read_bytes()).hexdigest(),
            "engine_sha256": hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest(),
            "policy": "Unchanged pinned compute, validation, journals and sealing; external bounded scheduler, "
                      "between-point controls, and parallel calculation of the engine's exact first coordinates "
                      "with the engine's own point functions into its journal format.",
        })
        if not (output / "workload_plan.json").exists():
            # A fresh generation: cache source partitions with the engine's own planner,
            # in its own process so this controller never holds the input data.
            publish(status="planning road partitions (about 15 minutes)")
            subprocess.run([sys.executable, "-u", str(ENGINE_PATH), "--plan-only", "--resume", "--output", str(output)],
                           cwd=ROOT, check=True)
        scheduler = Scheduler(engine, output, protocol, selected)
        # Avoid native-library thread pools multiplying the selected process count.
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "1"
        shed = 0
        last_shed = restore_since = None
        last_progress = 0.
        init_failures = 0
        while True:
            config = read_json(CONTROL / "command.json", {})
            action = config.get("action", "pause")
            desired = max(1, min(int(config.get("workers", 4)), worker_limit()))
            available = memory()["available_gib"]
            now = time.monotonic()
            if awake != (action == "run"):
                awake = action == "run"
                keep_awake(awake)
            stopping = action == "stop"

            for worker in list(workers):
                for kind, payload in worker.messages():
                    if kind == "ready":
                        worker.ready = True
                        init_failures = 0
                    elif kind == "init_error":
                        init_failures += 1
                        print(payload, flush=True)
                        if init_failures >= 3:
                            raise RuntimeError("Workers cannot load the calculation inputs; see calculation.log")
                    elif kind in ("done", "error"):
                        task_kind, task = worker.task
                        worker.task = None
                        link = task["link_id"]
                        if kind == "error":
                            print(payload["traceback"], flush=True)
                            scheduler.failure(task_kind, task, payload["traceback"])
                        elif task_kind == "road":
                            scheduler.finished_road(link, payload, stopping)
                            if payload.get("private_gib") and payload["private_gib"] > RECYCLE_ABOVE_GIB:
                                worker.retire()
                        else:
                            scheduler.finished_points(task, payload)
                        if worker.retiring:
                            worker.send("exit")
                if worker.exited:
                    if worker.task is not None:
                        task_kind, task = worker.task
                        scheduler.failure(task_kind, task, f"Worker process {worker.pid} exited with code {worker.process.exitcode}")
                    (CONTROL / "shed" / f"{worker.pid}.json").unlink(missing_ok=True)
                    (CONTROL / "workers" / f"{worker.pid}.json").unlink(missing_ok=True)
                    workers.remove(worker)

            records = {w.pid: read_json(CONTROL / "workers" / f"{w.pid}.json", {}) for w in workers}
            busy = [w for w in workers if w.task is not None]
            active = [w for w in workers if not w.retiring]

            # Low memory: retire the largest worker; its point or road is saved and requeued.
            if available is not None and available < SHED_BELOW_GIB and not stopping and len(active) > 1:
                if last_shed is None or now - last_shed > 30:
                    candidates = [w for w in active if w.task is not None] or active
                    victim = max(candidates, key=lambda w: records[w.pid].get("private_gib") or 0)
                    write_json(CONTROL / "shed" / f"{victim.pid}.json", {"reason": "low memory", "updated": utc_now()})
                    victim.retire()
                    shed += 1
                    last_shed = now
                    print(f"{utc_now()} low memory ({available:.2f} GiB): retiring worker {victim.pid}", flush=True)
            if available is not None and available > RESTORE_ABOVE_GIB and shed:
                restore_since = restore_since or now
                if now - restore_since > 60:
                    shed -= 1
                    restore_since = now
            else:
                restore_since = None
            target = max(1, desired - shed)

            if stopping and not busy:
                scheduler.recover_staging()
                engine.progress(output, engine_started, scheduler.done, TOTAL_ROADS)
                publish(status="stopped", completed_roads=len(scheduler.done), active_roads=0, worker_records=[])
                break

            blocked = None
            if action == "run":
                idle = [w for w in active if w.ready and w.task is None]
                for worker in idle:
                    task, blocked = scheduler.next_task(available, sole_worker=len(workers) == 1)
                    if task is None:
                        break
                    worker.task = task
                    if not worker.send(*task):
                        worker.task = None
                        scheduler.failure(task[0], task[1], "Could not send task to worker")
                if blocked is not None and len(active) > 1:
                    # A large road call needs memory: free it by retiring idle workers.
                    for worker in [w for w in active if w.ready and w.task is None][:-1]:
                        worker.retire()
                loading = any(not w.ready for w in active)
                room = available is None or available > SPAWN_ABOVE_GIB or not active
                if len(active) < target and scheduler.has_work() and not loading and room and (blocked is None or not active):
                    workers.append(Worker(output))
                elif len(active) > target:
                    for worker in [w for w in active if w.task is None][:len(active) - target]:
                        worker.retire()

            if scheduler.idle() and not any(w.task for w in workers):
                if scheduler.failed:
                    publish(status=f"finished with {len(scheduler.failed)} failed roads", completed_roads=len(scheduler.done),
                            active_roads=0, failed_roads=sorted(scheduler.failed))
                elif selected is not None:
                    publish(status="selected roads complete", completed_roads=len(scheduler.done), active_roads=0)
                else:
                    publish(status="writing final results", active_roads=0)
                    engine.close_run(output, protocol, engine_started)
                    publish(status="complete", completed_roads=TOTAL_ROADS, active_roads=0)
                break

            paused = [r for r in records.values() if r.get("link_id") and r.get("phase") == "paused"]
            if stopping:
                status = "saving and stopping"
            elif action == "pause":
                status = "paused" if len(paused) == len(busy) else "pausing after current points"
            elif blocked is not None:
                status = f"freeing memory for a large road ({blocked:.1f} GiB)"
            elif shed:
                status = f"running with {shed} fewer workers (low memory)"
            else:
                status = "running"
            work = scheduler.work()
            elapsed = time.monotonic() - started
            rate = scheduler.session_points / (elapsed / 3600) if elapsed > 1200 and scheduler.session_points else None
            work.update(observations_per_hour=rate, rough_remaining_hours=
                        (work["planned_observations"] - work["estimated_observations_done"]) / rate if rate else None)
            views = []
            for w in workers:
                record = dict(records.get(w.pid, {}), pid=w.pid, retiring=w.retiring)
                if w.task is not None:
                    kind, task = w.task
                    job = scheduler.jobs.get(task["link_id"])
                    record["link_id"] = task["link_id"]
                    record["task"] = (f"{task['phase']} points ({job.computed + len(job.known):,}/{job.total:,})"
                                      if kind == "points" and job else "road (engine)")
                views.append(record)
            publish(status=status, workers=desired, active_roads=len({v["link_id"] for v in views if v.get("link_id") and v.get("task")}),
                    worker_records=views, completed_roads=len(scheduler.done), failed_roads=sorted(scheduler.failed),
                    elapsed_seconds=elapsed, work=work, shed_workers=shed)
            if time.monotonic() - last_progress >= 20:
                engine.progress(output, engine_started, scheduler.done, TOTAL_ROADS)
                last_progress = time.monotonic()
            time.sleep(1)
    except BaseException as exc:
        command("stop", state.get("workers") or 4)
        traceback.print_exc()
        publish(status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # Workers finish their current point and save it; staged chunks are merged on the next start.
        deadline = time.monotonic() + 900
        while any(w.task is not None and not w.exited for w in workers) and time.monotonic() < deadline:
            for w in workers:
                if any(kind in ("done", "error") for kind, _ in w.messages()):
                    w.task = None
            time.sleep(.5)
        for w in workers:
            w.send("exit")
        for w in workers:
            w.process.join(30)
            if w.process.is_alive():
                w.process.terminate()
        keep_awake(False)
        lock.release()


def snapshot():
    state = read_json(CONTROL / "state.json", {})
    state["service_alive"] = service_alive()
    state["sealed"] = (OUTPUT / "inventory.json").exists()
    if not state["service_alive"] and not state["sealed"]:
        status = state.get("status", "stopped")
        state["status"] = status if status == "error" or status.startswith("finished") else "stopped"
    return state


def gui():
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = tk.Tk()
    root.title("Quiet UK — Calculation Monitor")
    root.geometry("860x700")
    root.minsize(760, 620)
    root.configure(bg="#eef3f6")
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("TFrame", background="#eef3f6")
    style.configure("TLabel", background="#eef3f6", foreground="#183446", font=("Segoe UI", 10))
    style.configure("Title.TLabel", font=("Segoe UI", 21, "bold"))
    style.configure("Status.TLabel", font=("Segoe UI", 14, "bold"))
    style.configure("TButton", font=("Segoe UI", 10), padding=(12, 9))
    frame = ttk.Frame(root, padding=24)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Quiet UK", style="Title.TLabel").pack(anchor="w")
    ttk.Label(frame, text="Local road & building-reflection calculation").pack(anchor="w", pady=(2, 18))
    status = ttk.Label(frame, text="Reading saved progress…", style="Status.TLabel")
    status.pack(anchor="w")
    summary = ttk.Label(frame, text="")
    summary.pack(anchor="w", pady=(12, 4))
    progress = ttk.Progressbar(frame, maximum=1000)
    progress.pack(fill="x", pady=6)
    details = ttk.Label(frame, text="", wraplength=790, justify="left")
    details.pack(anchor="w", pady=(4, 14))
    buttons = ttk.Frame(frame)
    buttons.pack(fill="x")
    workers = tk.IntVar(value=read_json(CONTROL / "command.json", {}).get("workers", 4))

    def handle(action):
        try:
            count = int(workers.get())
            if action == "run":
                launch(count)
            elif service_alive():
                command(action, count)
        except Exception as exc:
            messagebox.showerror("Calculation control", str(exc), parent=root)

    start = ttk.Button(buttons, text="Start / Resume", command=lambda: handle("run"))
    start.pack(side="left")
    pause = ttk.Button(buttons, text="Pause", command=lambda: handle("pause"))
    pause.pack(side="left", padx=8)
    stop = ttk.Button(buttons, text="Save & Stop", command=lambda: handle("stop"))
    stop.pack(side="left")
    options = ttk.Frame(frame)
    options.pack(fill="x", pady=(14, 12))
    ttk.Label(options, text="Worker limit").pack(side="left")
    spinner = ttk.Spinbox(options, from_=1, to=worker_limit(), textvariable=workers, width=4)
    spinner.pack(side="left", padx=8)
    ttk.Label(options, text=f"Up to {worker_limit()} · change after Save & Stop · fewer run automatically when memory is low").pack(side="left")
    ttk.Label(frame, text="Workers", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 6))
    tree = ttk.Treeview(frame, columns=("road", "task", "stage", "memory"), show="headings", height=7)
    for name, text, width, anchor in (("road", "Road", 300, "w"), ("task", "Task", 220, "w"),
                                      ("stage", "Stage", 140, "w"), ("memory", "Memory", 90, "e")):
        tree.heading(name, text=text)
        tree.column(name, width=width, anchor=anchor)
    tree.pack(fill="both", expand=True)
    links = ttk.Frame(frame)
    links.pack(fill="x", pady=(12, 10))
    ttk.Button(links, text="Open calculation log", command=lambda: os.startfile(CONTROL / "calculation.log")).pack(side="left")
    ttk.Button(links, text="Open saved results folder", command=lambda: os.startfile(OUTPUT)).pack(side="left", padx=8)
    ttk.Label(frame, text="Closing this window leaves the calculation running. The laptop stays awake while it calculates,\nbut closing the lid or choosing Sleep still stops it; Save & Stop preserves checkpoints.\nOffline study at one location; no map update or all-inclusive noise value.",
              foreground="#526875", justify="left").pack(anchor="w")

    def refresh():
        state = snapshot()
        alive = state["service_alive"]
        sealed = state["sealed"]
        status.configure(text="Complete — results saved" if sealed else state.get("status", "stopped").capitalize())
        done_roads = len(list(OUTPUT.glob("roads/*/result.json")))
        work = state.get("work") or {}
        fraction = 1. if sealed else work.get("fraction")
        if fraction is None:
            plan_rows = read_json(OUTPUT / "workload_plan.json", {}).get("roads", [])
            total = sum(r["minimum_primary_plus_finer_observations"] for r in plan_rows)
            finished = {p.parent.name for p in OUTPUT.glob("roads/*/result.json")}
            fraction = sum(r["minimum_primary_plus_finer_observations"] for r in plan_rows if r["link_id"] in finished) / total if total else 0.
        progress["value"] = round(1000 * fraction)
        summary.configure(text=f"{fraction:.1%} of planned work  ·  {done_roads:,} / {TOTAL_ROADS} roads saved (128 are instant input holds)")
        mem = memory()
        ram = f"{mem['available_gib']:.1f} GB RAM free" if mem["available_gib"] is not None else ""
        info = f"{state.get('active_roads', 0) if alive else 0} active roads · worker limit {state.get('workers') or 4} · {ram}"
        if state.get("failed_roads"):
            info += f"\n{len(state['failed_roads'])} roads failed repeatedly and were set aside (failures.json). The run cannot be sealed until they succeed."
        if state.get("error"):
            info += "\n" + state["error"]
        elif not alive and not sealed:
            info += "\nSaved work is retained. Start / Resume continues it."
        elif work.get("rough_remaining_hours"):
            info += (f"\nRough remaining time: {work['rough_remaining_hours']:.0f} h at {work['observations_per_hour']:,.0f} observations/h "
                     "(planned minimum; held roads stop earlier, refinements add more).")
        else:
            info += "\nRemaining time appears after about 20 minutes of calculation."
        details.configure(text=info)
        start.configure(state="disabled" if sealed else "normal")
        pause.configure(state="normal" if alive else "disabled")
        stop.configure(state="normal" if alive else "disabled")
        spinner.configure(state="disabled" if alive else "normal")
        tree.delete(*tree.get_children())
        for record in state.get("worker_records", []) if alive else []:
            link = record.get("link_id")
            stage = record.get("phase", "")
            if stage == "calculating" and link and record.get("task") == "road (engine)":
                stage = read_json(OUTPUT / "roads" / link / "activity.json", {}).get("phase", stage)
            if record.get("retiring"):
                stage = "retiring"
            gib = record.get("private_gib")
            tree.insert("", "end", values=(link or "Loading / ready", record.get("task") or "—", stage,
                                           f"{gib:.2f} GB" if gib else "—"))
        root.after(2000, refresh)

    def close():
        if service_alive() and not read_json(CONTROL / "ui_preferences.json", {}).get("close_explained"):
            messagebox.showinfo("Calculation continues", "The calculation will keep running in the background.\nOpen this monitor again to pause or stop it.", parent=root)
            write_json(CONTROL / "ui_preferences.json", {"close_explained": True})
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", close)
    refresh()
    root.mainloop()


def main():
    global OUTPUT, CONTROL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--action", choices=("start", "pause", "resume", "stop", "status"))
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--output", help="Run generation (default: the v4 calculation)")
    parser.add_argument("--control", help="Control directory (default: artifacts/local_calculation_control)")
    parser.add_argument("--roads", help="Comma-separated road IDs to calculate; never seals the run")
    args = parser.parse_args()
    if args.output:
        OUTPUT = Path(args.output).resolve()
    if args.control:
        CONTROL = Path(args.control).resolve()
    selected = set(args.roads.split(",")) if args.roads else None
    if args.service:
        run_service(selected)
    elif args.action == "status":
        print(json.dumps(snapshot(), indent=2))
    elif args.action in ("start", "resume"):
        launch(args.workers, sorted(selected) if selected else None)
        print("Calculation starting in the background. Open Launch Calculation Monitor.cmd to follow it.")
    elif args.action:
        if service_alive():
            command(args.action, args.workers)
        print("Control saved; current point may take a little time to finish." if service_alive() else "Calculation is stopped.")
    else:
        gui()


if __name__ == "__main__":
    main()
