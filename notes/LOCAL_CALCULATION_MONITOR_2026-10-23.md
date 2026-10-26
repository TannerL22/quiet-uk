# Local calculation monitor — 23 October 2026

Double-click **Launch Calculation Monitor.cmd** in either the outer project
folder or the inner repository. The window controls the existing v4 road and
reflection calculation independently of Codex; opening it does not start work
automatically. Start / Resume launches a separate background Python process.

- **Start / Resume** continues saved roads and exact-coordinate observations.
- **Pause** waits at the next source-point boundary. It keeps workers and their
  loaded inputs in memory. A long current point can delay the pause.
- **Save & Stop** finishes current points, closes the existing observation
  journals and exits the workers to release memory. Resume replays integration
  using cached observations; a small amount of bookkeeping may repeat.
- Closing the monitor leaves the calculation running. Sleeping, shutting down
  or restarting the laptop stops progress; reopen and resume afterward. There
  is no automatic startup or sleep-prevention setting.

The default limit is **four worker processes**, increased from two. On this
12-core laptop, memory is more restrictive than core count: total RAM is about
15.6 GiB, with about 2.8 GiB free before this launch. Startup is staggered. The
controller holds back extra roads when free RAM is below 1.25 GiB, pauses between
points below 0.5 GiB, and clears that memory pause once more than 1 GiB is free.
These checks cannot prevent every transient memory peak inside a point. Close
other apps if memory is tight, or Save & Stop and choose fewer workers.

The worker selector accepts up to the detected CPU count (12 on this laptop);
the former eight-worker cap has been removed. Change it after Save &
Stop, then resume. Native math library thread pools are limited to one thread
per worker, and workers run at below-normal Windows priority.

Progress distinguishes the 594 eligible roads from the 128 existing input holds.
"Processed" includes roads whose result is withheld; it does not mean every
road produced an accepted decibel contribution. Remaining time is deliberately
not extrapolated from the shortest roads. The table shows current road IDs,
stages and newly calculated source positions for the current road attempt.

## Execution and provenance

`scripts/calculation_monitor.py` is an external execution controller. It imports
the original, byte-unchanged script 119 and retains its `prepare`, `compute`,
`progress` and `close_run` functions. It schedules independent roads with a
larger process pool. A journal key hook checks pause/stop controls before source
positions; it delegates coordinate keys and all stored/calculated values to the
original implementation. Stop unwinds the original journal close block.

The original 93-file code binding, input validation, runtime identity, run
fingerprint, acceptance criteria, scientific model and final sealing are
unchanged. No broad physics validation blocks are reopened. The external
controller's own hash and the original engine hash are recorded per launch in
`artifacts/local_calculation_control/execution_receipt.json`.

Controls, worker heartbeats, status and log files live in
`artifacts/local_calculation_control/`, outside the run generation. A Windows
file lock prevents two monitor schedulers running together and releases on
process exit. Recent unmanaged calculation activity blocks a second launch;
do not start the old script 119 launcher alongside this controller.

Offline results still cover the existing receiver and declared selected paths.
They do not update the map or calculate a complete all-inclusive sound level.

## Command-line equivalents

Run from the inner repository:

```powershell
.venv\Scripts\python.exe scripts/calculation_monitor.py --action start --workers 4
.venv\Scripts\python.exe scripts/calculation_monitor.py --action status
.venv\Scripts\python.exe scripts/calculation_monitor.py --action pause
.venv\Scripts\python.exe scripts/calculation_monitor.py --action resume --workers 4
.venv\Scripts\python.exe scripts/calculation_monitor.py --action stop
```

The background process survives closing the monitor and Codex. It is not a
Windows service and does not resume after a restart until you start it again.

Six execution-control checks cover pause/resume, stop while memory-paused,
cross-process duplicate exclusion, stale status handling, immediate restart
after a clean stop, and rejection of recent unmanaged calculation activity.
Real-run pause/stop/restart is also checked before handover. The GUI layout
was checked for clipping at its initial window size.
