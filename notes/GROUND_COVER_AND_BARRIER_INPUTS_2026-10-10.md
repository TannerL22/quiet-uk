# 2021 land cover and historical barrier inputs - 10 October 2026

The bounded input increment is complete for the fixed north-Heathrow study:
**2021 native land-cover classes across the 144 km² source halo**, an explicit
conditional acoustic crosswalk, and historical wall/fence/embankment candidates.
The extract preserves original class values and masks. Acoustic ground factors,
qualified noise barriers and real-area noise predictions remain unestablished.

This follows the [ground/barrier convention audit and synthetic canary](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md).
The core remains BNG `(506000,178000,508000,180000)`; the 5 km acquisition halo
remains `(501000,173000,513000,185000)`. That halo is not a proven acoustic cutoff.

## Source acquisition and licensing

The [UKCEH 2021 GB 10 m raster](https://catalogue.ceh.ac.uk/id/a22baa7c-5809-4a02-87e0-3cf87d4e223a)
is still a useful lead, but the native download redirects to EIDC login. It was
not acquired. Its [raster licence](https://eidc.ac.uk/licences/lcm-raster/plain)
also needs separate consideration for commercial/derived-product use. A display
WMS was not substituted for native class data.

Instead, the openly accessible [ESA WorldCover 2021 v200](https://esa-worldcover.org/en/data-access)
provides a reusable classification source under CC BY 4.0. The original
`N51W003` GeoTIFF (107,959,080 bytes), official tile grid, Product User Manual,
Product Validation Report and source metadata are retained. The product citation
is Zanaga et al. (2022), [ESA WorldCover 10 m 2021 v200](https://doi.org/10.5281/zenodo.7254221).
The map attribution is:

> © ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.

The original raster tags establish product version V2.0.0 and observation period
1 January–31 December 2021. Its creation timestamp is 21 October 2022. Publication,
observation and retrieval dates remain distinct. Provider tables and the
[JRC 2012 ground table](https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf)
(PDF p86–87) were checked from original PDFs, including visual table inspection.

The ESA manual describes an InputQuality product at 1/2000 degree with observation
counts and invalid-observation percentages. It is not a class posterior. The
public S3 input-quality prefix listing did not contain this tile, so no local
quality layer was acquired. This establishes only that retrieval route's result.
The provider's global 76.7% and European 77.9% overall classification accuracy
in [PVR table 3](https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/docs/WorldCover_PVR_V2.0.pdf)
does not establish local pixel accuracy, acoustic uncertainty or probabilities.
2020 v100 and 2021 v200 use different algorithms; they are not a pure change map.

Two historical [OpenStreetMap](https://www.openstreetmap.org/copyright) snapshots
are retained under ODbL 1.0, with © OpenStreetMap contributors attribution. Original
responses, geometry, tags, versions, edit timestamps, requested snapshot times,
queries and failures are preserved. ODbL terms accompany these separate candidate
records; no unrestricted redistribution claim is made for a combined database.

Acquisition made **12 bounded requests**, receiving **116,882,867 bytes** within
the 24-request / 256 MiB cumulative budget. Both first Overpass endpoints returned
HTTP 500; the alternate endpoint returned complete responses without error
remarks. The failure bodies and receipts remain in the sealed source generation.
No account creation, provider messages or paid purchases were performed.

## Native classes and conditional ground interpretation

The source grid is EPSG:4326 with native spacing **1/12000 degree**, approximately
**5.79 m east–west × 9.27 m north–south** at this study's centre. It is not a 10 m
British National Grid raster. The bounded integer window is **1,323 × 2,119**
pixels, including a small rectangular fringe. All **2,803,437 values and masks**
agree with the original tile. No reprojection, interpolation or class smoothing
was used. A separate native-grid membership mask identifies pixel centres inside
the BNG halo without changing the original source mask.

There are **2,685,220 native pixel centres inside the halo**, and **74,597 inside
the core**, all with a recognised provider class and valid mask. This is complete
classified support in this extract, not complete acoustic knowledge.

| Class | Core native centres | Share of core centres |
|---|---:|---:|
| Tree cover | 14,763 | 19.79% |
| Grassland | 23,714 | 31.79% |
| Cropland | 3,791 | 5.08% |
| Built-up | 31,660 | 42.44% |
| Bare / sparse vegetation | 518 | 0.69% |
| Permanent water bodies | 151 | 0.20% |

These are pixel-centre counts, not equal-metre area fractions. Zero remains
provider nodata and never becomes acoustic G=0. The pinned coordinate operation
is OSGB36/WGS84 operation (6), with a registry 2 m accuracy descriptor. The better
OSTN15 operation is unavailable in this runtime. The descriptor is not a local
error bound; the full pipeline and runtime are recorded for reproduction.

The crosswalk admits **conditional surface analogies**, not observed G:

- Grassland/cropland can test G=1 only under an explicit porous-soil assumption.
  Compacted grass/fields may require different ground treatment.
- Built-up can test G=0 only on an actual sealed hard ground surface. Roofs,
  mixed pixels and unknown road-platform width/deck geometry remain unresolved.
- Water can test G=0 only over the actual water surface; banks, mixed and seasonal
  pixels require separate evidence.
- Trees/shrubs, bare soil/rock, snow/ice, wetlands, mangroves and moss/lichen do
  not determine the substrate from their class alone. They stay ambiguous in the
  default analogy. For example, a tree canopy may cover paved ground or water.

G=0, 0.3, 0.7 and 1 are retained as declared stress cases, following the checked
ground descriptors. They are not calibrated probabilities or confidence bounds.
Source-platform/local pavement conventions remain separate, avoiding duplicate
emission/propagation corrections. Every observed/qualified G field remains null.

All **100 existing geometry probes** now have native containing-cell class
profiles at ≤2 m diagnostic midpoint spacing, with length weights that sum to
the whole path. Missing and ambiguous intervals are never omitted from the
denominator. There are **26,518 sampled positions**. Only 15 paths have classes
fully mapped by the declared surface analogy; those remain conditional and do not
qualify source decks, receptors, footprint crossings or annual soil conditions.

Refining midpoint spacing to ≤1 m changes class fractions by at most **3.85
percentage points total variation**. Declared ±2 m registration stress changes
up to **11.83% of midpoint classifications** on a path. These are geometry/class
diagnostics, not dB error or statistical confidence limits. They show why native
class support must not be promoted to fine-scale acoustic certainty.

## Historical barrier results

The way-only query covers any `barrier` tag, `wall=noise_barrier`, or
`man_made=embankment`, over a bounding envelope containing the entire halo.
Nodes, relations and unmapped structures are outside its inventory scope.

The snapshots contain **678 ways at year-end 2020** and **1,206 at year-end 2021**:

| Year-end 2021 category | Query records | Intersect halo | Intersect core |
|---|---:|---:|---:|
| Generic fence candidates | 562 | 522 | 9 |
| Generic wall / retaining-wall candidates | 189 | 183 | 1 |
| Embankment candidates | 24 | 24 | 0 |
| Other tags, without acoustic classification | 431 | 364 | 9 |

The remaining tags include hedges, guard rails, gates and kerbs. They are retained
as source evidence and are not automatically opaque acoustic screens. **171 ways
have positive explicit metre height tags**, but none qualifies an acoustic screen
height/top datum, sealing, absorption or transmission. Other units, ranges,
nonpositive values and absent heights remain withheld; no height is imputed from
DSM-minus-DTM or an ordinary material tag.

**592 ways have matching geometry/tags at both snapshots; 76 differ; 538 appear
only in the later snapshot; 10 appear only in the earlier one.** Matching edits
do not prove physical presence or unchanged conditions throughout 2021. The raw
history remains distinct from construction/removal dates.

No `wall=noise_barrier` ways were returned. **This does not prove noise barriers
are absent.** Sixteen probe paths intersect generic candidate lines; none intersects
a returned wall candidate. Qualified acoustic barriers remain zero and barrier
absence stays unknown on every probe.

The [National Highways hub](https://opendata.nationalhighways.co.uk/) was checked
using its official organisation and content group. Bounded `noise OR barrier`
searches returned four/three catalogue items, respectively, without a suitable
historical barrier asset layer. The searches do not establish that no authoritative
records exist. M4 scheme-document leads also need physical 2021/as-built evidence;
proposed mitigation cannot establish construction dates. A
[concrete asset-data request draft](HISTORICAL_BARRIER_DATA_REQUEST_DRAFT_2026-10-10.md)
is prepared and **unsent**.

## Verification and retained artifacts

**314 related tests pass**, including 65 new ground-cover safeguards. The independent
verifier imports neither the evaluated module nor the builder. It compares every
extracted class/mask, memberships/counts, 26,518 native samples, all 100 path
profiles and refinement/registration cases, and all 1,206 barrier records against
the originals. It verifies withheld states and checks parent hashes again after
completion. This establishes export fidelity, not independent acoustic, local
land-cover or coordinate accuracy. The class/candidate figure was visually checked.

| Completed generation | Identity |
|---|---|
| `ground_cover_sources_v1` | `ground-cover-sources-84bfc2fe3df30e09a523` |
| `ground_cover_inputs_v3` | `ground-cover-inputs-cd239be18bdf56bb3f83` |
| `ground_cover_review_v3` | `ground-cover-review-a973b6734dd3281e6e58` |

The sources contain complete originals, receipts and documentary evidence. Inputs
contain native class/membership rasters, crosswalk, path samples/profiles and the
barrier ledger. Review contains independent verification and
`ground_cover_and_candidates.png`. Each inventory records byte hashes; code,
runtime, parent generations, licences/credits and unknown states are retained.
Earlier V1/V2 input generations remain immutable; V3 pins the final edge guard,
explicit method-source lineage and documentary references. Source values, class
counts and path/candidate results are unchanged.

Offline reproduction uses retained source originals and fresh outputs:

```powershell
.venv\Scripts\python.exe scripts\58_build_ground_cover_inputs.py --sources artifacts/ground_cover_sources_v1 --output artifacts/ground_cover_inputs_NEW
.venv\Scripts\python.exe scripts\59_verify_ground_cover_inputs.py --inputs artifacts/ground_cover_inputs_NEW --output artifacts/ground_cover_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/ground_cover_pytest_NEW
```

For a fresh public-source acquisition, use script 57 with a new `--output`. Exact
remote reproduction depends on provider availability and retained product/history
versions. Test receipts are `artifacts/ground_cover_validation_v3.xml` and `.json`.
Artifacts are local and Git-ignored; they need a separate backup. No production
map, launcher, existing noise raster or earlier completed inventory was modified.

## Next bounded step

The terrain-profile/mean-plane step below is now completed in the
[terrain geometry report](TERRAIN_PROFILE_AND_PROPAGATION_CHECKS_2026-10-10.md).
Terrain diffraction and ground coupling are the next bounded method increment.

Implement and qualify the terrain-profile/mean-plane part of the propagation
chain against controlled sloped-ground and ridge scenes, with independent
benchmarks and explicit method version. Keep uncertain ground and obstacle cases
conditional or withheld. The authoritative barrier request can proceed separately
if the owner authorises sending it through a verified contact. Local annual
timing/speed/pavement, road decks, historical footprint compatibility, weather,
missing network contributions and full method qualification still precede any
credible absolute real-area noise release. The provider enquiry's recorded status
has not been checked or changed in this increment.
