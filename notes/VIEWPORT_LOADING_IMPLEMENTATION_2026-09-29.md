# Regional display loading and correctness fixes — 29 September 2026

Implemented the viewport-aware loading increment from the project audit. Published analytical rasters, release IDs, source evidence, native observations and full evidence bundles are unchanged. The runtime creates display-only overviews from the existing verified PNGs.

**Display behavior.** Wide views load one overview, with a maximum side of 1024 pixels. Detailed views load intersecting core images, then a small optional margin. Visible cores take priority; if all visible cores cannot fit, the view uses a complete overview rather than showing an arbitrary subset. The limits are eight detailed images and 16,777,216 decoded pixels, including the overview: 64 MiB of RGBA payload. These are active-source/pixel limits, not a measured browser/GPU memory guarantee.

Images outside the new viewport are removed on movement completion. Retained nearby images are reused. Hiding the overlay removes its sources. Camera movement and resize reconcile the plan. The overview provides a fallback while detail loads; detailed layers become visible together after they are ready, avoiding blended overview/detail imagery in the settled view. Loading, hidden, outside-view and failed-image states are explicit. Native point inspection remains independent.

On the installed 5,000 km² release, the overview is **1024 × 528**, or **2,162,688 decoded RGBA bytes**, versus **537,931,996 bytes** for the former complete 50-image indicator set. Road Lden transfers **216,199 bytes** for its overview, compared with approximately 16.75 MB for the old complete set. These are image payload comparisons, not claims about total browser memory or basemap transfer. At the reproduced Heathrow boundary point, the desktop detail plan used two cores plus the overview: about 23.7 MB decoded.

The backend builds overviews serially, reading one PNG at a time and selecting nearest existing pixels with their alpha. It does not average noise values, fill unknown cells, or modify analytical grids. A maximum of eight completed overviews and 8 MiB of encoded overview bytes are cached in memory. Cache hits still verify private-source fingerprints. No overview is written into a frozen release or added to its evidence ZIP.

**Correctness and presentation fixes.**

- Selected-point links retain full numerical coordinate precision; camera/display rounding stays separate. The reproduced Road/Night boundary remains **35.3 dB(A)** after reload, rather than changing to 35.0.
- Point requests and downloads include their expected release. The API rejects a mismatch with **409 / `release_mismatch`**. The frontend also checks response identity, including coverage and display metadata, and clears unavailable point feedback.
- Zero-code and TIFF-nodata legends have distinct beige/blue-gray colors and opposite hatch directions.
- Regional acquisition QA, display construction and point lookup reject nonidentity or nonfinite scale/offset metadata. Existing QA records remain byte-compatible; old frozen releases are not rewritten. The installed release has identity encodings.
- Sampled small explanatory text, map captions, credits and legend text are darker, with slightly larger minimum sizes for credits and legend labels. This improves the specific contrast defects found in the audit; it is not a full accessibility certification.

The HTTP interface and launcher requirement are now **version 3**, because the frontend needs the new display metadata/overview routes. Restart an existing Quiet UK server before using these changes. The launcher refuses to reuse version 2. Future same-interface code-update detection remains separate work from audit F08.

**Verification.**

- Full Python suite: **523 passed, 3 skipped, 3 deselected**, 167.90 s. Skips are the existing Windows SQLite/symlink capability cases.
- Browser policy/recipe regression suite: **9 passed**, using Node's built-in test runner. It is included in CI alongside Python checks and has no package-install or browser-path dependency.
- Targeted regional source/display/snapshot tests: **62 passed, 1 skipped**. They cover nearest-pixel/alpha preservation, overview cache bounds, cached-source tampering, HTTP identity rejection and unsupported encoding at all three entry points.
- Real-release HTTP check: **200 points / five clients**, median **38.9 ms**, p95 **57.6 ms**, maximum **89.0 ms**. The full Heathrow point JSON exactly matched the pre-change audit baseline. This is local serving acceptance for these examples, not national capacity or reduced-reader-cache pressure testing.
- All nine overviews served at 1024 × 528. First uncached builds observed around **0.97–1.21 s**; warm HTTP responses around **5–29 ms**. One cached selection was evicted while exercising all nine; the eight-entry limit is deliberate. These timings exclude startup and external basemap loading.
- In-app browser checks at **1440 × 900** and **390 × 844** covered overview/detail transitions, panning, hide/show, distant-core navigation, all three sources, Unknown/outside states, coordinate reload and legend styles. A deliberately corrupted PNG in the owned temporary snapshot produced an explicit incomplete-overlay warning while point evidence remained available. Original release files were not corrupted.
- The first full run had one Windows connection abort in the unchanged screening API's invalid-Host test. That entire module passed **15/15** on rerun, and the subsequent full suite passed. No screening implementation/test workaround was introduced.

The browser checks are manual integration checks; the CI addition tests the pure loading/recipe policy, not a full browser session. Remote CI, physical phones, screen readers, browser heap/GPU memory and the planned reduced-reader-cache experiment were not run. The larger mobile place-summary/comparison redesign remains a separate increment.

Local measurements, request traces and screenshots are in [viewport_loading_checks](../artifacts/viewport_loading_checks), which is ignored by Git.

```text
.venv\Scripts\python.exe -m pytest -q -rs --strict-markers
node --test tests/pilot_display.test.cjs
```

![Final desktop overview](../artifacts/viewport_loading_checks/desktop-overview-final.jpg)
