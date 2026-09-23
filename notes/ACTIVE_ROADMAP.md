# Quiet UK active roadmap

Updated 23 September 2026. This supersedes the delivery ordering in older reviews;
those documents remain historical evidence.

**Product:** an elegant, general-purpose map for understanding quieter and louder
places, supported by traceable evidence. Homes, walks and research are applications
of the product, not separate product identities.

**Completed in the explorer phase:** local England-wide road/rail display, labelled
basemap, pan/zoom, place/postcode and coordinate search, exact cell inspection,
aviation and uncertainty explanations, versioned display products, location and
dataset downloads, responsive design and local view links. Research construction
requirements are defined in `RESEARCH_DATA_CONTRACT.md`.

**Completed next increment:** aircraft-presence hatching enabled by default,
aircraft-only lower-bound display, source-view controls with saved-view support,
numeric aircraft inspection, explicit machine-readable unavailable metrics, and
a duration-aware temporal calculation library with contrasting-history tests.
See `SOURCE_VIEWS_AND_TEMPORAL_FOUNDATION.md`. This adds neither a validated
all-source total nor actual national event/time-series data.

**Current boundary:** this is a local exploratory release of historical data.
No public hosting, full UK noise coverage, validated below-cutoff ranking or
study-ready exposure release is claimed. The source checkpoint includes the app,
tests, launcher and documentation; generated geospatial artifacts and local
environments are excluded from Git. Publishing source does not deploy the app or
back up the local data releases.

**Completed source-linked pilot:** four 2 × 2 km areas now expose original 10 m
road, rail and aircraft grids separately, with Lden/day/night controls, exact point
comparisons and downloadable acquisition evidence. All 36 layers pass native-grid
and request-chain checks; their display derivatives reproduce offline. Road/rail
reference 2021; aircraft's period remains unspecified. See
`SOURCE_LINKED_PILOT.md` for the delivered release, provider issues and verification.
This completes the bounded canary implementation, not the national/research release
gate: aircraft comparability and independent validation remain outstanding.

**Completed regional increment:** the four areas now span 10 × 10 km each
(400 km²), with place/coordinate search and explicit outside-coverage handling.
All 1.44 million original sample values/masks are unchanged in the expanded data.
A newly installed isolated environment passes the full suite and reproduces all
36 maps from the portable evidence bundle offline. This closes the bounded
Windows environment-reproduction check; broader platform support and independent
scientific validation remain open. See `SOURCE_REGIONAL_RELEASE.md`.

| Next phase | Outcome | Exit condition |
|---|---|---|
| Source-linked data canary and release preparation | Establish a traceable construction path under the research contract | Original evidence recovered or new acquisition linked to exact provider products, periods and snapshots; clean-environment reproduction and canary scientific QA pass |
| User-tested explorer beta | Improve comparison and exploration based on observed needs | Fresh users find a place, distinguish unknown evidence, compare 2–3 locations and share a view without coaching; add saved places only if useful |
| Deployable England release | Stable read-only distribution, attribution, operating budget and rollback | Verified compatible web/analytical generations; public-serving hardening and provider usage arrangements; user reviews a concrete deployment |
| UK coverage | Country adapters with honest comparability | Scotland, Wales and Northern Ireland data inventoried, licensed, transformed and validated with country-specific metric/domain metadata |
| Independently validated quietness improvements | Better distinctions where current maps censor the quiet end | Predefined external validation criteria pass; uncertainty and failures published; existing experimental road model corrected before consideration |

The next data phase does not require redesigning the explorer or building a large
platform. Start with bounded canaries. Introduce day/night/source separation,
research area/route exports, access or other contexts only when the relevant data
contract and user question justify them.

The clarified default product view should include all compatible supported
transport sources, with optional road/rail/aircraft controls. Heathrow is a required
validation case: a road/rail-only display must not imply low overall exposure where
aircraft evidence is present. Before implementation, verify metric/period
compatibility and obtain separate road and rail products. The data canary must
also inventory available temporal/event evidence, following the multiple-metric
contract: distinguish what supports long-term exposure, background sound and loud
events, and identify unavailable metrics. Do not infer event histories from annual
rasters or delay a bounded release until every metric has national coverage.

Each task must name its user behaviour or evidence gap, bounded deliverable and
completion check. Demonstrate the product regularly. Do not use additional models,
audit documents or infrastructure as substitutes for a working map.
