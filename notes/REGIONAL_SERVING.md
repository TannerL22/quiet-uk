# Regional serving isolation and bounded exports

Implemented 25 September 2026. Scientific release remains
`pilot-70542da395300f52dd27`; source values, missingness, periods, thresholds and
acquisition evidence have not changed. This is a serving prerequisite for the
planned tiled canary, not a new data release or a public-hosting milestone.

## Reader contract

- `SourcePilot` remains a strict reader of the original release. Direct use still
  checks file hashes; offline verification and reproduction are unchanged.
- `ExplorerServer` takes its own private `RegionalSnapshot`. It copies each
  manifest-listed file with a 1 MiB buffer and hashes the exact bytes copied.
  Manifest identity, parent lineage and evidence-contract validation still apply.
  Corrupt input aborts startup and removes the partial private copy.
- HTTP requests read only the private copies, which are marked read-only.
  Source-folder replacement or editing has no effect on a running regional
  session. Restart explicitly to adopt a different release. Copies are not hard
  links, and the caller's in-memory manifest is not shared.
- Requests check private-file identity, size and nanosecond modification/change
  timestamps instead of hashing entire TIFFs repeatedly. These guards catch
  accidental copy changes; they are **not** protection against malicious code
  running as the same OS user. OS account permissions remain the trust boundary.
- At most 48 raster readers remain open. An LRU closes idle readers when needed;
  each GDAL dataset is exclusively borrowed by one request. Busy readers are never
  evicted or concurrently used. Errors release the borrow. Integrity checks also
  bracket use of cached readers.

## Evidence downloads and lifecycle

The first ZIP request writes one archive to a temporary disk file, hashes each
member while exporting it, and publishes the completed file by rename. Concurrent
requests share that archive. Failed writes remove partial output and may be retried;
changed completed archives are rejected. ZIP member contents and the manifest are
identical to the verified release. ZIP container metadata is not a release identity.

The server streams the ZIP in 1 MiB chunks instead of assembling a ZIP-sized byte
array. Two download slots bound concurrent export/transfer buffers; excess requests
receive 503 and can retry. Point queries remain available. Disconnects release
their slot and do not append JSON errors to partial ZIP bytes. The old `bundle()`
in-memory convenience method remains for small callers/tests; HTTP never uses it.

Normal server shutdown waits for active requests, closes raster readers, and then
removes its private data and archive. Failed startup cleans up too. Killing the
process or machine can leave an orphan `quiet-uk-serving-*` directory in the OS
temporary folder. No automatic sweep removes directories belonging to other
running processes. The launcher checks the serving capability before reusing a
server; an older running server needs to be stopped and relaunched.

## Regional measurements

Run without concurrent tests or other heavy project work:

```text
python scripts/32_benchmark_regional_serving.py --pilot artifacts/source_regions_v2
```

Default output is `artifacts/regional-serving-benchmark.json`, outside the data
release. The command uses no provider/geocoder network requests, starts an ephemeral
loopback server, and cleans up afterward. It measures 200 varying point requests
and 200 varying three-place requests across all four regions with five workers.
The random seed is fixed. Source/reader startup, first requests and export costs
are reported separately. OS file caches are not flushed, so these are not cold-disk
latencies. HTTP clients run in the same process as the server.

Measured on Windows 11 build 26200, Python 3.14.2, Qualcomm ARMv8 machine with 12
logical CPUs; original regional data total 298,268,162 bytes (excluding manifest):

| Measurement | Result |
|---|---:|
| Original source checksum verification | 0.843 s |
| Private snapshot copy and validation | 1.196 s |
| First point / first three-place request | 187 / 304 ms |
| Point median / p95, 200 requests | 66 / 92 ms |
| Three-place median / p95, 200 requests | 166 / 204 ms |
| First evidence export / cached export | 4.177 / 0.051 s |
| Evidence ZIP | 29,384,768 bytes |
| Python allocation peak during first export | 4,601,076 bytes |
| Whole-process lifetime peak RSS | 301,428,736 bytes |
| Private release plus archive on disk | 327,831,763 bytes |

The provisional p95 goals (<500 ms for points and <1.5 s for three places) pass on
this regional workload. Python allocation figures include the streaming client
but exclude native GDAL allocations; process RSS includes both and is a lifetime
high-water mark, not additional export memory. Reader reuse increases retained
native cache memory. The 48-reader cap bounds handles, not total process RAM.

The initial implementation without reader reuse measured point p95 538 ms and
comparison p95 647 ms. It overlapped briefly with tests, so it is diagnostic rather
than a controlled speedup comparison. Its output is retained locally as
`artifacts/regional-serving-before-reader-cache.json`.

## Verification and remaining scope

Regressions cover both release schemas, exact point/geometry/evidence preservation,
source mutation isolation, private-copy corruption, concurrent lookups with forced
reader eviction, error release, archive member hashes, one archive build under
concurrency, failed-export retry, HTTP download limits, safe member paths, and
cleanup after in-flight requests finish.

The complete local Windows/Python 3.14 suite passed: **492 passed, two platform
skips, three opt-in release checks deselected**. The release-backed benchmark
also verified the live HTTP release IDs and successful private-copy cleanup.

Still required before larger acquisition: explicit provider request/transfer and
local disk budgets, tiled overlap/seam and resume checks, and measurements on the
50 × 50 km canary including native memory, storage pressure and long-running usage.
Private copies cost one extra release-sized allocation on disk per running server,
plus a ZIP after the first download. There is no persistent cross-process cache,
hard disk quota, download resume/Range support or national performance claim.
The optional historical overview retains its existing verification/serving path.
No participant usability sessions, provider semantic clarification or public
deployment are completed by this change.
