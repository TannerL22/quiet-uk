# Quiet UK England dataset catalogue

Added 12 September 2026. This is a local, read-only catalogue for the existing historical England four-band tile dataset. The v2 scientific validation contract and v1 publication contract are not a public release, map frontend, web service, or completed mapping product.

## Build

The builder takes explicit source paths and writes only to the requested derived output directory. It does not call the runner, download sources, rewrite manifests, rebuild rasters, or mosaic tiles:

```text
.venv\Scripts\python.exe scripts\19_build_catalogue.py data\processed\england\tiles data\processed\england\tile_status_manifest.json notes\source_grid_reconciliation_audit\source_grid_reconciliation.json data\processed\england_mask\england_100m_mask.tif artifacts\england_catalogue_v3 --config config.json
```

The resulting files are `artifacts/england_catalogue_v3/catalogue.json` and `artifacts/england_catalogue_v3/catalogue.sqlite3`. The builder, using the v2 validation contract, independently recomputes source-grid acceptance from manifest raw geometry using the shared source-grid validator, and validates each four-band tile against the aligned land mask and existing semantic rules. The default is refusal when the output directory is non-empty; use `--overwrite` only when intentionally replacing that derived catalogue.

The SQLite database contains indexed numeric tile bounds, tile headers and checksums, source assessments, and per-band qualifications. A normal B-tree bounds index is sufficient for the 1,498 tiles; no spatial extension is used.

## Query

Coordinates are explicit: `--bng EASTING NORTHING` means EPSG:27700 easting/northing, while `--wgs84 LONGITUDE LATITUDE` means EPSG:4326 longitude/latitude.

```text
.venv\Scripts\python.exe scripts\20_lookup_location.py artifacts\england_catalogue_v3 data\processed\england\tiles data\processed\england_mask\england_100m_mask.tif --bng 414550 567550
```

The reusable `DatasetCatalogue`/`lookup_location` API performs coordinate validation and transformation, exact stored-cell indexing, one-cell land-mask checking, one-cell raster reads, and JSON-safe result construction. Tile ownership is `west <= x < east` and `south < y <= north`: shared vertical edges select the eastern tile, shared horizontal edges select the southern tile, and a four-tile junction selects the southeast tile. The outer west and north boundaries are included; outer east and south boundaries are outside coverage. There is no interpolation.

Tile checksums are verified on first access in a lookup session and cached only while the file identity/statistics remain unchanged. A changed size, modification identity, or checksum raises an integrity error instead of returning stale quality assessments. Queries open SQLite read-only and do not modify catalogue, source, manifest, or quality files. An older validation or publication contract is refused with rebuild guidance rather than silently treated as fully validated.

## Publication consistency

SQLite is the authoritative metadata snapshot for a published generation. The JSON file is a checked export: the reader parses it, validates its contract, and requires the complete parsed JSON object to equal the authoritative SQLite metadata object. Both include the same non-empty `build_id`, `catalogue_validation_contract_version: 2`, and `catalogue_publication_contract_version: 1`. Lookup results use the SQLite metadata after this check; JSON is never used as a second source of labels, thresholds, inputs, or reference years.

Each build is constructed under a unique temporary directory inside the output directory. The staged database is committed, closed, checked with SQLite `integrity_check`, checked for expected tables and counts, and checked for journal/WAL sidecars before it is eligible for publication. The JSON export is serialized and compared after parsing. Only then are the database and JSON replaced in that order. The output lock is acquired before checking whether overwrite is allowed and remains held through publication and temporary cleanup; its lock file is outside the output directory.

Construction or serialization failure leaves an existing published pair byte-for-byte unchanged. A database replacement failure also leaves the old pair unchanged. If JSON replacement fails after the database has been replaced, the build raises an incomplete-publication error and does not roll back: readers refuse the mixed build IDs, and a later explicit `--overwrite` rebuild recovers the output. Already-open readers retain their SQLite generation where the platform permits replacement; new readers validate the current pair. These guarantees cover the local staged workflow, not power loss or unsupported network-filesystem replacement semantics.

Pass `--diagnostic` only when raw stored values are specifically needed for investigation. Such values are labelled unqualified and retain the source-quality warning; ordinary lookup withholds values whose dependent source quality is rejected or missing.

## Bands and qualification

| Band | Meaning | Dependencies |
|---|---|---|
| `combined_reported_lower_db` | Reported road + rail + airport energy lower bound | Road, rail, airport |
| `road_rail_upper_db` | Conservative road + rail upper bound using configured censor floors | Road, rail |
| `airport_reported_lower_db` | Reported airport energy lower bound | Airport |
| `airport_reported_fraction` | Fraction of source cells with a reported airport value | Airport |

The bands are not a lower/upper pair for the same sources. In particular, `road_rail_upper_db` excludes airport noise, and no airport-inclusive `combined_upper_db` band is invented for the frozen four-band dataset. The 43.0103 dB road/rail floor is not presented as measured ambient noise.

Source states are kept separate from acoustic interpretation:

- `grid_accepted`: recorded response satisfies the current structural source-grid policy; this is not acoustic validation.
- `grid_rejected`: dependent bands are `withheld_due_to_source_quality` at tile level. This conservative tile rule does not prove every cell is wrong.
- `outside_declared_coverage`: explicit airport skip remains visible as a coverage limitation, not “no aircraft noise”. The road/rail upper band may remain qualified, and a zero airport fraction means no reported airport pixels, not silence.
- `missing_evidence`: dependent bands remain unknown/withheld.

The finite `-9999` sentinel is legitimate only for the two lower-bound bands where censoring is part of the frozen contract. On England land, `road_rail_upper_db` must be finite and non-sentinel, and `airport_reported_fraction` must be finite, non-sentinel, and within `[0,1]`. NaN, Infinity, invalid fraction values, and cross-band violations fail catalogue construction and selected-cell lookup, including diagnostic mode. Legitimate lower-bound nodata is returned as JSON `null` with a censored/unreported reason. Outside England land and outside catalogue coverage also return `null` with distinct reasons.

## Validation and source-grid reconciliation

The builder reads each tile in bounded tile-sized memory and reads the corresponding authoritative 100 m mask window only after checking CRS, resolution, and exact grid alignment. It reuses the existing production semantic validator for finite values, nodata placement, fraction range, the road/rail censor floor, and the frozen cross-band relationships. A matching checksum establishes byte identity only; it does not make semantically corrupt values valid.

For every manifest source record with raw geometry, the builder compares the report response fields with the manifest and calls the shared `validate_source_grid` policy with the manifest target grid, recorded WCS version, and declared bounds supplied through `--config` when available. The report's `accepted`/`rejected` label and supported policy identifier must agree with that recomputation. Explicit skips and missing raw evidence remain separate states and do not receive an invented grid.

## Real catalogue build

The v3 local build completed from the existing source files with:

- 1,498 tile records;
- publication build ID `304d0ae422a84bebb83bcb7a7379e236`, present in and matched between SQLite and JSON;
- EPSG:27700, 100 m output grid, 10 m source resolution;
- input SHA-256 records for the historical manifest, reconciliation report, England mask, and supplied production config;
- reference year marked `not_established` for road, rail, and airport because no year was supplied to this build. No year was inferred from coverage names.

Source quality counts:

| Source | `grid_accepted` | `grid_rejected` | `outside_declared_coverage` |
|---|---:|---:|---:|
| Road | 1,495 | 3 | 0 |
| Rail | 1,495 | 3 | 0 |
| Airport | 1,010 | 96 | 392 |

Band qualification counts:

| Band | Qualified | Qualified with coverage limitation | Withheld due to source quality |
|---|---:|---:|---:|
| `combined_reported_lower_db` | 1,010 | 389 | 99 |
| `road_rail_upper_db` | 1,495 | 0 | 3 |
| `airport_reported_lower_db` | 1,010 | 392 | 96 |
| `airport_reported_fraction` | 1,010 | 392 | 96 |

## Offline smoke queries

The following coordinates were selected from catalogue tile bounds and the supplied land mask. They were queried without network access:

| Check | BNG coordinate | Result |
|---|---|---|
| Ordinary accepted road/rail/airport cell | `414550, 567550` | `r0009c0033`; England land; all four bands qualified; combined lower value reported |
| Airport-rejected tile | `341650, 576450` | `r0008c0025`; airport-dependent bands withheld; road/rail upper remains qualified |
| Outside declared airport coverage | `391150, 649650` | `r0000c0030`; airport skip visible; road/rail upper remains qualified; zero airport fraction is labelled not silence |
| Outside England land | `382650, 657550` | `r0000c0030`; all bands null with outside-land status |
| Outside dataset coverage | `82599, 331450` | No owning tile; all bands null with no-dataset-coverage status |

## Historical and scientific limitations

The catalogue makes existing tile bytes and their quality evidence easier to inspect; it does not improve the underlying acoustics. Source-grid acceptance is structural compatibility only. The historical reconciliation still contains rejected/rescaled edge responses and explicit airport skips. Band withholding is intentionally conservative and tile-level.

The catalogue therefore does not make national redownloading ready. Another England-wide run still needs a justified provider/request strategy for the rejected road/rail and airport edge responses, with exact native-grid support and source semantics established before acceptance. No source-domain fix, broadened grid policy, national rerun, or independent acoustic validation was performed here.

## Verification

```text
.venv\Scripts\python.exe -m pytest -q tests\test_catalogue.py
40 passed, 1 skipped

.venv\Scripts\python.exe -m pytest -q
230 passed, 2 skipped in 20.43s

The skips are platform-specific: Windows may prohibit replacing an open SQLite database, and symbolic links are unavailable in one runner test in this Windows environment.

git diff --check
passed
```

Existing `data/`, `results/`, manifests, source rasters, and historical QA reports were not modified.
