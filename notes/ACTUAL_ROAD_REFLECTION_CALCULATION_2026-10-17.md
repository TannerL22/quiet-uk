# Actual road-line calculation with selected reflections — 17 October 2026

The next step after the closed numerical validation blocks is now running:
calculate the declared direct-plus-single-reflection model along the existing
real road lines at the existing receiver. This is a calculation run, not a new
pilot or another general propagation test programme.

Current work directory: `artifacts/real_road_reflection_calculation_v4`.
Its `progress.json` records completed roads and active source-point counts.
The directory is an **unsealed work journal until the calculation finishes**;
it must not be cited as a completed result or consumed by the map. At completion
the runner writes `road_results.json`, `report.json`, and an immutable inventory.
Preparation is caching each actual road's geometry partition first; a queued
two-worker calculation starts automatically when `workload_plan.json` is ready.
Queue state is outside the eventual immutable generation, at
`artifacts/real_road_reflection_calculation_v4_queue.json`.

## Fixed scope

- Existing receiver: BNG `(507000,179000)`, 4 m above containing-cell terrain.
- All 722 retained source roads remain in the result ledger. The 128 roads
  without a previous combined-reference direct component stay withheld,
  including the diagnosed roof/terrain conflict. The other 594 are eligible
  for actual calculation; new missing input or numerical holds remain possible.
- Existing `combined_reference` physical case and `reference_facade` case:
  octave absorption 0.2, vertical solid facades, flat candidate roof tops and
  explicitly assumed 1 m apparent incident-view dimensions.
- Existing traffic, vehicle speeds/classification, source emission and grouped
  hourly air transmission are unchanged. Each direct/reflected path uses its
  own 3D distance for the joint source/air power. H/F conditions stay separate;
  no missing annual favourable probability is invented.
- The existing 100 m extra-plan-length search limit and 20,000 mirror-solve
  budget are explicit resource/model limits, not acoustic cutoffs. No complete
  source/path support, field accuracy or all-inclusive value is claimed.

## Implementation changes for actual calculation

The old snapshot builder could prepare a facade approached from its building's
interior side. Most of the 823 own-footprint conflicts in the prior attachment
arose this way. The new run uses polygon-ring orientation to keep only the
strict exterior half-plane for both endpoints, for every coincident facade
identity. This is a geometric filter under the declared opaque-building model.
It does not assign a small sound value to unknown facade properties.

The two-profile preparation cap is removed. Every eligible outward-facing
facade inside the declared detour is prepared, subject to the unchanged
mirror-solve budget. Unknown wall geometry, overlapping/ambiguous paths,
terrain/roof conflicts and exhausted search support withhold the component.
The existing acoustic kernels and all 92 files bound by the completed
reflection-connection receipt remain unchanged.

The first real-road execution identified a representational contact sliver:
the captured interval `[4073.1205847292235,4073.12058472925]` is only
`2.637534635141492e-11 m` long, at an outward-facing reflector hit. This is below
the existing coordinate-precision convention of 64 ulps of the world coordinates
(`3.725290298461914e-9 m` in that example). The run now reconstructs an interval
as a contact only when its complete extent lies within this precision bound of
an interior-of-facet hit and the search already established strict exterior
approach. Original interval ordinates are retained separately. Corners, larger
roof extents, nonreflecting buildings and all terrain elevations are unchanged.
This does not relax the roof/terrain consistency rule or recover the preexisting
height-conflicted road. The stopped unsealed v1 run is development evidence only;
its observations/results are not mixed into the current run.

Source partitioning adds crossings of facade half-planes, rays from the mirrored
receiver through finite facade endpoints, and roots of the 100 m detour-limit
equation. This prevents a narrow finite reflection interval from disappearing
between initial source samples. These cuts supplement existing source turns,
terrain-cell boundaries and receiver/obstacle geometry cuts. They do not prove
that every terrain, ground or acoustic branch transition is enumerated.

Each source cell is first clipped to the actual finite-facade visibility cone.
Extended wall planes/rays that cannot produce a finite reflection do not create
partitions. That interval is then clipped to subintervals satisfying the actual
100 m detour inequality; a finite mirror cone that exceeds the detour everywhere
also adds no source panels.
This corrects a measured preparation overhead: the first 100 roads in the
unpruned v2 workload plan would have required more than 1.17 million primary/finer
positions. The stopped unsealed v2 run is also development evidence, not a
completed calculation. The further stopped v3 run is preserved in the same way.
For the same first 100 planned roads, v4 reduces the full primary/finer position
count to **254,259**, about **78% fewer** than v2's 1,170,378. Actual finite
reflection transitions remain partitioned. Earlier completed development-road
comparisons differed by less than 0.001 dB after removing irrelevant partitions;
none is substituted for the current complete fixed run.

The remaining work is still substantial. Early node throughput and the geometry
workload imply **several days, potentially a week or more**, with high uncertainty
until the complete workload and actual input holds are known. This is a laptop
compute-time estimate, not a numerical confidence bound. Physical holds can end
a road early; adaptive refinement can add work. The run does not start new
scientific scenarios or a new reference-test programme while it is calculating.

The existing adaptive positive quadrature is reused with a 4 m maximum initial
panel, 0.01 m minimum child panel, 0.1 dB indicator and 512 refinement budget.
Each road's observation cap covers its entire initial partition plus at most
5,120 refinement observations. A primary-met road also receives four interior
positions on every accepted leaf, comparing H/F day/evening/night/Lden changes
against 0.1 dB. Failure leaves no accepted whole-line component. These are
calculation acceptance diagnostics, not rigorous noise-error bounds.

Actual point energies and road weights receive scalar source × path-energy
accounting. Direct and selected-reflected components are integrated at the same
positions, so any reported reflection increment compares like sampling with
like sampling. It is not a comparison of a newly refined total with an older,
coarser direct-only estimate.

## Restart recovery

Two bounded local workers calculate roads in increasing source-length order.
Every road remains scheduled; the ordering makes early progress visible rather
than defining a new study area.

Source-point observations are journalled immediately and moved into atomic,
closed gzip chunks. A partial final JSON line is not accepted as an observation.
Cached points are reused only when both coordinate floats match exactly and
the run fingerprint agrees. The fingerprint binds code, input inventories,
local input copies, declared policy and numerical runtime versions.

Completed road results are written atomically. After interruption or laptop
restart, resume with:

```powershell
.venv\Scripts\python.exe -u scripts/119_calculate_real_road_reflections.py --resume
```

Run this from the inner `quiet-uk-starter` repository. Resume preserves completed
roads and replays interrupted numerical integration using the exact-coordinate
observation cache. It refuses a sealed generation or changed code/input/runtime.
Use a fresh output directory for an intentionally changed model.

Nine small controller checks passed before launch: both polygon-ring directions
select the same exterior facade, a narrow finite reflection interval is split,
irrelevant extended facades add no source panels,
finite facades beyond the declared detour add no source panels,
only the precision-sized contact sliver is normalized, corners/real extents stay
unchanged,
exact-coordinate recovery excludes partial records, and a changed run identity
is rejected. Their record is
`artifacts/real_reflection_controller_check_v7/report.json`. The previously
completed 221-check / 448-public-comparison / 96-controlled-integral validation
was not repeated or expanded into a new physics test programme.

## Meaning of the results

A numeric road value will be a **conditional selected-path component**. Missing
roads and withheld components are never inserted as zero. Any aggregate is only
the known accepted subset; complete road-network and all-inclusive values stay
null. The 13,206 original roads without accepted source budgets remain visible
in the copied source-input inventory. Rail and aircraft are outside this run.
The installed map remains unchanged.

The completed source-line calculation will make the existing method usable on
actual inputs. Its finite inventory, unverified facade/traffic/physical
assumptions and missing source/path support still limit what can be presented
as accurate noise at a place.
