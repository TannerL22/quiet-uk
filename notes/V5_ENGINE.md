# v5 engine — 26 October 2026

Branch `v5-engine`, worked on in a separate `git worktree` so the folder that
holds the unsealed v4 run is never checked out to other bytes. Nothing here
changes v4: it stays resumable and sealable from `main` (tag `v4-run-pinned`).

## Code identity: pin what the engine runs

v4 pins 93 repository files, including the explorer server, catalogue and
unrelated tests; editing any of them blocks resume and fails the final seal.
v5 pins the engine's **import closure**: the repository files loaded by
importing script 119 in a clean interpreter (`run_identity.import_closure`),
now 31 files. The engine has no function-level imports of repository modules,
so the closure covers everything it executes.

A closure file is accepted when it is byte-identical to the validation receipt
(`road_reflection_connection_validation_v1.json`), or when an **equivalence
receipt** (`artifacts/engine_equivalence_v5.json`, written by script 121)
shows the current files reproduced at least 1,000 saved v4 observations byte
for byte. Any other change stops `prepare` and `close_run`. The protocol records
the equivalence receipt's hash, so a resumed or sealed run proves which replay
accepted its code.

## Speed (byte-identical)

Profiling real coordinates showed the cost was not profile building
(`prepare_point`, about 17%) but the reflection kernel re-validating the same
~950 ground segments for every band and condition: `side_ground` and
`ground_average` took about 55% of the time. Both are pure functions of
their inputs, so they are now cached inside the existing per-path geometry
scope (`propagation_geometry_cache`). Terrain sampling for each crossed 2 m
cell is vectorised with the same float operations as `NativeTerrain.sample`.

| Measure | v4 code | v5 code |
|---|---|---|
| Same 120 random saved points (mean per point) | 1.33 s | 0.96 s |
| 400 other random saved points (mean per point) | — | 0.88 s |
| 15 expensive points, 7 reflections each (under profiler) | 8.7 s | 4.05 s |

Every replayed observation is identical to v4's saved journal line (script 121):
120, 400 and 1,000-record samples (32, 109 and 136 of them withheld points),
across 93 to 303 roads; the 1,000-record run is the receipt v5 requires. The v4 code also reproduced its own records, which
confirms the harness and the calculation's determinism. Cheap points (few
reflections) gain least; the expensive points that dominate the remaining long
roads gain most.

## Memory

- The PointJournal cache holds each observation as compact JSON text (about
  9 KB) instead of decoded dicts (about 31 KB), decoding on use.
- Node files are written with a JSON `default` instead of `plain()`, which
  deep-copied every node first and doubled peak memory at the end of a road.
- The 2 m terrain grid and its mask (171 MiB) are memory-mapped from a derived
  cache in `artifacts/_derived_raster_cache/<GeoTIFF sha256>/`, shared by all
  workers through the page cache. The cache is verified by hash on every load.

Measured after loading inputs: private memory per worker 1.07 GiB (v4 code)
and 0.89 GiB (v5); the terrain is then shared page cache. The monitor's memory
estimate per observation drops from 70 KB to 48 KB for v5.

## Shared code moved out of numbered scripts

| Now in the package | Was in | The old script |
|---|---|---|
| `quiet_uk.generations` (`verified`, `seal`) | script 57 | re-exports them |
| `quiet_uk.real_scene_inputs` (`load_builder`) | script 81 | re-exports it |
| `quiet_uk.road_calculation_inputs` (`initialise`, `STATE`) | script 89 | imports them |
| `quiet_uk.digests` (`file_hash`, `json_bytes`) | `quiet_uk.explorer` | re-exports them |

Script 119 imports only package modules; about 50 older scripts that load
script 57 by path keep working unchanged.

## Not changed

- Acoustic equations, inputs, acceptance criteria and the 0.1 dB finer check.
- Whether one held point withholds a whole road. That is a modelling decision;
  `scripts/120_estimate_road_yield.py` (on `main`) estimates how much of the
  remaining work it affects.
- The on-disk journal format, so existing verification scripts read v5 output.

## Starting v5

1. Finish or stop v4. Never run two calculations on this laptop at once.
2. From this worktree, write the equivalence receipt (about 20 minutes):
   `python scripts/121_verify_engine_equivalence.py --count 1000 --receipt artifacts/engine_equivalence_v5.json`
3. Start it with the monitor: `python scripts/calculation_monitor.py --action start --workers 5`.
   On a fresh generation the monitor first runs the engine's own `--plan-only`
   step (about 15 minutes) in a separate process.
