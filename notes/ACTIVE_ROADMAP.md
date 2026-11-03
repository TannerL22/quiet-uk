# Quiet UK active roadmap

Updated 14 October 2026 following the owner's data-first instruction. This is the
current delivery order; older plans and dated implementation reports are evidence,
not competing task lists. See [the finding-by-finding assessment and detailed
plan](PROJECT_STOCKTAKE_2026-09-28.md).

## Current priority: real evidence before estimated gaps

Prioritise additional qualified source data, then bounded statistical/acoustic
evaluation where data remains unavailable. This instruction supersedes the older
serving-headroom task as the immediate delivery order. The viewport-aware loader
and small correctness fixes are delivered; their checks are in
[the loading report](VIEWPORT_LOADING_IMPLEMENTATION_2026-09-29.md).

The first addition is installed: **632 distinct short recorded samples at 11
London sites**, separately labelled as historical mixed sound associated with
2019 surveys. These cannot validate annual, source-isolated or below-cutoff noise.
An offline experiment now profiles 100 road metric cores and evaluates 1,152 whole
reported patches using three fixed interpolation baselines. It exports no real-gap
predictions. [Qualification, results and reproduction](DATA_FIRST_DELIVERY_2026-10-02.md).

Next, qualify reusable provider GIS data with better year/source identity, starting
with airport evidence and country adapters identified in
[the primary-source search](NOISE_DATA_AND_ESTIMATION_RESEARCH_2026-09-29.md).
Continue pursuing unthresholded source outputs/domain definitions. The provider
enquiry was authorised and sent on **3 October** to the verified public contact:
[exact request and status](DEFRA_DATA_REQUEST_2026-10-03.md). It asks for
unthresholded road/rail outputs, domain masks and zero/nodata definitions, plus
access/construction details for the combined transport entry linked by the live
road metadata. That entry currently denies public viewing and is not qualified
for ingestion. The latest recorded status is the owner's 3 October acknowledgement
saying a reply may take a few days; this 10 October method increment did not check for a
substantive response.

The [3 October aircraft and combined-transport readiness follow-up](AIRCRAFT_AND_TRANSPORT_READINESS_2026-10-03.md)
qualifies the Heathrow publication lead and prepares a GIS enquiry (not sent).
It also audits all 450 installed rasters: all three sources report numeric values
in only 1.73% of Lden, 0.89% of Lday and 0.52% of Lnight cells. These are support
counts, not qualified totals.

The [road-model input audit](ROAD_MODEL_INPUT_AUDIT_2026-10-03.md) is complete for a
fixed 4 km² north-Heathrow core and 144 km² surrounding source area. It found 17
unused local minor-road count points, direct official traffic attribution for
17.89% of core road length, 55.56% surrounding terrain coverage, and missing
qualified traffic timing/speeds and shielding inputs. The
[6 October minor-road linkage increment](MINOR_ROAD_LINKAGE_2026-10-06.md) now
provisionally associates 15 of those 17 points, all Counted, to one
single-carriageway link each; two are withheld. It adds 0.54 km in the core and
3.54 km in the surrounding area. Baseline plus provisional association length
is 19.03% in the core, under an explicit constant-flow link assumption; 2021/2026
geometry compatibility is not established. No noise predictions were created.
The [6 October timing/speed qualification](ROAD_TRAFFIC_TIMING_AND_SPEED_2026-10-06.md)
now retains 840 exact-2021 hourly-direction records at 35 local points, including
all 15 provisionally linked minor points, plus official annual directional
estimates and national timing/free-flow speed tables. Those raw counts cover
one survey day per observed point, 07:00–19:00. National assumption candidates
are separate; local annual period flows and observed speeds remain unqualified.
Bounded WebTRIS discovery did not qualify local annual flow/speed inputs.
The [7 October physical-scene increment](PHYSICAL_SCENE_INPUTS_2026-10-07.md)
completes native 2 m terrain and last-return surface coverage across all 144 km²,
retains matching survey metadata and derives provisional building surface-height
summaries from OS generalised footprints. Survey-date assignment is explicitly
inferred from the provider's newest-survey rule at equal source resolution;
historical footprint compatibility and exact acoustic obstacle heights remain
unestablished. Missing/ambiguous heights are withheld.
The [7 October carriageway/direction increment](ROAD_CARRIAGEWAY_AND_DIRECTION_2026-10-07.md)
now retains 219 official annual-direction records and a year-end 2021 OSM one-way
snapshot. It exports 722 provisional unsplit traffic budgets and withholds
whole-road totals on slip roads, roundabouts and unresolved duals. No separate
dual pair qualifies; all 15 minor-road additions remain provisional. Qualified
representation length is 10.42% in the core, compared with the earlier 19.03%
association-availability figure before these refusal rules. The subsequent
[road-emission assumption protocol](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md)
now freezes 15 timing/speed/surface/classification cases and evaluates conditional
spectral source power for 104 distinct donors supporting those 722 budgets.
Source power is not receiver noise; local annual timing, speed and pavement stay
unknown. Limited daytime shape checks favour the national curve over uniform
at 29 of 35 survey points, without validating annual/night traffic. The
[ground/barrier audit and synthetic propagation increment](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md)
now checks all 722 retained representations at 47,454 native sample positions,
preserves unknown deck/ground/obstacle properties, and verifies 224 controlled
synthetic results plus 40 abstentions. The 249 related tests and independent
scalar/native-data checks establish construction fidelity, not acoustic accuracy.
The [2021 land-cover and historical barrier increment](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md)
now retains exact native ESA WorldCover classes across the halo and an explicit
conditional acoustic crosswalk, plus 1,206 historical barrier-related ways. All
measured G/qualified screen properties stay unknown. Original pixel/mask and
geometry checks plus 314 related tests pass. The public catalogue did not qualify
authoritative barrier assets; an unsent bounded request draft is retained.
The [terrain-profile/mean-plane increment](TERRAIN_PROFILE_AND_PROPAGATION_CHECKS_2026-10-10.md)
now implements distance-weighted fits and perpendicular endpoint geometry. All 20
public geometry comparisons, 32,908 native intervals and 354 related tests pass.
The [single-edge diffraction/ground-coupling increment](TERRAIN_DIFFRACTION_AND_GROUND_COUPLING_2026-10-10.md)
now verifies the homogeneous boundary term and frequency-dependent Rayleigh
selection: 94 public numerical comparisons and 411 related tests pass. All 100
real paths remain withheld. The
[multiple-edge/finite-width increment](MULTIPLE_EDGE_AND_FINITE_WIDTH_PROPAGATION_2026-10-10.md)
now checks blocked homogeneous paths over thick roofs and multiple ridges:
192 public comparisons, 576 independently reproduced numerical cases and 489
related tests pass. The
[clear-sightline/remaining horizontal-obstacle increment](CLEAR_SIGHTLINES_AND_OBSTACLE_CONFIGURATIONS_2026-10-10.md)
now adds physical-edge selection, roof surface/G support and fixed-chain image
handling: all 614 public comparisons, 1,064 independent numerical cases and 568
related tests pass. The subsequent
[favourable propagation/atmospheric absorption increment](FAVOURABLE_PROPAGATION_AND_ATMOSPHERIC_ABSORPTION_2026-10-10.md)
adds amended circular paths, explicit air inputs and sound-energy weather weighting:
911 public comparisons, independent boundary/960 atmospheric grid checks and 632
related tests pass. The
[11 October reflection audit and single-wall increment](REFLECTION_PATH_AUDIT_AND_SINGLE_WALL_CHECKS_2026-10-11.md)
now adds reflected power, wall-top losses, known absent curved paths and sound-energy
path addition: 136 public comparisons, independent checks and 718 related tests pass.
All 100 real paths remain withheld for qualified wall/source/ground/weather inputs.
The amended lateral method excludes segmented roads and reflection images.
The [terrain/screen reflection and source-line increment](SCREENED_REFLECTIONS_AND_ROAD_LINE_INTEGRATION_2026-10-11.md)
completes these controlled calculations: adjacent physical reflection legs,
incoherent power per metre, sound-energy path/weather integration, 448 public
comparisons, 80 road octave integrals and 767 related tests. One 0.03 dB public
receiver discrepancy is retained within its original 0.1 dB tolerance.
The [real-road scene builder](REAL_ROAD_SCENE_BUILDER_2026-10-11.md) now automates
geographic preparation for all 722 retained source roads: 5,448 source cells,
722 direct snapshots and 1,418 facade-plan profiles at the existing core centre.
All 791 related tests and independent native-data/vector checks pass. The complete
13,928-road ledger remains visible; 13,206 roads still lack accepted source budgets.
The [traffic/speed/weather increment](ROAD_OPERATING_INPUTS_2026-10-11.md) now
supplies a conditional bundle for these 722 roads, with all 17 omitted donors
examined, 72 added survey records and 25,810 original Heathrow 2021 weather reports.
The 8,760-hour calendar retains DST and missing support; 8,516 hours have complete
accepted air tuples. Vehicle speeds, timing, uniform path-air transfer and gaps
have explicit assumptions plus comparison cases. All 824 related tests and the
independent record/equation audit pass. These candidates are usable under the
protocol; observed local annual timing/speeds and favourable probability remain
unqualified. The [physical-scene assumption policy](PHYSICAL_SCENE_ASSUMPTION_POLICY_2026-10-11.md)
now supplies global candidate roof tops, full-support conditional ground, explicit
missing-height/screen cases, bridge/tunnel proximity flags and per-class speed-valid
pavement comparisons. All 857 related tests pass. Fifteen cases cover all 2,140
prepared profiles; these are input and sightline diagnostics, not receiver decibels.
The [conditional direct-road integration](CONDITIONAL_ROAD_PROPAGATION_2026-10-11.md)
now executes at the existing receiver over all 722 retained road lines: 32,688
quadrature evaluations under two declared physical cases, with traffic-group/hourly
air energy averaging and retained unknown support. The combined reference case has
595 roads numeric at every fine-grid sample and 127 held roads. All 902 related tests
and independent accounting pass. The original computational equations are preserved;
this is an offline conditional direct component, with reflections explicitly omitted.
The [adaptive integration follow-up](ADAPTIVE_ROAD_INTEGRATION_2026-10-11.md) refines
all 97 previously unstable road cases with 22,703 observations. Independent checks
cover every point, source-air product, leaf weight, road sum and original ledger
entry. A separate 1,320-observation half-spacing check passes on two of the three
selected cases; the original 9.3 dB discrepancy becomes 0.0004 dB on that road.
The third case exposes an unsupported positive path and its earlier primary value
is removed from the final ledger. Sixteen selected cases retain their primary
diagnostic, including 14 without the finer check; 59 are physically held and 22
exceed the initial observation budget. All 961 related tests and both independent
accounting reviews pass. No rigorous numerical bound or field accuracy is claimed.
The [curved-path terrain/obstacle correction](CURVED_PATH_TERRAIN_OBSTACLE_FIX_2026-10-12.md)
now repairs all 65 recorded failed points across 59 road cases. The fault came from
validating arcs between noncontrolling nearby candidates before choosing the
enclosing path. Independent circle geometry verifies the corrected chains and all
23,958 previously numeric point chains remain unchanged; 1,005 related tests pass.
Physical input support and the earlier ledger are preserved. The
[corrected road-integration follow-up](CORRECTED_ROAD_INTEGRATION_2026-10-13.md)
now reruns all 59 affected roads under the same physical reference case. Of these,
56 meet the primary diagnostic, two exceed the unchanged observation budget and
one has two height inconsistencies. Independent primary review passes for all
62,040 observations, 11,153 leaf rules and all 1,444 ledger entries; 1,013 related
tests pass. The finer pass and its independent review are complete on all 56
passing roads plus two controls: 57 pass, with a maximum change of 0.075211 dB.
A newly sampled ground average rounds above 1 by one floating-point step;
independent exact arithmetic confirms the represented average is exactly 1.
That road's earlier numeric total is removed from the final ledger. All three
reviews pass; **55 of 59 affected roads retain both numerical checks, with four
still withheld** at that checkpoint. The
[14 October ground-average correction](GROUND_AVERAGE_ROUNDING_FIX_2026-10-14.md)
now resolves that rejection with exact arithmetic only for derived boundary
rounding; supplied factors remain strictly validated. All 629 positions on the
affected road are replayed from native geography, with all 628 previously numeric
observations bit-identical. Its finer check passes at 0.079022 dB. All 1,050
related tests and independent review pass. At that checkpoint the ledger retained
**56 of the 59 affected roads, with three still withheld**; the other 1,443 entries
were preserved. The completed [24 budget-held case increment](BUDGET_HELD_ROAD_INTEGRATION_2026-10-14.md)
now passes all 24 primary and finer-spacing checks, plus both independent reviews.
The largest finer change is 0.030198 dB, below the unchanged 0.1 dB target, and all
1,420 other ledger entries are exactly preserved. The increment's receipt is
`artifacts/budget_road_validation_v2.json`; its ledger has **82 cases passing both
checks, 14 older primary-met cases awaiting finer checks and one height-held
case** across the 97-case adaptive study. The earlier circular-envelope group now
has 58 of 59 cases passing both checks. Acoustic equations and source inputs
remain unchanged, with 1,059 distinct tests covered by the linked receipts.
The completed [remaining-case follow-up](REMAINING_ROAD_CHECKS_AND_HEIGHT_AUDIT_2026-10-14.md)
now passes all 14 older cases and both independent reviews. All 6,016 primary
positions were replayed with bit-identical transfers/source powers/densities;
4,680 finer positions give a maximum change of 0.050122 dB. The current receipt is
`artifacts/remaining_road_validation_v1.json`, with 1,066 distinct tests covered
by linked receipts and all 1,430 unselected ledger entries preserved. The study
now has **96 cases passing both checks, zero older primary-met cases awaiting
finer checks, and one height-held case**. The completed native height audit
identifies a flat DSM-median roof conflicting with terrain at a partly overlapping
raster/footprint edge. That road remains withheld pending matched geometry or a
separately justified model treatment; the acoustic equations and input data are unchanged.
The [bounded reflection connection and independent numerical comparison](ROAD_REFLECTION_CONNECTION_CLOSURE_2026-10-14.md)
now closes the two agreed validation blocks: 221 focused checks, 448 original
public comparisons and 96 controlled road integrals pass. Explicit facade
hypotheses and path-specific source/air energy are connected without duplicate
paths or length. Existing native input attachment covers all 722 snapshots and
1,418 reflection profiles, reusing the already audited physical overlays.
Whole real-road reflection integrals have not been computed; the capped facade
search remains incomplete. These checks qualify the connection arithmetic,
not real-area accuracy or a complete road total. No additional general
propagation-test expansion is scheduled. Further work should deliver useful
product behaviour or address identified input/source/path gaps; independently
matched sound evidence remains necessary for field-accuracy claims.
The [17 October actual road-line calculation](ACTUAL_ROAD_REFLECTION_CALCULATION_2026-10-17.md)
now applies the declared direct/reflected model to the retained real roads. Its
fixed ledger covers 722 roads, preserving 128 existing direct-component holds;
594 are eligible for calculation. Outward-facing facade selection removes
building-back candidates, the two-profile cap is removed within the declared
100 m detour model, and finite-facade/detour changes split source intervals.
Exact-coordinate observation journals and completed-road files support restart
recovery. It completed on 3 November 2026 as the sealed generation
`artifacts/real_road_reflection_calculation_v5` (the faster, byte-identical v5
engine): 351 roads accepted, 235 withheld for primary support or numerics, 8 withheld
in the finer check, and the 128 existing direct-component holds. The unfinished v4 run was stopped; all 394 of its
completed roads and all 277,948 of its saved observations are identical in v5.
It is not a map release. There is no additional general propagation-test programme.
Complete road-network and all-inclusive predictions remain withheld. Historical
counter mapping/full-year quality and remaining physical/traffic inputs still
precede a credible real-area absolute prediction. The provider wait does not
block these input improvements or place-summary preparation.

For estimation, test an acoustic propagation approach with road/traffic, terrain,
building/barrier and independently matched sound evidence. Validate at withheld
geographies and the quiet end before displaying uncertainty-labelled predictions.
Enclosed low-colour rings alone do not justify assigning quietness or absolute dB.

## Product and current boundary

An elegant, general-purpose UK map for understanding quieter and louder places,
backed by traceable evidence. Housing, walking and research are possible uses.

The delivered local release is a continuous **5,000 km² Oxford–London** footprint,
with 50 cores and 450 native 10 m road/rail/aircraft indicator rasters. It provides
search, source/time controls, exact point inspection, saved comparisons, explicit
unknowns, aircraft cues and portable evidence exports. Regional serving works
without the optional provisional historical England-wide overview.

This is historical modelled transport evidence, not present-day total sound.
Road/rail reference 2021; aircraft's provider period remains unspecified. No real
unknown cell is assigned a certified quietness bound. No UK-wide detailed coverage,
real event dataset, public service or independently validated research exposure
release is claimed. Scientific rules remain in [the research contract](RESEARCH_DATA_CONTRACT.md).

## Completed checkpoints

- Portable tests, byte-preserved provider fixtures and Windows/Linux Python
  3.12/3.14 CI. The [latest implementation matrix passed](https://github.com/TannerL22/quiet-uk/actions/runs/36469407333).
- Shared evidence semantics: zeros, nodata and extract limits remain distinct;
  airport minimum, actual threshold and observed minimum are separate.
- Bounded extraction audit: 15 exact protocol pairs, 11 unavailable comparisons,
  two rejected edge-grid mismatches. This does not resolve zero/domain meanings.
- Independent regional serving, source warnings, historical hatch seam fix and
  tiled acquisition with cumulative budgets and recoverable checkpoints.
- Oxford–London acquisition, offline display reproduction, 765 display joins,
  unchanged western images, serving verification and launcher/browser delivery.
- Local serving bottleneck and resource increment: verified private copies,
  reusable bounded readers, 128 MiB GDAL pixel-cache cap and disk-streamed ZIPs.

Latest five-client measurements: point p95 **140 ms**, comparison p95 **238 ms**,
startup **47 s**, peak process RSS through export **781 MiB**, first export **70 s**,
ZIP **742 MB**, private snapshot plus ZIP **6.14 GB**. All 2,448 archive members
verify. The 450 rasters fit the 512-reader cache. These results are local and do
not establish larger-release or public capacity. Detailed evidence and historical
measurements are in [OXFORD_LONDON_EXPANSION.md](OXFORD_LONDON_EXPANSION.md).

## Coverage milestone and retained capacity work

**Before a substantially larger acquisition: a bounded serving-headroom check using the data already held.**
Exercise the existing 450 rasters with a reader cap below that working set and
compare with the current baseline. Include dispersed/local point access,
comparisons and concurrent images/exports. Measure browser initial loading,
pan/zoom and layer switches; current display creates an image layer per selected
core. Preserve exact observations, missing states, geometry and hashes.

Keep the existing five-client p95 gates: point <500 ms, comparison <1,500 ms.
Record startup, RAM, disk and browser costs and a next-batch budget. A reduced
cache is a pressure test, not proof of a larger manifest or national scale. Fix
only demonstrated failures, then stop optimising and proceed to acquisition.
Test the actual enlarged release before it is delivered.

| Order | Deliverable | Completion evidence |
|---|---|---|
| 1. Serving headroom | A measured capacity decision for the next regional batch | Cache-pressure and browser checks above; correctness retained; explicit resource budget |
| 2. More useful coverage | An adjoining continuous area, exact boundary chosen from provider/grid/resource checks | Reuse originals, acquire only new cores, budgeted recovery, exact overlaps, reproduced maps, display joins, full serving checks and installed launcher release |
| 3. Place summary and evidence improvements | Road, rail and aircraft visible together for a selected place/metric; justified interpretation gains | Heathrow source visibility, explicit unavailable evidence, unchanged original values and versioned interpretations; no invented total |
| 4. Traceable England coverage and distribution | Repeatable batches, new source-linked overview, restorable releases and an optional public beta | Replace provisional overview as new coverage permits; off-machine restore; measured hosting capacity, attribution, operational limits and rollback |
| 5. UK coverage and specific research uses | Country-specific adapters, justified new metrics and documented analysis examples | Source/year/domain compatibility; uncertainty and exclusions; appropriate independent validation for each scientific claim |

Steps 2 and 3 can progress together after the capacity decision. Documentary
validation proceeds alongside delivery. Public regional availability can precede
England completion if its own distribution and hosting gates pass.

## Evidence and distribution work alongside expansion

Geographic expansion does not resolve Unknown inside existing coverage. Publish
footprint and reported/unknown support separately by source/metric. Pursue
provider zero/nodata and calculation-domain authority plus an airport/product/year/
threshold crosswalk. The [clarification draft](PROVIDER_CLARIFICATION_DRAFT.md) is
retained for possible technical follow-up. The focused initial enquiry has been
sent with the owner's authorisation; see its current status above.
Do not block every new region on that reply or infer bounds while waiting.

Data artifacts are excluded from Git. Each new release needs an inventory,
checksums, licence/attribution and restoration instructions. Arrange an off-machine
copy when storage is selected and verify restoration. A source push is not a data
backup. Independent field validation requires a separately designed study; it is
not replaced by exact TIFF comparisons or a generic research-quality label.

## Scope discipline

The owner prioritised coverage over further usability questions. Retain the
[partial feedback](USABILITY_P1_2026-09-27.md); do not reinstate a five-person gate
as an expansion blocker. Use short practical checks for actual new interactions.

Keep speculative sub-threshold road models out of production. Permit bounded,
documented evaluation under the new data-first priority. Freeze screening features, event features
without event data and repeated historical-provenance archaeology. Preserve their
code/tests/evidence. Do not add a framework, database, finer grid or distributed
service without a measured need. A place summary must keep all supported sources
visible while distinguishing annual indicators from background sound and events.

Every increment must deliver more usable coverage, a supported new answer at a
place, or a measured prerequisite for one of those. README describes what runs;
this roadmap sets order; the research contract sets scientific rules.
