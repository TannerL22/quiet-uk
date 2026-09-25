# 50 × 50 km extraction canary

This is an engineering acquisition and fidelity checkpoint. It is not yet added
to the map, and is not a validated research exposure release. The current app
continues to use its existing regional release. No new quietness bounds, event
indicators, airport periods or all-source totals are inferred.

## Area and recipe

The study rectangle is `[445005, 165005, 495005, 215005]` in EPSG:27700
(west, south, east, north): 2,500 km² around Oxford, Didcot, Reading and the
Chilterns. It fully overlaps the earlier Oxford, Didcot and Chilterns regional
extracts; Heathrow remains outside this canary and in the existing app.

Twenty-five 10 × 10 km core tiles partition the rectangle without gaps or
double-counting. Requests have a one-cell (10 m) halo where another tile adjoins,
giving a 20 m shared strip for exact seam comparisons. Requests are at most
1002 × 1002 native cells; outer borders stop at the study rectangle. This is a
separate tiled recipe, not an increase to the four-site pilot's limits.

Road, rail and aircraft each retain Lden, Lday and Lnight: 225 planned raster
requests and 225 million unique core source-indicator cells. Halos add redundant
verification cells. Every request intersects the independently captured provider
storage envelope before dimensions are calculated. Returned shape, extent, CRS,
10 m phase, dtype, finite values and nodata encoding are checked. Clipped output
is never padded or silently resampled. A provider storage rectangle is not a
verified calculation-domain mask.

## Budgets, capture and resumption

`plan.json` and the reference manifest are pinned before network work. The recipe
cannot change in the same destination. Cumulative limits apply across restarts:

| Limit | Value |
|---|---:|
| Total HTTP attempts, including discovery and failed requests | 300 |
| Retained response data budget | 3 GiB |
| Maximum response | 32 MiB |
| Attempts for any identical prepared request | 3 |
| Minimum interval between request starts | 1 second |
| Free-disk reserve checked before requests | 10 GiB |

Only one acquisition process holds the destination lock. Requests run serially.
No automatic hidden retries or redirects occur; redirects and HTTP failures are
retained as responses. Streaming uses 64 KiB buffers. Bodies are the HTTP response
content exposed by Requests (content encoding may be decoded); saved headers
record content encoding where supplied. These are retained-body limits, not a
measurement of TCP/TLS overhead or a host-wide disk quota.

Each attempt has its own body and JSON record: prepared URL, retrieval time,
status, selected headers, byte count, checksum and completion flag. An initial
journal reservation is written before requesting. A process interrupted before
finalising its journal conservatively consumes a full response allowance on
resume. Completed failed attempts retain their actual saved bytes. No failed
response is overwritten. HTTP 200 is insufficient: a raster failing grid/value
QA is marked rejected and cannot enter the accepted records.

Successful responses and accepted checkpoints are verified before reuse. A
re-run retries failures within the original budgets and does not redownload
successes. Discovery evidence and declared periods are reconstructed offline and
compared to the saved products. A changed product identifier/period stops work
for review. Use the same code checkout throughout an acquisition; the sealed
result retains the verifier/construction sources and dependency lock.

## Completion and offline checks

Publication is last: `manifest.json` appears only after all planned tile/indicator
records are accounted for, every accepted raster passes QA, shared strips match
exactly, and intersections with the reference release match exactly. Both raw
sample values and validity masks must match, along with encoding and grid phase.
Reference comparisons use tile cores so halo duplicates do not inflate the count.
Zeros and TIFF nodata are retained, not replaced with noise bounds.

The final manifest hashes the acquisition recipe, metadata, every attempt
(including failures), accepted records, verification report, reference manifest
and construction sources. Offline verification checks the sealed file index and
manifest identity, reconstructs request URLs, repeats raster QA and comparisons,
and requires the same reference release. These checks establish extraction
fidelity and reproducibility, not independent acoustic accuracy.

```text
# Print the bounded plan; no network or data writes.
python scripts/33_tiled_canary.py

# Acquire one tile's nine layers, or resume from a checkpoint.
python scripts/33_tiled_canary.py --acquire --max-new-rasters 9

# Resume the remaining work within the SAME cumulative budgets.
python scripts/33_tiled_canary.py --acquire

# Full offline replay after completion; requires the original reference release.
python scripts/33_tiled_canary.py --verify
```

Defaults: output `artifacts/tiled_canary_v1`, reference
`artifacts/source_regions_v2`. Keep both releases for replay. A sealed destination
cannot be reacquired or overwritten. The generated data remain outside Git;
pushing the application is not a data backup. The CLI never changes the normal
launcher or installs the canary as the app's active release.

## Remaining canary phase

After the acquisition checkpoint, implement a tiled reader/display path and
benchmark its private snapshot, 48-reader cache, archive, memory and latency on
this larger working set. The existing four-region performance numbers cannot be
assumed to hold for 225 rasters. Test exact tile-boundary lookup ownership and
display seams before exposing the wider area in the app. That serving/display
gate, participant usability sessions and provider zero/domain clarification
remain separate from this extraction checkpoint.
