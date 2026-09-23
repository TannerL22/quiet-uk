# Quiet UK research data contract

Adopted 21 September 2026. This is the project's construction and release policy,
not a claim of external certification. The product remains a general-purpose
geographic explorer. Research exports and consumer views use the same explicitly
identified evidence; an attractive map must never confer scientific credibility
that the inputs have not earned.

**Current status: provisional historical baseline.** The existing England data
has substantial structural QA and hash-checked catalogue access. Original source
acquisition linkage and reference periods remain unresolved. It must not be
described as independently validated, measured ambient noise, or a research-ready
exposure dataset. The explorer displays this status and carries it into downloads.

## Construction invariants

1. Record provider, product/version, reference period, retrieval timestamp,
   licence/attribution, exact request, response identity/checksum, metric,
   receptor height, units, coordinate system, resolution, model domain and
   censoring threshold for each source. Retrieval dates are not reference years.
2. Preserve immutable source snapshots when permitted, or durable versioned
   retrieval references plus hashes and an explicit availability limitation.
   Record all processing parameters, code identity, dependency/native runtime
   versions and output hashes. Never retrofit current settings as historical facts.
3. Distinguish reported, below reporting threshold, outside model domain,
   missing/error, withheld and outside land. Missing evidence is never zero noise.
4. Use source-appropriate metrics and censoring rules. Do not combine different
   time periods or incompatible indicators without documenting and evaluating the
   assumption. Lden evening/night penalties do not become a visit-time reading.
5. Combine equivalent acoustic contributions in energy space. Define spatial
   and temporal aggregation before calculation, including support, weights and
   treatment of unavailable/censored observations. Never average raster colours.
6. Keep original analytical values separate from visual resampling and rounded
   presentation. A map must not imply greater spatial or numeric precision than
   its evidence. Exact export precision is for reproducibility, not claimed accuracy.
7. Check input integrity before processing and output integrity before publication.
   Publish new generations atomically; do not modify frozen scientific releases.
   Make schema, algorithm and provenance changes explicit versions.
8. Use independent evidence and geographically appropriate holdouts for acoustic
   accuracy claims. Unit tests, a structurally valid raster and agreement with the
   same model used for training are not independent validation.

## Multiple metrics and temporal evidence

Product clarification, 21 September 2026: the backend must support different
research and product questions without reducing its evidence to the map's chosen
colour metric. Continuous background sound and intermittent loud events must be
distinguishable wherever the source evidence permits. This is a construction
requirement; the current historical four-band release does not provide event data.

Preserve the finest licensed source evidence available, with immutable originals
and reproducible derivations. Keep road, rail and aircraft contributions separately
identified before deriving compatible combinations. Accept annual rasters, period
summaries, monitoring time series and event records as different evidence types;
do not pretend an annual raster contains recoverable event histories.

Each observation or derived metric must identify its location/spatial support,
source(s), metric definition, frequency and applicable time weighting, units,
reference dates, assessment hours/time zone, averaging duration, receptor height
and free-field/facade convention, measured/modelled/derived status, provenance,
coverage/censoring and uncertainty status. Unknown uncertainty is not zero.
Event and monitoring data additionally need temporal resolution, observed duration,
gaps, detection thresholds and event-detection rules. Preserve frequency-band
information where supplied; an A-weighted scalar cannot recover a spectrum.

Supported metric families should include, when evidence allows:

- Energy-equivalent exposure over explicit periods (LAeq,T), plus separately
  defined day/evening/night indicators such as Lden and Lnight.
- Background and distribution descriptors, for example LA90,T and LA50,T;
  percentiles cannot be reconstructed from LAeq or added across sources.
- Event maximum levels with their time weighting, event sound exposure (SEL),
  counts above stated thresholds, and event timing/duration.
- Time below/above defined thresholds and lengths of quiet intervals, only where
  sufficient temporal observations or validated modelling support them.

Do not sum maxima, percentiles, overlapping event counts or incompatible periods.
Total-sound distributions and quiet intervals generally require aligned temporal
evidence, including overlap between sources. A combined transport indicator still
excludes unrepresented environmental sound sources. Report unavailable metrics as
unavailable; additional schema fields cannot manufacture additional evidence.

The explorer may choose one clearly named default metric and offer source/time/
metric controls. Research exports retain the available evidence and definitions,
independently of that presentation. Any future quietness score is a versioned
derived interpretation with explicit assumptions, never the canonical observation.

Use contrasting synthetic temporal histories as acceptance examples. Assuming
constant A-weighted levels at one receiver, 40 dB for 24 hours gives LAeq,24h =
40 dB. A day with 30 dB for 86,390 seconds and 120 dB for 10 seconds gives about
80.635 dB LAeq,24h by energy averaging. This describes energy exposure, not a
constant experienced level; a maximum of 120 dB alone would not specify the
event's energy. Histories with equal LAeq but different event timing/counts must
retain those distinctions when the source contains them.

Primary metric references:
- [CAA CAP 2598, Noise Metrics Guidance (2025)](https://www.caa.co.uk/data-and-publications/publications/documents/content/cap2598/).
- [Environmental Noise Directive, Annex I definitions and supplementary indicators](https://eur-lex.europa.eu/legal-content/EN/ALL/?uri=CELEX%3A32002L0049).
  This is a definitions reference, not a claim of regulatory compliance.

## Current analytical fields

| Field | Units and meaning | What it cannot establish |
|---|---|---|
| `road_rail_upper_db` | Conservative road + rail upper bound under the configured censoring rules, energy-aggregated to a 100 m cell | Total noise, aviation exposure, exact below-cutoff levels |
| `combined_reported_lower_db` | Lower bound from reported road, rail and airport energy | Expected ambient level or a valid interval when paired with the road/rail-only upper band |
| `airport_reported_lower_db` | Lower bound from reported airport energy | Full aircraft exposure outside reported coverage |
| `airport_reported_fraction` | Unitless fraction of fine cells with a reported airport value | Aircraft event frequency, aircraft-free time or proof of silence when zero |

Every value has a qualification, value status, source dependencies and explanation.
Withheld values remain null in ordinary downloads. A null lower bound can represent
legitimate unreported/censored energy and must not be treated as a measured 0 dB.
The roughly 43.0103 dB two-source floor means many cells cannot be distinguished;
it is not a true ambient noise estimate or a ranking among quiet places.

## Explorer release and export contract

`GET /api/dataset` returns a machine-readable record with display contract version,
release ID, source catalogue build ID/database hash, publisher hash, input hashes,
output hashes, resolution, CRS, metric evidence, reference-period states, field
dictionary, display method, attribution and limitations. `created_at_utc` describes
construction of the display product, never the age of environmental conditions.

`GET /api/location?lon=…&lat=…` returns the exact qualified stored cell. It includes
the requested coordinate, transformed coordinate, cell centre and bounds, source
tile ID/checksum, all four band values and statuses, source-grid evidence, catalogue
and display identities, historical-linkage status and retrieval time. The cell
outline uses WGS84 for display; analytical ownership remains on the BNG grid.
The existing west-inclusive/east-exclusive, south-exclusive/north-inclusive
ownership convention is preserved. Coordinates and searches are not server-logged.

The location download is a reproducible inspection record, not a published study
dataset. The dataset record supplies citation ingredients, not a fabricated DOI
or invented provider reference year. Cite the release ID, field definition,
retrieval date and limitations together with the provider documentation.

The two generated GeoTIFFs are **display products only**. Qualified upper bounds
are converted to relative energy, with zero reserved for nodata; overview means
exclude unavailable/outside pixels. Categorical quality uses maximum resampling
so withheld support stays visibly distinct. At low zoom, mixed support is a visual
summary, not an exposure estimate for the whole region. Maximum quality state can
intentionally over-emphasise a small withheld area. Point inspection always returns
the original analytical cell rather than a display-pixel value.

## Requirements for a study-ready release

The next data release must meet these gates before it is promoted:

| Gate | Required evidence |
|---|---|
| Traceable acquisition | Every included source is linked to provider product, reference period, request/snapshot and checksum; unresolved sources are explicitly excluded or separately classified |
| Reproducible transformation | A clean documented environment reconstructs a canary and release outputs within predefined numeric tolerances; software, native libraries and configuration are identified |
| Coverage and censoring | Domain, source states and exclusions reconcile to the authoritative denominator; coverage gaps cannot become quiet cells |
| Scientific QA | Acoustic identities, spatial support, resolution, edge effects, uncertainty assumptions and source compatibility are documented and tested |
| Independent validation | Suitably matched measurements or independent reference evidence, with geographic holdouts, error/ranking/calibration results and failure cases; criteria specified before final evaluation |
| Fitness for a named analysis | Chosen exposure metric, period and spatial/temporal support match the study question, or mismatches and sensitivity analyses are explicit |
| Distribution | Versioned analytical products, data dictionary, licence, citation, changelog, checksums and a small reproducible analysis example accompany the release |

A release can be structurally sound and reproducible without being accurate enough
for a particular study. “Research quality” is a set of evidenced properties and
fitness decisions, not a badge inferred from test count.

## Area averages and joins

There is deliberately no generic “average noise” export in this phase. For a future
area or route API, specify the estimand first. An energy-equivalent area level is
`10 log10(sum(w_i * 10^(L_i/10)) / sum(w_i))` for defined comparable levels and
weights. A mean of dB values is a different statistical descriptor, not an
energy-equivalent acoustic level. Neither automatically estimates personal exposure.

An area request must return its geometry/CRS, cell-intersection weighting, included
and excluded support, censoring treatment, source/metric/time-period identity,
and uncertainty or bounds. Distinguish land-area, dwelling/population, route-length
and time-at-location weighting. Do not silently drop censored or unavailable cells,
assign postcode centroids to every home without disclosure, or substitute a 100 m
cell for a facade measurement.

For house-price or stress research, temporal alignment, address/geocode precision,
spatial autocorrelation, selection effects and confounding need explicit treatment.
For example, transport access and urbanity may affect both mapped noise and the
outcome. An ecological area-level association is not evidence of an individual's
exposure or a causal effect. These are requirements for future analysis design,
not claims that this project has performed such studies.

## Immediate evidence programme

Timebox recovery of original acquisition records. If historical linkage cannot
be demonstrated, create a new traceable canary/release rather than repeatedly
auditing the same missing records. Keep the historical baseline immutable. Validate
urban, rural, coastal, aviation-affected, sub-threshold and domain-edge examples.
Agree measurement and temporal matching before fieldwork: a brief sound recording
cannot directly validate annual Lden. Expand nationally only after the canary's
construction and evidence gates pass.
