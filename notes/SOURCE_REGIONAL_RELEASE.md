# 10 m regional explorer

Delivered 23 September 2026. Open the normal launcher, then **10 m explorer**,
or <http://127.0.0.1:8766/pilot>.

## User-facing change

Each original 2 × 2 km sample is now a 10 × 10 km area around the same center:
Heathrow, Didcot, Oxford and the Chilterns. The four non-overlapping squares cover
400 km², 25 times the original 16 km². This is still regional coverage, not a new
national 10 m product.

Search for a town, postcode or `latitude, longitude`. A successful search checks
the native data bounds and selects the appropriate area automatically. Places
outside the detailed coverage are explicitly labelled; the England-map link
retains the selected coordinates. Place searches use the existing throttled,
cached Nominatim service. Coordinates stay local, and no autocomplete requests
are sent. Source and day/night controls and the nine-indicator point comparison
remain available. Saved view URLs retain their dataset identity.

The launcher serves `artifacts/source_regions_v1` when its completed manifest is
present; otherwise it falls back to the original small pilot. Published originals
are retained. A checksum failure in the selected release fails startup rather
than silently choosing different evidence.

## Data construction

Release **`pilot-d0aa536e7c93d05b09fe`** contains 36 accepted native GeoTIFFs,
each 1000 × 1000 cells. The acquisition recipe is pinned before downloading;
changing a recipe cannot resume into the same output directory. Requests are
limited to four areas and 36 million source-indicator cells. WCS 1.0 dimensions
follow the chosen extent; WCS 2.0 uses native cell-edge subsets without scaling.
The original source-grid phase, nodata and values are preserved.

The planning report's 144 MB is explicitly the hypothetical float32 array size,
not a download/storage promise. Actual native TIFFs total 291,473,160 bytes;
24 files are float32 and 12 float64, with provider TIFF storage retained. The
complete release occupies about 295 MB; its portable evidence ZIP is 26,884,231
bytes (26.9 MB). Verification keeps an additional extracted copy locally.

Defra now sometimes returns client-rendered HTML with metadata in its embedded
dataset JSON. The parser supports that and the original rendered Period field,
checks the dataset identity, rejects malformed/reversed periods and rejects
conflicting HTML/JSON dates. Neither format permits substituting publication or
creation dates for the modelling period.

Road and rail remain **2021**. The [official airport metadata](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8)
still leaves its period unspecified, so aircraft is not numerically combined
with the other sources. Independent Heathrow reports do not prove the period of
this exact Defra raster. Missing values and values below reporting cutoffs are
not assigned synthetic quietness levels. No background sound, event count, peak
or quiet-interval evidence was introduced.

## Completed verification

- **414 tests passed, 2 existing platform skips**, using a new isolated Windows
  CPython 3.14.2 environment (`.venv-regional-check`). All 35 dependency versions
  match the existing lock; `pip check` passes. Installation used cached official
  wheels through pip rather than inheriting development site-packages.
- All **36 raster grids and acquisition chains** and **177 pinned files** verified.
- Every one of the original **1,440,000 source-indicator cells** compared exactly
  with its location in the expanded rasters: **zero changed values or masks**.
  The comparison rejects missing layers, incompatible periods/identifiers,
  differing grid phase or encoding, incomplete overlap, and altered values.
- All **36 display PNGs reproduced byte-for-byte**, offline, from an extracted
  ZIP using its own saved construction code and the freshly installed environment.
  This checks software/data reproducibility on this Windows platform; it does not
  constitute independent acoustic validation or cross-platform equivalence.
- Targeted tests cover the new bounded recipes, recipe mismatch before network
  access, larger grids, hydrated provider metadata, coverage lookup, polar inputs,
  and deliberately shifted/changed/incompatible overlap comparisons.
- Browser checks passed for Watlington place search, automatic area selection,
  local Oxford coordinate search outside the original square, source/day/night
  switching, honest outside-coverage results, saved point links and a 390 px phone
  layout without horizontal overflow. No browser errors were observed. A live
  nine-indicator point request took 0.263 s and a complete HTTP ZIP download and
  integrity check took 2.821 s on this machine (single smoke checks, not load tests).

Evidence: `artifacts/source_regions_verification_v1/verification.json`,
`overlap.json`, and `source-regions-evidence.zip`. The live release and the original
pilot have not been overwritten by verification.

```powershell
# Verify current release and reproduce its display offline.
.\.venv-regional-check\Scripts\python.exe scripts/30_source_pilot.py --output artifacts/source_regions_v1 --verify --reproduce --compare-to artifacts/source_pilot_v1

# Inspect the bounded recipe without contacting Defra.
.\.venv\Scripts\python.exe scripts/30_source_pilot.py --extent-km 10 --plan

# A new acquisition must use a new destination.
.\.venv\Scripts\python.exe scripts/30_source_pilot.py --extent-km 10 --output artifacts/source_regions_v2
```

## Next sensible boundary

Use these larger areas to evaluate whether source switching, uncertainty and
day/night comparisons answer real questions. Before national expansion, plan
tiled storage/serving and acquisition costs using the measured provider file
sizes, resolve airport period/coverage semantics, and obtain independent validation
data. A 10 m pixel still cannot establish which unreported location is quieter.
