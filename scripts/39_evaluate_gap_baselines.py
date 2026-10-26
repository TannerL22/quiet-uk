"""Profile native unreported areas and test interpolation on whole held-out patches.

No source rasters are changed and no prediction of an actual gap is exported.
Requires the research extra (SciPy); runs offline from a pinned local release.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import rasterio
from rasterio.windows import from_bounds

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.gap_evaluation import component_inventory, evaluate_core, summarise


def run(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new evaluation destination')
    manifest_bytes = (root/'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    records = sorted([r for r in manifest['records'] if r['source']=='road' and r['metric'] in ('Lden', 'Lnight')], key=lambda r:r['id'])
    if not records or len(records)>120:
        raise ValueError('Expected a bounded road release, at most 120 metric cores')
    # Fixed systematic selection, spread over the release; no target-based tuning.
    tiles = sorted({(r['site'], r['tile']) for r in records})
    selected = {tiles[i] for i in np.linspace(0, len(tiles)-1, min(12, len(tiles)), dtype=int)}
    inventory, patches, hashes = [], [], {}
    with rasterio.Env(GDAL_CACHEMAX=128*1024*1024):
        for record in records:
            path = (root/record['path']).resolve()
            if not path.is_relative_to(root) or file_hash(path) != record['sha256']:
                raise CatalogueIntegrityError('Native evaluation source identity mismatch')
            hashes[record['path']] = record['sha256']
            with rasterio.open(path) as raster:
                window = from_bounds(*record['core_bounds'], transform=raster.transform).round_offsets().round_lengths()
                if window.width > 1000 or window.height > 1000 or tuple(raster.res) != (10, 10):
                    raise ValueError('Expected at most 1000 × 1000 native 10 m cells per core')
                data = raster.read(1, window=window, masked=True)
            values = data.filled(np.nan)
            reported = np.isfinite(values) & (values > 0)
            cutoff = record['evidence']['reporting_cutoff_db']
            if cutoff not in (35, 40):
                raise ValueError('Expected qualified road reporting cutoffs')
            item = component_inventory(values, reported, cutoff)
            item.update(record_id=record['id'], metric=record['metric'], cutoff_db=cutoff)
            item['zero_cells'] = int((values==0).sum())
            item['nodata_or_nonfinite_cells'] = int((~np.isfinite(values)).sum())
            inventory.append(item)
            if (record['site'], record['tile']) in selected:
                seed = int(hashlib.sha256(record['id'].encode()).hexdigest()[:8], 16)
                for patch in evaluate_core(values, reported, cutoff, seed):
                    patches.append(dict(record_id=record['id'], metric=record['metric'], **patch))
            if file_hash(path) != record['sha256']:
                raise CatalogueIntegrityError('Native source changed during evaluation')
            print('Evaluated', record['id'], flush=True)
    if (root/'manifest.json').read_bytes() != manifest_bytes:
        raise CatalogueIntegrityError('Native manifest changed during evaluation')
    buckets = Counter()
    for item in inventory:
        for bucket in item['buckets']:
            buckets[(item['metric'], bucket['size_cells'], bucket['ring'])] += bucket['components']
    report = {
        'schema_version': 1, 'status': 'evaluation_only_no_gap_predictions', 'release_id': manifest['release_id'],
        'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(), 'source_hashes': hashes,
        'recipe': {'source':'road', 'metrics':['Lden','Lnight'], 'cell_size_m':10, 'connectivity':8,
                   'patch_widths_m':[30,90,210], 'perimeter_width_cells':1, 'patches_per_core_size_stratum_max':8,
                   'selected_cores':sorted(selected), 'selection':'systematic spatial core selection; seeded nonoverlapping patches per size',
                   'methods':['nearest','idw8 (inverse squared distance, eight closest perimeter cells)','harmonic interpolation in dB'],
                   'model_parameters':'fixed before inspecting held-out errors; no fitted noise propagation model'},
        'limitations': [
            'Truth is an annual model grid, not independent measured sound.',
            'Holdouts are entirely reported squares with fully reported one-cell perimeters: optimistic interpolation support.',
            'Near-floor results cover reported values only, never below-cutoff truth.',
            'Unknown zero/nodata cells have unresolved censoring and domain meanings; no ceiling or quietness assigned.',
            'All three interpolators stay within perimeter extrema and cannot establish a quieter enclosed interior.',
            'Tile-border components are excluded, not assumed open or counted as complete holes.',
            'Different sizes may sample nearby cells; no independent sample, confidence interval or calibrated prediction interval claimed.',
            'No method selected for deployment; actual unknown cells are never modified or predicted by this script.'
        ],
        'unknown_core_inventory': inventory,
        'enclosed_component_buckets': [{'metric':metric,'size_cells':size,'ring':ring,'components':count}
                                      for (metric,size,ring),count in sorted(buckets.items())],
        'holdout_summary': summarise(patches),
    }
    output.mkdir(parents=True)
    (output/'report.json').write_bytes(json_bytes(report))
    if patches:
        with (output/'heldout_patches.csv').open('w', newline='', encoding='utf-8') as stream:
            writer = csv.DictWriter(stream, fieldnames=list(patches[0])); writer.writeheader(); writer.writerows(patches)
    print(json.dumps({'status':report['status'],'profiled_metric_cores':len(inventory),
                      'heldout_patches':len(patches)//3,'summary':str(output/'report.json')}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, default=ROOT/'artifacts/oxford_london_map_v1')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/gap_evaluation_v2')
    args = parser.parse_args()
    run(args.pilot, args.output)
