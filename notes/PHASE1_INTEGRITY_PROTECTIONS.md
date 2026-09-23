# Phase 1 integrity protections

Added 11 September 2026. This note describes the maintenance protections added after the historical Phase 1 validation runs; it does not revise those historical results.

## Protections

- Coverage discovery now makes Lden eligibility a prerequisite, recognises `Lden`, `L_den` and `L-den`, filters explicit conflicting source families, and reports equal-preference ambiguity.
- Production tile validation now checks the finite `-9999` nodata contract, exact outside-land masking, required bands, airport fraction states, censored lower-bound relationships, configured road/rail censor floors, optional `combined_upper_db`, and the existing 0–150 dB dataset sanity range. The range is a dataset sanity check, not a universal physical law. National QA reuses the same semantic checks.
- New manifests carry a versioned `build_identity` containing a readable canonical payload and SHA-256 fingerprint. It covers effective grid and metric settings, thresholds including null-versus-number, WCS endpoints/coverage IDs/protocols/formats/bounds, source decoding and alignment settings, the land-mask content hash, the current output schema, and hashes of the explicitly listed Phase 1 implementation files.
- The production configuration now has one shared normalizer. At the batch boundary, an explicit `run_batch(..., mask_path=...)` is authoritative, is resolved to one effective absolute file, and that effective configuration is used for scheduling, processing, validation, and identity hashing. A configured mask is accepted only when it resolves to the same file. The caller's mapping is never mutated. The England runner resolves config-relative mask paths from the project root, so launching it from another working directory is supported; direct tile processing without a mask remains supported for synthetic or lower-level use.
- Before any manifest creation, output mutation, cleanup, or network work, the production configuration must be complete and valid: source endpoints and coverage IDs, supported WCS versions, effective formats and thresholds, finite numeric settings, valid grid divisibility, and finite non-degenerate coverage bounds. Omitted road/rail reporting thresholds default to `40`; airport `null` remains valid and selects the four-band schema. Opaque WCS identifiers are preserved.
- A new manifest may only be created when the reserved `tiles` and `temporary_10m` locations are absent or genuinely empty. Existing compatible manifests remain resumable; legacy or incompatible state is rejected untouched. Unrelated files elsewhere under the output root are allowed, but a conflicting standard manifest path is rejected rather than auto-discovered or overwritten.
- New manifests also carry storage ownership metadata: a storage schema version and the canonical absolute output-root path. Resume requires supported metadata and an exact canonical-root match; older manifests remain unmodified and unsupported. Completed records must point to the scheduled tile's canonical `<output-root>/tiles/<tile-id>.tif` path, including after symlink or junction resolution.
- Each batch acquires nonblocking operating-system locks for both the canonical output root and canonical manifest path, in deterministic order with duplicate resources collapsed. Lock files live in a stable per-user temporary directory, are SHA-256 named, and are retained after release; only the active OS lock controls contention. Normal exceptions and process termination release the OS-held locks automatically.
- Worker status transitions, retry/error history, snapshot creation, JSON serialization, and atomic manifest publication are coordinated by one per-run reentrant state lock. Network calls, raster work, rate limiting, and backoff waits remain outside that state lock. Interruption waits for already-running workers to settle, cancels work not yet started where possible, and preserves completed records.
- Source-grid validation now applies named versioned policies before production alignment. Road and rail accept only `exact-target-grid v1` (EPSG:27700, requested resolution, dimensions, north-up orientation, transform and bounds). Airport accepts that exact grid or the documented `airport-wcs20-padded-native-grid v1` response: one extra 10 m cell in each dimension with the precisely established half-cell offset, and only for WCS 2.0.1.
- Source-footprint support is tracked separately from decoded acoustic values. The permitted airport alignment reprojects an all-valid geometric support array as well as the dB values; target cells without support inside expected coverage fail before output publication. A geometrically valid all-nodata raster remains an explicit unreported source, while a truncated or shifted production response is rejected rather than converted into censoring.
- Declared coverage bounds are EPSG:27700 and classify target pixel centres with inclusive boundaries. An airport tile with no centres inside declared bounds skips its request and is recorded as outside declared coverage; this does not assert inaudibility. Partial airport coverage records inside/outside counts and rejects finite values outside the declared domain. Any road or rail target-centre exclusion fails because incomplete finite upper-bound support cannot be inferred.
- Source diagnostics retain the existing decoding keys and add reported-finite, exclusive and overlapping nodata/mask/non-finite counts, decoded-zero censoring counts, policy/version, alignment reason, coverage state and geometric-support counts. Declared nodata and masks are interpreted as unreported under the current dataset-specific provider policy; that interpretation is not universal. Unexpected raw NaN/Infinity fails unless NaN is explicitly declared as the dataset nodata representation. A finite eligible raw value that becomes non-finite after finite scale/offset decoding now fails immediately, with bounded index evidence and source/tile context in the production loader; it is never converted to acoustic censoring.
- The read-only source-grid reconciliation audit reconstructs target grids from a supplied manifest/configuration, applies the current validator to recorded raw-grid metadata, reports provenance for configuration-supplied WCS fields, and writes deterministic JSON/CSV only under an explicitly supplied audit directory. It does not invoke the runner, download, mutate manifests, or broaden source-grid acceptance.

The identity excludes worker and retry settings, logging/progress, HTTP timeout, output and manifest locations, selected tile IDs, run limits, documentation, and unrelated Phase 2 research files. The land-mask path itself is excluded; identical bytes at another path retain the same scientific identity.

## Resume behaviour

Legacy manifests without a fingerprint or storage metadata, internally inconsistent identity payloads, mismatched fingerprints, changed full schedules, unexpected records, incompatible stored tile definitions, and invalid stored output paths are rejected before manifest rewrite, staging cleanup, or tile processing. Existing outputs are left untouched. Start a distinct future scientific build in a new output root with a new manifest; do not retrofit metadata onto the frozen baseline manifest.

The runner provides cooperative locking for this runner on supported local filesystems. It does not claim distributed locking across machines or reliable semantics on arbitrary network filesystems. Persistent coordination files are not production data and are not removed on release.

## Verification

Using the project environment:

```text
.venv\Scripts\python.exe -m pytest -q
190 passed, 1 skipped in 7.87s

git diff --check
passed (Git emitted only line-ending normalization warnings for existing modified files)
```

The synthetic tests exercise integrity, configuration normalization, ownership, locking, interruption, resume behaviour, source-grid policy enforcement, support coverage and encoding diagnostics only; they do not independently validate national acoustic accuracy, provider nodata semantics, live WCS behaviour, or revalidate the production dataset. No national or pilot outputs were regenerated.
