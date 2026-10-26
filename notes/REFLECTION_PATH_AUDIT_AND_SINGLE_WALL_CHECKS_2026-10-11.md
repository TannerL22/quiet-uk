# Reflection-path audit and controlled single-wall checks

Completed 11 October 2026. The next bounded increment now evaluates a single
vertical wall's reflected transfer under homogeneous and favourable propagation,
including spectral wall absorption, retrodiffraction and a known absent curved
reflection. **136 public scalar comparisons and 718 related tests pass.**
No real-area noise prediction or map gap was created.

## Real reflection inputs

The verified 144 km² north-Heathrow inputs support discovery, not qualified walls.

| Retained evidence | Reflection qualification still missing |
|---|---|
| 50,495 generalised building footprints; 37,167 provisional surface-height candidates | Surveyed façade planes, bases/tops/extents, wall height uncertainty and historical compatibility; all qualified acoustic heights are missing |
| 1,206 historical barrier-related ways | Qualified wall top/common datum, sealing/transmission, per-band absorption and physical historical presence; all absorption/sealing entries remain null |
| 100 retained real-path probes | Complete reflection geometry and incident-view dimensions, spectral absorption, plus previously missing source, ground, obstacle and weather qualifications |

The new readiness records retain all earlier missing states and add explicit null
wall geometry, top/base datum, incident-view dimensions, absorption and historical
presence. Generalised footprints do not establish acoustic façades. Tagged
materials or heights do not provide measured absorption or certified wall tops.
Missing walls cannot be treated as a reflection-free scene. All 100 probes remain
withheld. The validation receipt independently recounts the original input rows.

## Method and bounded implementation

The source audit uses the byte-retained original 2015 Annex, its 2018 correction
and the 2021 amendment. Reflection equations are on original PDF pages 41–43
(OJ L 168/41–43); the amended eligibility rules are on PDF page 9
(OJ L 269/73). Relevant original pages were rendered and visually inspected.
See [the original Annex](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32015L0996),
[2018 correction](https://publications.europa.eu/resource/oj/JOL_2018_005_R_0012.ENG)
and [2021 amendment](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32021L1226).

Image-source power receives `10 log10(1 − α)` and loses the retrodiffraction term
(equations 2.5.35–38). In positive attenuation convention, the absorption loss is
added once. Retrodiffraction uses the negative wall-top path excess in the unfolded
section: straight lengths for H, corresponding circular arc lengths for F. Its
single-edge expression has no multiple-edge coefficient or 25 dB barrier cap.
At zero excess it is `10 log10(3)`; it reaches zero at `δ′ = −λ/20`.
Ground reflection remains inside the existing boundary term.

The new `reflection_path.py` adds horizontal mirror geometry, straight/curved
wall-hit heights, spectral absorption, top loss, conditional path-energy addition
and weather weighting. Terrain support follows both physical legs cumulatively.
Divergence and atmospheric attenuation use the unfolded image-to-receiver 3D
distance; no curved detour distance is substituted. Earlier propagation modules
and their frozen reference generations remain byte-identical.

Eligibility requires an actual wall hit, slope below 15° and at least 0.5 m in
both incident-view dimensions. This increment accepts **exactly vertical walls**;
inclined walls inside the wider method domain are explicitly withheld. Apparent
dimensions must be supplied for each weather condition; raw side length is not
silently substituted at grazing incidence. Selected diffraction interactions,
multiple walls, railway vehicle/body effects and lateral paths are outside this
implementation. Extremely large tops outside the circular domain also abstain.

A known wall miss or ineligible reflector contributes zero energy. Unknown inputs
withhold the contribution. A supplied probability is retained even when only one
weather condition reflects; it is never renormalised. Zero-probability unknown
conditions do not invalidate a computable weighted transfer. With no probability,
conditional branches remain available and the mix remains unknown. `α = 1` has an
explicit zero-energy extension; the Annex logarithmic equation retains its `α < 1`
domain. No infinite values are serialized to represent absent paths.

Two correctness fixes are included: coordinate-scale floating-point guards retain
wall endpoint hits and reject a source on the wall after rotation/translation;
top-level status follows a computable weather mix when an unknown condition has
zero weight.

## Public benchmarks and independent checks

Five small public JSON files (6,756 bytes total) were acquired from the pinned
[NoiseModelling revision](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/tree/1a0158958d3288752daef3a3a60b5f8bc1afacef).
Original GPL licence, Java expected arrays, cuts and acquisition receipts are
retained unchanged. The implementation uses own mathematical derivations;
the external Java engine was not executed or ported.

| Case | Check |
|---|---|
| TC16 | Both H/F reflected paths, mixed terrain/ground, all eight wall absorption bands, H top loss zero and F 63 Hz top loss 0.684531 dB versus published 0.68 dB |
| TC26 | Road reference with H reflection present and F ray above the wall; supplied 50% favourable probability adds 3.0103 dB loss to the H-only reflection contribution |
| TC17 | Original direct/reflection cuts retained for the next screened-reflection increment; no completed numerical benchmark claimed |

The 16 public octave cases give **136 distinct scalar comparisons**: reflected
boundary/levels, absorption/top loss where published, divergence/air loss,
weather-mixed reflection and direct-plus-reflected reference power. All pass the
declared 0.02 dB tolerance; maximum difference is **0.013468 dB**. The reference
93 dB source power and A-weighting serve only to compare original expectations.
They are not supplied road-emission or real receiver predictions.

The public paths explicitly assume incident-view eligibility with 1 m supplied
dimensions and wall base at local supplied ground. These are calculation
assumptions, not sourced façade measurements. Each original cut's reflection
coordinate is 1 mm from the exact mirror intersection; this is disclosed and
checked separately within 2 mm. Original cut coordinates and expected arrays
were not adjusted to improve agreement.

Forty-one synthetic scenes produce **328 octave cases / 656 H/F branches**:
350 numeric, 120 known absent and 186 withheld. They exercise spectral and
near-perfect absorption, exact/below/above wall-top hits, varied ground, datum
shifts, view dimensions, slope limits, unknown inputs, screened interactions,
weather endpoints and unknown conditions with zero probability. Nineteen plan
cases check mirror geometry, reciprocity, orientation, BNG translation, wall
endpoints and missing or ineligible geometry.

Script 77 imports earlier independent references, never the evaluated reflection
module or builder. It reflects the receiver and solves a 2×2 intersection,
projects unchanged physical legs using GEOS, constructs circle centres, integrates
arcs with QUADPACK, uses numerical mean planes and expanded ground equations,
calculates air absorption with 50-digit Decimal arithmetic, and multiplies/adds
energy transfers. It separately re-parses the final original reflection arrays.
All numeric/absent/withheld branches, both public direct paths and combined powers,
plan cases and real-path states pass. Maximum independent attenuation difference
is **7.54 × 10⁻¹¹ dB**. This is numerical agreement, not measured acoustic accuracy.

The 86 new focused tests plus the preceding 632 related tests pass: **718 tests**,
zero failures/errors/skips. JUnit and hash receipts are retained in
`artifacts/reflection_validation_v1.xml` and `.json`. The independent figure was
viewed and is legible. Full current-method, ISO and real-area qualifications
remain false.

## Retained completion evidence and reproduction

| Current generation | Identity | Inventory SHA-256 |
|---|---|---|
| `reflection_sources_v1` | `reflection-sources-313e4ecea4ca57db25d4` | `67a368762cebe2dcc008f7bb566b48fd3dbe2909aa7803511ece7961461452b2` |
| `reflection_checks_v3` | `reflection-checks-9d3296a03b7f756fff2a` | `42009321cb30bf40f16801ad0d4c510f99e840a1021ae39c8df35af617b06d0e` |
| `reflection_review_v4` | `reflection-review-915c74f61ce3a3b7cce4` | `3faa82a3338851c9dbc735a486a93b9d6d5db564aaf6253d1432467d24576c1f` |

These include inputs/receipts, protocol, results, readiness, frozen code, figure
and byte inventories. Earlier development generations are preserved; the three
listed generations are the completion evidence. Artifacts remain local and
Git-ignored. Parent inventories and frozen production/reference code are verified
before and after evaluation; previous tests are byte-preserved.

Reproduce offline into fresh outputs with retained sources:

```powershell
.venv\Scripts\python.exe scripts\76_run_reflection_checks.py --sources artifacts/reflection_sources_v1 --output artifacts/reflection_checks_NEW
.venv\Scripts\python.exe scripts\77_verify_reflection_checks.py --inputs artifacts/reflection_checks_NEW --output artifacts/reflection_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_reflection_path.py tests/test_favourable_propagation.py tests/test_sightline_boundary.py tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/reflection_pytest_NEW
```

Script 75 performs a new bounded public acquisition into a fresh output when
needed; offline reproduction does not require another download.

## Applicability decision and next increment

The lateral-path audit resolves an important scope question: the amended rules
require a real point source, excluding segmented extended sources and reflection
image sources. The retained original section also places these equations under
industrial noise. They therefore cannot be silently used as a finite-obstacle
bypass for segmented roads. No road lateral path is added on that basis.
[Applicable amendment, OJ L 269/73](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32021L1226).

Next benchmark **reflections with intervening terrain or screens**, starting with
retained TC17 and then TC18. Retrodiffraction in these cases uses the relevant
source-to-diffraction segment; applying the current clear-path formula to the final
receiver would be unsupported. Source-line integration follows. Historical
geometry/traffic, qualified wall/ground/source/weather inputs and independently
matched quiet-end measurements still precede credible real-area absolute noise.

11 October follow-up: the [terrain/screen reflection and road source-line
increment](SCREENED_REFLECTIONS_AND_ROAD_LINE_INTEGRATION_2026-10-11.md) completes
the above controlled next steps. It uses the physical leg adjacent to diffraction,
retains global favourable curvature, and integrates source power per metre through
direct/reflected paths. All 767 related tests pass; the newer report and sealed
generation receipts supersede the next-step ordering above. Real-input and
full-method qualifications remain open.
