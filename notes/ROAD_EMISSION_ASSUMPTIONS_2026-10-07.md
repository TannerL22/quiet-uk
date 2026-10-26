# Road-emission assumption protocol — 7 October 2026

The bounded road study now has a frozen assumption protocol, conditional source
emission calculations and a sensitivity evaluation. It evaluates **15 cases for
104 distinct traffic donors** supporting the existing **722 provisional spatial
budgets**. **196 related tests pass**, and an independent scalar calculation
verified every exported source-power value. This prepares an eventual propagation
experiment; it does not produce noise at a receiver or fill any unknown map cell.

The main finding is that timing, speed and pavement assumptions materially change
conditional source emissions. The national daytime profile is a useful candidate,
but local annual night traffic, representative speeds and actual pavement remain
unknown. The protocol keeps those facts explicit rather than promoting a convenient
default to an observation.

## Evidence and method identity

The study remains the fixed north-Heathrow / West Drayton **4 km² core** and
**144 km² source area**. All original annual traffic, directional estimates, road
geometry, carriageway decisions and terrain/building inputs are unchanged. The
722 spatial budgets share 104 traffic donors: 53 Counted and 51 Estimated. These
are repeated source associations, not 722 independent traffic observations.

The [DfT national traffic tables](https://www.gov.uk/government/statistical-data-sets/road-traffic-estimates-tra)
already retained in the temporal audit supply the exact-2021 hourly/weekday
indices. Their notes define a shared annual reference, flag COVID-19 effects and
historical revisions, and give no separate motorcycle, bus or HGV-axle profiles.
The existing four national free-flow speed rows are preserved as conditional
candidates, including their notes and two unavailable motorway bus cells. Numeric
mph values also have an exact conversion using 1 mph = 1.609344 km/h. No national
road-type row is automatically assigned to a local link whose speed limit and
traffic conditions are unknown.

[Defra's indicator definitions](https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps)
provide day 07:00–19:00, evening 19:00–23:00 and night 23:00–07:00. Those are
nominal civil-hour strategic periods of 12/4/8 hours in Europe/London. This is
not an elapsed-time monitoring calculation or a reconstruction of DST transitions.

The source equations were checked against Chapter III of
[JRC72550, Common Noise Assessment Methods in Europe (2012)](https://publications.jrc.ec.europa.eu/repository/handle/JRC72550),
including visual checks of the original classification and equation pages.
The complete replacement **F-1** coefficients and selected **F-4** surface rows
come from [Directive 2021/1226](https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj/eng).
This is an explicitly identified research subset: the eight 2021 coefficient bands
(63 Hz–8 kHz) are used in the checked source equations. Some 2015/consolidated
EUR-Lex requests yielded navigation or a robot challenge, retained as incomplete
retrievals. A complete current regulatory annex and compliant implementation have
not been qualified. **No regulatory CNOSSOS compliance is claimed.**

Ordinary source discovery used the Firecrawl skill and its secondary connection.
The bounded original JRC PDF acquisition was one public GET, **6,545,045 bytes**,
under an 8 MiB cap. Source responses, retrieval identities, original PDF,
documentation limitations, coefficient tables and hashes are retained.

## Frozen assumptions and conservation rules

The numerical anchor uses national vehicle-group timing, **70 km/h**, the virtual
reference surface, buses in category 2 and powered two-wheelers in category 4b.
It is a comparison reference, **not a selected best local estimate**. The common
speed deliberately avoids inferring a road's legal limit or observed speed from
its name or generic class.

| Input | Protocol |
|---|---|
| Timing | Joint calendar-weighted national curves; national all-motor curve; uniform 24-hour control; night weights multiplied by 0.5 or 2 and the entire daily distribution renormalised |
| Calendar | All 168 weekday/hour cells retain their relative magnitude; 2021 has 53 Fridays and 52 of every other weekday. No separate daily normalisation erases weekday/weekend volume differences |
| Vehicle profiles | Cars/taxis, LGVs and HGVs use their respective national curves. Motorcycles and buses use an explicit all-motor proxy; all HGV axle classes share the HGV curve |
| Speeds | Controlled common-speed comparisons at 20, 40, 70 and 100 km/h. No extrapolation below 20 or above 130; congestion, idling and acceleration are not represented |
| Surfaces | Reference surface, Thin layer A and Worked surface from F-4, as hypothetical codes. Thin layer A is supported at 40–130 km/h; Worked surface at 50–130. Actual local pavement/condition remains null |
| Vehicle classes | Cars/taxis and LGVs → 1; two-axle rigid HGVs → 2 with twin rear tyres assumed; other HGVs → 3. Bus alternatives use 2 or 3. Two-wheeler alternatives use all 4b or all 4a; the all-moped case assumes 40 km/h |
| Reference conditions | Flat, dry road; steady speed; 20°C; no studded tyres. These are stated research conditions, not measured local conditions. Source height convention is 0.05 m above the road |
| Traffic conservation | Each original directional vehicle category is consumed once. Its daily count is divided among periods, then divided by period duration for vehicles/hour. Original all-vehicle/HGV totals remain evidence; rounded category residuals are not repaired |
| Missing and zero | A missing original category withholds all source spectra. Valid zero traffic stays zero linear power; its logarithm is null with an explicit zero flag |
| Scope | Only previously qualified provisional carriageway budgets. All other roads remain excluded; there is no count propagation or class-median filling |

For example, the calendar-weighted candidate fractions are:

| Vehicle profile | Day | Evening | Night |
|---|---:|---:|---:|
| Cars/taxis | 81.71% | 11.73% | 6.55% |
| LGVs | 82.86% | 9.25% | 7.89% |
| HGVs | 76.19% | 7.72% | 16.08% |
| All motor vehicles, used as bus/motorcycle proxy | 81.60% | 11.03% | 7.37% |

These are national transfer assumptions, not local measured annual fractions.
Applying one all-motor timing curve to every category can lose the substantially
different HGV night fraction.

The kernel combines rolling and propulsion energy for classes 1–3; two-wheelers
have propulsion only. It applies rolling `alpha + beta log10(v/70)` and the
declared `min(alpha,0)` propulsion surface convention. The JRC surface treatment
includes local source/absorption effects; a later propagation implementation must
not apply the same pavement absorption correction again. This convention and
the selected surface codes remain part of the research case, not a qualified
local pavement classification.

Source-line power includes the density factor **Q / (1000 v)**, with Q in
vehicles/hour and v in km/h. Output is octave-band sound power per metre relative
to **10⁻¹² W/m**, never sound pressure at a house, microphone or map receptor.
Bands are unweighted, not dB(A). A separate penalised source-power diagnostic
uses the 12/4/8-hour energy weights and evening/night penalties; it is not a
receiver Lden field. No terrain, shielding, meteorological propagation,
diffraction or receiver reflections are calculated in this increment.

There are 15 predeclared cases: the anchor, four timing alternatives, three speed
alternatives, two pavement alternatives, two vehicle-classification alternatives,
two interaction stress cases and one deliberate invalid surface/speed combination.
They are engineering comparisons, not probabilities, confidence intervals or
guaranteed physical bounds. No case was fitted to the installed noise rasters.

## Measured evaluation results

The **1,560 donor/case evaluations** comprise **1,456 conditional source results**
and **104 withheld results**. All withheld results belong to the deliberately
unsupported Worked surface at 40 km/h; its published validity starts at 50.
The original category sums differ from their original published stream totals
by −3 to +3 vehicles/day across the 104 donors; those independently rounded
differences remain visible rather than being redistributed.

At **1 kHz**, the descriptive medians over the 104 donors are:

| Case relative to the numerical anchor | Night source-power change | Penalised source-power change |
|---|---:|---:|
| Uniform 24-hour traffic | +6.34 dB | +3.53 dB |
| Night weights halved, then daily total conserved | −2.81 dB | −0.79 dB |
| Night weights doubled, then daily total conserved | +2.65 dB | +1.12 dB |
| 40 km/h rather than 70 | −4.23 dB | −4.49 dB |
| 100 km/h rather than 70 | +3.20 dB | +3.27 dB |
| Thin layer A surface at 70 km/h | −2.62 dB | −2.72 dB |
| Worked surface at 70 km/h | +3.15 dB | +3.41 dB |

These are conditional **source-power changes at one frequency**, not changes
in experienced noise, LAeq or Lden. The full output retains every band and
period, donor-level results and min/p10/median/p90/max descriptions. Donors
are not independent geographic samples, and these percentiles are not uncertainty
intervals. The interaction cases expose combined effects; their extrema do not
bound all missing physical inputs.

For a limited local check, each of the **35** complete survey days was normalised
only within its observed 07:00–19:00 window and compared with the national curve
for that same weekday. This conditional comparison does not expand the survey
day to the full year or infer any unobserved hour.

The national shape had lower total-variation distance than a uniform daytime
shape at **29/35 points**. Its median distance was **0.0478**, versus **0.0684**
for uniform; its maximum was **0.1920**. Total variation is half the summed
absolute difference between the twelve hourly shares, on a 0–1 scale. These are
limited daytime shape diagnostics, not evidence that the national annual or
night profile is accurate locally.

## Verification, artifacts and reproduction

The delivered immutable generation is `artifacts/road_emission_assumptions_v2/`,
identity `road-emission-assumptions-c5ac694e4e366323b531`:

- `protocol.json`: assumptions, original source identity, case definitions,
  consumption/exclusion rules and nullable observed-input fields.
- `case_period_profiles.json`: all hourly weights, period fractions and explicit
  vehicle-profile proxies for every case.
- `donor_source_scenarios.json`: original category AADF, assumed period/hour flows,
  speed/class assumptions, source spectra and withholding reasons for 1,560 cases.
- `group_evaluation_index.json`: all 722 original spatial budgets mapped to their
  donor evaluations. Source powers are per metre; no network flow total is formed.
- `daytime_profile_diagnostics.json`: original observed hour shares, national
  same-weekday shares and the diagnostic comparison for each survey.
- `conditional_national_free_flow_candidates.json`: original speed cells, notes,
  conditional km/h conversions and an explicitly null local application.
- `report.json`, `source_sensitivity_1000hz.csv`, visually checked
  `source_assumption_sensitivity.png`, published coefficient and code snapshots,
  and `inventory.json`.

The runtime record identifies Python 3.14.2, NumPy 2.5.2, Matplotlib 3.11.1,
Windows and the Agg plotting backend. The earlier completed v1 remains immutable;
v2 adds explicit runtime/provenance metadata. All eight numerical/evidence/figure
files are byte-identical between the two generations.

Method sources are in `artifacts/road_emission_sources_v1/`, identity
`road-emission-sources-b090dfc1b5dbe35bc5b5`. Source licences and attribution
travel with the protocol, including the existing DfT/OS OGL and OSM ODbL conditions.
The PDF equation/classification review is separately retained in
`artifacts/road_emission_method_review_v1/`.

**196 tests pass**, including 34 new emission checks for calendar weighting,
night wrap, conservation, source density units, energetic summation, explicit
motorcycle propulsion-only treatment, original missing/zero states, refusal
outside speed/surface support and an official-coefficient canary. The canary
independently calculates the category-1 1 kHz energy sum from F-1 AR=100.1 and
AP=84.7 at 70 km/h. Receipts are `artifacts/road_emission_validation_v1.xml` and
`.json`, with exact code/test/fixture hashes. The final
`artifacts/road_emission_validation_v2.json` links that unchanged unit-test run
to the v2 input/review inventories and current script hashes.

The separate scalar verifier does not call the evaluated profile or source
functions. It reconstructs calendar weights from the frozen original indices,
checks every original donor category and assumed period flow, verifies all 722
spatial group memberships, and recomputes **46,592 scalar source-power values**
for all valid cases, bands and periods. Maximum relative difference is
**5.29 × 10⁻¹⁶**. Two unavailable speed cells remain unavailable and no speed
row has a local application. The input inventory remains unchanged. This verifies
construction fidelity, not independent traffic/noise accuracy.

The delivered review generation is `artifacts/road_emission_review_v2/`, identity
`road-emission-review-1f67d06c35dde0f1c8a6`; its inventory and verification receipt
identify the v2 inputs. The full scalar verification passed on both generations.

```powershell
# Completed generations are immutable; use fresh output directories.
.venv\Scripts\python.exe scripts\51_acquire_road_emission_sources.py --output artifacts/road_emission_sources_NEW
.venv\Scripts\python.exe scripts\52_evaluate_road_emission_assumptions.py --sources artifacts/road_emission_sources_NEW --output artifacts/road_emission_assumptions_NEW
.venv\Scripts\python.exe scripts\53_verify_road_emission_assumptions.py --inputs artifacts/road_emission_assumptions_NEW --output artifacts/road_emission_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/road_emission_pytest_NEW
```

The acquisition script expects the retained `road_emission_documentation_v1`
retrievals; another PDF acquisition creates a new separate source snapshot.
These artifacts remain local and excluded from Git; source code alone is not
a data backup. The explorer and its native noise evidence are unaffected.

## Next bounded step and release boundary

**Subsequent 10 October delivery:** the [ground/barrier audit and synthetic
propagation checks](GROUND_BARRIER_AND_PROPAGATION_2026-10-10.md) are complete.
They preserve unknown input states and verify controlled mathematical cases;
real-area ground/barrier data and complete compatible method qualification remain
open. The paragraph below records this increment's original handoff.

Complete the **ground and barrier convention audit** for this same core/source
area: distinguish road/source elevation from terrain, avoid duplicated pavement
absorption/reflection, specify ground treatment and test building/barrier
inclusion, withheld heights and source-margin sensitivity. Then build a small
synthetic propagation canary with analytic limiting cases before a reported-only
comparison. Remaining method-version and input qualification gaps must be stated
before any real-area absolute prediction.

Local annual timing and speeds, pavement properties, historical geometry
compatibility, gradients/junction dynamics and full network contributions remain
unqualified. The 54 associated dual links, 98 slip links, 102 roundabout links and
other previously withheld roads remain excluded. An experiment on the retained
subset cannot claim all-road noise or certify quiet missing cells. Independent,
temporally matched acoustic validation remains a separate requirement.
