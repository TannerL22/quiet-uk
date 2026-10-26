# Terrain profiles and controlled propagation geometry

Completed 10 October 2026. The next prerequisite from the
[ground-cover/barrier increment](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md)
is implemented: distance-weighted mean ground planes, perpendicular endpoint
heights, projected distances, separate planes at supplied terrain edges, and
controlled slope/ridge checks. **354 related tests pass**, including 40 terrain
tests. Independent integration, geometry and original-cell checks also pass.

These results qualify numerical construction within the stated research cases.
They do not qualify physical terrain accuracy, real-area noise estimates, the
complete current CNOSSOS method or ISO certification. The map has no new estimated
noise values from this increment.

## Method and original evidence

The implementation follows the continuous least-squares construction in
[JRC72550 / CNOSSOS-EU 2012](https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf),
equations VI-3/4, printed page 77, and the orthogonal geometry on pages 84–85
(DOI 10.2788/31776). The original PDF is retained from the emission-source
generation; checked page images/text and its byte identity accompany this increment.

The fit minimises the integral of squared elevation error **over horizontal
distance**. It integrates the supplied piecewise-linear profile exactly; adding
unevenly spaced vertices on the same terrain does not change its statistical
weight. A point-only ordinary least-squares fit gives a different, biased result
in the retained sampling example. Centred, normalised coordinates avoid raw
large-coordinate moment cancellation.

Source/receiver heights are measured perpendicular to the mean plane. Projected
distance along that plane is recorded separately from direct Euclidean distance.
Negative signed equivalent heights are retained diagnostically, with only the
derived equivalent height set to zero and the equivalent/mirror points placed on
the plane. Original endpoint elevations are preserved. The wording on the 2012
page has an above/below inconsistency; the explicit below-plane correction in the
[2020 consolidated Annex II](https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELLAR:2b501776-736d-11ea-a07e-01aa75ed71a1)
was checked and retained. This dated consolidation does not establish complete
current regulatory compliance.

Public original geometry and rounded expected values come from
[NoiseModelling's committed tests](https://github.com/Universite-Gustave-Eiffel/NoiseModelling/blob/1a0158958d3288752daef3a3a60b5f8bc1afacef/noisemodelling-propagation/src/test/java/org/noise_planet/noisemodelling/propagation/AttenuationComputeOutputCnossosTest.java)
and the matching TC05/TC06 cut-profile JSON, pinned to commit
`1a0158958d3288752daef3a3a60b5f8bc1afacef`. The six unchanged public files include
the geometry implementation, test source, two profiles, validation documentation
and GPLv3 licence. They are retained under their own licence; third-party code
was not ported into the application. The project identifies these cases with
ISO/TR 17534-4:2020. Only the public geometry subset is checked here; the external
NoiseModelling engine was **not executed** and the full ISO suite was not tested.

## Controlled results and withheld branches

Fifteen scenes cover slopes −0.5, −0.2, 0, 0.2 and 0.5, a large vertical datum,
symmetric/off-centre/narrow ridges, two ridges, a clear valley, tangent terrain,
missing terrain, missing source elevation and missing ground properties.

Four declared ground sensitivities (G = 0, 0.3, 0.7, 1) and eight octave bands
produce **456 unique cases: 168 conditional ground results and 288 withheld
results**. The unknown-G scene has one missing state per band, rather than four
duplicate nominal cases. Numeric results are restricted to explicitly planar
synthetic scenes and the checked JRC2012 63–4000 Hz ground subset. The 8000 Hz
rows are withheld scope probes. Ground/source-platform assumptions remain those
of the earlier synthetic canary; these are ground boundary terms, not annual
receiver levels.

| Withheld reason | Cases |
|---|---:|
| Nonplanar terrain needs qualified diffraction/selection | 192 |
| Missing terrain support | 32 |
| Missing endpoint elevation | 32 |
| Outside checked frequency scope | 24 |
| Unknown ground G | 8 |

An upper convex profile path records whether supplied terrain blocks or touches
the straight ray and records its geometric path difference. Separate source/edge
and edge/receiver mean planes are calculated where supplied edges exist. These
are **geometry diagnostics**: no single/multiple-ridge attenuation is released.
A valley's geometrically clear ray also stays withheld acoustically; frequency-
dependent Rayleigh selection and ground/diffraction coupling still need a
compatible qualified implementation. Native pixel steps do not establish physical
diffracting edges or finite barrier widths.

Four external geometry cases check five fields each: slope, intercept, source and
receiver equivalent heights, and projected distance. They include the TC05 and
TC06 common planes and both TC06 side planes. **All 20 scalar comparisons pass**
within 0.0051 of the original values rounded to two decimals; the largest error
is 0.004926617. The independent verifier parses expectations from the retained
original test source instead of trusting the builder's copied numbers.

## Existing native terrain paths

The unchanged north-Heathrow study has a 4 km² core and 144 km² source halo in
EPSG:27700, using the retained native 2 m ODN terrain raster. The existing 100
straight geometry probes are now traced through native containing cells by
their exact represented lengths. Original heights/masks are retained with a
declared **piecewise-constant diagnostic representation**; no interpolation,
terrain smoothing or new acoustic edge interpretation is introduced.

All **32,908 intervals** have valid original terrain support. They represent
32,907 distinct path/cell supports: one corner crossing has a retained
approximately 2.2 × 10⁻¹¹ m floating-point fragment in the same cell as an
adjacent interval. The independent check compares both each interval's support
and summed cell lengths, with 10⁻⁷ m absolute geometry tolerance. The maximum
cell-length difference is 3.6 × 10⁻¹¹ m. Full path length is conserved.

For diagnostic frames only, the earlier at-grade source +0.05 m and receiver
+4 m assumptions are explicit. The fitted plane puts **55 source and three
receiver equivalent heights below zero**; the derived heights are clipped as the
method specifies while signed heights and actual assumed coordinates remain.
This demonstrates why treating an entire ridge as a flat scene can misrepresent
the geometry. It does not prove physical source burial or terrain shielding.
Every path's qualified source/deck elevation, measured acoustic G and noise
prediction remain null. Road decks, receiver qualification, ground, obstacles and
survey/traffic/footprint temporal compatibility still need evidence.

## Verification and reproduction

The independent verifier imports neither the evaluated terrain/propagation
modules nor the builder. It uses three-point Gauss–Legendre quadrature and solved
normal equations for all mean planes, adaptive QUADPACK integration on the
synthetic/public polylines, vector tangent/normal projections, GEOS upper convex
chains, original GDAL cell indexing and GEOS cell intersection lengths. Separate
ground equations check the 168 planar conditional results, with maximum difference
**3.81 × 10⁻¹⁴ dB**. All held states, originals, frozen code and parent inventories
are checked again after completion. This verifies fidelity and numerical geometry;
it does not substitute for field validation.

| Completed generation | Identity |
|---|---|
| `terrain_method_sources_v1` | `terrain-method-sources-f61090d4a95ee96052b1` |
| `terrain_profile_checks_v2` | `terrain-profile-checks-75c3704bccf1612072fd` |
| `terrain_profile_review_v1` | `terrain-profile-review-7f4faed6953068929f1b` |

Inputs retain the protocol, synthetic geometry/ground rows, public benchmarks,
sampling-bias example, native profiles, source lineage, code/runtime, credits and
inventories. The review retains its verifier, receipt and visually inspected
`terrain_geometry_checks.png`. Earlier completed generations remain immutable;
V2 removes duplicate unknown-G rows without changing terrain values or numerical
results. Test receipts are `artifacts/terrain_profile_validation_v1.xml` and `.json`.
Artifacts are local and Git-ignored and need a separate backup.

Offline reproduction uses originals and fresh generation names:

```powershell
.venv\Scripts\python.exe scripts\61_run_terrain_profile_checks.py --sources artifacts/terrain_method_sources_v1 --output artifacts/terrain_profile_checks_NEW
.venv\Scripts\python.exe scripts\62_verify_terrain_profile_checks.py --inputs artifacts/terrain_profile_checks_NEW --output artifacts/terrain_profile_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/terrain_profile_pytest_NEW
```

Script 60 performs bounded public-source reacquisition with a fresh `--output`.
Availability of the remote versions remains a prerequisite for that operation.
Environment Agency OGL terrain and OS credits remain in the generation reports;
NoiseModelling originals retain their GPLv3 licence and JRC/EU method attribution.
The provider enquiry's recorded status was not checked or changed.

## Next bounded step

The single-edge homogeneous calculation below is now completed in the
[diffraction and ground-coupling report](TERRAIN_DIFFRACTION_AND_GROUND_COUPLING_2026-10-10.md).
Multiple horizontal edges and finite-width obstacle geometry are the next bounded
method increment.

Implement and independently benchmark **terrain diffraction and ground coupling**,
starting with a single supplied ridge and the frequency-dependent Rayleigh branch
for geometrically clear paths. Use explicitly compatible method versions and
published numerical boundary expectations. Keep unsupported multiple/finite-width
edges, reflections, weather and missing inputs withheld until their own method
qualification. These checks and remaining traffic/physical input qualification
precede credible real-area annual estimates.
