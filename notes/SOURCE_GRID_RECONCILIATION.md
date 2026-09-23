# Source-grid reconciliation audit

Added 11 September 2026. This is a bounded, read-only audit of the historical England tile-status manifest. It does not revise the historical manifest, regenerate any production output, download from WCS, invoke the batch runner, or establish national acoustic accuracy.

## Audit method

Run the thin wrapper with an explicitly new output directory:

```text
.venv\Scripts\python.exe scripts\18_audit_source_grids.py \
  data\processed\england\tile_status_manifest.json config.json \
  notes\source_grid_reconciliation_audit
```

The utility reconstructs each target grid from the supplied manifest and applies the current versioned source-grid validator to the recorded `raw_grid`. It emits deterministic JSON and CSV rows for all three sources per scheduled tile. It records the requested geometry, recorded response geometry, WCS/coverage metadata, current policy decision, target-centre support, coarse 100 m England-mask evidence where available, prior alignment metadata, evidence classification, and a proposed disposition. A non-empty output directory is refused unless `--overwrite` is explicit.

The old manifest does not contain complete coverage IDs and WCS versions in every `source_info` record. The audit fills those fields from the supplied `config.json` and marks that provenance; it does not silently present them as historical observations. The report is stored at:

- `notes/source_grid_reconciliation_audit/source_grid_reconciliation.json`
- `notes/source_grid_reconciliation_audit/source_grid_reconciliation.csv`

## Reproduced historical counts

The audit covered 1,498 scheduled tiles and 4,494 source rows. The current validator reproduced the raw-grid counts in the historical manifest exactly:

| Source | Accepted | Rejected | Skipped / no raw grid |
|---|---:|---:|---:|
| Road | 1,495 | 3 | 0 |
| Rail | 1,495 | 3 | 0 |
| Airport | 1,010 | 96 | 392 |

The 1,495 road and 1,495 rail accepted responses satisfy the exact requested grid. The 1,010 accepted airport responses satisfy the documented WCS 2.0.1 one-cell padded native-grid policy and were historically aligned with nearest neighbour. The 392 airport rows are explicit outside-domain skips; they do not assert inaudibility or source absence.

## Rejected geometry patterns

The deterministic pattern counts are:

| Source | Pattern | Count |
|---|---|---:|
| Road / rail | response extent changed at west edge; x resolution rescaled | 2 each |
| Road / rail | response extent changed at west and south edges; x/y resolution rescaled | 1 each |
| Airport | configured-domain clipped at west edge; x resolution rescaled | 43 |
| Airport | configured-domain clipped at east edge; x resolution rescaled | 24 |
| Airport | configured-domain clipped at south edge; y resolution rescaled | 17 |
| Airport | configured-domain clipped at north edge; y resolution rescaled | 10 |
| Airport | configured-domain clipped at west and north edges; x/y resolution rescaled | 1 |
| Airport | configured-domain clipped at west and south edges; x/y resolution rescaled | 1 |

Representative road/rail records are `r0063c0000`, `r0064c0000`, and `r0065c0000`. The first two recorded 1,000 × 1,000 responses with west bound 82,645 rather than the requested 82,600 and x resolution 9.955 m. `r0065c0000` recorded 230 × 1,000 cells, west bound 82,645, south bound 5,335 rather than 5,300, x resolution 9.955 m, and y resolution about 9.847826 m. Road and rail coverage bounds are not declared in the supplied configuration, so the audit cannot reconcile these responses to a documented domain.

All 96 rejected airport records recorded 1,001 × 1,001 cells. Their response bounds match the WCS 2.0.1 padded request after clipping to the configured airport bounds `[333485, 93815, 594465, 574285]`, while one or both axes were rescaled to preserve the fixed response dimensions. This is consistent with provider/domain clipping, but it is an inference from metadata rather than proof of value semantics or provider resampling behaviour. The current validator therefore continues to reject them.

## Support and land evidence

Support was evaluated from recorded response bounds against target pixel centres, independently of decoded dB values. Examples from the audit are:

- `r0063c0000` and `r0064c0000`: 996,000 supported target centres and 4,000 without recorded support; the coarse land-mask intersection is zero and all 4,000 are outside the coarse land evidence.
- `r0065c0000`: 226,092 supported and 3,908 without recorded support; 70 missing-support cells overlap the expanded coarse land evidence and 3,838 do not.
- Airport `r0008c0025`: 610,128 supported and 389,872 without recorded support; 56,190 missing-support cells overlap the expanded coarse land evidence and 333,682 do not.

Land-only evidence cannot justify accepting these responses. Source support is established before the final 100 m land mask, and missing finite source coverage must not be turned into acoustic censoring merely because some missing cells are outside land. The `r0065c0000` result specifically shows that a rejected response is not explained solely by sea overlap.

## Dispositions and policy decision

No new acceptance policy was added. The production contract remains:

- road and rail: exact requested EPSG:27700 target grid only;
- airport: exact target grid, or the named one-cell padded native grid only for WCS 2.0.1, followed by the documented nearest-neighbour alignment.

The historical rejected rows are assigned disposition `C. requires future request-planning change; remains rejected`: the geometry is suggestive of clipping/rescaling, but the request/domain handling and provider value semantics are not proven. Explicit airport outside-domain skips retain their skip disposition and make no acoustic presence claim. The audit also reserves disposition D for incomplete or otherwise insufficient evidence; no historical raw-grid reject needed that fallback classification.

The strict downloader is ready as a fail-fast implementation for a future controlled run whose requests are known to return the exact or documented padded grids. It is not cleared for another England-wide run on the basis of this audit: the six road/rail rejects and 96 airport rejects still require request-planning/domain evidence or a separately justified provider contract. No future request strategy or broadened acceptance policy was implemented here.

Historical raw-grid metadata cannot prove whether rejected source values had already been resampled by the provider. The accepted airport records and pilot notes document the earlier nearest-neighbour alignment path, but that does not establish the processing history or acoustic correctness of rejected responses.

### Future request-planning dependency (not implemented)

A safe future investigation would derive an in-domain request for each edge tile, obtain authoritative road/rail domain bounds, and ask the provider for a response whose dimensions and affine are the native 10 m grid of that request. The response would still have to pass the exact validator, with complete target-centre support, before any acoustic values are used. For airport edges, a smaller request with provider-guaranteed variable dimensions or an authoritative contract for the clipped response would be required; reprojecting the observed fixed-dimension, rescaled response a second time is not an accepted substitute. This strategy is deliberately only a dependency to investigate, not a production policy or a claim that the historical responses are recoverable.

## Verification and boundaries

The focused regression set passed:

```text
.venv\Scripts\python.exe -m pytest -q tests\test_raster.py tests\test_tiling.py tests\test_source_grid_audit.py tests\test_build_identity.py
55 passed in 1.00s

.venv\Scripts\python.exe -m pytest -q
190 passed, 1 skipped in 7.87s

git diff --check
passed (Git emitted only line-ending normalization warnings for existing modified files)
```

The decoder now rejects eligible finite raw cells whose finite scale/offset transformation becomes non-finite, before acoustic combination or output publication. Supported raster masks, declared finite nodata, and explicitly declared NaN nodata remain excluded; scale and offset are still applied before decoded-zero censoring. Decoder errors include source/tile context in production loading.

The full suite and `git diff --check` are the final verification gates for this change. The audit did not modify `data/`, `results/`, the historical manifest, historical validation notes, or production configuration. These checks establish output-integrity and ingestion-policy behaviour only; they do not independently establish national downloader compatibility, provider nodata semantics, revalidate historical acoustic values, or prove national acoustic accuracy.
