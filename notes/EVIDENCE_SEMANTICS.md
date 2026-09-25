# Source evidence semantics, 24 September 2026

Follow-up: the [25 September provider audit](PROVIDER_ENCODING_AND_EXTRACTION_AUDIT.md)
records protocol comparisons, edge behaviour and documented nil encodings.
It does not establish a universal zero rule or calculation-domain mask; the
interpretation release and its quietness bounds remain unchanged.

The 10 m explorer now uses interpretation release `pilot-70542da395300f52dd27`
at `artifacts/source_regions_v2`, derived from `pilot-d0aa536e7c93d05b09fe`.
The parent release, all original responses and its construction code are preserved.
No provider requests or new scientific rasters were needed for this change.
This improves interpretation and traceability; it does not validate acoustic accuracy.

## What a cell means

`raw_encoding` and `status` are separate. A provider's numeric zero is not a
reported zero dB. TIFF nodata is retained separately, including when the nodata
sentinel itself is zero. The shared contract drives point records, comparison
records and exports; the UI uses its labels and explanations.

| Status | Meaning | Numeric evidence |
|---|---|---|
| `reported_model_value` | Reported source/indicator value | Original value and equal lower/upper model bounds |
| `verified_below_cutoff` | Encoding and domain evidence establish censoring | Strict upper bound; no invented point estimate |
| `unreported_unknown` | No established value or bound | Null value and bounds; `zero_code` or `tiff_nodata` retained |
| `outside_extract` | Outside the downloaded rectangle | No sampled cell; no claim about the provider's model domain |
| `outside_domain` | Independently established domain exclusion | No exposure inference |

**No real cell in this release is promoted to `verified_below_cutoff` or
`outside_domain`.** The necessary encoding/domain authority is not yet established.
Synthetic tests cover these supported states and reject unsupported zero rules.
Two censored cells cannot be ranked. Where compatible bounds are established,
A <40 and B=52.3 implies B−A >12.3 dB, not an exact difference. Strictness survives
JSON/CSV export; comparison still requires compatible source, metric and declared
period. No aircraft differences are newly enabled by this release.

## Thresholds and periods

Defra's [road metadata](https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517)
and [rail metadata](https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947)
document 40 dB Lden/day and 35 dB night reporting cutoffs. Those declarations
alone do not establish the meaning of every zero or missing raster cell.
The [airport metadata](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8)
describes minimum reporting thresholds but states actual thresholds vary by airport.

The old shared `reporting_cutoff_db` map is removed from schema-2 manifests and
comparison JSON. Each record and observation now has:

- `provider_minimum_reporting_level_db`: the documented product minimum.
- `reporting_cutoff_db` and `reporting_cutoff_status`: road/rail declaration;
  null and `airport_specific_not_established` for aircraft.
- `threshold_evidence`: metadata URL, saved path and checksum.
- `observed_min_reported_db` and `observed_min_scope`: minimum in this particular
  source/metric extract, with record ID, raw hash and bounds. It never supplies a
  ceiling on missing values. Heathrow aircraft night illustrates the distinction:
  provider minimum 35 dB, observed extract minimum 44 dB, actual cutoff unknown.
- `period_evidence`: raw declared period and status, pinned metadata, and a
  separate `assessed_period` which remains null. Retrieval/publication dates do
  not become exposure dates.

The CAA Heathrow 2021 report remains contextual evidence. Matching its airport
and product to the national raster mosaic requires further work; we have not
relabeled unspecified provider metadata as a declared 2021 aircraft period.

## Map and compatibility

The legend names the active source and metric. Beige hatching means zero-coded
unknown; blue-grey hatching means TIFF nodata. Neither colour denotes quietness.
Each source view explicitly excludes other sources. Road/rail point inspection
and the map caption flag separately reported aircraft at the same point/metric,
with a button to switch layers. Missing aircraft evidence is not absence of noise.

The national 100 m overview retains its historical provisional construction,
with a visible warning about its unverified quiet-end assumptions and a link to
the source-specific view. It is not retrospectively certified by this work.
Schema-1 reading/reproduction remains available for historical releases. Existing
saved links show the release change; old saved comparisons are not silently loaded
into the new release. Dataset versions, raw hashes and parent identity accompany
new exports. The launcher now prefers the new interpretation when present.

## Reproduce and verify

From the repository root, in the documented Python environment:

```text
python scripts/30_source_pilot.py --reinterpret artifacts/source_regions_v1 --output artifacts/source_regions_v2
python scripts/30_source_pilot.py --output artifacts/source_regions_v2 --verify --reproduce --compare-to artifacts/source_regions_v1
python -m pytest -q -rs --strict-markers
```

Use a new output directory; existing or nested destinations are rejected. The
bundle retains original construction files and pins the interpretation code under
`interpretation/`. In an extracted bundle, replay the new interpretation using
`python interpretation/scripts/30_source_pilot.py --output . --verify --reproduce`.
The parent manifest is saved as `parent-manifest.json`; all files named in it
remain unchanged. Failed/incomplete attempts are not silently resumed.

Local validation: **456 passed, 2 platform skips, 3 release checks deselected**.
All 36 display images reproduce offline. All **36 million source-indicator cells**
match the parent exactly, including masks; these are not unique geographic cells.
Live JSON/CSV checks cover Heathrow and Chilterns, numeric/zero/nodata encodings,
and null aircraft cutoffs. Browser checks confirm the Heathrow aircraft warning,
unknown rail state, source switch and updated legend in the narrow layout.
Local evidence is under `artifacts/evidence_semantics_v1` and
`artifacts/evidence_semantics_tests.xml`.

## Remaining evidence gate

This completes the bounded semantics implementation, not the entire combined
semantics/extraction phase. Next: small independently discovered WCS 1.0/2.0
matched-request comparisons; authoritative zero encoding and domain evidence
(including coastal/edge/all-zero cases); and an airport/product/period crosswalk.
These require new evidence, not inferred precision. Wider acquisition and
field-validation claims remain deferred.
