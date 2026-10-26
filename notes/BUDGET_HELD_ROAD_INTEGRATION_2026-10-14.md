# Budget-held road integration — 14 October 2026

This completed numerical increment resolves exactly **24 combined-reference road
cases** whose complete initial geometry exceeded the old 2,048-observation cap:
22 from `adaptive_road_integration_v3` and two from
`corrected_road_integration_v1`. All were withheld in the preceding validated
ground-correction ledger; all now pass both numerical checks and independent
reviews. This is the same receiver and source area, not a new
field study.

## Protocol and resource bounds

The existing five-node rules need **102,400 initial observations** across the
24 cases, with a maximum of 11,480 for one case. Retain all **4,711 distinct
historical locations**, including the two complete 2,048-point development
histories already reconciled by the corrected-observation audit. Reevaluate
every retained location from the native inputs under the model validated by
`ground_average_validation_v2.json`; reuse no historical numerical values.

For each case, the new observation cap is its initial five-node requirement,
plus its retained history, plus 5,120 observations: ten nodes for each of at most
512 refinements. The batch cap is **229,991 observations including history**;
the largest case cap is **16,642**. Keep the 0.1 dB diagnostic, 4 m maximum initial
panel, 0.01 m minimum child and 512-refinement limit. No geometry partitions,
source inputs or acoustic equations change. A remaining numerical limit or any
unknown positive contribution still withholds the whole road.

Stream full terrain/obstacle kernel scenes to separate compressed files rather
than retain every profile in worker memory. Compact observation records carry
exact file/record references. Two workers and at most four queued jobs remain
the resource limits. Per-case checkpoints record native sample counts and
elapsed time every 200 samples; complete road rows are saved after each case.
These checkpoints aid diagnosis after a pause, but do not yet resume an
interrupted partial case automatically.

Every case meeting the primary diagnostic receives four fresh, positive,
equal-weight interior midpoint samples per final leaf at half the accepted
midpoint spacing. Independent reviews verify history coverage, source/air
products, scene references, geometry partitions, leaf rules, error indicators,
road sums and withholding. Sampled native profiles and uncached acoustic bands
check implementation parity. Every one of the **1,420 unselected ledger entries**,
including earlier finer-check passes, must remain exactly unchanged.

## Completed result

Follow-up: the [remaining-case increment](REMAINING_ROAD_CHECKS_AND_HEIGHT_AUDIT_2026-10-14.md)
has since completed all 14 older finer checks. The current study receipt is
`artifacts/remaining_road_validation_v1.json`, with 96 cases passing both checks
and one diagnosed height hold. The figures below record this budget increment's
own checkpoint.

**All 24 cases pass the primary and finer-spacing diagnostics; none remain
budget-held. Both independent reviews passed.** The final receipt is
[`artifacts/budget_road_validation_v2.json`](../artifacts/budget_road_validation_v2.json),
with status `passed_budget_road_integration_validation`. This increment's validated
ledger is
[`artifacts/budget_road_resume_spacing_check_v1/road_ledger.json`](../artifacts/budget_road_resume_spacing_check_v1/road_ledger.json).

| Check | Result |
| --- | --- |
| Primary observations, including retained history | 103,081 |
| Native observations added beyond the 4,711 historical locations | 98,370, including 4,280 recovered from completed cases after restart |
| Primary cases meeting the 0.1 dB diagnostic | 24 / 24 |
| Largest primary indicator | 0.099294 dB |
| Largest primary offset-rule change | 0.013226 dB |
| Fresh finer-spacing observations | 81,936 |
| Finer-spacing cases passing | 24 / 24 |
| Largest finer-spacing change | **0.030198 dB**, below the unchanged 0.1 dB target |
| Other ledger entries exactly preserved | 1,420 / 1,420 |

The independent primary review checks every observation, 2,473,944 source/air
values, 4,947,888 point-energy values, 20,484 leaf rules and all 1,444 ledger
entries. It reconstructs 215 source-cell partitions and samples 12 native
profiles, 47,266 terrain knots and 96 uncached acoustic bands. The independent
finer review checks all 81,936 nodes and weights, 1,966,464 source/air values,
3,932,928 point-energy values and every final ledger entry.

All six immutable output/review generations, the restart recovery, both test
receipts, 76 current code-file hashes and the final ledger hash were reverified
at closeout. The original 1,050-test model receipt remains valid with unchanged
covered source; 68 focused controller/adaptive tests passed, including nine new
tests. These receipts cover **1,059 distinct tests**; the complete 1,050-test
suite was not rerun for this controller-only increment.

Across the 97-case adaptive study, **82 cases now have passed both checks**, 14
older primary-met cases still await finer checks, and one remains withheld by
the existing building/terrain height inconsistency. Of the earlier 59 cases
affected by circular-envelope failures, 58 now pass both checks and only that
height case remains held. No selected budget case failed or fell back to an old
value.

The resumed primary integration took 11,887.4 seconds (3 h 18 min); the finer
checks took 10,407.0 seconds (2 h 53 min), plus independent review time. The
numerical process exited successfully. No long-running case job remains for
this increment.

## Preparation and restart record

Preparation completed in 815.4 seconds. The sealed generation is
`budget-road-observations-fe9c7b87eb2d83bc2f2b` in
`artifacts/budget_road_observations_v1`. All **4,711 historical locations** were
reevaluated natively, with **zero remaining unknown historical observations**.
Independent review passed in sealed generation
`budget-observation-review-c0326b6d9ef66d3db98c`, including all 4,711 historical
transfer comparisons, 113,064 source/air values, 226,128 point-energy values,
24 native profiles, 88,092 terrain knots and 192 uncached acoustic bands.

The gated completion runner ran primary integration, its independent review,
every eligible finer-spacing check, the finer independent review and the final
receipt successfully.
The first integration was stopped by the owner's laptop restart. Two complete
cases and all 4,298 of their observations survived in the completed-road journal.
They were captured in sealed recovery generation
`recovered-budget-primary-749946ce61e350c1abea`, including all 4,280 completed native
scene records. The interrupted observation stream contained the exact complete
JSON-line prefix; only its final gzip footer was missing. Both completed scene
streams passed full gzip/CRC and identity checks. Incomplete cases restart from
their reviewed historical seeds; partial scene files are not accepted as
completed observations.

The restart wrapper preserved the two completed rows, restored their scene
references and scheduled exactly the remaining 22 cases. Its Windows spawn check
and recovery-routing check passed. No acoustic equations or tolerances changed.
The full independent numerical review approved the recovered rows along with
the remaining 22 cases.

The resumed pipeline uses `budget_road_resume_integration_v1`,
`budget_road_resume_integration_review_v1`, `budget_road_resume_spacing_check_v1`
and `budget_road_resume_spacing_review_v1`, with final receipt
`budget_road_validation_v2.json`. Tool session `17183` finished with exit code 0.

The Windows worker-spawn check and **68 focused tests** passed, including nine
new controller tests. The existing 1,050-test model receipt remains valid because
every covered source file is unchanged. The final receipt and current ledger
are linked above; preparation alone was not used to qualify a road value.

## Reproduction

Use the current source checkout. Every output must be fresh; never overwrite a
sealed generation. The completion runner stops if any preceding review fails.

```powershell
.\.venv\Scripts\python.exe scripts/109_integrate_budget_held_roads.py --stage spawn-probe
.\.venv\Scripts\python.exe -m pytest tests/test_budget_road_controller.py tests/test_adaptive_road_integration.py tests/test_adaptive_spacing_check.py tests/test_adaptive_validation.py -q --junitxml=artifacts/budget_road_controller_tests_v1.xml --basetemp=artifacts/budget_road_controller_test_tmp_v1
.\.venv\Scripts\python.exe -u scripts/109_integrate_budget_held_roads.py --stage prepare --output artifacts/budget_road_observations_v1
.\.venv\Scripts\python.exe -u scripts/110_verify_budget_road_observations.py --inputs artifacts/budget_road_observations_v1 --output artifacts/budget_road_observation_review_v1
.\.venv\Scripts\python.exe -u scripts/111_complete_budget_road_checks.py --observations artifacts/budget_road_observations_v1 --observation-review artifacts/budget_road_observation_review_v1 --junit artifacts/budget_road_controller_tests_v1.xml --output artifacts/budget_road_validation_v1.json
```

The completion runner produces fresh `budget_road_integration_v1`,
`budget_road_integration_review_v1`, `budget_road_spacing_check_v1` and
`budget_road_spacing_review_v1` generations, followed by the flat validation
receipt. The ledger in the finer generation becomes authoritative only when
both reviews and the final receipt pass.

After the laptop restart, the actual recovery/resume commands are:

```powershell
.\.venv\Scripts\python.exe -u scripts/112_resume_budget_road_checks.py --stage recover --inputs artifacts/budget_road_integration_v1 --output artifacts/budget_road_recovery_v1
.\.venv\Scripts\python.exe -u scripts/111_complete_budget_road_checks.py --observations artifacts/budget_road_observations_v1 --observation-review artifacts/budget_road_observation_review_v1 --prefix budget_road_resume --junit artifacts/budget_road_controller_tests_v1.xml --recovery artifacts/budget_road_recovery_v1 --output artifacts/budget_road_validation_v2.json
```

## Scope

This remains an offline, conditional direct-road calculation. H and F weather
states remain separate. The 0.1 dB diagnostic is not a rigorous error bound or a
field-accuracy claim. The remaining height inconsistency, 14 older primary-met
cases awaiting finer checks, unbudgeted roads, reflections and non-road sources
remain separate outstanding work. No map changes or all-inclusive noise value
are authorised by these results alone.
