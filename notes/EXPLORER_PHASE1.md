# Quiet UK explorer — completed phase

21 September 2026. A general-purpose local England noise explorer now connects
geographic browsing to the existing qualified analytical catalogue. It does not
specialise the product around homes or hiking. The original scientific rasters,
historical manifests and catalogue contents remain unchanged.

## Launch

Run from the repository directory:

```powershell
.\.venv\Scripts\python.exe scripts\29_serve_explorer.py
```

Open **http://127.0.0.1:8766/**. Use `--port` to choose another loopback port.
The command creates `artifacts/explorer_display_v2` only when it does not exist;
otherwise it checks the generation's recorded hashes. To construct a separate
generation, pass `--display artifacts\your_new_generation`. Existing destinations
are never overwritten. `--build-only` builds/verifies without serving.

The original catalogue, tile root and England mask are required. Override them
using `--catalogue`, `--tiles`, and `--mask`. The output is an exploratory display
product, not a replacement national acoustic release.

## Delivered behaviour

- England-wide pan/zoom map with OpenFreeMap geography and labelled places.
- Continuous road/rail display using discrete, stated bound ranges; layer toggle
  and opacity control. Zooming in reveals the original 100 m granularity.
- Submitted town/postcode search through OpenStreetMap Nominatim, plus local
  `latitude, longitude` entry. No autocomplete requests or reverse-geocoding calls.
- Click or inspect the map centre for qualified analytical values and the exact
  cell footprint. A keyboard-accessible search and inspection flow accompanies
  the map; this is not a formal accessibility certification.
- Floor ties described as below reporting detail; withheld and out-of-coverage
  values do not become noise readings. Aviation evidence stays separate.
- Location evidence and dataset-record JSON downloads, with a plain JSON view
  link for location evidence. Server attachment responses use actual files' data,
  not screenshot colours or a rounded UI number.
- View links retain map position/zoom, selection, visibility, opacity and dataset
  version. Opening a mismatched-generation link shows a notice. Links currently
  work only on the computer running the local server.
- A narrow layout places search and map first, with location evidence below.
- Methods/provenance dialog and a research contract that explicitly identifies
  the historical baseline as provisional.

## Data construction

`quiet_uk.explorer.publish_display` checks every source tile against the catalogue
checksum and production semantic rules, applies authoritative land support and
band qualification, and publishes two tiled BNG rasters in a new generation:

| File | Role |
|---|---|
| `energy.tif` | Relative energy of qualified road/rail upper bounds, with energy-space mean overviews and zero reserved for nodata |
| `quality.tif` | Outside = 0, qualified = 1, withheld/uncovered land = 2; maximum resampling during display warp preserves withheld support |
| `dataset.json` | Input/catalogue identities, display version, code/runtime identity, output hashes, field dictionary, reference-year states, interpretation and limitations |
| `publisher_source.py` | Snapshot of the display publisher corresponding to its recorded hash |

The server projects these derivatives into 256-pixel XYZ PNG tiles on demand and
uses a bounded in-memory cache. It opens raster handles independently for each
request. The rasters are local tiled GeoTIFFs with analytical energy overviews;
no public COG/PMTiles distribution or hosting is claimed. Outside/withheld support
is excluded from energy means, so these visual summaries must not be used as
whole-area exposure estimates. Exact point values come from the existing catalogue.

The release used for final verification is `explorer-01ba5d8ada35910e9ead`, tied to
catalogue build `304d0ae422a84bebb83bcb7a7379e236`. The initial internal display
draft remains in `artifacts/explorer_display_v1`; the launcher uses v2.

Windows note: publication now inherits workspace ACLs rather than preserving the
private ACL created by `tempfile.mkdtemp`. The previously generated v3 catalogue
and initial display draft had owner-only file ACLs; these were reset to their
existing parent/workspace inheritance so the normal user can read them. File
contents were not changed, and recorded checksum checks still pass.

## Verification

Final full source-tree run:

```text
.venv\Scripts\python.exe -m pytest -q -rs
364 passed, 2 skipped in 55.39 seconds
```

The two skips are existing Windows constraints: replacing an open SQLite database
and unavailable symbolic-link creation. Nineteen explorer tests cover energy-space
overview semantics, withheld/sea distinctions, exact export linkage, tamper
rejection, publication refusal, coordinate/tile validation, PNG output, HTTP
attachment responses, route/Host/Origin restrictions and search caching.
`node --check explorer/app.js` also passes.

Live checks in the Codex browser:

| Check | Observed result |
|---|---|
| Initial regional view | Geographic labels and noise patterns visible around the Peak District |
| Inspect map centre | Qualified location details returned for 53.256, -1.783 |
| Town search | Bakewell result selected; map and location panel updated |
| Postcode search | Public postcode SW1A 1AA resolved; map and details updated |
| Shared view | Copy control acknowledged; reload restored position and selected coordinate |
| Dataset mismatch | Old-generation link showed a notice after switching to final display generation |
| Outside England | 55.9533, -3.1883 displayed evidence unavailable rather than quiet |
| Reporting-floor tie | 55.740155, -2.142518 displayed below reporting detail and its limitation |
| Narrow layout | Inspected at 390 × 844 CSS pixels; map visible before location card, no horizontal document overflow |
| Data explanation | Dialog inspected on the narrow layout; provenance and research limitations readable |
| Browser console | No error/warning entries in the inspected live session |

Real loopback HTTP observations on this machine: dataset metadata approximately
46 ms; point lookup approximately 49 ms; JSON location attachment approximately
59 ms; out-of-coverage lookup approximately 13 ms. These are individual observed
requests, not a load test, percentile guarantee or internet-hosting benchmark.

JSON attachment endpoints were verified with HTTP 200, correct attachment headers
and expected qualified data. The in-app automation did not expose a download-manager
event, so native file-save UI completion is not claimed. Downloads use ordinary
HTTP attachment links and the location also offers an inspectable JSON URL.

## External services and limitations

MapLibre GL JS 5.24.0 is vendored with its licence and a checksum/source manifest
under `explorer/vendor`. The map uses OpenFreeMap's Positron style and displays
provider attribution. Geographic tiles/fonts require internet access; if the
style cannot load, the UI explains the missing basemap rather than pretending
geographic context is available.

Nominatim searches are explicit submissions, UK-restricted, cached and globally
limited to at most one uncached request start per 1.05 seconds within this process.
Requests have an identifying User-Agent and timeouts. `--geocoder` permits replacing
the service before wider distribution. Search failures have an actionable message;
coordinate entry remains local. No analytics, accounts or query/coordinate logs
are added. The HTTP service binds only to loopback and exposes an asset allowlist.
It is a local preview, not a hardened public multiuser service.

The public dataset's original source linkage and periods remain unresolved.
England is the only noise coverage. Independent acoustic validation, comparisons,
bookmarks, arbitrary-area/route research exports and wider UK integration remain
future phases. The UI does not claim to measure a home's interior, predict stress,
provide causal estimates, determine access rights or identify the UK's quietest place.

The next milestone is the source-linked canary and release evidence defined in
`RESEARCH_DATA_CONTRACT.md` and `ACTIVE_ROADMAP.md`. It should be bounded and should
not replace user testing of the now-working explorer.
