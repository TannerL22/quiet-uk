# Bounded candidate screening

`scripts/22_extract_candidates.py` provides a read-only, bounded screen for
road/rail candidate areas. It is downstream of the reviewed catalogue and
does not acquire sources, rebuild rasters, modify catalogue inputs, or assign
provider reference years.

## Contract

The request is an exact `EPSG:27700` BNG rectangle whose four edges align to
the authoritative 100 m England mask grid. The rectangle must be ordered,
finite, inside the mask extent, and contain no more than 160,000 cells. A
misaligned or oversized request is rejected; it is never snapped, clipped,
interpolated, or silently reduced.

For every selected tile, the screening pass verifies the catalogue-recorded
file identity, verifies the authoritative mask identity, and reuses the
shared production semantic validation on the raster read window. A tile
ownership overlap is an integrity error. Cells outside the England land mask,
uncovered cells, road/rail bands that are not exactly `qualified`, and values
above the supplied threshold are ineligible and break 4-neighbour components.
The comparison is inclusive and exact:

```text
road_rail_upper_db <= supplied threshold
```

The threshold must be finite and inside the existing 0--150 dB acoustic sanity
range. Component labels use bounded flood fill and 4-neighbour adjacency;
SciPy and Shapely are not required. Components are filtered by minimum cell
count, then ordered by descending cell count and a stable spatial tie-breaker.
That order is an area order, not an acoustic ranking. The reported area is
`cell_count × 0.01 km²`; it is not calculated from lon/lat geometry.

Each retained component records its deterministic ID, cell count, cell-count
area, BNG bounds, a deterministic representative cell centre, road/rail upper
minimum and maximum, threshold, bbox-boundary contact, adjacency to uncovered
or road/rail-withheld land, and source tile IDs. Airport information is kept
separate: source-quality state counts, fraction availability by qualification,
zero/positive fraction counts, and finite/censored airport lower-bound counts
with qualification are included. Every airport partition reconciles to the
component cell count. A zero airport fraction means no reported airport
pixels, not no aircraft noise.

Component IDs are hashes of the catalogue build identity, screening policy and
version, request parameters, and global 100 m cell membership. Repeating an
identical request against the same catalogue therefore preserves component
IDs and ordering. The three outputs share the same run ID:

- `candidates.json` — machine-readable screening report;
- `candidates.geojson` — WGS84 lon/lat FeatureCollection for the actual
  eligible cells, without a GeoJSON `crs` member;
- `candidates.md` — human-readable summary and limitations.

The destination must be new or empty. Existing non-empty directories are
refused, and all three files are serialized in a staging directory before the
empty destination is replaced. An empty result is valid.

## Reviewed pilot

The offline pilot used the reviewed `england_catalogue_v3`, its recorded tile
directory and authoritative England mask:

```powershell
python scripts/22_extract_candidates.py `
  artifacts/england_catalogue_v3 `
  data/processed/england/tiles `
  data/processed/england_mask/england_100m_mask.tif `
  artifacts/candidate_screening_pilot_v1 `
  --bng 400000 550000 420000 570000 `
  --threshold-db 45 `
  --minimum-component-cells 10
```

The 20 km × 20 km request contains 40,000 aligned cells and read nine tiles.
The run completed in 3.872370 seconds on the reviewed Windows environment.

| Pilot measure | Result |
|---|---:|
| Requested cells | 40,000 |
| England land cells | 39,979 |
| Owned land cells | 39,979 |
| Uncovered land cells | 0 |
| Road/rail withheld or non-qualified land cells | 0 |
| Road/rail cells above 45 dB | 22,399 |
| Eligible cells before size filter | 17,580 |
| Retained components | 128 |
| Retained component cells | 17,305 |
| Cells excluded as small components | 275 |

The pilot's airport summary covers the 17,305 retained component cells. It
records 15,246 `grid_accepted` and 2,059 `grid_rejected` airport source states;
17,099 zero fractions and 206 positive fractions; and a reconciled partition.
All 128 deterministic representative cells were checked against the existing
catalogue lookup convention (west-inclusive/east-exclusive and
south-exclusive/north-inclusive): every lookup returned England land, a tile
listed by the component, the same representative centre, and a road/rail value
at or below 45 dB. The first comparison was component
`candidate-ad5559ef81d29e33dc302559` at `410650,562650`, tile `r0009c0032`;
the final comparison was component `candidate-8fa1c4658ce4737ee9a0fea6` at
`407850,562850`, also tile `r0009c0032`. These comparisons are read-only and
do not modify the reviewed catalogue or rasters.

The output records `public_access_status: not_assessed` and
`historical_acquisition_linkage: unresolved`. No provider reference year is
assigned to the historical rasters.

## Candidate geometry generation

The geometry pass uses the already-labelled regional candidate raster. It
calls `rasterio.features.shapes` once with the integer component labels, a
mask containing only retained labels, `connectivity=4`, and the authoritative
regional transform. Returned pieces are grouped by integer label and emitted
as a `Polygon` or `MultiPolygon` after the existing BNG-to-WGS84 transform.
This preserves the actual eligible cells, holes, and disconnected pieces
without allocating a components × height × width array or repeating full
regional polygonization for every component. If no component survives the
size filter, polygonization is skipped entirely. Component IDs, output
schema, policy version, ordering, and reported cell-count area are unchanged.

The geometry back-check transforms every output geometry from WGS84 back to
BNG and rasterizes cell centres with `all_touched=False`. The regression suite
requires exact per-component membership and exact union membership, no
overlap between component footprints, valid Shapely geometry, and a covered
representative cell. It covers diagonal-only contact, holes, narrow
one-cell connections, horizontal and vertical seams, a four-tile junction,
withheld or uncovered interruptions, concave/corner boundaries, and highly
fragmented labels. Empty results are checked to perform no polygonization.

## Publication failure guarantees

JSON, GeoJSON, and Markdown are written into an invocation-owned temporary
directory. Publication failures surface the original exception, remove the
owned temporary directory where possible, and do not leave a completed output
set. A failed final replacement also leaves the destination absent or empty
under the tested local-filesystem failure mode. A pre-existing non-empty
destination is refused before writing and remains byte-for-byte untouched.
These checks cover cleanup and publication refusal; they are not a claim of
durability against power loss or arbitrary filesystem failure.

## Geometry verification and benchmark

The bounded evidence harness is
`artifacts/candidate_geometry_verification_v1/geometry_verification.json`
and its Markdown companion. It records Python/native geospatial versions,
the synthetic inputs, the polygonization call count and raster workload,
exact membership checks, validity checks, and timings for the captured
pre-change implementation and the single-pass implementation.

| Synthetic case | Baseline calls / cells | Single-pass calls / cells | Baseline s | Single-pass s |
|---|---:|---:|---:|---:|
| contiguous 64×64 | 1 / 4,096 | 1 / 4,096 | 0.001200 | 0.003586 |
| 1,600 isolated cells | 1,600 / 10,240,000 | 1 / 6,400 | 1.228782 | 0.304424 |
| 729 four-cell components | 729 / 4,665,600 | 1 / 6,400 | 0.515584 | 0.122021 |
| holes and narrow connection | 1 / 6,400 | 1 / 6,400 | 0.000646 | 0.001201 |

On the maximum permitted request, a 400×400 grid (160,000 cells) with
40,000 isolated components completed with one polygonization call, 160,000
cells supplied, and 40,000 geometries returned in 6.655167 seconds in the
recorded environment. The one-component cases show that fixed overhead can
make the new path slower; the useful improvement is bounded raster work as
component count grows. Timings are environment-specific, not performance
guarantees, and coordinate transformation, feature creation, and JSON
serialization still scale with component count and geometry complexity.

## Pilot v1/v2 equivalence

The reviewed `artifacts/candidate_screening_pilot_v1` output is preserved.
The fresh `artifacts/candidate_screening_pilot_v2` output was generated with
the same catalogue, mask, request, threshold, and minimum component size.
It contains 128 retained components and 17,305 retained cells, with 275
cells excluded as small components. Comparison found identical catalogue
build ID, request parameters, component IDs and order, component summaries,
airport partitions, and zero geometry-membership differences after the
WGS84-to-BNG raster back-check. All 128 geometries were valid.

The exact PowerShell reproduction commands are:

```powershell
& .\.venv\Scripts\python.exe scripts\23_benchmark_candidate_geometry.py

& .\.venv\Scripts\python.exe scripts\22_extract_candidates.py `
  artifacts\england_catalogue_v3 `
  data\processed\england\tiles `
  data\processed\england_mask\england_100m_mask.tif `
  artifacts\candidate_screening_pilot_v2 `
  --bng 400000 550000 420000 570000 `
  --threshold-db 45 `
  --minimum-component-cells 10

& .\.venv\Scripts\python.exe -m pytest -q tests\test_candidates.py -rs
& .\.venv\Scripts\python.exe -m pytest -q -rs

& .\.venv-repro-check\Scripts\python.exe -m build --wheel --no-isolation --outdir dist\repro-check-final
& .\.venv-repro-check\Scripts\python.exe -m pip install --no-deps --force-reinstall dist\repro-check-final\quiet_uk-0.2.0-py3-none-any.whl
& .\.venv-repro-check\Scripts\python.exe -m pip check
& .\.venv-repro-check\Scripts\python.exe -m pytest -q -rs -o pythonpath=
```
