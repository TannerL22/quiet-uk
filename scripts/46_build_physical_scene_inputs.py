"""Build an offline native physical scene; no propagation/noise estimates.

Keeps exact 2 m elevations, provider survey codes, generalised OS footprints,
provisional surface-height summaries and explicit withheld records.
"""
import argparse
from collections import Counter
from contextlib import ExitStack
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
import zipfile

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import ListedColormap
from matplotlib.patches import Rectangle
import numpy as np
from pyproj import CRS, Transformer
import rasterio
from rasterio.features import geometry_window
from rasterio.transform import from_origin
from rasterio.windows import Window, from_bounds, transform as window_transform
import shapefile
from shapely.geometry import box, mapping, shape
from shapely.ops import transform as geometry_transform, unary_union

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.physical_scene import (
    AMBIGUOUS_SURVEY, HEIGHT_RULES, assert_native_grid, building_surface_summary,
    paired_survey_mask, qualify_surveys, surface_difference, survey_grid, valid_elevations,
)

HALO = (501000, 173000, 513000, 185000)
CORE = (506000, 178000, 508000, 180000)
RASTERS = ('terrain_odn_2m.tif', 'surface_odn_2m.tif', 'surface_above_terrain_2m.tif',
           'dtm_survey_code_2m.tif', 'dsm_survey_code_2m.tif')


def verify_inventory(directory):
    inventory = json.loads((directory / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        if file_hash(directory / name) != digest:
            raise ValueError(f'Source checksum mismatch: {name}')
    return inventory


def mosaic(source, output, geometries, survey_records):
    profile = {'driver': 'GTiff', 'crs': 'EPSG:27700', 'count': 1,
               'width': 6000, 'height': 6000, 'transform': from_origin(HALO[0], HALO[3], 2, 2),
               'tiled': True, 'blockxsize': 512, 'blockysize': 512, 'compress': 'deflate'}
    with rasterio.open(source / 'dtm/dtm_501000_173000_2m.tif') as first:
        nodata = first.nodata
    profiles = [dict(profile, dtype='float32', nodata=nodata, predictor=3),
                dict(profile, dtype='float32', nodata=nodata, predictor=3),
                dict(profile, dtype='float32', nodata=np.nan, predictor=3),
                dict(profile, dtype='uint16', nodata=0, predictor=2),
                dict(profile, dtype='uint16', nodata=0, predictor=2)]
    stats = {region: {'total_cells': 0, 'dtm_valid_cells': 0, 'dsm_valid_cells': 0,
                      'paired_elevation_cells': 0, 'matched_survey_elevation_cells': 0,
                      'negative_surface_difference_cells': 0, 'survey_code_counts': Counter()}
             for region in ('core', 'halo')}
    verified = 0
    with ExitStack() as stack:
        writers = [stack.enter_context(rasterio.open(output / name, 'w', **p))
                   for name, p in zip(RASTERS, profiles)]
        for writer, name in zip(writers, RASTERS):
            writer.update_tags(description=name, source_generation=source.name,
                               resampling='none; exact native pixel windows',
                               attribution='Environment Agency 2022; Open Government Licence v3.0',
                               vertical_units='metres above Ordnance Datum Newlyn' if name.startswith(('terrain_', 'surface_odn'))
                               else 'metres above local terrain' if name.startswith('surface_above') else 'survey identity codes')
        for south in range(HALO[1], HALO[3], 2000):
            for west in range(HALO[0], HALO[2], 2000):
                bounds = (west, south, west + 2000, south + 2000)
                arrays, masks = [], []
                with ExitStack() as tile_stack:
                    readers = [tile_stack.enter_context(rasterio.open(source / kind / f'{kind}_{west}_{south}_2m.tif'))
                               for kind in ('dtm', 'dsm')]
                    for reader in readers:
                        assert_native_grid(reader, bounds)
                        if reader.nodata != nodata or reader.dtypes != ('float32',):
                            raise ValueError('Inconsistent native elevation definition')
                        arrays.append(reader.read(1))
                        masks.append(reader.read_masks(1))
                    transform = readers[0].transform
                codes = [survey_grid([(g, c) for g, c in layer if g.intersects(box(*bounds))],
                                     arrays[0].shape, transform, survey_records) for layer in geometries]
                difference, paired_elevations = surface_difference(*arrays, *masks)
                matched = paired_elevations & paired_survey_mask(*codes)
                destination = Window((west - HALO[0]) // 2, (HALO[3] - south - 2000) // 2, 1000, 1000)
                for writer, array in zip(writers, [*arrays, difference, *codes]):
                    writer.write(array, 1, window=destination)
                for region, extent in (('halo', HALO), ('core', CORE)):
                    intersection = box(*bounds).intersection(box(*extent))
                    if intersection.area == 0:
                        continue
                    window = from_bounds(*intersection.bounds, transform).round_offsets().round_lengths()
                    rows, cols = window.toslices()
                    selected = (rows, cols)
                    st = stats[region]
                    st['total_cells'] += int(window.width * window.height)
                    st['dtm_valid_cells'] += int(np.count_nonzero(valid_elevations(arrays[0], masks[0])[selected]))
                    st['dsm_valid_cells'] += int(np.count_nonzero(valid_elevations(arrays[1], masks[1])[selected]))
                    st['paired_elevation_cells'] += int(np.count_nonzero(paired_elevations[selected]))
                    st['matched_survey_elevation_cells'] += int(np.count_nonzero(matched[selected]))
                    st['negative_surface_difference_cells'] += int(np.count_nonzero((difference < 0)[selected]))
                    unique, counts = np.unique(codes[0][selected][matched[selected]], return_counts=True)
                    st['survey_code_counts'].update({str(int(c)): int(n) for c, n in zip(unique, counts)})
                verified += 2 * arrays[0].size
        for st in stats.values():
            for key in ('dtm_valid_cells', 'dsm_valid_cells', 'paired_elevation_cells', 'matched_survey_elevation_cells'):
                st[key.replace('_cells', '_fraction')] = st[key] / st['total_cells']
                st[key.replace('_cells', '_km2')] = st[key] * 4 / 1e6
    # Compare every reconstructed native tile to its immutable response, including masks.
    with ExitStack() as stack:
        readers = [stack.enter_context(rasterio.open(output / name)) for name in RASTERS[:2]]
        for reader in readers:
            assert_native_grid(reader, HALO)
        for south in range(HALO[1], HALO[3], 2000):
            for west in range(HALO[0], HALO[2], 2000):
                window = Window((west - HALO[0]) // 2, (HALO[3] - south - 2000) // 2, 1000, 1000)
                for kind, reader in zip(('dtm', 'dsm'), readers):
                    with rasterio.open(source / kind / f'{kind}_{west}_{south}_2m.tif') as original:
                        if not (np.array_equal(reader.read(1, window=window), original.read(1))
                                and np.array_equal(reader.read_masks(1, window=window), original.read_masks(1))):
                            raise ValueError('Mosaic changed native observations or nodata')
    return stats, verified


def buildings(source, output, survey_records):
    halo, core = box(*HALO), box(*CORE)
    survey_lookup = {row['code']: row for row in survey_records}
    transformer = Transformer.from_crs(27700, 4326, always_xy=True)
    counts = {'core': Counter(), 'halo': Counter()}
    reasons = {'core': Counter(), 'halo': Counter()}
    footprint_areas = {'core': 0., 'halo': 0.}
    height_values = {'core': [], 'halo': []}
    seen, records, review, provenance = set(), [], [], {}
    archive = source / 'buildings/opmplc_essh_tq.zip'
    base = 'OS OpenMap Local (ESRI Shape File) TQ/data/TQ_Building'
    started = time.monotonic()
    with zipfile.ZipFile(archive) as zipped, ExitStack() as stack:
        components = [zipped.getinfo(base + suffix) for suffix in ('.shp', '.shx', '.dbf', '.prj')]
        if sum(info.file_size for info in components) > 600 * 1024 * 1024:
            raise ValueError('Building decompression budget exceeded')
        prj = zipped.read(base + '.prj')
        if not CRS.from_wkt(prj.decode()).equals(CRS.from_epsg(27700)):
            raise ValueError('Footprint coordinate system mismatch')
        (output / 'building-source.prj').write_bytes(prj)
        licence_name = 'OS OpenMap Local (ESRI Shape File) TQ/doc/licence.txt'
        (output / 'OS-LICENCE.txt').write_bytes(zipped.read(licence_name))
        readers = [stack.enter_context(rasterio.open(output / name)) for name in
                   (RASTERS[0], RASTERS[1], RASTERS[3], RASTERS[4])]
        # The DBF reader seeks back to zero for each selected record. ZIP streams
        # would repeatedly decompress ~79 MB; bounded exact component extraction
        # makes those seeks constant-cost, without parsing unrelated attributes.
        native = output / 'original_building_components'
        native.mkdir()
        for info in components:
            destination = native / Path(info.filename).name
            with zipped.open(info) as stream, destination.open('wb') as target:
                shutil.copyfileobj(stream, target, length=1024 * 1024)
            if destination.stat().st_size != info.file_size:
                raise ValueError('Building component decompression size mismatch')
        reader = shapefile.Reader(str(native / 'TQ_Building.shp'))
        provenance = {'archive_layer': base, 'regional_record_count': reader.numRecords,
                      'fields': [field.name for field in reader.fields[1:]],
                      'selected_by': 'bounding box then positive-area halo intersection',
                      'footprint_product_version': '2026-04',
                      'footprint_definition': 'generalised roofed entity; may amalgamate buildings and structures',
                      'geometry_crs': 'EPSG:27700', 'vertical_datum': None,
                      'stable_historical_building_identity': False,
                      'original_component_hashes': {p.name: file_hash(p) for p in native.iterdir()}}
        for source_record in reader.iterShapeRecords(bbox=HALO):
            attributes = source_record.record.as_dict()
            geometry = shape(source_record.shape.__geo_interface__)
            if geometry.is_empty or not geometry.intersects(halo):
                continue
            if geometry.is_valid and geometry.intersection(halo).area <= 0:
                continue
            source_id = attributes['ID']
            if source_id in seen:
                raise ValueError('Duplicate selected building identity')
            seen.add(source_id)
            if len(seen) > 100000:
                raise ValueError('Local building record budget exceeded')
            fully_inside = geometry.is_valid and halo.covers(geometry)
            if geometry.is_valid and fully_inside:
                window = geometry_window(readers[0], [mapping(geometry)])
                data = [r.read(1, window=window) for r in readers]
                masks = [r.read_masks(1, window=window) for r in readers[:2]]
                summary = building_surface_summary(geometry, *data[:2], *masks, *data[2:],
                                                    window_transform(window, readers[0].transform))
            else:
                empty = np.zeros((1, 1), dtype='float32')
                summary = building_surface_summary(geometry, empty, empty, empty, empty,
                                                    empty, empty, readers[0].transform,
                                                    fully_inside=False)
            summaries = [survey_lookup[c] for c in summary['survey_codes']]
            properties = {'source_id': source_id, 'source_record_number': source_record.shape.oid,
                          'feature_code': attributes['FEATCODE'], 'footprint_product_version': '2026-04',
                          'geometry_crs': 'EPSG:27700', 'area_m2': geometry.area,
                          'intersects_core': bool(geometry.intersects(core) and
                                                  (not geometry.is_valid or geometry.intersection(core).area > 0)),
                          **summary, 'survey_ids': [r['polygon_id'] for r in summaries],
                          'survey_start': min((r['survey_start'] for r in summaries), default=None),
                          'survey_end': max((r['survey_end'] for r in summaries), default=None),
                          'height_uncertainty_m': None,
                          'survey_date_status': 'inferred from provider newest-survey rule at equal source resolution',
                          'p10_p90_definition': 'observed within-footprint spread, not a confidence interval'}
            records.append({**properties, 'geometry_wkt_bng': geometry.wkt})
            review.append({'type': 'Feature', 'geometry': mapping(geometry_transform(transformer.transform, geometry)),
                           'properties': properties})
            for region, extent in (('halo', halo), ('core', core)):
                if region == 'core' and not properties['intersects_core']:
                    continue
                counts[region]['footprints'] += 1
                counts[region][summary['status']] += 1
                if summary['withheld_reason']:
                    reasons[region][summary['withheld_reason']] += 1
                if geometry.is_valid:
                    footprint_areas[region] += geometry.intersection(extent).area
                if summary['status'] == 'provisional_surface_height':
                    height_values[region].append(summary['provisional_height_agl_m'])
            if len(records) % 2000 == 0:
                print(f'Building footprints processed: {len(records)}', flush=True)
        reader.close()
    if not records:
        raise ValueError('No local footprints extracted')
    with (output / 'building_heights_bng.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        for row in records:
            writer.writerow({key: json.dumps(value) if isinstance(value, list) else value for key, value in row.items()})
    (output / 'building_heights_review_wgs84.geojson').write_bytes(json_bytes({
        'type': 'FeatureCollection', 'features': review,
        'purpose': 'review only; full original BNG geometries are in building_heights_bng.csv',
        'attributions': ['Contains OS data © Crown copyright and database right 2026.',
                         '© Environment Agency copyright and/or database right 2022. All rights reserved.'],
        'height_status': 'provisional surface height; historical geometry compatibility unestablished'}))
    return {'source': provenance, 'counts': counts, 'withheld_reasons': reasons,
            'summed_clipped_footprint_area_m2': footprint_areas,
            'candidate_height_median_m': {r: float(np.median(v)) if v else None for r, v in height_values.items()},
            'candidate_height_range_m': {r: [min(v), max(v)] if v else None for r, v in height_values.items()},
            'elapsed_seconds': round(time.monotonic() - started, 3)}, review


def plot_review(output, review):
    extent = (HALO[0] / 1000, HALO[2] / 1000, HALO[1] / 1000, HALO[3] / 1000)
    with rasterio.open(output / RASTERS[0]) as ds:
        terrain = ds.read(1, out_shape=(600, 600), masked=True)
    with rasterio.open(output / RASTERS[3]) as ds:
        codes = ds.read(1, out_shape=(600, 600), masked=True)
    fig, axes = plt.subplots(1, 3, figsize=(16, 5.5), constrained_layout=True)
    im = axes[0].imshow(terrain, extent=extent, cmap='terrain', vmin=15, vmax=60)
    fig.colorbar(im, ax=axes[0], label='Ground elevation (m above ODN)', shrink=.7)
    axes[0].set_title('Native terrain input — 2 m')
    im = axes[1].imshow(codes, extent=extent, cmap=ListedColormap(['#9467bd', '#bcbd22', '#17becf', '#ff7f0e']),
                        vmin=.5, vmax=4.5)
    color = fig.colorbar(im, ax=axes[1], ticks=[1, 2, 3, 4], shrink=.7)
    color.ax.set_yticklabels(['2018 Mar–Apr', '2018 Jan', '2021 Mar', '2020 Dec–2021 Feb'])
    axes[1].set_title('Provider survey support')
    candidates = [feature for feature in review if feature['properties']['status'] == 'provisional_surface_height']
    withheld = [feature for feature in review if feature['properties']['status'] == 'withheld']
    inverse = Transformer.from_crs(4326, 27700, always_xy=True)
    for features, marker, label in ((withheld, 'x', 'Withheld'), (candidates, '.', 'Provisional')):
        centroids = [geometry_transform(inverse.transform, shape(f['geometry'])).centroid for f in features]
        values = [f['properties']['provisional_height_agl_m'] for f in features]
        if marker == 'x':
            axes[2].scatter([p.x / 1000 for p in centroids], [p.y / 1000 for p in centroids],
                            s=5, c='#999999', marker=marker, linewidths=.4, label=label)
        else:
            im = axes[2].scatter([p.x / 1000 for p in centroids], [p.y / 1000 for p in centroids],
                                 s=4, c=values, cmap='viridis', vmin=2, vmax=25, marker=marker, label=label)
            fig.colorbar(im, ax=axes[2], label='Provisional median surface height (m)', shrink=.7)
    axes[2].set_title('Generalised building height candidates')
    for ax in axes:
        ax.add_patch(Rectangle((CORE[0] / 1000, CORE[1] / 1000), 2, 2, fill=False, edgecolor='black', linewidth=1.5))
        ax.set_xlim(extent[:2]); ax.set_ylim(extent[2:]); ax.set_aspect('equal')
        ax.set_xlabel('BNG easting (km)'); ax.set_ylabel('BNG northing (km)')
    axes[2].legend(loc='upper right', markerscale=3)
    fig.suptitle('North Heathrow physical inputs | black box: study core | no noise predictions\n'
                 'EA composite 2022; local LiDAR 2018/2020–21; OS generalised footprints 2026-04', fontsize=12)
    fig.savefig(output / 'physical_scene_review.png', dpi=160)
    plt.close(fig)


def run(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_relative_to(ROOT / 'artifacts') or not output.is_relative_to(ROOT / 'artifacts') or output.exists():
        raise ValueError('Use completed sources and a fresh workspace artifact generation')
    source_inventory = verify_inventory(source)
    source_manifest = json.loads((source / 'source-manifest.json').read_bytes())
    if tuple(source_manifest['halo_bounds_bng']) != HALO or tuple(source_manifest['core_bounds_bng']) != CORE:
        raise ValueError('Frozen study bounds changed')
    docs = [json.loads((source / 'metadata' / f'{kind}-surveys.geojson').read_bytes()) for kind in ('dtm', 'dsm')]
    records, geometries = qualify_surveys(*docs)
    output.mkdir(parents=True)
    code = output / 'code'
    code.mkdir()
    for relative in ('scripts/45_acquire_physical_scene_inputs.py', 'scripts/46_build_physical_scene_inputs.py',
                     'src/quiet_uk/physical_scene.py'):
        shutil.copyfile(ROOT / relative, code / Path(relative).name)
    research = ROOT / 'artifacts/physical_scene_research_v1'
    if research.exists():
        shutil.copytree(research, output / 'research')
    print('Source hashes and survey identities verified; constructing native mosaics', flush=True)
    with rasterio.Env(GDAL_CACHEMAX=128 * 1024 * 1024):
        coverage, verified = mosaic(source, output, geometries, records)
        print('Native mosaics complete and all source pixels/masks verified', flush=True)
        building_report, review = buildings(source, output, records)
    geometrical = {}
    for region, bounds in (('core', CORE), ('halo', HALO)):
        extent = box(*bounds)
        geometrical[region] = {kind + '_index_union_km2': unary_union([g for g, _ in layer]).intersection(extent).area / 1e6
                              for kind, layer in zip(('dtm', 'dsm'), geometries)}
    report = {'schema_version': 1, 'core_bounds_bng': CORE, 'halo_bounds_bng': HALO,
              'crs': 'EPSG:27700', 'grid_resolution_m': 2, 'resampling': None,
              'source_generation_id': source_inventory['generation_id'],
              'source_inventory_sha256': file_hash(source / 'inventory.json'),
              'vertical_datum': source_manifest['vertical_datum'], 'coverage': coverage,
              'survey_records': records, 'survey_index_geometrical_support': geometrical,
              'survey_date_assignment': {
                  'status': 'inferred_from_provider_selection_rule',
                  'rule': 'winner must have the same source resolution as all competing surveys and start after they ended',
                  'source': 'EA product pages: newest, best-resolution survey data used for the composite',
                  'direct_pixel_source_id_available': False,
                  'raw_overlapping_index_features_retained': True},
              'building_heights': building_report, 'height_rules': HEIGHT_RULES,
              'verification': {'exact_source_elevation_pixels_and_masks': verified,
                               'independent_height_or_noise_validation': False},
              'licenses': source_manifest['licenses'], 'attributions': source_manifest['attributions'],
              'limits': ['DSM includes vegetation, vehicles and structures; surface differences are not inherently building heights.',
                         'OS footprints are generalised and dated 2026-04; historical compatibility with 2018/2020–21 LiDAR is unestablished.',
                         'Height summaries are provisional observations, not exact roof/eaves/acoustic obstacle heights.',
                         'Source-survey vertical accuracy does not establish derived building-height uncertainty.',
                         'Survey codes infer date support from the provider selection rule; they are not directly observed raster pixel source IDs.',
                         'Small/missing/ambiguous footprints remain withheld; no class-average or zero filling.',
                         'No noise predictions, traffic period allocation or local speed assumptions were created.']}
    (output / 'report.json').write_bytes(json_bytes(report))
    plot_review(output, review)
    (output / 'README.md').write_text(
        '# Physical-scene inputs\n\n'
        'See report.json for sources, dates, measured coverage, rules and limitations.\n\n'
        'terrain_odn_2m.tif and surface_odn_2m.tif retain exact native EPSG:27700 pixels and nodata.\n'
        'surface_above_terrain_2m.tif is DSM minus DTM, including vegetation and structures; negatives are retained.\n'
        'Survey code rasters use report.json: 0 = unmapped, 65535 = unresolved overlap; positive codes infer survey support from the documented provider selection rule.\n'
        'building_heights_bng.csv retains original unmodified BNG WKT geometries, source record IDs, observed statistics and explicit withheld reasons.\n'
        'Blank candidate heights mean unavailable, not zero. GeoJSON is WGS84 review output.\n'
        'Provisional surface heights do not establish exact building/acoustic obstacle heights or historical geometry compatibility.\n'
        'The p10–p90 spread is not a confidence interval. height_uncertainty_m and acoustic_obstacle_height_m remain unavailable.\n\n'
        'Both source products are under Open Government Licence v3.0: https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/\n'
        + '\n'.join(report['attributions']) + '\n', encoding='utf-8')
    verify_inventory(source)
    for path in code.iterdir():
        original = ROOT / ('src/quiet_uk' if path.name == 'physical_scene.py' else 'scripts') / path.name
        if file_hash(path) != file_hash(original):
            raise ValueError('Build code changed during generation')
    files = {str(p.relative_to(output)).replace('\\', '/'): file_hash(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    inventory = {'generation_id': 'physical-scene-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20], 'files': files}
    (output / 'inventory.json').write_bytes(json_bytes(inventory))
    print(json.dumps({'generation_id': inventory['generation_id'], 'coverage': coverage,
                      'buildings': building_report['counts'], 'withheld_reasons': building_report['withheld_reasons']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default=str(ROOT / 'artifacts/physical_scene_sources_v1'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/physical_scene_inputs_v1'))
    args = parser.parse_args()
    run(args.source, args.output)
