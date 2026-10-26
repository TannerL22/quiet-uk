# Refining conditional road integration

This increment addresses the large spacing discrepancies in the
[previous direct-road calculation](CONDITIONAL_ROAD_PROPAGATION_2026-10-11.md).
It remains an offline calculation at the existing receiver, BNG 507000, 179000,
4 m above the containing terrain cell. Traffic, speed, weather, emission,
physical assumptions and the 0.1 m terrain-profile simplification are preserved.
The propagation equations are unchanged.

## Numerical method

Every original road turn and source metre is retained. Initial intervals split
at source terrain-grid boundaries, receiver-to-building/barrier vertex rays,
and the nearest point to the receiver. Polygon holes are included. These cuts
address supplied plan geometry; they do not enumerate every terrain-path,
ground-class, controlling-edge or frequency-dependent acoustic transition.

Each interval compares its midpoint rule, two quarter-point midpoints and two
different interior Gauss points. The retained integral uses the quarter-point
rule with positive length weights. Local absolute differences are accumulated
before A weighting, separately for all H/F and day/evening/night octave bands.
The road diagnostic checks A-weighted periods and Lden in both propagation
states; it does not promise a relative tolerance for every individual octave.

All original coarse/fine samples are retained as verified observations. Every
refinement keeps historical probes when estimating local variation, so a peak
already encountered cannot simply disappear from the sampling diagnostic.
Refinement targets the largest contribution to the remaining error indicator.
Both sample weights and source length are recorded for independent replay.

The declared target is **0.1 dB for the numerical indicator**, with initial
panels at most 4 m, child panels at least 0.01 m, at most 2,048 observations
including reused samples, and at most 512 refinements per road case. The small
child intervals are quadrature choices; they do not improve the resolution of
the underlying terrain or building data.

Passing this indicator is **not a rigorous integration error bound**. A
controlled narrow-step test explicitly demonstrates an unsampled feature can
produce an actual discrepancy larger than the indicator. Known discontinuities
should therefore be partitioned, and smaller-spacing checks remain necessary.
No continuous convergence or field-accuracy claim is made.

## Population and withholding

The predeclared selection covers every previous road case with a worst absolute
H/F period/Lden coarse-to-fine change greater than 1 dB: **97 cases on 96 distinct
roads**, comprising 96 combined-reference cases and one baseline case. Their
case-weighted source length is 7,403.10 m. This is refinement of the existing
retained network, not a new geographic pilot or an expanded road inventory.

The complete 1,444-case ledger remains available. Unselected cases retain their
previous finite-grid records and carry an explicit outstanding integration flag.
A selected case that fails its new check receives no fallback numeric value
from the earlier run. Results distinguish an unsupported positive path, sample
budget exhaustion, minimum panel size, refinement budget, and a met diagnostic.
Any unknown observed at any reused, coarse, fine or offset sample withholds the
whole-road contribution, even when the final midpoint sum itself is numeric.

The three largest prior discrepancies among the settled cases receive an
additional check with four midpoints on every final adaptive interval: half the
accepted midpoint spacing. This check uses the same physical model. It is a
numerical sensitivity test, not independent acoustics or field validation.

## Verification and release scope

The 902 existing related tests and 59 additional tests pass: **961 tests**.
New tests cover finite-line analytic integration, declared steps, sampled peak
retention, positive weights, length conservation, seed reuse, independent-offset
alias detection, zero energy, invalid inputs, sticky unknowns and transactional
budget interruptions. The finer-spacing tests cover every possible missing
sample and rejection of a large numerical change. Six additional checks ensure
a finer-check failure removes the earlier numeric road value from the final
ledger, preserves untouched cases and cannot resurrect an already withheld road
or confuse different physical cases on the same line.

The independent reviewer reconstructs source/category/weather products, every
point identity, leaf weights, absolute local indicators, road sums, withholding,
selection and original-ledger carry-forward. Source terrain traversal and
obstacle rays are checked through a separate geometric construction. Sampled
native profile replay and frozen uncached kernel replay are labelled as parity
checks. The finer-spacing reviewer independently checks all four-point weights,
source-air products and period/Lden changes. Prior executed physics and copied
air tables must be byte-identical.

Reflections, lateral diffraction, transmission, outside-halo roads, 13,206
unbudgeted road records and non-road sound remain missing or omitted. None of
these are assigned zero. An all-inclusive noise value remains unavailable.
The installed map receives no estimated decibels from this work.

## Run evidence

The primary calculation completed on 12 October 2026. Its sealed generation is
`adaptive-road-integration-c24c434003308f80b1c0`. It records **22,703 observations**,
including 3,866 verified observations reused from prior calculations and 18,837
new evaluations.

| Primary outcome | Road cases | Meaning |
| --- | ---: | --- |
| Numerical indicator met | 17 | Complete sampled road support; maximum indicator 0.096024 dB |
| Unsupported positive path observed | 58 | Whole-road value withheld; recorded reason `unsupported_circular_envelope` |
| Initial partition exceeds budget | 22 | Whole-road value withheld before fresh propagation evaluations |

The last group needs between 2,135 and 11,480 initial observations under the
declared partition, above the 2,048-observation allowance. The limit is a
computational policy, not evidence that the omitted road is quiet. Unknown
observations also remain decisive when the same road would exceed the budget.

The 58 physically held cases contain 64 recorded unsupported observations.
An earlier sampled value on these roads does not replace their unknown support.
The 17 cases meeting the primary indicator have a maximum midpoint/Gauss change
of 0.088806 dB. These two diagnostics describe the executed samples only.

The independent primary review passed for every observation, 4,240 leaf rules,
all 97 case sums and all 1,444 ledger entries. It separately reconstructs 485
source-cell partitions and replays 12 native profiles and 96 frozen uncached
octave calculations. This sampled replay checks implementation parity, not
independent physical accuracy.

The separate half-spacing check executes **1,320 new observations**:

| Road / combined-reference case | Original maximum spacing change | New half-spacing check | Outcome |
| --- | ---: | ---: | --- |
| `39445FD4-C51D-4767-B9A1-7203C773C48C` | 9.300409 dB | 0.000397 dB | Passed sampled numerical check |
| `593F62C8-8735-4122-88C7-BE7A3B8FB129` | 6.783906 dB | Unknown | One new unsupported circular-envelope observation; withheld |
| `A22C07BF-4FD2-47B4-B590-E27D6B87B335` | 3.551534 dB | 0.001448 dB | Passed sampled numerical check |

The finer-spacing independent review checks all 1,320 locations, weights,
category/air products, energies and H/F period/Lden changes. Both reviews pass;
passing the accounting review does not make the unsupported road numeric.

The [postcheck ledger](../artifacts/adaptive_road_postcheck_ledger_v1.json) is the
final consumption record for this increment. It overrides the newly failed
road's earlier primary value with unknown, flags its accuracy as outstanding,
and retains the evidence without substituting a partial sum or old grid result.
Of the 97 selected cases, **16 retain their primary diagnostic**: two also pass
the finer check and 14 were not selected for it. **59 cases are physically held
and 22 are held by the initial budget policy.** The other 1,347 original cases
retain an explicit integration-accuracy-outstanding flag. Continuous convergence
and real-area noise accuracy remain unproved.

The sealed generations are:

- Primary: `adaptive-road-integration-c24c434003308f80b1c0`
- Independent primary review: `adaptive-road-review-891e211995a70d579a9a`
- Finer-spacing check: `adaptive-spacing-check-ff27a90cf8038d781471`
- Independent finer review: `adaptive-spacing-review-bd58ea8cfbfaa5acb6fc`

Interrupted development runs are not final calculations. The primary protocol
retains the sealed cache `development-road-observations-cfb6131c94da8de6782d` as
a hash-checked parent. Its reused point observations preserve the executed
physics; its unfinished road integrals are not reused. The final validation
receipt links the ledger hash, four reviewed inventories, regression report and
unchanged prior implementation.

Evidence:

- [Primary summary](../artifacts/adaptive_road_integration_v3/report.json)
- [Primary execution protocol and parent identities](../artifacts/adaptive_road_integration_v3/protocol.json)
- [Independent accounting and geographic replay](../artifacts/adaptive_road_integration_review_v1/report.json)
- [Finer-spacing results](../artifacts/adaptive_road_spacing_check_v1/road_checks.json)
- [Independent finer-spacing accounting](../artifacts/adaptive_road_spacing_review_v1/report.json)
- [Final ledger and test validation receipt](../artifacts/adaptive_road_validation_v1.json)
- [961-test regression report](../artifacts/adaptive_road_validation_v3.xml)

![Conditional energy density along the largest-discrepancy road](../artifacts/adaptive_road_density_plot_v1/road_density.png)

This plot covers the 32.31 m road with the original 9.300 dB discrepancy.
Original coarse samples miss the high-energy part at the start of the line;
the refined samples expose abrupt changes along it. Each propagation state is
normalised independently. These are conditional direct-only point energy
densities, not observed noise, receiver totals or a comparison between H and F.

## Reproduction and next work

These are the original pipeline commands, requiring the original executed code
snapshots for historical reproduction. The current solver includes the subsequent
[curved-path repair](CURVED_PATH_TERRAIN_OBSTACLE_FIX_2026-10-12.md); a new run must
reevaluate earlier unsupported observations under that changed implementation.
Use the project root, sealed parents and a fresh output directory for each command:

```powershell
.venv\Scripts\python.exe scripts\92_refine_conditional_roads.py --output artifacts\adaptive_road_integration_next --threshold-db 1 --workers 2 --reuse-observations artifacts\development_road_observations_v1
.venv\Scripts\python.exe scripts\93_verify_adaptive_roads.py --inputs artifacts\adaptive_road_integration_next --output artifacts\adaptive_road_integration_review_next
.venv\Scripts\python.exe scripts\95_check_refined_road_spacing.py --inputs artifacts\adaptive_road_integration_next --output artifacts\adaptive_road_spacing_check_next
.venv\Scripts\python.exe scripts\96_verify_refined_spacing_check.py --inputs artifacts\adaptive_road_spacing_check_next --output artifacts\adaptive_road_spacing_review_next
.venv\Scripts\python.exe scripts\94_record_adaptive_validation.py --inputs artifacts\adaptive_road_integration_next --review artifacts\adaptive_road_integration_review_next --confirmation artifacts\adaptive_road_spacing_check_next --confirmation-review artifacts\adaptive_road_spacing_review_next --junit artifacts\adaptive_road_validation_v3.xml --output artifacts\adaptive_road_validation_next.json --ledger-output artifacts\adaptive_road_postcheck_ledger_next.json
```

The subsequent [curved-path repair](CURVED_PATH_TERRAIN_OBSTACLE_FIX_2026-10-12.md)
now resolves all 65 recorded failed point geometries. The next step is fresh
whole-road integration and finer-spacing checks under that corrected selector.
This historical generation and its postcheck ledger remain preserved. Then
revisit the 22 partition-budget cases, check the
other 14 primary-met cases at smaller spacing, and refine the retained cases
below the original 1 dB selection threshold. These remain separate from
completion of reflection assumptions, missing road sources and independent
real-noise validation. No new geographic pilot is required for this work.
