# Quiet UK — Phase 2C Road-Source Integrity and Stability

Validation date: 2026-08-29  
Status: **PASS WITH ISSUES**

Phase 2C is a bounded research pass over the same ten Phase 2B regions. It does not modify or regenerate the frozen Phase 1 England national tiles, and it does not create a national Phase 2 raster.

## Reproducible run

The completed live experiment used 100 representative and 100 balanced receptors per region, for 2,000 records total. The smaller sample was chosen after benchmarking showed that finite-line integration is substantially more expensive than the Phase 2B proxy; all ten regions and all spatial holdouts were retained.

```text
.venv\Scripts\python.exe scripts\17_run_phase2c_road_integrity.py --sample-n 100 --timeout 120
```

Large raw and processed inputs remain gitignored under `data/raw/` and `data/processed/`. Lightweight audit outputs are committed under `results/phase2c/`.

## Source provenance

- Primary traffic year: exact 2021 DfT AADF rows, 22,294 records, with the 2021 DfT MRDB geometry archive. The loader refuses year fallback.
- Road geometry: April 2026 [OS Open Roads](https://osdatahub.os.uk/downloads/open/OpenRoads), EPSG:27700, national GeoPackage. The [OS documentation](https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads) describes the complete classified/unclassified GB road network.
- Defra targets: `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lden_England_Round_4_All` and `562c9d56-7c2d-4d42-83bb-578d6e97a517:Road_Noise_Lnight_England_Round_4_All`, WCS 1.0.0 GeoTIFF, EPSG:27700, 10 m cells.
- The [DfT traffic downloads](https://roadtraffic.dft.gov.uk/downloads) source and [DfT API documentation](https://roadtraffic.dft.gov.uk/api-documentation) are recorded in the manifest. Traffic count confidence is preserved.
- Four bounded walkable-path demonstrations were obtained from the [Overpass API](https://overpass-api.de/api/interpreter) using OpenStreetMap data, © OpenStreetMap contributors, ODbL: Heathrow, South Downs, Peak District, and East Anglia coast. These are model-behaviour demonstrations, not validation measurements.

## 1. Deterministic traffic assignment

The Phase 2C matcher is a deterministic two-pass process:

1. all candidate DfT/MRDB matches are scored first using distance, road number, name, broad class, and orientation;
2. accepted direct matches are stored;
3. only after that pass are hierarchical imputations calculated.

OS links are sorted by stable link identifier and fid. Candidate ties are resolved by score, distance, and DfT identifier. The order-invariance regression test shuffles the OS links five times and obtains identical flows, HGV flows, source labels, and imputation methods.

Direct attribution across the ten source windows changed from the Phase 2B 12,856/226,234 links (5.683%) to 13,059/226,234 links (5.772%), an increase of 203 links or 0.092 percentage points. The Phase 2C direct matches comprise 12,977 high-confidence and 82 medium-confidence matches. This small gain is intentional: precision was prioritised over forcing weak matches.

The direct match QA sample covers motorway and urban/rural A-road contexts. B-road and minor-road classes are present in the class-level inventory, but no accepted direct examples were available in these ten windows; those classes are therefore explicitly treated as an attribution limitation rather than represented by invented matches. Distance, score, road-class, road-name, orientation, and candidate-count diagnostics are retained in `match_qa_sample.csv`.

## 2. Hierarchical imputation

The imputation hierarchy is:

1. same road number among accepted direct matches;
2. nearby same-class direct matches within 5 km;
3. same class plus urban/rural direct median;
4. class median over available DfT MRDB sources;
5. global DfT median.

The result remains heavily imputed. Across the 226,234 links, 213,175 are imputed. The largest method is the global fallback (206,622 links, 91.331%), followed by same-road-number direct support (6,513, 2.879%) and nearby same-class support (38, 0.017%). This exposes a limitation of the available DfT MRDB coverage: most OS minor/B-road geometry has no locally matched traffic observation. Imputed values are explicitly labelled and are not treated as measurements.

The attribution summary is broken out by road class in `traffic_match_by_class.csv`. The direct-rate increase is therefore not evidence that the traffic network is well observed nationally; it is a modest precision-controlled improvement.

## 3. England land masking

The existing ONS-derived 100 m mask was reused without modification. It is expanded to the 10 m receptor grid only for sample eligibility, using the existing any-20 m-subcell land rule.

| Region | Eligible 10 m receptors | Excluded non-land receptors | Land fraction |
|---|---:|---:|---:|
| Heathrow / outer London | 1,000,000 | 0 | 1.0000 |
| Birmingham / M42 | 1,000,000 | 0 | 1.0000 |
| Manchester / M60 | 1,000,000 | 0 | 1.0000 |
| Leeds suburban fringe | 1,000,000 | 0 | 1.0000 |
| Norfolk flat | 999,800 | 200 | 0.9998 |
| South Downs fringe | 1,000,000 | 0 | 1.0000 |
| Peak District | 1,000,000 | 0 | 1.0000 |
| North York Moors fringe | 1,000,000 | 0 | 1.0000 |
| East Anglia coastal rural | 594,500 | 405,500 | 0.5945 |
| Northumberland remote | 1,000,000 | 0 | 1.0000 |

The East Anglia coastal mask is materially important: 40.55% of that rectangular window is excluded. No sampled record has `land_mask_valid=0`. Because the Phase 2C comparison fits both Phase 2B and Phase 2C models on the same masked sample, the isolated numerical effect of excluding non-land cells is not separately identifiable; the eligibility effect is reported rather than hidden.

## 4. Acoustic source and propagation changes

The [published CNOSSOS-EU reference report](https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf) informed the separate rolling and propulsion speed-response relationships. Phase 2C combines those two component levels logarithmically, then combines light-vehicle and HGV category energy. The constants remain simplified, uncalibrated relative A-weighted proxy constants; no CNOSSOS compliance is claimed.

The redundant post-hoc HGV-share multiplier was removed. HGV flow is already represented explicitly in the HGV category energy. A regression test confirms that changing a redundant `hgv_share` field without changing HGV flow does not change source energy.

The historical Phase 2B `AADF / distance²` term remains only as a benchmark. The new source term treats each assigned OS link's relative emission as a per-metre intensity and integrates regularised inverse distance along every straight geometry segment. The integral is additive over collinear segments, so one 2 km road, two 1 km roads, and twenty 100 m roads give the same feature within `1e-10` dB in the synthetic test. This is a finite-line approximation, not full atmospheric, barrier, ground, or terrain propagation.

## 5. Phase 2B versus Phase 2C results

Results below are weighted all-region leave-one-region-out holdouts on the same land-masked 2021 sample. The Phase 2B row is the old distance/inverse-square proxy recomputed on the Phase 2C eligible sample; this makes the comparison cleaner than comparing different sample populations, but the smaller sample means it is not numerically identical to the earlier 800-per-region Phase 2B run.

| Target / model | RMSE | MAE | Bias | P90 AE | Censor Brier | Censor probability bias | Reported rank |
|---|---:|---:|---:|---:|---:|---:|---:|
| Lden — Phase 2B proxy | 7.813 | 6.123 | -2.080 | 13.085 | 0.1054 | +0.0173 | 0.535 |
| Lden — Phase 2C proxy | 7.767 | 6.101 | -2.052 | 13.013 | 0.1050 | +0.0175 | 0.540 |
| Lden — Phase 2C finite line | 7.649 | 6.090 | -2.010 | 12.841 | 0.1021 | +0.0200 | 0.572 |
| Lden — Phase 2C complete | 6.969 | 5.433 | -1.794 | 11.666 | 0.0992 | +0.0134 | 0.621 |
| Lnight — Phase 2B proxy | 8.813 | 6.931 | -3.660 | 14.932 | 0.1368 | +0.0246 | 0.288 |
| Lnight — Phase 2C proxy | 8.726 | 6.882 | -3.603 | 14.924 | 0.1359 | +0.0242 | 0.297 |
| Lnight — Phase 2C finite line | 8.657 | 6.857 | -3.444 | 14.534 | 0.1314 | +0.0284 | 0.355 |
| Lnight — Phase 2C complete | 7.779 | 6.018 | -3.030 | 13.419 | 0.1228 | +0.0229 | 0.455 |

The headline complete model improves RMSE by 0.843 dB for Lden and 1.034 dB for Lnight versus the Phase 2B proxy on this matched sample. The improvement is coupled: the run changes attribution, source emission, finite-line propagation, and adds complete-model features. There was no isolated rolling/propulsion-only ablation, so a separate causal effect for that correction is not claimed.

## 6. Censor boundary

For the best complete model, the reported-cell boundary diagnostics are:

- Lden 40–42 dB: 6.724 dB MAE, +1.608 dB bias, rank 0.229;
- Lden 42–45 dB: 5.377 dB MAE, +1.329 dB bias, rank 0.194;
- Lden 45–50 dB: 4.316 dB MAE, +0.008 dB bias, rank 0.098;
- Lnight 35–37 dB: 4.815 dB MAE, +0.980 dB bias, rank -0.017;
- Lnight 37–40 dB: 4.893 dB MAE, -0.218 dB bias, rank 0.110.

The model is therefore still weak as a fine ranker at the quiet boundary even though its average holdout error improves. Censor probabilities remain usable diagnostics but are not perfect: the bounded finite-line model has approximately +0.020 Lden and +0.028 Lnight overall probability bias in this sample. This is not calibrated real-world uncertainty.

## 7. Rural behaviour and output framing

Phase 2B's negative rural profile problem was investigated rather than clipped. Phase 2C adds a structurally bounded primary formulation, `mu = 0 + softplus(X beta)`, which cannot predict negative dB. In the selected sample the finite-line model already has minima of 22.87 dB Lden and 12.22 dB Lnight, while the bounded formulation has the same fit to displayed precision. The structural bound matters for out-of-domain profiles; it does not manufacture accuracy below the Defra reporting cutoff.

The bounded model is suitable as a physically constrained diagnostic, not as proof that absolute sub-threshold dB is defensible. Probability outputs such as `P(Lden < 40 dB)` and relative ranks are the more honest future end products, accompanied by traffic-attribution quality and extrapolation indicators.

## 8. Neighbourhood and walking diagnostics

Neighbourhood evaluation uses all available cells in the target raster, not just sampled neighbours. For the bounded finite-line model, rank correlations against full-grid target neighbourhood means are:

- Lden: 0.572 exact, 0.559 within 100 m, 0.566 within 250 m;
- Lnight: 0.355 exact, 0.317 within 100 m, 0.322 within 250 m.

Neighbourhood averaging does not materially stabilise the rank in this small experiment. These are reported-cell diagnostics, not property validation.

The OSM demonstration contains one longest suitable returned way in each of four prototype regions. At 100 m sampling, predicted Lden medians were approximately 65.0 dB for the Heathrow primary way, 55.0 dB for the South Downs tertiary way, 50.0 dB for the Peak District primary way, and 31.8 dB for the East Anglia path. These figures demonstrate route-profile behaviour only; they are not independent acoustic observations or a routing product.

## 9. Uncertainty and remaining risks

The lightweight uncertainty summary reports median model disagreement of about 1.27 dB for Lden and 1.42 dB for Lnight over representative records. These are model-spread diagnostics, not confidence intervals. The dominant remaining error sources are:

1. 94.3% of OS links in the Phase 2B windows remain imputed in the broader traffic-assignment accounting, with 91.3% using the global fallback method in Phase 2C;
2. observed speed coverage is still zero;
3. Defra targets censor the quiet end;
4. no harmonised independent national sub-threshold validation dataset has been found;
5. the finite-line proxy omits terrain, barriers, ground, meteorology, and other CNOSSOS propagation effects.

The full source-quality and uncertainty fields are retained in the local experiment outputs and selected audit tables under `results/phase2c/`.

## Direct answers

1. **Was assignment made order-independent?** Yes; two-pass deterministic assignment and a shuffle regression test pass.
2. **How did direct matching change?** 5.683% to 5.772%, +203 links; 12,977 high-confidence and 82 medium-confidence direct matches.
3. **How did hierarchical imputation perform?** The matched-sample proxy improved RMSE by 0.045 dB Lden and 0.086 dB Lnight; the complete model improved by 0.843 and 1.034 dB respectively, but those gains are coupled with propagation/features.
4. **Did land exclusion materially change results?** It materially changes eligibility in East Anglia (40.55% removed), but its isolated performance effect was not estimated; both comparison models use the same mask.
5. **Did rolling/propulsion correction materially change results?** It is correctly implemented and tested, but no isolated ablation was run, so no standalone effect is claimed.
6. **Is source integration segmentation-invariant?** Yes within `1e-10` dB in the synthetic equivalent-road test.
7. **Lden improvement?** Complete model: -0.843 dB RMSE, -0.690 dB MAE versus the matched Phase 2B proxy.
8. **40–45 dB improvement?** The complete Lden model has 6.724 dB MAE at 40–42 and 5.377 dB at 42–45; it remains a weak fine ranker near the boundary.
9. **Lnight 35–40 dB?** Complete model MAE is 4.815 dB at 35–37 and 4.893 dB at 37–40; rank correlations remain near zero.
10. **Are probabilities calibrated?** Approximately, but not sufficiently to call them calibrated measurement probabilities; bias is about +0.020 Lden and +0.028 Lnight for the bounded finite-line model.
11. **Is rural extrapolation resolved structurally?** Negative outputs are prevented by the softplus formulation, but low-end accuracy is not thereby established.
12. **Absolute dB or probability/rank?** Probability and relative rank should lead future use; absolute sub-threshold dB remains research-only.
13. **Are neighbourhood rankings ready for homes?** No; reported-cell rank stability is only moderate for Lden and weak for Lnight, with no material neighbourhood gain.
14. **Are route profiles ready for hiking screening?** No; OSM paths are behaviour demonstrations without independent validation.
15. **Dominant remaining error?** Imputed traffic attribution, followed by absent speeds and target censoring.
16. **Another bounded research iteration?** Yes; improve traffic linkage/speed inputs and obtain independent sub-threshold validation before end-use ranking.
17. **Is national Phase 2 justified?** No. Phase 2C improves internal consistency and holdout metrics, but does not demonstrate stable, physically validated, spatially transferable quiet-end predictions.

## Final status

**PASS WITH ISSUES.** The Phase 2C integrity tests, land-aware ten-region run, deterministic attribution, finite-line integration, bounded quiet-end formulation, OSM path demonstration, audit outputs, and documentation are complete. Phase 1 remains frozen. A national Phase 2 raster or quiet-place ranking is not justified yet.
