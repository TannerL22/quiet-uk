# Local calculation monitor — 23 October 2026 (revised 26 October)

Double-click **Launch Calculation Monitor.cmd** in either the outer project
folder or the inner repository. The window controls the road and reflection
calculation; opening it does not start work. Start / Resume launches a
separate background Python process.

- **Start / Resume** continues saved roads and exact-coordinate observations.
- **Pause** waits at the next source-point boundary and keeps workers in memory.
- **Save & Stop** finishes current points, saves every journal and staged chunk,
  and exits all workers. Resume continues from the saved observations.
- Closing the monitor leaves the calculation running. While it calculates the
  laptop is kept from idle sleep; closing the lid, choosing Sleep, shutting down
  or restarting still stops it. Reopen and resume afterwards.

## What changed on 26 October

The 23 October controller ran one road per process, shortest first, and paused
every worker when free memory was low. That left the longest road (at least
19 CPU-hours) running alone at the end, and a memory pause could not end on
its own, because paused workers keep their memory. It also stopped every
worker if any single road raised an unexpected error.

- **Longest roads first.** Instant input holds go first, then roads in
  decreasing planned work (`workload_plan.json`).
- **Parallel points within a road.** For roads with at least 400 planned
  observations, the engine's own first coordinates (five per base panel, then
  four per accepted leaf) are calculated in chunks of 48 across all workers,
  using script 119's point functions. Each chunk is staged in the engine's
  journal format and merged into the road's journal before the unchanged engine
  completes the road, reusing them. A held point cancels only later chunks; the
  engine stops at that panel anyway. Recomputed v4 roads match v4 byte for byte
  (results, node files, every observation and the first-held witness).
- **Memory.** Below 0.6 GB free, the worker using the most memory saves its
  point and exits, which actually returns its memory; its work is requeued.
  Workers come back once more than 2 GB has been free for a minute. A worker
  that grows past 1.5 GB after a road is replaced. A road call predicted to
  need more memory than is free waits, and idle workers retire to make room.
- **Failures stay local.** A road whose task raises or whose worker dies is
  retried up to three times, recorded in `failures.json`, and then set aside;
  the rest continue. The run cannot be sealed until every road succeeds, and
  each new start retries roads that were set aside.
- **Honest progress.** The bar and percentage are weighted by planned
  observations, not road counts. A rough remaining time appears after 20
  minutes; it uses the planned minimum, so held roads finish sooner and
  refinements add work.
- **Yield estimate (optional).** `python scripts/120_estimate_road_yield.py`
  checks, without any acoustics, which roads the model must withhold because
  of a facade at one of their first coordinates (for example a facade narrower
  than the assumed view width). The monitor uses its result to order work and
  to skip calculating coordinates past a road's certain hold. On a sample of
  12 saved roads, all 6 predicted holds were roads the engine withheld; run it
  with `--include-completed` to repeat that check across every saved road.
  `--max-checks N` bounds the cost per road; the hold count is then a lower
  bound, since a narrow facade may be visible from only a short stretch.

## Memory and workers

Each worker loads about 0.9 GB of inputs. On this 15.6 GB laptop, 4–6 workers
leave room for the larger roads; the controller now reduces the count itself
when memory runs low. The worker limit is chosen after Save & Stop.

## Execution and provenance

`scripts/calculation_monitor.py` is an external execution controller. It loads
the original, byte-unchanged script 119 and uses its `prepare`, `compute`,
`progress` and `close_run` functions, plus its point functions for staged
chunks. `src/quiet_uk/calculation_scheduling.py` reproduces the engine's
coordinates; tests check it against `integrate_adaptive` itself, and it
matched every one of the 594 eligible roads' cached partitions bit for bit.
The protocol, code binding, runtime identity, acceptance criteria, scientific
model and sealing are unchanged. The controller's and scheduling module's
hashes are recorded per launch in `execution_receipt.json`; per-road chunk
effort is in `prefetch_receipts.jsonl` (a road's `elapsed_seconds` covers only
its final engine call when its points were calculated in chunks).

Controls, worker heartbeats, staged chunks, status and logs live in
`artifacts/local_calculation_control/`, outside the run generation. A Windows
file lock prevents two schedulers running together. Do not start the old
script 119 launcher alongside this controller.

## Command-line equivalents

Run from the inner repository:

```powershell
.venv\Scripts\python.exe scripts/calculation_monitor.py --action start --workers 5
.venv\Scripts\python.exe scripts/calculation_monitor.py --action status
.venv\Scripts\python.exe scripts/calculation_monitor.py --action pause
.venv\Scripts\python.exe scripts/calculation_monitor.py --action stop
.venv\Scripts\python.exe scripts/120_estimate_road_yield.py --workers 4
```

`--output`, `--control` and `--roads` select another run generation, control
folder or subset of roads (a subset never seals the run); they are used for
checks on copies of the run.

## Line endings and git

Pinned files are hashed byte for byte. `.gitattributes` now checks text out
with LF and stores four pinned files with mixed line endings exactly, so a
fresh checkout reproduces all 93 v4 hashes. Never switch branches in a folder
that holds an unsealed pinned run; use a separate `git worktree`.
