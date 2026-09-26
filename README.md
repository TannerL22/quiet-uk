# Quiet UK — an explorer for quieter and louder places

Quiet UK is a general-purpose map for understanding mapped environmental noise.
Homes, walks, travel and research are possible uses of the same product.

**Current phase, 25 September 2026:** a working local 10 m regional explorer with
coverage outlines, source/time controls, place and coordinate search, keyboard
point inspection, saved comparisons and evidence downloads. It runs independently
of the optional historical England-wide 100 m overview. Wider UK noise coverage
is not yet implemented. This is an exploratory product, not an independently
validated research exposure release.

The **10 m explorer** contains source-linked data for four 10 × 10 km areas
around Heathrow, Didcot, Oxford and the Chilterns (400 km²). It separates road,
rail and aircraft with day/night/Lden controls, place search, point comparisons,
and a downloadable evidence bundle. [Regional release and verification](notes/SOURCE_REGIONAL_RELEASE.md).

The latest local interpretation separates zero-coded unknowns from missing cells,
keeps aircraft cutoffs explicitly unknown, and warns when aircraft evidence is
excluded from a road/rail view. Original source values are unchanged.
[Evidence meanings and verification](notes/EVIDENCE_SEMANTICS.md).

The [provider extraction audit](notes/PROVIDER_ENCODING_AND_EXTRACTION_AUDIT.md)
now replays offline from checked-in captures. Successful interior checks agree;
coverage-edge changes and unavailable protocol comparisons remain explicit.
Provider zero/domain definitions are still unresolved, so no additional quietness
bounds have been assigned.

In the 10 m explorer, select a point and **Add to comparison**. Save and name up
to three places, then **Compare places** to see all nine source/time indicators
side by side. Places persist in your browser; values are re-read from verified
originals. Download exact observations as CSV or JSON, or copy a release-specific
local comparison link. [Comparison behaviour and evidence](notes/PLACE_COMPARISON.md).

## Verify a source-only checkout

The default tests need no downloaded noise data or running app. In a fresh Python
3.12 or 3.14 virtual environment, run from the repository root:

```text
python -m pip install -e ".[dev]"
python -m pip check
python -m pytest -q -rs --strict-markers
```

Windows 3.14 also has an exact dependency lock. The default suite builds tiny
synthetic datasets and uses checked-in, byte-preserved Defra fixtures. Three
production-release checks are explicitly deselected; to run those against an
existing data checkout, use `python -m pytest --run-release-checks -m release_data`.
Missing release inputs then fail clearly rather than silently skipping.
See [verification setup and scope](notes/REPRODUCIBLE_ENVIRONMENT.md#source-only-verification).

## Launch the app

On Windows, double-click **Launch Quiet UK.cmd** in this repository folder
(the existing shortcut in the containing folder also works). It starts the local server in the background, waits
until it is ready, and opens your default browser. Repeated launches reuse the
running release. Startup logs are saved under `artifacts/launcher`.

From this directory on the existing Windows environment:

```powershell
.\.venv\Scripts\python.exe scripts\29_serve_explorer.py
```

Open **http://127.0.0.1:8766/**. The original 10 m regional map is the home
page when a regional release is installed. **Show all areas** reveals its coverage;
**Inspect map centre** supports keyboard use. The selected source/time value is
shown first, with the nine-indicator table under **All sources and time indicators**.
View links preserve the source, metric, point, camera, overlay visibility and release.

A fresh source checkout plus an extracted regional evidence ZIP is sufficient:

```text
python scripts/29_serve_explorer.py --regional-only --pilot PATH_TO_EXTRACTED_BUNDLE
```

Use the directory containing `manifest.json`. No historical catalogue, mask or
national raster is needed in this mode. The source checkout itself contains test
fixtures, not the regional scientific release: obtain the existing **Download data
& evidence** ZIP from a data-equipped installation, or follow the regional
acquisition instructions below. The normal launcher discovers the standard local
`source_regions_v2`, `source_regions_v1` or `source_pilot_v1` locations.

When all historical inputs are installed, normal startup also enables the
explicit **Historical overview** link at `/overview`. Its construction and Lden
meaning are labelled separately; returning restores the detailed selection.
Existing national links at `/#map=…` continue to open that overview when available.
With only historical data installed, the overview remains the home page.

Startup verifies existing releases; it no longer implicitly builds national data.
Use `--build-only` explicitly to build/verify a historical display from the original
catalogue, mask and tiles. Incomplete or incompatible installed releases are not
repaired silently. Use a new `--display` destination for a new display generation.

Regional startup makes a private, checksum-verified copy in the operating-system
temporary directory. The running app stays pinned to those bytes if the original
folder changes; restart to load another release. A maximum of 48 raster readers
are reused with exclusive access. Evidence ZIPs are built once on disk and streamed,
with at most two simultaneous downloads. Normal shutdown removes the private copy
and ZIP; forced termination may leave a `quiet-uk-serving-*` temporary directory.
Allow extra disk space for the copied release and its ZIP (about 328 MB for the
current regional release). Historical overview serving is unchanged.
See [serving isolation, measurements and limitations](notes/REGIONAL_SERVING.md).

Git tracks the application, tests, dependency lock and documentation. Downloaded
rasters, generated `artifacts/` (including catalogues, display assets and evidence
bundles), local configuration and Python environments remain outside Git. Updating
or cloning this repository is not a backup or restoration of those data products.
See [environment setup](notes/REPRODUCIBLE_ENVIRONMENT.md) and
[regional acquisition and verification](notes/SOURCE_REGIONAL_RELEASE.md) for
reproduction instructions.

The basemap needs internet access to OpenFreeMap. Submitted place searches use
OpenStreetMap Nominatim; coordinate searches and analytical lookups are local.
Try `51.464852, -0.447622` for a local coordinate lookup at Heathrow. Shared links preserve the
view and dataset version, but this loopback preview is accessible only on the
same computer. No public deployment has been made.

In the optional historical overview, **Together** overlays purple aircraft-presence hatching on
road/rail upper-bound colours. **Aircraft** shows its reported lower-bound colours;
**Road + rail** hides aircraft and explicitly warns about that omission. These
are separate historical evidence layers, not a validated numerical total. Road
and rail are still combined in the historical output. Display generation v4 uses
contract version 2 and does not overwrite earlier display generations.

| Present | Still ahead |
|---|---|
| England road/rail map and source-qualified point inspection | Wales, Scotland and Northern Ireland noise integration |
| Versioned assets, source-linked regional data and evidence exports | National source-linked construction and independent validation |
| Clear floor ties, withheld values and aircraft limitations | Defensible distinctions between below-threshold quiet places |
| Responsive explorer, saved three-place comparisons, evidence exports and local-view sharing | User-tested workflows, broader detailed coverage and optional context layers |

**Start here:** [Source views and temporal foundation](notes/SOURCE_VIEWS_AND_TEMPORAL_FOUNDATION.md),
[Original explorer implementation](notes/EXPLORER_PHASE1.md),
[research data contract](notes/RESEARCH_DATA_CONTRACT.md),
[active roadmap](notes/ACTIVE_ROADMAP.md).
The 18 September product review and older phase reports are historical evidence;
the active roadmap is the current delivery plan.

## Tiled acquisition canary

The separate 50 × 50 km Oxford–Reading–Chilterns workflow uses 25 overlapping
native-grid requests per source/indicator, fixed acquisition budgets, resumable
checkpoints and exact seam/reference comparisons. It retains failed attempts and
supports explicit recovery of damaged checkpoints without trusting lost evidence.
Run `python scripts/33_tiled_canary.py` to inspect the plan without networking.
See [canary status, verification and commands](notes/TILED_CANARY.md). The separate
[tiled map derivative](notes/TILED_MAP.md) integrates this area and Heathrow into
the explorer (2,600 km²). Build it offline with `python scripts/34_tiled_map.py`;
the launcher selects the verified derivative when installed. Source rasters and
unknown-value meanings remain unchanged.

## Existing scientific pipeline

A reproducible pipeline for building a fine-resolution map of **modelled anthropogenic environmental noise** in England using Defra Round 4 strategic noise mapping.

The Phase 1 England national output has been downloaded, masked and QA-validated. The current Phase 2C road-source work is a bounded research experiment for sub-threshold interpretation; it does not replace or modify the frozen Phase 1 national raster.

## Phase 1 objective

Produce a 100 m England grid with:

- road-noise Lden
- rail-noise Lden
- airport-noise Lden
- logarithmically combined sound-energy bounds
- source attribution where a source is reported
- an explicit censoring / confidence indicator
- a conservative quiet-place ranking suitable for identifying candidate low-noise zones

This is an **anthropogenic noise map**, not a total soundscape map. Wind, waves, birds, rivers, temporary construction, neighbours and other local sounds are outside Phase 1.

## Official England source data

Defra Round 4 strategic noise mapping:

- Road Noise — All Metrics — England Round 4  
  https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517
- Rail Noise — All Metrics — England Round 4  
  https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947
- Airport Noise — All Metrics — England Round 4  
  https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8

Road and rail are modelled on a 10 m grid at 4 m receptor height. For Lden, Defra applies a lower reporting cutoff of 40 dB. Airport data are also on a 10 m grid, but actual airport reporting thresholds can vary by competent authority.

Coordinate system: British National Grid, EPSG:27700.

## Why censored cells matter

A blank road-noise cell does **not** mean 0 dB. For Lden it generally means the modelled road contribution is below the 40 dB reporting threshold.

For example, if road, rail and airport sources are all reported only as `<40 dB`, we cannot say the combined level is 0 dB. If all three were just under 40 dB, their combined level could approach 44.8 dB.

The pipeline therefore carries **acoustic-energy bounds**:

- `combined_lower_db`: dB equivalent of acoustic energy that is definitely represented by reported source values
- `combined_upper_db`: conservative dB ceiling obtained by assigning each censored source its reporting-threshold energy

For a cell where every source is censored, the numerical lower bound is not usefully expressible in dB and remains nodata, while the upper bound remains finite.

### Important interpretation

`combined_lower_db` is a mathematical bound, **not an estimate of expected ambient noise**. It can become very low after spatial aggregation because censored fine-grid cells contribute zero *known* energy to the lower bound.

Therefore Phase 1 must **not** rank Britain's quietest locations using the lower bound alone. Candidate quiet zones should initially be ranked conservatively using:

1. lowest `combined_upper_db`,
2. censoring status / uncertainty,
3. size and contiguity of the low-noise area.

A later Phase 2 model can resolve the `<40 dB` region further using road distance/traffic, terrain, settlement, aviation and validation measurements.

## Recommended 100 m output fields

| Field | Meaning |
|---|---|
| `combined_lower_db` | energy-equivalent lower bound from reported source energy |
| `combined_upper_db` | conservative upper bound including censored-source threshold energy |
| `uncertainty_db` | upper minus lower where a numerical lower bound exists |
| `all_sources_below_threshold` | no source has a reported value in the fine-grid cell/block |
| `dominant_source` | road / rail / airport where source attribution is available |
| `quiet_candidate_rank` | later national rank based primarily on conservative upper bound |

Later versions can add access, terrain, roads, settlements, national parks, woodland, public transport and measured validation.

## Setup

Python 3.12+ is required by the current dependency family. The exact tested Windows environment uses Python 3.14.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

`requirements.txt` delegates to the runtime dependencies in `pyproject.toml`. For the
full test/development environment, install the package with `pip install -e ".[dev]"`.
The exact tested Windows CPython 3.14 x64 environment and its wheel verification
commands are recorded in `notes/REPRODUCIBLE_ENVIRONMENT.md`.

## One-command live pilot

```bash
cp config.example.json config.json
python scripts/04_run_pilot.py
```

On an internet-connected machine this performs:

1. WCS coverage discovery.
2. Heuristic selection of each Lden coverage.
3. Download of a 10 km × 10 km Heathrow / west-London pilot.
4. Raster metadata and alignment validation.
5. Logarithmic combination of road + rail + airport source energy.
6. Correct aggregation from 10 m to 100 m using acoustic energy rather than arithmetic dB averaging.
7. Export of 10 m diagnostic bounds and a 100 m three-band GeoTIFF.

The pilot bounding box is `[503000, 171000, 513000, 181000]` in EPSG:27700. It was selected to give a high probability of overlapping aviation, road and rail sources in one integration test.

## Why 100 m aggregation is done in energy space

Decibels cannot be arithmetically averaged. For fully observed values `L_i`, the energy-equivalent mean is:

`10 * log10(mean(10 ** (L_i / 10)))`

Censoring adds another wrinkle. A censored 10 m cell is **not dropped** from the 100 m lower-bound calculation. Its known lower acoustic energy is zero and it remains in the averaging denominator. Dropping censored cells with `nanmean` would systematically overstate the 100 m lower bound.

For quiet-place discovery, future national output should also retain an intrusion statistic such as the maximum or p90 fine-grid level within each 100 m cell so a mostly quiet block with one noisy road edge can be distinguished from a uniformly quiet block.

## Validation completed locally

The local test suite checks:

- logarithmic addition of equal dB sources (+3.01 dB for two equal sources),
- three-source combination,
- energy averaging vs arithmetic averaging,
- censored lower/upper bounds,
- correct treatment of censored fine cells during aggregation,
- Lden coverage-ID selection.

A synthetic three-source GeoTIFF integration test has also been run through the full combine → 100 m export path to verify raster dimensions, band descriptions, British National Grid georeferencing and affine scaling. Synthetic rasters are deliberately **not included** in this package so they cannot be mistaken for Defra observations.

The England Phase 1 production run has 1,498 complete 10 km tiles and 13,086,924 England land 100 m cells; see `notes/ENGLAND_NATIONAL_VALIDATION.md`. Phase 2C has a separate deterministic traffic-assignment, finite-line source-integration and bounded-model experiment over ten geographically varied regions; see `notes/PHASE2C_ROAD_SOURCE_INTEGRITY.md`. It remains research-only because most OS road links are imputed and the Defra targets are censored below threshold.

## Phase 1 England national build

The historical national tile build is complete. Its acquisition linkage remains
unresolved and its legacy manifest cannot be resumed by the hardened runner.
The following is the original intended pipeline, not a checklist of completed
release products:

1. tile the England extent,
2. download source rasters in manageable chunks,
3. validate grid alignment and nodata semantics,
4. combine energy bounds per tile,
5. aggregate to 100 m,
6. clip to England land,
7. mosaic to a Cloud Optimized GeoTIFF,
8. find contiguous low-`combined_upper_db` zones,
9. calculate conservative national quiet-candidate rankings,
10. publish web tiles / PMTiles in MapLibre.

The actual frozen national product has four bands: combined reported lower,
road/rail upper, airport reported lower, and airport reported fraction. It has
no all-source upper bound or national quietness rank. The current explorer uses
a separate qualified energy-display raster and bounded raster tiles; it does
not use the old full-memory mosaic helper. Do not start a national Phase 2 road
reconstruction from research outputs without independent sub-threshold validation.

## Phase 2: resolving the truly quiet places

The official source maps are intentionally censored below their reporting thresholds. To distinguish, for example, a likely 39 dB area from a likely 25 dB remote area, Phase 2 should build a sub-threshold model using predictors such as:

- distance to roads by class,
- traffic volume and speed,
- HGV share,
- distance/service intensity for rail,
- airport/flight-path exposure,
- terrain shielding,
- settlement and population density,
- industrial sources,
- land cover.

That second-stage model should be calibrated/validated against field measurements where suitable data exist.

## Data licence

Preserve attribution and licence metadata for each source. Defra identifies these datasets under the Open Government Licence with Crown/Defra attribution.
