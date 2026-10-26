# Road-model input audit — 3 October 2026

## Decision

The cached inputs support a bounded traffic-linkage experiment. They do not yet
support scientifically qualified absolute noise predictions for unknown cells.
The first useful improvement is to recover **17 unused local minor-road traffic
count points**, then resolve temporal flows, speed, geometry and shielding inputs.
The Defra unthresholded/domain enquiry remains valuable while this work proceeds.

This audit ran offline on the actual cached files and the installed native road
release. It fitted no noise model, generated no noise predictions and changed no
source raster or explorer behaviour.

**6 October follow-up:** the [minor-road linkage increment](MINOR_ROAD_LINKAGE_2026-10-06.md)
has provisionally associated 15 of these 17 points, withholding two. It adds
0.54 km of associated road in the core and 3.54 km in the source area, with full
quality/category evidence retained. The figures below remain the frozen original
audit baseline; the follow-up reports provisional additions separately. The
[timing and speed audit](ROAD_TRAFFIC_TIMING_AND_SPEED_2026-10-06.md) also now
retains exact-2021 survey-day/directional evidence and separate national
assumption candidates. The [7 October physical-scene increment](PHYSICAL_SCENE_INPUTS_2026-10-07.md)
now completes native 2 m terrain/surface coverage over all 144 km² and retains
provisional building surface-height summaries. Survey-date assignment is inferred
from the provider rule; historical footprint compatibility remains unestablished.
The [carriageway/direction increment](ROAD_CARRIAGEWAY_AND_DIRECTION_2026-10-07.md)
now supplies provisional unsplit budgets and a full refusal ledger; separate dual
allocations remain unqualified. The tables below remain this audit's original
baseline. Local annual timing, observed speed and documented emission/physical
assumptions remain open requirements.

## Fixed study area and method

The study core is **2 × 2 km north of Heathrow, around West Drayton**, with British
National Grid bounds `[506000, 178000, 508000, 180000]`. A 5 km source margin gives
a **12 × 12 km surrounding area**, bounds `[501000, 173000, 513000, 185000]`.
The area was fixed before measuring coverage, to reuse the cached terrain and
installed road targets. It is an urban input trial, not a representative UK
sample or a quiet rural validation site.

The 5 km margin inherits the earlier prototype's search radius. It is an audit
configuration, not proof that acoustic contributions farther away are negligible.
The traffic matcher receives a further 500 m geometry search margin so links near
the source-area boundary can retain nearby DfT candidates.

The audit reads exact 2021 AADF, validates the traffic/road CRS, runs the existing
deterministic two-pass matcher for attribution diagnostics, and clips actual line
geometry to each area. It records both link counts and road-length coverage;
splitting a road into extra links must not change the latter. Native noise cells
are selected by their centres and owned cores, with no resampling.

Original input/code identities were checked before and after the run. The report
records **21 file hashes**, runtime versions and the native release identity
`pilot-ad6d31f2da1b0a754135`. These are present-file checksums. They do not recreate
missing historical download-response records or prove every input was surveyed
in 2021.

## Traffic: useful official data is being left unused

The cached CSV contains **22,297 exact 2021 rows**. Three have unusable flow or
coordinate evidence; **22,294** are retained, matching the earlier input inventory.
No later-year fallback is permitted. Missing vehicle-category values remain
missing rather than zero.

Within the 144 km² source area there are **96 count points**:

| Input characteristic | Count |
|---|---:|
| Major-road points | 79 |
| Minor-road points | 17 |
| DfT quality flag `Counted` | 51 |
| DfT quality flag `Estimated` | 45 |
| Points used by the existing MRDB line matcher | 78 |
| Unused points | 18: all 17 minor points and one major point |

The MRDB-based path omits the minor-road point evidence. Of those **17 unused
minor points, 16 are Counted and one Estimated**; every nearest OS feature is
classified as a minor road and lies **0.2–5.6 m** away. One of the points is within
the 4 km² core itself. These are strong linkage candidates, not certified matches:
check ambiguous junctions, source road/section identity, direction, sampling
location and changes between 2021 and 2026 before assigning a flow. A point count
does not establish traffic along an entire nearby neighbourhood.

The source has ten detailed motor-vehicle columns, including motorcycles,
cars/taxis, buses/coaches, vans and axle-based HGV categories. All are populated
at these 96 local points. Preserve this information instead of inheriting the
old light/HGV-only simplification. Mapping these fields to an acoustic vehicle
classification still needs qualification; complete columns alone do not establish
full source-emission inputs.

[DfT's guidance](https://roadtraffic.dft.gov.uk/downloads) describes AADF as a
year's average daily traffic in all directions and distinguishes Counted from
Estimated quality. Even Counted AADF is an annual estimate informed by counts,
not continuous observation throughout the year. The cached table has no hourly,
day/evening/night or directional flow columns. It cannot independently supply
Lnight or the time-weighted Lden emission profile. DfT lists separate directional
AADF and raw-count downloads; their actual temporal coverage and source-year
suitability must be checked before use.

## How much of the road network has matched traffic?

The spatial query returns 13,930 candidates; **13,928 valid road links intersect
the source area**. No fictitious links occur in this selection. The existing
matcher directly attributes 999 and imputes 12,929.

| Attribution diagnostic | 4 km² core | 144 km² source area |
|---|---:|---:|
| Intersecting road links | 526 | 13,928 |
| Road length | 47.61 km | 1,258.89 km |
| Directly matched links | 75 (14.26%) | 999 (7.17%) |
| Directly matched road length | **8.52 km (17.89%)** | **128.45 km (10.20%)** |
| Imputed road length | 82.11% | 89.80% |

“Direct” means an accepted geometric match to an official AADF record, which can
itself be Counted or Estimated. In the core, **9.07% of road length** is matched
to Counted AADF and **8.82%** to Estimated AADF. The core's 75 matched links use
only **eight distinct count-point IDs**, so 75 must not be treated as 75
independent traffic observations.

The largest unresolved contribution is minor roads: **439 core links, 37.76 km
(79.31% of core road length)** receive the old matcher's global median fallback.
Across the source area, 12,576 links use that fallback. This median is derived
from the available DfT MRDB donors; it is not local observed traffic on those
minor roads. No acoustic emission or estimated gap level was calculated from it
in this audit.

## Geometry, terrain and shielding

| Input | What is actually held | What is needed before a credible propagation trial |
|---|---|---|
| Road geometry | GB OS Open Roads GeoPackage, prior manifest declares April 2026; same-year 2021 DfT MRDB major-road geometry | Establish temporal compatibility, private/public source scope, elevation and carriageway interpretation |
| Speeds | Class-reference imputation for every assigned link; **zero observed speeds** | Source-year speed evidence or explicitly evaluated speed assumptions |
| Terrain | EA composite requested as 10 m WCS output; all **40,000 core cells** valid | Full surrounding terrain, original request/source linkage and local survey-index dates |
| Surrounding terrain coverage | **800,000 valid cells, 80 of 144 km² (55.56%)** | Acquire the uncovered **64 km²**, preferably a versioned 2 m source with survey metadata |
| Building footprints/heights | No qualified local source registered | Qualified footprints and heights/DSM, with date, CRS, vertical datum and geometry checks |
| Barriers, road surfaces and ground absorption | No qualified local source registered | Versioned inputs or explicit, evaluated assumptions; unknowns cannot become absent barriers or a known road surface |
| Annual propagation conditions | No qualified local meteorological input registered | Choose and document the propagation method and its applicable conditions |

The road product distinguishes collapsed dual carriageways, separate dual
carriageways, slip roads and tunnels. In the surrounding area there are **594
collapsed dual links, 100 dual links and 14 tunnel links**; 28 directly matched
core links have a dual-carriageway form. AADF is all-direction traffic. Before an
emission calculation, resolve whether each geometry represents both directions
or a separate carriageway, and how flow is allocated. Multiple links sharing a
count-point ID may be legitimate successive segments; that fact alone does not
prove duplicate acoustic energy. Tunnel/structure geometry also needs a suitable
source/propagation treatment.

The existing terrain extract has 10 m output pixels, not the original 2 m
resolution. Its ground elevations are 23.74–40.13 m in the core. Valid zero or
negative terrain heights must remain valid elevations; the sound-grid zero rule
must never be applied to a DTM.

The [EA product description](https://environment.data.gov.uk/dataset/09ea3b37-df3a-4e8b-ac69-fb0842227b04)
says the DTM removes surface objects, and the 2022 composite draws on surveys from
2000–2022. The survey-index service identifies local acquisition dates. This
audit does not establish those dates for the cached extract. DTM alone cannot
recover building shielding. For a DSM-derived height candidate, qualify matched
survey dates, alignment and vertical reference, and distinguish buildings from
vegetation; a DSM-minus-DTM difference is not automatically a building model.

[OS OpenMap Local](https://docs.os.uk/os-downloads/products/maps-and-imagery-portfolio/os-openmap-local/os-openmap-local-overview)
provides generalised building polygons. It is a candidate for bounded acquisition
and comparison with surface data, not a verified detailed local acoustic geometry
or height dataset. No building data was acquired in this audit. The local file
name scan found only a speed-ablation result, which is not a speed observation.
The “no qualified source” finding refers to the project's registered/cached
inputs; it does not assert that no external dataset exists.

## Existing road targets and the quiet-end limit

Each native target provides **40,000 study-core cell centres**:

| Indicator | Reported values | Zero codes (unknown) | TIFF nodata |
|---|---:|---:|---:|
| Lden | 39,990 | 10 | 0 |
| Lday | 39,972 | 28 | 0 |
| Lnight | 39,526 | 474 | 0 |

The core is almost entirely reported and is useful for a later reported-value
consistency experiment. It supplies very little real gap support and no
authoritative below-cutoff truth. A rural/quiet-border companion area and
appropriate independent evidence will be needed before testing transferability
or quiet-end accuracy.

[Defra's explanation](https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps)
identifies annual indicators, traffic/road-surface inputs, 10 m receivers at 4 m
height and reporting limits. The existing targets already come from acoustic
modelling. Their reporting cutoffs do not identify every zero as a censored
calculated value. Do not reuse the earlier prototype's inferred censor labels or
manufacture a below-cutoff training value. Await qualified domain/encoding
evidence and keep reported-only feasibility tests distinct from real-gap validation.

## Next delivery order

1. **Minor-road linkage:** delivered provisionally on 6 October; see the follow-up
   above. The pass preserves Counted/Estimated flags and full vehicle categories,
   abstains at ambiguous locations and measures improvements by clipped length.
   It does not spread one count over unrelated links. The 7 October carriageway
   increment now withholds unsupported special roads and separate duals rather
   than reusing whole-road totals on each physical representation.
2. **Traffic timing and speed:** the 6 October qualification is delivered; see
   the linked report. Survey-day counts and national profiles are retained
   separately. Local annual period flows, observed speeds and historical counter
   mapping still need qualification; annual daily averages do not become night flow.
3. **Physical scene:** native full-area terrain/surface coverage and provisional
   building-height inputs are delivered on 7 October; see the follow-up above.
   Exact acoustic obstacle heights, historical footprint compatibility, barriers,
   road surfaces and ground conventions remain to be qualified.
4. **Modelling feasibility:** use a documented propagation method after its input
   requirements are met. The old relative finite-line proxy does not establish
   CNOSSOS compliance or calibrated absolute dB. Begin with reported-only spatial
   holdouts, explicit source-margin sensitivity and a contrasting quieter area;
   retain the separate quiet-end validation gate.

## Reproduction and checks

```powershell
.venv\Scripts\python.exe scripts\41_audit_road_model_inputs.py --output artifacts/road_input_audit_v1
.venv\Scripts\python.exe -m pytest tests\test_road_input_audit.py tests\test_phase2c_road.py -q
```

Use a new output directory for another run. The audit uses cached files only and
requires the research extra already installed in this workspace. **20 tests
passed**, including exact-year refusal, duplicate count-point rejection, invalid
traffic handling, missing-versus-zero vehicle fields, clipped-length invariance,
native half-cell ownership, valid zero/negative terrain and existing matcher
regressions. The live run checked frozen input identities before and after.

Outputs under `artifacts/road_input_audit_v1/`:

- `report.json`: study bounds, input/code hashes, runtime, coverage and decisions.
- `road_link_evidence.csv`: all 13,928 link identities, clipped lengths and
  traffic attribution/quality, without exporting imputed flow estimates.
- `local_2021_count_points.csv`: all 96 local official points, detailed categories
  and nearest-road diagnostics.
- `traffic_by_class_and_quality.csv`: core and surrounding attribution groups.
- `primary-source-evidence.json`: public source URLs and Firecrawl retrieval IDs;
  provider-supplied cache timestamps are not asserted as independent observations.

Official pages were checked using Firecrawl's secondary connection. Research
remained bounded; large replacement sources were not downloaded and no outside
enquiry was sent.
