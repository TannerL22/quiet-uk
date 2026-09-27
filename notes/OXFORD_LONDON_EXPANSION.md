# Oxford–London detailed coverage expansion

## Scope

Extend the existing 50 × 50 km western area eastward to a 100 × 50 km rectangle,
BNG cell edges `[445005, 165005, 545005, 215005]`. The resulting 5,000 km² includes
Heathrow; it is not counted again as a separate area. This adds 2,400 km² to the
previous 2,600 km² map. Native 10 m road, rail and aircraft grids retain all three
indicators (Lden, Lday, Lnight). This rectangle includes central London but is not
a claim to cover all Greater London or the UK.

## Reuse and acquisition

`seed_extension` first verifies the sealed western canary. A separate destination
retains the complete original release under `reuse/` and copies its acquisition
journals, metadata and accepted records into the working extension. The original
directory remains unchanged. Offline verification repeats parent checks and
requires all 225 inherited records to remain identical.

The 25 western core tiles keep their original halo requests. The first eastern
column overlaps the western edge by one native cell, allowing exact values and
masks to be checked across the join. The 25 new eastern cores require 225 new
rasters; no western layer is re-downloaded. The entire regional reference remains
available for comparison, now including the Heathrow intersections.

Budgets remain serial and cumulative across resumptions. The extended recipe
allows 600 total attempts and 6 GiB of charged response bodies **including the
inherited attempt history**. The 32 MiB response ceiling, three attempts per
request, one-second request interval and 10 GiB disk reserve are unchanged.
Archived parent copies consume additional disk; they are not additional transfers.

## Reproduction

Run with the documented project environment from the repository root:

```text
python scripts/33_tiled_canary.py --extend-from artifacts/tiled_canary_v1 --output artifacts/oxford_london_acquisition_v1
python scripts/33_tiled_canary.py --acquire --output artifacts/oxford_london_acquisition_v1
python scripts/33_tiled_canary.py --verify --output artifacts/oxford_london_acquisition_v1
python scripts/34_tiled_map.py --canary artifacts/oxford_london_acquisition_v1 --output artifacts/oxford_london_map_v1
python scripts/32_benchmark_regional_serving.py --pilot artifacts/oxford_london_map_v1 --output artifacts/oxford_london_benchmark.json
```

The seed command requires a new destination. Subsequent acquisition resumes the
existing checkpoint. Display construction supports `--resume` for an unsealed
destination; sealed releases are immutable. Both commands default to the original
regional evidence reference at `artifacts/source_regions_v2`.

The launcher prefers the expanded map when its sealed manifest exists; otherwise
it retains the previous release. A running server needs a restart to adopt a new
release. Existing versioned links disclose a release change; old saved comparisons
are not silently transferred across releases.

## Evidence limits

Geographic expansion does not fill unknown cells, resolve provider zero/domain
meanings, establish the aircraft reference period, or create an all-source total.
No new quietness bounds, event histories or claims of independent acoustic
validation are introduced. The sampled provider descriptions include every planned
core, but accepted raster responses and exact overlap checks are required before
publication. Data artifacts remain local and excluded from Git.

## Delivery results

Acquisition and verification in progress. No expanded installed release is claimed
until the recorded checks and browser delivery below are complete.
