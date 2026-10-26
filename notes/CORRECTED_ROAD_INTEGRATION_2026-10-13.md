# Road integration after the curved-path correction

This follow-up applies the [curved-path correction](CURVED_PATH_TERRAIN_OBSTACLE_FIX_2026-10-12.md)
to all 59 affected roads under the combined reference case at the existing
receiver. It retains the original traffic, weather, source-line, terrain and
obstacle assumptions.
It is an offline calculation of a conditional direct road contribution.

**The rerun, independent reviews and final ledger are complete.** Of the 59
affected roads, **55 pass both numerical checks and four remain withheld**:
two exceed the unchanged sampling budget, one has inconsistent obstacle/terrain
heights, and one encounters a ground-average rounding rejection. Both previously
passed control roads pass again. The largest passing finer-spacing change is
**0.075211 dB**, within the declared 0.1 dB criterion. All **1,013 related tests**
and all three independent accounting reviews pass.

**14 October update:** the [ground-average correction](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md)
recovers the rounding-held road after a complete native replay. The new ledger
retains 56 of the 59 affected roads and leaves three withheld. This report and
its sealed generations retain the 13 October findings.

## Verified observations

The observation review passes for all **36,797 distinct historical points** on
the 59 affected cases and two previously passed finer-check controls:

- 36,656 numeric points retain their complete original calculation record and
  exactly the same controlling circular chain under the old and corrected solver.
- All 141 previously unknown points are recalculated from native geographic
  inputs. Their original native-profile identity is preserved. Of these, 139
  become conditional numeric points; two remain withheld because an obstacle top
  lies below the reconstructed terrain.
- All 65 recorded failure witnesses now support conditional point calculations.
- The independent review checks 1,870 duplicate records, 883,080 source/air band
  values and 1,766,160 point-energy values, plus independent enclosing-circle
  geometry on all 139 recovered points. It also replays native geometry on 12
  sampled points and the current attenuation calculation in 96 octave cases.

The full candidate scenes remain in the sealed observation generation. Worker
seeds omit only those large scene arrays and retain an exact observation reference,
the original energy values and all provenance. Every distinct historical point,
including observed peaks and remaining unknowns, is retained. Exact coordinate
reuse in the finer check refuses ambiguous source-cell/fraction identities.

An initial Windows worker-pickling failure occurred after the compatible numeric
observations had been saved. A sealed recovery generation preserves that closed
prefix; a fresh generation resumes it and recalculates the unknown points. The
failed output is not treated as a completed integration. The corrected launcher
is included in the executed-code snapshots and its actual spawned worker path
has been checked.

## Integration and spacing protocol

The primary rerun finishes with **56 of 59 cases meeting the 0.1 dB diagnostic**.
The 59 selected source lines total 3,461.195 m. The run retains 62,040 distinct
observations, including 27,619 new geographic calculations. Its largest passing indicator is
0.099316 dB and largest passing interior Gauss/midpoint change is 0.041006 dB.
The calculation takes 6,145.9 seconds (102.4 minutes) on the current machine.

Two cases require 2,895 and 3,135 observations just for their declared initial
partition, above the unchanged 2,048-observation limit. Those cases remain
withheld. The third held road has two retained height inconsistencies. No earlier
numeric value replaces a held road.

Independent primary review passes. It verifies every point identity, 1,488,912
source/air band values, 2,977,824 point-energy values, 11,153 positive leaf rules,
all 59 road totals and all 1,444 ledger entries. It independently reconstructs
230 source-cell partitions and replays 12 new native profiles and 96 octave cases.

The finer check completes on **58 cases**: all 56 newly primary-met roads plus
two controls, with **45,660 positively weighted sample positions**. Of these,
44,352 require new native geographic evaluations and 1,308 reuse exact verified
observations. **57 cases pass and one is withheld**. The maximum passing
change is 0.0752111533 dB; the two controls reproduce their earlier changes of
0.0003967666 and 0.0014478982 dB. The finer run takes 5,414.6 seconds (90.2 minutes).

Independent finer review checks all 45,660 locations and positive weights,
1,095,816 source/air band values, 2,191,632 point-energy values and every selected
road sum and spacing change. Passing the primary check alone does not qualify a
road for the final reviewed ledger.

The finer pass finds a previously unsampled rejection on
`04C8025A-1D65-448D-A443-07DAF07B9744`. A diagnostic replay with unchanged
equations shows a computed ground factor of `1.0000000000000002`: one floating
point step above 1, rejected by the strict ground-factor validator during the
homogeneous sightline calculation. Every original ground-segment factor is in
`[0, 1]`, and the source factor is 0. This identifies a derived-average rounding
issue for a subsequent correction. The current generation remains pinned to its
reviewed implementation and withholds that road's earlier total.
The diagnostic point is at fraction `0.5776780161696142` in that road's
`:0:0:0` source cell, with native-profile SHA-256
`f395b30cd3091925c7ac535d6ba3b8dbcbbf5c33ce432ed52b61d0bd1e14dcc6`.
The 63 Hz replay retains the strict validator and all equations; it only records
the rejected value and its call chain. No derived value is clamped in this run.
An independent exact rational calculation verifies that all 427 represented
ground intervals are contiguous, the clipped coverage ratio is exactly 1, and
the true weighted average of the represented inputs is exactly 1. This confirms
an arithmetic rounding fault rather than missing or overlapping ground support.

The affected population previously consisted of 58 primary-unknown cases and the
one road rejected by the old finer check. The 22 earlier initial-budget cases
remain outside this increment. Combined with the two additional budget refusals
found here, **24 cases still require numerical-budget work**. Fourteen previously
primary-met cases remain outside the current finer population. Those historical
cases are carried forward with their original evidence and outstanding checks.

The primary calculation retains the existing limits: 2,048 observations per case,
including verified history, initial panels no longer than 4 m, a 0.01 m minimum
child length and a 0.1 dB diagnostic target. Positive midpoint/quarter-point and
interior Gauss evaluations, absolute local energy indicators and historical probe
retention remain unchanged. A known unsupported positive contribution withholds
the whole road. Exhausting a numerical budget also withholds it.

Every affected case meeting the primary diagnostic receives four equally weighted
interior samples per final leaf, halving the accepted midpoint spacing. The two
previously passed roads are included as controls. Compatible points at exactly
the required coordinates can be reused; other points are evaluated natively.
Any unknown finer sample or change above 0.1 dB withholds the earlier primary
value in the final ledger. An earlier or partial integral cannot supply a fallback.
The comparisons cover A-weighted day, evening, night and Lden values under
homogeneous and favourable weather separately. Local annual favourable-condition
probabilities remain unqualified.

The separate reviewers reconstruct source/air products, point energies, positive
weights, lengths, partitions, road sums and spacing changes. Primary review also
replays sampled new native geometry and octave calculations. A final receipt links
the observation, primary and finer generations and reviews to the regression tests
and complete 1,444-entry road/case ledger.

The final ledger removes the earlier numeric integral on the failed finer case.
Every unaffected entry retains its earlier value and outstanding support status.
Across the preceding 97-case adaptive population, 57 have a passing finer check,
14 retain a primary diagnostic without a finer check, 24 are held by budgets,
one is held by height consistency and one by the rounding rejection. The remaining
roads retain their earlier finite-grid or input-withheld status. None of these
carried entries supplies an all-inclusive value.

## Scope

This work checks implementation and numerical consistency under declared inputs.
The 0.1 dB diagnostic and finer-spacing comparison are not a rigorous error bound
or independent field validation. Unsampled narrow path changes remain possible.
The 0.1 m terrain simplification is a geometry policy, not a noise-error bound.

There is no map update or all-inclusive noise value. Reflections, lateral paths,
transmission, unbudgeted roads and nonroad sources remain outstanding. The original
13,928-road input ledger still includes 13,206 roads without accepted source
budgets. Traffic timing, speeds, ground and obstacle properties retain their
explicit conditional assumptions.

## Evidence

- [Corrected observation summary](../artifacts/corrected_road_observations_v2/report.json)
- [Independent observation review](../artifacts/corrected_road_observation_review_v1/report.json)
- [Compatible observation recovery](../artifacts/corrected_observation_recovery_v1/report.json)
- [Completed primary integration summary](../artifacts/corrected_road_integration_v1/report.json)
- [Independent primary integration review](../artifacts/corrected_road_integration_review_v1/report.json)
- [Completed finer-spacing summary](../artifacts/corrected_road_spacing_check_v1/report.json)
- [Independent finer-spacing review](../artifacts/corrected_road_spacing_review_v1/report.json)
- [Exact ground-rounding diagnostic](../artifacts/ground_average_rounding_diagnostic_v1.json)
- [Final validation receipt](../artifacts/corrected_road_validation_v1.json)
- [Complete final road/case ledger](../artifacts/corrected_road_postcheck_ledger_v1.json)
- [1,013-test regression report](../artifacts/corrected_road_validation_v2.xml)

The regression run passes all **1,013 related tests**, including eight new tests
for reuse identity, unknown propagation, finer rejection and changed native-input
refusal. The acoustic solver remains byte-identical to the reviewed correction.
The primary generation is `corrected-road-integration-b6123aed444543188ae8`.
Its independent review is `adaptive-road-review-fa41fca0c58ab5adde75`.
The finer generation is `corrected-road-spacing-8118c3fe994073aa601d`, with review
`adaptive-spacing-review-39063c6a3624e1bee23a`. The observation generation is
`corrected-road-observations-e8ecb120012effc4ecfe`, with review
`corrected-observation-review-0f0bcca42e9ba559d5de`.

## Reproduction

With the existing sealed source and corrected observation generations installed,
the following commands describe the pinned 13 October implementation. They
require its matching source snapshot and intentionally reject a changed live
implementation. For the corrected 14 October source, use the
[ground-average replay and review](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md).
Run from the repository directory. Each output directory must be fresh. The
Windows entry point registers the dynamically loaded evaluators consistently in
spawned workers; it changes no numerical settings.

```powershell
.venv\Scripts\python.exe -u scripts\106_launch_corrected_road_stage.py --stage integrate --inputs artifacts\corrected_road_observations_v2 --review artifacts\corrected_road_observation_review_v1 --output artifacts\corrected_road_integration_next
.venv\Scripts\python.exe -u scripts\93_verify_adaptive_roads.py --inputs artifacts\corrected_road_integration_next --output artifacts\corrected_road_integration_review_next
.venv\Scripts\python.exe -u scripts\106_launch_corrected_road_stage.py --stage finer --inputs artifacts\corrected_road_integration_next --output artifacts\corrected_road_spacing_check_next
.venv\Scripts\python.exe -u scripts\96_verify_refined_spacing_check.py --inputs artifacts\corrected_road_spacing_check_next --output artifacts\corrected_road_spacing_review_next
```

`103_record_corrected_road_validation.py` then links the six sealed generations,
their three reviews and the passing regression XML to a fresh validation receipt
and a fresh final ledger. Previous sealed generations and the old ledger remain
intact for comparison.

## Next implementation priority

The [14 October follow-up](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md) completes
the derived-ground correction and native road replay while retaining strict
input validation. Next address the 24 sampling-budget cases, the unresolved height support and the 14
older primary-met cases lacking finer checks. Refine the other retained roads and
complete missing-source/reflection support before interpreting an all-inclusive
prediction. The current result remains offline.
