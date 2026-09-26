# Tiled map integration

The explorer now supports one continuous 50 × 50 km Oxford–Reading–Chilterns
area plus the existing 10 × 10 km Heathrow area: 2,600 km², with road, rail and
aircraft available separately for Lden, Lday and Lnight. The 25 acquisition tiles
are implementation details, not 25 destinations in the area selector. Search,
point inspection, comparisons, saved views and evidence downloads use the same
release. The default new view shows road Lden across the wider area; saved source
and metric choices still take precedence.

## Delivered checkpoint: 26 September 2026

Installed local release: `pilot-be13e2d6e9145df58aa6` in
`artifacts/tiled_map_v2`. The one-click launcher is running this release.

| Verification or measurement | Result |
|---|---:|
| Reproduced display images | 234 of 234 |
| Hashed release files | 1,147 |
| Exact analytical seams / original reference cells | 360 / 27,000,000 |
| Display seams / overlapping display pixels checked | 360 / 13,491,864 |
| Display gaps / double-opacity pixels | 0 / 0 |
| Boundary / outside source-indicator observations | 1,872 / 72 |
| Point HTTP p95, 200 requests, five clients | 372.38 ms |
| Three-place HTTP p95, 200 requests, five clients | 345.74 ms |
| Display HTTP p95, all 234 responses hash-checked | 24.33 ms |
| Private snapshot startup, after source verification | 7.94 s |
| Evidence bytes / first download archive | 2,028,093,907 / 166,242,473 bytes |
| First / cached export | 16.69 / 0.23 s |
| Export Python allocation peak / process peak RSS | 5,931,550 / 407,691,264 bytes |
| Open reader cache / private-copy cleanup | 48 / passed |

The final benchmark ran after construction, replay and browser interactions had
finished, with the app idle. Both local targets passed (point p95 under 500 ms,
three-place p95 under 1,500 ms). An earlier exploratory run during other verification
work missed both targets; retain `artifacts/tiled_map_benchmark.json` as evidence
of workload sensitivity, rather than treating the final figures as hard bounds.
The workloads have different cache reuse patterns; comparison time is not a
linear multiple of point time.

Local reports: `artifacts/tiled_map_v2_verification.json`,
`artifacts/tiled_map_v2_benchmark.json` and `artifacts/tiled_map_v2_display_seams.json`.
The complete local suite passed 506 tests, with two Windows skips and three
opt-in release checks deselected. Subsequent targeted tests cover the seam gate,
resumption and shuffled recovery records; the
[Windows/Linux Python 3.12/3.14 CI matrix passed all four jobs](https://github.com/TannerL22/quiet-uk/actions/runs/36257431463)
for implementation commit `51a4a5f`.

Browser checks used 1440 × 1000 and 390 × 844 viewports. They covered continuous
coverage, coordinate search, rail/night selection, Heathrow aircraft (86.0 dB Lden
at the retained sample), its road-view warning, cross-area comparison, outside
coverage, keyboard controls and saved metric/camera reload. The narrow page had
no horizontal overflow; browser error logs were empty. Test comparison points
were cleared and the viewport override was reset. The desktop screenshot is
`artifacts/tiled_map_desktop.png`. These are functional checks, not participant
usability sessions.

## Analytical ownership and evidence

The map release is a separate immutable schema-3 derivative. It contains the
entire sealed canary and regional parent, including unsuccessful acquisition
attempts, recovery evidence and construction sources. The parents are copied and
hashed; analytical rasters are not resampled, trimmed or overwritten. Parent
identities, complete layer membership, tile cores, source/period compatibility and
record evidence are reconstructed when opening the release.

A point belongs to one core using native raster conventions: west and north are
included, east and south are excluded. Lookup selects that core's nine layers;
acquisition halos are never additional point observations. Outside points return
nine explicitly unavailable observations. Exports retain the original raster's
row/column, checksum, request evidence and native-cell geometry. The metadata
continues to distinguish zero codes, TIFF nodata and outside coverage. Aircraft
periods and unknown quietness bounds remain unresolved; no all-source total is
introduced.

## Display construction

Each tile uses the same globally aligned 10 m Web Mercator display lattice.
Interior pixels use GDAL nearest-cell sampling. A belt around core edges is
inverse-projected with vectorized `pyproj`, then assigned to the containing native
cell and core. This avoids projecting millions of unaffected interior pixels.
Full-size checks independently project shared strips with GDAL and require no
gaps or double opacity before the manifest can be published. This is display-only
nearest-cell sampling; Mercator
metres are not a claim of ground-level resolution or model accuracy. Hatching
uses global pixel coordinates so its phase does not restart at tile edges.

The initial local derivative (`tiled_map_v1`, `pilot-82b07c7cfca57cf3d553`) failed
the full-size alpha seam check despite passing a small synthetic mosaic check.
Independent GDAL warps disagreed at one aircraft boundary. It is retained for
diagnosis and is not selected by the launcher. The corrected derivative is
`artifacts/tiled_map_v2`, using exact inverse pixel-centre sampling at core edges. Neither
attempt changes the sealed analytical canary.

The browser draws all 25 core images for the selected wider area, with a single
outer outline. Point clicks reuse existing map layers; source/metric/area changes
replace them. Analytical point values are always read from native rasters, never
from the PNG. Heathrow remains selectable and retains the aircraft warning in
road/rail views.

## Reproduction

```text
# Build a new derivative offline; refuses an existing destination.
python scripts/34_tiled_map.py --output artifacts/tiled_map_v2

# Resume an interrupted, unsealed build: recheck parents, regenerate displays.
python scripts/34_tiled_map.py --resume --output artifacts/tiled_map_v2

# Replay parent extraction checks and every display image offline.
python scripts/34_tiled_map.py --verify --output artifacts/tiled_map_v2

# Check real display boundaries for gaps and double opacity.
python scripts/35_check_display_seams.py --pilot artifacts/tiled_map_v2

# Benchmark every core and check boundary samples through the serving reader.
python scripts/32_benchmark_regional_serving.py --pilot artifacts/tiled_map_v2 --output artifacts/tiled_map_v2_benchmark.json

# Serve independently of the historical overview.
python scripts/29_serve_explorer.py --regional-only --pilot artifacts/tiled_map_v2
```

The existing one-click launcher prefers the corrected tiled release when its
manifest is installed. Earlier regional releases remain usable with `--pilot`.
Old links identify a different release and receive a visible version warning;
saved comparisons must be reselected against current evidence. The portable ZIP
contains both parents for offline replay without the acquisition directories.
Generated releases remain outside Git; a source push is not a data backup.

## Verification scope

Source-only regressions cover four-way ownership, outer edges, halos, zero/nodata
semantics, native-cell preservation, display mosaic equality, portable ZIP replay,
lineage tampering, image and point HTTP routes, comparison CSV, and serving from a
private snapshot after a source file changes. The benchmark visits all 26 cores,
checks 1,872 source/indicator boundary observations, checks every image response
against its hash, and measures 200 point and 200 three-place requests at five
concurrent clients. It also measures first/cached evidence exports and snapshot
cleanup with the existing 48-reader and two-download limits.

Loopback timing, warm filesystem caches and one Windows machine do not establish
public-hosting capacity. Python allocation peaks exclude GDAL/native memory; whole
process RSS is recorded separately. Browser operation is not participant
comprehension or independent acoustic validation.

## Next boundary

Use the wider map in the existing five-person usability protocol before expanding
geography again. Collect evidence about coverage comprehension, unknown values,
source switching and aircraft warnings. Provider zero/domain clarification and
aircraft period/threshold evidence remain separate scientific gates. A public
beta also needs operating limits, release distribution/backup, attribution and a
rollback path; this local increment does not claim those are complete.
