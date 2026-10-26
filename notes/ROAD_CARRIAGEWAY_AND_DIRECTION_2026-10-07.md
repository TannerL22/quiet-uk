# Road carriageway and direction handling — 7 October 2026

The bounded north-Heathrow study now has a source-preserving carriageway input
generation and a decision for every physical road link. It retains **722 provisional
unsplit traffic budgets**, including all 15 previously linked minor-road points.
It withholds whole-road totals from slip roads, roundabouts and unresolved separate
carriageways. **No separate-carriageway pair qualified in this study.** This is an
input-handling increment; it creates no noise predictions or explorer changes.

The study retains the fixed 4 km² core, BNG `[506000,178000,508000,180000]`, and
144 km² source area, `[501000,173000,513000,185000]`. The 5 km margin remains a
trial boundary, not a proved acoustic cutoff.

## Source evidence and interpretation

The original exact-2021 DfT annual totals and April 2026 OS Open Roads GeoPackage
are unchanged. An additional **32 official annual-direction records at 17 donor
IDs** fill the earlier temporal selection's donor omission. Together with its
187 records, the selection now retains **219 records at 113 points**: 106 opposite
direction pairs, five combined `C` records and two unpaired records (74924/E and
74925/W). All original coordinates, vehicle categories and Counted/Estimated
flags remain available. Of those points, 57 are Counted and 56 Estimated; a Counted
annual estimate does not establish continuous full-year observation.

[OS form-of-way definitions](https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification/code-lists-and-enumerations/formofwayvalue)
identify a collapsed dual carriageway as one line representing both carriageways.
It receives one unsplit budget containing the original streams. A dual carriageway
has separate physical representations; copying a whole-road total onto both
would duplicate traffic. Single carriageways can carry one or both directions.

[OS RoadLink attributes](https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification/feature-types/roadlink)
identify start/end nodes with geometry vertices; these do not encode legal travel
direction. [The product specification](https://docs.os.uk/os-downloads/products/transport-network-portfolio/os-open-roads/os-open-roads-technical-specification)
also describes generalised centreline geometry and nonpersistent IDs between
releases. The 2026 links have not been established as the 2021 physical network.

A bounded Overpass query requested the **31 December 2021, 23:59:59 UTC** historical
snapshot and retained **5,241 OSM road ways**, their full geometry, tags, versions
and edit timestamps. The query includes explicit `oneway` highways plus major
through-road classes in a small envelope around the source area. According to
[OSM's one-way definition](https://wiki.openstreetmap.org/wiki/Key:oneway), `yes`
follows the OSM way's own vertex order and `-1` reverses it. This direction is
transferred geometrically to an OS link; OS vertex order alone never supplies it.
The year-end snapshot corroborates historical direction, but does not prove a
restriction held throughout 2021. It is community evidence, not authoritative
traffic regulation or independent road-flow validation.

Exact API bodies, queries and retrieval receipts are preserved. Initial Overpass
requests returned 406/429; the latter requested a meaningful User-Agent. The
successful bounded retry supplied the project identity and reused the already
acquired DfT rows. A second endpoint timed out; its receipt is retained. No paid
road dataset, nationwide replacement download or external enquiry was required.
Primary documentation was retrieved using the installed Firecrawl skill and its
secondary connection.

## Allocation rules

- Original published streams reconcile against the frozen whole-road record for
  total traffic, HGVs and all ten vehicle categories, allowing the existing
  independently rounded difference of at most one vehicle/day per known field.
  Rounding is retained: 523 accepted budgets have zero total residual, 138 have
  −1 and 61 have +1. Missing categories remain null; a valid zero remains zero.
- A single or collapsed centreline consumes its original streams once. The
  original whole-road total is audit metadata. A downstream emission calculation
  must use the components once; the convenience primary sum is not another source.
- An unpaired published direction does not prove one-way operation or imply zero
  traffic in the absent direction. A combined `C` record is not divided 50:50.
- A separate dual allocation requires exactly two qualified local members with
  opposite published directions, compatible explicit OSM one-way geometry and
  no additional concurrent same-road representation. Unassociated or differently
  associated parallel links also participate in the overlap guard.
- One-way matching requires the same road reference, at least 95% OS-link coverage
  within 10 m, and consistent direction at seven probes, with heading agreement
  within 25°. Conditional/class exceptions, reversible or conflicting tags and
  absent explicit direction are withheld; no motorway default is substituted.
- Mapping a cardinal count to a separate carriageway requires a count projection
  within 75 m, at least 20 m from both link ends and within 35° of its published
  cardinal axis. Pair separation must be 3–80 m, with mutual longitudinal and
  buffered overlap of at least 50%. Ordinary short end-to-end continuations are
  excluded from the parallel guard. These are fixed engineering refusal rules,
  not calibrated confidence probabilities.
- Slip roads, roundabouts, tunnels, loops and unresolved road identities need
  their own flow/structure evidence. They inherit no whole-road primary flow.
  Paired annual counts on an apparently one-way single carriageway are withheld
  as an evidence conflict.

The old matcher is retained as a frozen source of associations, not reused to
impute traffic. An annual count supports its point; transferring it along each
previously associated link still assumes constant flow. The same traffic can
continue through sequential links. Summing flows over links or budgets would
not produce a network vehicle total. Conservation is checked within each
carriageway representation group and includes HGVs and every vehicle category.

## Actual study result

Of 13,928 physical links, **1,014** have retained count associations from **110**
donors: 999 from the baseline and 15 provisional minor-road associations.

| Associated form | Provisional primary budgets | Withheld links |
|---|---:|---:|
| Single carriageway | 420 | 32 |
| Collapsed dual carriageway | 302 | 6 |
| Separate dual carriageway | 0 | 54 |
| Slip road | 0 | 98 |
| Roundabout | 0 | 102 |

The remaining **12,914** links have no retained count association. Their primary
flow is null. In total, 292 associated links are withheld. All 54 separate dual
links remain unresolved: 20 fail local/cardinal geometry support, 19 have nearby
conflicting one-way evidence, six fail heading agreement, six lack sufficient
one-way geometry coverage and three lack a complete published direction pair.
An apparent missing side is never repaired by copying the whole-road total.

| Length-based qualification | 4 km² core | 144 km² source area |
|---|---:|---:|
| Physical road length | 47.61 km | 1,258.89 km |
| Provisional primary-budget links | 25 | 722 |
| Provisional primary-budget length | 4.96 km | 92.72 km |
| Provisional primary-budget length fraction | 10.42% | 7.37% |

These percentages are more restrictive than the earlier **19.03% core association
availability**. That earlier figure counted associations before carriageway
qualification. This increment exposes unsafe allocations; it has not removed
official source observations or expanded the production noise coverage.

## Artifacts, verification and reproduction

The delivered immutable input generation is `artifacts/road_direction_inputs_v2/`,
identity `road-direction-inputs-203a2686c4c8393b7f9f`. Its files include:

- `carriageway_budgets.json`: explicit groups and the streams consumed by each
  representation; source attribution and consumption instructions travel with it.
- `donor_partitions.json`: all 113 original annual records, 219 original
  directional records, quality flags, rounded residuals and missing states.
- `road_representation_ledger_bng.csv`: all 13,928 original BNG line geometries,
  nodes, form, status, withholding reason, group identity and nullable primary flow.
- `allocation_decisions.json`: full per-link association/direction evidence.
- `osm_way_evidence_bng.json`: preserved way identity, version, timestamp, tags
  and projected geometry. `associated_roads_review_wgs84.geojson` is for review;
  calculations use BNG geometry.
- `report.json`, `inventory.json`, frozen code and source query/manifest copies.

The source generation is `artifacts/road_direction_sources_v2/`, identity
`road-direction-sources-b9787e3649eba6e10f86`. The first source attempt and first
completed input pass remain immutable earlier iterations; v2 adds the full
physical-network overlap guard and is the delivered input generation.

The related suite passes **162 tests**, including **50 new direction checks**.
It exercises rounded/missing/zero values, duplicate/wrong-year source refusal,
conditional and conflicting one-way evidence, short continuations, mixed or
unassociated concurrent representations, special-road refusal and accepted dual
pair conservation under input reordering and geometry reversal. Synthetic pair
tests exercise the implementation; they do not establish that a real study pair
qualifies. The test receipts are `artifacts/road_direction_validation_v1.xml`
and `artifacts/road_direction_validation_v1.json`, with code/test hashes and the
delivered input/review inventory identities.

The independent review script reconciles exports against original source geometry
and counts, checks all ten categories/HGVs and missing states per group, and
remeasures clipped coverage. The live pass verified all **13,928** original road
geometries at zero tolerance, all **113** annual points and **219** directional
records, every component in the **722** groups, and all **5,241** OSM geometries,
versions and tags. Input checksums remained unchanged. It verifies export
fidelity, not historic traffic or acoustic accuracy. Its separate receipt and
visually checked qualification map are in `artifacts/road_direction_review_v1/`,
identity `road-direction-review-7e2f3da35e7909488adb`.

```powershell
# Existing completed generations are immutable: use fresh output directories.
.venv\Scripts\python.exe scripts\48_acquire_road_direction_evidence.py --reuse-source artifacts/road_direction_sources_v2 --output artifacts/road_direction_sources_NEW
.venv\Scripts\python.exe scripts\49_build_road_direction_inputs.py --sources artifacts/road_direction_sources_NEW --output artifacts/road_direction_inputs_NEW
.venv\Scripts\python.exe scripts\50_verify_road_direction_inputs.py --inputs artifacts/road_direction_inputs_NEW --output artifacts/road_direction_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/road_direction_pytest_NEW
```

DfT and OS originals retain Open Government Licence v3.0; OSM evidence is under
ODbL 1.0 with © OpenStreetMap contributors attribution. Redistribution of an
OSM-assisted derived database must follow applicable ODbL requirements. Machine
outputs retain all three source licences/credits. Artifacts are ignored by Git;
this workspace copy is not an off-machine backup.

## Next bounded step

**Subsequent 7 October delivery:** the [road-emission assumption protocol](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md)
now freezes and evaluates those cases, with conditional source spectra and an
independent scalar verification. Actual local timing/speed/pavement remain
unknown. The next step is ground/barrier conventions and a synthetic propagation
canary. Those checks were subsequently delivered in the [10 October report](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md);
real-area ground/barrier inputs and complete compatible method qualification remain
open. The paragraph below records this increment's original handoff.

Define and test the road-emission assumption protocol: annual day/evening/night
allocation, speed and road-surface categories, with explicit alternatives and
sensitivity ranges where qualified local evidence is missing. Ground/barrier
conventions and historical road/building compatibility also remain open. This
carriageway increment must not make annual period flows, speeds or acoustic
obstacle heights appear observed. Before any propagation experiment, freeze
those assumptions and exclusion rules, including unresolved dual/slip/junction
contributions and source-margin sensitivity. A reported-only model comparison
will not independently validate missing cells or the quiet end.
