# Aircraft data and combined-transport readiness — 3 October 2026

Defra's owner-reported acknowledgement leaves useful work available. This
increment qualifies the Heathrow lead, measures joint native support, and
prepares a separate aircraft GIS enquiry. It installs no new noise values and
does not fill unknown cells.

## Aircraft qualification

The operator's [reports page](https://www.heathrow.com/company/local-community/noise/data/reports)
and the complete [CAA ERCD 2201 report for Heathrow 2021](https://www.heathrow.com/content/dam/heathrow/web/common/documents/company/local-community/noise/reports-and-statistics/reports/noise-action-plan-contours/LHR_2021_Summer_and_NAP_Contours.pdf)
were retrieved through the secondary Firecrawl connection. The reports page
links annual/summer publications and community monitoring. The inspected links
and targeted GIS searches did not establish an official reusable continuous
Heathrow receiver grid; this is a bounded search, not proof none exists.

The 2021 report uses ANCON 2.4, informed by radar and screened aircraft noise
measurements. Its annual Lday/Lden contours run from 55–75 dB in 5 dB steps;
Lnight runs from 50–70 dB. Those published contours cannot recover exact
continuous levels or establish the omitted quiet end.

**Period correction:** section 1.5's March 2021–March 2022 footnote refers to the
6.5-hour night-quota assessment; Table 1 explicitly distinguishes that season
pair. It must not be assigned to every annual indicator. The earlier research
table has been corrected. Annual Lday/Lnight/Lden are labelled 2021; linkage to
the installed Defra aircraft mosaic, exact dates and receiver convention remain
unqualified.

The report permits attributed contextual reuse of its text. Its mapping and
population material include separate copyright notices. Neither report-text
permission nor public availability establishes redistribution rights for the
underlying GIS/model. Request the raw data's own licence.

**Decision:** retain Heathrow as an authoritative aircraft lead. Request original
annual receiver outputs and provenance before considering integration. Do not
digitise contour colours into precise point levels or transfer their year,
threshold or licence to the installed mosaic.

## What a combined transport view can support today

The offline audit verified all **450 native raster hashes**, examined the
nonoverlapping owned cores, and required matching native grids within each
source/metric group. It excluded request padding. Each metric covers 50 million
10 m cells across **5,000 km²** in release `pilot-ad6d31f2da1b0a754135`.

| Indicator | Road reported | Rail reported | Aircraft reported | All three reported | No sources reported |
|---|---:|---:|---:|---:|---:|
| Lden | 77.86% | 22.43% | 5.25% | **1.73%** | 19.78% |
| Lday | 73.35% | 15.61% | 4.55% | **0.89%** | 24.27% |
| Lnight | 61.75% | 15.54% | 2.43% | **0.52%** | 34.62% |

Reported means an unmasked, finite positive provider value accepted by the
current encoding contract. It does not mean measured ambient sound. The eight
disjoint source combinations and per-core counts are preserved in
`artifacts/transport_readiness_v2/report.json`, with the manifest, source and code
hashes plus runtime versions. Zero codes and masked cells remain unknown contributions.

This makes the main dependency concrete: summing only complete numeric triplets
would produce a combined value in a very small part of the map. Even those
triplets are not qualified for summation today: road/rail identify calendar 2021,
while the installed aircraft product's period remains unspecified. Airport scope,
metric/receiver compatibility and the mosaic's construction also need evidence.

The audit exports no summed dB, lower bound, prediction or uncertainty interval.
It diagnoses data availability; it does not certify future product compatibility.

## Combination implementation requirements

Prepare the future calculation around these acceptance cases:

- Equivalent source contributions at the same receiver, with matched period,
  metric definition, assessment hours/time zone and receiver convention, combine
  in energy space: `10 log10(sum(10^(Li/10)))`. Two compatible 50 dB
  contributions give about 53.01 dB, not 100 dB.
- An unknown source contribution makes an exact combined transport result
  unavailable. Do not substitute zero dB or zero acoustic energy. Verified
  censoring may support a separately labelled bound after its domain and strict
  inequality semantics are established.
- A missing year, period mismatch, incompatible metric/support, duplicate source
  contribution or unresolved aircraft-product linkage must reject an exact total.
- Label any qualified result **combined transport noise** with its annual
  indicator and year. It cannot promise present-day all-inclusive sound or
  account for unrepresented construction, neighbours, industry and natural sound.

The current increment implements and tests the support audit, not the future
calculation or a new map total. The original analytical release remains frozen.

## Prepared aircraft GIS enquiry — draft, not sent

Recipient: Heathrow's responsible noise-data owner, with CAA Environmental
Research and Consultancy Department referral if appropriate. The report names
ERCD for publication-content enquiries. A current electronic contact route must
be verified before sending; no recipient email is asserted here.

Subject: Heathrow annual aircraft noise — original GIS outputs and reuse terms

Hello,

I am developing Quiet UK, an evidence-based noise map using original England
Round 4 road, rail and airport grids. We would like to qualify Heathrow aircraft
data independently and avoid treating unreported cells as quiet.

Could you advise whether the original Heathrow 2021 annual Lden, Lday and Lnight
receiver outputs are available as a georeferenced numeric grid, preferably
including calculated values below the published contours? A representative
extract around Heathrow would be useful if a full transfer is impractical.

Please include or identify:

1. Product/model version, exact assessment dates, metric definitions and
   assessment hours/time zone. Please distinguish annual indicators from summer
   LAeq and the 6.5-hour night-quota period.
2. CRS, grid/receiver support, receiver height, free-field/facade convention,
   calculation domain and zero/nodata/reporting-cutoff definitions.
3. Reuse and redistribution licence, attribution and any separate third-party
   rights applying to the numeric outputs and GIS geometries.
4. Whether these outputs correspond to the Heathrow component of Defra's
   England Round 4 airport dataset (`dac9cba4-abe7-43bd-b8e9-8a83da52edd8`),
   and how airport/source overlaps were handled in that mosaic.

If continuous outputs cannot be shared, are original vector contours available
with level attributes, assessment dates, CRS and reuse terms? Please also advise
whether an unthresholded extract or a suitable monitored source can be provided
for checking lower-level model results.

We retain original files, metadata and checksums and keep annual transport
indicators separate from short recorded sound. We would appreciate a referral to
the responsible data owner if this enquiry belongs elsewhere.

Thank you,
Tanner Looney

## Reproduction and next work

```powershell
.venv\Scripts\python.exe -m pytest tests\test_transport_readiness.py -q
.venv\Scripts\python.exe scripts\40_audit_transport_readiness.py --output artifacts/transport_readiness_v2
```

Use a new output directory on subsequent runs. The audit rejects unexpected
encodings, altered files, overlapping owned cores and misaligned native grids.
Tests cover all eight support patterns, masked nodata, missing sources,
unexpected values, frozen input identity and a half-cell grid shift.
All **11 tests passed**. The final audit verified 450 rasters across 150 metric
cores; the initial v1 inventory is retained, with identical support counts, while
v2 additionally records code/runtime identity and checks the full metric set.

Independent work can now proceed in this order:

1. Qualify the contact route and obtain the aircraft GIS outputs using the draft
   above; sending this separate enquiry requires the owner's instruction.
2. **Completed:** the [bounded road-input audit](ROAD_MODEL_INPUT_AUDIT_2026-10-03.md)
   matches source-year traffic, road geometry, terrain and building/barrier
   availability for a small study area. Follow its input-improvement order before
   fitting a new model.
3. Prepare a place summary that keeps the three contributions visible together
   and shows explicit unavailable evidence. Add a combined indicator only when
   its compatibility and missing-source requirements are satisfied.

Defra's unthresholded data/domain reply can improve these steps when it arrives;
it need not stop the input audit or evidence-summary preparation. Existing
reported-cell holdouts remain feasibility evidence, not quiet-end validation.
