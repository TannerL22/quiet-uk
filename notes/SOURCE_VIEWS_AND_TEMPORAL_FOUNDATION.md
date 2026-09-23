# Source views and temporal foundation

Completed 21 September 2026. This increment responds to the Heathrow false-quiet
interpretation and the distinction between continuous sound and intermittent events.
It remains a local, general-purpose exploratory product.

## Product behaviour

- **Together** is the default: road/rail upper-bound colours plus purple hatching
  for reported aircraft energy. Hatching means presence, not aircraft loudness.
  This is an overlay of separate evidence, not an arithmetic or energetic total.
- **Aircraft** colours the reported aircraft lower bound. Beige is accepted but
  unreported lower energy, not silence. Grey marks withheld/coverage-limited
  evidence. Outside England land is transparent.
- **Road + rail** retains its upper-bound display and explicitly says aircraft is
  hidden. Independent road and rail views require new source-separated products.
- Location details put an available aircraft lower-bound card first, followed by
  road/rail. Both are labelled as historical evidence; the exact values remain in
  inspection/downloads. The panel explains missing temporal capabilities.
- View URLs include the source choice. Unknown choices fall back to Together.
  Existing links retain position/selection and get the generation-change notice.
- All noise layers share the visibility/opacity controls. Mobile layout reserves
  space for expanded attribution so it cannot cover the legend footer.

Launch remains `.venv\Scripts\python.exe scripts\29_serve_explorer.py`, then
http://127.0.0.1:8766/. Default display directory is `artifacts/explorer_display_v3`.
Earlier generations and scientific source tiles remain unchanged.

## Construction and API

Display contract version 2 adds `aircraft_energy.tif` and `aircraft_quality.tif`.
Aircraft lower-energy zero retains accepted unreported cells in the averaging
denominator; -1 is nodata. Quality codes are outside 0, accepted 1, withheld 2,
coverage-limited 3. Mean energy overviews retain zero support. Quality uses native
maximum resampling, so unavailable support conservatively suppresses a mixed
display pixel. Zoomed-out overlay/colours must not be sampled for analysis.

Tile routes are `/tiles/{release}/{road_rail|aircraft|aircraft_presence}/{z}/{x}/{y}.png`.
View identity is part of the bounded tile cache key and URL. Publication checks
all source tiles and records checksums for the new derivatives.

The dataset API includes `capabilities` and `views`. Point/download responses add
`evidence_schema_version`, `observations` and `capabilities` while preserving the
original `bands`. Each observation identifies its statistic/bound type, source
dependencies, value status, metric evidence, spatial support and dataset identity.
Unknown periods and uncertainty remain null with explicit statuses. A spatial
fraction is not misrepresented as a fraction of time. This is a historical-output
adapter, not yet a general acquisition schema or research-ready release.

Verified generation: `explorer-78547d9fccb8e1359ed3`.
Unchanged catalogue: `304d0ae422a84bebb83bcb7a7379e236`.

## Temporal computation foundation

`quiet_uk.temporal.summarize_intervals` accepts non-overlapping `SoundInterval`
records at one receiver, with explicit observation-window bounds, a common
seconds axis, comparable A-weighted levels, and input kind `interval_laeq` or
`constant`. It sorts and validates intervals and uses duration-weighted energy.

- Observed-time LAeq is separate from full-window LAeq. Gaps make the latter null.
- Piecewise-constant inputs additionally support observed-time LA90/LA50, time
  strictly below a threshold, and the longest contiguous observed quiet interval.
  Percentiles use a duration-weighted inverse empirical CDF, without interpolation.
- Interval LAeq inputs do not establish within-interval percentiles, peaks or
  quiet intervals. The returned maximum is named **maximum input level**, not LAmax.
- Gaps break quiet intervals. Splitting a constant segment leaves results unchanged.
- The library does not infer acoustic event counts, SEL, Lden, personal exposure,
  or source combinations. Callers must preserve input evidence, receiver metadata,
  time origin and weighting. No live national temporal observations are connected.

Example (synthetic, not environmental measurements):

```python
from quiet_uk.temporal import SoundInterval, summarize_intervals

result = summarize_intervals(
    [SoundInterval(0, 86390, 30), SoundInterval(86390, 86400, 120)],
    window_start_s=0, window_end_s=86400,
    level_kind="constant", quiet_threshold_db=40,
)
# LAeq,24h approximately 80.635 dB; 86,390 observed seconds below 40 dB.
# Event count remains unavailable: intervals are not annotated acoustic events.
```

## Provider inventory and what it permits

Official descriptions checked again on 21 September 2026:

| Product family | Documented indicators | Implication for next acquisition |
|---|---|---|
| Defra roads | Lden, Lday, Leve, Lnight, LAeq,16h | Separate source and time-period rasters are a viable acquisition target, not yet linked to our historical outputs |
| Defra rail | The above plus LAeq,6h and LAeq,18h | Preserve these as distinct metrics; do not substitute one duration for another |
| Defra airports | Lden, Lday, Leve, Lnight, LAeq,16h | Per-airport thresholds and reference periods need verification; the collection's period field is unpopulated |
| Temporal/event measures | Not advertised by those raster product descriptions | Obtain appropriate additional observations/model outputs; an annual raster cannot supply event histories |

These descriptions establish provider-advertised product families, not successful
fresh WCS acquisition, historical linkage, or national availability of every metric.
No new acoustic raster was acquired in this increment. Airport publication dates
remain separate from acoustic reference periods. CAA guidance defines additional
metrics but is not itself an event dataset.

Primary references:
- [Defra mapping explanation](https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps)
- [Defra airport collection](https://environment.data.gov.uk/dataset/dac9cba4-abe7-43bd-b8e9-8a83da52edd8)
- [Defra rail collection](https://environment.data.gov.uk/dataset/3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947)
- [CAA CAP 2598 metrics guidance](https://www.caa.co.uk/data-and-publications/publications/documents/content/cap2598/)

## Verification

- Full suite: **385 passed, 2 skipped**, 66.86 seconds. Existing Windows skips:
  replacing an open SQLite database and unavailable symbolic links.
- Focused explorer/temporal suite: 40 passed. Tests cover source-specific rejection,
  zero lower-energy support, missing/sea distinction, aircraft derivative tampering,
  per-view HTTP PNGs, observation semantics and temporal inference boundaries.
- Temporal regression cases include the 40 dB constant / 30 dB plus 10 seconds
  at 120 dB example, equal-energy histories with different quiet intervals,
  unequal-duration samples, gaps, invalid/overlapping intervals and segment splitting.
- JavaScript syntax check passed. Browser checks: Heathrow Together hatching,
  aircraft contours, numeric aircraft card, road/rail-only warning, source choice
  surviving reload, and phone-sized 390 x 844 layout with no horizontal overflow.
- Heathrow test location 51.464852, -0.447622: aircraft stored lower bound
  85.05769348144531 dB and road/rail stored upper bound 46.060791015625 dB.
  These retain historical qualifications; they are not validated current readings.

## Next bounded milestone

Acquire a fresh Heathrow canary containing separately identified road, rail and
aircraft Lden and available day/night products. Preserve exact request/response
identities, checksums and metadata snapshots. Verify reference-period and receptor
compatibility, thresholds, grid support and cross-source assumptions before
publishing a combined indicator or separate national road/rail controls. Source
appropriate temporal evidence independently; do not make national event coverage
a prerequisite for the bounded raster canary.
