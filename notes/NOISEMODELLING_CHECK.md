# NoiseModelling check against Defra (north Heathrow), 5 November 2026

**Question:** can we calculate road noise ourselves well enough to fill in the
quiet places, where Defra publishes only "under 40 dB" (under 35 dB at night)?
Before paying for a cloud computer, we checked on this laptop whether our own
calculation reproduces Defra where Defra *does* publish numbers.

**Short answer: not yet.** With open, published inputs our best setup reads
about 2½ dB louder than Defra on average, with a typical error of about 5½ dB,
measured on 352 independent points. The pass bar was 1 dB and 3 dB. The error
is largest at the quieter, shielded points, which are the ones that matter for
quiet places. The gap comes mostly from three inputs Defra used
that we don't have: measured local weather, a finer ground-cover map, and UK
vehicle noise figures. A bigger computer would not close it, so no cloud spend
is justified yet.

## What was run

- **Software:** NoiseModelling 6.0.0 (GPL v3), an open implementation of
  CNOSSOS-EU, the EU method Defra used for Round 4 (Directive 2015/996).
  - It runs headless with Microsoft OpenJDK 25 (Windows ARM64).
  - Both are installed outside the repository, in a `tools/` folder beside it.
  - Driver: `scripts/125_noisemodelling_check.py`.
- **Test square:** BNG 506000–508000 E, 178000–180000 N (Sipson/Harlington, with
  the M4 and the A4 nearby). Defra publishes a value in every cell here, with
  a median of 57 dB Lden, so it tests agreement, not quiet places.
- **Points:** Defra's own 10 m cell centres, 4 m above the ground, skipping
  points inside building footprints. Settings were explored on 21 points (every
  40th cell). The chosen setting was then measured on 352 different points
  (every 10th cell); the two sets share no point.
- **Inputs:**
  - Terrain: Environment Agency 2 m LiDAR DTM, averaged to 10 m.
  - Buildings: 23,823 OS OpenMap Local footprints. 17,633 have a LiDAR-measured
    height; the rest use the median, 6.4 m.
  - Ground type: ESA WorldCover 2021.
  - Major roads: DfT MRDB 2021, with each road's own 2021 AADF count.
  - Other roads: OS Open Roads with typical DfT flows for that road class
    (B roads from the nearest count on the same road where one exists).
  - Speeds: DfT SPE0102 (2021).
  - Hourly pattern: DfT TRA0307/TRA0308.
  - Roads within 3 km of each point, the same limit Defra used.
- **Pass bar, fixed before any result:** an average difference (bias) of at most
  ±1 dB and a typical error (RMSE) of at most 3 dB, over cells where Defra
  publishes a value.

## What we learned about running it

- **Road height:** road lines need a height of 0.05 m (the CNOSSOS source
  height). NoiseModelling reads heights as "above the ground" unless told
  otherwise.
- **Sound over roofs:** NoiseModelling's "diffraction on horizontal edges"
  setting is sound bending over roofs and terrain. It must be on: with it off,
  any road hidden behind a building contributes nothing, and some points came
  out completely silent. "Vertical edges" (sideways bending around buildings)
  stays off for roads, as the directive says.
- **Cost:** with those settings each point takes about 13 seconds of the whole
  laptop (10 threads), plus about 6 minutes of setup per run.
  - The 40,000-cell square would take about 5–6 days.
  - A statistical check on 352 points takes 1½–2½ hours.
- **Bug:** with a road search distance below about 3 km, NoiseModelling 6.0.0
  splits the area into tiles and crashes on roads cut at a tile edge
  ("Unsupported source geometry GeometryCollection").

## Results on the 21 exploration points (Lden)

| Setting | Bias | RMSE | Within 3 dB |
|---|---|---|---|
| First guess: sound-carrying weather 50% day / 75% evening / 100% night, built-up land as hard ground | +7.8 | 9.0 | 14% |
| Same, major roads only | +5.0 | 9.1 | 14% |
| Major roads only, never sound-carrying weather | −18.3 | 22.3 | 10% |
| Major roads only, built-up land as soft ground | −0.3 | 8.3 | 24% |
| Irish measured weather (Dublin: 39% / 52% / 52%), hard ground | +6.6 | 7.8 | 19% |
| **Irish measured weather, built-up land as soft ground** | **+2.2** | **4.7** | **33%** |

Lnight follows the same pattern. With the last setting, night has bias +2.3 dB
and RMSE 5.1 dB.

What this shows:

1. **The excess comes from the major roads**, mainly the M4. Houses hundreds of
   metres away, behind other houses, were getting nearly what an open field
   would get. Minor-road traffic guesses were not the cause.
2. **Weather matters most.** Under sound-carrying conditions (wind towards
   you, or still cool nights) sound bends back down over the roofs. How often
   that happens moves the result by up to 25 dB at shielded points, and Defra
   sits between our two extremes at almost every point.
3. **Ground matters next.** The satellite land-cover map labels whole housing
   estates "built-up", so gardens and lawns count as hard ground. Treating
   them as soft removes about 4–5 dB.
4. **What's left** (about +2 dB on average, scattered ±5 dB): shielded houses
   are still too loud, and one point right beside the motorway is too quiet.

## What Defra did differently

From the paper describing Defra's national system (Shilton et al., *Overview
of research supporting development of a national noise modelling system for
England*, Euronoise 2025):

- **All roads in England**, with default traffic flows where nothing was
  counted. Including minor roads was right.
- **Ground:** the UKCEH Land Cover Map 2019, which separates suburban gardens
  from hard surfaces. We used ESA WorldCover.
- **Weather:** measured shares of sound-carrying conditions from 131 Met
  Office stations, by Nord2000, on a 1 km grid in 20° directions. Not
  published as far as we can find. We used Ireland's equivalent county values,
  from the Irish EPA's Guidance Note for Strategic Noise Mapping v4, Part 2,
  Appendix C, averaged over direction.
- **UK vehicle noise figures** and UK road-surface corrections. We used
  NoiseModelling's EU defaults and the reference surface.
- **Software:** Predictor-LimA. Defra's reply of 3 November says efficiency
  settings were needed, and that results below the cutoffs were not reliable
  because of them.

We also have no noise-barrier data, and Defra's model may include barriers.

## Measurement on 352 separate points

The best setting from the table above (Irish measured weather, built-up land
as soft ground) was then run once on 352 points that played no part in
choosing it. This was a measurement after the check had already failed, not a
second attempt at passing. The run took 77 minutes.

| Indicator | Points | Bias | RMSE | Within 3 dB | Within 5 dB |
|---|---|---|---|---|---|
| Lden | 352 | +2.6 | 5.5 | 36% | 58% |
| Lnight | 348 | +3.0 | 5.9 | 39% | 60% |

By Defra's own level at each point (Lden):

| Defra level | Points | Bias | RMSE |
|---|---|---|---|
| under 50 dB | 25 | +3.9 | 5.9 |
| 50–55 dB | 86 | +3.6 | 5.4 |
| 55–60 dB | 108 | +4.8 | 5.7 |
| 60–65 dB | 62 | +1.1 | 4.7 |
| 65–70 dB | 33 | −1.8 | 5.1 |
| 70 dB and over | 38 | −0.8 | 6.2 |

At night, the 201 points Defra puts under 50 dB Lnight read +4.7 dB too loud
(RMSE 6.4 dB). Four points fall under Defra's 35 dB night cutoff, and only one
of them also comes out under 35 dB here.

What this adds:

- **The 21-point result holds up.** On the larger, independent set the result
  is slightly worse (+2.6 dB and 5.5 dB, against +2.2 dB and 4.7 dB), so the
  exploration points were not unusually favourable.
- **The error is largest at the quieter points**, which is where the
  calculation would be used. Shielded, quieter places read 4–5 dB too loud,
  while points beside the main roads are about right or slightly too quiet. A
  calculation that is too loud in quiet places would hide exactly the places
  the map is trying to find.

## What this means

- **No cloud spend yet.** The gap is in the inputs, not in computing power.
- **The calculation itself works.** NoiseModelling runs correctly, and with
  better inputs it should come much closer.
- **Ways forward:**
  1. **Ask Defra** about two things: the combined transport noise product they
     said was "calculated below the source-specific reporting cutoffs" (when,
     and down to what level), and the national model's inputs (the weather
     grid, the UK vehicle and surface tables, the ground-type rules and the
     default traffic flows).
  2. **Use the UKCEH Land Cover Map** for ground type, if its licence allows.
  3. **A rural test square** (needs new downloads). Even a pass near Heathrow
     wouldn't prove accuracy in quiet countryside, where weather matters even
     more over long open distances.

## Reproduce

```
.venv/Scripts/python.exe -I scripts/125_noisemodelling_check.py --output artifacts/noisemodelling_check_timing --prepare --sample 40
.venv/Scripts/python.exe -I scripts/125_noisemodelling_check.py --output artifacts/noisemodelling_check_timing --run base --label ie_dublin_soft --favourable D=0.39,E=0.52,N=0.52 --ground-file ground_soft.shp
.venv/Scripts/python.exe -I scripts/125_noisemodelling_check.py --output artifacts/noisemodelling_check_timing --compare
```

`--prepare` writes `ground_soft.shp` next to `ground.shp`: the same map with
WorldCover class 50 (built-up) set to G = 1. The files used in these tests were
made by an equivalent one-off step and are byte-identical to it. Each run's folder keeps its settings, input fingerprints and timing in
`run.json`. `report.json` holds every comparison.
