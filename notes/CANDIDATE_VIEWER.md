# Local candidate-output viewer

The viewer is a read-only inspection surface for already-published candidate
outputs. It does not run extraction, acquire data, change catalogue inputs,
create an excluded-cell layer, add a basemap, or host the application.

## Coordinate and snapshot contract

The published `candidates.geojson` remains unchanged in WGS84 (`EPSG:4326`).
At server startup, the validated JSON/GeoJSON pair is transformed through the
installed Rasterio/PROJ machinery into an in-memory viewer-only payload at
`/data/candidates-bng.json` (`EPSG:27700`, with `source_crs: EPSG:4326`). The
derived payload carries the same run ID, schema version, screening-policy
version, ordered component IDs, requested `bbox_bng`, and representative BNG
points as `candidates.json`.

Candidate data are read, validated, transformed, and serialized once at
startup. The three data endpoints then serve immutable bytes from that
snapshot, so changing the source files while the server is running cannot mix
generations. Restart the server to inspect a different pair. By default an
invalid pair prevents startup; `--serve-invalid` is available for exercising
the browser's clear error state without serving invalid data.

The browser draws every spatial layer in BNG: the requested rectangle comes
directly from `bbox_bng`, footprints come from the derived BNG geometry, and
representative points come from the derived BNG properties. One shared
pixels-per-metre scale is calculated from the requested BNG extent and the
available viewport. Content is centred with padding, northing is inverted for
screen coordinates, and the same transform is used for the rectangle,
footprints, points, selection styling, resize handling, north indicator, and
metric scale bar. Display geometry is not used to calculate land area; the
details panel continues to show the recorded cell-footprint area.

## Start locally

From the repository root:

```powershell
.\.venv\Scripts\python.exe scripts\24_serve_candidate_viewer.py
```

Open `http://127.0.0.1:8765/`. To inspect another existing pair, pass
`--json` and `--geojson` paths explicitly. The server exposes only the viewer
assets and designated candidate data endpoints; it does not expose arbitrary
repository paths.

## Large-input behaviour

Validation uses Set/Map identity checks and finite-value validation. Browser
bounds are computed by iteration rather than argument-spreading coordinate
arrays. The list is paginated at 50 entries while the full component lookup
and area order remain available. Footprints are rendered in batches of 250
for larger results, yielding between batches and showing progress; components
are not silently omitted.

The measured fragmented regression contains 40,000 components and 200,000
coordinate points. It rendered 40,000 footprint paths with 50 list buttons in
the DOM, reached page 800 of 800, and selected `synthetic-40000` with matching
map, list, and detail identities.

## Browser verification

The development-only browser harness is separate from the application
dependencies. Install its ignored tooling and run it after starting the
four local test servers described by the harness:

```powershell
npm install --no-save --prefix .browser-verify playwright-core
node scripts\26_verify_candidate_viewer_browser.cjs
```

The final run used Chromium/Chrome `146.0.7680.165`, with 1400×1000 and
620×900 viewports. It verified all 128 pilot components, list-to-map and
map-to-list selection, selected fill/stroke, polygon holes, pagination (50,
50, 28), equal-scale BNG geometry at both viewports, resize selection
preservation, empty results, mismatched run IDs, and the fragmented large
case. No unexpected console errors or external requests remained; the three
HTTP 422 console messages were the expected negative-case mismatch loads.

Evidence is recorded in
`artifacts/candidate_viewer_verification_v1/`, including
`browser_verification.json`, `browser_verification.md`, and screenshots for
the wide, narrow, empty, mismatched, and fragmented-large cases.

## Remaining limitations

This is a local inspection tool with no basemap or provider integration. It
shows retained candidate footprints and recorded details, not a complete
excluded-cell explanation. Very large results still produce one SVG path per
footprint, so rendering remains bounded by the browser's SVG capacity even
though list DOM growth and coordinate-bound calculations are bounded. The
viewer-only BNG snapshot is deliberately ephemeral and is regenerated on
restart.
