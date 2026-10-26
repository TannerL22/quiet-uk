# Derived ground-average rounding correction

The rounding rejection found in the [13 October integration checks](CORRECTED_ROAD_INTEGRATION_2026-10-13.md)
is fixed. The affected road now passes its finer-spacing check with a maximum
change of **0.0790218891 dB**, within the existing 0.1 dB criterion. **56 of the
59 affected roads now pass both numerical checks; three remain withheld**:
two exceed their sampling budgets and one has inconsistent obstacle/terrain
heights. All 58 cases selected for finer checks, including the two controls,
now have passing results.

All **1,050 related tests pass**, including 37 new regression cases. A fresh
native replay evaluates all **373 primary and 256 finer positions** on the
repaired road. The **628 previously numeric observations remain bit-for-bit
unchanged** in attenuation transfer, source/air power and point energy. The
previously rejected point recovers conditional calculations in all eight bands
under both homogeneous and favourable weather.

This remains an offline conditional direct-road calculation. The map receives
no new predictions, and the all-inclusive noise value remains unavailable.

## Cause and correction

On road `04C8025A-1D65-448D-A443-07DAF07B9744`, the source-side ground mean
covers 0 to 12.968881310556787 m. Two valid intervals meet at
3.09681158594688 m; both have factor 1. Their exact mean is 1, but floating-point
endpoint subtraction and summation produce `1.0000000000000002`. The strict
ground-factor validator correctly rejects a supplied factor above 1, exposing
the error in the derived average.

Both whole-profile and clipped side averages now preserve their existing
floating-point result when it lies in `[0, 1]`. If it falls outside that range,
the calculation uses exact rational arithmetic on the represented interval
endpoints and validated factors. The resulting mean then passes through the
unchanged strict validator. No tolerance is applied to supplied factors.

The side-average function also checks contiguous increasing intervals, finite
positive requested length and full coverage of that range. Empty or unknown
support returns an unavailable average. Negative factors, factors even one
floating-point step above 1, booleans, strings and nonfinite values remain
rejected. Gaps and overlaps are rejected. An invalid input cannot be hidden by
averaging it with valid factors.

The production change is confined to the two averaging functions and their two
private helpers in `terrain_boundary.py`, with the standard-library `Fraction`
import. Every other original function in that module retains identical syntax;
all other source and evaluator files from the preceding run retain their bytes.
The source factors, terrain, buildings, road geometry, traffic, speeds, weather,
atmospheric tables and acoustic equations are unchanged.

## Replay and independent review

The replay uses the original source cells and observation positions with fresh
native geographic preparation and the current kernel. All 629 native-profile
hashes match their original records. Every available earlier materialized
kernel scene also matches its fresh counterpart. The earlier primary road
integral is preserved after verifying every primary point energy.

The finer rule still uses four positive equally weighted interior positions
per final leaf. All 256 finer observations now support a complete road sum;
the repaired road's change is below the declared 0.1 dB criterion for each
A-weighted period and Lden result, under both weather states separately.

The separate reviewer checks:

- All 629 original identities and native-profile references, including exact
  equality of all 628 previously numeric observation results.
- Independent source/air products and point energies at every replayed position.
- Primary quarter-point weights, all 256 finer positions and weights, complete
  source length, road sums and all period/Lden changes.
- Exact rational coverage and mean of the original 427-interval ground support.
  Its clipped coverage ratio and mean are both exactly 1. The frozen old function
  reproduces the rejection; the corrected function returns 1.
- Three independently reconstructed native profiles, including the repaired point.
- The complete 1,444-entry ledger. Exactly one road/case entry changes; the other
  1,443 entries are identical to the preceding reviewed ledger.
- The 1,050 passing test identities, including every preceding 1,013 test identity
  and the 37 new rounding, coverage and invalid-input cases.

The new ledger restores the repaired road's verified primary integral only after
the finer check passes. It retains all other values and outstanding support
statuses. The old datasets, ledgers and validation receipts remain intact.

Across the preceding 97-case adaptive population, 58 cases now have a passing
finer check, 14 retain a primary diagnostic without a finer check, 24 are held by
sampling budgets and one by height consistency. The largest current passing
finer-spacing change is 0.0790218891 dB.

## Evidence and reproduction

- [Regression tests: 1,050 passed](../artifacts/ground_average_validation_v1.xml)
- [Fresh native replay](../artifacts/ground_average_replay_v1/report.json)
- [Independent review](../artifacts/ground_average_review_v2/report.json)
- [Current validation receipt and code hashes](../artifacts/ground_average_validation_v2.json)
- [Updated complete road/case ledger](../artifacts/ground_average_replay_v1/road_ledger.json)
- [Original exact-arithmetic diagnosis](../artifacts/ground_average_rounding_diagnostic_v1.json)

The replay generation is `ground-average-native-replay-b69e3424f2b6444fe450`.
Its review is `ground-average-fix-review-0da266ca04ba175307ba`. The second review
generation includes snapshots of its accounting and native-reconstruction
dependencies. Both review generations preserve the same numerical outcome.

With the existing sealed source generations and passing regression XML installed,
run from the repository directory using fresh output paths:

```powershell
.venv\Scripts\python.exe -u scripts\107_replay_ground_average_fix.py --output artifacts\ground_average_replay_next
.venv\Scripts\python.exe -u scripts\108_verify_ground_average_fix.py --inputs artifacts\ground_average_replay_next --output artifacts\ground_average_review_next --junit artifacts\ground_average_validation_v1.xml --receipt artifacts\ground_average_validation_next.json
```

The earlier 101/106 study controllers pin their reviewed implementation and
intentionally reject changes to that implementation. The new replay and review
controllers verify the approved ground-average revision without relaxing those
earlier guards. Historical reproduction of the preceding studies requires their
matching source snapshots; this follow-up uses the corrected live source.

## Remaining work

Address the 24 sampling-budget cases, resolve the remaining height support and
check the 14 older primary-met cases without finer checks. The other retained
roads still need their outstanding numerical work. Missing roads, reflections,
lateral paths, transmission and nonroad sources remain outside this result.
Traffic, ground, height and weather assumptions retain their original status.

The 0.1 dB checks establish numerical consistency under declared inputs. They
provide neither a rigorous acoustic error bound nor field validation. No local
annual favourable-condition probability or all-inclusive noise value is supplied.
