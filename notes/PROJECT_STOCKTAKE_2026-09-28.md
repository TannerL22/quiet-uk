# Quiet UK stocktake against the Opus review

28 September 2026. Reviewed source checkpoint `d768cb1`, including implementation
`e6bbc76`, against the supplied Opus review of `6ba3a0e`. This is a review and plan;
proposed milestones below are not implemented. The [active roadmap](ACTIVE_ROADMAP.md)
is the current delivery order. The [24 September response](REVIEW_RESPONSE_AND_DELIVERY_PLAN.md)
preserves the original assessment, not current completion status.

## Assessment

Quiet UK has become a credible, reproducible local explorer of historical
transport-noise evidence. It is substantially stronger than the project Opus
reviewed. It is still short of the intended broadly useful UK map: detailed
coverage is regional, many cells cannot support a noise estimate, and separate
source indicators do not answer total sound at a place.

The right next product milestone is broader continuous coverage at the existing
10 m resolution. The immediate engineering prerequisite is a bounded check that
serving works when the raster working set exceeds its cache. Fix measured failures
only, then deliver the next region. Do not turn this into another open-ended
performance programme or a national architecture rewrite.

Two different problems must stay visible: acquiring more geography makes more
places available; resolving unknown values requires new interpretation evidence
or suitable additional data. More downloads of the same product will not, by
themselves, solve unknowns already inside coverage.

## What is actually delivered

| Area | Current evidence | Remaining limitation |
|---|---|---|
| Geographic product | Continuous 100 × 50 km Oxford–London footprint: 5,000 km², 50 cores, 450 source/indicator rasters | Regional England coverage; the separate historical England overview has older construction assumptions. No integrated Scotland, Wales or Northern Ireland noise data |
| Detail | Original 10 m road, rail and aircraft rasters; Lden, day and night; exact cell inspection | Grid spacing is not positional or acoustic accuracy. No evidence supports inventing finer analytical detail |
| User experience | Map/search, source/time controls, aircraft cue, saved three-place comparisons, exports and one-click local launch | A selected source is still the main answer; all-source information is secondary. No validated overall sound estimate or event experience |
| Construction | Preserved provider responses, request records, hashes, native grids, explicit missing states and reproducible displays | Provider zero/domain meanings and airport period/product identity remain unresolved |
| Verification | Windows/Linux Python 3.12/3.14 CI passed for the latest implementation; real provider fixtures; full expansion reproduced offline | Software correctness and reproducibility do not independently validate the underlying acoustic model |
| Distribution | Portable evidence ZIP and standalone regional serving | Data artifacts are outside Git. Source on GitHub is not an off-machine scientific-release backup or a published data service |

The expansion checks cover 765 analytical overlaps and 765 display joins. All
450 display images reproduce; the 225 western images remain byte-identical to
their preceding release. These are source/derivative consistency checks, not
independent acoustic validation. See [expansion evidence](OXFORD_LONDON_EXPANSION.md).

Latest local benchmark: point p95 **140 ms**, three-place comparison p95 **238 ms**,
verified startup **47 s**, process peak through export **781 MiB**, first evidence
ZIP **70 s**, ZIP **742 MB**, private snapshot plus ZIP **6.14 GB**. Five concurrent
clients issued 200 requests per interactive workload. All 2,448 ZIP members were
verified; boundary, outside-coverage and image checks passed. These are measured
Windows loopback results with unflushed OS caches, not public or national capacity.

Evidence: `artifacts/oxford_london_serving_resources.json`,
`artifacts/oxford_london_serving_resources_ci.json`, and
[the four-job CI run](https://github.com/TannerL22/quiet-uk/actions/runs/36469407333).
This stocktake inspected the saved results and relevant implementation; it did
not rerun the expensive release reproduction or commission new acoustic validation.

## Disposition of the original review

| Opus finding | Status now | Judgment / next action |
|---|---|---|
| D1: inconsistent quiet-end interpretation | Partly addressed | Regional schema/UI/exports distinguish zero, nodata and outside extract. No real unknown has become a certified bound. Historical overview still has provisional assumptions; replace it from traceable data rather than making new semantics imitate it |
| D2: aircraft cutoff ambiguity | Misleading field corrected; evidence incomplete | Provider minimum, observed minimum and actual cutoff are distinct. Actual airport cutoff remains unknown until justified |
| D3: all aircraft differences blocked | Still restricted, deliberately | Shared national mosaic ID does not establish a homogeneous airport model/year. Establish an airport/product crosswalk, then permit only justified comparisons; do not infer a declared year from regulation |
| D4: aircraft omitted from road/rail impression | Implemented, product outcome partial | Explicit source scope and point-specific aircraft cue exist. Participant feedback still favours a place summary with all available sources visible |
| D5: clean-checkout failures, no CI/fixtures | Addressed | Artifact-independent default tests, explicit production checks, byte-preserved provider fixtures and four-platform/version CI. Keep these gates |
| D6: hashing, memory ZIP, TOCTOU, scaling | Local serving addressed; scaling open | Exact-byte verified private copies, reader reuse, bounded GDAL cache, streamed cached ZIP. Current fast path warms all 450 rasters under a 512-reader cap; larger releases use another path. National readiness is unproven |
| D7: regional app requires historical data | Addressed | A verified regional bundle runs independently with `--regional-only --pilot PATH` |
| D8: hatch phase seams | Addressed for the reported defect | Historical hatching uses global pixel coordinates; new tiled delivery also has explicit display-join checks |
| D9: competing documentation | Drift recurred; corrected in this stocktake | README still said 400 km², 48 readers and 328 MB. Roadmap mixed completed work with future tense. Current guidance is being consolidated; dated evidence stays dated |
| U1: zero/nodata patterns and meaning | Patterns established, meaning incomplete | Encoding facts are preserved; authoritative domain/censoring interpretation remains outstanding |
| U2: extraction fidelity | Bounded, partial evidence | 15 exact protocol pairs, 11 unavailable comparisons, two edge grid mismatches; all 31 successful interior responses match earlier references. Road-night/aircraft cross-protocol checks remain unavailable; production guards reject observed bad edge grids |
| U3: historical half-cell phase | Not resolved by later native-grid work | Historical grid audit is useful but not a new exact numerical reconciliation of that issue. Freeze archaeology; replace the provisional overview as coverage becomes available |
| U4: actual UX | Some browser checks and partial human feedback | Participant understood colours, source-specific road values and unavailable evidence, but wanted more data and a place-level answer. Do not restart the deferred five-person gate as an expansion prerequisite |

Sources: [semantics](EVIDENCE_SEMANTICS.md), [provider audit](PROVIDER_ENCODING_AND_EXTRACTION_AUDIT.md),
[serving contract](REGIONAL_SERVING.md), [participant feedback](USABILITY_P1_2026-09-27.md),
[original response](REVIEW_RESPONSE_AND_DELIVERY_PLAN.md).

I agree with Opus that construction integrity must precede confident interpretation,
and that legacy research branches of work should stop distracting from the map.
I do not accept three shortcuts: inferring universal censoring from reporting
floors alone; treating an observed airport minimum as a ceiling on missing cells;
or treating a shared mosaic as proof of compatible source periods. Nor should a
new unvalidated road model fill unknowns merely to make the map look complete.

## Architecture and scope critique

The acquisition → immutable evidence → interpretation → display/export separation
is worth retaining. There is no demonstrated reason to replace the language,
frontend framework or database architecture now. The main scaling questions are
more specific:

- `RegionalSnapshot` warms metadata only if every raster fits its reader cap.
  Current 450-raster timings do not test a release above 512. Raising that cap
  indefinitely would conceal a resource problem.
- `explorer/pilot.js` adds an image source/layer for every selected core. There is
  no explicit application-level viewport subset or regional multizoom tile pyramid.
  Measure browser requests, memory and layer switching as well as API latency.
- Serving copies the complete evidence tree at startup. A 6.14 GB private footprint
  works locally today but should not be extrapolated to a national launch.
  Separate a small verified serving package from archival evidence if measurements
  justify it, while preserving immutable identity and exact source queries.
- A data-equipped laptop remains central to restoration. A release inventory,
  off-machine copy and restore check are needed before calling distribution solved.

Legacy screening, speculative sub-threshold road models and temporal mathematics
remain in the repository. Keep their tests and provenance, freeze feature work,
and label them historical/experimental. Do not delete modules just to reduce line
count. The temporal library has no real event dataset to operate on; its existence
is not delivery of peaks, background levels or quiet intervals.

## Delivery plan

### 1. Establish headroom for the next coverage batch

First task: expose a benchmark-only reader-cap setting and exercise the existing
450 real rasters with a cap well below the working set, alongside the current
512 baseline. Include geographically varied and repeated-locality points,
three-place comparisons and concurrent image/export work. A reduced cap is a
cache-pressure experiment, not proof of national capacity or a larger manifest.

Also record initial map load, pan/zoom and source-switch requests/layers in the
browser. Where the next footprint adds more records, test that actual larger
manifest before delivery. Use synthetic load only when clearly identified as such.

Exit: unchanged values, masks, cell geometry and hashes; no reader/resource leak;
existing five-client p95 gates (point <500 ms, comparison <1,500 ms) pass; measured
startup/RAM/disk/browser costs and a bounded next-batch resource budget are recorded.
If a gate fails, profile and fix that cause, considering viewport display loading
or display tiles separately from analytical raster access. If it passes, stop
optimising and proceed. Do not require a national rewrite before a regional gain.

### 2. Deliver the next useful continuous area

Plan an adjoining Oxford–London expansion, choosing the exact boundary after
provider-envelope, grid-edge, overlap and resource checks. Keep native 10 m
resolution and all nine supported indicators. Reuse sealed data; acquire only
new cores under explicit cumulative request/byte/disk budgets with resumable logs.

Publish a release coverage inventory by source and metric: distinguish downloaded
footprint, reported values and unknown/nodata support. Do not advertise all 5,000
km² (or its successor) as fully known sound. Count unique core area, not overlaps
or nine indicators as extra geography. Retain parent release and rollback.

Exit: native-grid and exact-overlap verification, offline display reproduction,
gap-free joins, boundary/outside checks, full serving and browser checks, successful
launcher installation, and visible new coverage. Include a versioned release
manifest, licence/attribution, checksums and restoration instructions. Arrange an
off-machine artifact copy when a destination is chosen; verify a restore rather
than assuming Git contains the data.

### 3. Make one place easier to understand; reduce evidence gaps in parallel

Show road, rail and aircraft for the chosen metric prominently together at a
selected place, with their availability and period. Reuse the existing nine-value
query. Keep source toggles for map exploration. A source summary is useful now;
it must not imply a combined dB total or a present-day sound prediction.

In parallel, pursue the [provider clarification draft](PROVIDER_CLARIFICATION_DRAFT.md)
and establish airport/product/year/threshold provenance using saved authoritative
evidence. The draft has not been sent; external contact requires explicit user
authorization. Lack of a reply does not stop geographic expansion. If a provider
rule is established, apply it only to its evidenced scope in a new interpretation
release, preserving original encodings and regression checks.

For additional data, first assess authoritative airport contours and any monitoring
or event products against the identified gaps: exact metric/period, domain,
resolution, licence, stable acquisition and compatibility. Do not claim those
products have already been acquired or that monitoring can fill a national map.

Exit: Heathrow makes aircraft evidence immediately visible alongside road/rail;
unknowns remain explicit; exports preserve provenance. Each interpretation/data
addition has a documented gain and validation. No repetitive usability interview
is required; use a short practical check when there is a concrete new interaction.

### 4. Scale traceable coverage toward England and a distributable beta

Repeat verified batches using measured costs. Build an overview from the new
source-linked data with explicit display aggregation, then retire the provisional
overview from normal navigation while preserving historical releases. Adopt
viewport tiles, partitioned serving packages or indexed access when measured
growth needs them, with original analytical cells retained for inspection.

Public hosting is a separate release gate: immutable downloadable assets,
attribution and basemap/geocoder arrangements, resource and cost limits, public
server hardening, monitoring and rollback. Do not expose the current loopback
development server as if it were already a hosted service. Regional publication
can precede England completion if those gates pass; nationwide coverage is not
itself a security or operational readiness test.

Exit: a new user can obtain the documented release without this laptop, understand
its coverage/age, query it consistently and restore the same evidence. Public
capacity is measured on the intended deployment, separately from local timings.

### 5. Extend UK coverage and earn specific research claims

Assess Scotland, Wales and Northern Ireland products separately before ingestion;
country labels must not conceal differences in metric, year, model domain or
resolution. Integrate comparable evidence with explicit gaps, rather than implying
a uniform UK-wide measurement. No specific current product availability is asserted
by this internal stocktake; discovery is part of that task.

For research, first perform a documented airport/product cross-check and matched
contour comparison where the reference truly corresponds. That is documentary
and model-output corroboration, not independent field validation. Define
appropriate tolerances before evaluating results. Independent measurement work
needs a separately designed, source/season/year-aware protocol and uncertainty.

Publish a small reproducible area/route research example only with a stated
estimand, spatial weights, missing/censored fractions, metric/period identity and
sensitivity limits. Housing/stress studies additionally need temporal alignment
and an appropriate analytical design; no blanket research-ready or causal claim.
Add background/event/quiet-interval features only when real supporting evidence
exists. Keep the [research contract](RESEARCH_DATA_CONTRACT.md) as the release gate.

## Rules for keeping the project focused

Every increment should deliver more usable coverage, a supported new answer at a
place, or a measured prerequisite for one of those. Keep native resolution until
better source evidence warrants changing it. Stop speculative road modelling,
quietness rankings, event features without event data, repeated historic audits
and unmeasured infrastructure work. Maintain three entry documents: README for
what runs now, ACTIVE_ROADMAP for delivery order, RESEARCH_DATA_CONTRACT for
scientific rules; dated audits are supporting evidence, not competing roadmaps.
