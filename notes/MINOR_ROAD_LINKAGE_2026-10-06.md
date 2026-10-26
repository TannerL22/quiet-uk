# Minor-road count linkage — 6 October 2026

The next increment from the [road-model input audit](ROAD_MODEL_INPUT_AUDIT_2026-10-03.md)
is implemented: **15 of 17 unused minor-road points have provisional associations
to one road link each**. All 15 retain the official `Counted` flag and all ten
vehicle categories. Two points are withheld. This recovers official traffic
evidence for a later modelling experiment; it does not fill any noise-map gaps.

## Fixed scope and association rules

The offline pass reuses the frozen 4 km² north-Heathrow / West Drayton study core
and its 144 km² source area. It reads exact 2021 DfT annual average daily flow
(AADF) and the cached OS Open Roads geometry declared as April 2026. Original
files and the parent audit are checked by hash before and after the run.

Policy version 2 uses fixed engineering abstention rules:

- The nearest physical road must be a minor, single-carriageway link, with no
  declared special structure, and lie within 10 m of the count point.
- The projected count location must be at least 20 m along the link from either
  endpoint. The next nearby physical road must be at least 15 m farther away.
  Incompatible road classes and carriageway forms still compete in this check.
- The entire 25 m competition-search neighbourhood must lie within the loaded
  network bounds. The smallest margin among these points is 113 m.
- Existing direct assignments are protected. Multiple otherwise accepted counts
  on one link are all withheld. Counts are never spread to neighbours or used
  to replace a class median across unrelated roads.
- Dual/collapsed-dual carriageways, roundabouts, slip roads and special structures
  receive no new all-direction flow. General directional allocation remains an
  input requirement for a later emission calculation.

These distances are transparent presets, not calibrated match probabilities.
They were not relaxed to admit the point just outside the endpoint-clearance rule.

| Withheld point | Evidence and reason |
|---|---|
| `801296` | 0.77 m from a single-carriageway link, but only **19.44 m** along it from an endpoint; fails the 20 m clearance rule |
| `810964` | 5.62 m from a **Collapsed Dual Carriageway**; its representation/directional flow is unqualified |

The second point carries the source's `Estimated` flag. It was withheld for road
geometry, not for that flag; the implementation permits and preserves qualified
Estimated AADF as well as Counted AADF. Counted AADF is itself an annual estimate
informed by counts, not continuous observation throughout the year.

## Measured improvement

Lengths are clipped from native EPSG:27700 geometry, not inferred from link counts.
The original direct-attribution baseline remains separate from the additions.

| Road-length diagnostic | 4 km² core | 144 km² source area |
|---|---:|---:|
| Total network | 47.61 km | 1,258.89 km |
| Existing directly attributed length | 8.52 km (**17.89%**) | 128.45 km (**10.20%**) |
| Additional provisionally associated links | 1 | 15 |
| Additional provisionally associated length | **0.54 km** | **3.54 km** |
| Additional percentage points | **1.14** | **0.28** |
| Baseline plus provisional association length | **19.03%** | **10.48%** |

The core addition is count point `811912` on Harmondsworth Road, supporting an
association to a link with 542.54 m inside the core. The gain is useful but modest;
most road length still lacks directly attributed local traffic evidence.

The final row describes input availability **under a constant-flow link
assumption**. A count supports its location; it does not prove the flow along an
entire link. Associations between 2021 traffic and 2026 geometry remain
provisional until historical network compatibility and section identity are
established. No acoustic accuracy or independently validated traffic coverage
is claimed by these percentages.

## Outputs, identity and reproduction

Final generation: **`minor-links-db1f580ee65b8a313437`**, from parent audit
`road-inputs-29f17997cd45723bec73`. Outputs are in
`artifacts/minor_road_linkage_v3/`:

- `report.json`: all 17 decisions, candidate diagnostics, policy, clipped-length
  coverage, source/code hashes, runtime versions and limitations.
- `provisional_traffic_inputs.json`: the 15 associations, source quality flags,
  detailed methods, ten vehicle categories, point coordinates, road identities
  and source-year/geometry distinctions. Temporal flows and observed speed stay
  explicitly null.
- `associations.geojson`: the same accepted properties with WGS84 geometry for
  review. It is marked non-analytical; modelling must read the original hashed
  EPSG:27700 road geometry.
- `inventory.json`: the generation identity and checksums of the three outputs.

DfT and OS licences, source links and attribution travel with the outputs. OS
attribution is: “Contains OS data © Crown Copyright and database rights 2026.”
These artifacts are local and excluded from Git. The earlier v1/v2 generations
are retained iteration records; v3 is the final generation for this increment.

```powershell
.venv\Scripts\python.exe scripts\42_link_minor_road_counts.py --output artifacts/minor_road_linkage_recheck
.venv\Scripts\python.exe -m pytest tests\test_minor_road_linkage.py tests\test_road_input_audit.py tests\test_phase2c_road.py -q
```

Use a new output directory for each run; existing generations and frozen inputs
are not overwritten. The implementation is in `src/quiet_uk/minor_road_linkage.py`.

**48 tests passed**, covering ambiguity, incompatible competing roads, endpoint
and network-boundary checks, carriageway/structure exclusions, conflicting counts,
exact-year and invalid-traffic refusal, preservation of missing versus zero
vehicle categories, deterministic ordering and existing matcher regressions.
The live run also verified source/code and parent identities before and after.
Final checks verified every output checksum, preservation of accepted traffic
fields, unique link identities, protection of direct assignments and an independent
length sum from the frozen audit CSV. Export metadata changes left accepted IDs
unchanged from v2.

## Next prerequisite

The [timing and speed qualification](ROAD_TRAFFIC_TIMING_AND_SPEED_2026-10-06.md)
is now delivered: all 15 accepted points have complete 2021 survey-day counts,
while local annual noise-period flows and observed speeds remain unqualified.
AADF cannot silently supply night-time flow or an Lden profile. The next delivery
is full surrounding terrain with local survey dates and suitable building/DSM
evidence; historical timing/speed and structure/barrier/surface requirements
remain explicit. No new external enquiry was sent here.

The provider rasters, production matcher, launcher and explorer remain unchanged.
No dB predictions were generated. Historical geometry compatibility, propagation
requirements and independent quiet-end validation still gate any model displayed
in the product.
