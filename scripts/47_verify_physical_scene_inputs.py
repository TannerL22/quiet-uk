"""Verify exported physical inputs against original building records; render QA.

Reads immutable generations. Saves a separate review/verification artifact;
does not change the analytical inputs or assign noise/height uncertainty.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch, Rectangle
import numpy as np
import rasterio
import shapefile
from shapely import from_wkt
from shapely.geometry import box, shape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if not inputs.is_relative_to(ROOT / 'artifacts') or not output.is_relative_to(ROOT / 'artifacts') or output.exists():
        raise ValueError('Use immutable inputs and a fresh workspace review directory')
    inventory = json.loads((inputs / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        if file_hash(inputs / name) != digest:
            raise ValueError('Input checksum mismatch')
    report = json.loads((inputs / 'report.json').read_bytes())
    with (inputs / 'building_heights_bng.csv').open(encoding='utf-8', newline='') as stream:
        rows = list(csv.DictReader(stream))
    if not 0 < len(rows) <= 100000:
        raise ValueError('Unbounded building table')
    reviews = json.loads((inputs / 'building_heights_review_wgs84.geojson').read_bytes())['features']
    review_lookup = {feature['properties']['source_id']: feature['properties'] for feature in reviews}
    if len(review_lookup) != len(reviews) or len(review_lookup) != len(rows):
        raise ValueError('Review identity/count mismatch')
    reader = shapefile.Reader(str(inputs / 'original_building_components/TQ_Building.shp'))
    counts = {region: Counter() for region in ('core', 'halo')}
    seen, geometries = set(), []
    core = box(*report['core_bounds_bng'])
    for row in rows:
        identity, oid = row['source_id'], int(row['source_record_number'])
        if identity in seen:
            raise ValueError('Duplicate exported identity')
        seen.add(identity)
        original = reader.shapeRecord(oid)
        attributes = original.record.as_dict()
        geometry = from_wkt(row['geometry_wkt_bng'])
        if (attributes['ID'] != identity or attributes['FEATCODE'] != int(row['feature_code'])
                or not geometry.equals_exact(shape(original.shape.__geo_interface__), tolerance=0)):
            raise ValueError('Export changed original identity, attributes or geometry')
        candidate = row['status'] == 'provisional_surface_height'
        if row['status'] not in ('provisional_surface_height', 'withheld'):
            raise ValueError('Unknown height status')
        if (candidate != bool(row['provisional_height_agl_m']) or candidate == bool(row['withheld_reason'])
                or row['acoustic_obstacle_height_m'] or row['height_uncertainty_m']):
            raise ValueError('Candidate/missing/evidence states inconsistent')
        if candidate and (float(row['provisional_height_agl_m']) != float(row['sample_median_m'])
                          or float(row['provisional_height_agl_m']) < 2):
            raise ValueError('Candidate differs from recorded median')
        properties = review_lookup[identity]
        height = float(row['provisional_height_agl_m']) if candidate else None
        if properties['status'] != row['status'] or properties['provisional_height_agl_m'] != height:
            raise ValueError('CSV and review height disagree')
        in_core = geometry.intersects(core) and geometry.intersection(core).area > 0
        if in_core != (row['intersects_core'] == 'True'):
            raise ValueError('Core membership differs from original geometry')
        for region in ('halo', 'core') if in_core else ('halo',):
            counts[region]['footprints'] += 1
            counts[region][row['status']] += 1
        geometries.append(geometry)
    reader.close()
    if counts != report['building_heights']['counts']:
        raise ValueError('Measured counts differ from report')

    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    halo = report['halo_bounds_bng']
    extent = (halo[0] / 1000, halo[2] / 1000, halo[1] / 1000, halo[3] / 1000)
    with rasterio.open(inputs / 'terrain_odn_2m.tif') as ds:
        terrain = ds.read(1, out_shape=(600, 600), masked=True)
    with rasterio.open(inputs / 'dtm_survey_code_2m.tif') as ds:
        survey_codes = ds.read(1, out_shape=(600, 600))
    active = sorted(int(code) for code in np.unique(survey_codes) if 0 < code < 65535)
    survey_lookup = {row['code']: row for row in report['survey_records']}
    colours = ['#17becf', '#ff7f0e', '#9467bd', '#bcbd22'][:len(active)]
    display_codes = np.full(survey_codes.shape, np.nan)
    for i, code in enumerate(active):
        display_codes[survey_codes == code] = i
    fig, axes = plt.subplots(1, 3, figsize=(16, 6), constrained_layout=True)
    im = axes[0].imshow(terrain, extent=extent, cmap='terrain', vmin=15, vmax=60)
    fig.colorbar(im, ax=axes[0], label='Ground elevation (m above ODN)', shrink=.75, extend='both')
    axes[0].set_title('Terrain: complete native 2 m input')
    axes[1].imshow(display_codes, extent=extent, cmap=ListedColormap(colours),
                   vmin=-.5, vmax=len(active)-.5)
    handles = []
    for colour, code in zip(colours, active):
        survey = survey_lookup[code]
        area = report['coverage']['halo']['survey_code_counts'][str(code)] * 4 / 1e6
        handles.append(Patch(facecolor=colour,
                             label=f'{survey["survey_start"]} to {survey["survey_end"]}\n{area:g} km²'))
    axes[1].legend(handles=handles, loc='upper left', fontsize=8, framealpha=.95)
    axes[1].set_title('Survey date support — inferred')
    xy = np.array([(geometry.centroid.x / 1000, geometry.centroid.y / 1000) for geometry in geometries])
    candidate_mask = np.array([row['status'] == 'provisional_surface_height' for row in rows])
    axes[2].scatter(xy[~candidate_mask, 0], xy[~candidate_mask, 1], s=5, c='#999999',
                    marker='x', linewidths=.4, label='Withheld')
    heights = [float(row['provisional_height_agl_m']) for row in rows if row['provisional_height_agl_m']]
    im = axes[2].scatter(xy[candidate_mask, 0], xy[candidate_mask, 1], s=4, c=heights,
                         cmap='viridis', vmin=2, vmax=25, label='Provisional')
    fig.colorbar(im, ax=axes[2], label='Provisional median surface height (m)', shrink=.75, extend='max')
    axes[2].legend(loc='upper right', markerscale=3)
    axes[2].set_title('Generalised building height candidates')
    for ax in axes:
        ax.add_patch(Rectangle((core.bounds[0] / 1000, core.bounds[1] / 1000), 2, 2,
                              fill=False, edgecolor='black', linewidth=1.5))
        ax.set_xlim(extent[:2]); ax.set_ylim(extent[2:]); ax.set_aspect('equal')
        ax.set_xlabel('BNG easting (km)'); ax.set_ylabel('BNG northing (km)')
    fig.suptitle('North Heathrow physical inputs | black box: 4 km² study core | no noise predictions\n'
                 'EA composite 2022; inferred local surveys 2020–21; OS generalised footprints 2026-04', fontsize=12)
    fig.savefig(output / 'physical_scene_review.png', dpi=160)
    plt.close(fig)
    result = {'input_generation_id': inventory['generation_id'], 'original_footprints_verified': len(rows),
              'exact_bng_geometry_comparison_tolerance_m': 0, 'counts': counts,
              'csv_review_height_states_verified': True, 'report_counts_verified': True,
              'input_inventory_sha256': file_hash(inputs / 'inventory.json'),
              'verification_code_sha256': file_hash(Path(__file__)),
              'independent_height_or_noise_accuracy_validation': False}
    (output / 'verification.json').write_bytes(json_bytes(result))
    for name, digest in inventory['files'].items():
        if file_hash(inputs / name) != digest:
            raise ValueError('Verification changed the inputs')
    files = {p.name: file_hash(p) for p in output.iterdir() if p.is_file()}
    (output / 'inventory.json').write_bytes(json_bytes({
        'generation_id': 'physical-scene-review-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20],
        'files': files}))
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', default=str(ROOT / 'artifacts/physical_scene_inputs_v2'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/physical_scene_review_v1'))
    args = parser.parse_args()
    run(args.inputs, args.output)
