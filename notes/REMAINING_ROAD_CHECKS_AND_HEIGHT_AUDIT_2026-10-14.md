# Remaining road checks and height audit — 14 October 2026

This increment covers the **14 older primary-met road/case entries** without a
finer-spacing check, plus a native-data diagnosis of the existing height hold.
It uses the same receiver, source area, physical assumptions and acoustic
implementation as the completed 24-case budget increment.

## Completed result

**All 14 cases passed both numerical checks and independent reviews.** The
largest finer-spacing change is **0.050122 dB**, below the unchanged 0.1 dB
diagnostic. All **1,430 other ledger entries** are exactly preserved.

The current validation receipt is
[`artifacts/remaining_road_validation_v1.json`](../artifacts/remaining_road_validation_v1.json),
with status `passed_remaining_road_check_validation`. The current validated
ledger is
[`artifacts/remaining_road_spacing_check_v1/road_ledger.json`](../artifacts/remaining_road_spacing_check_v1/road_ledger.json).

Across the 97-case adaptive study, **96 cases now pass both numerical checks**,
**zero primary-met cases await finer checks**, and the one diagnosed roof/terrain
conflict remains withheld. This completes the outstanding finer-spacing work
for this study; it does not qualify the remaining physical inputs or a complete
road-network prediction.

| Validation | Result |
| --- | --- |
| Fresh primary locations | 6,016; all prior transfers, source/air powers and densities bit-identical |
| Primary cases meeting their unchanged diagnostic | 14 / 14 |
| Fresh finer-spacing positions | 4,680 |
| Finer-spacing cases passing | 14 / 14 |
| Largest finer-spacing change | 0.050122 dB |
| Other ledger entries exactly preserved | 1,430 / 1,430 |
| New focused tests | 7 passed |
| Prior distinct tests preserved by unchanged covered source | 1,059 |
| Total distinct test coverage in linked receipts | 1,066 |

The independent finer review checks all 4,680 nodes and weights, 112,320
source/air values, 224,640 point-energy values and all 1,444 ledger entries.
The four new immutable result/review generations, the native height audit,
80 current code-file hashes, test receipt and final ledger hash were reverified
at closeout. No acoustic equations or original input data changed. The original
complete test suite was not rerun for this controller-only increment.

Primary replay took 549.3 seconds (9 min 9 s); finer checks took 404.8 seconds
(6 min 45 s), plus the independent reviews. The numerical continuation exited
successfully, and no case job remains active for this increment.

## Numerical protocol

Select exactly the 14 unchecked primary-met entries in the ledger bound by
`artifacts/budget_road_validation_v2.json`. These comprise 13 combined-reference
cases and one baseline case, across 13 roads. Their original accepted primary
partitions and all **6,016 recorded primary locations** come from
`adaptive_road_integration_v3` and its independent review. The three overlapping
development histories are already complete within those primary observations.

Replay every primary position from native geography under the current validated
model. Require identical native-profile hashes and numerical parity for every
old transfer, source/air power and point-energy value. Recompute the original
positive leaf integrals, offset rules, full-history error proxies and primary
diagnostic from those fresh observations. A missing sample, meaningful numerical
change or unmet 0.1 dB diagnostic stops the run; an old value cannot supply a
fallback.

Only after independent review, evaluate **4,680 fresh finer-spacing positions**:
four equal positive midpoint weights per accepted leaf. Review every position,
weight, source/air product, road sum and H/F period/Lden change independently.
Preserve all **1,430 unselected ledger entries** exactly, including the 82
existing finer-check passes and the physically held road. This is a numerical
stability check, not field accuracy or an all-inclusive prediction.

## Validation and execution record

The seven new focused controller tests and Windows worker-start check pass.
All 6,016 primary positions have been replayed, with every prior transfer,
source/air power and density **bit-identical**, and all 14 primary diagnostics
still met. The sealed primary replay is
`remaining-road-primary-replay-b76d998feced93ad64ef`.

The first accounting pass stopped because the independent checker distributed
the pair-rule weight before adding, while the replay producer added before
weighting. The largest observed discrepancy was 3.47e-18 in a tiny local error
proxy, from last-bit association rather than changed acoustic values. The
checker now reproduces the declared pair-rule operation order before subtraction;
its tolerances and all source results are unchanged. The complete sealed native
replay is reused rather than recomputed.

The independent primary review passed in generation
`remaining-road-primary-review-bd7c4394efb5d99ef169`, checking all 6,016 native
observations, 144,384 source/air values, 288,768 energy values, 1,170 leaf rules,
all 1,444 ledger entries, 44 reconstructed source-cell partitions, 14 sampled
native profiles, 32,622 terrain knots and 112 uncached acoustic bands.

The gated continuation completed in tool session `22892` with exit code 0 via
`scripts/116_finish_remaining_road_checks.py`. It produced
`remaining_road_primary_replay_v1`, `remaining_road_primary_review_v1`,
`remaining_road_spacing_check_v1`, `remaining_road_spacing_review_v1` and
`remaining_road_validation_v1.json`. Both reviews passed and the new ledger is
authoritative for this study. The finer review generation is
`remaining-road-finer-review-824f392ac65846b02461`. Prior validated source files
remain unchanged.

The main replay/check workload was 10,696 native point evaluations. Session
identifiers are diagnostic only.

## Height investigation

The remaining held case is
`F4E6B65F-E0DF-412B-939B-7B666CCE8273|combined_reference`. Its two retained native
witnesses reproduce the same geometry rejection. Both intersect building
`os:A7CAB645-5440-42D1-AA5E-991DAEA82666:1626954`.

The declared flat roof is the paired DSM median, **38.506001 m ODN**. At the two
footprint crossings, the unsimplified midpoint terrain reconstruction reaches
approximately **38.515507** and **38.521725 m ODN**: respectively **0.009507** and
**0.015724 m above the assumed roof**. Rejection occurs before the later 0.1 m
terrain simplifier; that tolerance cannot explain away this conflict.

The separate native audit completed in sealed generation
`native-height-consistency-audit-b71f0cfe29641fde20e6`, in
`artifacts/remaining_height_audit_v1`. It reproduces both original native-profile
hashes and rejections. The same native 2 m terrain cell contributes to both rays:
row 2648, column 3525, centre **[508051, 179703] BNG**, elevation **38.874172 m
ODN**, which is **0.368172 m above the assumed roof**. Its centre is outside the
generalised footprint, but its pixel overlaps that footprint by **0.228810 m²**.
The ray/footprint overlap with this cell is approximately 0.35–0.37 m long.
This identifies a raster/footprint-edge and flat-roof assumption conflict, rather
than floating-point rounding or the later vertical simplifier.

The complete interpolated profile places approximately 0.0227–0.0370 m of the
assumed roof below modelled ground. Switching to native raster steps would not
remove the underlying higher terrain-cell ordinate. The existing declared
`roof_p90` scenario clears the geometry check at these two points; the baseline,
`roof_p10` and combined-reference median-roof scenarios remain held. These are
scenario support checks, not a reason to replace a failed median-roof case with
a selected higher percentile.

**The height-held road remains withheld.** A defensible change requires matched
roof-edge/ground geometry or a separately justified and validated terrain/roof
union method. A different roof percentile, footprint trimming or a relaxed
clearance tolerance is not automatically a qualified replacement. The audit
changes no source inputs, model equations, accepted ledger values or live map
layer.

## Reproduction

Every output must be fresh. The current model and previous validation receipts
must still match their recorded hashes.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_remaining_road_controller.py -q -p no:cacheprovider --junitxml=artifacts/remaining_road_controller_tests_v1.xml --basetemp=artifacts/remaining_road_controller_test_tmp_v1
.\.venv\Scripts\python.exe scripts/113_complete_remaining_road_checks.py --stage spawn-probe
.\.venv\Scripts\python.exe -u scripts/113_complete_remaining_road_checks.py --stage complete
.\.venv\Scripts\python.exe -u scripts/115_audit_remaining_height_hold.py --output artifacts/remaining_height_audit_v1
```

The actual continuation from the existing sealed primary replay is:

```powershell
.\.venv\Scripts\python.exe -u scripts/116_finish_remaining_road_checks.py
```

The current study ledger is bound by the new remaining-case validation receipt.
Reflections, missing source budgets and
non-road contributions remain separate unresolved coverage; H/F weather states
remain separate. These checks do not authorise a complete-area noise level.
