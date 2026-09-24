Please perform an independent, thorough review of Quiet UK:
https://github.com/TannerL22/quiet-uk

Review the latest main branch and record the commit SHA you inspected. Review the entire project, not just the newest feature or the documentation. This is a review request: do not implement changes, push commits, deploy, or launch large data downloads. Read-only checks and bounded local tests are welcome.

PRODUCT INTENT
I want a beautiful, intuitive, general-purpose map of the UK that helps people understand where places are quieter or louder and what evidence supports that conclusion. Finding a quiet home and a quiet hiking route inspired the idea, but these are examples, not the product's boundaries.

The underlying data should eventually support credible academic research, such as studying relationships between environmental noise and house prices or stress. Research quality is an ambition, not a claim that the current data is already suitable. The product should make the evidence understandable without presenting model outputs as measured reality.

I am concerned about mission creep. Be candid about unnecessary complexity, weak assumptions, scientific mistakes and features that distract from this goal. Do not recommend a large rewrite or more infrastructure without explaining the concrete benefit and a smaller alternative.

CURRENT STATE — VERIFY THESE CLAIMS
- A local England-wide explorer uses historical 100 m outputs. It shows road/rail bounds with separate aircraft evidence; historical acquisition provenance is incomplete.
- A separate 10 m explorer uses original provider grids for four 10 × 10 km areas: Heathrow, Didcot, Oxford and the Chilterns, totalling 400 km².
- The regional release has separate road, rail and aircraft layers for Lden, daytime and night-time indicators. Road/rail reference 2021; the exact aircraft product's period remains unspecified. Sources are not presented as a validated numerical total.
- Below-cutoff, nodata and missing/domain gaps cannot reliably be distinguished everywhere. Unreported cells must not be treated as silence, zero dB, or ranked as the quietest.
- Latest work adds up to three saved points, source/time comparisons, release-specific links and exact CSV/JSON exports with provenance. Reported verification is 437 tests passed and two platform skips; examine what the tests actually establish.
- Full UK coverage, national source-linked 10 m construction, independent acoustic validation, public deployment, background sound and real noise-event histories are not complete.

ACCESS AND EVIDENCE
GitHub contains code, tests, documentation, dependency locks and selected audit reports. Large rasters, generated catalogues/maps/evidence bundles, local configuration and Python environments are excluded. A source-only clone cannot automatically reproduce the fully populated local app. Identify unavailable evidence explicitly; do not assume local artifact references prove a claim or that absent generated data means the corresponding implementation is missing.

Start with README.md and notes/ACTIVE_ROADMAP.md. Also inspect notes/RESEARCH_DATA_CONTRACT.md, SOURCE_REGIONAL_RELEASE.md, PLACE_COMPARISON.md, SOURCE_VIEWS_AND_TEMPORAL_FOUNDATION.md and REPRODUCIBLE_ENVIRONMENT.md. Then trace the relevant code in src/quiet_uk/, scripts/, explorer/, candidate_viewer/ and tests/. Treat older reviews as historical context, not conclusions to repeat.

REVIEW SCOPE
1. Product and scope: How close is this to the original goal? Which workflows genuinely help, what is missing, and what should be simplified, deferred or removed? Assess whether the separate national and regional explorers create confusion.
2. Implementation and architecture: Trace acquisition → validation → analytical storage → display → point lookup → comparison/export. Check correctness, module boundaries, duplication, dependencies, configuration, error handling, concurrency, publication consistency, caching, performance and maintainability. Evaluate the effort and costs of scaling coverage.
3. Scientific and geospatial quality: Audit source authority, licensing, provenance, hashes, metadata/period parsing, metric definitions, receptor heights, units, CRS, native grid alignment, resampling, energy-based calculations, censoring, missingness, coverage semantics and source compatibility. Distinguish spatial resolution from demonstrated accuracy and reproduction from independent validation. Check that UI and exports preserve these distinctions.
4. Temporal meaning: An annual average cannot distinguish steady noise from short loud events. Examine what the present data can actually establish about average exposure, background sound, peaks, event counts and quiet intervals. Do not infer unavailable histories from annual rasters.
5. Research fitness: State which analyses are defensible now and which are not. Consider spatial/temporal matching, exposure assignment, selection bias, uncertainty, confounding and limits on causal claims. Propose a practical independent validation design with explicit acceptance criteria.
6. UI/UX: Inspect the running UI if possible, including desktop and phone layouts, search, source/day/night controls, legends, quiet-end colours, unknown areas, comparisons, sharing and downloads. Assess visual quality, accessibility, responsiveness and misleading interpretations. Heathrow is a key case: road-only evidence must not imply low overall exposure near aircraft noise. Separate observed browser behaviour from source-code inference.
7. Reliability, security and operations: Assess tests and blind spots, clean-clone setup, reproducibility, integrity failures, input handling, exports, local-serving boundaries, third-party requests, dependency risks, attribution and readiness for public hosting. Distinguish present local risks from future deployment requirements.

NEXT PLANS — CHALLENGE THE ORDERING
The proposed next data step is broader verified native 10 m coverage through bounded tiled acquisition and serving, with explicit storage, request and operating budgets. Smaller interpolated pixels are not a substitute for better evidence. In parallel, evaluate the comparison workflow with real users, resolve aircraft period/coverage semantics and obtain independent validation. Longer-term aims include a deployable England release, country-specific UK integration and validated improvements where current data cannot distinguish quiet places. Recommend a different sequence if it would deliver more value or reduce scientific risk.

EXPECTED OUTPUT
- A plain-English assessment of where the project stands, its strongest parts and its biggest obstacles.
- A prioritized findings table: severity, confidence, affected files/lines or observed behaviour, supporting evidence, user/scientific impact and concrete remedy. Separate confirmed defects, design tradeoffs and unverified concerns.
- A clear account of what can and cannot currently be trusted for public use and research.
- The five highest-value next actions, with rough effort and dependencies; identify what to stop or postpone.
- A phased plan with bounded deliverables and measurable completion criteria. Finish by choosing ONE next implementation task and explaining why it comes first.
- A verification appendix listing inspected areas, commands/tests/browser checks, external sources and unavailable evidence.

Ground claims in the repository and reproducible examples. For external scientific or provider claims, consult authoritative primary sources and link them. Do not accept documentation or passing tests as proof of scientific validity. Do not claim to have tested inaccessible data or UI. If access is limited, continue the useful review and clearly identify what additional evidence is needed.
