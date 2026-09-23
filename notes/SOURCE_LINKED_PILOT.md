# Source-linked 10 m pilot

Completed 22 September 2026. This is a working, bounded data/product milestone,
not a national replacement or a validated research exposure release.

Open the normal launcher and choose **10 m pilot**, or visit
<http://127.0.0.1:8766/pilot>. Choose Heathrow, Didcot, Oxford or the Chilterns,
then Road / Rail / Aircraft and Lden / Day / Night. Clicking a location compares
all nine indicators at that coordinate and outlines its native 10 m cell.
The address retains the selected area, source, metric, point and release.

## Delivered

- Four 2 × 2 km areas, 36 accepted original GeoTIFFs: three transport sources
  and three annual indicators per area. No analytical regridding or 100 m averaging.
- New acquisition linked to saved dataset pages, capabilities, coverage
  descriptions, exact prepared request URLs, response timestamps, HTTP status,
  selected headers and SHA-256 hashes. This does not retroactively establish
  provenance for the historical England product.
- Grid checks verify EPSG:27700, 10 m spacing, native cell phase, dimensions,
  requested extent, nodata and accepted value encoding before publication.
- Road and rail reference **2021**, extracted from the provider's explicit
  Period field. Aircraft's period stays null, as the provider does not specify it.
- GeoTIFF nodata and zero sentinel cells stay **unreported**. Values are never
  imputed as 0 dB, a cutoff, or proof of quietness. Raw sentinel values are retained
  in downloads, alongside a null analytical value and an explicit status.
- Night retains reported 35–40 dB values; day and Lden start at 40 dB. Aircraft
  metadata says actual cutoffs vary by airport. These data still cannot distinguish
  the quietest below-cutoff areas or support event/background measures.
- Web Mercator PNGs use nearest-cell reprojection solely for display. Original
  GeoTIFFs are the analytical authority. Unknown cells have a separate hatched
  appearance; outside-pilot cells are not rendered as quiet.
- Point JSON and a portable evidence ZIP are available in the UI. The ZIP includes
  originals, metadata, checksums, dependency lock and construction/verification
  source snapshots. Only the manifest's `records` list identifies accepted data.
- Local HTTP routes retain Host/Origin restrictions, fixed resource allowlists and
  file-integrity checks. The launcher checks the pilot release before reusing a server.

Release: `pilot-6c08842ad8a81dfdc4bf`, at `artifacts/source_pilot_v1`.
The existing England display remains `explorer_display_v4`.

## Provider issues discovered and retained

The optional CSW metadata XML endpoint returned HTTP 500. Its response is kept;
the working dataset HTML is the metadata authority for this pilot. WCS XML
capabilities and coverage descriptions were successfully captured separately.

The WCS 1.0 road-night identifier advertised a `wksp` prefix that DescribeCoverage
rejected. The pipeline discovers the WCS 2.0 identifier independently and records
the fallback; it does not guess or rewrite identifiers. WCS 2.0 native cell-edge
subsets, without scaling, passed all grid checks. An initial cell-centre subset
returned a 199 × 199 grid and was rejected. That original response is retained as
an explicitly excluded nonproduct raster, never used by the map or point API.

## Verification and reproduction

```powershell
# Entirely offline: validate bytes, acquisition chain, period, grids and PNG replay.
.\.venv\Scripts\python.exe scripts/30_source_pilot.py --verify --reproduce

# New acquisition: always choose a new destination for a new published release.
.\.venv\Scripts\python.exe scripts/30_source_pilot.py --output artifacts/source_pilot_v2
```

The downloaded bundle can be extracted and verified with its own
`scripts/30_source_pilot.py --output <extracted-directory> --verify --reproduce`.
Use a Python environment matching its Windows dependency lock. Bundle replay was
tested with the existing project environment; a fresh dependency installation on
a clean machine has not been established by this milestone.

Checks completed: **401 passed, 2 skipped** (existing platform skips); 36 accepted
rasters and 180 pinned files verified; all 36 PNGs reproduced byte-for-byte both
from the working release and from an extracted evidence bundle. Regression tests
cover missingness, night cutoffs, unknown airport period, native-grid phase,
tampering, request preservation, outside-area results and HTTP restrictions.

Reference-point JSON and the tested bundle are saved in
`artifacts/source_pilot_verification_v1`. At the Heathrow preset coordinate
(-0.447622, 51.464852, on the airport site), road Lden is 44.89 dB and aircraft
Lden is 85.956 dB in their respective source products. These are model values for
that exact cell, not independently measured levels or a combined comparable total.

## Next gate

Review these four areas in use. Before extending the new construction path across
England, establish the airport reference period and applicable coverage/threshold
semantics, agree a bounded rollout/storage budget, and run clean-environment
reproduction. Road/rail day/night can progress with their explicit 2021 period.
Independent validation, broader UK coverage, background sound and event data remain
separate evidence gaps. Do not imply that smaller pixels solve those gaps.

Provider records: [Road](https://environment.data.gov.uk/dataset/562c9d56-7c2d-4d42-83bb-578d6e97a517),
[Rail](https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947),
[Airport](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8).
