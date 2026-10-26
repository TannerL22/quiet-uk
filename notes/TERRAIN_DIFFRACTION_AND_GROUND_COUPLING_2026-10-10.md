# Single-edge terrain diffraction and ground coupling

Completed 10 October 2026, following the
[terrain-profile/mean-plane increment](TERRAIN_PROFILE_AND_PROPAGATION_CHECKS_2026-10-10.md).
The bounded homogeneous single-horizontal-edge boundary calculation is now
implemented and independently checked against published numerical cases.
**411 related tests pass**, including 57 new boundary tests. All **94 public
reference comparisons** across 24 frequency cases pass; maximum difference from
the rounded references is **0.004956 dB**.

This is a numerical prerequisite for estimates. It supplies conditional
boundary attenuation under stated synthetic/reference assumptions, not annual
noise levels, physical validation or the complete regulatory method. All 100
existing real study paths remain withheld, with no new estimated map values.

## Method and preserved source identity

The implementation uses the homogeneous single-horizontal-edge subset of EU
Annex II: ground equations 2.5.15–18, diffraction 2.5.21–22 and coupling 2.5.30–32,
with the Rayleigh selector and below-mean-plane rules in
[Directive 2021/1226](https://eur-lex.europa.eu/legal-content/EN/TXT/?qid=1674232859961&uri=CELEX%3A32021L1226).
The retained [2020 consolidated Annex II](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELLAR:2b501776-736d-11ea-a07e-01aa75ed71a1)
and original 2021 amendment document this bounded chain. Annex II's **Ch = 1**
applies here; the height-dependent low-edge modifier from the older JRC2012
research canary is not part of this calculation. In particular, a ridge whose
corner lies on its fitted side planes must not have its diffraction zeroed by
that older modifier.

Fresh EUR-Lex HTML/PDF attempts returned generic journal/access-check pages;
those responses were excluded as method evidence. PDF request failures are
retained in the source receipts. The earlier complete primary documents remain
pinned by their inventories. No fresh complete consolidation or full current
compliance review is claimed.

Public original expected values and cut profiles come from
[NoiseModelling's committed TC05–TC07 tests](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/1a0158958d3288752daef3a3a60b5f8bc1afacef/noisemodelling-propagation/src/test/java/org/noise_planet/noisemodelling/propagation/AttenuationComputeOutputCnossosTest.java),
pinned to `1a0158958d3288752daef3a3a60b5f8bc1afacef`. The original TC07 profile was
downloaded; the earlier TC05/TC06 profiles, tests, validation document and GPLv3
licence are preserved unchanged. These remain research evidence under their own
licence; third-party implementation code was not ported into the application.
The external engine was **not executed**. Comparison with this public subset
does not amount to full ISO/TR 17534-4 testing or ISO certification.

## Implemented behaviour

The model keeps the ground profile separate from a raised screen's top. It fits
separate source/edge and edge/receiver mean planes, calculates perpendicular
equivalent heights and mirror points, and integrates supplied G by horizontal
path length. Source-platform G is an explicit input; it is 0 in the declared
synthetic road-platform cases and 0.9 in the public fixtures.

A blocked direct ray uses diffraction. For a geometrically clear controlling
edge, both amended conditions must hold: δ > −λ/20 and δ > λ/4 − δ*, with
λ = 340/f at the nominal octave frequency. δ* uses the source and receiver
mirrors from their respective side planes. When selection declines diffraction,
the result uses the common ground plane and its ground-only term. The two side
planes are still retained as diagnostics.

When diffraction is selected, the main and two image diffraction terms are
calculated separately. Only the main term entering the final boundary sum is
capped at 25 dB; the image terms and the uncapped main value used for coupling
remain uncapped. Source-side ground uses its corrected G; the receiver side uses
its own path G directly, including its lower bound. Ground coupling contributes
to the single boundary term and is not added again as a separate ground loss.

The below-mean-plane rule explicitly equates the affected image diffraction to
the uncapped main diffraction and uses that side's ground term for coupling.
Signed heights and original physical endpoint positions remain in the output;
only the method's derived equivalent height/mirror construction is clipped.
Strict selector boundaries, translated elevations, cap behaviour and source,
receiver or both-endpoint special cases have dedicated tests.

The accepted edge is a supplied ridge corner or ideal extruded opaque screen.
The upper profile path must have a single controlling edge, or the clear-ray
candidate must be the largest signed path difference among convex terrain
corners. Unsupported other/multiple controlling edges remain withheld. These
rules operate on declared synthetic or published reference scenes; native raster
steps and an unqualified real scene cannot authorise a numeric prediction.

## Controlled and public results

Twenty-nine scenarios cover planar ground, high/low screens, symmetric,
off-centre, narrow and sloping ridges, a sloping screen, a clear crest, tangent
geometry, main-cap probes, endpoints below mean planes and missing/unsupported
conditions. Four declared G sensitivities and the eight 63–8000 Hz octave bands
produce **904 unique rows**:

| Result | Rows |
|---|---:|
| Diffraction including ground | 344 |
| Ground-only clear-edge branch | 72 |
| Ground-only planar branch | 32 |
| Withheld | 456 |

The withheld rows preserve missing terrain/endpoints/G/edge properties, unknown
or unsupported widths, finite lateral extent, transmission, favourable weather,
multiple/incorrectly selected edges, a valley lacking a declared controlling
edge, and attempted real-area scene qualification. Unknown ground has one
missing case per band, rather than duplicated nominal G rows. None of these
abstentions becomes zero attenuation or a quietness estimate.

| Public case | Homogeneous result |
|---|---|
| TC05: higher receiver above varying terrain | Ground-only in all eight bands |
| TC06: reduced receiver height, geometrically clear crest | Diffraction at 500 and 1000 Hz; ground-only in the other six bands |
| TC07: spatially varying ground and long screen | Diffraction including ground in all eight bands |

The protocol compares 24 final boundary terms plus 70 active intermediate
terms: main/image diffraction, side ground and side coupling. Expectations are
parsed from the retained original test source, independently in the builder and
verifier. Inactive diffraction arrays contain upstream zero placeholders; these
are not treated as numerical diffraction outputs on ground-only branches.
Declared comparison tolerances are 0.02 or 0.1 dB; every observed error is below
0.005 dB.

TC07's original wall has finite geometry. The comparison concerns its supplied
direct horizontal-edge boundary term; the controlled calculation treats that
cross-section as an extruded edge. It does not establish total scene attenuation,
lateral bypass, reflections, atmospheric losses or receiver levels.

## Independent verification and existing real paths

The verifier imports neither the evaluated terrain/propagation modules nor the
builder. It reuses the pinned independent Gauss/QUADPACK and vector geometry
verifier from the preceding increment, checks upper paths with GEOS, calculates
signed excess using NumPy distances/orientation determinants, and uses separate
expanded ground and amplitude-sum coupling equations. Public world coordinates
are independently projected onto their original source/receiver line. Original
fixture geometry, G intervals, array expectations, branch decisions and all
withheld states are checked.

All **472 numeric results** (448 synthetic plus 24 public cases) agree with that
independent calculation, with maximum difference **1.1533 × 10⁻¹¹ dB**. The check
also covers 152 capped-main cases and 64 cases each using the source and receiver
below-mean-plane rules. All 456 synthetic abstentions and the 100 real-path
withheld states are verified. Source/deck elevation, measured G and qualified
horizontal-edge properties remain null on the real paths; no barrier absence is
inferred. Native terrain values and earlier completed inventories are preserved.

The diagram `single_edge_boundary_checks.png` was visually inspected. It shows
the TC06 geometry and selector margins, public boundary spectra and TC07's
coupling contributions. The illustrated values are conditional method results.

## Retained generations and reproduction

| Completed generation | Identity |
|---|---|
| `diffraction_method_sources_v1` | `diffraction-method-sources-12ab269d66bbabd095f9` |
| `terrain_boundary_checks_v1` | `terrain-boundary-checks-ae1fe1332109bba8291b` |
| `terrain_boundary_review_v1` | `terrain-boundary-review-42efe3ca72801fb7c7ba` |

These retain original sources/receipts, protocol, synthetic/reference outputs,
real-path readiness, independent review/figure, frozen code, runtime, credits and
byte inventories. Parent hashes are verified before and after calculations.
Test receipts are `artifacts/terrain_boundary_validation_v1.xml` and `.json`.
Artifacts are local and Git-ignored and need a separate backup. Environment
Agency OGL terrain and OS credits remain in the parent reports. The provider
enquiry's recorded status and unsent barrier request were not changed.

Offline reproduction requires retained originals and fresh output names:

```powershell
.venv\Scripts\python.exe scripts\64_run_terrain_boundary_checks.py --sources artifacts/diffraction_method_sources_v1 --output artifacts/terrain_boundary_checks_NEW
.venv\Scripts\python.exe scripts\65_verify_terrain_boundary_checks.py --inputs artifacts/terrain_boundary_checks_NEW --output artifacts/terrain_boundary_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/terrain_boundary_pytest_NEW
```

Script 63 performs bounded public-source acquisition into a fresh `--output`;
remote availability remains a condition for that operation.

## Next bounded step

Implement and independently benchmark **multiple horizontal-edge diffraction
and finite-width obstacle geometry**, using compatible amended coefficients and
public numerical expectations. Continue withholding unsupported cases rather
than reducing several ridges or a building to a single screen. Favourable
propagation/weather, atmospheric absorption, reflection handling, source-line
integration and remaining physical/annual traffic evidence still precede a
credible real-area annual estimate and quiet-end field validation.

The subsequent [multiple-edge and finite-width increment](MULTIPLE_EDGE_AND_FINITE_WIDTH_PROPAGATION_2026-10-10.md)
now verifies blocked homogeneous paths over thick roofs and multiple ridges.
Clear multiple-obstacle selection and changed controlling image chains remain
withheld; the linked report records the qualification boundary and next step.
