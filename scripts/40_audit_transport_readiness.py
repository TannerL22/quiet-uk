"""Audit joint transport support in a bounded frozen native release, offline.

Exports counts and unresolved requirements, never summed levels or gap values.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.source_pilot import SourcePilot
from quiet_uk.transport_readiness import SOURCES, joint_reported_support


def run(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new audit destination')
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = SourcePilot.read_manifest(root)
    code_paths = ('scripts/40_audit_transport_readiness.py', 'src/quiet_uk/transport_readiness.py',
                  'src/quiet_uk/source_pilot.py', 'src/quiet_uk/evidence_semantics.py')
    code_hashes = {name: file_hash(ROOT / name) for name in code_paths}
    records = manifest['records']
    if manifest['schema_version'] != 3 or not 0 < len(records) <= 450:
        raise ValueError('Expected a bounded schema-3 release, at most 450 rasters')
    groups = defaultdict(dict)
    for record in records:
        if record['source'] not in SOURCES or record['metric'] not in ('Lden', 'Lday', 'Lnight'):
            raise ValueError('Unexpected source or metric')
        key = (record['site'], record['tile'], record['metric'])
        if record['source'] in groups[key]:
            raise ValueError('Duplicate source for a metric core')
        groups[key][record['source']] = record
    for layers in groups.values():
        if set(layers) != set(SOURCES):
            raise ValueError('A metric core lacks a source raster')
    metric_sets = defaultdict(set)
    for site, tile, metric in groups:
        metric_sets[(site, tile)].add(metric)
    if any(metrics != {'Lden', 'Lday', 'Lnight'} for metrics in metric_sets.values()):
        raise ValueError('A spatial core lacks a metric')
    # Core padding is excluded; spatial overlap would otherwise double-count.
    cores = {(r['site'], r['tile']): tuple(r['core_bounds']) for r in records}
    boxes = list(cores.values())
    for i, (w, s, e, n) in enumerate(boxes):
        if not np.isfinite([w, s, e, n]).all() or not w < e or not s < n:
            raise ValueError('Invalid core bounds')
        for ww, ss, ee, nn in boxes[:i]:
            if max(w, ww) < min(e, ee) and max(s, ss) < min(n, nn):
                raise ValueError('Audit cores must not overlap')
    inventory, source_hashes = [], {}
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024):
        for (site, tile, metric), records_by_source in sorted(groups.items()):
            arrays, grid, paths = {}, None, []
            core = cores[(site, tile)]
            for source in SOURCES:
                record = records_by_source[source]
                path = (root / record['path']).resolve()
                if (not path.is_relative_to(root)
                        or manifest['files'].get(record['path']) != record['sha256']
                        or file_hash(path) != record['sha256']):
                    raise CatalogueIntegrityError('Native audit source identity mismatch')
                if tuple(record['core_bounds']) != core:
                    raise ValueError('Sources disagree on owned core bounds')
                with rasterio.open(path) as raster:
                    window = from_bounds(*core, transform=raster.transform)
                    parts = [window.col_off, window.row_off, window.width, window.height]
                    if (raster.crs != rasterio.crs.CRS.from_epsg(27700)
                            or tuple(raster.res) != (10, 10) or raster.count != 1
                            or any(abs(v - round(v)) > 1e-7 for v in parts)
                            or window.col_off < 0 or window.row_off < 0
                            or window.col_off + window.width > raster.width
                            or window.row_off + window.height > raster.height
                            or not 0 < window.width <= 1000 or not 0 < window.height <= 1000):
                        raise ValueError('Expected owned integer-aligned native 10 m cells')
                    window = window.round_offsets().round_lengths()
                    identity = (raster.crs.to_string(), tuple(raster.window_transform(window)),
                                int(window.height), int(window.width))
                    if grid is not None and identity != grid:
                        raise ValueError('Native source grids do not align')
                    grid = identity
                    arrays[source] = raster.read(1, window=window, masked=True)
                paths.append((path, record['sha256']))
                source_hashes[record['path']] = record['sha256']
            counts = joint_reported_support(arrays)
            inventory.append(dict(site=site, tile=tile, metric=metric, core_bounds=list(core), **counts))
            if any(file_hash(path) != digest for path, digest in paths):
                raise CatalogueIntegrityError('Native source changed during audit')
            print(f'Audited {tile} {metric}', flush=True)
    if (root / 'manifest.json').read_bytes() != manifest_bytes:
        raise CatalogueIntegrityError('Native manifest changed during audit')
    if any(file_hash(ROOT / name) != digest for name, digest in code_hashes.items()):
        raise CatalogueIntegrityError('Audit code changed during evaluation')
    summary = []
    for metric in ('Lden', 'Lday', 'Lnight'):
        members = [item for item in inventory if item['metric'] == metric]
        cells = sum(item['cells'] for item in members)
        source_counts, sets = Counter(), Counter()
        for item in members:
            source_counts.update(item['reported_cells_by_source'])
            for item_set in item['disjoint_source_sets']:
                sets[tuple(item_set['sources'])] += item_set['cells']
        summary.append({'metric': metric, 'cells': cells, 'area_km2': cells * 100 / 1e6,
                        'reported_cells_by_source': dict(source_counts),
                        'all_three_reported_cells': sets[SOURCES],
                        'all_three_reported_percent': 100 * sets[SOURCES] / cells,
                        'no_sources_reported_cells': sets[()],
                        'disjoint_source_sets': [{'sources': list(sources), 'cells': number}
                                                 for sources, number in sorted(sets.items())]})
    report = {
        'schema_version': 1, 'status': 'support_inventory_only_no_combined_levels',
        'release_id': manifest['release_id'],
        'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'recipe': {'method': 'disjoint positive unmasked source support on owned aligned native cells',
                   'cell_size_m': 10, 'units': 'cell counts, percentages and square kilometres',
                   'code_hashes': code_hashes,
                   'runtime': {'python': sys.version, 'numpy': np.__version__,
                               'rasterio': rasterio.__version__, 'gdal': rasterio.__gdal_version__}},
        'source_hashes': source_hashes, 'summary': summary, 'metric_core_inventory': inventory,
        'product_periods': {source: {key: manifest['products'][source][key]
                                    for key in ('reference_period', 'reference_period_status')}
                           for source in SOURCES},
        'combination_status': 'not_qualified',
        'outstanding_requirements': [
            'Aircraft product/airport/version linkage and source-specific reference periods.',
            'Compatible metric definitions, assessment hours/time zone and receiver conventions.',
            'Provider domain and zero/nodata meanings before using any missing contribution or bound.',
            'Complete applicable source inventory: confirm aircraft mosaic airport scope and overlaps.',
            'Qualified construction/licence for any provider combined product or acquired aircraft grid.'
        ],
        'limitations': [
            'Counts cover owned native cores only; request padding is excluded.',
            'A reported cell is an unmasked finite provider number greater than zero, not measured total sound.',
            'All-three support is not proof of compatible periods, source completeness or acoustic accuracy.',
            'Zero and masked cells are unknown; no missing contribution is replaced with silence.',
            'The audit never calculates a total, a bound, an estimate, or a confidence interval.',
            'A future transport combination excludes sound sources unrepresented by road, rail and aircraft.'
        ]
    }
    output.mkdir(parents=True)
    (output / 'report.json').write_bytes(json_bytes(report))
    print(json.dumps({'status': report['status'], 'summary': summary,
                      'report': str(output / 'report.json')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, default=ROOT / 'artifacts/oxford_london_map_v1')
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/transport_readiness_v2')
    args = parser.parse_args()
    run(args.pilot, args.output)
