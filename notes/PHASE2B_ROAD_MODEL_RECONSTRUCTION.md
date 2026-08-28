# Quiet UK — Phase 2B Road Model Reconstruction

Validation date: 2026-08-26
Status: **PASS WITH ISSUES**

This note records the bounded Phase 2B road reconstruction experiment. It does not change the validated Phase 1 acoustic pipeline, and no England-wide Phase 2 output was produced.

## Scope and reproducibility

The experiment uses ten geographically separated 10 km regions, with 800 representative and 800 deliberately balanced sample cells per region. The representative sample is weighted back to the full 1,000,000-cell region population; the balanced sample is used only to expose sampling bias. Validation is leave-one-region-out spatial cross-validation.

The reproducible entry point is:

```text
.venv\Scripts\python.exe scripts\15_run_phase2b_road_reconstruction.py --sample-n 800 --timeout 180
```

After the model-intercept correction and profile-radius correction, the cached-input recalculation was:

```text
.venv\Scripts\python.exe scripts\16_recompute_phase2b_models.py --output-root data/processed/phase2b_road --raw-root data/raw/phase2b_road
```

Generated diagnostics are retained under `data/processed/phase2b_road/`, including the receptor sample, raster and network inventories, validation tables, model JSON, uncertainty summaries, PNGs, and end-use diagnostics.

## Input provenance

### Traffic and road geometry

- DfT AADF download: [DfT road traffic downloads](https://roadtraffic.dft.gov.uk/downloads), exact 2021 selection; 22,294 rows. The 2025 comparison selection contains 22,328 rows. No year fallback is used.
- DfT MRDB: 2021 and 2025 published archives, both under the [DfT road traffic storage area](https://storage.googleapis.com/dft-statistics/road-traffic/).
- OS geometry: [OS Open Roads](https://osdatahub.os.uk/downloads/open/OpenRoads), [product documentation](https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads), April 2026 release, national GeoPackage, EPSG:27700, 3,961,077 continuous `road_link` geometries.
- OS and DfT data are used under the applicable Open Government Licence terms. The DfT page warns that estimated individual-link values are less robust than counted values; the experiment retains those distinctions rather than treating them as equally observed.

Within the ten 10 km regions and their 5 km feature margins there are 226,234 OS links. Only 12,856 2021 links (5.683%) are directly associated with DfT traffic records; 213,378 (94.317%) receive class/global-median traffic imputation. The corresponding 2025 figures are 12,851 (5.680%) direct and 213,383 (94.320%) imputed. This is the principal road-model data-quality limitation.

Speed coverage is zero: no observed speed field was available in the selected inputs. The speed-enabled model therefore uses class-reference speed imputation. The speed ablation compares that with a fixed 70 km/h reference; it is not evidence of observed speed effects.

### Defra targets

- Road Lden coverage: `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All`
- Road Lnight coverage: `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lnight_England_Round_4_All`
- WCS: 1.0.0, GeoTIFF, EPSG:27700, 10 m source cells.
- All 20 rasters are float32 with scale 1 and offset 0. Rasterio reports `nodata=-96`; the below-reporting cells are represented by raw zero values, not by valid 0 dB measurements. No raster nodata cells were present in these windows.
- The minimum positive reported values are stable across the ten windows: Lden 40 dB and Lnight 35 dB. These thresholds are used only as inferred censoring cutoffs for the Defra road targets.

The population-weighted mean censor fractions across the ten equal-size windows are 0.384062 for Lden and 0.516364 for Lnight. Censored cells are never treated as measured quiet observations.

## Model method

The existing Phase 2A distance/inverse-square proxy is retained as a benchmark. Phase 2B adds continuous OS road geometry, nearest-road attributes, traffic-source labels, HGV share, distributed line-source energy, and a simplified CNOSSOS-inspired relative emission proxy. The published [CNOSSOS-EU reference report](https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf) informed the separate rolling/propulsion terms, but this implementation is explicitly not CNOSSOS compliant and is not a regulatory noise calculation.

The fitted censored models now include an explicit intercept. Profile features use the same 5 km source-search radius as fitted receptor features; this was corrected after QA found a profile-only distribution mismatch. Ten-kilometre geometry margins are not silently mixed into the 5 km model feature definition.

Controlled physical checks pass:

- higher flow is non-decreasing at 100% of tested distances;
- low-flow prediction decreases with distance at 100% of tested steps;
- high-flow prediction decreases with distance at 100% of tested steps.

These are controlled sanity checks, not independent validation.

## Spatial validation results

Weighted all-region spatial-holdout results for representative sampling are:

| Target / model | RMSE (dB) | MAE (dB) | Bias (dB) |
|---|---:|---:|---:|
| Lden — Phase 2A proxy | 7.983 | 6.364 | -2.196 |
| Lden — complete road features | 7.277 | 5.869 | -1.863 |
| Lden — simplified CNOSSOS-inspired | 7.339 | 5.924 | -1.893 |
| Lnight — Phase 2A proxy | 9.182 | 7.357 | -4.093 |
| Lnight — complete road features | 8.260 | 6.676 | -3.329 |
| Lnight — simplified CNOSSOS-inspired | 8.341 | 6.762 | -3.344 |

The complete feature model is the numerical best in this bounded sample; the constrained CNOSSOS version is identical to the unconstrained fit here because the unconstrained fitted signs already satisfy the tested physical constraints.

The 2021-to-2025 traffic ablation is small in these windows: CNOSSOS-inspired Lden RMSE is 7.339 dB for 2021 versus 7.335 dB for 2025; Lnight is 8.341 versus 8.331 dB. This supports using exact 2021 traffic for the target-year experiment, but does not validate future-year extrapolation.

Inverse-probability weighted censor-probability bias is +0.0126 for Lden and +0.0233 for Lnight for the CNOSSOS-inspired model. The deliberately unweighted balanced sample has materially worse RMSE (9.801 dB Lden; 11.587 dB Lnight), confirming that class balancing must not be used as a population prevalence estimate without correction.

## Speed, end-use, and uncertainty diagnostics

The speed ablation has zero observed speed coverage. Class-reference imputation improves holdout RMSE relative to a fixed 70 km/h reference by 0.245 dB for Lden and 0.307 dB for Lnight, but this is an imputation comparison, not a speed validation.

Reported-cell ranking diagnostics for the CNOSSOS-inspired model are:

- Lden Spearman rank: 0.621 exact, 0.592 within 100 m, 0.644 within 250 m.
- Lnight Spearman rank: 0.437 exact, 0.460 within 100 m, 0.484 within 250 m.

These are not property-level validation. Profile diagnostics show plausible high road-associated signals in urban and motorway windows, but the linear sub-threshold extrapolation can fall below 0 dB in very sparse rural profiles. Those values are diagnostic extrapolations, not defensible physical predictions. The Phase 2B output must therefore not yet be used to rank quiet homes, hiking routes, or genuinely quiet areas nationally.

For the CNOSSOS-inspired model, median model disagreement below a 35 dB Lden primary mean is about 2.45 dB and the fitted 80% residual half-width is about 9.29 dB. For Lnight the corresponding figures are about 2.33 dB and 10.45 dB. These are uncertainty diagnostics, not calibrated measurement error.

Terrain was not promoted into the Phase 2B primary model. The earlier Phase 2A terrain experiment remains secondary evidence only; no claim is made here that the road proxy accounts for terrain shielding.

No harmonised independent national sub-threshold road-noise validation dataset was identified. [NoiseCapture / Noise-Planet](https://onomap-gs.noise-planet.org/noisecapture_data.html) is recorded in the inventory as a possible future source, but calibration, route selection, time-of-day, device, and LAeq/LA50-versus-Lden differences prevent treating it as ground truth.

## Direct answers and decision

1. **Can the model identify likely quiet homes or hiking areas?** Not defensibly yet. It can produce research diagnostics and relative road-exposure hypotheses, but censoring, imputed traffic, absent observed speeds, residual error, and negative rural extrapolations are too significant for a national quiet-place ranking.
2. **Is 2021 traffic correctly aligned?** Yes. The live run selects exact 2021 AADF records and uses 2021 MRDB geometry associations; 2025 is an explicit ablation only.
3. **Does continuous road geometry improve the model?** Yes in this bounded test: the complete feature model is best by RMSE/MAE, while the simplified line-energy model is close.
4. **Are road classes and traffic labels useful?** They are retained and reported, but their national usefulness is limited until the 94% imputation share is reduced or independently validated.
5. **Does the simplified CNOSSOS proxy behave physically?** Yes in controlled monotonicity checks; it is not a compliant CNOSSOS implementation.
6. **Was speed data found?** No observed speed coverage was found. Current speed effects are imputed assumptions.
7. **Was Defra road Lnight added?** Yes. It uses the live WCS Lnight coverage, separate 35 dB censor inference, and separate validation outputs.
8. **Was spatial validation performed?** Yes, with ten-region leave-one-region-out holdouts. The results are informative but not independent field validation.
9. **Was the sampling bias corrected?** Yes for representative headline estimates and weighted balanced comparisons; the unweighted balanced comparison is retained as a warning diagnostic.
10. **Were terrain and physical uncertainty handled?** Terrain remains secondary; model disagreement and residual intervals are reported, but are not calibrated uncertainty bounds.
11. **Was independent validation found?** No suitable harmonised national dataset was found; an inventory and explicit limitations are retained.
12. **Should Phase 2 be run nationally now?** No. The architecture is suitable for another bounded research iteration, but national sub-threshold production should wait for better traffic attribution/validation, observed or defensible speed inputs, and a solution to the rural extrapolation/independent-validation problem.

## Final status

**PASS WITH ISSUES.** The Phase 2B code, live Defra Lden/Lnight inputs, 2021 traffic alignment, continuous OS geometry integration, censored fitting, sampling correction, spatial validation, and diagnostic outputs are reproducible and tested. The remaining issues are genuine limitations of the available data and proxy model, not reasons to alter the validated Phase 1 pipeline. No national Phase 2 job was run.
