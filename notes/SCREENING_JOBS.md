# Local screening jobs

The bounded screening-job service is a small loopback-only orchestration layer
around the existing `quiet_uk.candidates.extract_candidates` engine. It does
not implement a second screening algorithm, change candidate eligibility, or
modify the candidate viewer.

## Start the service

From the repository root, configure the catalogue, tiles, authoritative mask,
dedicated output root, and loopback port explicitly:

```powershell
& .\.venv\Scripts\python.exe scripts\27_serve_screening_jobs.py `
  --catalogue-dir artifacts\england_catalogue_v3 `
  --tile-root data\processed\england\tiles `
  --land-mask data\processed\england_mask\england_100m_mask.tif `
  --job-root artifacts\screening_job_verification_v1\jobs `
  --port 8781
```

The service binds only to `127.0.0.1`. It validates the catalogue metadata,
SQLite identity, and authoritative mask identity before listening. A second
service using the same job root fails because the root is held by a
nonblocking process-level lock.

## Submit and poll

Clients may submit only these fields:

```json
{
  "bbox_bng": [400000, 550000, 420000, 570000],
  "road_rail_upper_threshold_db": 45,
  "minimum_component_cells": 10
}
```

Example PowerShell requests:

```powershell
$request = @{ bbox_bng = @(400000,550000,420000,570000); road_rail_upper_threshold_db = 45; minimum_component_cells = 10 } |
  ConvertTo-Json -Compress
$accepted = Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8781/api/jobs `
  -ContentType 'application/json' -Body $request
$accepted

do {
  $status = Invoke-RestMethod -Uri ("http://127.0.0.1:8781" + $accepted.status_url)
  $status
  if ($status.state -eq 'running') { Start-Sleep -Milliseconds 200 }
} while ($status.state -eq 'running')
```

`POST /api/jobs` returns `202` with an opaque job ID and relative status URL.
Only one job runs at a time; a concurrent valid submission returns `409` and
does not enter a queue. Invalid requests return `400`, bodies above 16 KiB
return `413`, unsupported media types return `415`, and a shutting-down or
occupied service returns `409`.

The request validator reuses the extraction rules: exact aligned 100 m BNG
bounds, mask containment, the 160,000-cell limit, the existing threshold
sanity range, and a positive integer minimum component size. Unknown fields,
duplicate JSON keys, nonfinite values, booleans in numeric positions, wrong
types, unexpected Host headers, and nonlocal Origin headers are rejected.
Filesystem paths, catalogue choices, filenames, commands, and output names
are never accepted from the client.

## Status and outputs

`GET /api/health` reports service availability and whether a job is active.
`GET /api/jobs/{job_id}` reports `running`, `succeeded`, or `failed`, with
normalized parameters, catalogue build ID, timestamps, elapsed time, and
success summary or a categorized failure message. `GET` of
`candidates.json`, `candidates.geojson`, or `candidates.md` is allowed only
after success. All other paths, filenames, unknown IDs, and traversal-shaped
paths return `404`.

Each accepted job owns `configured_job_root/{job_id}`. Extraction writes the
three outputs through the existing atomic publication path. The manager marks
a job successful only after all files exist, the JSON/GeoJSON identities and
component ordering agree, the catalogue build ID and accepted parameters
match, the summary is readable, and Markdown is non-empty. Running and failed
jobs never serve partial outputs. Failed jobs release the single active slot;
completed job directories remain until external cleanup.

## HTTP connection and shutdown behaviour

Accepted HTTP connections use the named
`DEFAULT_HTTP_REQUEST_TIMEOUT_SECONDS` default (one second). The server
constructor accepts `request_timeout_seconds` so tests and controlled local
launches can use a smaller finite value. The timeout covers header and body
reads; an incomplete POST body receives a structured `408 request_timeout`
when the connection is still usable, while incomplete headers or a client
disconnect are closed safely without creating a job. A request that sends
occasional bytes cannot keep shutdown open: `server.shutdown()` first stops the
accept loop and admission, then `server_close()` closes every tracked request
socket and drains ordinary request threads. The extraction worker is not one
of those request threads and is never terminated by HTTP cleanup.

`ScreeningJobManager.begin_shutdown()` is the nonblocking admission stop.
`ScreeningJobManager.close()` is intentionally blocking: it waits for every
active extraction, removes only the manager's own marker, and releases the
job-root lock in a cleanup `finally` path. Repeated `close()` calls are safe.
The production launcher follows the same order, so a running extraction keeps
root ownership until it completes and a later service can then acquire it.

The service remains deliberately bounded and local. It has no cancellation or
recovery for an extraction, and an HTTP connection still in progress may be
closed during shutdown rather than receiving a final response. Request-thread
cleanup has a finite drain bound as a final safety measure for unexpected
handler failures; normal handlers exit as soon as their tracked sockets are
closed.

## Lifetime and restart behaviour

The job registry is in memory. Status history lasts only for the service
process. Completed artifacts remain on disk after restart, but old jobs are
not rediscovered and interrupted work is not resumed automatically. There is
no cancellation, recovery, queue, distributed execution, deletion endpoint,
public binding, CORS policy, authentication account, or general directory
server. On graceful shutdown, new submissions are refused, the active bounded
extraction is allowed to finish, worker state is closed, and the job-root lock
is released.

The future viewer integration point is the job status/output contract above;
the current candidate viewer routes and rendering are intentionally unchanged.

## Pilot verification

The real HTTP pilot used the preserved v2 catalogue, England tiles and mask,
and request `[400000, 550000, 420000, 570000]`, threshold `45`, minimum `10`.
The service returned `202`, reached `succeeded`, and served all three outputs.
The deterministic run ID matched
`screen-52e8d69959dd7d42739c01da494bb4f9`; component IDs/order, component
summaries, airport partitions, and geometry membership all matched the
preserved pilot. The result contained 128 retained components, 17,305
retained cells, and 275 excluded small-component cells.

Evidence and API-retrieved outputs are under
`artifacts/screening_job_verification_v1/`, including
`pilot_verification.json`, `pilot_verification.md`, and `retrieved/`.
