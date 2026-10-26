# Terrain/screen reflections and road source-line integration — 11 October 2026

This completes the requested controlled calculation increment. A road now has a
bounded numerical route from spectral sound power **per metre**, through direct
and single reflected propagation paths, to a conditional receiver result. This
is a prerequisite for filling noise-map gaps credibly. It does not qualify the
available real traffic, wall, ground or weather inputs as accurate annual noise.
No pilot is required for this increment.

## What changed

`src/quiet_uk/screened_reflection.py` extends the previously frozen clear-wall
calculation to reflection before/after terrain or screens and between diffraction
edges. It uses the adjacent selected physical diffraction leg for the reflecting
ray hit and wall-top retrodiffraction. The homogeneous leg is straight. Every
favourable physical leg retains the radius of the **whole unfolded S/R path**,
`max(1000 m, 8 × unfolded 3D S/R distance)`. Recomputing the radius from a shorter
leg would give the wrong result: in TC18 at 63 Hz it gives approximately 3.19 dB
instead of the correct 2.77 dB wall-top loss.

The underlying terrain, ground, horizontal-edge and weather kernels remain
byte-preserved. Reflector tops are never inserted into the ground mean plane.
The TC27 duplicated ground/topography coordinate is merged only when elevations
agree, and zero-length ground intervals are omitted. Negative absolute elevation
is allowed because endpoints and surfaces share a datum. Reflection exactly at
a selected diffraction vertex remains withheld; this is an unimplemented joint
interaction rather than a path with known zero energy.

`src/quiet_uk/road_line_integration.py` adds composite midpoint integration of
incoherent line power. Each cell receives source power per metre × its declared
length. At each source point it sums the direct/reflected energies within each
weather condition, mixes the supplied directional weather probability, and then
integrates along the road. It never averages decibels or multiplies source length
twice. The adapter checks finite-wall plan geometry against the world endpoints
and unfolded profile, rejects duplicate wall paths and excludes overlapping
physical road source intervals. Distinct parallel carriageways remain distinct.

Coordinate metres, horizontal/3D length basis, complete path support and known
path/emission breakpoints must be declared. These are caller input qualifications,
not facts inferred by this integrator. Initial cell length is at most half the
cell's closest 3D receiver distance. Successive uniform refinements must settle
twice, with at least three refinements, within 0.005 dB for the retained examples.
The default budgets are 12 refinements and 20,000 evaluations per octave. This
is an explicit numerical policy: the Annex leaves transport source segmentation
outside its scope. A receiver on the source line, exhausted budget or unresolved
positive contribution yields no receiver level. Known zero source power or an
absent reflection contributes zero; missing data is never substituted with zero.
Unknown weather leaves known H/F integrals available while withholding the mix.

## Evidence and practical limits

Retained original TC16/17/18/26/27 direct/reflected profiles cover clear reflection,
intervening terrain, screen diffraction, favourable wall misses and a low road
source beside a terrain step. **40 public octave cases / 448 scalar comparisons**
pass the stated tolerances. The 0.02 dB target is met except TC27 homogeneous
125 Hz receiver value: 36.149884 dB versus printed 36.12 dB, a **0.029884 dB**
difference. The original test explicitly allows `ERROR_EPSILON_VERY_LOW=0.1 dB`
for this receiver assertion. The exception stays visible alongside both tolerances;
the expected value and physical equations are not fitted to remove it. Published
NaN intermediate expectations are excluded, not replaced by zero.

Seventeen controlled scenes produce **136 octave cases / 272 H/F branches**:
144 numeric, 96 withheld and 32 known absent. They cover reflection on either
side of terrain/screens, between two screens, before a thick roof, frequency
dependent curvature, unknown ground/roof/absorption/view/weather and coincidence.

Ten road examples produce **80 octave integrals**: analytic free field, directional
weather, clear/direct screen paths, direct plus reflected paths, screen/reflection
interaction, split-source invariance, and three periods connected to the existing
vehicle-emission kernel. The emission example uses entirely synthetic 2,400
cars/day, 70 km/h, reference surface and explicit day/evening/night fractions
0.6/0.2/0.2. Source height is 0.05 m above the declared flat road. These inputs
exercise units and period allocation; they are not observed local traffic or a
qualified annual indicator.
Emission retains the kernel's flat/dry/steady 20 °C reference case; propagation
air is separately supplied at 10 °C, 70% RH and 101.325 kPa for this interface
test. Local temperature corrections remain outside the source-emission kernel.

Script 80 imports earlier independent numerical references, never the evaluated
modules or builder. It uses GEOS projection of raw public physical legs, numerical
mean-plane/ground calculations, circle centres and QUADPACK arc integrals, Decimal
air absorption, and energy products/sums. Adaptive Gauss–Kronrod quadrature
independently checks the road integrals, with an exact finite free-field line
integral as an additional reference. Numerical agreement establishes construction
fidelity; neither the refinement threshold nor independent agreement is a
real-world error bar. The sealed review/validation receipts provide final values.
The independent check verifies all 352 reflection branches and 448 published
scalar comparisons. Maximum independent reflection difference is
**1.063 × 10⁻¹¹ dB**. All **240 H/F/mixed road integrals** agree with independent
adaptive quadrature within **0.0002555 dB**, and 24 emission-to-line octave inputs
are independently reconstructed from scalar vehicle equations. The figure was
visually inspected and the final byte inventories verified.

**767 related tests pass**, including 49 new focused tests. They check source power
conservation, exact finite-line integration, rotation/datum changes, subdivision,
separate carriageways, duplicate/overlapping sources and reflections, directional
weather, unknown positive contributions, unused zero-probability conditions,
singular receiver geometry, budgets and the existing emission-unit interface.

All **100 retained real-location probes remain withheld**. Existing physical
input readiness still has no qualified acoustic building heights or barrier
absorption/sealing. This work does not perform an automatic GIS reflection/path
search, multiple/inclined/lateral reflections, transmission or scattering. The
amended lateral rules exclude segmented roads and reflection images, so no road
finite-obstacle shortcut is introduced. Full current-method qualification,
external engine execution and real-area accuracy validation remain false.

## Sources, retained artifacts and reproduction

The calculations derive from original [2015 Annex II](https://eur-lex.europa.eu/eli/dir/2015/996/oj),
its retained 2018 correction and the [2021 amendment](https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj).
The original PDFs are retained, including source-line power and segmentation
sections, receiver energy addition, reflection/retrodiffraction and favourable
curvature amendments. The unchanged public JSON/Java fixtures and GPLv3 licence
come from NoiseModelling commit `1a0158958d3288752daef3a3a60b5f8bc1afacef`.
No external Java engine was executed and no implementation code was ported.
The fresh Firecrawl discovery search used excerpts only; equations rely on the
retained original documents. Script 78 made four bounded public GETs (6,202 bytes).

Current completion evidence is `artifacts/screened_line_sources_v1`,
`artifacts/screened_line_checks_v3`, `artifacts/screened_line_review_v2` and
`artifacts/screened_line_validation_v1.{json,xml}`. They retain source receipts,
protocol, synthetic/public/line results, real-input readiness, frozen code,
independent verification, figure and byte inventories. Development checks v1/v2
and the unsealed initial review v1 are preserved and are not completion evidence.
Artifacts stay local and Git-ignored. Prior production/test/reference code and
parent inventories are checked for byte preservation in the validation receipt.

| Current generation | Identity | Inventory SHA-256 |
|---|---|---|
| `screened_line_sources_v1` | `screened-line-sources-f83901de5d9cea44a51a` | `ae1ffecaa465902c4d400fe4073a50c6f6e0cfb04858ce80a79e163f2ef9573b` |
| `screened_line_checks_v3` | `screened-line-checks-af2a1323e5fc0b833342` | `45948eb734d26e2036a5c420c4529a4ab6db9528b4e2dcfcffed62079aedb316` |
| `screened_line_review_v2` | `screened-line-review-e87cd70c5f41763e22d6` | `2efc162bfaf7cf499ba0a7c9ba50f4eeb22237982701c4f265295d7bc5d87b28` |

Reproduce offline into fresh outputs:

```powershell
.venv\Scripts\python.exe scripts\79_run_screened_line_checks.py --output artifacts/screened_line_checks_NEW
.venv\Scripts\python.exe scripts\80_verify_screened_line_checks.py --inputs artifacts/screened_line_checks_NEW --output artifacts/screened_line_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_screened_reflection.py tests/test_road_line_integration.py tests/test_reflection_path.py tests/test_favourable_propagation.py tests/test_sightline_boundary.py tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/screened_line_pytest_NEW
```

The next prerequisite is qualifying reusable real inputs and complete road/path
geometry against independent sound evidence. More numerical tests of the same
implemented cases do not establish that missing real inputs are accurate. Further
propagation features require an explicit scope decision and suitable benchmarks.

11 October follow-up: the [real-road scene builder](REAL_ROAD_SCENE_BUILDER_2026-10-11.md)
now connects the retained geographic inputs to this interface. It preserves
source-line length/budgets and exports direct/reflected geographic evidence plus
per-road blockers. Real acoustic qualification remains open; this follow-up does
not turn provisional input values into annual receiver levels.
