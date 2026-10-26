# Multiple horizontal edges and finite-width obstacles

The next bounded propagation prerequisite is implemented: **blocked homogeneous
paths over multiple ridges, thin screens and thick obstacle cross sections**.
All 192 public numerical comparisons and 489 related tests pass. An independent
calculation agrees across all 576 numerical cases, with a maximum difference
of 2.3910 × 10⁻¹² dB. These are conditional boundary attenuation calculations,
not receiver noise predictions. All 100 existing real-area paths remain withheld.

## Method and original sources

The new module is `src/quiet_uk/multiple_edge_boundary.py`. It implements a
bounded subset of Annex II 2.5.21, 2.5.23 and 2.5.30–32, interpreted with the
2021 amendment. The earlier single-edge and terrain modules and completed
inventories are preserved byte-for-byte.

Firecrawl returned a transport-closed error during source retrieval. Direct
EUR-Lex attempts returned empty access-check responses or unrelated index
content. The EU Publications Office supplied the **original 78-page English
2021/1226 PDF**, through its English Official Journal expression:
[Publications Office original](https://publications.europa.eu/resource/oj/JOL_2021_269_R_0004.ENG).
Original response bytes, content types, redirected URLs, hashes and metadata
are retained. PDF page 6 / OJ L269/70 was rendered and visually inspected to
check amended equation 2.5.23 and Figure 2.5.d. This successful retrieval does
not assert a complete current consolidated implementation or certification.

For multiple diffraction, `e` is the total path length between the first and
last controlling diffraction points. With `lambda=340/f`, the amended
coefficient is `C''=1` when `e<=0.3 m`, otherwise
`C''=(1+(5*lambda/e)^2)/(1/3+(5*lambda/e)^2)`.
The implementation uses an algebraically equivalent scaled expression.
The main and image diffraction terms use `10*log10(3+40*C''*delta/lambda)`,
with the prescribed zero floor. Only the main term entering the final boundary
sum is capped at 25 dB. The ground coupling uses uncapped main and image terms;
separate ground attenuation is zero on the diffraction branch.

The [public NoiseModelling test originals](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/1a0158958d3288752daef3a3a60b5f8bc1afacef/noisemodelling-propagation/src/test/java/org/noise_planet/noisemodelling/propagation/AttenuationComputeOutputCnossosTest.java)
are pinned to commit `1a0158958d3288752daef3a3a60b5f8bc1afacef`.
The original test source, DIRECT cut-profile JSONs, validation documentation
and GPLv3 licence are retained unchanged. No GPL implementation was ported into
the production code. The external Java engine was not executed; comparisons
are to its committed original expected values, not a fresh engine run or ISO
certification.

## Geometry and qualification boundary

Terrain and obstacle roofs are separate inputs. Each explicit obstacle has a
roof polyline: one vertex represents an ideal thin screen; two or more describe
its finite width along the propagation direction. The complete supplied
cross section requires explicit opacity and infinite lateral extrusion.
Unknown vertices or properties withhold calculation. Obstacles are bounded,
ordered, nonoverlapping and strictly inside the source/receiver profile.
Every ground and roof breakpoint is checked so a roof cannot hide terrain
above it.

An upper convex path selects the controlling terrain/roof vertices. Redundant
collinear vertices and shadowed obstacles do not create extra diffractions.
For a sloping roof or several ridges, `e` sums actual distances along the
controlling chain; it is neither horizontal width nor the sum of the widths of
the individual obstacles.

The source mean ground plane ends at the first controlling edge; the receiver
plane begins at the last. Ground factors are weighted over these outer sides,
and their mirrors are calculated independently. Roof height does not replace
the supporting outer ground profiles. Original physical endpoint coordinates
are retained if equivalent heights fall below a mean plane; the amended
below-plane image/coupling rule is applied without moving the physical source.

The present numerical domain is **blocked direct rays with unchanged
controlling image chains**. The solver checks each relevant image chain
against every supplied terrain/roof candidate. A changed controlling chain
withholds the result. Clear multi-edge or finite-width configurations also
withhold until their selection and ground-plane rules are independently
benchmarked. Explicitly empty planar scenes reuse the completed ground-only
calculation. Transmission, finite transverse extent, favourable propagation,
reflections, atmospheric absorption and annual receiver levels remain outside
this increment. Width along propagation does not qualify sound travelling
around the sides of a building or a short barrier.

## Checks and results

Thirty-three declared synthetic scenes produce 1,008 unique octave-band/G
cases. A specified heterogeneous-ground scene and the unknown-G scene are
each evaluated once per band, without duplicate nominal-G rows.

| Synthetic result | Count |
|---|---:|
| Conditional numerical boundary calculations | 552 |
| Withheld | 456 |
| Numerical cases with two controlling edges | 424 |
| Numerical cases with three controlling edges | 32 |
| Numerical single-edge cases | 64 |
| Numerical empty-planar cases | 32 |

These cover roof width and slope, a roof crest, multiple ridges/screens,
shadowed/collinear vertices, the coefficient threshold, capped-main terms,
heterogeneous outer ground and source/receiver below-plane rules. Abstentions
retain unknown terrain/endpoints/G/obstacles/properties, unsupported lateral or
transmission/weather conditions, clear multi-edge geometry, image-chain
changes and attempted real-area qualification.

| Public DIRECT homogeneous case | Checked geometry |
|---|---|
| TC10 | Cubic building, 10 m width, two controlling roof edges |
| TC12 | Polygonal object, two controlling edges |
| TC15 | Scene with four buildings; the direct ray crosses three and has four controlling edges |

Across eight bands per case, all 24 final boundary terms and 168 intermediate
terms pass: main and image diffraction, side ground and coupled ground terms.
The protocol uses 0.02 dB for TC10/15 and 0.05 dB for TC12, stricter than some
upstream assertions. TC12's original table uses `e=6.55 m`, while its original
world-coordinate cut points give `6.51057 m`. The maximum comparison error is
**0.0242145 dB**. Original geometry and expectations are not altered or rounded
to force agreement. Only DIRECT homogeneous boundary arrays are extracted;
later lateral-path assignments and full-scene sound levels are excluded.

Script 68 imports neither the evaluated modules nor builder 67. It reuses the
previously sealed independent quadrature/ground references, reconstructs world
chainages with GEOS projection, selects convex chains with GEOS rather than the
evaluated stack, measures lengths with NumPy, fits planes with Gauss/QUADPACK,
and calculates coupling through a separate amplitude sum. It independently
extracts the original DIRECT expected arrays and checks coverage, unique case
keys, geometry, frames, ground, qualification states and current/frozen bytes.

All **576 numerical cases** (552 synthetic plus 24 public) agree. These include
480 multi-edge cases, 354 capped-main cases, and 64 cases each using the source
and receiver below-plane rules. All 456 synthetic abstentions and all 100
real-path withheld states are checked. Real source/deck elevation, measured G
and complete qualified obstacle cross sections remain null. Native terrain
steps and provisional DSM roof summaries do not become acoustic obstacles.

The new 78 focused unit tests and the existing 411 related tests all pass,
with zero failures, errors or skips. They cover published cubic-building
expectations, compatibility with the completed single-edge calculation,
geometry invariance, cap placement, ground semantics, missing inputs and
malformed/unsupported geometry. The independent figure
`artifacts/multiple_edge_review_v1/multiple_edge_checks.png` was visually
inspected and shows original building geometry and conditional boundary terms.

## Retained generations and reproduction

| Completed generation | Identity |
|---|---|
| `multiple_edge_sources_v1` | `multiple-edge-sources-c1338243b9a7932ada06` |
| `multiple_edge_checks_v1` | `multiple-edge-checks-284516037f9aa0cfa308` |
| `multiple_edge_review_v1` | `multiple-edge-review-9e790b56564cc93a6ebb` |

These retain original source receipts, the amended PDF/metadata, public
fixtures/licence, protocol, outputs, real-path readiness, independent review,
figure, frozen code, runtime and byte inventories. Parent and code hashes are
checked before and after construction/verification. Test receipts are
`artifacts/multiple_edge_validation_v1.xml` and `.json`. Artifacts remain local
and Git-ignored and require a separate backup. Existing Environment Agency
OGL and OS credits remain in the parent physical-input reports.

Offline reproduction requires the retained sources and fresh generation names:

```powershell
.venv\Scripts\python.exe scripts\67_run_multiple_edge_checks.py --sources artifacts/multiple_edge_sources_v1 --output artifacts/multiple_edge_checks_NEW
.venv\Scripts\python.exe scripts\68_verify_multiple_edge_checks.py --inputs artifacts/multiple_edge_checks_NEW --output artifacts/multiple_edge_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/multiple_edge_pytest_NEW
```

Script 66 performs five bounded public reads into a fresh `--output`. Remote
availability is required for acquisition, but not for reproducing the checks.

## Next bounded step

Qualify clear-ray selection for multiple/finite-width obstacles and image
paths that change controlling edges, with compatible ground-plane construction
and independent public expectations. Then complete favourable/weather and
atmospheric propagation, reflections and source-line integration. Historical
physical/annual traffic qualification and independent quiet-end field evidence
still precede a credible real-area annual noise release.

The subsequent [clear-sightline and remaining horizontal-obstacle increment](CLEAR_SIGHTLINES_AND_OBSTACLE_CONFIGURATIONS_2026-10-10.md)
now resolves physical-edge selection, composite roof support and the previously
held virtual-hull/returning-leg cases. Its report records the reference geometry
limits and the next favourable-propagation/atmospheric-absorption checkpoint.
