# Provider encoding and extraction audit — 25 September 2026

**Decision: do not assign additional quietness bounds.** Published reporting
cutoffs are established; a universal zero-as-censoring rule and a spatially
explicit calculation-domain mask are not established by the sources inspected.
Small, interior extractions agree where the two interfaces work. Coverage-edge
behaviour and unavailable cross-protocol comparisons remain explicit exceptions.
The app and interpretation release `pilot-70542da395300f52dd27` are unchanged.

## What the provider establishes

The [Defra explanation of the 2022 maps](https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps)
describes modelled rather than measured exposure, inclusion of all public roads
and all railways in England, a 10 m receptor grid at 4 m height, and minimum
reported levels of 40 dB for Lden/day and 35 dB for night. Those statements
describe sources, receptors and reporting; they do not define the meaning of
every nonpositive/missing raster sample. They also do not make the maps a
measurement of present-day sound or of every sound source.

The [road](https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517)
and [rail](https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947)
dataset metadata support those reporting cutoffs. The
[airport metadata](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8)
states that actual airport reporting thresholds vary. Its unspecified reference
period still cannot establish compatibility with the road/rail 2021 period.

The audit retains those four pages, six GetCapabilities responses and 18
DescribeCoverage responses, with exact HTTP requests, dates and SHA-256 hashes.
The service descriptions and TIFFs establish the following **encoding facts**:

| Evidence | Established fact | Interpretation limit |
|---|---|---|
| Road/rail WCS 2.0 descriptions | `-96` is declared nil, with OGC reason `unknown` | Not a below-cutoff ceiling |
| Road/rail TIFFs | TIFF nodata is `-96`; numeric zero is distinct and unmasked | Zero's acoustic/domain meaning is not stated by the reviewed evidence |
| Aircraft TIFFs | Nodata is approximately `3.4e38`; missing samples use that encoding in these probes | Does not distinguish censoring, airport coverage gaps or other omissions |
| Aircraft WCS 2.0 descriptions | Lnight declares the same nil sentinel with reason `unknown`; Lden/day range records are empty in this capture | Metadata completeness varies by metric; do not generalise one description |
| Rectified-grid envelopes | Published rectangles and 10 m grid phase can be read explicitly | Rectangular storage extent is not a mask of valid calculation receptors |

Road/rail envelopes are `[82645, 5335, 655995, 657605]`; aircraft's is
`[333485, 93815, 594465, 574285]`, in EPSG:27700, ordered west/south/east/north.
Both Cardiff probes lie inside the road/rail rectangle but return entirely
masked nodata. That observed result demonstrates why an envelope test alone
cannot certify a valid model receptor. It does not supply an England-wide domain
mask. An administrative land boundary would not supply that missing evidence
either: calculation limits and omitted receptor classes can differ from it.

Several WCS range records label units `W.m-2.Sr-1`, inconsistent with the acoustic
dataset documentation. Preserve and flag that metadata defect; do not convert
the samples as radiance. Dataset documentation remains the basis for the dB
interpretation. No authoritative zero-encoding definition or calculation-domain
mask was located in the inspected documentation/services or targeted public
searches. This is an unresolved question, not proof such documentation does not
exist. Other countries' mapping reports cannot certify these England rasters.

## Extraction experiment and results

`scripts/31_audit_provider.py` independently discovers the identifiers advertised
by WCS 1.0.0 and 2.0.1. No namespace is silently repaired. WCS 1.0 requests a
20 × 20 grid over a 200 m square; WCS 2.0 uses native E/N subsets without scaling.
The comparator checks the actual CRS, shape, cell-edge bounds, transform, dtype,
nodata, masks and every numeric sample, with no numeric tolerance or interpolation.
HTTP 200 XML exceptions are failures, not TIFFs or successful comparisons.

The pinned recipe contains **28 pairs / 56 GetCoverage requests**, at most
22,400 requested source-indicator cells. It covers all nine source/metric
combinations. Twenty reference windows were selected deterministically from
the original regional release: greatest positive-value range, most balanced
reported/unreported transition, and fully unreported Lden windows where present.
Additional probes cover Chilterns, Cardiff and rectangles crossing the advertised
western edge. These are deliberately chosen stress cases, not a representative
national sample. Reference crops are labelled derivatives with original hashes
and exact integer crop windows; HTTP responses are retained byte-for-byte.

| Source | Exact protocol matches | Unavailable comparisons | Grid mismatches |
|---|---:|---:|---:|
| Road | 6 | 2 | 1 |
| Rail | 9 | 0 | 1 |
| Aircraft | 0 | 9 | 0 |
| Total | **15** | **11** | **2** |

**31 successful responses also match the corresponding existing regional crop
exactly** (10 road, 14 rail, 7 aircraft); nine reference comparisons are unavailable.
There are no observed interior value or mask disagreements among these successful
comparisons. The two protocol paths use the same provider backend, and the
earlier regional captures use that provider too. This establishes limited
extraction consistency, not independent acoustic accuracy or zero semantics.

Exceptions and their implications:

- **Road Lnight WCS 1.0:** the advertised `wksp…` identifier is rejected for both
  DescribeCoverage and GetCoverage. Its two WCS 2.0 windows match the earlier
  release; cross-protocol equivalence remains unverified for this metric.
- **Aircraft WCS 1.0:** DescribeCoverage names native format `GeoTIFF` but lists
  no supported output formats, and GetCoverage rejects `GeoTIFF`. Two separately
  retained follow-up probes also reject `image/tiff` and
  `image/tiff;application=geotiff`, which WCS 2.0 advertises. This establishes
  failure of the tested formats, not impossibility of every WCS 1.0 request.
  WCS 2.0 matches the seven existing reference crops, but there is no successful
  aircraft cross-protocol comparison. The aircraft edge request also clips to
  20 × 10; its pair is classified unavailable because WCS 1.0 failed first.
- **Road/rail western edge:** both interfaces clip the requested 200 m width
  to 100 m. WCS 1.0 retains 20 columns, producing **5 m horizontal pixels**;
  WCS 2.0 retains native 10 m pixels and returns **10 columns**. Neither matches
  the requested rectangle. These probes contain nodata; they expose grid
  behaviour, not differences in positive acoustic values. The production
  `inspect_raster` guard already rejects both responses; new regressions verify
  that rejection using the real captures.

Expansion must intersect planned requests with verified raster geometry and
validate every returned grid. Never reinterpret clipped or rescaled output as
the requested native grid, pad it with a quiet value, or report failed pairs as
equivalence passes. No existing release was rebuilt during this audit.

## Reproduction and retained evidence

The complete sealed audit is in `tests/fixtures/provider_audit.zip` (about 223 kB
compressed; approximately 15 MB expanded, mainly padded derivative TIFF blocks).
It preserves 191 indexed files plus its manifest. The manifest SHA-256 is
`4bdddbbebde9ee4344894c6ab848fdf60f0e74e27b046f0b5cf4e5559b5bdf23`.
`tests/fixtures/provider_format_probe.zip` retains the two supplemental requests,
responses, comparisons and an index linked to that manifest.

From a clean checkout and installed documented Python environment, these commands
reproduce the report offline, including grid/value/mask comparisons:

```text
python -m zipfile -e tests/fixtures/provider_audit.zip artifacts/provider_audit_fixture
python scripts/31_audit_provider.py --output artifacts/provider_audit_fixture
python -m pytest -q tests/test_provider_audit.py
```

The default CLI only verifies saved evidence. A fresh live audit requires
`--acquire --output artifacts/provider_audit_NEW --reference artifacts/source_regions_v1`;
it needs the regional reference release locally and permits at most 60 coverage
requests. Live provider data can change. A sealed destination cannot be reused.
Captured failures remain failures on resume. The source snapshot under
`construction/` records the acquisition implementation; the current verifier also
checks request URLs against independently discovered identifiers and the recipe.

Tests cover real evidence replay with networking disabled, one-cell value and
mask changes, nodata changes, grid shifts, HTTP-success XML failures, changed
capture bytes, a request/recipe mismatch, and production edge rejection. These
checks verify the audit mechanics; they do not create a scientific validation claim.

Local validation: **469 passed, two Windows platform skips, three opt-in release
checks deselected**. The 13 new audit regressions are included in that default
suite. Test output is retained locally as `artifacts/provider_audit_tests.xml`.

Contains Defra public-sector information under the
[Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Road/rail metadata attributes 2023; airport metadata attributes 2024. Original
attribution and licence text remain in the captured provider pages.

## Remaining authority gate

Use the [prepared provider questions](PROVIDER_CLARIFICATION_DRAFT.md) to obtain a
citable encoding specification, product/version-specific calculation domain, and
airport threshold/period crosswalk. No message has been sent. A future bounded
interpretation release may introduce a censoring bound only when the encoding
rule, actual cutoff, rounding/inequality semantics and domain membership are
established for that source/metric/version. TIFF nodata does not become a quiet
bound merely because a separate zero rule is confirmed. Keep unknowns and their
raw encodings in exports and in the map until those conditions are met.
