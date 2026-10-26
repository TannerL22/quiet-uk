# Road reflection connection: bounded validation closure — 14 October 2026

The requested scope is two numerical validation blocks: connect direct and
single-reflected sound without duplicate energy, then compare the connected
road calculation with independent references. **Both blocks are complete and
independently reviewed.** The final receipt is
`artifacts/road_reflection_connection_validation_v1.json`. No additional general
propagation test programme is part of this increment.

This is a connection and arithmetic validation, not a claim of field accuracy or
a completed all-source estimate. Whole real-road reflection integrals have not
been calculated in this increment. The native closest-point snapshots below
attach existing inputs; they never substitute for an integral along a road.

## Implemented connection

`conditional_road_reflections.py` materializes a separate reflected cross section
from native profiles and the exact declared physical scenario. A dedicated
`ReflectionExecution` token permits the screened-reflection kernel to evaluate
the declared real-input hypothesis. The lower boundary calculation retains the
existing physical policy. Real profiles remain `conditional_real_input`; the
default real-scene refusal remains in place. The only change to a previously
receipt-bound source file is the screened-reflection signature, import and
domain gate. Its acoustic equations are byte-preserved.

At each source point the bridge multiplies each path's H/F, eight-band geometry
transfer by source power per metre with traffic-weighted air transmission at
**that path's own distance**. It adds these energy densities once. The road
integrator subsequently multiplies by horizontal source length once. It does
not average path decibels, reuse direct-path air for a reflection, or multiply
emission by road length twice. Unknown favourable probability leaves the H/F
results separate; no annual weather mix is invented.

One direct path is required. Duplicate identities, reversed facade segments,
and different overlapping segments on the same reflecting plane are rejected.
A known absent reflected branch contributes zero. An unknown selected positive
path withholds the selected sum and retains its known components separately.
Search-budget exhaustion also withholds that sum. Complete road/all-source
outputs remain null even when the selected finite path set is numeric.

## Explicit facade hypotheses

Three scenarios use octave-independent absorption of 0, 0.2 and 0.5. These are
hard/reference/absorptive sensitivity hypotheses, not observed materials,
probabilities, confidence intervals or guaranteed acoustic bounds. The executed
native attachment uses the 0.2 reference case and the existing
`combined_reference` physical case.

All three assume vertical, solid facades, the physical scenario's globally flat
roof top, containing-cell terrain as the local wall base, and 1 m apparent
incident-view height and width in both weather conditions. The incident-view
values are explicitly unverified eligibility hypotheses, not quantities derived
from footprint length. A wall whose physical height or facade length is below
the assumed apparent dimension stays withheld. Different tops for coincident
facade identities remain ambiguous. A reflection hit adjacent to the interior
of its own solid footprint is also withheld, rather than forcing two touching
roof intervals through the existing boundary kernel.

The existing geometry-conflicted road stays withheld. No roof percentile,
terrain tolerance or physical input is changed to force a pass.

## Block 1: connection and support accounting

The current focused suite passes **221 checks**, including **17 new connection
checks**. It covers the native builder/materializer/kernel connection using
controlled inputs, exact policy identity, preserved evidence, distinct path-air
distances, duplicate paths and planes, unknown/absent contributions, physical
wall-size conflicts, source-length conservation, endpoint/unfolding mismatches,
own-footprint conflicts and exhausted search budgets.

Five retained public direct/reflected examples pass the connection at all eight
octaves. Two controlled 60 m road lines, clear and screened, connect explicit
two-group source/air inputs across three periods and both H/F conditions. Three
midpoint spacings are retained. The independent review checks every retained
source weight and path/source/air energy product, then compares all 96 integrated
condition/period/octave values with separate adaptive quadrature.

The native attachment covers the existing **722 road snapshots / 1,418 reflected
profiles**. It reuses byte-preserved, independently audited physical overlays
from `physical_scene_assumptions_v2/profile_sensitivity.csv` and attaches the new
facade assumptions and guards. Repeating the old fifteen-case terrain overlay
was unnecessary. Native profile hashes, finite plans, feature identities, wall
tops/bases, inherited geometry diagnostics and new holds are independently
checked against the original inputs. These are input checks, not real-road
receiver decibels or new pilot locations.

Of these 1,418 prepared profiles, **556 retain geometry candidates and 862 are
held**. Holds overlap: 823 touch the interior of the reflector's own footprint,
84 have missing candidate endpoint elevation, 83 have unresolved source/deck
policy, five have missing reflector tops, and some have other inherited native
or obstacle support gaps. A geometry candidate is not a computed or qualified
reflected sound level.

## Block 2: independent reference comparison

All **448 original public scalar comparisons pass**, and all **96 controlled
road condition/period/octave integrals** agree with independent quadrature
within the fixed **0.005 dB** target. The largest road-integral difference is
**0.000028947 dB**. All 240 public connected H/F/period/octave energy densities
agree within **1.072 × 10⁻¹¹ dB**, and the unsimplified public paths agree within
**1.063 × 10⁻¹¹ dB**. The comparison uses the retained original
NoiseModelling TC16/17/18/26/27 fixtures and Java expectations, plus existing
independent scalar/numerical boundary references, receiver-image finite-wall
geometry, circular arc quadrature, Decimal air absorption and adaptive
Gauss–Kronrod road integration. The verifier imports neither the new connection
module nor its runner. An external Java engine is not executed.

The original 448 public scalar comparisons and their published tolerances are
retained. TC27's previously documented 125 Hz homogeneous receiver discrepancy
against its printed value remains visible; its original 0.1 dB tolerance is not
replaced by a fitted value. The controlled road comparison target is 0.005 dB.
Numerical agreement is not a real-world uncertainty bound.

The adapter's existing 0.1 m vertical terrain approximation is checked separately
at all original knots. Its connected acoustic result is compared using the
actual simplified cross section, including its consistent ground chainages.
Original unsimplified public paths retain their own independent comparison.
The initial checker incorrectly compared the simplified adapter with an
unsimplified profile; correcting that reference accounting does not change
production calculations or relax any numerical tolerance. The final review
binds both the previous frozen checker and its corrected source. Path-air
accounting also explicitly retains the 3D endpoint-height difference, rather
than using only plan distance. In these public examples, the declared terrain
approximation changes a path transfer by up to **0.048055 dB** relative to the
unsimplified profile. That approximation difference is reported separately from
independent implementation agreement; a 0.1 m geometric bound is not a noise
error bound.

## Preserved results and stopping point

The existing direct-road ledger remains byte-identical to
`remaining_road_validation_v1.json`: 96 adaptive cases passed both numerical
checks, with one height-conflicted case held. Earlier receipts and their source
guards are preserved. The new conditional gate has its own versioned evidence;
it does not rewrite those historical validations.

Reflection searches still use the existing 100 m extra-path preparation limit
and two-profile output cap. These are resource choices, not proven acoustic
cutoffs. Missing facade inventories, other paths/orders, unbudgeted roads, rail,
aircraft and qualified real input properties still prevent complete sound at a
place. All complete/all-inclusive values remain null and the installed map is
unchanged.

The stopping point is closure of these two connection/reference blocks. Work
after that should deliver useful product behaviour or resolve specific missing
inputs, rather than continually adding generic propagation scenarios. A future
claim about whole real-road reflected sound would additionally need its actual
source-line calculation and adequate path support; this report does not claim
that calculation was performed.

## Evidence and reproduction

Completion evidence is the validation receipt above, the 221-test JUnit receipt
`artifacts/road_reflection_connection_tests_v2.xml`, and these sealed generations:

| Generation | Identity |
|---|---|
| `road_reflection_connection_v2` | `road-reflection-connection-b52bb90a9747be9f0a05` |
| `road_reflection_connection_review_v1` | `road-reflection-review-97d49e4f262c027a4207` |

The validation receipt binds all **92 current code-file hashes**, both inventories,
the unchanged direct ledger and prior validation, and the focused test receipt.
Its SHA-256 is `7c3c8a6d0e2d6033685eb763bf69c60d27d160f1f56fb08823cc1048bb528168`.
All 448 retained source-point weights and all 1,418 native facade attachments
pass independent accounting. No numerical jobs remain running for this task.

The stopped, unsealed `road_reflection_connection_v1` development directory is not
completion evidence and is preserved. It was interrupted to add the wall-size
guard and reuse existing physical overlays instead of repeating them.

Reproduce into fresh outputs:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_conditional_road_reflections.py tests/test_screened_reflection.py tests/test_reflection_path.py tests/test_road_line_integration.py tests/test_road_scene_builder.py tests/test_conditional_road_propagation.py -q -p no:cacheprovider --basetemp artifacts/reflection_tests_NEW --junitxml artifacts/reflection_tests_NEW.xml
.venv\Scripts\python.exe scripts/117_validate_road_reflection_connection.py --output artifacts/reflection_connection_NEW
.venv\Scripts\python.exe scripts/118_verify_road_reflection_connection.py --inputs artifacts/reflection_connection_NEW --output artifacts/reflection_review_NEW --junit artifacts/reflection_tests_NEW.xml
```

Primary method sources are the retained [2015 Annex II](https://eur-lex.europa.eu/eli/dir/2015/996/oj),
2018 correction and [2021 amendment](https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj).
The retained public fixtures and licence are from NoiseModelling commit
`1a0158958d3288752daef3a3a60b5f8bc1afacef`; no implementation code was ported.
