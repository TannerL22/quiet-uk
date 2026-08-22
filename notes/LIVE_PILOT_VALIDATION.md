# Live Defra pilot validation — 2026-08-22

## Result

**PASS WITH ISSUES.** The first real Defra road + rail + airport tile completed through download, source combination, alignment, 10 m diagnostics, 100 m energy aggregation and PNG visual QA. No national download or processing was started.

The issues found are service/data-contract details now handled explicitly in the code, with two policy risks retained for the national build: the airport censor threshold needs a nationally documented interpretation, and the airport WCS 2.0.1 grid must be padded/aligned on every tile.

## Live coverages and WCS requests

| Source | Coverage ID used | Discovery / GetCoverage | Format |
|---|---|---|---|
| Road | `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All` | WCS 1.0.0 | `GeoTIFF` |
| Rail | `3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947:Rail_Noise_Lden_England_Round_4_All` | WCS 1.0.0 | `GeoTIFF` |
| Airport | `dac9cba4-abe7-43bd-b8e9-8a83da52edd8__Airport_Noise_ALL_Lden` | WCS 2.0.1 | `image/tiff` |

Road and rail accepted the existing WCS 1.0.0 KVP request (`coverage`, `bbox`, `crs`, `response_crs`, `width`, `height`). The airport endpoint advertises WCS 1.0.0 capabilities, but its WCS 1.0.0 `GetCoverage` rejects `GeoTIFF` with “format ... is not supported for this coverage”. Its WCS 2.0.1 `DescribeCoverage` reports native format `image/tiff`; the working request uses `coverageId`, `outputCRS`, repeated `subset=E(...)` / `subset=N(...)`, and `scaleSize`.

The airport native grid is half a pixel shifted relative to the road/rail response. The request therefore includes one-cell padding at native 10 m spacing, producing a 1001 × 1001 source, then the pipeline nearest-neighbour reprojects it to the road reference grid before combining. This is recorded in `data/processed/pilot/pilot_manifest.json`.

## Source raster properties

Pilot request extent: `[503000, 171000, 513000, 181000]` EPSG:27700; requested road/rail resolution: 10 m.

| Source | Returned shape / dtype | CRS / resolution | Raw transform / bounds | Nodata / blank behaviour |
|---|---|---|---|---|
| Road | 1000 × 1000, float32 | EPSG:27700, 10 m | exactly the requested grid | declared `-96.0`; 2,144 literal `0.0` cells were below-threshold/censored |
| Rail | 1000 × 1000, float32 | EPSG:27700, 10 m | exactly the requested grid | declared `-96.0`; 624,506 literal `0.0` cells were below-threshold/censored |
| Airport | 1001 × 1001, float64 | EPSG:27700, 10 m | padded grid `[502995,170995,513005,181005]` | nodata `3.39999995e38`, mask applied; 484,738 raw nodata cells |

No `-96` road/rail cells occurred in this tile. Treating literal zeroes as 0 dB would have materially overstated known lower-bound energy; the new raster loader maps them to NaN/censored values and applies GDAL scale/offset metadata before interpretation.

## Pilot statistics

### Reported source values after airport alignment

| Source | Reported cells | Minimum | Median | P90 | P99 | Maximum |
|---|---:|---:|---:|---:|---:|---:|
| Road | 997,856 | 40.00 | 54.26 | 66.81 | 78.31 | 87.55 |
| Rail | 375,494 | 40.00 | 45.42 | 59.51 | 72.75 | 83.94 |
| Airport | 516,673 | 49.00 | 58.22 | 67.46 | 82.23 | 88.60 |

The airport threshold used for this pilot was the minimum positive reported value, 49.0 dB, because its config threshold is intentionally unspecified. This is an empirical pilot ceiling, not a claim that 49 dB is the nationally correct airport censor threshold.

### Combined output

| Output | Valid cells | Minimum | Median | P99 | Maximum |
|---|---:|---:|---:|---:|---:|
| 10 m lower bound | 999,536 | 40.00 | 59.31 | 81.37 | 88.60 |
| 10 m upper bound | 1,000,000 | 49.98 | 59.50 | 81.37 | 88.60 |
| 100 m lower bound | 10,000 | 37.10 | 60.30 | 80.37 | 87.25 |
| 100 m upper bound | 10,000 | 50.03 | 60.48 | 80.37 | 87.25 |
| 100 m uncertainty | 10,000 | 0.00 | 0.08 | 3.72 | 12.93 |

The 100 m lower bound can be below 40 dB because censored fine cells contribute zero known energy while remaining in the aggregation denominator. This is expected and is not a 0 dB interpretation.

Independent recomputation of the stored 100 m bands matched within `3.9e-6 dB` (float32 output rounding). Fine-grid checks passed: combined lower bound was never below the loudest reported source, upper bound was never below lower bound, and 464 fine cells had all sources censored. All final source arrays share the exact EPSG:27700 road reference grid.

## Geographic / visual check

The diagnostic image is [combined_upper_100m_diagnostic.png](../data/processed/pilot/combined_upper_100m_diagnostic.png). It shows a coherent dense road pattern, narrow railway-like corridors, and a broad airport footprint. After alignment, airport cells at or above 70 dB occupy approximately BNG `[504245,174865,510275,176785]`, consistent with Heathrow in this pilot. The combined output does not show a raster offset or CRS inversion.

## Code and test changes

- Added per-source WCS version and response-format settings to `config.example.json` and the live `config.json`.
- Added WCS 2.0.1 retrieval with the airport `coverageId` / `image/tiff` syntax and padded subset request.
- Added single-band validation, declared nodata/mask handling, zero-as-censored handling, and scale/offset decoding.
- Added explicit nearest-neighbour alignment to the reference grid for the airport response, plus strict grid checks for shape/CRS/real offsets.
- Added manifest checks, source diagnostics, and a simple combined 100 m diagnostic PNG.
- Added targeted tests for zero and unusual nodata, integer/scaled encoding, unexpected multiband responses, WCS 2.0.1 request construction, and shifted-grid alignment.

The test suite passes: **12 tests**.

## Readiness and remaining risks

The pilot code path is ready for a controlled national tiling implementation, but the England build should not start until the tiler carries forward the same airport padding and alignment logic, with overlap/padding checks at tile boundaries. The airport 49 dB empirical threshold also needs a documented national policy or coverage-specific threshold source before conservative national ranking is interpreted. No blocker remains for further pilot tiles; national scaling was intentionally stopped here.
