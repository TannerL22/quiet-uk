# Terrain and building-height inputs — 7 October 2026

## Delivered scope

The fixed north-Heathrow road-model study now has complete **native 2 m terrain
and last-return surface elevation** coverage: 4 km² in the study core and
144 km² in its surrounding source area. The previous cached 10 m terrain
covered only 80 km² of that surrounding area; the new inputs cover the remaining
64 km² as well. This increment prepares physical inputs for a bounded experiment;
it does not add noise predictions or change the product's existing map layers.

The core remains EPSG:27700 `(506000,178000,508000,180000)` and the surrounding
area `(501000,173000,513000,185000)`. Original source responses and existing
road/traffic/indicator generations were preserved. Both new elevation mosaics
retain the exact provider pixels and missing-data masks without additional
resampling, shifts or filling. All **72,000,000 elevation pixels and their masks**
were compared back to the 72 original 2 km extracts.

## Source qualification

- [Environment Agency 2 m DTM](https://environment.data.gov.uk/dataset/09ea3b37-df3a-4e8b-ac69-fb0842227b04)
  and [2 m last-return DSM](https://environment.data.gov.uk/dataset/f083c5dc-504f-4428-9811-a1b2519fa279):
  the provider's **2022 composite**, in metres above Ordnance Datum Newlyn,
  EPSG:27700. The source composite itself can include provider resampling; this
  increment applies none. The DSM includes vehicles, vegetation and structures.
  Source-survey ±15 cm RMSE does not establish the accuracy of derived roof heights.
- [Official survey index](https://environment.data.gov.uk/KB6uNVj5ZcJr7jUP/ArcGIS/rest/services/LIDAR_Composite_Catalogues/FeatureServer):
  layer 5 for DTM, layer 4 for last-return DSM, exact composite-year filter and
  full surrounding-area query. Four records occur in each response; corresponding
  survey identities, dates, source resolutions and polygon geometries match.
  Raw survey polygons overlap and include older surveys, so they are not treated
  as a directly observed per-pixel lineage map.
- [OS OpenMap Local](https://api.os.uk/downloads/v1/products/OpenMapLocal):
  declared version **2026-04**. Only the TQ regional shapefile archive was downloaded,
  not the national GeoPackage. The provider size and MD5 verified, alongside SHA-256.
  The [building definition](https://docs.os.uk/os-downloads/products/maps-and-imagery-portfolio/os-openmap-local/os-openmap-local-technical-specification/feature-types/building)
  explicitly allows generalised/amalgamated buildings and structures. This is
  suitable provisional context, not independently surveyed detailed acoustic geometry.

The two EA source dates can be paired by identical metadata. A **survey-date
inference** additionally follows the provider's newest/best-resolution rule:
choose a survey only when its source resolution equals every competing survey's,
and its start date is later than all their end dates. Differing resolutions,
overlapping date ranges or tied dates remain ambiguous. This is explicitly
labelled inference from the provider rule, not a direct raster pixel source ID.
The original index records and their complete dates remain available.

The index contains these survey ranges, all at 1 m original survey resolution:

| Survey | Start | End | Provider year field |
|---|---|---|---|
| P_10751 | 2018-03-13 | 2018-04-05 | 2018 |
| P_10752 | 2018-01-18 | 2018-01-18 | 2018 |
| P_12149 | 2021-03-11 | 2021-03-11 | 2021 |
| P_12150 | 2020-12-04 | 2021-02-21 | 2021 |

A provider year of 2021 therefore does not imply every survey occurred entirely
in that calendar year. Acquisition timestamps are retrieval records, not surveys.

The conservative date inference selects P_12150 across **117 km²**, including the
entire study core, and P_12149 across **27 km²**. No pixel receives a 2018 survey
code after this selection. Both elevation products have 100% matching inferred
survey support; the older records remain in the original index evidence.

## Measured coverage and height availability

| Input/support | Study core, 4 km² | Surrounding area, 144 km² |
|---|---:|---:|
| Valid native terrain cells | 1,000,000 (100%) | 36,000,000 (100%) |
| Valid native surface cells | 1,000,000 (100%) | 36,000,000 (100%) |
| Matching inferred survey support | 1,000,000 (100%) | 36,000,000 (100%) |
| Generalised footprints intersecting area | 1,744 | 50,495 |
| Provisional surface-height candidates | 1,256 | 37,167 |
| Withheld candidate heights | 488 | 13,328 |
| Median of candidate medians | 6.41 m | 6.39 m |

Footprint counts are source features, not verified individual buildings. The
surrounding area's withheld reasons are 8,158 with too few interior pixels,
2,992 without a buffered interior, 1,003 with excessive negative differences,
766 without a clear elevated median, 373 crossing the extraction boundary, and
36 with mixed inferred survey dates. Candidate medians span 2.00–57.22 m in the
surrounding area and 2.02–33.59 m in the core. These are observed candidate
distributions, not independently validated building dimensions.

## Building surface heights and their limits

The bounded footprint extraction retains source IDs, source record numbers and
unmodified EPSG:27700 WKT geometry. Roofed entities in the Building layer are
included; ImportantBuilding labels are not duplicate footprints. Glasshouse,
barrier/wall, vegetation-class and ground-absorption inputs are not qualified by
this building layer.

For each full footprint inside the surrounding area, the method selects native
pixel centres in a **2 m inward buffer**. It retains DSM minus DTM statistics:
minimum, p10, median, p90 and maximum. Negative differences are retained for
diagnostics. They are not silently converted to zero. A candidate requires at
least four interior and paired pixels, at least 95% valid elevation and inferred
paired-survey support, one survey identity, at least 95% nonnegative samples and
a median surface difference of at least 2 m. These are fixed operational screening
rules, not calibrated accuracy or probability thresholds.

Candidates carry the status `provisional_surface_height`. Others retain an
explicit reason and a blank candidate height, including small interiors,
extract-edge footprints, missing/ambiguous support, mixed survey dates, negative
differences and no clear elevated surface. No class-average, neighbour-height
or zero filling occurs. Full native surface differences remain available for
later terrain/structure inspection.

An inward buffer reduces footprint-edge contamination; it cannot distinguish
every tree overhang, rooftop object or courtyard inside a generalised outline.
The 2026-04 footprints also post-date the LiDAR: historical compatibility is
**unestablished** for every record. The median is not a verified eaves, exact roof
or acoustic obstacle height. `acoustic_obstacle_height_m` and
`height_uncertainty_m` remain unavailable. The p10–p90 spread describes samples
inside an outline and is **not a confidence interval**.

## Provenance, outputs and reproduction

Source generation: `artifacts/physical_scene_sources_v1/`, identity
`physical-scene-sources-484bd6d42cae4064b5c2`. It contains 73 successful bounded
GET downloads, **532,832,055 bytes**, request/redirect/header receipts, SHA-256
checksums, verified regional provider MD5, retained WCS descriptions and survey
indices. There were no acquisition failures. Original discovery receipts are
retained separately under `artifacts/physical_scene_discovery_v1/` and copied as
metadata into the source generation.

The completed input generation is under `artifacts/physical_scene_inputs_v2/`:
identity **`physical-scene-df5e37f446642f352e5c`**, 996,522,960 bytes including
original decompressed building components and review outputs.

- `terrain_odn_2m.tif` and `surface_odn_2m.tif`: exact native elevations.
- `surface_above_terrain_2m.tif`: raw surface differences, including negatives;
  not a building/vegetation classification.
- `dtm_survey_code_2m.tif`, `dsm_survey_code_2m.tif`: inferred survey support;
  0 is unmapped and 65535 is unresolved overlap.
- `building_heights_bng.csv`: original BNG geometries, provisional heights,
  samples, dates, unknown fields and withholding reasons.
- `building_heights_review_wgs84.geojson`: review geometry only; calculations
  use the original BNG footprints.
- `report.json`, `inventory.json`, code snapshots, provider-research receipts,
  original archive building components, OS licence and review image.

The archive has 1,792,420 regional building records. Its building components are
decompressed once with an explicit 600 MiB budget, then filtered by bounding box
and positive-area intersection. The reader otherwise seeks back to the start
of a compressed DBF for every selected feature, causing repeated decompression.
Only the selected local geometries and attributes are processed for heights.

Both products use [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/).
Attribution travels with the machine outputs:
© Environment Agency copyright and/or database right 2022. All rights reserved.
Contains OS data © Crown copyright and database right 2026.
These local artifacts are ignored by Git and are not an off-machine backup.

```powershell
# Use new output directories; completed inventories are immutable.
.venv\Scripts\python.exe scripts\45_acquire_physical_scene_inputs.py --output artifacts/physical_scene_sources_NEW
.venv\Scripts\python.exe scripts\46_build_physical_scene_inputs.py --source artifacts/physical_scene_sources_NEW --output artifacts/physical_scene_inputs_NEW
.venv\Scripts\python.exe scripts\47_verify_physical_scene_inputs.py --inputs artifacts/physical_scene_inputs_NEW --output artifacts/physical_scene_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_physical_scene.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py -q -p no:cacheprovider --basetemp artifacts/physical_scene_pytest_NEW
```

The related suite passes **95 tests**, including 31 new physical-scene checks.
The final test receipts are `artifacts/physical_scene_validation_v2.xml` and
`artifacts/physical_scene_validation_v2.json` with exact code/test hashes.
The initial run encountered permissions on an existing shared pytest temp/cache
directory; the clean run used a fresh workspace temp directory. Tests cover
processing correctness and refusal rules, not independent physical-height or
noise accuracy.

The source-subtraction diagnostic retains **7,615,104 negative cells (21.15%)**
over the surrounding area. Their median is only **−0.000500 m**; 1,835 cells are
below −0.15 m and 392 below −1 m. Their cause has not been independently
established. Original values are preserved, with no blanket clipping to zero.
The exact distribution and source hashes are in
`artifacts/physical_scene_surface_diagnostics_v1/diagnostics.json`.

An additional live integrity pass checked **all 50,495 exports** against the
original building layer's record numbers, IDs, feature codes and full BNG
geometries, using zero tolerance for geometry equality. It also reconciled every
CSV/review height state and all core/surrounding counts, then verified that the
input inventory remained unchanged. This establishes export fidelity, not the
accuracy of the building heights themselves.

The separate review generation is `artifacts/physical_scene_review_v1/`, identity
`physical-scene-review-534127fb18cc57007260`. Its `verification.json` and code
snapshot retain the checks; its `physical_scene_review.png` was visually checked.
The figure labels date support as inferred, shows only the selected 2020–21
surveys, and marks height/terrain colour-scale overflow. Analytical input files
remain in the immutable input generation. The stopped first build, which kept
every raw index overlap ambiguous and exposed slow ZIP seeking, had no completed
inventory and was removed; only the completed v2 inputs are delivered.

## Next model prerequisites

Terrain coverage is complete, and the physical scene now includes traceable
surface grids and provisional building-height candidates. The subsequent
[carriageway/direction increment](ROAD_CARRIAGEWAY_AND_DIRECTION_2026-10-07.md)
delivers provisional unsplit budgets, preserves published directional counts
and abstains on unresolved separate carriageways. Local annual traffic timing,
observed speeds, historical road/building compatibility and road-surface/ground/
barrier conventions remain open. The next bounded step is the documented
road-emission assumption protocol and sensitivity cases. That subsequent
[protocol increment](ROAD_EMISSION_ASSUMPTIONS_2026-10-07.md) is now delivered as
conditional source-power evaluation; the next open step is ground/barrier
conventions and a synthetic propagation canary.
The subsequent [10 October audit and canary](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md)
now complete those convention and limiting-case checks. Ground absorption,
standalone barrier evidence, road decks and exact acoustic obstacle heights remain
unqualified; that method checkpoint does not fill these input gaps.
A propagation experiment must state its remaining assumptions and test their
sensitivity before any absolute-noise or real-gap claim. Reported-only comparison
with existing modelled indicators will not independently validate the quiet end.
