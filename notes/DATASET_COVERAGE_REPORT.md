# Quiet UK dataset coverage and evidence report

Added 12 September 2026. This is a read-only audit of the existing historical England catalogue. It measures mask-cell coverage, stored-value availability, and the evidence recorded by the supplied historical inputs. It does not change acoustic calculations, source qualification, source rasters, or provenance.

## PowerShell command

Run from the repository root:

```text
.venv\Scripts\python.exe scripts\21_report_dataset.py artifacts\england_catalogue_v3 data\processed\england\tiles data\processed\england_mask\england_100m_mask.tif data\processed\england\tile_status_manifest.json notes\source_grid_reconciliation_audit\source_grid_reconciliation.json config.json artifacts\england_dataset_report_v3
```

The command requires the manifest, reconciliation report, England mask, and production configuration SHA-256 identities to match the authoritative catalogue metadata. It uses the existing read-only `DatasetCatalogue` reader, refuses a non-empty report destination, stages both outputs, and publishes `dataset_report.json` and `dataset_report.md` only after computation and serialization complete. There is no overwrite, migration, network access, mosaic, or automatic repair.

## Denominator and categories

The denominator is the positive-cell count from the complete supplied England 100 m mask, including any land cells outside catalogue tile footprints. A mask cell is counted once. Areas are explicitly mask-cell footprint areas: `cell_count * 0.01 km²`. This is not exact physical land area because coastal mask cells can contain only part land.

Per-band coverage partitions the denominator into exactly:

- `qualified` — inherited from the catalogue tile's existing band qualification;
- `qualified_with_coverage_limitation` — inherited coverage limitation;
- `withheld_due_to_source_quality` — inherited tile-level source-quality withholding;
- `no_dataset_coverage` — positive mask cells with no owning catalogue tile.

Source-state counts use the same whole-mask denominator and include `grid_accepted`, `grid_rejected`, `outside_declared_coverage`, `missing_evidence`, and `no_dataset_coverage`. These are structural source-quality states, not proof of acoustic accuracy.

Value availability is separate from qualification. Lower-bound bands distinguish finite stored values from the legitimate `-9999` censored/unreported sentinel. The road/rail upper band counts finite stored upper bounds. The airport fraction counts zero and positive reported fractions. Each availability table is cross-tabulated by qualification, so finite values in withheld tiles remain visible without being presented as qualified data.

## Evidence statuses

Each road, rail, and airport evidence field has a value (or `null`), evidence references containing the input SHA-256, tile/source context, and field path, a status, a limitation note, a variation indicator, and full observations.

- `recorded_in_historical_input` — explicitly present in the supplied historical manifest or reconciliation report;
- `configured_only` — present only in the supplied current configuration and not proof of the settings used to create historical rasters;
- `not_established` — no value recorded in the supplied evidence;
- `conflicting` — descriptive historical and configured evidence disagree and the disagreement is retained.

Identity-critical conflicts fail report generation. Multiple recorded coverage identifiers remain visible rather than being reduced to the first value. A metric appearing only in a coverage label is not independently treated as a metric, and timestamps are not treated as reference years.

## Evidence normalization and schema version 2

The v2 evidence register retains one observation per supplied historical/configured field occurrence. Each observation records the input identity/hash, tile ID where applicable, source, exact field or alias path, original value, and normalized comparison value. Equal normalized values are grouped for display, but their supporting references are retained in full JSON.

Coverage identifiers accept only a nonempty string or a nonempty list of nonempty strings. Strings are not split on punctuation. Historical and configured values normalize to sorted identifier sets for comparison, so scalar versus singleton-list forms, list order, and duplicates do not create conflicts. Nested lists, objects, empty strings, and invalid element types fail clearly. Scalar fields retain their scalar or structured values; bounds and reference-period structures are not flattened.

Conflict scope is explicit. Contradictory aliases for the same tile/source fact are `conflicting`; equivalent aliases are not. Unequal identity-critical coverage sets fail with both normalized sets and their references. Different values across tiles set `varies_across_tiles: true` while leaving the status independent, so legitimate heterogeneous years, periods, or coverage IDs are not treated as errors merely because they differ. Historical and current configuration remain distinguishable; configuration is never substituted for historical evidence. Reference-year and reference-period observations retain separate kinds when their relationship is not explicitly established.

The report schema incremented from `1` to `2` for the observation/context and `varies_across_tiles` fields. Markdown shows the variation flag, historical/configured context, and a compact reference summary; the complete per-observation references remain in JSON.

## Real report

Outputs:

- `artifacts/england_dataset_report_v1/dataset_report.json`
- `artifacts/england_dataset_report_v1/dataset_report.md`
- `artifacts/england_dataset_report_v2/dataset_report.json`
- `artifacts/england_dataset_report_v2/dataset_report.md`
- `artifacts/england_dataset_report_v3/dataset_report.json`
- `artifacts/england_dataset_report_v3/dataset_report.md`

The v1 and v2 pairs are preserved. The corrected v2 report ID is `f02277cca7234bfcb1153309e3237039`; the latest v3 report ID is `30d02add47f54faaaa62bce9991c569c`.
Catalogue build ID: `304d0ae422a84bebb83bcb7a7379e236`
SQLite catalogue schema: `2`
Scientific validation contract: `2`
Publication contract: `1`

The v1 report schema is `1`; the v2 and v3 report schemas are `2`. The v2 and v3 reports were generated read-only from the existing v3 catalogue and unchanged source inputs.

### Authoritative mask coverage

| Category | Cells | % of authoritative land | Mask-cell footprint km² |
|---|---:|---:|---:|
| Total authoritative land | 13,086,924 | 100.000000% | 130,869.24 |
| Owned by catalogue tile | 13,086,924 | 100.000000% | 130,869.24 |
| No catalogue tile | 0 | 0.000000% | 0.00 |

The mask was validated as a binary `uint8`, nodata-0, north-up 100 m EPSG:27700 grid. Tile footprints were checked for integer mask-grid alignment and overlap before counting.

### Per-band qualification coverage

| Band | Qualified | Qualified with limitation | Withheld | No dataset coverage |
|---|---:|---:|---:|---:|
| `combined_reported_lower_db` | 9,713,511 (74.223026%) | 2,600,163 (19.868405%) | 773,250 (5.908570%) | 0 (0.000000%) |
| `road_rail_upper_db` | 13,085,126 (99.986261%) | 0 (0.000000%) | 1,798 (0.013739%) | 0 (0.000000%) |
| `airport_reported_lower_db` | 9,713,511 (74.223026%) | 2,601,961 (19.882143%) | 771,452 (5.894831%) | 0 (0.000000%) |
| `airport_reported_fraction` | 9,713,511 (74.223026%) | 2,601,961 (19.882143%) | 771,452 (5.894831%) | 0 (0.000000%) |

Every band reconciles exactly to the independently counted 13,086,924-cell denominator.

### v1/v2 numerical comparison

The following sections and counts are exactly equal between the preserved v1 report and corrected v2 report:

| Compared section | Result |
|---|---|
| `mask_denominator` | unchanged |
| `per_band_coverage` | unchanged |
| `source_state_land_cells` | unchanged |
| `value_availability` | unchanged |
| Processed tile count | 1,498 → 1,498 |
| Processed tile cells | 14,944,700 → 14,944,700 |
| Processed catalogue-owned land cells | 13,086,924 → 13,086,924 |
| Authoritative mask land cells counted | 13,086,924 → 13,086,924 |

The v2 processing elapsed time was approximately `24.481951` seconds; timing, report ID, timestamps, schema, and evidence representation are expected to differ.

### v2/v3 numerical comparison and scalar classification correction

The v3 correction tightens descriptive scalar configuration matching: a configured value is `recorded_in_historical_input` only when it agrees with every distinct normalized historical value for that field. A partial match is `conflicting`, even when historical values vary across tiles. Repeated equal historical values still produce a recorded match, while historical variation without configuration remains recorded with `varies_across_tiles: true`.

The following sections and processing counts are exactly equal between the preserved v2 report and the v3 report:

| Compared section | Result |
|---|---|
| `mask_denominator` | unchanged |
| `per_band_coverage` | unchanged |
| `source_state_land_cells` | unchanged |
| `value_availability` | unchanged |
| Processed tile count | 1,498 → 1,498 |
| Processed tile cells | 14,944,700 → 14,944,700 |
| Processed catalogue-owned land cells | 13,086,924 → 13,086,924 |
| Authoritative mask land cells counted | 13,086,924 → 13,086,924 |

The v3 processing elapsed time was approximately `26.564093` seconds. The change is limited to evidence classification and its JSON/Markdown representation; it does not alter numerical coverage, stored-value availability, acoustic calculations, catalogue contents, or source rasters.

### Stored-value availability

| Band | Availability | Cells | % of authoritative land |
|---|---|---:|---:|
| `combined_reported_lower_db` | Finite reported value | 8,070,572 | 61.668976% |
|  | Legitimate censored/unreported lower bound | 5,016,352 | 38.331024% |
| `road_rail_upper_db` | Finite stored upper bound | 13,086,924 | 100.000000% |
| `airport_reported_lower_db` | Finite reported value | 166,941 | 1.275632% |
|  | Legitimate censored/unreported lower bound | 12,919,983 | 98.724368% |
| `airport_reported_fraction` | Zero reported fraction | 12,919,983 | 98.724368% |
|  | Positive reported fraction | 166,941 | 1.275632% |

These availability totals do not promote values to qualified data. In particular, withheld tiles may contain finite stored values, and zero airport fraction means no reported airport pixels, not silence.

### Evidence findings and unresolved provenance

For all three sources, coverage identifiers, WCS versions, metric, reporting threshold, and endpoint are `configured_only` in the v2 report: the current `config.json` records them, while the historical `source_info` does not. Airport declared bounds are also present in the historical manifest for the explicit skip records; road and rail declared bounds remain `not_established`. Source-grid policy/version is recorded by the reconciliation report as structural evidence; the airport policy field is explicitly marked as varying across tiles and is not treated as a contradiction. Reference year or period remains `not_established` for road, rail, and airport.

The unresolved facts still requiring provider documentation or stronger historical records are the settings used to create the historical rasters, source reference periods, and independent provider/acoustic semantics. No year was inferred from coverage labels or build timestamps.

## Processing and limitations

- Processed tiles: `1,498`.
- Processed tile cells: `14,944,700`.
- Processed catalogue-owned land cells: `13,086,924`.
- Authoritative mask land cells counted: `13,086,924`.
- Elapsed report-generation time: v1 approximately `27.062891` seconds; v2 approximately `24.481951` seconds; v3 approximately `26.564093` seconds.

Processing reads the full mask in raster windows, verifies each tile once per run, reads its four bands together, and uses tile-sized mask windows. It does not build a national acoustic mosaic or call point lookup once per cell.

The report does not calculate minimum noise, average dB, rankings, candidate areas, or an interpretation of silence. Coverage and stored-value availability remain distinct from structural qualification and acoustic accuracy.
