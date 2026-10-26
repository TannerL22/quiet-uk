# Data-first addition and gap evaluation — 2 October 2026

**3 October update:** the owner approved the provider-access step. A focused
[Defra data request](DEFRA_DATA_REQUEST_2026-10-03.md) has now been sent; a reply
and any new dataset remain pending. The original broader technical draft is
retained, unsent, for follow-up.

The owner authorised qualified real data before credible estimates where evidence
is unavailable. This increment acquires a small openly licensed acoustic dataset,
adds useful observed-sound context to the explorer, and measures interpolation
baselines without assigning predictions to real unknown cells.

## Additional evidence installed

[International Soundscape Database, pinned Zenodo record 10672568](https://zenodo.org/records/10672568),
version 1.0.1-alpha.1, CC BY 4.0, Mitchell et al. The source snapshot contains the
2,077,871-byte data CSV, 5,114,380-byte metadata workbook and official record JSON:
**7,208,697 bytes in total**. HTTP identities, acquisition dates, SHA256 hashes
and provider MD5 checksums are preserved. Audio archives and the optional London
meter-log archive were not downloaded.

The [published protocol](https://doi.org/10.3390/app10072397) describes calibrated
binaural recordings and sound-level-meter collection. This release uses the CSV's
`LAeq_L(A)` acoustic-recording metric, **not meter-log time series**. Protocol-level
calibration is reported; individual recording calibration records have not been
verified. Receiver height and numerical measurement uncertainty remain unknown.

Qualification retains **632 distinct acoustic recordings at 11 London sites**,
associated with **2019 survey dates**, with durations **17.42–74.62 seconds**.
Source row numbers, session IDs, GroupIDs and all associated survey dates are
retained. Survey dates are not claimed as exact recording timestamps. The source
contains 1,171 selected London survey rows sharing 732 GroupIDs; one acoustic
recording is retained per GroupID, not one per respondent. One conflicting group
(`ET203`), `UNKNOWN` and groups lacking valid acoustic values/durations are
withheld: **100 groups in total**. This is not a sample of independent locations.

Positions come from the author's approximate site coordinates in the workbook,
rather than differing respondent coordinates. StPaulsRow's published row omits
the `Lon:` label; its literal final-line longitude `-0.0991` is retained through
a narrowly identified parsing exception, documented in its position support.
The original workbook is unchanged. Source demographics are excluded from the
derived product and download.

The qualified release is `artifacts/recorded_sound_v3`, identity
`observations-0a28ddfdd6f45da21bae`. Original acquisitions are in
`artifacts/independent_evidence_v1/source`. Earlier v1/v2 derived directories are
development candidates; the launcher installs only v3.

## Product behaviour

**Recorded sound samples** is an optional panel with purple `R` map markers and
a keyboard-accessible location selector. Each site shows the min/max range across
its recordings, distinct count, historical survey year and duration range. This
range is neither an uncertainty interval nor a typical/annual level. The layer
contains mixed environmental sound; it cannot isolate road, rail or aircraft.

Selecting a recorded site also inspects the native transport grid at the
approximate site coordinate. The two kinds of evidence keep their own labels and
values. Selecting another point clears the recording card. The display keeps its
viewport image budget, and only 11 lightweight recording markers are added.
View links preserve recording release/site/marker visibility as well as the
existing camera, point and source selections.

`/api/observations` serves verified site context; `/downloads/observations.json`
exports the qualified observations, site features, provenance, exclusions and
source hashes. Small release bytes are verified once and held in memory. Later
edits to source directories cannot change the running server's recording bytes.
The existing loopback Host/Origin restrictions apply; arbitrary queries are
rejected. Missing recording installations do not prevent normal regional startup.
An explicitly installed corrupt recording release fails startup verification.
The launcher checks interface **4** and the installed recording identity.

## Gap experiment and result

`scripts/39_evaluate_gap_baselines.py` reads the installed, pinned native road
Lden/Lnight grids without editing them. `artifacts/gap_evaluation_v2/report.json`
contains verified source hashes, the selection/algorithm recipe, 100 core
inventories and 36 method/size/stratum summaries. `heldout_patches.csv` preserves
individual patch errors. Construction code hashes and Python/native library
versions are in `artifacts/data_first_delivery_checks/construction-provenance.json`.
The first v1 development evaluation used interior values to stratify patches;
v2 uses the visible perimeter only and is the reported result here.

Inventory uses eight-connected unknown components inside each 1000 × 1000 core.
Components touching a core border are excluded from enclosed-hole totals: their
full shape is not established within that core. No domain or censoring authority
is inferred from connectivity. Perimeter levels do not become upper bounds.

| Road indicator | Reported cells | Unknown cells | Enclosed unknown components | Enclosed components whose reported ring stays in lowest 5 dB band |
|---|---:|---:|---:|---:|
| Lden | 38,932,284 | 11,067,716 (22.14%) | 74,707 | 17,802 |
| Lnight | 30,876,719 | 19,123,281 (38.25%) | 128,361 | 32,104 |

Low rings mean reported neighbours at most 45 dB for Lden or 40 dB at night.
They are candidate geometry, not certified quiet interiors. Unknown inventories
retain zero-coded and nodata/nonfinite counts separately.

Validation hides entire **30, 90 and 210 m squares**, never isolated pixels.
It chooses 12 systematic spatial cores, eight seeded nonoverlapping patches per
metric/size/stratum/core: **1,152 patches**, each evaluated by three fixed methods.
The target interior is inaccessible to predictors. All perimeter cells must be
reported. Baselines are nearest neighbour, inverse-square-distance interpolation
using eight perimeter neighbours, and discrete harmonic interpolation in dB.
None is an acoustic propagation model. Parameters were fixed before scoring.

Harmonic interpolation had the lowest RMSE in the higher reported stratum for
all six metric/size groups in this run:

| Hidden square width | Road Lden RMSE | Road Lnight RMSE |
|---|---:|---:|
| 30 m | 1.58 dB | 1.49 dB |
| 90 m | 2.86 dB | 2.13 dB |
| 210 m | 3.62 dB | 3.22 dB |

The 210 m harmonic maximum absolute cell errors reached **23.62 dB Lden** and
**25.38 dB Lnight**. In the near-floor *reported* stratum harmonic RMSE was
0.19–0.26 dB, but this selects smooth, entirely reported patches; it supplies no
below-cutoff ground truth. Choosing a winning baseline here is not deployment
approval or independent validation.

The reference is the same annual noise model, not field measurements. Entirely
reported squares with complete perimeters are optimistic support. Sizes can
sample nearby locations; no independent sample count, statistical confidence
interval or calibrated prediction interval is claimed. All three interpolators
obey perimeter extrema, so they cannot establish a quieter interior than the
surrounding reported minimum. Real noise also depends on emissions, barriers,
buildings, terrain and meteorology, which these interpolators do not model.
**No actual gap prediction is exported or shown in the product.**

## Reproduction and verification

From the repository root in a configured environment:

```text
python scripts/37_acquire_independent_evidence.py
python scripts/38_build_independent_observations.py --output artifacts/recorded_sound_v3
python scripts/38_build_independent_observations.py --output artifacts/recorded_sound_v3 --verify
python scripts/39_evaluate_gap_baselines.py --output artifacts/gap_evaluation_v2
python scripts/29_serve_explorer.py
```

Use a **new output directory** when the builder/evaluation destination already
exists. Acquisition can resume verified completed files; interrupted `.part`
files are retained for inspection. Research evaluation needs SciPy via the
research extra. The recording UI and server use only standard-library additions.
Generated datasets are ignored by Git and need their own backup.

The offline suite passed **544 tests**, with three expected skips and three
release-data tests deselected; nine browser display-policy tests passed. New
tests cover recording deduplication/conflicts, invalid values, immutable serving,
manifest/payload tampering, HTTP contracts, acquisition byte/checksum limits,
gap connectivity, whole-patch leakage prevention, missing perimeters and a
high interior value surrounded by low reported values. Launcher PowerShell and
browser JavaScript syntax checks passed.

Browser checks completed 3 October using a separate loopback review server:
1440 × 900 desktop and 390 × 844 mobile viewports, 11 recording markers,
keyboard site selection, source/native value separation, camera/site/release and
hidden-marker restoration, and clearing a recording card on another point
inspection. There was no horizontal overflow or browser console error. At the
checked detailed desktop view the imagery used two images / 3,254,922 decoded
pixels; the recording markers did not change that loading policy. The JSON
download contained all 632 observations and exactly matched installed values
(SHA256 `8d984d786eb5d6d3785f1a41c9b85af13868d670280486a438a2cc06f3c8a6b7`).
Its verification is in `artifacts/data_first_delivery_checks/export-check.json`;
the desktop screenshot is in that folder as `recorded-sound-desktop.png`.
Verified startup took 38.77 seconds in this run. These checks are browser
emulation, not testing on physical phones or a general usability study. The
temporary review server and its private snapshot were closed after verification.

## Next evidence work

Continue with openly reusable, well identified airport/country GIS data from
[the primary-source inventory](NOISE_DATA_AND_ESTIMATION_RESEARCH_2026-09-29.md).
Those can improve source identity or geographic coverage; neither automatically
resolves Oxford–London's unreported low end. Highest-value clarification remains
unthresholded receiver output plus source-specific model-domain/encoding evidence.
The provider draft remains unsent.

If those outputs are unavailable, a bounded acoustic propagation study needs
qualified traffic/emission inputs, terrain/building/barrier support and matched
independent recordings. Withhold geographies, include genuinely quiet references,
and evaluate interval calibration and fallback/abstention before presenting
estimates. The installed short London recordings are useful product context;
their temporal/source mismatch prevents annual or quiet-end validation.
