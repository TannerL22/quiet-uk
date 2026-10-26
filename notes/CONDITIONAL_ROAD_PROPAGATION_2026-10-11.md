# Conditional real-input road propagation and source-line integration

The prepared operating and physical inputs now drive an actual offline receiver
calculation. It evaluates homogeneous and favourable direct propagation, preserves
the link between hourly traffic and air conditions, and sums sound energy along
all **722 retained road lines**. The existing core-centre receiver is unchanged:
BNG **507000, 179000**, with an assumed outdoor height of 4 m. This is the next
implementation increment over the existing inputs, not a new field study.

The run and accounting audit are complete. Numerical accuracy still needs work:
some whole-road components change substantially with integration spacing. These
conditional components therefore remain offline, with no estimated map values,
complete road-network level or all-inclusive noise total.

## What is connected

- Original vehicle categories and accepted representation budgets, consumed once.
  Published whole-road convenience totals are excluded from source summation.
- Declared vehicle speeds, national timing transfers and published octave-band
  emission coefficients. Emission retains the flat, dry, steady, 20°C reference
  surface; local source-temperature, gradient and junction corrections are unknown.
- All 8,760 Heathrow 2021 hourly candidate air tuples, including labelled gap
  references. Atmospheric transmission is averaged jointly with each vehicle
  group's traffic timing within day, evening and night. It does not substitute
  average weather or one all-motor timing curve for every category.
- Native containing-cell terrain, native categorical ground samples, globally
  declared building tops and the selected screen assumptions. Actual geometry
  remains labelled `conditional_real_input`; an explicit case/scope token permits
  the offline calculation. Default kernel calls still reject unqualified real data.
- Every original road turn and retained length: **92,722.618 m**, represented by
  **5,448 cells** of at most 25 m, then **10,896 half-cell subdivisions**. Each
  midpoint contribution is multiplied by its horizontal length exactly once.
- Eight octaves, separate H/F states, energy mixtures for explicitly assumed
  favourable probabilities 0, 0.5 and 1, and A-weighted day/evening/night/Lden
  components. These probabilities are comparisons, not measured wind probabilities.
  The [published A-weighting corrections](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32021L1226)
  are applied in sound energy, with the 12/4/8-hour Lden penalties.

## Results and their support

Two physical cases were executed over every retained road. Both use conditional
ground, global DSM p50 tops where accepted, and the existing source-deck context
rules. The second case additionally declares hypothetical 6 m missing buildings
and 3 m generic solid walls; it is not an inference that those dimensions are real.

| Physical case | Roads numeric at every fine-grid sample | Roads with an unknown positive contribution | Quadrature weights at numeric nodes | Quadrature weights at held nodes |
|---|---:|---:|---:|---:|
| Baseline: missing heights remain unknown, unverified screens explicitly omitted | 4 | 718 | 0.099 km | 92.623 km |
| Combined reference: missing buildings 6 m, generic walls 3 m | 595 | 127 | 62.208 km | 30.514 km |

“Numeric at every sample” refers to this finite quadrature. It does not establish
complete continuous path support or convergence between samples. Numeric nodes
from otherwise held roads are retained as **known evaluated partial components**;
the whole-road result remains null. The two cases have different available source
support, so comparing their partial sums is not a matched-source sensitivity or
statistical uncertainty interval.

Whole-source guards retain structure/deck conflicts, positive road length inside
a building footprint, and unresolved building heights anywhere in the conservative
source-receiver fan. Point-specific failures also stay held. Observed failures
include unsupported circular envelopes, touching/nonoverlapping-roof requirements,
overlapping roofs and zero equivalent-height sums in favourable ground coupling.
No failed positive contribution is replaced with zero.

The 584 combined-reference roads numeric at both spacings have a median worst
period/state change of **0.134 dB**, a 95th percentile of **2.326 dB**, and a maximum
of **9.300 dB**. **96 roads exceed 1 dB; 15 exceed 3 dB.** These are refinement
diagnostics, not acceptance thresholds or field-error bounds. Eleven additional
roads are numeric on the finer spacing while a coarse sample is unsupported.

Terrain reconstruction uses a declared maximum 0.1 m vertical-residual
simplification of the full native midpoint polyline, preserving exact endpoints.
That bound describes geometry only. Twelve sampled comparisons against the full
native profiles produced at most **0.143 dB** octave-transfer change and **0.032 dB**
A-weighted source-point component-density change. They do not establish a global
acoustic error bound or a whole-road noise uncertainty interval.

Reflections, lateral diffraction and transmission are explicitly omitted from
this direct-only component scope. Reflection absorption and complete search support
remain unresolved. The **13,206 roads without accepted source budgets**, roads
outside the extract, rail, aircraft and other sound sources also remain unknown.
The output never substitutes a partial sum for total noise or a certified quietness
bound.

## Verification and reproducibility

**902 related tests pass**, including 45 new focused checks for conditional scope,
exact per-path cache parity, missing-input handling, hourly joint-energy averaging,
analytic finite-line integration, conservation, zero energy and bounded workers.

The independent accounting audit checks all **722 source budgets**, **70,080 hourly
air coefficients**, **384,096 transmission-table values**, **32,688 node identities
and length weights**, **272,400 joint source-band values**, **544,800 node energy
values**, **1,444 road-case quadratures/guards** and **4,704 spacing-change metrics**.
It reconstructs 33 sampled geographic profiles, checks **127,941 native terrain
knots**, and replays **264 frozen uncached transfer bands**. Source/air/accounting
equations are independent; kernel replay is an implementation parity check.
Neither is independent field validation or an external-engine comparison.

All eight input generations verify unchanged. **56 prior code/config/test files
remain byte-identical**. Four core modules add geometry-cache decorators and an
explicit conditional-input gate; removing those additions leaves their complete
computational ASTs identical to the matching frozen originals. The previous
controlled line/reflection integrator and its qualification rules are unchanged.

The full run took **1,279 s** with two local workers. Its sealed bundle is about
**186 MB**. This is a one-receiver offline workload, not a production or national
capacity claim. Worker submission is limited to four pending roads, and source
changes during execution prevent sealing. Artifact generations 1–3 were interrupted
development runs and are unsealed; generation 4 is authoritative.

- [Road results and declared assumptions](../artifacts/conditional_road_integration_v4/road_contributions.json)
- [Per-road CSV](../artifacts/conditional_road_integration_v4/road_contributions.csv)
- [Execution protocol](../artifacts/conditional_road_integration_v4/protocol.json)
- [Independent accounting and sampled replay](../artifacts/conditional_road_integration_review_v1/report.json)
- [Validation and preservation receipt](../artifacts/conditional_road_validation_v1.json)

The sealed calculation is `conditional-road-integration-9b703e8dc7a23ffc3012`;
the review is `conditional-road-review-853e84bd2aceedc88868`. Inventories, exact code
copies and parent provenance are retained. Keep the parents' source-specific
licence/attribution manifests with the results. Reproduction requires the existing
frozen parents and a fresh output directory:

```powershell
.venv\Scripts\python.exe scripts\89_integrate_conditional_roads.py --output artifacts\conditional_road_integration_next --workers 2
.venv\Scripts\python.exe scripts\90_verify_conditional_road_integration.py --inputs artifacts\conditional_road_integration_next --output artifacts\conditional_road_integration_review_next
```

## Next implementation priority

The [completed adaptive follow-up](ADAPTIVE_ROAD_INTEGRATION_2026-10-11.md) now
refines all 97 previously unstable cases. It preserves this original generation
as evidence and supplies a separate postcheck ledger. Its half-spacing check
reduces the largest original discrepancy to 0.0004 dB on that road, while a newly
unsupported sample withholds another primary-met case. The numerical indicator
alone is not a rigorous bound; 59 selected cases remain physically held and 22
exceed the initial sample budget. The next priority is favourable circular-envelope
terrain/obstacle handling and the remaining integration support.

The original refinement priorities for this generation were to locate visibility/obstacle
and terrain transitions, refine the largest discrepancies, and retain unsupported
positive support throughout refinement. The largest original change was on road
`39445FD4-C51D-4767-B9A1-7203C773C48C`; the next largest are
`04C8025A-1D65-448D-A443-07DAF07B9744` and
`593F62C8-8735-4122-88C7-BE7A3B8FB129`.

Then audit the remaining favourable zero-height and obstacle configurations,
complete explicit reflection assumptions/search scope, and compare a suitable
matched model against independent sound evidence. Passing arithmetic checks alone
does not make these conditional calculations dependable real-area predictions.
