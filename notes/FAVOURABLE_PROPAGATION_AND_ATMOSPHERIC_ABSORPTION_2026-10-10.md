# Favourable propagation and atmospheric absorption

The conditional DIRECT cross-section solver now includes favourable curved
paths, favourable ground effects and explicit-air atmospheric absorption.
**All 911 public comparisons and 632 related tests pass.** Independent
calculations reproduce the boundary and paired attenuation terms to
8.513 × 10⁻¹² dB, and all 960 absorption grid cases to 7.959 × 10⁻¹³ dB/km.
The preceding completed solver/input generations remain byte-preserved.

These are propagation attenuation terms, not measured sound or annual receiver
levels. All 100 real-area paths remain withheld. No missing map cells receive
estimates, and no source powers, historical meteorology or qualified acoustic
geometry are inferred from these method checks.

## Implemented equations and explicit choices

`src/quiet_uk/favourable_propagation.py` derives the circular and atmospheric
equations independently of external implementation code. It reuses the prior
validated scene geometry, composite roof/G support, mean-plane fits and pure
diffraction coefficient. Earlier modules are unchanged.

Favourable geometry uses upper minor circular arcs, with radius
`max(1000, 8*d)` and physical 3D source–receiver distance `d`. The physical
path is the shortest enclosing chain of arcs with a downward deflection at
each retained obstacle. A straight chord hull is insufficient: the amended
arc can pass over a corner that blocked the straight ray. Segment length is
`2*radius*asin(chord/(2*radius))`. A clear single-edge path uses equation
2.5.27, including the named edge's vertical projection onto the straight ray.
Both strict amended Rayleigh tests are retained for clear paths.

The selected physical edges stay fixed for the pure image diffraction terms.
Each image pair uses its own direct arc baseline and corresponding radius.
Returning image legs remain positive excess paths. Side-plane physical
coordinates are preserved and negative equivalent heights are clipped to
zero; the source/receiver below-plane special rules are retained. Only the
main horizontal diffraction term is capped at 25 dB, and ground is coupled
inside that boundary term rather than added twice.

Favourable ground uses the original equivalent heights for corrected G and
the lower-bound geometry. Bending and turbulence modify the heights inside
the frequency-dependent ground expression. `Gw=Gpath`; the source-side
lower-bound `Gm` is corrected `Gprime`, while the receiver side uses `Gpath`.
Explicit hard ground uses its geometry-dependent lower bound, which can
approach -9 dB. A positive G with two null equivalent heights is withheld
because the turbulence correction is singular. Roof G is still supplied
separately from underlying terrain G.

Absorption requires temperature in Celsius, relative humidity in **percent**,
pressure in kPa and an explicit uniform absorption-air assumption. ISO
pure-tone equations calculate oxygen/nitrogen relaxation and absorption in
dB/km; RH is converted to molar water-vapour percent using the documented
saturation-pressure equation. The bounded domain is -20..50 °C, 10..100% RH,
50..120 kPa and 50..10000 Hz. Missing, unsupported or nonuniform air withholds
the combined attenuation. The standard's approximate 10% or 20% coefficient
accuracy range is recorded from water concentration; numerical agreement
does not imply that outdoor sound is known to those numerical tolerances.

Ground/diffraction retain nominal octave frequencies. Absorption requires an
explicit `exact_base10` or `nominal` frequency policy. The public atmospheric
benchmarks use exact centres `1000*10^(0.3*k)`, k=-4..3, whose nominal labels
are 63..8000 Hz. The actual frequency is recorded. Pressure and frequency
policy have no hidden default.

The paired DIRECT calculation adds divergence, absorption along the physical
3D direct distance and the appropriate H/F boundary term. A horizontal roof
detour does not replace direct distance for that absorption term. Weather
averaging weights **sound energy**, rather than averaging dB. It requires an
explicit favourable probability in [0,1]. Missing probability preserves the
two conditional terms but withholds a mixed result. Every annual/noise level
remains null; the supplied probability is not a historical weather record.

## Original method and public reference evidence

Sources retain the [original 2015 Annex/journal issue](https://publications.europa.eu/resource/oj/JOL_2015_168_R_0001.ENG),
the [2018 corrigendum](https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32015L0996R(01)),
the preceding original 2021 amendment and EU metadata. The 2015 PDF response
contains the complete 823-page journal issue; equations on pages 35/39 and the
2018 correction were rendered and visually inspected. The amendment's
shortest enclosing circular-path figure and equations were also inspected.
EUR-Lex image/PDF reads sometimes returned an HTML challenge. Those bytes
were excluded from the source generation; the correction's EU PDF/A item was
located through its original expression/manifestation metadata.

The original [ISO 9613-1:1993 SIST preview](https://cdn.standards.iteh.ai/samples/17426/fe55139127f44c1bbdca09bf2a68450d/SIST-ISO-9613-1-1998.pdf)
provides the principal absorption equations and accuracy domains. It is a
12-page preview, not the whole standard. The original project's
[python-acoustics equation documentation](https://python-acoustics.github.io/python-acoustics/standards.html#iso-9613-1-1993)
supports the RH/saturation conversion; no implementation code was copied.
Firecrawl research succeeded on the Secondary account.

Sixteen unchanged NoiseModelling DIRECT cuts and original committed expected
arrays are pinned to commit `1a0158958d3288752daef3a3a60b5f8bc1afacef` under
their retained GPLv3 licence. TC01–04 were added with four bounded public
reads totalling 2,546 bytes; the other 12 originals come from the verified
prior sources. The external Java engine was not executed.

There are 128 reference octave cases and 911 unique scalar comparisons:
128 favourable boundary terms, 399 active favourable intermediates, and
128 each of atmospheric coefficients, atmospheric attenuation and divergence.
TC15 has no published F intermediate arrays; H arrays are not substituted.
TC21 independently checks the favourable diffraction branch at 250 Hz only;
TC23 checks a favourable arc retaining one edge where H retained two.
TC11/14's previously documented below-plane height-table incompatibility
remains excluded; the primary zero-height rule is retained.

The declared research tolerances are 0.02 dB/dB per km, with 0.05 dB for
TC12/14 boundary/intermediates and 0.03 dB for TC13. TC13's favourable main
term differs by 0.021651 dB on the exact fixture geometry; that small reference
discrepancy is disclosed rather than changing geometry or expected values.
The largest public attenuation difference is 0.042513 dB (TC14), and the
largest atmospheric coefficient difference is 0.003946 dB/km. These are
bounded compatibility checks, not full current-method or ISO qualification.

## Independent calculations and regression validation

Fifty-one synthetic scenes produce 1,584 unique octave/G cases: 1,040 numeric
and 544 withheld. Numeric branches comprise 632 diffraction, 272 clear-edge
ground and 136 no-edge ground cases. In particular, the corpus checks an arc
clearing a straight obstruction and a middle obstacle lying above the chord
but below the enclosing arc. Ninety-six synthetic cases intentionally remain
outside the bounded monotonic circular domain (two extreme 400 m roof scenes
and an extreme 20 km screen); this is not an impossibility claim about other
ray geometries. Twenty-four positive-G/two-null-height cases also abstain.

Script 74 imports neither the evaluated weather modules nor builder 73. It
uses exhaustive paths constrained by circle visibility and downward tangents,
QUADPACK circular line integrals, prior Gauss/QUADPACK plane fits, independent
vector mirrors, expanded ground/amplitude/energy equations, 50-digit decimal
absorption, and independent extraction of the original expected arrays.
The 1,168 distinct numeric synthetic/public boundary cases agree. Its count
of 1,182 boundary checks additionally includes the repeated boundary checks
inside eight air guards and six probability cases. It verifies 134 paired
attenuation results, 960 atmospheric grid cases, all 544 boundary abstentions,
eight air guards and all 100 real-path withheld states. The figure was viewed
and is legible.

The 64 new focused tests and preceding 568 related tests pass: **632 tests**,
zero failures/errors/skips. Tests cover original spectral expectations,
circular path changes, datum invariance, fixed returning image legs, separate
ground factors, below-plane equivalent heights, explicit weather units/policies,
unknown inputs, atmospheric distance and stable sound-energy averaging.
`artifacts/weather_validation_v1.xml` and `.json` retain the receipt and hashes.

| Latest completed generation | Identity |
|---|---|
| `weather_sources_v1` | `weather-sources-5335ca10d299cbc04cc5` |
| `weather_checks_v2` | `weather-checks-df86f7e6a8e55df76d23` |
| `weather_review_v2` | `weather-review-88225876adb2c9d6d6dc` |

These retain original source bytes, receipts/licence, assumptions, conditional
results, readiness, independent review/figure, frozen code and byte inventories.
Earlier development generations remain available; v2 is the completion
evidence. Artifact files are local and Git-ignored. Prior physical-data
attribution and the unsent historical barrier request remain intact.

Reproduce offline with fresh outputs and retained sources:

```powershell
.venv\Scripts\python.exe scripts\73_run_weather_checks.py --sources artifacts/weather_sources_v1 --output artifacts/weather_checks_NEW
.venv\Scripts\python.exe scripts\74_verify_weather_checks.py --inputs artifacts/weather_checks_NEW --output artifacts/weather_review_NEW
.venv\Scripts\python.exe -m pytest tests/test_favourable_propagation.py tests/test_sightline_boundary.py tests/test_multiple_edge_boundary.py tests/test_terrain_boundary.py tests/test_terrain_profile.py tests/test_ground_cover.py tests/test_propagation_canary.py tests/test_road_emission.py tests/test_road_direction.py tests/test_road_temporal.py tests/test_minor_road_linkage.py tests/test_road_input_audit.py tests/test_phase2_road.py tests/test_phase2b_road.py tests/test_phase2c_road.py tests/test_physical_scene.py -q -p no:cacheprovider --basetemp artifacts/weather_pytest_NEW
```

Script 72 copies verified retained method originals from a supplied research
directory and acquires the four pinned cuts into a fresh generation. Offline
evaluation does not require another network acquisition.

## Next bounded step

Audit **reflection-path inputs and the applicable method**, then benchmark a
controlled single vertical wall, including reflected power and retrodiffraction.
Review lateral-path applicability before adding a finite-obstacle bypass model:
the retained amended section explicitly scopes its lateral equations to
industrial sources. Source-line integration, historical geometry/traffic and
directional weather qualification, and independent quiet-end field evidence
still precede a credible real-area road-noise release.

11 October completion: the
[reflection audit and controlled single-wall checks](REFLECTION_PATH_AUDIT_AND_SINGLE_WALL_CHECKS_2026-10-11.md)
now pass 136 public comparisons, independent calculations and 718 related tests.
The lateral applicability review excludes segmented roads and reflection images;
screened-reflection interactions are the next bounded increment.
