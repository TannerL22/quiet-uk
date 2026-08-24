# Quiet UK - England National Output QA

Date: 2026-08-24  
QA command: `python scripts/13_validate_england.py --config config.json --output-root data/processed/england --manifest data/processed/england/tile_status_manifest.json --qa-root data/processed/england/qa`

## Result

The national four-band England dataset passes the technical integrity audit.

The production run initially ended with 1,487 complete tiles and 11 transiently failed tiles. Only those 11 were retried with the existing `--failed-only` runner; all subsequently completed and passed output validation. No valid completed tile was redownloaded.

## National completion and grid integrity

| Check | Result |
|---|---:|
| Expected scheduled England tiles | 1,498 |
| Manifest scheduled tiles | 1,498 |
| Validated complete output tiles | 1,498 |
| Missing output files | 0 |
| Unexpected output files | 0 |
| Duplicate tile IDs/files | 0 |
| Pending/running/failed manifest entries | 0 |
| Staged `.attempt-*` files | 0 |
| Temporary 10 m directories remaining | 0 |
| Shared horizontal edges checked | 1,418 |
| Shared vertical edges checked | 1,422 |
| Structural gap edges | 0 |
| Structural overlap edges | 0 |
| Misaligned tiles | 0 |
| Tile CRS | EPSG:27700 for every tile |

Every output passed `validate_tile_output()`. Tile transforms are exact 100 m core-grid transforms and the deterministic land-filtered schedule matches the manifest exactly.

## England land-mask reconciliation

The official ONS-derived 100 m England mask contains **13,086,924** land cells. The national tile products contain exactly **13,086,924** land cells in total, so every England land cell is represented once and only once.

There are 1,857,776 scheduled product cells outside the England land mask. All of these cells are `-9999` nodata in every acoustic band. No internal land gaps were found. The mask uses the retained 20 m generalised, coastline-clipped ONS boundary and the existing any-20 m-subcell land rule for 100 m cells.

## National band statistics

Statistics are calculated over England land cells only. Nodata counts below therefore refer to England land cells, not the rectangular tile-product sea/border cells. The complete machine-readable table is `data/processed/england/qa/national_band_statistics.csv`.

| Band | Valid | Nodata | Min | P01 | P05 | P10 | P25 | Median | P75 | P90 | P95 | P99 | Max | Mean |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| combined_reported_lower_db | 8,070,572 | 5,016,352 | 20.000 | 23.230 | 32.848 | 37.951 | 42.786 | 48.500 | 54.222 | 60.272 | 64.735 | 71.756 | 93.069 | 48.603 |
| road_rail_upper_db | 13,086,924 | 0 | 43.010 | 43.010 | 43.010 | 43.010 | 43.010 | 43.960 | 51.024 | 56.702 | 61.797 | 69.589 | 88.011 | 47.623 |
| airport_reported_lower_db | 166,941 | 12,919,983 | 20.000 | 33.987 | 40.416 | 41.200 | 43.624 | 48.778 | 52.826 | 57.750 | 61.309 | 69.459 | 93.069 | 48.958 |
| airport_reported_fraction | 13,086,924 | 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 1.000 | 1.000 | 0.012 |

The `road_rail_upper_db` minimum of 43.010 dB is the expected energy-equivalent value for two censored 40 dB source contributions. No valid acoustic band contained 0 dB, infinity, or an accidental sentinel value.

### Airport reported fraction

| Criterion | Cells | Share of England land |
|---|---:|---:|
| 0 | 12,919,983 | 98.7244% |
| >0 | 166,941 | 1.2756% |
| >=0.25 | 164,032 | 1.2534% |
| >=0.50 | 162,065 | 1.2384% |
| >=0.75 | 159,979 | 1.2224% |
| 1.0 | 156,981 | 1.1995% |

All airport fractions are in `[0, 1]`. Where the fraction is zero, the airport lower-bound band is nodata; no zero-fraction cell contains a reported airport value.

## Cell-level checks and geographic plausibility

All of the following passed nationally:

- `combined_reported_lower_db >= airport_reported_lower_db` wherever both are finite;
- all cells outside England are nodata in all four bands;
- no non-finite values occur in valid outputs;
- no valid acoustic 0 dB values occur;
- `road_rail_upper_db` respects the existing two-source 40 dB censoring floor;
- airport reported fraction is bounded and internally consistent.

The road/rail map shows coherent major-road, motorway, urban and rail-corridor structure across England. The combined reported-lower map shows stronger metropolitan and transport corridors, with white/nodata regions where no fine-grid source energy is reported; those gaps are expected censoring behaviour, not missing raster tiles. The airport-fraction map shows compact airport-shaped footprints rather than tile-shaped artefacts. No artificial tile boundary or CRS offset is visible in the fixed-scale diagnostics.

The 20 lowest finite combined lower-bound cells are 20 dB and cluster in sparse reported-energy areas, including the northeast/Scotland-border part of the schedule. They are explicitly QA points, not claims about the quietest places. The lowest road/rail upper-bound cells are the expected 43.010 dB censor-floor values. The high combined, road/rail and airport points are geographically plausible strong reported-source cells; all 100 requested extreme records, with BNG coordinates and WGS84 latitude/longitude, are in `data/processed/england/qa/national_extremes.csv`.

## Retries and service errors

Historical resolved errors remain in both the manifest and `data/processed/england/tile_errors.log`:

- 328 tiles required more than one total attempt;
- maximum total attempts for a tile: 8;
- 483 recorded errors, matching 483 error-log lines;
- 466 HTTP 504 gateway timeouts;
- 8 HTTP 500 internal server errors;
- 7 connection errors and 2 chunked-encoding/connection-reset errors;
- all errors eventually resolved; 0 unresolved failed tiles.

Retry tiles span tile rows 7-64 and columns 0-56, with a broad geographic distribution rather than a single airport or regional data-family cluster. The retry map is retained as `data/processed/england/qa/national_retry_tiles.png`.

## QA implementation and outputs

Added:

- reusable windowed QA logic in `src/quiet_uk/national_qa.py`;
- command-line entry point `scripts/13_validate_england.py`;
- targeted tests in `tests/test_national_qa.py`;
- `matplotlib` in `requirements.txt` for reproducible static diagnostics.

The QA pass reads one 100 m tile at a time. Exact percentiles are computed from disk-backed temporary value arrays rather than loading the national raster into RAM. Temporary QA work files are removed after a successful run.

Outputs under `data/processed/england/qa/`:

- `national_qa_summary.json`
- `national_band_statistics.csv`
- `national_histograms.csv`
- `airport_fraction_summary.csv`
- `national_extremes.csv`
- `tile_qa_summary.csv`
- `tile_retry_summary.csv`
- `national_road_rail_upper_db.png`
- `national_combined_reported_lower_db.png`
- `national_airport_reported_fraction.png`
- `national_distributions.png`
- `national_retry_tiles.png`

The full test suite passes: **23 tests**.

## Methodological limitation

This PASS is a statement about pipeline and data integrity. It is not a claim that the lowest lower-bound values estimate the true quietest locations.

The Defra strategic maps censor unreported values below their reporting thresholds. `combined_reported_lower_db` contains only reported source energy; censored fine cells contribute zero known lower energy during correct energy-space aggregation. `road_rail_upper_db` is a conservative road+rail ceiling using the known 40 dB road/rail reporting threshold. Airport reporting behaviour is coverage-specific, with the previously observed 40 dB and 49 dB low-end families, so the dataset intentionally has no airport-inclusive national upper-bound band and does not assume 49 dB nationwide.

The national Phase 1 dataset is technically safe to use as the foundation for Phase 2 modelling, provided downstream analysis preserves these censoring semantics and does not rank the finite lower-bound values as true ambient quietness.

## Final result

# PASS
