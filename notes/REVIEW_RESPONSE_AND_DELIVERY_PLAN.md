# Quiet UK: review response and delivery plan

Prepared 24 September 2026 against source commit
`6ba3a0ed99b6b9c9165e9f47d6f9c27470fb461d` and regional release
`pilot-d0aa536e7c93d05b09fe`. This is a plan, not a claim that the changes below
have been implemented. It supersedes the previous proposal to expand coverage
as the immediate next task.

## Decision

Keep the product goal: a beautiful, general-purpose UK map of quieter and louder
places, backed by traceable data suitable for increasingly rigorous uses. Home
search and walking remain examples. Build toward research fitness by stating
exactly what each observation and comparison can establish.

The review changes the order of work. First make verification portable, then
correct the shared evidence semantics, improve the existing user experience and
test it, then expand coverage on a serving design that supports growth. Continue
bounded documentary/extraction validation alongside these steps. Do not wait for
a months-long field study to improve the existing exploratory product.

## What was independently checked here

- Re-read the current source, exports, tests, launcher and historical notes.
  The reviewed commit matches the local/GitHub checkpoint.
- Loaded the local regional release with `SourcePilot`, which verifies its
  manifest and all pinned files. Inspected the saved provider metadata and QA.
  No new provider rasters were downloaded and no published release was changed.
- Exported the committed source with `git archive` into an isolated directory
  without generated data. Ran `tests/test_candidate_viewer.py` using the existing
  isolated Windows Python environment: **9 failed, 3 passed**, because local
  candidate artifacts are absent. This reproduces the reported portability
  defect without attributing it to Linux. It is not a fresh dependency install
  or a rerun of the reviewer's complete Linux suite.
- Across the 24 road/rail records, the manifest records **13,915,376 zero-valued
  source-indicator cells and no TIFF nodata cells**. These counts include multiple
  metrics and are not unique geographic locations. Across 12 aircraft records,
  there are no zero sentinels and 10,657,963 nodata cells.
- Heathrow aircraft observed minima are **49 dB Lden, 49 dB Lday and 44 dB
  Lnight** in this extract. These are observations, not certified censoring
  thresholds or evidence that all missing aircraft values lie below them.
- Rechecked Defra's published metric/cutoff descriptions and CAA ERCD 2201.
  UI critiques from the external review remain code-inferred; our previous
  browser checks establish operation/layout, not success with independent users.

Local check evidence: `artifacts/review_followup_v1/checks.json` and the
artifact-free source export. These remain outside Git.

## Review findings: accepted, qualified and deferred

| Finding | Assessment | Planned treatment |
|---|---|---|
| D1: inconsistent quiet-end interpretation | Agree that the two views give inconsistent impressions. Zero and nodata must remain distinguishable. The counts alone do not establish their scientific meaning. | Establish source/metric/domain-specific encoding rules, then display supported bounds. Do not make the regional map agree with an unverified historical assumption merely for consistency. |
| D2: aircraft cutoff field | Confirmed machine-readable ambiguity: a shared `reporting_cutoff_db` of 40/35 is exported even though airport thresholds vary. Existing notes do warn about this, but consumers should not need prose to repair a misleading field. | Separate provider minimum reporting level, authoritative airport cutoff if known, and observed minimum for a specific extract. Actual cutoff stays null when not established. |
| D3: refusing every aircraft difference | Current restriction is conservative and can be refined. Unknown year does not inherently prevent a descriptive difference within one homogeneous product. However, the national ALL-airports coverage is a mosaic; matching its coverage ID does not prove matching periods or models at different airports. | Permit qualified same-airport/product differences when identity and compatible construction are established. Keep period uncertainty visible. Never use this to justify an all-source total. |
| D4: aviation visibility in road/rail view | Agree. A source selector and a table are weaker safeguards than an explicit cue. | Add a persistent source-scope statement and a point-specific aircraft evidence warning with a switch. An area-wide presence hint must not imply aircraft are reported at every point. Absence of mapped aircraft is not absence of aviation noise. |
| D5: clean-clone tests and fixtures | Confirmed locally. Previous 437-pass result was real but depended on a data-populated checkout. An isolated environment is not the same as a clean clone. | Make unit/component tests self-contained; retain genuine release checks as explicit integration tests. Add Windows/Linux CI and real provider fixtures. |
| D6: serving/hash/ZIP design | Agree it needs work before expansion. Hash-then-reopen has a race; startup-only hashing would also be inadequate if files remain writable. | Establish an enforceable immutable serving snapshot, then remove repeated whole-file work. Use prebuilt/streamed downloads and benchmark tile serving. Retain checksums and verification tools. |
| D7: regional serving depends on historical data | Confirmed. This unnecessarily blocks a portable regional demonstration. | Allow a verified regional bundle to run independently, with unavailable national coverage explained. |
| D8: hatch seams | Confirmed tile-local phase calculation; visual consequence was not retested in this planning pass. | Fix using global tile pixel coordinates and add an adjacent-tile continuity check. |
| D9: documentation drift | Agree about competing current claims and the README's historical bounds/ranking language. Older dated sample sizes and test counts are not intrinsically contradictions. | Keep one current roadmap and clear current product/data guidance; mark historical reports as historical, preserving evidence and links. |
| U1: actual zero/nodata patterns | Local manifest check completed above. | Encoding authority and applicability beyond these four inland samples still need checking. |
| U2: WCS 1.0 versus native subsets | Useful unresolved extraction-fidelity test. Correct dimensions/phase and earlier overlap checks do not independently prove the provider did no resampling. | Compare small matched requests via independently discovered WCS 1.0/2.0 identifiers, including raw values, masks and grids. |
| U3: historical half-cell phase | Plausible from recorded requests, not a newly demonstrated numerical defect here. | One bounded comparison can quantify it. Preserve historical provenance warnings; do not start another national archaeology project. |
| U4: actual UX | The external reviewer did not use the UI. | Combine existing browser regression checks with new observed user sessions. The old candidate-viewer browser script is not a substitute for testing the two current explorers. |

## Scientific qualifications that change the proposed fixes

**Road/rail zero does not mean literal zero dB.** Defra documents all-public-road
and railway modelling and 40 dB day/Lden and 35 dB night cutoffs. That plus our
observations supports investigating a below-cutoff class. The sources checked do
not explicitly define every numeric zero in every WCS response. Pin a provider
encoding statement or a justified, documented interpretation with checks covering
coasts, domain edges, all-zero areas and failures. An England bounding box alone
is not an applicability mask. If confirmation remains unavailable, retain an
explicit interpretation status rather than exporting a certified upper bound.

**An observed minimum is not an upper bound on missing cells.** A small crop can
omit quieter reported values, and an airport mosaic can mix thresholds and
footprints. Do not replace the global 40 with 49, or label absent Heathrow values
as `<49`, based on the observed minimum. Keep `observed_min_reported_db` scoped
to the extract/metric and separate from `reporting_cutoff_db` and censoring bounds.

**Aircraft year needs evidence levels.** CAA's report explicitly describes
Heathrow's 2021 contours and the pandemic disruption. Defra's particular airport
record still leaves Period unspecified. Regulatory/report context can support a
2021 inference without rewriting missing provider metadata as a declared date.
Record both the raw metadata and any assessed year, citations, scope and confidence.
An authoritative airport/product cross-reference can establish a period even if
the generic metadata field remains blank. Matching a contour area is supporting
evidence, not unique proof of dataset identity or period.

**Present-day exposure is a separate issue.** Show the modelling period and
historical/pandemic qualification where supported. Do not apply a blanket 3–4 dB
uplift. Traffic counts alone do not capture fleet mix, routes, operating hours or
weather. Do not combine annual and summer-only indicators in validation.

**Validation criteria need design work.** Keep extraction fidelity separate from
acoustic accuracy and from fitness for a particular research study. The review's
±5% contour tolerance and seven-day/3 dB/80%/correlation targets are suggestions,
not established standards for this project. A clipped 10 km square cannot test
full airport contour areas. Use the complete relevant extent, matching metric,
definition and period, and predefine rasterisation/boundary tolerances. A week
of current measurements cannot simply be converted to an annual 2021 exposure
using a traffic ratio. Design seasonal sampling, source separation and uncertainty
with appropriate expertise before promising field-validation thresholds.

## Delivery phases and completion gates

Effort below is a rough single-developer planning range, not a delivery promise.
It excludes provider responses, user recruitment and external measurement work.

### 1. Portable verification — first implementation task (about 1–2 days)

Deliver a source-only checkout that runs its default suite without local data.
Replace artifact-dependent unit/component inputs with small reproducible fixtures;
keep actual national/candidate-release assertions in an explicit integration
suite. Requested integration checks must fail clearly if prerequisites are absent;
ordinary CI should report them as not run rather than claim the release passed.

Add small attributed provider HTML, DescribeCoverage and GeoTIFF fixtures with
request/checksum provenance and edge cases. Keep synthetic fixtures for deliberate
fault injection. Add Windows/Linux CI with explicitly supported Python versions
and documented installation; test the declared minimum version separately from
the production runtime. Do not call a Windows-specific lock a universal lock.

**Done when:** fresh source checkouts have zero default-suite failures on both
platforms; unit checks retain their behavioural coverage; offline real-provider
parsing/grid tests pass; optional data checks are clearly distinguished; CI results
and setup instructions identify exactly what was exercised.

### 2. Correct the evidence contract and existing map (about 2–4 days, subject to evidence)

Deliver one shared interpretation contract used by lookup, legend, comparison
and export. Preserve raw bytes and encoding status independently of interpretation.
Use explicit reported / verified-below-cutoff / nodata-or-unknown / outside-domain
states. Distinguish verified provider declarations from qualified interpretations.

Remove ambiguous aircraft cutoff exports. Add extract-scoped observed minima and
period evidence levels. Add the aircraft cue in source-specific views and put the
active metric on the legend. Implement bounded differences only for established
compatible bounds: e.g. A <40 and B=52.3 implies B−A >12.3 dB for that source and
indicator. Two values in the same censored class cannot be ranked.

Perform the small WCS protocol comparison here, before treating construction as
fully established or acquiring a larger canary. Begin the airport/product crosswalk
and documentary contour check; unresolved airport evidence should restrict those
claims rather than block all road/rail improvements indefinitely.

Publish a new interpretation/display release with parent release and unchanged
raw-file hashes. Do not edit old manifests or silently change saved-link meanings.
No need to redownload unchanged source data solely to change interpretation.

**Done when:** the same evidence has consistent meaning across UI/API/CSV/JSON;
test cases cover verified bounds, ambiguous zeros, true nodata, domain edges,
aircraft gaps and different metrics; no observed minimum becomes a missing-value
ceiling; no old release changes; Heathrow source omission is obvious. If encoding
cannot be established, the release explicitly retains uncertainty for those cells.

### 3. One understandable exploration experience (about 3–5 development days plus user sessions)

Make the regional release independently runnable. Move toward one map entry point
with detailed coverage visible and source/year/resolution qualifications carried
through selection and comparisons. A detail layer must not quietly replace a
different metric with the same colour. Keep the historical national view clearly
qualified until replacement. Reuse the current frontend; no framework rewrite.

Fix hatch continuity, keyboard point inspection, phone layout and plain-language
legends. Then observe 5–6 people finding a place, interpreting the quiet end,
recognising aviation near Heathrow, comparing points and sharing a view. Adjust
the nine-indicator presentation based on observation, not speculation.

**Done when:** at least four of five participants complete the core tasks without
coaching, and none in that small sample confuses unknown with silent or misses
aircraft relevance in the Heathrow task. Treat this as a formative gate, not a
population-wide usability claim. Repeat failed tasks after corrections.

### 4. Bounded tiled acquisition and serving (about 3–5 days for a spike; revise after measurement)

Build a coherent 50 × 50 km canary from 10 km native-grid tiles, retaining some
overlap with existing samples. Do not implement expansion by merely increasing
the four-site pilot limits. Use resumable requests, provider throttling, explicit
budgets and publish-last manifests. Select the area to include useful urban,
rural, transport and boundary cases.

Reuse the existing Rasterio/tile-rendering approach where appropriate. Evaluate
COGs/overviews for derived serving assets, retain originals for analysis, and
define suitable aggregation separately for each quantity: sound energy, censoring
bounds and coverage/quality masks must not share one arbitrary resampler.

Establish immutable snapshots and reader isolation before reducing per-request
hashing. Prebuild or stream evidence bundles. Prototype memory/disk usage,
concurrency, cold/warm lookup latency and failure/rollback behaviour.

Measured regional transfer size suggests roughly 1.8 GB of accepted TIFFs for
25 comparable tiles × nine indicators; this is only an extrapolation. Set the
actual request/transfer/storage budget before acquisition, accounting for failed
attempts, evidence, derivatives and exports. National costs and service duration
remain provisional until measured; do not promise a one-day national download.

**Done when:** overlaps preserve every source value/mask; tiled seams and missing
areas pass checks; interrupted acquisition resumes reproducibly; corrupt releases
are rejected; bundle generation does not scale RAM with total archive size;
measured performance meets a stated local beta budget. An initial target can be
p95 <500 ms for a nine-indicator point and <1.5 s for three points at five concurrent
users, measured over at least 200 requests with hardware and cold-start results
reported separately. Adjust targets from measured user needs, not headline numbers.

### 5. Traceable England release, then public beta (estimate after the canary)

Expand in monitored batches with explicit coverage inventory. Derive a national
overview from the verified native data with documented grid/aggregation semantics.
Compare against the historical baseline, publish discrepancy reports, and retire
it from the default UI only when the replacement is ready. Retain archived evidence.

Separate analytical release readiness from public-hosting readiness. Before public
deployment: source attribution, basemap/geocoder arrangements, server hardening,
resource limits, monitoring, cost limits and rollback must be demonstrated.
Research exports need a clear fitness-for-use statement, not a blanket
"research-grade" badge. Public exploratory use and research suitability have
different acceptance criteria.

### 6. UK coverage and richer acoustic evidence (later)

Integrate devolved-nation products with explicit source/metric/year differences.
Prioritise newer authoritative aviation products when identity, licensing and
comparability are established. Treat any event/Number Above dataset as a separate
indicator; never synthesize peaks or quiet intervals from annual rasters. Better
quiet-end modelling comes only with a validation case and uncertainty estimates.

## Work to freeze, not delete

Pause new Phase 2 road modelling, candidate-screening features, speculative event
features and repeated historical-provenance audits. Keep the existing modules,
tests and evidence until an actual dependency/usage audit supports reorganisation.
Line count alone is not an architecture diagnosis. The current national explorer
still depends on some of this machinery. No archive branches are needed.

Avoid new databases, frontend frameworks or distributed services unless the
canary demonstrates a concrete need. Preserve the strengths already built:
original-source retention, release identity, missingness, portable exports and a
working map. Consolidate navigation and current guidance without erasing dated
reports or rewriting historical results.

## Authoritative references checked

- [Defra: Explaining the 2022 noise maps](https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps): model scope, metrics, road/rail cutoffs and limits on present-day interpretation.
- [Defra airport dataset](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8): variable airport thresholds, competent-authority lineage and unspecified Period field.
- [CAA ERCD 2201, hosted by Heathrow](https://www.heathrow.com/content/dam/heathrow/web/common/documents/company/local-community/noise/reports-and-statistics/reports/noise-action-plan-contours/LHR_2021_Summer_and_NAP_Contours.pdf): distinguishes annual and summer indicators and records 2021 pandemic/operational effects. This is a corroborating airport report, not proof of identity for every cell in Defra's national airport coverage.

No scientific interpretations, tests, application code or GitHub state were
changed in preparing this plan. The roadmap documentation is the only tracked
project change in this planning task.
