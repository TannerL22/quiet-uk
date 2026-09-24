# Quiet UK active roadmap

Updated 24 September 2026. This supersedes the delivery ordering in older reviews;
those documents remain historical evidence.

**Review-adjusted plan:** see [Review response and delivery plan](REVIEW_RESPONSE_AND_DELIVERY_PLAN.md).
An independent review and local checks changed the next step from coverage
expansion to portable verification and evidence semantics. The verification
implementation is recorded below; the other review findings remain planned work.

**Product:** an elegant, general-purpose map for understanding quieter and louder
places, supported by traceable evidence. Homes, walks and research are applications
of the product, not separate product identities.

**Completed in the explorer phase:** local England-wide road/rail display, labelled
basemap, pan/zoom, place/postcode and coordinate search, exact cell inspection,
aviation and uncertainty explanations, versioned display products, location and
dataset downloads, responsive design and local view links. Research construction
requirements are defined in `RESEARCH_DATA_CONTRACT.md`.

**Completed next increment:** aircraft-presence hatching enabled by default,
aircraft-only lower-bound display, source-view controls with saved-view support,
numeric aircraft inspection, explicit machine-readable unavailable metrics, and
a duration-aware temporal calculation library with contrasting-history tests.
See `SOURCE_VIEWS_AND_TEMPORAL_FOUNDATION.md`. This adds neither a validated
all-source total nor actual national event/time-series data.

**Current boundary:** this is a local exploratory release of historical data.
No public hosting, full UK noise coverage, validated below-cutoff ranking or
study-ready exposure release is claimed. The source checkpoint includes the app,
tests, launcher and documentation; generated geospatial artifacts and local
environments are excluded from Git. Publishing source does not deploy the app or
back up the local data releases.

**Completed source-linked pilot:** four 2 × 2 km areas now expose original 10 m
road, rail and aircraft grids separately, with Lden/day/night controls, exact point
comparisons and downloadable acquisition evidence. All 36 layers pass native-grid
and request-chain checks; their display derivatives reproduce offline. Road/rail
reference 2021; aircraft's period remains unspecified. See
`SOURCE_LINKED_PILOT.md` for the delivered release, provider issues and verification.
This completes the bounded canary implementation, not the national/research release
gate: aircraft comparability and independent validation remain outstanding.

**Completed regional increment:** the four areas now span 10 × 10 km each
(400 km²), with place/coordinate search and explicit outside-coverage handling.
All 1.44 million original sample values/masks are unchanged in the expanded data.
A newly installed isolated environment passes the full suite and reproduces all
36 maps from the portable evidence bundle offline. This closes the bounded
Windows environment-reproduction check; broader platform support and independent
scientific validation remain open. See `SOURCE_REGIONAL_RELEASE.md`.

**Completed place-comparison increment:** the 10 m explorer saves up to three
named points locally, shows all nine source/time indicators side by side, and
exports exact observations with native-cell geometry, source hashes, coverage
identifiers and reference periods. Numerical differences require matching declared
periods; missing values never become zero or a quietness ranking. Saved recipes
and links are pinned to the data release and reload verified values. See
`PLACE_COMPARISON.md`. This implements the comparison workflow; actual user
testing remains outstanding and no new geographic coverage is claimed.

**Portable verification completed:** the nine artifact-dependent viewer tests
now use a generated two-component catalogue. Three opt-in release checks retain
the real 128-component join, catalogue membership and geometry assertions.
Byte-preserved Defra fixtures cover real metadata and raster encodings, and CI
runs a Windows/Linux, Python 3.12/3.14 matrix. A source-only copy in a newly
installed Windows 3.14 environment passes **446 tests, with two platform skips**;
the three release checks also pass when explicitly pointed at the local data.
See [verification instructions and scope](REPRODUCIBLE_ENVIRONMENT.md#source-only-verification).
The [hosted matrix passed](https://github.com/TannerL22/quiet-uk/actions/runs/36041725652)
on source commit `a25b79c`: both Linux versions passed 448 tests; both Windows
versions passed 447 with one platform skip.

**Evidence semantics implemented:** release `pilot-70542da395300f52dd27` preserves
the parent responses and separates numeric zeros, TIFF nodata and extract limits.
Aircraft cutoff is explicitly unknown; provider minimum, observed extract minimum
and period evidence are separate. The UI/API/exports share the new contract, and
road/rail views flag separately reported aircraft at the selected point.
All 36 million source-indicator cells match the parent and all 36 maps reproduce.
No ambiguous cell has been promoted to a certified quiet bound. See
[evidence semantics and verification](EVIDENCE_SEMANTICS.md).

**Next evidence task:** bounded WCS cross-protocol extraction checks and a
source/domain/airport-period evidence crosswalk. The semantics implementation is
complete; the broader extraction-fidelity gate remains open before expansion.

| Next phase | Outcome | Exit condition |
|---|---|---|
| Portable verification (complete) | A checkout others can test | Default Windows/Linux suites passed without local artifacts; release integration checks remain explicit; provider fixtures exercise real encodings |
| Evidence semantics and extraction fidelity | Consistent source-qualified states and exports | Verified bounds distinguished from unknowns; aircraft minima/thresholds/period evidence separated; small WCS cross-protocol checks pass; new interpretation release preserves raw originals |
| Coherent, user-tested explorer | One understandable map experience and independently runnable regional data | Consistent legends and source warnings; 4/5 users complete core tasks, none in the formative sample equates unknown with silence or misses Heathrow aircraft evidence |
| Tiled 50 × 50 km canary | Bounded expansion and measured serving costs | Exact overlaps, resumability, immutable serving snapshots, bounded-memory exports, tile/latency checks and explicit acquisition budgets |
| Traceable England release and public beta | Replace the provisional overview, then distribute reliably | Versioned analytical/display products, clear coverage, discrepancy reports, attribution, provider arrangements, operating limits and rollback |
| UK coverage and validated richer evidence | Country adapters and justified new indicators | Country-specific source/metric/period validation; independent validation appropriate to claims; no inferred event histories or invented quiet-end precision |

Documentary and extraction validation proceeds alongside these phases. Field
validation requires a separately designed source/season/year-matched protocol;
it does not block every improvement to the exploratory map. Freeze speculative
road models, candidate-screening expansion, event features and further historical
archaeology. Retain evidence and tests; do not delete modules based on line count.

The product must keep every supported transport source visible in its explanation,
with optional source controls. Heathrow remains a required validation case: a
road-only display must not imply low overall exposure where aircraft evidence is
present. Any future numeric combination requires compatible metric, period,
coverage and construction. Preserve the distinction between annual averages,
background sound and loud events, and state which remain unavailable.

Each task must name its user behaviour or evidence gap, bounded deliverable and
completion check. Demonstrate the product regularly. Do not use additional models,
audit documents or infrastructure as substitutes for a working map.
