"""Desktop controls and a detached scheduler for the unchanged, pinned road model.

The scheduler changes execution only. Existing protocol/code/runtime validation,
road calculations, exact-coordinate journals and final sealing stay in script 119.
Control files live outside that generation so controls also work after sealing.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor
import ctypes
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback
import uuid

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/real_road_reflection_calculation_v4"
CONTROL = ROOT / "artifacts/local_calculation_control"
ENGINE_PATH = ROOT / "scripts/119_calculate_real_road_reflections.py"
ENGINE = None


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
    """Check controls between source points; never interrupt an artifact write."""
    def __init__(self, directory):
        self.directory = Path(directory)
        self.link = None
        self.last_check = 0.
        self.last_heartbeat = 0.

    def heartbeat(self, phase, force=False):
        now = time.monotonic()
        if force or now - self.last_heartbeat >= 2:
            write_json(self.directory / "workers" / f"{os.getpid()}.json", {
                "pid": os.getpid(), "link_id": self.link, "phase": phase, "updated": utc_now(),
            })
            self.last_heartbeat = now

    def check(self):
        if time.monotonic() - self.last_check < .25:
            return
        while True:
            command = read_json(self.directory / "command.json", {})
            action = command.get("action", "pause")
            if action == "stop":
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


def calculate_road(source):
    GATE.link = source["link_id"]
    try:
        GATE.check()
        result = ENGINE.compute(source)
        return {"finished": True, "result": result}
    except StopRequested:
        return {"finished": False, "link_id": source["link_id"]}
    finally:
        GATE.link = None
        GATE.heartbeat("ready", force=True)


def worker_limit():
    return os.cpu_count() or 1


def command(action, workers=4):
    if action not in ("run", "pause", "stop"):
        raise ValueError("Unknown control action")
    workers = int(workers)
    if not 1 <= workers <= worker_limit():
        raise ValueError(f"Choose between 1 and {worker_limit()} workers, within the CPU count")
    write_json(CONTROL / "command.json", {"action": action, "workers": workers, "updated": utc_now()})


def launch(workers):
    if (OUTPUT / "inventory.json").exists():
        raise ValueError("This calculation is already complete")
    if not service_alive():
        # Refuse to overlap an independently launched legacy scheduler.
        recent = [OUTPUT / "progress.json", *OUTPUT.glob("roads/*/activity.json")]
        previous = read_json(CONTROL / "state.json", {})
        clean_stop = previous.get("status") == "stopped"
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
    with (CONTROL / "calculation.log").open("ab", buffering=0) as log:
        subprocess.Popen([str(python), "-u", str(Path(__file__).resolve()), "--service"],
                         cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                         close_fds=True)


def run_service():
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
            print(f"{utc_now()} {state['status']}; {state.get('completed_roads', 0)}/722 saved roads; "
                  f"{state.get('active_roads', 0)} active", flush=True)

    pool = None
    pending = {}
    try:
        publish()
        if (OUTPUT / "inventory.json").exists():
            publish(status="complete")
            return
        for path in (CONTROL / "workers").glob("*.json"):
            path.unlink()
        (CONTROL / "memory_pause.json").unlink(missing_ok=True)
        engine = load_engine()
        output, protocol = engine.prepare(OUTPUT, True)
        write_json(CONTROL / "execution_receipt.json", {
            "started": utc_now(), "calculation_fingerprint": protocol["fingerprint"],
            "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "engine_sha256": hashlib.sha256(ENGINE_PATH.read_bytes()).hexdigest(),
            "policy": "Unchanged pinned compute, validation, journals and sealing; external bounded scheduler and between-point controls.",
        })
        sources = engine.read(output / "source_lines.json")["source_roads"]
        done = {p.parent.name for p in (output / "roads").glob("*/result.json")}
        for link in done:
            assert engine.read(output / "roads" / link / "result.json")["fingerprint"] == protocol["fingerprint"]
        jobs = iter(sorted((r for r in sources if r["link_id"] not in done),
                           key=lambda r: (r["prepared_length_m"], r["link_id"])))
        # Avoid native-library thread pools multiplying the selected process count.
        for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
            os.environ[name] = "1"
        config = read_json(CONTROL / "command.json", {})
        maximum = max(1, min(int(config.get("workers", 4)), worker_limit()))
        pool = ProcessPoolExecutor(max_workers=maximum, initializer=initialise_worker,
                                   initargs=(str(output), str(CONTROL)))
        exhausted = False
        last_progress = 0.
        last_launch = 0.
        while True:
            config = read_json(CONTROL / "command.json", {})
            action = config.get("action", "pause")
            available = memory()["available_gib"]
            marker = CONTROL / "memory_pause.json"
            if available is not None and available < .5:
                write_json(marker, {"reason": "Less than 0.5 GB RAM free. Close other apps or save and stop.", "updated": utc_now()})
            elif available is None or available > 1.0:
                marker.unlink(missing_ok=True)
            for future in list(pending):
                if future.done():
                    reply = future.result()
                    if reply["finished"]:
                        done.add(pending[future])
                    del pending[future]
            if action == "stop" and not pending:
                engine.progress(output, started, done, 722)
                publish(status="stopped", completed_roads=len(done), active_roads=0)
                break
            if action == "run" and not marker.exists() and not exhausted:
                desired = min(maximum, max(1, int(config.get("workers", maximum))))
                # Start one worker at a time; wait for native inputs to load first.
                workers = list((CONTROL / "workers").glob("*.json"))
                loading = any(read_json(p, {}).get("phase") == "loading inputs" for p in workers)
                room = not pending or available is None or available > 1.25
                if len(pending) < desired and not loading and room and time.monotonic() - last_launch >= 5:
                    source = next(jobs, None)
                    if source is None:
                        exhausted = True
                    else:
                        pending[pool.submit(calculate_road, source)] = source["link_id"]
                        last_launch = time.monotonic()
            if exhausted and not pending:
                publish(status="writing final results", active_roads=0)
                engine.close_run(output, protocol, started)
                publish(status="complete", completed_roads=722, active_roads=0)
                break
            records = [read_json(p, {}) for p in (CONTROL / "workers").glob("*.json")]
            occupied = [r for r in records if r.get("link_id")]
            paused = len([r for r in occupied if r.get("phase") == "paused"])
            if action == "stop":
                status = "saving and stopping"
            elif marker.exists():
                status = "paused for low memory" if paused == len(pending) else "pausing for low memory"
            elif action == "pause":
                status = "paused" if paused == len(pending) else "pausing after current points"
            else:
                status = "running"
            publish(status=status, workers=maximum, active_roads=len(pending), worker_records=records,
                    completed_roads=len(done), elapsed_seconds=time.monotonic() - started)
            if time.monotonic() - last_progress >= 20:
                engine.progress(output, started, done, 722)
                last_progress = time.monotonic()
            time.sleep(1)
    except BaseException as exc:
        command("stop", state.get("workers") or 4)
        traceback.print_exc()
        publish(status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        # Workers finish their current point, then unwind the original journal's finally block.
        if pool:
            pool.shutdown(wait=True, cancel_futures=True)
        lock.release()


def snapshot():
    state = read_json(CONTROL / "state.json", {})
    state["service_alive"] = service_alive()
    state["sealed"] = (OUTPUT / "inventory.json").exists()
    if not state["service_alive"] and not state["sealed"]:
        state["status"] = "stopped" if state.get("status") != "error" else "error"
    return state


def gui():
    import tkinter as tk
    from tkinter import messagebox, ttk
    root = tk.Tk()
    root.title("Quiet UK — Calculation Monitor")
    root.geometry("800x660")
    root.minsize(720, 590)
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
    progress = ttk.Progressbar(frame, maximum=594)
    progress.pack(fill="x", pady=6)
    details = ttk.Label(frame, text="", wraplength=730, justify="left")
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
    ttk.Label(options, text=f"Up to {worker_limit()} · change after Save & Stop").pack(side="left")
    ttk.Label(frame, text="Current roads", font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(4, 6))
    tree = ttk.Treeview(frame, columns=("road", "stage", "points"), show="headings", height=6)
    tree.heading("road", text="Road")
    tree.heading("stage", text="Stage")
    tree.heading("points", text="New points this session")
    tree.column("road", width=310)
    tree.column("stage", width=190)
    tree.column("points", width=170, anchor="e")
    tree.pack(fill="both", expand=True)
    links = ttk.Frame(frame)
    links.pack(fill="x", pady=(12, 10))
    ttk.Button(links, text="Open calculation log", command=lambda: os.startfile(CONTROL / "calculation.log")).pack(side="left")
    ttk.Button(links, text="Open saved results folder", command=lambda: os.startfile(OUTPUT)).pack(side="left", padx=8)
    ttk.Label(frame, text="Closing this window leaves the calculation running. Pause keeps it in memory;\nSave & Stop releases memory and preserves checkpoints. Sleep/restart stops calculation.\nOffline study at one location; no map update or all-inclusive noise value.",
              foreground="#526875", justify="left").pack(anchor="w")

    def refresh():
        state = snapshot()
        alive = state["service_alive"]
        sealed = state["sealed"]
        status.configure(text="Complete — results saved" if sealed else state.get("status", "stopped").capitalize())
        original = read_json(OUTPUT / "progress.json", {})
        counts = original.get("status_counts", original.get("summary", {}).get("status_counts", {}))
        total = state.get("completed_roads", original.get("completed_roads", 0))
        # These 128 input holds are predetermined and aren't eligible acoustic jobs.
        held_existing = counts.get("withheld_existing_input_support", 0)
        eligible_done = min(594, max(0, total - held_existing))
        if sealed:
            eligible_done = 594
        progress["value"] = eligible_done
        summary.configure(text=f"{eligible_done:,} / 594 eligible roads processed ({eligible_done/594:.1%})  ·  128 existing input holds")
        mem = memory()
        ram = f"{mem['available_gib']:.1f} GB RAM free" if mem["available_gib"] is not None else ""
        active = state.get("active_roads", 0) if alive else 0
        info = f"{active} active roads · worker limit {state.get('workers') or 4} · {ram}"
        if state.get("error"):
            info += "\n" + state["error"]
        elif not alive and not sealed:
            info += "\nSaved work is retained. Start / Resume continues it."
        else:
            info += "\nRemaining time: varies with road length and reflections; no reliable ETA yet."
        details.configure(text=info)
        start.configure(state="disabled" if sealed else "normal")
        pause.configure(state="normal" if alive else "disabled")
        stop.configure(state="normal" if alive else "disabled")
        spinner.configure(state="disabled" if alive else "normal")
        tree.delete(*tree.get_children())
        records = state.get("worker_records", []) if alive else []
        for record in records:
            link = record.get("link_id")
            activity = read_json(OUTPUT / "roads" / link / "activity.json", {}) if link else {}
            stage = record.get("phase", "")
            if stage == "calculating":
                stage = activity.get("phase", stage)
            tree.insert("", "end", values=(link or "Loading / ready", stage, activity.get("new_native_points", "—")))
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", action="store_true")
    parser.add_argument("--action", choices=("start", "pause", "resume", "stop", "status"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.service:
        run_service()
    elif args.action == "status":
        print(json.dumps(snapshot(), indent=2))
    elif args.action in ("start", "resume"):
        launch(args.workers)
        print("Calculation starting in the background. Open Launch Calculation Monitor.cmd to follow it.")
    elif args.action:
        if service_alive():
            command(args.action, args.workers)
        print("Control saved; current point may take a little time to finish." if service_alive() else "Calculation is stopped.")
    else:
        gui()


if __name__ == "__main__":
    main()
