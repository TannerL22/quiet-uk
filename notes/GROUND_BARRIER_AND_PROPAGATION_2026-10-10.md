# Ground/barrier audit and synthetic propagation checks - 10 October 2026

The convention audit and synthetic propagation canary are complete for the
existing north-Heathrow study. This establishes traceable construction and
limiting-case checks. It does not establish accurate real-area noise, qualify
unknown ground/barrier inputs, or fill missing map cells.

## Real input audit

The same 4 km2 core and 144 km2 source area remain fixed in EPSG:27700. All
722 retained provisional road representations were sampled at no more than
2 m along-line spacing using original containing-cell DTM/DSM values and masks.
No interpolation, resampling, source-height assignment or traffic expansion was
performed. The retained subset still represents only 10.42% of core road length.

| Check | Result |
|---|---:|
| Diagnostic road sample positions | 47,454 |
| Positions with original terrain and surface values | 47,440 |
| Clipped edge positions outside native pixel ownership | 14 |
| Negative DSM-minus-DTM differences retained | 13,266 |
| Surface excess at least 2 m, without object classification | 1,825 |
| Retained links intersecting generalised footprints | 1 link, 2 footprints |
| Fixed geometry paths crossing footprints | 69 of 100 |
| Fixed paths encountering withheld footprint heights | 32 of 100 |

The probes use a fixed 5x5 grid of 25 core locations and the four nearest distinct
retained road links at each location. They are geometry diagnostics, not selected
or validated noise receivers. Seven locations are inside generalised footprints
(28 of the 100 paths); that flag is retained. Sixty-eight paths have native terrain
above the straight ray under explicit at-grade source/4 m receiver assumptions.
Those sample counts are not proven acoustic occlusions: a 0.05 m source is small
relative to the 2 m horizontal raster support, containing-cell steps differ from
the real road surface, and actual road deck/grade is unknown.

All 50,495 footprint states remain intact: 37,167 provisional surface medians and
13,328 withheld heights. No footprint has a qualified acoustic obstacle height.
The native edge samples remain outside, with null elevations; no value is copied
from an adjacent cell. There is no interior missing terrain at these sample
positions. Negative surface differences are diagnostics, not silently clamped.

## Frozen conventions

- Keep BNG horizontal coordinates and ODN vertical elevations. Terrain, road
  deck, roof top, height above ground and equivalent height above a mean plane
  are different quantities. An arbitrary terrain cell plus a footprint median
  does not establish a roof edge.
- Place the nominal road source 0.05 m above the actual road surface. All 722
  retained links have an unspecified structure field; that does not prove they
  are at grade. All 14 explicitly marked tunnels in the wider ledger are already
  excluded. Bridges/viaducts require deck elevations; tunnels require special
  modelling. The source-elevation helper refuses terrain substitution without
  an explicit at-grade assumption or supplied deck elevation.
- Ground factor G describes acoustic ground treatment, not raster elevation.
  No local off-road G is assigned. In the checked method road platform Gs=0;
  pavement/local propagation corrections belong in emission. Add no independent
  extra road image or reflection gain on top of the boundary calculation.
- A horizontal-edge diffraction branch includes its source-side and
  receiver-side ground coupling; its separate ground term is zero. Only the
  main horizontal-edge diffraction term is capped at 25 dB; the image terms
  used for coupling remain uncapped.
- An encountered footprint with withheld height means unknown shielding.
  It cannot be removed, given zero height or replaced with a typical height.
  DSM excess includes unclassified vegetation/vehicles/structures; it cannot
  automatically become a noise wall. Standalone barrier absence is unknown.
- Reflections, receiver own-facade treatment, finite/thick buildings, lateral
  paths, transmission and multiple obstacles need separate qualification.
  Source-halo size must be tested, not treated as an acoustic cutoff.

Equations and figures were checked against [JRC72550](https://publications.jrc.ec.europa.eu/repository/handle/JRC72550)
(VI-12, 14-18, 21-22, 30-32), including visual checks of PDF pages 86-88 and 91-93.
The [2021 amendment](https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj/eng)
adds the Rayleigh condition to the unobstructed-path selector. The canary tests
that condition explicitly. It follows the checked 2012 source-side ground text
and does not claim a complete current regulatory implementation. Remaining
method versions and interactions require qualification before real-area use.

## Ground and barrier data leads

The [2021 GB 10 m UKCEH classified raster](https://catalogue.ceh.ac.uk/id/a22baa7c-5809-4a02-87e0-3cf87d4e223a)
is a credible temporally relevant ground-classification candidate. It is not an
acoustic impedance/G measurement. Detailed classes, mixed urban pixels and
compactness require a justified acoustic crosswalk and sensitivity checks.
The [provider licence](https://eidc.ac.uk/licences/lcm-raster/plain) has personal/
non-commercial and derived-product conditions, with separate commercial terms;
this audit downloaded documentation only. It did not ingest or commercially
qualify the raster. The [production/validation paper](https://essd.copernicus.org/articles/15/4631/2023/)
is retained with its separate CC BY 4.0 attribution. Its quoted national accuracy
describes the validated land-parcel product, not local acoustic ground accuracy.

[National Highways](https://nationalhighways.co.uk/our-contribution/protecting-our-environment/environment/communities/noise/)
describes noise barriers made from several materials. Bounded discovery did not
qualify historical local geometry, top/base elevations, material or acoustic
properties. That is a gap in this audit's evidence, not proof that no dataset or
barrier exists. No national download or speculative barrier extraction was made.

## Synthetic experiment and verification

The test uses exact flat mean planes, homogeneous atmospheric conditions, source
height 0.05 m, receiver height 4 m, horizontal distance 100 m and one infinitely
wide opaque thin screen at 40 m. Eight bands span 63 Hz to 8 kHz. Four fixed G
cases and absent/zero/1/2/3/6/60 m or unknown screens produce **264 scenarios**:
**224 conditional results and 40 withheld unknown-ground/height results**.
These are arbitrary spectral source powers and transfer ratios, not dBA or annual
transport indicators. No real traffic, source scenario or reported noise raster
enters the numerical kernel. Atmospheric absorption is zero for analytic isolation;
weather averaging and realistic building/path search are outside this canary.

Checks establish -6.0206 dB per doubled point-source distance, +3.0103 dB for two
equal incoherent sources, and the -3.0103 dB infinite-line distance limit. Discrete
line sources conserve metres of source power and converge to the exact integral.
At 0.5 m spacing the controlled segmentation error is 1.90e-7 dB. The kernel uses
the published rounded +11 dB divergence constant, with its 0.00879 dB difference
from exact 10log(4pi) stated explicitly.

At 1 kHz a 3 m synthetic screen changes boundary attenuation from -3 to 4.618 dB
on G=0 ground (7.618 dB insertion loss), and from 3.275 to 7.152 dB on G=1 ground.
Ground/screen effects interact by frequency; adding a barrier is not a universal
fixed subtraction, and the hard/porous cases are not guaranteed uncertainty bounds.

The unattenuated finite-line margin exercise shows a 5 km half-length deficit of
0.014, 0.056 and 0.583 dB from the ideal infinite line at perpendicular distances
25, 100 and 1,000 m. This is a calculus stress test, including distances beyond
the legacy report's 2 km elementary-path scope. It is not a CNOSSOS application,
real-network tail bound or proof that the existing 5 km acquisition halo suffices.

The independent verifier imports neither the evaluated propagation functions nor
the audit script. It checks **94,908 native elevation slots**, including **94,880
original numeric values**, all 722 road memberships, original footprint states,
100 probe obstacle sets/native samples/ray diagnostics, 224 scalar boundary
results and 40 abstentions. Maximum scalar boundary difference is **1.78e-15 dB**.
Fifteen independent adaptive line integrals agree within **4.44e-16 relative**.
This proves export/equation fidelity, not independent noise accuracy.

**249 related tests pass**, including 53 propagation checks. Both numerical and
geometry figures were visually inspected. No production map, launcher, existing
noise raster or earlier completed input inventory was modified.

## Artifacts and reproduction

| Immutable generation | Identity |
|---|---|
| `ground_barrier_method_review_v1` | `ground-barrier-method-cf6611e041658e25050a` |
| `ground_barrier_audit_v1` | `ground-barrier-audit-4c36cddddc1b2e06251e` |
| `synthetic_propagation_v2` | `synthetic-propagation-c2b328b6cf0c2833bcc4` |
| `propagation_review_v2` | `propagation-review-f72261879f4a990c32d6` |

The audit contains conventions, all road-native samples, per-link physical flags,
probe paths and a report. The canary contains the frozen protocol, all boundary,
margin and segmentation cases and its numerical figure. The review contains the
independent receipt and geometry figure. Inventories retain file/code/parent
hashes and licences/credits. V1 canary/review remain immutable; V2 adds explicit
audit selection/reproduction support and more probe verification without changing
the numerical scenario, margin or segmentation outputs.

```powershell
# Uses retained immutable physical, road and method inputs. Fresh outputs only.
.venv\Scripts\python.exe scripts\54_audit_ground_and_barriers.py --output artifacts/ground_barrier_audit_NEW
.venv\Scripts\python.exe scripts\55_run_synthetic_propagation.py --audit artifacts/ground_barrier_audit_NEW --output artifacts/synthetic_propagation_NEW
.venv\Scripts\python.exe scripts\56_verify_propagation_canary.py --audit artifacts/ground_barrier_audit_NEW --canary artifacts/synthetic_propagation_NEW --output artifacts/propagation_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/propagation_pytest_NEW
```

The method generation retains original retrievals and the already acquired JRC
PDF identity. Runtime and all parent inventories are recorded; test receipts are
`artifacts/propagation_validation_v2.xml` and `.json`. Artifacts remain local and
excluded from Git; these files require a separate backup.

## Next bounded step

The [subsequent land-cover/barrier increment](GROUND_COVER_AND_BARRIER_INPUTS_2026-10-10.md)
now completes the bounded 2021 native class extract and conditional crosswalk,
retaining historical barrier candidates and their unknown acoustic properties.
Next qualify terrain profiles/mean planes on controlled slope/ridge scenes.
Complete the compatible propagation-method chain and independently
benchmark it before real-area application. Source deck heights, historical
road/building compatibility, annual local timing/speeds/pavement, weather and
missing network contributions still need explicit treatment. A reported-only
comparison can follow those gates; it will not independently validate the quiet
end or justify labelling missing cells as quiet. The provider enquiry's last
recorded acknowledgement is unchanged; this increment did not check for a reply.
