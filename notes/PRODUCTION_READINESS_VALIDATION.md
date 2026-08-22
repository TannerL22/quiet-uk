# England production-readiness validation — 2026-08-22

## Result

**PASS WITH ISSUES.** The England four-band pipeline is ready for a controlled England-wide run. The live 15-tile canary passed masking, output validation, resumability, and regional checks. The airport-inclusive conservative upper bound remains intentionally unavailable because the airport reporting floor is not nationally uniform.

No England-wide Defra processing job was launched.

The configured live coverages are road `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All`, rail `3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947:Rail_Noise_Lden_England_Round_4_All`, and airport `dac9cba4-abe7-43bd-b8e9-8a83da52edd8__Airport_Noise_ALL_Lden`. Road/rail use WCS 1.0.0 and airport uses WCS 2.0.1 with `GeoTIFF` / `image/tiff` respectively.

## England land mask

The mask uses the official ONS [Countries (December 2024) Boundaries UK BGC](https://www.data.gov.uk/dataset/8580e329-83c9-4646-bf93-d0411f00c53a/countries-december-2024-boundaries-uk-bgc1) product: Generalised (20 m), clipped to the coastline at the Mean High Water mark. The local request selects `CTRY24CD=E92000001` / England and requests `outSR=27700` from the official [ONS ArcGIS Feature Service](https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/Countries_December_2024_Boundaries_UK_BGC/FeatureServer/0/query).

The source URL, query, source/target CRS, product description, licence statement, and attribution are retained in `data/processed/england_mask/metadata.json`. The data.gov.uk record states **No Licence Provided**; the source attribution is: “Contains both Ordnance Survey and ONS Intellectual Property Rights.” The response geometry is also retained as `england_boundary_epsg27700.geojson`.

Artifacts:

- `data/processed/england_mask/england_20m_mask.tif`
- `data/processed/england_mask/england_100m_mask.tif`
- `data/processed/england_mask/metadata.json`

The EPSG:27700 mask extent is `[82600, 5300, 655700, 657600]`. The 20 m mask contains 326,148,773 England cells. A 100 m cell is retained when any of its 25 source 20 m cells is England land; this gives **13,086,924 England 100 m cells**.

## Production runner and airport handling

The reusable runner is in `src/quiet_uk/runner.py`, with `scripts/10_run_england.py` as the national entry point. It uses 10 km core tiles, exact 100 m-aligned boundaries, EPSG:27700, WCS 1.0.0 road/rail, WCS 2.0.1 airport retrieval with the validated half-cell padding and explicit alignment, and acoustic-energy aggregation before 100 m output.

The default operational settings are one worker, a one-second tile-start interval, 180 second WCS timeout, four attempts, and exponential backoff from five seconds up to 120 seconds. Status is written atomically per tile with `pending`, `running`, `complete`, or `failed`, attempt/retry counts, errors, source diagnostics, and validation results. Tile output is written to a hidden staging filename, validated, atomically renamed, and validated again before `complete`. Temporary 10 m inputs are deleted after the validated 100 m output exists. Stale temporary/staged files from an interruption are safe to remove on resume.

The output remains exactly four bands:

1. `combined_reported_lower_db`
2. `road_rail_upper_db`
3. `airport_reported_lower_db`
4. `airport_reported_fraction`

No national `airport=49` assumption and no airport-inclusive upper bound were added.

The retained live airport inventory found two low-end families: Heathrow,
Gatwick, Manchester, Birmingham, Stansted, Southampton, and Newcastle reach
49 dB minimum positive Lden, while Bristol, London City, and Liverpool reach
40 dB. The full low-tail counts and quantiles remain in
`data/processed/airport_threshold_inventory.csv` and
`notes/TILING_AND_AIRPORT_THRESHOLD_VALIDATION.md`; this is evidence of
coverage-specific behavior, not a national censor threshold.

The live aggregate airport coverage declares EPSG:27700 bounds `[333485, 93815, 594465, 574285]`. Defra returns HTTP 500 for a request wholly outside that rectangle, even with otherwise valid WCS 2.0.1 syntax. The runner now records these declared bounds in config and skips only an airport request with no spatial intersection, producing no reported airport contribution and fraction zero; partial edge tiles are still requested. This behavior is covered by a targeted test and is recorded in per-tile source diagnostics.

The exact southwest mask-edge tile `[82600,5300,92600,7600]` was also requested live for road and rail; both WCS 1.0.0 requests returned valid GeoTIFFs. This removes the small ONS/Defra extent difference as a current launch blocker.

## Canary

The production runner processed **15** tiles, selected from these environments: central London, Heathrow, M25/M4, Bristol airport, Manchester, Birmingham motorways, Leeds, East Anglia rural, Norfolk coast, Peak District, Lake District, south coast, southwest rural, England/Wales boundary, and Newcastle/northeast. Selection and reasons are in `data/processed/canary/canary_selection.json`.

Live QA artifacts:

- `data/processed/canary/tile_status_manifest.json`
- `data/processed/canary/canary_validation.json`
- `data/processed/canary/canary_combined_lower_contact_sheet.png`
- `data/processed/canary/tile_errors.log`
- `data/processed/canary/resume_test.json`

All 15 tiles are `complete`. The QA checks found:

- all cells outside the ONS England mask are nodata;
- all airport fractions are within `[0,1]`;
- combined reported lower is never below airport-only reported lower where both are reported;
- no temporary 10 m directory remains;
- combined reported lower range across the canary: **20.00–87.251 dB**;
- road+rail upper range: **43.010–82.418 dB**;
- airport reported lower range: **20.00–87.250 dB** at 100 m cell level;
- airport reported fraction range: **0–1**.

The 20 dB lower values are sparse reported energy averaged over a 100 m cell; they are not interpreted as a reporting threshold.

The pre-fix run recorded eight HTTP 500 responses for two tiles wholly outside the aggregate airport rectangle (`r0032c0055` Norfolk coast and `r0035c0024` England/Wales boundary). After the bounds fix, both completed with airport fraction zero. One separate tile had a second recorded attempt but no unresolved error. No tile remained failed.

The earlier live 2×2 Heathrow seam test remains clean: zero gaps, zero overlaps, exact CRS/transform, airport alignment on every tile, and maximum difference **0.0 dB** against the single larger extent. Its boundary diagnostic is retained under `data/processed/tile_validation/`.

## Resume test

The canary was rerun unchanged: 14 valid complete tiles were skipped. Then only `r0047c0044` was marked failed in the manifest and the failed-only command was run. `resume_test.json` records exactly that one tile being retried and 14 tiles skipped. The final manifest is 15/15 complete.

## England scale

The mask-filtered plan is in `data/processed/national_scale_estimate_masked.json` and contains the full deterministic tile list without downloading source data.

- scheduled England-intersecting 10 km tiles: **1,498**;
- 100 m cells in scheduled tile products, including coastal/outside-land cells: **14,944,700**;
- England land cells at 100 m: **13,086,924**;
- 10 m source cells per source band over scheduled cores: **1,494,470,000**;
- all three source bands at 10 m: **4,483,410,000**;
- uncompressed three-source core-cell equivalent if retained: **17.93 GB**;
- uncompressed four-band scheduled tile products: **239.1 MB**;
- uncompressed four-band England land-cell payload: **209.4 MB**;
- observed three-source raw temporary size for a 10 km pilot: **25.84 MB**;
- expected peak temporary raw size with the default one worker: approximately **25.84 MB**, plus small working/output overhead.

The final product is kept as independently validated tiles; raw 10 m inputs are not accumulated nationally. The 15-tile canary took 10.5–29.4 seconds per final attempt, median 14.7 seconds. A simple median extrapolation is about 6.1 hours for 1,498 tiles at one worker, but this is only an operational indicator; service retries, coverage-edge behavior, and regional response times can make the actual run longer.

## National command — prepared, not run

After reviewing this note and the canary artifacts, the exact default command is:

```powershell
.\.venv\Scripts\python.exe scripts/10_run_england.py `
  --config config.json `
  --output-root data/processed/england `
  --manifest data/processed/england/tile_status_manifest.json
```

Use `--failed-only` to retry only tiles whose manifest status is `failed`. The default is one worker; a higher worker count is configurable but should be treated as an explicit service-load decision.

## Remaining issues

1. Airport threshold behavior remains coverage/airport-specific: the live inventory found both 40 dB and 49 dB low-end families. National processing is defensible for the four reported/conservative bands, but not for an airport-inclusive upper bound or a national airport censor threshold.
2. The Defra airport service has a known HTTP 500 behavior for wholly out-of-coverage requests. The declared-bounds guard prevents those requests; in-coverage failures still rely on retry/backoff and are logged per tile.

There is no remaining issue that prevents starting the England-wide **four-band** run. Airport-threshold resolution remains a blocker only for later airport-inclusive upper-bound interpretation.
