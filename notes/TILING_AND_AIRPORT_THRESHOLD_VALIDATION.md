# Tiling and airport threshold validation — 2026-08-22

## Result

**PASS WITH ISSUES.** The England-ready tile architecture passed a live 2×2 Heathrow seam test. Airport threshold sampling shows genuine variation between individual airport coverages, so no single national airport upper-bound threshold is defensible yet.

No England-wide download or national processing job was run.

## Chosen tile architecture

- CRS: EPSG:27700.
- Core tiles: 10 km × 10 km by default; every core edge is snapped/aligned to the 100 m output grid. Edge tiles remain multiples of 100 m.
- Source requests: 10 m road/rail WCS 1.0.0 and airport WCS 2.0.1.
- Each tile creates an exact 10 m target grid from its core bounds. The airport request adds the validated one-cell WCS 2.0.1 padding, then uses explicit nearest-neighbour reprojection to that target grid.
- Temporary 10 m GeoTIFFs are stored per tile only, then removed after the 100 m tile is written. The tile result records this policy and source alignment diagnostics.
- Tile output bands are:
  1. `combined_reported_lower_db` — reported road + rail + airport energy only;
  2. `road_rail_upper_db` — conservative upper bound for road + rail only, using the existing 40 dB road/rail thresholds;
  3. `airport_reported_lower_db` — reported airport energy only, with unknown/censored cells contributing zero known lower energy;
  4. `airport_reported_fraction` — fraction of 10 m cells in the 100 m cell with a reported airport value.

Because `reporting_threshold_db.airport` remains null, the tile pipeline does **not** emit a total `combined_upper_db` band. Heathrow’s 49 dB value is not silently used nationwide.

Implementation: `src/quiet_uk/tiling.py`; live seam runner: `scripts/05_run_tile_seam.py`.

## Live 2×2 seam test

Extent: `[503000,171000,513000,181000]` EPSG:27700. Four adjacent 5 km core tiles were processed independently, mosaicked to a 100 m 100 × 100 grid, and compared with one independently processed 10 km tile.

| Check | Result |
|---|---:|
| Tiles | 4 (each 500 × 500 source cells and 50 × 50 output cells) |
| Mosaic gaps | 0 |
| Mosaic overlaps/duplicate cells | 0 |
| CRS and transform | exact EPSG:27700 / 100 m match |
| Airport alignment | performed for all four tiles |
| Temporary 10 m cleanup | passed |
| Maximum difference vs single 10 km tile | 0.0 dB in every band |
| Maximum shared-edge difference | 0.0 dB in every band |

Outputs:

- `data/processed/tile_validation/mosaic_2x2_100m.tif`
- `data/processed/tile_validation/single_10km_reference.tif`
- `data/processed/tile_validation/mosaic_2x2_tile_boundaries.png`
- `data/processed/tile_validation/seam_test_manifest.json`

The boundary PNG shows the four tile cores over the combined reported-lower result and was visually inspected.

## Airport threshold inventory

The live WCS 2.0.1 endpoint was queried for ten individual `*_Lden` airport coverages. Each full individual airport footprint was retrieved at its native 10 m grid, with `padding_cells=0` because the inventory statistics do not require cross-grid alignment. Declared nodata/masks and zero sentinels were excluded. For each footprint the inventory records the minimum positive value, low-tail quantiles, counts within 0.5/1 dB of the minimum, first low-end values, and 2×2 spatial-quadrant minima.

Inventory: `data/processed/airport_threshold_inventory.csv`.

| Airport | Valid reported cells | Minimum positive Lden | Cells at minimum | q01 | q05 |
|---|---:|---:|---:|---:|---:|
| Heathrow | 1,998,181 | 49.0 | 140 | 49.081 | 49.399 |
| Gatwick | 492,448 | 49.0 | 33 | 49.082 | 49.410 |
| Manchester | 660,836 | 49.0 | 42 | 49.092 | 49.459 |
| Birmingham | 386,573 | 49.0 | 20 | 49.091 | 49.462 |
| Stansted | 1,165,587 | 49.0 | 75 | 49.067 | 49.342 |
| Bristol | 1,325,038 | 40.0 | 17,097 | 40.000 | 40.300 |
| London City | 418,376 | 40.0 | 4,475 | 40.000 | 40.400 |
| Liverpool | 853,309 | 40.0 | 4,418 | 40.100 | 40.900 |
| Southampton | 39,583 | 49.0 | 3 | 49.052 | 49.262 |
| Newcastle | 214,827 | 49.0 | 9 | 49.099 | 49.491 |

Findings:

- The sampled airport coverages do not share one minimum: Bristol, London City and Liverpool reach 40 dB, while the other seven sampled footprints reach 49 dB.
- The 40 dB group has a strong repeated endpoint, especially Bristol, consistent with a deliberate reporting floor.
- The 49 dB group has a common observed endpoint and a low q01 tail around 49.07–49.10 dB, consistent with a possible reporting floor, but exact-minimum counts are much smaller and the low tail is continuous. This is evidence, not proof, of the competent-authority threshold.
- No sampled airport showed a >1 dB difference between 2×2 quadrant minima. That does not prove one uniform threshold within an airport; disconnected contour geometry and coverage-specific data assembly cannot be separated by this test alone.
- The inventory therefore supports a coverage/airport-specific threshold table, not a single national airport threshold. The production configuration correctly leaves the airport threshold null and does not produce a total airport-inclusive upper bound.

## England processing scale estimate (superseded by production validation)

The estimate uses the live road/rail WCS rectangular coverage extent `[82645,5335,655995,657605]` as a conservative planning rectangle. It is not land-clipped and therefore overstates England’s true cell count.

With the selected 10 km tile size:

- 3,828 proposed tiles (66 rows × 58 columns);
- snapped output shape 6,524 × 5,734;
- approximately 37.41 million 100 m cells;
- approximately 3.741 billion 10 m cells per source, 11.223 billion across road + rail + airport;
- approximately 44.9 GB uncompressed for three float32 raw source bands if retained nationally;
- approximately 598.5 MB uncompressed for the four-band tile/mosaic product;
- observed pilot raw input size for one 10 km tile: approximately 25.8 MB across the three source GeoTIFFs;
- expected peak temporary raw storage is therefore roughly 25.8 MB per 10 km tile, plus processing workspace and response buffering, when tiles are processed sequentially and deleted after validation.

A 20 km alternative would reduce the rectangular tile count to 957 but increase observed raw temporary storage to roughly 103 MB per tile. The reproducible calculation is in `data/processed/national_scale_estimate.json` and `scripts/07_estimate_national_scale.py`.

Intermediate raw tiles can be safely deleted after the tile GeoTIFF, tile manifest/checks, and any required audit metadata have been written and validated. Failed tiles should be retried before being marked complete; national execution will also need service retry/rate-limit handling.

## Remaining blockers before national processing (historical snapshot)

1. Establish a documented airport threshold policy based on the individual airport/coverage inventory, including all remaining airport layers or an authoritative source-level threshold field. Do not set `airport=49.0` globally.
2. Add national request retry/rate-limit/resume handling around the now-validated per-tile processor.
3. Supply the final England land mask/processing extent and retain tile-level audit manifests before starting the national job.

The tile architecture and seam behavior are ready for the next controlled engineering step, but the England-wide run remains intentionally gated by airport threshold policy and operational download safeguards.

The later production-readiness validation added the ONS land mask, retry/rate-limit/resume runner, and live canary. The four-band England run is now prepared; airport-threshold resolution remains required only before airport-inclusive upper-bound interpretation. See `notes/PRODUCTION_READINESS_VALIDATION.md`.
