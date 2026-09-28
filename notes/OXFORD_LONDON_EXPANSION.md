# Oxford–London detailed coverage expansion

## Scope

Extend the existing 50 × 50 km western area eastward to a 100 × 50 km rectangle,
BNG cell edges `[445005, 165005, 545005, 215005]`. The resulting 5,000 km² includes
Heathrow; it is not counted again as a separate area. This adds 2,400 km² to the
previous 2,600 km² map. Native 10 m road, rail and aircraft grids retain all three
indicators (Lden, Lday, Lnight). This rectangle includes central London but is not
a claim to cover all Greater London or the UK.

## Reuse and acquisition

`seed_extension` first verifies the sealed western canary. A separate destination
retains the complete original release under `reuse/` and copies its acquisition
journals, metadata and accepted records into the working extension. The original
directory remains unchanged. Offline verification repeats parent checks and
requires all 225 inherited records to remain identical.

The 25 western core tiles keep their original halo requests. The first eastern
column overlaps the western edge by one native cell, allowing exact values and
masks to be checked across the join. The 25 new eastern cores require 225 new
rasters; no western layer is re-downloaded. The entire regional reference remains
available for comparison, now including the Heathrow intersections.

Budgets remain serial and cumulative across resumptions. The extended recipe
allows 600 total attempts and 6 GiB of charged response bodies **including the
inherited attempt history**. The 32 MiB response ceiling, three attempts per
request, one-second request interval and 10 GiB disk reserve are unchanged.
Archived parent copies consume additional disk; they are not additional transfers.

## Reproduction

Run with the documented project environment from the repository root:

```text
python scripts/33_tiled_canary.py --extend-from artifacts/tiled_canary_v1 --output artifacts/oxford_london_acquisition_v1
python scripts/33_tiled_canary.py --acquire --output artifacts/oxford_london_acquisition_v1
python scripts/33_tiled_canary.py --verify --output artifacts/oxford_london_acquisition_v1
python scripts/34_tiled_map.py --canary artifacts/oxford_london_acquisition_v1 --output artifacts/oxford_london_map_v1
python scripts/32_benchmark_regional_serving.py --pilot artifacts/oxford_london_map_v1 --output artifacts/oxford_london_benchmark.json
```

The seed command requires a new destination. Subsequent acquisition resumes the
existing checkpoint. Display construction supports `--resume` for an unsealed
destination; sealed releases are immutable. Both commands default to the original
regional evidence reference at `artifacts/source_regions_v2`.

The launcher prefers the expanded map when its sealed manifest exists; otherwise
it retains the previous release. A running server needs a restart to adopt a new
release. Existing versioned links disclose a release change; old saved comparisons
are not silently transferred across releases.

## Evidence limits

Geographic expansion does not fill unknown cells, resolve provider zero/domain
meanings, establish the aircraft reference period, or create an all-source total.
No new quietness bounds, event histories or claims of independent acoustic
validation are introduced. The sampled provider descriptions include every planned
core, but accepted raster responses and exact overlap checks are required before
publication. Data artifacts remain local and excluded from Git.

## Delivery results

Acquisition completed 27 September 2026 as `canary-858be9d7c0087e00e61b`.

| Acquisition check | Result |
|---|---:|
| Accepted source/indicator rasters | 450 (225 reused, 225 new) |
| Exact analytical overlap comparisons | 765 |
| Overlap source/indicator cells checked | 1,487,448 |
| Exact regional reference intersections | 144 |
| Regional reference source/indicator cells checked | 36,000,000 |
| Provider-rectangle exclusions | 0 |
| HTTP attempts, including inherited history | 490 / 600 |
| Charged response bytes, including inherited history | 3,842,468,342 / 6,442,450,944 |

The local source suite passed 508 tests (two platform skips, three opt-in checks
deselected); the added interrupted-seed check also passed in a targeted rerun.
All four Windows/Linux, Python 3.12/3.14 clean-checkout jobs passed for implementation
commit `11482b2`: [CI results](https://github.com/TannerL22/quiet-uk/actions/runs/36306214693).
The retained machine-readable CI result is `artifacts/oxford_london_ci.json`.

Map release `pilot-ad6d31f2da1b0a754135` is constructed and reproduced offline in
`artifacts/oxford_london_map_v1`. All 450 display images reproduce exactly and
2,447 evidence files verify. The 765 display joins checked 38,395,269 overlapping
display pixels, with zero gaps and zero double-opacity pixels. All 225 western
images also match the previous map release byte-for-byte.

Reports are retained in `artifacts/oxford_london_verification.json` and
`artifacts/oxford_london_western_display_preservation.json`.

### Serving measurement, 27 September 2026

The benchmark completed against the sealed expanded release with five concurrent
clients, 200 point requests and 200 three-place comparisons distributed across all
50 cores. The renderer and main app were stopped. Results are local, with OS file
caches not flushed, and do not establish public-server capacity.

| Measurement | Result |
|---|---:|
| Point median / p95 | 949.67 / 1,718.73 ms |
| Three-place comparison median / p95 | 1,439.67 / 1,818.69 ms |
| Image HTTP p95, all 450 responses hash-checked | 22.76 ms |
| Boundary / outside observations checked | 3,600 / 36 |
| Reader-cache entries | 48 |
| Source verification / private snapshot startup | 53.663 / 72.201 seconds |
| First / cached evidence export | 144.572 / 1.607 seconds |
| Evidence ZIP | 509,466,548 bytes |
| First-export Python allocation peak | 11,265,368 bytes |
| Process lifetime peak RSS | 397,864,960 bytes |
| Private snapshot and archive disk use | 5,904,219,505 bytes |
| Temporary snapshot cleanup | Passed |

Data-integrity and export checks passed, but **both interactive latency targets
failed** (point p95 under 500 ms; comparison p95 under 1,500 ms). Do not mark the
serving gate complete or discard this measurement in favour of a later faster
run. The previous smaller-release report has the same recorded processor, OS and
Python version, but the runs are not a controlled simultaneous comparison of load,
power state or filesystem cache. The follow-up profile and fix are recorded below.

Raw results: `artifacts/oxford_london_benchmark.json`; console log:
`artifacts/oxford_london_benchmark.log`.
Browser delivery was still outstanding at this initial measurement; the completed
launcher/browser check is recorded below.

### Serving bottleneck fix, 27 September 2026

Sequential profiles attributed most lookup time to repeatedly opening raster
files. With 450 rasters and a 48-reader cache, movement across the region evicted
readers before they could be reused. The pool also held its shared lock while
opening a file, unnecessarily serialising unrelated requests.

The revised pool reserves bounded slots under its lock, then closes/opens files
outside it. The default cap is now 512; this release opens all 450 readers during
snapshot startup. Only metadata is warmed, with no pixel-array preload. This
trades retained memory and startup work for substantially faster browsing. Larger
releases that exceed the cap continue to use lazy LRU eviction. Per-file integrity
checks, exclusive reader borrowing, failed-open recovery and shutdown cleanup
remain enforced. Cell geometry is reused within a request on identical grids;
raw values and masks remain independent source reads.

Intermediate diagnostics used the same 200-point/200-comparison, five-client
workload, with integrity/image/export phases explicitly omitted:

| Diagnostic | Point p95 | Comparison p95 |
|---|---:|---:|
| Concurrent file opens | 674.20 ms | 929.01 ms |
| Grid-geometry reuse and explicit GeoTIFF driver | 592.82 ms | 792.33 ms |
| Cached validated paths and request GDAL environment | 593.26 ms | 759.61 ms |
| Companion-file discovery experiment (not adopted) | 584.92 ms | 768.66 ms |
| 512-reader cap with startup metadata warming | 131.06 ms | 238.15 ms |

Reports remain separately retained as `artifacts/oxford_london_*_diagnostic.json`.
These are iterative diagnostics, not isolated causal estimates for each change.
The companion-file experiment did not materially help and is not enabled in the
implementation. Source/sidecar interpretation remains unchanged. Profiles can be
repeated using `scripts/36_profile_regional_lookup.py --output PATH`; their timings
include instrumentation overhead and are not HTTP latency results.

### Full serving verification, 28 September 2026

The complete workload passed on implementation commit `a55580a`, against the
unchanged release `pilot-ad6d31f2da1b0a754135`. No diagnostic omissions were enabled.
Tests and the main application were stopped during measurement. The client count,
request count, random seed and 50-core coverage match the failed baseline.

| Measurement | Before | After |
|---|---:|---:|
| Point median / p95 | 949.67 / 1,718.73 ms | 89.45 / 147.11 ms |
| Three-place comparison median / p95 | 1,439.67 / 1,818.69 ms | 170.59 / 217.70 ms |
| Image HTTP p95 (450 images) | 22.76 ms | 17.23 ms |
| Source verification | 53.663 s | 50.607 s |
| Snapshot startup, including reader warming | 72.201 s | 45.983 s |
| First / cached evidence export | 144.572 / 1.607 s | 120.035 / 0.929 s |
| Open reader entries / configured cap | 48 / 48 | 450 / 512 |
| Process lifetime peak RSS | 397,864,960 bytes | 1,480,028,160 bytes |
| First-export Python allocation peak | 11,265,368 bytes | 11,265,400 bytes |

Both latency targets pass. The full check preserved 3,600 boundary observations
(including native-cell polygons), 36 outside-coverage observations, all 450
image hashes and the 509,466,548-byte export. Temporary snapshot cleanup passed;
private snapshot plus archive disk use remains 5,904,219,505 bytes.

The tradeoff is material: retained GDAL pixel caches increase measured whole-process
peak memory to about **1.38 GiB**, versus 379 MiB before. Snapshot startup itself
peaked at 126 MiB; the point/comparison workload reached 1.23 GiB. The reader cap
does not bound GDAL block-cache memory. Source verification plus snapshot creation
still takes about **97 seconds**, and first evidence export about **two minutes**.
Startup, memory and export costs remain optimisation opportunities; these results
do not establish national-scale or public-hosting capacity. Measurements are from
separate local runs without OS-cache flushing, not a controlled speedup estimate.

Raw results: `artifacts/oxford_london_serving_fixed.json`; console log:
`artifacts/oxford_london_serving_fixed.log`. The failed baseline and all intermediate
diagnostics are retained. The local full suite initially had 511 passes, two skips
and one Windows-only test setup failure (trying to rename an already-open file).
After correcting that test setup, all 20 serving tests passed, with one symlink
privilege skip; production code was unchanged by that correction.

All four clean-checkout CI jobs passed for `a55580a` (Windows/Linux, Python
3.12/3.14): [verification run](https://github.com/TannerL22/quiet-uk/actions/runs/36389042539).
The retained result is `artifacts/oxford_london_serving_fix_ci.json`.

The existing one-click launcher successfully started the expanded release on
28 September. The app identity endpoint reported `pilot-ad6d31f2da1b0a754135`.
In-app browser smoke checks confirmed the 100 × 50 km / 5,000 km² map renders,
coordinate search selects Heathrow, road Lden returns 44.9 dB(A) with a separate
86.0 dB(A) aircraft cue, and switching to aircraft shows that aircraft value and
the unspecified-period notice. Returning to all-area coverage worked; no browser
console errors were captured. This is an automated smoke check, not another human
usability study or a new Firefox compatibility claim.

The expanded local delivery and serving gate are complete. Next performance work
should reduce verification/startup and retained GDAL cache memory before another
large geographic expansion; acquisition and scientific-evidence limitations remain
separate from this serving fix.
