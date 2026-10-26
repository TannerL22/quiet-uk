# Curved-path terrain and obstacle correction

The solver now handles the **65 recorded circular-envelope failures on 59 road
cases** from the [adaptive integration follow-up](ADAPTIVE_ROAD_INTEGRATION_2026-10-11.md).
Every failed point recovers conditional H/F attenuation in all eight octave bands.
This removes an implementation fault that blocked otherwise supported geometry.
It does not supply new building, terrain, traffic or weather evidence.

All **1,005 related tests pass**, including 44 new geometry tests. Independent
circle calculations verify all 65 repaired envelopes, and all **23,958 previously
numeric point paths** retain exactly the same controlling chain. The affected
whole-road integrals still needed a fresh calculation at this 12 October
checkpoint. The old postcheck ledger remains unchanged, including its withheld
values. The [13 October integration follow-up](CORRECTED_ROAD_INTEGRATION_2026-10-13.md)
now completes the rerun, all selected finer checks and their independent reviews:
55 of the 59 affected roads pass both numerical checks; four remain withheld.

## Cause and repair

The earlier stack tested upper minor arcs between temporary candidate neighbours
before eliminating corners that cannot control the enclosing path. Terrain and
roof edges can be very close in horizontal chainage but differ greatly in height.
Their local connecting arc can fail the height-graph restriction even though a
valid path passes above those points.

Every recorded failure has this early height-graph rejection. The smallest
example, on the finer-check road `593F62C8-8735-4122-88C7-BE7A3B8FB129`, contains
two candidates 3.832 mm apart with a 12.149 m height difference. The valid enclosing
arc begins at an earlier roof edge and clears the temporary pair. The full
recorded scenes have up to 658 candidates, but at most nine upper support corners.

The [2021 CNOSSOS amendment](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32021L1226)
defines the favourable path using equal-radius enclosing arcs and downward
deflection at each retained diffraction point. The retained original PDF is
`weather_sources_v1/eu-2021-amendment.pdf`; the relevant description is on PDF page 7.

The reduction is our implementation derivation from that bounded geometry:
an upper height-graph arc is concave, and downward junctions preserve concavity.
A concave path enclosing every supplied point lies above their straight upper
convex hull. A corner below that hull cannot control the circular envelope.
The repaired selector therefore removes such corners before validating minor
arcs. It then applies the original curved-stack selection to the remaining
support. The straight hull supplies candidates; it does not replace the curved
path or determine diffraction by itself.

All original terrain, roof and ground support remains available for surface and
side-plane calculations. No heights are raised, profiles smoothed, corners snapped
together or unknown properties supplied. Malformed support and genuinely
unsupported selected arcs still fail, including an excessive obstacle height and
a controlling near-vertical edge. Conditional real scenes retain their identity
and their explicit physical assumptions.

![Original cross-section and the corrected enclosing path](../artifacts/curved_envelope_fix_plot_v1/geometry.png)

This figure uses the preserved conditional cross-section. It shows geometry in
metres, not measured noise or a whole-road contribution. The dashed connection
was temporary candidate geometry; it is not part of the corrected physical path.

## Independent verification and limits

The reviewer computes circle centres and tangent vectors independently, checks
clearance against all original candidates, and enumerates admissible downward
paths. It does not call the evaluated hull selector. Large recorded scenes use
an independently obtained Qhull upper polygon before exhaustive path search;
the new small-scene tests enumerate all candidates without this reduction.

The completed checks cover:

- All 65 repaired envelopes and 21,806 original candidate-clearance checks.
- All 520 failed-point octave cases, paired with the original frozen solver.
- Exact chain equality at all 23,958 previously numeric points in the primary and
  finer-spacing generations.
- 96 octave replays on 12 previously numeric road points. The maximum transfer
  difference is 1.421 × 10^-14 dB, consistent with floating-point round-off.
- The 961 preceding tests plus 44 new tests, including full exhaustive visibility
  comparisons for 32 varied scenes, nearby roof/terrain corners, retained genuine
  failures and malformed input refusal.

The validation receipt verifies that 64 other original source/controller files
remain byte-identical. Within `favourable_propagation.py`, nine numerical functions
retain the same mathematical syntax; the boundary result only gains an algorithm
label. The intentional numerical change is the circular-envelope selector.
Earlier sealed datasets, code snapshots and the postcheck road ledger are intact.

These are implementation and conditional propagation checks. They do not provide
field validation, a rigorous acoustic error bound or an all-inclusive noise value.
The terrain reconstruction policy, declared building/barrier heights, pavement,
speed and weather assumptions remain those of the original input generations.
Reflections, lateral paths, transmission and missing sources remain outstanding.

## Evidence and reproduction

- [Preserved failure population and classification](../artifacts/curved_envelope_witnesses_v1/report.json)
- [Original conditional scenes and failure traces](../artifacts/curved_envelope_witnesses_v1/witnesses.json)
- [Independent geometry and compatibility review](../artifacts/curved_envelope_fix_checks_v1/report.json)
- [All repaired conditional point calculations](../artifacts/curved_envelope_fix_checks_v1/witness_replay.json)
- [Validation receipt and unchanged-code checks](../artifacts/curved_envelope_validation_v1.json)
- [1,005-test regression report](../artifacts/curved_envelope_validation_v1.xml)

The recorded witness generation is `curved-envelope-witnesses-7c95f2a5e871ff98789c`.
The review is `curved-envelope-fix-review-cf4b4c5df01bc4a63149`. The revised capture
script also recaptures the original frozen solver after the repair: generation
`curved-envelope-witnesses-3797d5a5b9b5e570c46d` has byte-identical witness data and
diagnostic report. The two generations differ in the capture script snapshot.

Use fresh output paths with the existing sealed parent datasets:

```powershell
.venv\Scripts\python.exe scripts\98_audit_curved_envelope_failures.py --output artifacts\curved_envelope_witnesses_next
.venv\Scripts\python.exe scripts\99_verify_curved_envelope_fix.py --inputs artifacts\curved_envelope_witnesses_next --output artifacts\curved_envelope_fix_checks_next
.venv\Scripts\python.exe scripts\100_record_curved_envelope_validation.py --inputs artifacts\curved_envelope_witnesses_next --review artifacts\curved_envelope_fix_checks_next --junit artifacts\curved_envelope_validation_v1.xml --output artifacts\curved_envelope_validation_next.json
```

## Follow-up status

The [completed follow-up](CORRECTED_ROAD_INTEGRATION_2026-10-13.md) recalculates
all 141 historical unknowns under the corrected solver and retains every distinct
observation. It reruns all 59 affected road cases and checks every newly
primary-met road at finer spacing, with two prior passed controls. The final
ledger withholds the newly exposed ground-average rounding case; repairing a
recorded witness alone does not qualify every point along its road.

The [14 October correction](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md) now fixes
that derived-ground fault and recovers its road through a complete native replay.
Next revisit the 22 preceding
initial partition-budget cases plus the two newly exposed budget cases, check
the 14 previously primary-met cases without finer checks, resolve remaining
height support, and refine the other retained roads.
Complete reflection and missing-source support before comparing an appropriately
matched model against independent sound evidence. The installed map remains
unchanged and receives no estimated noise from this increment.
