# Historical barrier asset data request - draft, 10 October 2026

**Unsent.** Prepared for a verified National Highways asset-data contact. The
bounded public catalogue search did not identify a suitable asset layer. A
contact/channel and owner authorisation to send would be needed separately.

Subject: Historical 2021 noise-barrier asset GIS for a bounded north-Heathrow study

Hello,

We are evaluating historical road-noise modelling for a small north-Heathrow /
West Drayton study. We would like to use authoritative physical inputs rather
than infer noise-barrier absence from an incomplete public map.

The area of interest is British National Grid EPSG:27700, easting 501000–513000,
northing 173000–185000, with a central 2 × 2 km study area at easting
506000–508000, northing 178000–180000. The reference period is calendar year 2021.

Could you advise whether a reusable extract exists of the noise barriers, solid
walls and relevant earth bunds serving roads in this area during 2021? Useful
fields would include:

- Asset identifiers, centreline/footprint geometry and horizontal CRS.
- Barrier base and top elevations or height, with vertical datum, height
  definition, measurement/source date and any stated positional/height accuracy.
- Material, continuity/gaps, acoustic absorption/transmission classifications
  and whether these are measured, design assumptions or unknown.
- Installation, removal/replacement dates and relevant survey/as-built evidence,
  particularly where a scheme was proposed or under construction during 2021.
- Dataset scope/completeness, version history, licence and attribution required
  for analysis, derived outputs and potential product redistribution.

A bounded GeoPackage, GeoJSON, shapefile or CSV/WKT extract would be sufficient.
If only planned-scheme documents are available, please identify which features
can be established as physically present in 2021 and which remain proposals.
We would also appreciate the correct data steward if this information is held
elsewhere.

Thank you.

## Evidence behind the request

The [National Highways open-data hub](https://opendata.nationalhighways.co.uk/)
was checked through its official public organisation and content-group searches
for `noise OR barrier`. Four organisation and three group results were returned,
without a qualified historical barrier asset layer. This search is not proof that
such records do not exist. Query JSON, retrieval receipts and metadata are held in
`artifacts/ground_cover_sources_v1`.

The retained year-end 2021 OpenStreetMap query has 1,206 barrier-related ways,
including ordinary fences, hedges and walls, and no explicit `wall=noise_barrier`
ways. Those community records do not establish a complete barrier inventory,
physical 2021 presence or acoustic properties. The
[input qualification report](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md)
records the counts and exclusions.
