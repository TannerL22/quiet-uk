# Quiet UK — Phase 2A road sub-threshold prototype

Date: 2026-08-25  
Result: **PASS WITH ISSUES**  
Scope: bounded road-only prototype; no Phase 1 England tile was modified or regenerated.

## Executive result

The prototype finds useful broad spatial signal below the Defra 40 dB reporting boundary, but it does not yet support defensible absolute sub-40 dB values. It is suitable for continued model development and conservative exploratory ranking. It is not ready for national Phase 2 raster production, quiet-home ranking, or a claim such as “estimated noise: 27.43 dB”.

The recommended prototype fit is a two-predictor Gaussian left-censored model using nearest-road distance and a 10 km traffic-energy proxy. The 10 km window avoids the artificial step produced by a 1 km window at its cutoff. Higher-dimensional traffic/HGV/terrain models remain comparison experiments, not production outputs.

## Source register

| Source | Use | Version / date | Licence and limitations |
|---|---|---|---|
| [Defra Round 4 road Lden WCS](https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517) | Target raster | WCS 1.0.0; `Road_Noise_Lden_England_Round_4_All`; GeoTIFF; EPSG:27700; 10 m request | Defra/Open Government Licence context; finite cells are reported values, blank/zero cells are treated as left-censored `<40 dB`, never as zero |
| [DfT Road Traffic Statistics downloads](https://roadtraffic.dft.gov.uk/downloads) and [API documentation](https://roadtraffic.dft.gov.uk/api-documentation) | AADF, vehicle classes, HGVs, estimation method and coordinates | 2025 AADF extract; 22,334 link-year rows loaded | Open Government Licence v3.0; DfT warns that individual-link estimates may be counted or estimated and are less robust at small spatial scales |
| DfT [Major Roads Database 2025](https://storage.googleapis.com/dft-statistics/road-traffic/mrdb-2025.zip) | Traffic-linked major-road geometry | 2025; 17,941 geometry records | Open Government Licence; CP number joined to DfT `count_point_id`; does not supply a complete minor-road geometry network |
| [OS Open Roads](https://osdatahub.os.uk/downloads/open/OpenRoads) | Investigated as a possible complete road geometry source | April 2026 product page | Open Government Licence; not duplicated in this prototype because DfT MRDB supplies the traffic-linked major geometry and minor DfT links are retained as point sources. A national build should revisit a complete OS Open Roads linkage for minor roads. |
| [Environment Agency LiDAR Composite DTM 10 m](https://environment.data.gov.uk/dataset/ce8fe7e7-bed0-4889-8825-19b042e128d2) | Terrain features | Advertised 10 m composite endpoint returned 404 during this run | Open Government Licence; attribution: © Environment Agency copyright and/or database right 2022. All rights reserved. |
| [Environment Agency DTM 2 m WCS](https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-2m/wcs) | Terrain raster actually used | WCS 2.0.1, coverage `09ea3b37-df3a-4e8b-ac69-fb0842227b04__Lidar_Composite_Elevation_DTM_2m`, requested with 10 m output dimensions | Official 2 m DTM service resampled by the service to 10 m output for this prototype. This is a practical bounded fallback, not evidence that the unavailable 10 m composite endpoint works. |

The DfT source is current 2025 traffic, while Defra Round 4 is an earlier modelling product. That temporal mismatch is an important source of unexplained error and must be resolved or explicitly modelled before national use.

## Prototype regions and sample

Seven independent 10 km × 10 km EPSG:27700 regions were retrieved:

| Region | Purpose | Raw reported fraction | Sampled reported / censored |
|---|---|---:|---:|
| Heathrow / outer London | central/outer London, motorway and strong urban signal | 0.998 | 2,329 / 2,077 |
| Birmingham / M42 | dense urban and motorway | 0.841 | 2,776 / 2,802 |
| Manchester / M60 | dense urban and motorway | 0.988 | 2,776 / 2,967 |
| Norfolk flat rural | flat countryside and rural minor roads | 0.850 | 2,039 / 2,246 |
| South Downs fringe | rural A-road and rolling countryside | 0.593 | 2,654 / 2,744 |
| Peak District | hilly countryside / National Park context | 0.149 | 2,730 / 2,693 |
| North York Moors fringe | hilly rural and remote roads | 0.396 | 314 / 87 |

The final receptor table contains **31,234** sampled cells: **15,618 reported** cells and **15,616 censored** cells. Sampling is deterministic and stratified by reported/censored status, with a maximum of 3,000 from each class per region. The North York Moors region has fewer available cells and should be treated as a weak holdout rather than a fully powered region.

## Features and censoring method

For each sampled 10 m receptor:

- finite Defra road Lden is retained as the observed target;
- a Defra blank/zero is retained as `road_censored_below_40=1`; its target is not treated as 0 or 39.9 dB;
- major road links use DfT MRDB line geometry joined by count-point number;
- minor-road records remain as DfT count-point sources rather than being silently discarded;
- distance features use nearest-road and class-specific nearest distances;
- traffic-energy features use `sum(AADF / max(distance, 10 m)^2)` within selected radii, followed by `log10`;
- HGV-flow energy, HGV share, counted/estimated quality and road class are retained;
- terrain features use receptor elevation, receptor-minus-road elevation, maximum excess above a straight line sampled at nine intermediate points, and an obstruction flag above 2 m.

The censor-aware model is a Gaussian Tobit-style likelihood: observed cells use the normal density; censored cells use `P(latent Lden < 40)`. No ordinary regression trained only on reported cells is used as a claimed sub-threshold model.

## Spatial validation and baseline results

Validation is leave-one-region-out: every holdout region is spatially separated from its training regions. The table below aggregates the 15,618 reported holdout cells; the near-boundary column uses reported targets from 40–45 dB.

| Model | MAE | RMSE | Near-40–45 MAE | Near-40–45 RMSE | Censored predicted `<40` |
|---|---:|---:|---:|---:|---:|
| Distance-only Tobit | 9.29 dB | 11.27 dB | 5.69 dB | 7.06 dB | 69.5% |
| Traffic + distance Tobit — recommended physical baseline | 10.20 dB | 12.59 dB | 7.24 dB | 9.56 dB | 66.4% |
| Multi-road energy Tobit | 8.33 dB | 10.64 dB | 6.14 dB | 7.60 dB | 68.0% |
| Road/traffic feature Tobit | 8.25 dB | 10.56 dB | 6.19 dB | 7.59 dB | 69.9% |
| Road/traffic + terrain Tobit | 9.72 dB | 12.92 dB | 8.19 dB | 11.19 dB | 67.6% |

The feature-rich road/traffic fit has the lowest observed-target error in this sample, but correlated traffic/HGV and class variables give unstable coefficient signs. The simpler traffic+distance fit is retained for profiles and maps because its controlled response is physically monotonic: increasing traffic never decreased the fitted value, and increasing distance decreased it for both tested traffic levels. This controlled check is not independent validation.

The recommended model still has broad uncertainty: its fitted residual scale is about **11 dB**. Reported-cell errors are materially larger in the Peak District holdout than in the urban/rural holdouts, showing that spatial transfer is not yet reliable in terrain-dominated settings.

## Terrain ablation

The terrain-feature model did not improve this prototype:

- overall RMSE worsened from **10.56 dB** without terrain to **12.92 dB** with terrain;
- near-40–45 dB RMSE worsened from **7.59 dB** to **11.19 dB**;
- the terrain model was not used for the recommended profile/map output.

This is not evidence that terrain is irrelevant. The prototype terrain representation is deliberately crude, uses an EA 2 m WCS response at 10 m output, samples only the nearest-source path, and does not represent buildings, vegetation, road cuttings, barriers, propagation directionality or receptor height properly. Terrain should be revisited with stronger geometry and validation rather than added nationally in its current form.

## Uncertainty and end-use assessment

For sampled cells whose recommended-model mean is below 35 dB:

- median disagreement across the five plausible prototype fits is about **3.9 dB**;
- the 90th percentile disagreement is about **6.4 dB**;
- the recommended model's residual scale is about **11 dB**, corresponding to an 80% normal interval half-width of roughly 14 dB;
- the prototype confidence framework therefore assigns no high-confidence low-end cells in this sample.

Interpretation:

1. **Can it discriminate sub-40 dB locations?** Yes, cautiously as a broad relative signal. The censored likelihood produces spatially varying below-threshold probabilities and road-distance/traffic gradients. It cannot yet establish the true absolute level below 40 dB.
2. **Held-out accuracy near 40 dB?** The recommended physically stable baseline is 7.24 dB MAE and 9.56 dB RMSE on reported 40–45 dB holdout cells. More flexible fits are around 6.1–6.2 dB MAE but are not yet physically stable enough to promote.
3. **Does terrain materially improve results?** No. It worsened this ablation and needs better features/validation.
4. **Are predictions physically sensible?** The recommended two-feature model is monotonic in the controlled distance/traffic check and the regenerated profiles are smooth after removing the 1 km window artefact. Absolute accuracy remains poor.
5. **How uncertain below 35 dB?** High. Model disagreement is several dB and residual uncertainty is roughly ±14 dB for the 80% interval. These are not reliable point estimates.
6. **Strong enough for national scaling?** No. A national Phase 2 raster should wait for road-geometry completeness, temporal alignment, constrained/regularised modelling and independent sub-40 validation.
7. **Strong enough for quiet-house ranking?** No. Property-scale decisions need better local road geometry, speed, shielding/building context and validation at the property/neighbourhood scale.
8. **Strong enough for quiet-hiking ranking?** Not yet for sustained quiet-route claims. It may support exploratory identification of likely noisy-road crossings and broad remote-road contrasts, but not defensible sub-35 dB route ranking.
9. **What changes before national Phase 2?** Add a complete road geometry/speed linkage, resolve the Defra/DfT time mismatch, regularise or constrain traffic/HGV effects, improve terrain/shielding, add field or other independent sub-threshold observations, and use spatially blocked validation designed around homes and route segments.

## Reproducible outputs

The live prototype outputs are under `data/processed/phase2_road_prototype/`:

- `prototype_manifest.json` — source IDs, URLs, regions, methods and output inventory;
- `region_inventory.csv` — reported/censored coverage and sample counts;
- `receptor_sample.csv` — target, censor flag, coordinates and features;
- `validation_metrics.csv` and `validation_detail_sample.csv` — spatial holdout results;
- `models_full.json` — fitted coefficients and standardisation metadata;
- `uncertainty_sample.csv` — model disagreement and qualitative confidence fields;
- `example_maps.png` — reported road structure and recommended sub-threshold model maps;
- `predicted_profiles.png` and `predicted_profiles.csv` — Heathrow and Norfolk profiles;
- `physical_sanity.png`, `physical_sanity.csv` and `physical_sanity.json` — controlled monotonic-response check;
- `raw/road_10m/` and `raw/dtm_10m_from_ea_2m_wcs/` — bounded live source rasters only.

No national Phase 2 download or national Phase 2 raster was run.

## Final result

# PASS WITH ISSUES

The architecture is useful for the next research iteration, but the prototype is not yet scientifically strong enough to scale nationally or to support quiet-home/quiet-hiking claims below 40 dB.
