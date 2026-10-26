# Physical-scene assumption policy — 11 October 2026

The physical-input policy is now executable over the retained real geography.
It supplies a separate candidate input set, an on-demand numeric scene materializer,
and 15 declared sensitivity cases for the 722 source roads and 2,140 prepared
profiles. Original evidence, missing values and previously qualified fields are
preserved. These are research input candidates; this increment does not generate
receiver decibels or change the installed map.

This follows the [traffic/speed/weather resolution](ROAD_OPERATING_INPUTS_2026-10-11.md).
The [native physical data](PHYSICAL_SCENE_INPUTS_2026-10-07.md),
[land-cover/barrier data](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md) and
[geographic scene builder](REAL_ROAD_SCENE_BUILDER_2026-10-11.md) remain sealed parents.

## What is evidence and what is assumed

| Input | Retained evidence | Declared candidate or refusal |
|---|---|---|
| Ground | Full native WorldCover class support along each path; no observed acoustic G | Grass/crop substrate assumed porous, G=1; built/water assumed sealed/water, G=0. Other recognised classes use explicit G=0.5 substrate reference. Missing/unsupported class support remains unknown in every case. |
| Buildings | 50,495 original generalised footprints and prior height-status records; 37,167 passed the earlier paired-surface acceptance rules | Recompute absolute DSM p10/median/p90 on the same native paired interior cells. Use the median as a global horizontal candidate top, with opaque prism and hard roof G=0 assumptions. These are surface samples, not measured roof/eaves heights. |
| Missing heights | 13,328 prior held footprints remain held in the baseline | Explicit 3/6/12 m prism references above the maximum native terrain pixel-centre ordinate within the whole footprint. An unavailable base or a footprint extending beyond the extract stays held. These heights have no claimed statistical probability. |
| Barriers | 1,206 historical barrier-related records, including 562 ordinary fences, 189 ordinary walls and 24 embankments; none is explicitly a noise barrier | Baseline explicitly omits unverified screen effects. Wall scenarios assume a solid wall at unverified tagged height, or a 1/3/5 m reference. Fences become solid only in the named stress case. Unknown barrier types remain omitted; embankments are not added a second time above native terrain. |
| Source surface/deck | Native DTM and year-end 2021 OSM structure context, alongside 2026 generalised source lines; original road-structure fields remain blank | Use DTM + 0.05 m only as an at-grade reference without positive nearby bridge/tunnel context. A positive context holds source elevation pending a deck/road identity resolution. Proximity is not an established road match, and absence of tags does not prove at-grade. |
| Pavement | Retained 2021 F-4 coefficient rows and existing per-original-class speeds/flows; contextual OSM surface tags | Reference, Thin layer A and Worked surface comparisons enforce each emitting class's published speed domain. An asphalt tag does not establish an acoustic pavement code, condition or age. No speed is clamped to make a surface valid. |
| Source dynamics | Existing source-emission reference convention; local dynamics and actual corrections remain unknown | Dry, flat, steady, 20°C, no junction acceleration or studded tyres. Zero applied dynamics corrections are declared reference assumptions. Station air is not silently used as a source-temperature correction. |
| Receiver and reflections | Existing outdoor 4 m receiver reference and finite plan facade candidates | Preserve endpoint/building conflicts. A vertical facade top can be a candidate, but incident-view dimensions, absorption, coincident identity and complete reflection support stay unresolved. |

All tops are global feature properties. A building or hypothetical screen therefore
has the same candidate absolute elevation regardless of source or receiver. This
avoids deriving a different roof/deck merely because a different path traverses it.
Flat tops below reconstructed terrain, endpoint overlaps, overlapping roofs and
collinear screen extents are flagged; they are not silently clamped or dropped.

The 3 m structure corridor is a bounded context search with any positive source-line
intersection length. Nearby footbridges, crossing roads or displaced carriageways
can generate conflicts without identifying the road's own deck. These flags are
review leads, not proven road structures. The original empty structure field is
retained alongside the separate candidate status.

The physical conventions follow the retained
[JRC CNOSSOS reference report](https://publications.jrc.ec.europa.eu/repository/handle/JRC72550),
with the selected source coefficients from the
[2021 amendment](https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj).
The [earlier surface protocol](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md) records the
coefficient and rolling/propulsion conventions. None of these sources establishes
local acoustic substrate, real facade opacity or actual pavement classification.
This policy is an explicit project judgement about research references, not an
externally prescribed uncertainty model.

## Comparisons and use

The 15 cases consist of the baseline, uniform recognised-ground G=0/0.3/0.7/1
stress cases, absolute roof p10/p90, missing-building 3/6/12 m references, wall
1/3/5 m references, a solid-fence-and-wall 3 m stress case and a combined
missing-building 6 m/wall 3 m reference. Tagged screen heights, where present,
remain unchanged across screen reference cases. The wall reference varies the
untagged candidates only.

Each profile/case records unresolved identities, omitted screen identities,
full-length ground support and candidate mean G, candidate obstacle counts,
obstacles above the endpoint sightline, and maximum terrain/obstacle clearance.
Reflected profiles use the unfolded horizontal coordinate as a geometry diagnostic.
Their actual reflected/diffracted path selection is not inferred from this test.

These are geometric comparisons. More obstacles above an endpoint sightline does
not by itself determine the sign or magnitude of a noise change. Roof sample
percentiles and scenario ranges are neither confidence intervals nor guaranteed
extrema. The pavement diagnostic is spectral source power at 1 kHz, not broadband
receiver sound pressure or a transport total.

`materialize_profile` reconstructs numeric ground intervals, roof/screen vertices,
source/receiver ordinates and declared properties from the sealed parent profile,
feature table, source policy and chosen case. Native terrain is a read-only shared
input. The scene retains `scene_kind='conditional_real_input'`; it is never relabelled
synthetic. Existing propagation kernels continue to reject that scene kind until
an explicit adapter consumes the declared research policy and checks supported
configurations. A complete numeric geometry candidate does not mean a complete
acoustic path or an eligible map estimate.

## Verification and reproduction

The geographic results show where evidence and assumptions still matter:

| Result | Count or diagnostic |
|---|---|
| At-grade reference candidates | 679 roads; 43 held for positive nearby bridge/tunnel context |
| Complete baseline geometry candidates | 59 of 2,140 profiles; 2,081 held |
| Missing-height 3/6/12 m reference cases | Each yields 2,006 complete geometry candidates; 134 held |
| Baseline profiles with unresolved building/screen heights | 2,065; blocker counts overlap |
| Roof p10 cases below reconstructed terrain | 1,018 profiles; these combinations are held |
| Supported pavement cases | 1,470 of 2,166; 696 Worked-surface cases held at unsupported speeds |
| Pavement-only source scenario spread at 1 kHz | Median 2.18 dB, range 2.00–7.15 dB across roads; source band only, not a confidence interval or receiver result |

Complete geometry candidates still lack full acoustic/reflection/network support.
The reference-height cases make hypothetical geometry calculable; they do not
measure the missing buildings. This distinction is why those cases cannot simply
replace the missing evidence in a claimed accurate total.

The related suite passes **857 tests**, including 33 new tests for missing native
support, paired surveys, globally consistent tops, ordinary fences, invalid source
surface/speed domains, geometry refusals, unchanged real-scene identity and audit
boundary handling.
The independent reviewer does not import the evaluated policy or propagation,
emission or source kernels. It checks native point-in-polygon surface summaries,
native building/screen bases, original feature identities, road-structure context,
scalar pavement source equations and every profile/case ground and sightline result.
This verifies construction and equations; it does not validate real acoustic accuracy.

The geometric centre-membership check initially disagreed with the retained GDAL
polygon burn where pixel centres lay exactly on a boundary. The audit now verifies
that each differing cell is within 0.0000001 m of that boundary, records its position
and both selections, and preserves the original GDAL edge convention. A disagreement
away from the boundary fails. These exceptions are retained in the review's
`native_polygon_boundary_ties.json`; the native data and earlier height rules were
not changed to make the audit agree.

The independent audit passes all 50,495 building reference bases, 37,167 accepted
absolute surface summaries, 1,206 original barrier identities/native bases, 722
structure contexts, 2,166 pavement cases (47,040 supported source-band comparisons),
and 32,100 profile/case ground and geometric comparisons. It records 18,378,721
centre-membership agreements and 130 boundary-rule exceptions. All six parent
inventories verify unchanged; all 56 preceding production/test/configuration files
retain their previous validation hashes.

| Generation | Identity | Inventory SHA-256 |
|---|---|---|
| `physical_scene_assumptions_v2` | `physical-scene-assumptions-63692271be1fd7796b86` | `91434247cff7c2f9d7460ccf897fa1a6bc1b4ae835988c45c29c96dbdbfc2141` |
| `physical_scene_assumption_review_v1` | `physical-scene-assumption-review-41b16703dbc7f6fe6d25` | `547bb5c4b7394149a19c6d5d7e97932fa5321b9b125b785f6f89e3ce6c9af8ee` |

The final test/runtime/preservation receipt is
`artifacts/physical_scene_assumption_validation_v1.json`, SHA-256
`bb77a9088ce92e1a5a3db0db560f0c7ddb9b09c6c70e61093a0d8b0336b4250a`.
Its JUnit evidence is `artifacts/physical_scene_assumption_validation_v2.xml`:
857 tests, zero failures/errors/skips. The unsealed development `physical_scene_assumptions_v1`
directory is not an input generation: it has no inventory and is rejected by the
standard checksum verifier. It was superseded while improving scenario output and
avoiding repeated copies of the same native terrain evidence.

```powershell
.venv\Scripts\python.exe scripts/87_resolve_physical_scene_assumptions.py --output artifacts/physical_scene_assumptions_v2
.venv\Scripts\python.exe scripts/88_verify_physical_scene_assumptions.py --inputs artifacts/physical_scene_assumptions_v2 --output artifacts/physical_scene_assumption_review_v1
```

Use fresh output directories for another reproduction. Artifacts are offline and
excluded from Git. Preserve EA/OS/DfT OGL v3, ESA CC BY 4.0 and OpenStreetMap ODbL
attribution with the sealed parents.

## Remaining work

The next implementation step is to consume these candidates in a clearly labelled
conditional real-scene propagation adapter, resolve or explicitly scope reflected
path support, and execute source-line quadrature for supported geometry. This must
preserve unsupported configurations and missing road contributions. The fixed
closest-point snapshots are not full-road integrations.

Real-area accuracy still needs independent matched sound evidence; the retained
historical mixed recordings cannot validate annual source-isolated road levels.
The 13,206 ledger roads lacking accepted traffic budgets, rail/aircraft support and
unknown sources remain missing contributions. A partial road-network estimate
cannot answer the user's all-inclusive noise question. No new pilot is required
by this increment, and previous method checks do not need to be repeated without
a relevant change.
