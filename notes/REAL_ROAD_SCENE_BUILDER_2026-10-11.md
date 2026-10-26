# Real road scene builder — 11 October 2026

The geographic bridge between the retained real inputs and the tested propagation
engine is implemented. It prepares actual road source lines, terrain profiles,
building/barrier intersections and finite facade reflection plans, and exposes a
source-point callback for the road-line integrator. The offline result shows
which evidence is available and which missing inputs prevent each road's receiver
contribution. No new pilot, download or provider message was needed.

## Delivered outputs

The builder consumes the frozen physical-scene, land-cover/barrier, road-direction
and conditional-emission generations for the existing north-Heathrow study. The
core is 4 km², with the existing 144 km² acquisition halo. All original **13,928
road records** remain in the exported inventory: **722 retained provisional source
roads** and **13,206 roads without an accepted source budget**. Their original
withheld reasons remain visible. This retained subset cannot represent all-road
sound at the receiver; the halo is an acquisition boundary, not an acoustic cutoff.

All 722 retained source lines are prepared. Every original polyline turn is kept,
and each edge is subdivided into cells at most 25 m long. The result is **5,448
source cells**, conserving **92,722.618239 m** of retained horizontal road length.
Twenty-one lines extend partly outside the extract; their excluded length and
original geometry are recorded. Clipped lines are not assigned their original
unclipped length or power. No road vertices are snapped to receiver profiles.

Each physical representation retains its original carriageway budget and donor
identity. The published whole-road convenience total remains audit metadata;
components are consumed once. The **104 frozen anchor emission cases** remain
unchanged and are referenced by roads. They retain national timing and assumed
70 km/h/reference surface/classification, rather than claiming measured local
period traffic. Qualified source power per metre stays null. Summing traffic over
sequential links would count the same vehicles repeatedly and is not performed.

For the existing core-centre position `(507000,179000)`, the run prepares **722
direct geographic snapshots** and **1,418 reflected profile candidates**. The
source point in each snapshot is the closest point on that retained road. This
selection audits geographic readiness; it does **not** approximate the whole road
with that one source point or execute real source-line quadrature. Source-line
cells and the arbitrary-source-point callback provide the integration interface.
The declared source height is 0.05 m above containing-cell terrain and receiver
height is 4 m; actual road surface/deck and receiver outdoor status remain unknown.

The run observes **32,092 direct building-footprint crossings** and **1,514 direct
barrier-candidate intersections**. These are geometric feature/path incidences,
not counts of unique verified screens. Native terrain support is complete for
all direct snapshots. Roof and screen top elevations, opacity, roof G and acoustic
absorption remain unknown; provisional DSM–DTM heights are retained as separate
evidence and never promoted to exact acoustic tops.

## Implementation and input boundaries

`src/quiet_uk/road_scene_builder.py` contains the native terrain adapter, road
subdivision, spatial indexes, direct/reflected profile preparation and integrator
callback. `scripts/81_build_real_road_scenes.py` loads the retained inputs and
writes the offline source inventory, scenes and readiness tables. No serving or
map UI changes occur in this increment.

Terrain rays cross native 2 m cells using exact represented lengths and original
values/masks. Zero and negative elevations remain valid. Missing/outside support
remains null. A separate profile connects sampled cell-midpoint ordinates and
endpoint cells; that is a declared piecewise-linear reconstruction, **not** a
claim that raster steps are real diffracting terrain corners. It remains
unqualified for real acoustic use. The WorldCover raster stays in its original
EPSG:4326 grid with the recorded BNG coordinate operation. Ground class sampling
uses full-length midpoint intervals at most 2 m, retaining containing cells and
unknowns. It does not locate exact class boundaries or assign observed acoustic G.

Footprint intersections preserve separate parts and courtyard holes. Barrier
contacts and collinear overlaps are retained. Endpoint footprint intersections
are flagged without relocating source or receiver. Generalised exterior footprint
edges supply possible facade segments. A finite horizontal specular plan is
checked before constructing and unfolding the two physical legs. The reflector
top is never inserted into the terrain profile.

Reflection preparation uses a 100 m extra plan-length limit, a 20,000-candidate
mirror-solve budget after conservative geometric filtering, and at most two
prepared profiles per snapshot. It found **5,933 eligible finite plan candidates**;
**4,515** remain unprepared under the output cap. None exceeded the calculation
budget. These are explicit preparation choices, not proven acoustic distance
cutoffs. Coincident facade planes retain all source identities while producing
one candidate, avoiding duplicated reflected energy. Facade historical geometry,
wall height/base, inclination, incident-view dimensions, opacity and absorption
remain unqualified. Reflection support is consequently never marked complete.

Actual data always produces `scene_kind=unqualified`; synthetic controls can only
be applied to an explicitly synthetic terrain fixture. The real-data loader
cannot silently enter the synthetic numeric route. The tested line adapter is
connected in a controlled geographic example, including source length, attenuation
and atmospheric absorption. Real annual/source-isolated receiver contributions
remain null for all 722 roads and for the combined road result.

## Independent verification

**791 related tests pass**, including 24 new scene-builder tests. They cover native
mask and datum handling, length/power conservation, turns and reversal, courtyard
holes, unknown barriers, endpoint conflicts, finite reflection unfolding,
coincident facade identities, resource caps and the complete controlled geographic
builder → propagation → road-line quadrature interface. The controlled receiver
integral agrees with separate adaptive quadrature within its declared 0.001 dB
numerical target. Numerical agreement does not establish field accuracy.

Script 82 never imports the evaluated builder or runner. It reconstructs source
membership and traffic cases from the originals, uses scalar closest-point
projections, analytic ray/cell slab intersections, native raster/mask equality,
independent categorical sampling, raw obstacle intersections, and a
receiver-mirror/2×2 finite-wall solution. All checks pass:

| Independent check | Count |
|---|---:|
| Source roads / source cells | 722 / 5,448 |
| Geographic profiles | 2,140 |
| Native terrain intervals | 7,102,107 |
| Native ground samples | 5,367,401 |
| Raw obstacle crossings | 102,005 |
| Finite reflection plans | 1,418 |
| Road-location snapshots and withheld contributions | 722 |

Parent inventories, preceding production/reference/test files and frozen
traffic/emission data remain byte-preserved. The geographic diagnostic figure was
visually inspected. Full current-method qualification, external engine execution
and independently validated real-area sound accuracy remain false.

## Artifacts and reproduction

Current completion evidence is:

| Generation | Identity | Inventory SHA-256 |
|---|---|---|
| `real_road_scenes_v2` | `real-road-scenes-03b103f52d10e958e51e` | `c2f36a7129a9a4081c3f0a62235e81bacaa6f52a79a3e976b8cfe77aa8f642d4` |
| `real_road_scene_review_v1` | `real-road-scene-review-6baf253084e5531e8666` | `412d33b2de1cd8687af4aa7756169354a4497c4bd815d601f5d89f59bcd4f6f3` |
| `real_road_scene_figure_v1` | `real-road-scene-figure-7557bbefe25d2951f2a1` | `2abd678955f588ffb2cff0a76b2d03e0932e67b9e27cc0737623b17f6200495b` |

`source_lines.json` holds the source cells, original budgets and emission-case
references. `road_input_inventory_bng.csv` is an unchanged copy of the complete
source ledger. `prepared_paths.jsonl.gz` streams geographic evidence without
loading every scene at once. `road_location_readiness.json` and `.csv` list per-road
blockers and null receiver contributions. Protocol, report, code and inventories
accompany them. The development generation v1 is preserved but is not completion
evidence. JUnit/hash receipts are `artifacts/real_scene_validation_v1.{xml,json}`.
Artifacts are local and Git-ignored; a source push is not a backup of these inputs.

Reproduce offline into fresh outputs:

```powershell
.venv\Scripts\python.exe scripts\81_build_real_road_scenes.py --output artifacts/real_road_scenes_NEW
.venv\Scripts\python.exe scripts\82_verify_real_road_scenes.py --inputs artifacts/real_road_scenes_NEW --output artifacts/real_road_scene_review_NEW
.venv\Scripts\python.exe scripts\83_plot_real_road_scene.py --inputs artifacts/real_road_scenes_NEW --output artifacts/real_road_scene_figure_NEW
.venv\Scripts\python.exe -m pytest tests/test_road_scene_builder.py tests/test_screened_reflection.py tests/test_road_line_integration.py tests/test_reflection_path.py tests/test_favourable_propagation.py tests/test_sightline_boundary.py tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/real_scene_pytest_NEW
```

## Remaining input work

**11 October follow-up:** the [traffic, speed and weather input bundle](ROAD_OPERATING_INPUTS_2026-10-11.md)
is now delivered, with original Heathrow 2021 weather, the unexamined donor-count
gap closed, and explicit national/reference transfer assumptions for all 722
retained representations. All 824 related tests and independent source/equation
checks pass. The original scene generation above remains unchanged. The subsequent
[physical-scene assumption policy](PHYSICAL_SCENE_ASSUMPTION_POLICY_2026-10-11.md)
adds declared physical candidates and 15-case ground/height/screen diagnostics;
all 857 related tests pass. Conditional real-scene propagation and supported
source-line integration are now the next implementation work. The paragraph below
records this scene builder's original handoff.

The next work is the real-input qualification and assumption policy: pursue local
annual period traffic/speeds and matched weather evidence; resolve road surface/
deck and historical geometry; make ground/height choices explicit, evaluate their
effect, and compare against independently matched acoustic evidence. Terrain,
footprint and land-cover availability are now connected to the engine, but their
acoustic interpretations still require that work. Unbudgeted roads, source-margin
effects, gradients/junction dynamics, incomplete barriers/reflections and remaining
method scope cannot be hidden inside a claimed complete road-noise total.
