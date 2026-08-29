# Quiet UK — England anthropogenic noise pipeline

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

Python 3.11+ is recommended.

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\\Scripts\\activate
pip install -r requirements.txt
```

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

The national Phase 1 build is complete and remains reproducible from the production runner and manifests. Its validated workflow is:

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

The final two steps are downstream products, not part of the frozen acoustic raster validation. Do not start a national Phase 2 road reconstruction from the Phase 2C research outputs without independent sub-threshold validation.

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
