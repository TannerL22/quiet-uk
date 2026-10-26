# Road traffic timing and speed qualification — 6 October 2026

The next input increment is complete. **840 exact-2021 hourly-direction count
records** were acquired and checked at **35 of the 96 local count points**,
including **all 15 newly linked minor-road points**. Each observed point has one
survey day with 12 published hourly intervals in each of two directions. Official
2021 national timing indices and free-flow speed tables are also retained as
possible assumption inputs. Local annual noise-period flows and observed speeds
remain unqualified; no noise predictions or product changes were made.

This follows the [minor-road linkage](MINOR_ROAD_LINKAGE_2026-10-06.md) and the
[frozen input audit](ROAD_MODEL_INPUT_AUDIT_2026-10-03.md), using the same 4 km²
north-Heathrow / West Drayton core and 144 km² surrounding source area.

## Qualified evidence

| Source/support | Result | Permitted interpretation |
|---|---|---|
| DfT 2021 local raw counts | 840 records, 35 points, one survey date per point; hour labels 7–18 | Hourly vehicles counted on that date, with directions and ten vehicle categories preserved |
| Previously accepted minor-point associations | All 15 have complete, coordinate/road-identity-compatible 07:00–19:00 count support | Survey-day evidence at the provisionally associated point; not proof of historic whole-link flow |
| DfT directional 2021 AADF | 187 records at all 96 points: 91 paired direction sets, four `C` records and one single `W` record | Annual directional estimates; not time-of-day observations or automatic carriageway allocations |
| TRA0307 and TRA0308 | 672 exact-2021 hour/weekday index values | GB all-road aggregate timing evidence; local transfer would be an assumption |
| SPE0102, exact 2021 sheet | Average free-flow speed rows for four road types, with vehicle groups and unavailable codes retained | National sample evidence; not measured local speeds or local speed limits |
| Local annual day/evening/night flows | None qualified | Remain null |
| Local observed speeds | None qualified by this increment | Remain null |

The raw-count interpretation follows [DfT's source guidance](https://roadtraffic.dft.gov.uk/about):
the published roadside counts cover 7 am–7 pm on sampled weekdays, rather than
continuous full-year observation. Source-date, road identity and coordinates
match the frozen annual inputs at all 35 observed points.

For example, Harmondsworth Road point **811912** has a Tuesday **14 September
2021** survey: **7,626 motor vehicles**, including **201 HGVs**, across the
observed 12-hour window in both directions. These are survey-day totals. They
must not be subtracted from its annual daily average to manufacture night flow:
[DfT's annual methodology](https://www.gov.uk/government/statistics/road-traffic-estimates-in-great-britain-2024/background-quality-report)
uses date-, vehicle- and road-stratum expansion factors.

The annual directional records retain the original quality flags and detailed
estimation methods. All 96 agree with the frozen input identities and quality
flags. Sums differ from the cached all-direction values by at most **one vehicle
per day** in any checked total/category; residuals remain explicit and are not
forced to zero. Point **74925** has only a `W` record whose total reconciles with
the cached AADF. Its road/direction interpretation still needs qualification;
an opposite flow is not invented. The `C` records remain unsplit.

## National evidence and transfer limits

The [TRA tables](https://www.gov.uk/government/statistical-data-sets/road-traffic-estimates-tra)
contain exact 2021 profiles for all motor vehicles and, separately, cars/taxis,
light commercial vehicles and HGVs. They combine road classes across Great
Britain. The published indices are preserved as indices, with notes; they are
not percentages and have not been normalised into local hourly flow. They do
not supply separate motorcycle, bus/coach or HGV-axle profiles. Their 2021
COVID-19 warning and historical-revision notes remain attached.

The [SPE tables](https://www.gov.uk/government/statistical-data-sets/vehicle-speed-compliance-statistics-data-tables-spe)
provide exact-year average speed evidence in mph for motorways, national-speed-
limit single carriageways, 30 mph roads and 20 mph roads. The latest workbook
recalculates historical 2018–2023 figures using a consistent site sample; these
are current published revisions of 2021 evidence. Published codes such as `[x]`
stay unavailable, rather than becoming zero speed.

The [2021 speed release](https://www.gov.uk/government/statistics/vehicle-speed-compliance-statistics-for-great-britain-2021/vehicle-speed-compliance-statistics-for-great-britain-2021)
selects sites for free-flow conditions and excludes features that constrain
speeds. Its averages cannot establish typical speeds on a congested local road.
Our generic minor-road identity also does not establish its historic speed limit,
so a national road-type row is not automatically assigned to it.

## Continuous local data: bounded discovery, not qualified input

[National Highways WebTRIS](https://webtris.highwaysengland.co.uk/api/swagger/ui/index)
offers public station, paged report and quality endpoints. Its
[17 September 2026 notice](https://webtris.highwaysengland.co.uk/Home/News)
pauses new uploads while retaining a rolling 10-year historical window. That
notice alone does not establish completeness at a particular station.

The acquired station inventory has **303 entries** in the source area and **39**
in the core. Entries include legacy, inactive and replacement IDs; they are not
303 independent physical sensors. Current Active status was not treated as proof
of 2021 availability, and inactive IDs were retained for discovery.

Eight bounded GET requests retained metadata, sample responses and quality
responses. Four nearby M4 IDs returned no report rows on the minor-road survey
date. The aggregate core-year quality response reports `data_quality: 5`; it is
not a road-coverage measure. One request involving all core IDs returned HTTP
400 and is retained as a failed request, not as evidence of absent traffic.
Two successful report pages yielded **100 rows**, all with blank volume and
average-speed fields. Those blanks stay unavailable. The pages are incomplete;
this does not establish that every 2021 record is absent or unusable. No WebTRIS
values were imported into model inputs, and licensing/field/site compatibility
still need qualification before such use.

A [Hillingdon council record from October 2023](https://modgov.hillingdon.gov.uk/mgAi.aspx?ID=44964)
requests 24/7 traffic and speed surveys on Harmondsworth Road. It is a useful
later-year lead, not acquired 2021 evidence. No outside enquiry was sent.

## Scope, artifacts and checks

Complete survey-day support attaches to **3.57 km** of existing directly assigned
core geometry and **0.54 km** of provisional minor-road geometry: **8.63%** of
core road length in total. This is a narrower diagnostic than the earlier
**19.03% annual-input association** figure, not a reduction in that baseline.
Neither figure validates flow throughout the links or annual acoustic indicators.
Across the halo, the count-day association diagnostic is **3.75%**.

The temporal selection covers the 96 points inside the fixed source area. The
old matcher's larger donor search also uses **17 point IDs outside that selection**,
supporting **11.71 km** of directly assigned halo geometry and **zero core road
length**. Those donors' temporal evidence is explicitly unexamined here, rather
than labelled absent.

Final generations:

- `artifacts/road_temporal_sources_v1/`: original DfT API response bytes and three
  ODS workbooks, requests, retrieval timestamps, response headers, checksums and
  OGL attribution. **14 GET requests, 1.21 MB**; exact-year/point filtering,
  pagination totals and duplicate IDs were checked.
- `artifacts/road_temporal_documentation_v1/`: retained Firecrawl page content and
  primary-source records. A failed documentation URL is recorded as HTTP 404;
  it supplies no supporting authority.
- `artifacts/road_temporal_webtris_probe_v1/`: original station/report/quality
  responses, request receipts and candidate metadata, marked discovery only.
- `artifacts/road_temporal_audit_v2/`: report, local survey-day/directional evidence,
  separate national assumption candidates and inventory. Final identity:
  **`road-temporal-audit-dfd829aea92fa2ab517d`**; DfT source identity:
  `road-temporal-sources-de6e65206482af4e0200`.

All source, parent and output generations were checked by checksum. **72 tests
passed**, including no year fallback, duplicate intervals, missing hours,
unpaired directions, coordinate mismatch, preservation of missing/zero values,
national-table schema/repeats and existing linkage regressions. The final live
audit verified unchanged sources, code and parents. Artifacts remain local and
excluded from Git; the v1 audit is retained as an earlier iteration.

```powershell
.venv\Scripts\python.exe scripts\43_acquire_road_temporal_evidence.py --output artifacts/road_temporal_sources_recheck
.venv\Scripts\python.exe scripts\44_audit_road_temporal_evidence.py --output artifacts/road_temporal_audit_recheck
.venv\Scripts\python.exe -m pytest tests\test_road_temporal.py tests\test_road_input_audit.py tests\test_minor_road_linkage.py tests\test_phase2c_road.py -q
```

Use new directories. The acquisition command obtains a fresh, separate source
snapshot; the audit intentionally reads the frozen `road_temporal_sources_v1`
and other named parent generations. Code is in `src/quiet_uk/road_temporal.py`.
This does not overwrite native rasters, the matcher, explorer or launcher.

## Next delivery

**7 October follow-up:** [physical-scene inputs](PHYSICAL_SCENE_INPUTS_2026-10-07.md)
now cover the full source area, and [carriageway/direction handling](ROAD_CARRIAGEWAY_AND_DIRECTION_2026-10-07.md)
adds the previously omitted donors' official directional records, provisional
unsplit budgets and a complete withholding ledger. The next bounded step is the
road-emission assumption protocol. That [subsequent increment](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md)
is now delivered with conditional source-power cases; ground/barrier conventions
are next. The delivery paragraph below records this temporal increment's
original handoff.

Complete the bounded **terrain and building/DSM input acquisition**: fill the
64 km² surrounding terrain gap, retain local survey dates and qualify building
height/shielding evidence. The temporal audit is complete, while annual local
period flows, typical speeds and 2021/2026 geometry compatibility remain open
requirements. A later temporal increment should resolve historical counter/site
mapping and full-year quality, then evaluate any necessary class/road assumptions
against qualified evidence. The physical-scene work can proceed alongside those
requirements; an absolute-noise release cannot bypass them.
