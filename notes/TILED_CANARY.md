# 50 × 50 km extraction canary

This is an engineering acquisition and fidelity checkpoint, not a validated
research exposure release. The app uses a separate map derivative of this archive.
No new quietness bounds, event indicators, airport periods or all-source totals
are inferred.

**Integration update, 26 September 2026:** a separate [tiled map release](TILED_MAP.md)
now serves the acquired area alongside Heathrow. This acquisition archive remains
unchanged.

## Completed acquisition: 26 September 2026

Sealed local release: `canary-90d7e5ac25859921a92b`, referenced to
`pilot-70542da395300f52dd27`.

| Check | Result |
|---|---:|
| Accepted source/indicator rasters | 225 of 225 |
| Exact shared-strip comparisons | 360 |
| Shared-strip source-indicator cells checked | 721,152 |
| Exact reference intersections | 108 |
| Unique reference source-indicator cells checked | 27,000,000 |
| HTTP attempts, including discovery and unavailable journals | 265 of 300 |
| Charged response bytes, including unavailable-attempt reservations | 2,225,820,452 of 3,221,225,472 |
| Retained dataset size, including recovery evidence | 1,701,020,906 bytes |

All comparisons match raw values and validity masks exactly. The dataset remains
local under `artifacts/tiled_canary_v1`; the source push does not back it up.
A separate post-seal `--verify` run passed offline, checking the manifest/file
hashes, metadata reconstruction, raster QA and all comparisons again; its report
is `artifacts/tiled_canary_offline_verification.json`.
The pre-recovery source checkpoint is retained in
`recovery/pre-recovery-source-e129cc2.zip`; the sealed construction directory
contains the recovery-capable version used to finish and verify the run. This
records the code transition without asserting per-attempt code provenance that
was not originally captured.

The recovery regression suite passed 11 tests. The local full suite had 502
passes, two skips and three opt-in deselections, plus one Windows connection-abort
failure in the existing HTTP API tests; all 15 tests in that module passed on
rerun. The [Windows/Linux, Python 3.12/3.14 CI matrix passed all four jobs](https://github.com/TannerL22/quiet-uk/actions/runs/36233521397)
for source commit `537c35a`.

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

### Recovery from damaged checkpoints

An interruption during the first live run left the latest `records.json` and 20
request journals zero-filled. Atomic rename had protected the filename transition
but had not ensured that recently buffered file contents survived. Checkpoint
writes now flush and `fsync` the temporary file before rename; completed response
bodies are flushed before publishing their completed journals. POSIX also syncs
the containing directory. These steps improve durability, not immunity to disk
failure or power loss.

Explicit `--recover-checkpoint` preserves the original damaged checkpoint and
journals, reconstructs request identity only where the pinned recipe matches the
request-directory hash, and marks those journals unavailable. It does not invent
HTTP status, retrieval time or missing checksums. Each unavailable attempt remains
charged a full 32 MiB allowance and counts toward the attempt limit. Its body
cannot be reused. The accepted list is rebuilt only from intact successful journals
whose body hashes, request identity and raster QA verify, then seam/reference
checks run again. Damaged metadata or changed completed response bytes stop for
review. Sealed releases are never repaired in place.

The actual recovery restored 110 accepted rasters; 130 intact request journals
(including discovery) had unchanged body hashes. It retained 20 damaged journals
and the zero-filled checkpoint, passed 148 shared-strip comparisons and matched
9,566,402 reference cells. Reacquisition uses the original budgets rather than
resetting counters. Evidence is under `recovery/` and `*.json.damaged` within the
canary directory. A regression reproduces lost-journal/checkpoint damage and
checks that only provable responses are reused.

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

# Only when the checkpoint/journals are damaged; preserves the original evidence.
python scripts/33_tiled_canary.py --recover-checkpoint

# Full offline replay after completion; requires the original reference release.
python scripts/33_tiled_canary.py --verify
```

Defaults: output `artifacts/tiled_canary_v1`, reference
`artifacts/source_regions_v2`. Keep both releases for replay. A sealed destination
cannot be reacquired or overwritten. The generated data remain outside Git;
pushing the application is not a data backup. The CLI never changes the normal
launcher or installs the canary as the app's active release.

## Subsequent integration

The tiled reader/display path, exact ownership and display-seam checks, and the
larger-working-set snapshot/cache/export/latency benchmark are now complete.
See [the integration report](TILED_MAP.md) for measured results rather than
extrapolating the earlier four-region numbers. Participant usability sessions and
provider zero/domain clarification remain open and separate from this checkpoint.
