# Road propagation study: increment history

Moved verbatim from the README on 26 October 2026. Each paragraph summarises one
dated increment and links its report; the reports remain the evidence.

The offline [physical-scene input report](PHYSICAL_SCENE_INPUTS_2026-10-07.md)
documents complete native 2 m terrain/surface coverage for the bounded
north-Heathrow road study and provisional building surface-height candidates.
It retains source dates, missing states and geometry limitations; these inputs
do not yet supply noise predictions to the map.

The subsequent [carriageway/direction report](ROAD_CARRIAGEWAY_AND_DIRECTION_2026-10-07.md)
retains official directional traffic and historical one-way evidence, exports
provisional unsplit budgets, and withholds unsafe separate-carriageway/junction
allocations. The analytical inputs and qualification map remain offline.

The [road-emission assumption report](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md)
documents conditional timing/speed/surface cases and spectral source-power
sensitivity. It supplies no receiver noise or missing-cell predictions to the map.

The [ground/barrier audit and synthetic propagation report](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md)
checks native terrain samples, unknown obstacle heights and source/deck conventions,
then verifies controlled ground/screen and analytic point/line cases. These offline
checks preserve missing inputs and establish numerical fidelity; real-area ground,
barriers and complete propagation-method qualification remain open.
The [2021 land-cover and historical barrier input report](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md)
adds an exact native ESA WorldCover extract across the 144 km² study halo, explicit
conditional ground analogies and 1,206 historical barrier-related records.
All observed absorption and qualified acoustic barrier properties remain unknown;
314 related tests and original-value/geometry verification pass.
The [terrain-profile and mean-plane increment](TERRAIN_PROFILE_AND_PROPAGATION_CHECKS_2026-10-10.md)
now adds distance-weighted fits, orthogonal geometry and controlled slope/ridge
checks. All 20 published geometry comparisons and 32,908 native interval checks
pass independently, alongside 354 related tests.
The [single-edge diffraction and ground-coupling increment](TERRAIN_DIFFRACTION_AND_GROUND_COUPLING_2026-10-10.md)
now implements the homogeneous boundary term and frequency-dependent Rayleigh
selection. All 94 public numerical comparisons, independent calculations and 411
related tests pass. The subsequent
[multiple-edge and finite-width increment](MULTIPLE_EDGE_AND_FINITE_WIDTH_PROPAGATION_2026-10-10.md)
now handles blocked homogeneous paths over thick roofs and multiple ridges:
192 public comparisons, independent calculations and 489 related tests pass.
The [clear-sightline and remaining horizontal-obstacle increment](CLEAR_SIGHTLINES_AND_OBSTACLE_CONFIGURATIONS_2026-10-10.md)
now adds frequency-dependent physical-edge selection, roof surface/ground
support and fixed-chain image handling. All 614 public comparisons, independent
calculations and 568 related tests pass. The subsequent
[favourable propagation and atmospheric absorption increment](FAVOURABLE_PROPAGATION_AND_ATMOSPHERIC_ABSORPTION_2026-10-10.md)
adds circular path selection, explicit temperature/humidity/pressure and sound-energy
weather weighting. All 911 public comparisons, independent boundary/960 absorption
grid checks and 632 related tests pass. The
[reflection audit and single-wall increment](REFLECTION_PATH_AUDIT_AND_SINGLE_WALL_CHECKS_2026-10-11.md)
now checks reflected power, wall-top losses and known absent curved reflections:
136 public comparisons, independent calculations and 718 related tests pass.
The [terrain/screen reflection and road source-line increment](SCREENED_REFLECTIONS_AND_ROAD_LINE_INTEGRATION_2026-10-11.md)
now uses adjacent physical diffraction legs and integrates incoherent power per
metre through direct/reflected paths to controlled receiver results: 448 public
comparisons, 80 road octave integrals and 767 related tests pass. One 0.03 dB
printed-value discrepancy remains flagged within its original 0.1 dB tolerance.
Qualified real inputs remain missing; real-area noise estimates stay withheld.
The [real-road geographic scene builder](REAL_ROAD_SCENE_BUILDER_2026-10-11.md)
now connects retained road lines, native terrain, footprint/barrier intersections
and finite facade plans to the propagation interface. It prepares 722 source roads
as 5,448 cells, 722 direct snapshots and 1,418 reflection candidates. All 791 related
tests and independent raw-data geometry checks pass; an offline per-road readiness
table retains missing inputs and withheld receiver contributions.
The [traffic/speed/weather increment](ROAD_OPERATING_INPUTS_2026-10-11.md)
now supplies explicit conditional operating inputs for all 722 retained roads,
with 2021 Heathrow station observations, preserved missing-hour support, national
timing transfers and vehicle-specific motorway speed candidates. Other speeds and
air gaps have labelled references and scenario comparisons. All 824 related tests
and independent source-record/equation checks pass. This prepares input consumption;
qualified real receiver noise and the missing-source road total remain withheld.

The [physical-scene assumption policy](PHYSICAL_SCENE_ASSUMPTION_POLICY_2026-10-11.md)
now adds globally consistent candidate building tops, declared ground/height/screen
cases, source-deck context flags and speed-valid pavement comparisons. It covers
all 722 retained roads and 2,140 prepared profiles under 15 explicit physical cases.
The materializer preserves real-scene identity and unknown acoustic properties;
857 related tests pass. These are offline input and geometric sensitivity results.
The [conditional direct-road propagation and source-line increment](CONDITIONAL_ROAD_PROPAGATION_2026-10-11.md)
now connects these inputs to offline receiver calculations along all 722 retained
roads, preserving hourly traffic/air correlation and every retained source length.
It evaluates 32,688 quadrature nodes under two declared physical cases. The combined
reference case has 595 roads numeric at every fine-grid sample and 127 held roads;
this is finite quadrature support, with reflections and missing sources explicit.
All 902 related tests and independent source/air/length/energy accounting pass.
The [adaptive road-integration follow-up](ADAPTIVE_ROAD_INTEGRATION_2026-10-11.md)
now checks all 97 previously unstable road cases with 22,703 observations and
independent length/energy/geometry accounting. A further half-spacing check reduces
the original 9.3 dB discrepancy to 0.0004 dB on that road; two of three checked cases
pass, while one exposes an unsupported positive path and is withheld in the final
ledger. After that check, 16 cases retain their primary numerical diagnostic, 59
are physically held and 22 exceed the initial observation budget. All 961 related
tests and both independent accounting reviews pass for that generation.
The [curved-path terrain/obstacle correction](CURVED_PATH_TERRAIN_OBSTACLE_FIX_2026-10-12.md)
now repairs all 65 recorded failed points on 59 road cases. Independent circle
geometry verifies the repaired paths, and every one of the 23,958 previously
numeric point chains is unchanged. All 1,005 related tests pass. Terrain, heights,
ground and weather assumptions are preserved. The
[corrected road-integration follow-up](CORRECTED_ROAD_INTEGRATION_2026-10-13.md)
now reruns all 59 affected roads: 56 meet the primary diagnostic, two exceed the
unchanged observation budget and one remains held by height inconsistencies.
Independent review passes for all 62,040 observations, every road sum and the
complete ledger; 1,013 related tests pass. The completed finer pass checks all 56
newly primary-met cases plus two controls: 57 pass, with a maximum change of
0.075211 dB. One sampled ground average rounds one floating-point step above 1;
exact arithmetic confirms its true value is 1. That road's earlier total is
removed from the final ledger. All three independent reviews pass; **55 of 59
affected roads retain both numerical checks and four remain withheld**.
The [14 October ground-average correction](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md)
now recovers that rejected point. All 629 positions on its road are replayed
natively; all 628 previously numeric results are bit-identical. The recovered
road passes at 0.079022 dB, and **56 of the 59 affected roads passed both
numerical checks at that checkpoint; three remained withheld**. All 1,050 related tests and the
independent review pass. A fresh ledger preserves the other 1,443 entries.
The [24 budget-held case increment](BUDGET_HELD_ROAD_INTEGRATION_2026-10-14.md)
is complete: **all 24 cases pass primary and finer-spacing checks and both
independent reviews**. The largest finer-spacing change is 0.030198 dB, below the
unchanged 0.1 dB target. All 1,420 other ledger entries are exactly preserved;
82 adaptive cases passed both checks at that checkpoint. Its validation receipt is
`artifacts/budget_road_validation_v2.json`, with 1,059 distinct tests covered by
the linked receipts. Acoustic equations and source inputs are unchanged.
The completed [remaining-case follow-up](REMAINING_ROAD_CHECKS_AND_HEIGHT_AUDIT_2026-10-14.md)
passes all 14 older cases and both independent reviews, with a maximum finer change
of 0.050122 dB and all 1,430 other ledger entries preserved. **96 adaptive cases
now pass both checks; no older primary-met case awaits finer checks.** The current
receipt is `artifacts/remaining_road_validation_v1.json`, with 1,066 distinct tests
covered by linked receipts. The native height audit confirms the last roof/terrain
input conflict; that road stays withheld pending matched geometry or a separately
justified model treatment. Acoustic equations and source inputs are unchanged.
The [bounded reflection-connection increment](ROAD_REFLECTION_CONNECTION_CLOSURE_2026-10-14.md)
adds explicit facade hypotheses and connects direct/reflected source power without
duplicate paths or lengths. Its 221 focused checks pass; 448 original public
comparisons and 96 controlled road integrals pass independent numerical comparison.
This closes the two agreed numerical blocks. The 722 existing road snapshots and
1,418 reflection profiles attach inputs only: complete real-road reflection
integrals have not been calculated, and capped searches do not qualify complete
path support. The existing direct ledger and its geometry hold are preserved.
The installed map still receives no estimated decibels or incomplete all-source total.
The [17 October actual road-line run](ACTUAL_ROAD_REFLECTION_CALCULATION_2026-10-17.md)
is now calculating the declared direct/reflected component for eligible roads
in the existing study. All 722 roads remain accounted for, with the 128 existing
direct-component holds preserved. Saved observations and completed-road results
support restart recovery. Current progress is in
`artifacts/real_road_reflection_calculation_v4/progress.json`; this work directory
is unsealed until completion and supplies no map values.
