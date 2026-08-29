# Live pilot status — v0.2

> Historical status note. The Phase 1 England national run and QA were completed after this pilot note was written. See `notes/ENGLAND_NATIONAL_VALIDATION.md` for the frozen national result and `notes/PHASE2C_ROAD_SOURCE_INTEGRITY.md` for the later bounded road-source experiment.

## Completed

- Verified current Defra Round 4 road, rail and airport dataset metadata.
- Verified 10 m / 4 m grid specification and Lden censoring rules for road and rail.
- Configured the official WCS endpoints.
- Built automatic WCS coverage discovery and Lden-ID selection.
- Built direct WCS 1.0 GeoTIFF retrieval.
- Built censor-aware logarithmic source combination.
- Corrected 10 m → 100 m aggregation to operate on lower/upper acoustic-energy bounds.
- Added raster-grid alignment checks before sources can be combined.
- Passed the acoustic unit test suite.
- Passed a synthetic three-GeoTIFF end-to-end integration test.
- Completed the first live Heathrow / west-London Defra pilot; see
  `notes/LIVE_PILOT_VALIDATION.md` for coverage IDs, service behaviour,
  statistics, alignment handling and remaining national-scale risks.
- Validated the reusable tiled architecture and bounded 2×2 Heathrow seam
  test; see `notes/TILING_AND_AIRPORT_THRESHOLD_VALIDATION.md`.

## Subsequent status

The official ONS England mask, resumable national runner, and geographically diverse live canary were completed and are documented in `notes/PRODUCTION_READINESS_VALIDATION.md`.

The live airport endpoint requires WCS 2.0.1 and returns a half-cell-shifted native grid; the validated national tiler pads and aligns it explicitly. Airport threshold variation remains a limitation for any airport-inclusive upper-bound interpretation.

On an internet-connected environment, the validated pilot can be rerun with:

```bash
cp config.example.json config.json
python scripts/04_run_pilot.py
```

If the live service returns coverage names/formats that differ from the WCS 1.0 assumptions, inspect `data/processed/coverage_catalog.json` and adapt only the retrieval layer; the acoustic and raster-processing pipeline is already independently tested.
