# Clear sightlines and remaining horizontal-obstacle configurations

The bounded homogeneous cross-section solver now handles clear sightlines,
thick roofs below a ray, open valleys and configurations previously withheld
because a virtual convex hull changed edges or a mirrored endpoint projected
before a selected edge. **All 614 public comparisons and 568 related tests
pass.** All 1,064 numerical cases agree with an independent calculation, with
a maximum difference of 9.1749 × 10⁻¹² dB.

These are conditional boundary attenuation calculations, not measured sound
or annual receiver levels. All 100 existing real-area paths remain withheld.
The previous single-edge and blocked multiple-edge implementations and their
completed generations are preserved unchanged.

## Method and decisions

The new module is `src/quiet_uk/sightline_boundary.py`. It applies the retained
Annex II / 2021 homogeneous equations to explicitly complete synthetic or
reference cross sections. Its two key changes are physical-edge selection and
the surface supporting the mean-plane/ground calculation.

For a blocked ray, the physical terrain/roof envelope selects the controlling
edges. For a clear ray, the solver searches actual terrain corners and roof
edges for the largest signed path difference. It applies both strict amended
tests, `delta > -lambda/20` and `delta > lambda/4 - delta_star`, with
`lambda=340/f`. `delta_star` uses only the selected edge D and the two side-plane
mirrors, as specified in the [original 2021 amendment](https://publications.europa.eu/resource/oj/JOL_2021_269_R_0004.ENG).
If either test fails, a common mean surface plane and ground-only boundary
term are used. A clear geometric line does not automatically mean zero
diffraction in every frequency band.

The edges selected for the **physical** path are retained for pure image
diffraction. Each image pair has its own direct-distance baseline. Rebuilding
a convex path around the virtual endpoint is recorded as a diagnostic; that
different edge chain is not substituted into the boundary equation. TC21's
receiver mirror provides a public numerical check of this distinction. The
old conservative refusal for changing virtual hulls is resolved without
replacing the selected physical chain with a different propagation model.

A mirrored endpoint can project before a fixed diffraction edge. The forced
path then has a returning leg with positive path excess; it is not the
negative-excess single-edge configuration below a ray between its endpoints.
TC14 independently checks this case. This extension removes the old blanket
ordering refusal while retaining the selected physical geometry.

Terrain input remains unchanged. On intervals occupied by a finite-width
obstacle, the supplied roof replaces surface height and uses an explicitly
declared `roof_g`. Vertical wall jumps have no positive horizontal support and
are omitted from the least-squares integral, rather than smoothed into ramps.
The source side ends at the first selected edge and the receiver side begins
at the last; either side may include other roof support. If no diffraction is
selected, the common plane includes all supported roofs. Unknown roof G
withholds a result; zero is an explicit reference/synthetic assumption, never
an observed value inferred from a building footprint or DSM.

Redundant collinear terrain/roof samples and acoustic G interval boundaries
do not create extra diffraction edges. A complete, explicit valley with no
downward corner can take the ground-only branch. Unknown obstacle inventory
is still distinct from an explicitly empty inventory.

The main diffraction term alone is capped at 25 dB. Ground coupling retains
uncapped image/main terms and contributes within the boundary calculation;
no separate ground term is added again. Original physical source/receiver
coordinates and the amended below-mean-plane rules remain intact.

## Original references and compatibility limits

The source generation retains the original 2020 Annex II text, original
78-page 2021/1226 PDF and its EU metadata, together with 12 public DIRECT
cut-profile cases. Six additional JSONs were acquired through bounded public
reads; earlier originals were copied from verified parent generations.
Firecrawl search succeeded on the Secondary account. Older JRC drafts returned
by search were not substituted for the retained amended method.

The [NoiseModelling original test expectations](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/1a0158958d3288752daef3a3a60b5f8bc1afacef/noisemodelling-propagation/src/test/java/org/noise_planet/noisemodelling/propagation/AttenuationComputeOutputCnossosTest.java)
and fixtures remain pinned to commit
`1a0158958d3288752daef3a3a60b5f8bc1afacef`, under their retained GPLv3 licence.
No GPL implementation was ported into production. The external Java engine
was not executed. These checks reproduce committed reference expectations;
they do not constitute full-method qualification or ISO certification.

There is a material reference geometry difference: TC11/TC14's original tables
give positive distances for diffraction points below their fitted mean plane.
The retained Annex text states that a negative equivalent height is replaced
with zero. The new implementation follows that null-height rule and records
the signed physical offset separately. It does **not** claim agreement with
those positive height entries. The side ground lower bound dominates in the
published bands, so the boundary and intermediate dB arrays can agree despite
that geometry-table difference. Physical endpoints are not moved to fit the
reference values.

| Public DIRECT cases | Checked scope |
|---|---|
| TC05, TC06, TC16 | Clear terrain, including frequency-dependent selection |
| TC07 | Ideal thin-screen horizontal boundary term |
| TC10, TC12, TC15 | Previous thick/multiple-obstacle comparisons retained |
| TC11, TC14 | Taller receivers, roof support and virtual returning legs |
| TC13 | Building on varying terrain and ground |
| TC21 | Physical selected edge versus a different virtual convex hull |
| TC23 | Terrain plateau with side ground coupling |

There are 96 octave-band reference cases and **614 unique scalar comparisons**:
96 final boundary terms and 518 active intermediate terms. Inactive upstream
zero placeholders are not treated as diffraction outputs. DIRECT arrays are
extracted before later lateral-path reassignments. Declared tolerances are
0.02 dB, or 0.05 dB for TC12/TC14, whose tabulated dimensions differ from their
original exact world-coordinate cuts. The maximum error is **0.0418325 dB**.
Neither original geometry nor expected values are changed to force agreement.

## Independent verification and tests

Forty-five synthetic scenes produce 1,392 unique octave-band/G cases:

| Synthetic state | Count |
|---|---:|
| Diffraction including ground | 676 |
| Ground-only clear-edge branch | 164 |
| Ground-only no-edge branch | 128 |
| Withheld | 424 |

The original 33 scenes are retained with explicit roof ground assumptions;
12 new scenes cover clear/tangent roofs and screens, roof G sensitivity,
redundant sampling, valleys, virtual returning legs and missing roof/lateral
properties. Unknown-G and fixed heterogeneous-G cases are each evaluated
once per band, avoiding duplicate nominal-G rows.

Script 71 imports neither the evaluated modules nor builder 70. It uses the
previously sealed independent references, GEOS world-coordinate projection
and convex envelopes, NumPy surface interpolation and vector distances,
Gauss/QUADPACK plane fits, expanded coefficient/ground expressions and a
separate amplitude-sum coupling calculation. Original fixture geometry,
roof assumptions, expectations, branch decisions, output uniqueness, parent
inventories and current/frozen code hashes are checked independently.

All **1,064 numerical cases** (968 synthetic and 96 public) agree. They include
408 clear/no-edge cases, 194 cases with a changed diagnostic virtual hull,
40 returning-image-leg cases, 386 main-cap cases, and 64 cases each exercising
the source/receiver below-plane rules. All 424 synthetic abstentions and all
100 real-path withheld states are also checked. The new 79 focused tests plus
the previous 489 related tests pass with zero failures, errors or skips.
The independent `sightline_checks.png` figure was visually inspected.

Unknown source/deck height, measured acoustic ground and complete qualified
historical obstacle geometry remain null on the real paths. Finite lateral
extent, transmission, favourable weather, reflection paths, atmospheric
absorption, source-line integration and annual levels remain outside this
increment. Native cell steps and provisional roof candidates do not become
qualified acoustic geometry, and missing cells receive no new estimates.

## Retained generations and reproduction

| Completed generation | Identity |
|---|---|
| `sightline_sources_v1` | `sightline-sources-fcd9c5dda702bfdfa8f7` |
| `sightline_checks_v1` | `sightline-checks-b7763997e50003378053` |
| `sightline_review_v1` | `sightline-review-e3b347cc434eae92791f` |

These retain original source bytes/receipts/licence, method lineage, protocol,
conditional results, real-path readiness, independent review/figure, frozen
code, runtime and byte inventories. Test receipts are
`artifacts/sightline_validation_v1.xml` and `.json`. Earlier completed input,
evaluation and verification generations remain byte-preserved. Artifact files
are local and Git-ignored; existing EA OGL/OS credits remain in the physical
input reports.

Use fresh names to reproduce offline with retained originals:

```powershell
.venv\Scripts\python.exe scripts\70_run_sightline_checks.py --sources artifacts/sightline_sources_v1 --output artifacts/sightline_checks_NEW
.venv\Scripts\python.exe scripts\71_verify_sightline_checks.py --inputs artifacts/sightline_checks_NEW --output artifacts/sightline_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_sightline_boundary.py tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/sightline_pytest_NEW
```

Script 69 performs six bounded public reads into a fresh `--output` and requires
remote availability. The offline checks do not require another acquisition.

## Next bounded step

Review and independently benchmark **favourable propagation and atmospheric
absorption**, using the amended curved-ray/ground equations and explicit
temperature/humidity/meteorological assumptions. Reflections/lateral bypass,
source-line integration, historical physical/annual traffic qualification and
independent quiet-end evidence still precede a credible real-area noise release.

Completed subsequently: the [favourable propagation and atmospheric absorption
report](FAVOURABLE_PROPAGATION_AND_ATMOSPHERIC_ABSORPTION_2026-10-10.md) retains
911 public comparisons, independent curved/absorption calculations and 632
passing related tests. Its next bounded step is the reflection-path audit and
a controlled single-wall benchmark.
