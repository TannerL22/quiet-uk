# Comparing places with the original 10 m evidence

Delivered 24 September 2026. The data release remains
`pilot-d0aa536e7c93d05b09fe`: four 10 × 10 km areas, 400 km². This increment adds
an exploration and research-export workflow; it does not acquire new rasters,
increase spatial resolution, or establish independent acoustic accuracy.

## Use

Open the launcher, then **10 m explorer**. Inspect a point and **Add to
comparison**. Repeat for up to three points, including points in different
preview areas. Edit their names under **Your places**, use **Show** to return to
one, and open **Compare places**. A/B/C markers correspond to the table columns.
Remove individual points or clear the selection when finished.

The table shows road, rail and aircraft separately for Lden, day and night.
The map's current indicator is highlighted; its differences from A are shown
below the table. Source averages are never added into an overall sound level.
The displayed numbers describe exact native cells, not area or town averages.
Several points can fall in one native cell; a permitted difference then explicitly
reports that shared cell. The interface rounds to one decimal place. Exported
values are not rounded.

## Scientific boundaries

- Differences are place minus A, within a single source and indicator, only when
  both values are reported and their declared periods, coverage identifiers and
  units match. Road/rail currently reference 2021. Aircraft values remain visible
  but no difference is calculated while their reference period is unspecified.
- A missing value stays null with its original sentinel and status. Outside
  coverage is distinguished from an unreported in-domain cell. Neither implies
  quietness; no overall ranking, background noise or event history is inferred.
- These are differences between model outputs. Small differences do not establish
  perceptible or statistically significant differences; provider cell-level
  uncertainty is unavailable. Independent validation remains necessary.

## Persistence and exports

Browser storage and URL fragments contain only a bounded recipe: release ID,
site, exact longitude/latitude and a name of at most 60 characters. They contain
no cached noise values. Names/coordinates are local until a user shares a link;
the lookup/export requests go to the loopback server. Links require that same
data release to be served on the receiving computer. A changed release refuses
to restore old comparisons automatically. Renaming does not change coordinates.

The API is `GET /api/pilot/comparison?release=...&places=...`, with the place
list encoded as JSON. Downloads use `/downloads/pilot-comparison.json` and
`/downloads/pilot-comparison.csv` with the same arguments. Every response reads
and checksum-verifies original rasters; stale releases and tampered files fail.
Requests are limited to one–three places and a 2,500-character recipe. Existing
loopback Host/Origin checks and no-search/coordinate logging policy apply.

JSON contains the exact per-point observations, raw values, missingness statuses,
native cell geometries, source SHA-256 hashes, request-record paths, coverage IDs,
declared periods, comparison qualification, product metadata and attribution.
CSV has one row per point/source/indicator (27 rows for three places). Null
numeric fields are blank with explicit status columns; formula-like user names
are prefixed with an apostrophe for spreadsheet safety. JSON retains names
verbatim. Both include resolution, receptor height, cutoffs and uncertainty.
The separate **Download data & evidence** ZIP remains the way to obtain the
original rasters and complete acquisition snapshots.

## Verification

Regression coverage checks exact differences and exports, source-period
incompatibility, aircraft period missingness, zero/nodata, outside coverage,
shared native cells, duplicate/oversized inputs, release mismatch, post-load
tampering, CSV quoting and formula safety, and HTTP download/Origin behaviour.
Browser verification exercises three regions, naming/removal, marker navigation,
reloading saved recipes, source/time focus and responsive comparison layouts.

Completed checks: **437 passed, 2 existing platform skips**, in the isolated
Windows environment. An initial suite run encountered Windows connection-abort
error 10053 in the existing screening API Host/Origin test; it passed immediately
in isolation and the subsequent complete suite passed (112.61 s). No test was
disabled or weakened. The comparison/source/explorer subset passed all 74 tests.
Live comparison JSON matched all 27 native observations exactly; CSV preserved
all 16 unreported observations as blank values with statuses. Local evidence is
under `artifacts/place_comparison_verification_v1`.

Browser checks passed at 1366 × 900 and 390 × 844. The phone table scrolls inside
its own container with no horizontal page overflow. Three-place limits, renaming,
removal, local-storage restoration, copy-link/fragment restoration and rejection
of old-release links were exercised. Navigation to another fragment in the same
tab now reloads and revalidates it. No browser console errors were observed.

Run the full suite with:

```powershell
.\.venv-regional-check\Scripts\python.exe -m pytest -q
```

Next: observe real users comparing places, then expand native 10 m geographic
coverage with bounded tiled storage/acquisition. This feature does not claim to
complete user testing or scientific validation.
