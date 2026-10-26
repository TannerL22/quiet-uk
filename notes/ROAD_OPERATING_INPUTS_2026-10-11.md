# Traffic, speed and weather inputs — 11 October 2026

The retained road scene now has a **usable conditional operating-input bundle**.
It supplies annual period flows, speeds by original vehicle category, source-power
scenarios and an hourly air series for all **722 retained road representations**.
Every choice distinguishes source evidence from assumptions. This completes the
traffic/speed/weather assumption increment; it does not release receiver noise.

## Evidence added and retained

| Input | Evidence | Conditional use |
|---|---|---|
| Annual traffic | Original 2021 directional estimates from 104 retained donors | Consume each representation's components once; preserve Counted/Estimated flags, category rounding and provisional link associations |
| Missing donor surveys | All 17 previously unexamined donors checked; 72 exact-2021 hourly records at three points | Survey-day evidence only; 37 retained donors now have complete compatible 07–19 surveys, supporting 145 representations |
| Traffic timing | Retained exact-2021 DfT national weekday/hour curves by vehicle group | Transfer the national shapes; bus/motorcycle use the explicit all-motor proxy |
| Speeds | Retained exact-2021 national free-flow rows | 26 identity-compatible motorway mainlines receive per-vehicle national transfer candidates; 696 other roads receive an explicit comparison reference |
| Weather | 25,810 original 2021 NOAA/NCEI Heathrow reports | One selected report per elapsed hour; 8,516 complete accepted temperature/dewpoint/station-pressure tuples |

The [DfT count guidance](https://roadtraffic.dft.gov.uk/about) distinguishes manual
survey days from annual estimates. The new raw records do not observe annual
night flow. National [traffic](https://www.gov.uk/government/statistical-data-sets/road-traffic-estimates-tra)
and [speed](https://www.gov.uk/government/statistical-data-sets/vehicle-speed-compliance-statistics-data-tables-spe)
tables retain their original notes, COVID-19 context and historical revisions.
Free-flow sampling does not establish typical local traffic speed.

[NOAA's ISD product](https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database)
provides station observations, including pressure and original quality flags.
This acquisition deliberately freezes its **historical 2021 archive**, station
`037720-99999`. ISD has been superseded by
[GHCNh](https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly);
the historical file is not a current weather service. Both product descriptions,
the original format specification, station-history CSV and weather gzip are retained.

Acquisition used **9 bounded public GETs, 5,463,209 bytes**, without an account,
purchase or external message. The first sandboxed attempt failed DNS resolution;
the approved network acquisition is the sealed `road_operating_evidence_v2`.

## Selected assumptions and alternatives

The baseline is an **explicit research reference**, not a best-estimate claim.

* Traffic: national vehicle-group weekday/hour indices apply to every elapsed
  2021 hour. The complete annual distribution is normalised once. There are no
  observed seasonal traffic factors and no independent daily normalisation.
* Speeds: only matching DfT/OS `M` road identities with collapsed/dual mainline
  form receive national motorway candidates. Cars use 111.044736 km/h, LGVs
  112.654080, and HGVs their own published axle-group speeds. Motorcycles use
  their published row. Motorway bus speeds are unavailable: **70 km/h is an
  explicit bus reference**. Other roads use **40 km/h**; this is neither an
  inferred legal limit nor an observed speed. All periods share the candidate
  speeds, with congestion and acceleration unresolved.
* Alternatives: common 20/40/70/100 km/h comparisons, plus half/double national
  night weights renormalised over the whole year, give **seven cases per road**.
* Emission: reference pavement, steady flat dry-road conditions, bus category 2,
  powered two-wheelers 4b and assumed twin rear tyres for category-2 rigid HGVs.
  The source-temperature reference remains **20°C**. Actual pavement, gradient,
  junction dynamics and source-temperature corrections remain unqualified.
* Absorption air: accepted station temperature, derived RH and **station pressure**
  transfer to uniform path air as an explicit spatial assumption. RH uses a
  water-based Magnus approximation derived from the retained
  [NWS saturation/dewpoint equations](https://www.weather.gov/media/epz/wxcalc/rhTdFromWetBulb.pdf).
  Its approximation is labelled; supersaturation is not clipped into valid data.
* Weather gaps: candidate references are **10°C, 70% RH, 101.325 kPa**, used only
  for missing/out-of-domain fields. Original missing observations stay null.
  Sea-level pressure and altimeter settings never replace station pressure.
* Propagation mix: favourable probabilities **0, 0.5 and 1** are declared
  comparisons, with 0.5 as the reference. Surface wind direction does not observe
  the vertical wind/temperature gradients needed to qualify favourable sound
  propagation. Qualified favourable probability stays null.

The observed wind-sector tables use 24 true-north bearings, ±60° sectors and a
declared 0.5 m/s threshold. They convert wind **from** to flow **toward** before
testing source-to-receiver bearings. Unknown/variable wind stays unknown. Their
lower/upper membership fractions account for missing station direction support;
they are not bounds on sound or favourable acoustic probability. A BNG grid
bearing cannot be used as a true-north bearing without coordinate conversion.

## Calendar and weather completeness

The 2021 series contains **8,760 elapsed UTC hours**, assigned to Europe/London:
4,380 day, 1,460 evening and 2,920 night hours. The spring missing local hour and
autumn repeated hour retain their offsets/folds. Missing weather slots remain in
the traffic calendar so weather availability cannot change annual traffic totals.
For any category, `AADF × 365 × slot_weight` gives its conditional vehicles in
that one-hour slot. Period averages conserve the original category AADF.

Reports are assigned to their nearest UTC hour, half-hour ties forward. Within
each slot, a complete accepted air tuple is preferred, then temporal offset,
SYNOP before METAR and source record number. All fields come from that single
report, without interpolation or combining pressure and temperature from different
reports. Quality codes **1 and 5** are accepted; gross-limit-only, suspect and
other flags remain visible but unavailable.

| Weather support | Hours |
|---|---:|
| Complete accepted air tuple | 8,516 |
| Report selected but one or more candidate-air fields need references | 143 |
| No report in the nearest-hour slot | 101 |
| Total | 8,760 |

Candidate gap references apply to 118 temperature slots, 122 RH slots and 236
pressure slots. These overlap; they must not be added as distinct missing hours.
The 2021 report headers contain three location variants, up to **945.21 m** from
the current station-history location. All are retained. A station relocation or
instrument equivalence is not inferred from those coordinates.

The traffic/weather joint calendar preserves national weekday/hour structure and
each hour's air tuple. The air-only diagnostic combines **linear transmissions**
before converting to dB, rather than averaging dB or inserting mean air into a
nonlinear absorption formula. It uses a declared 1 km path, exact base-10 octave
centres and all-motor timing; it is not an actual road/receiver result. Future
source integration must use the relevant vehicle/source weights and every source
point's physical path, rather than substituting this diagnostic.

## Findings and verification

The 37 complete survey-day comparisons have median national-shape total variation
**0.04744**, versus **0.05986** for uniform daytime timing. This supports retaining
national timing as a candidate; it neither qualifies local annual timing nor fits
the night fraction from those daytime counts.

Across the seven cases, the **1 kHz penalised source-power scenario spread** ranges
from **7.92 to 13.38 dB**, with median **11.09 dB**. This is source power per metre,
not receiver loudness, broadband dB(A) or predicted error. These are engineering
comparisons, not confidence intervals, probabilities or guaranteed physical bounds.
Traffic-estimate errors and incomplete road coverage are not captured by that spread.

**824 related tests pass**, including 33 new tests. They cover original quality
flags and pressure kind, missing versus zero, RH domain, joint tuple selection,
nearest-hour/year boundaries, DST, annual flow conservation, vehicle speed
crosswalks, exclusion of unestablished motorway forms, Q/(1000v), the frozen common-
speed source kernel and wind direction conventions.

Script 86 does not import the evaluated input builder, source kernel or air kernel.
It checks original records, scalar source equations, independent ISO relaxation
terms and vector wind-sector membership:

| Independent check | Count |
|---|---:|
| Selected original station observations / elapsed slots | 8,659 / 8,760 |
| Annual traffic/weather slot weights | 105,120 |
| Newly acquired original survey records | 72 |
| Vehicle speed assignments / baseline period-category flows | 7,220 / 21,660 |
| Conditional source cases / scalar octave-band powers | 5,054 / 161,728 |
| Wind sector-period tables | 72 |
| Hourly absorption coefficients / joint energy averages | 70,080 / 24 |

All current/parent generation hashes and **50 preceding implementation/test files**
remain verified. The 13,928-road inventory is unchanged, including **13,206 roads
without accepted source budgets**. All observed local annual period flow/speed
fields, qualified weather probability and receiver/combined road noise stay null.

## Delivered files and reproduction

The reviewable table is `artifacts/road_operating_inputs_v2/road_operating_inputs.csv`.
The JSON beside it holds components, vehicle speeds, baseline period flows,
source-cell IDs and seven source spectra per road. `hourly_weather.json` holds
original station fields separately from candidate air. `traffic_weather_calendars.json`
provides aligned weights; `protocol.json` lists the decisions above. No original
scene/source-cell values are overwritten or relabelled as synthetic observations.

| Generation | Identity | Inventory SHA-256 |
|---|---|---|
| `road_operating_evidence_v2` | `road-operating-evidence-046bbe3570182971eaca` | `f8e59d384eb2ef136ceaa6824877f36aa89800aa4a581b05a02590f204b88032` |
| `road_operating_inputs_v2` | `road-operating-inputs-31876f4679a52ca887d1` | `159ccd83fe2ea563132171afb9bff824ad364021234ec05cc04b49c5e84934ec` |
| `road_operating_review_v2` | `road-operating-review-492bac3bebbfbf52c89a` | `d610a25a7f8ab71315ec8fc2c3923e9898d8fac5cc88dd7ce1641ebaaadd0b45` |

JUnit/hash receipts: `artifacts/road_operating_validation_v1.{xml,json}`. The
failed/partial evidence/input v1 directories and earlier review v1 remain development
records, not current completion evidence. Artifacts are local and Git-ignored.

`tzdata` is now a declared dependency for Windows clock handling. The run records
version **2026.5** and retains the actual `London.tzif` bytes and hash for auditing.
The original observations and prior generations are reproducible without network:

```powershell
.venv\Scripts\python.exe scripts\85_resolve_road_operating_inputs.py --output artifacts/road_operating_inputs_NEW
.venv\Scripts\python.exe scripts\86_verify_road_operating_inputs.py --inputs artifacts/road_operating_inputs_NEW --output artifacts/road_operating_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_road_operating_inputs.py tests/test_road_scene_builder.py tests/test_road_emission.py tests/test_road_temporal.py tests/test_favourable_propagation.py -q -p no:cacheprovider --basetemp artifacts/operating_tests_NEW
```

Acquisition is a separate online operation:
`.venv\Scripts\python.exe scripts\84_acquire_operating_evidence.py --output artifacts/road_operating_evidence_NEW`.
Pass its path to script 85 with `--evidence` when deliberately evaluating that
new snapshot. Use fresh directories; source generations refuse overwrites.

## Remaining delivery

Traffic/speed/weather candidates can now be consumed with the explicit protocol;
there is no need to repeat their implementation checks. The subsequent
[physical-scene assumption policy](PHYSICAL_SCENE_ASSUMPTION_POLICY_2026-10-11.md)
now resolves separate ground, provisional building/barrier-height, pavement and
source surface/dynamics candidates with explicit sensitivity cases. The next
implementation step is the conditional real-scene propagation adapter and supported
source-line integration. Real model accuracy still requires independent matched sound evidence.
An incomplete road network or unsupported path cannot become an all-inclusive total
by selecting a traffic/weather case. Full-method qualification, external engine
execution and real-area acoustic validation remain false.
