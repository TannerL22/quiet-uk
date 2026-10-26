"""Audit cached road-model inputs around a fixed north-Heathrow study area.

Offline, bounded and read-only for input releases. No acoustic model is fitted.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pyproj
import rasterio
import shapely
from shapely.geometry import Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.phase2c_road import (assign_traffic_two_pass, load_dft_mrdb_sources_phase2c,
                                 load_os_open_roads_links)
from quiet_uk.road_input_audit import (VEHICLE_FIELDS, clipped_attribution, load_exact_traffic,
                                     point_rows_within, terrain_inventory, window_for_centres)
from quiet_uk.source_pilot import SourcePilot, require_identity_encoding

CORE = (506000, 178000, 508000, 180000)
HALO_M = 5000
HALO = (CORE[0] - HALO_M, CORE[1] - HALO_M, CORE[2] + HALO_M, CORE[3] + HALO_M)


def write_csv(path, rows):
    if not rows:
        return
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(output, pilot_root=ROOT / 'artifacts/oxford_london_map_v1'):
    output, pilot_root = Path(output).resolve(), Path(pilot_root).resolve()
    if output.exists() or output.is_relative_to(pilot_root) or output.is_relative_to(ROOT / 'data'):
        raise ValueError('Use a new output outside the frozen input directories')
    raw = ROOT / 'data/raw/phase2b_road'
    traffic = raw / 'aadf/dft_traffic_counts_aadf.csv'
    roads = raw / 'os_open_roads/Data/oproad_gb.gpkg'
    mrdb = raw / 'mrdb_2021/MRDB_2021.shp'
    terrain = ROOT / 'data/processed/phase2_road_prototype/raw/dtm_10m_from_ea_2m_wcs/heathrow_london.tif'
    manifest_bytes = (pilot_root / 'manifest.json').read_bytes()
    manifest = SourcePilot.read_manifest(pilot_root)
    if manifest['schema_version'] != 3 or manifest['products']['road']['reference_period'] != {
            'start': '2021-01-01', 'end': '2021-12-31'}:
        raise ValueError('Expected the installed calendar-2021 native road release')
    source_paths = [traffic, roads, terrain, raw / 'os_open_roads/Doc/licence.txt',
                    ROOT / 'data/processed/phase2b_road/phase2b_manifest.json',
                    ROOT / 'data/processed/phase2_road_prototype/prototype_manifest.json',
                    pilot_root / 'manifest.json', pilot_root / manifest['products']['road']['metadata_file']]
    source_paths += [mrdb.with_suffix(suffix) for suffix in ('.shp', '.shx', '.dbf', '.prj')]
    target_records = []
    for record in manifest['records']:
        if record['source'] == 'road' and box(*record['core_bounds']).intersection(box(*CORE)).area > 0:
            target_records.append(record)
            path = (pilot_root / record['path']).resolve()
            if (not path.is_relative_to(pilot_root)
                    or manifest['files'].get(record['path']) != record['sha256']):
                raise CatalogueIntegrityError('Unregistered native target')
            source_paths.append(path)
    if not target_records or len(target_records) > 12:
        raise ValueError('Expected at most twelve road target metric cores')
    code_paths = [ROOT / name for name in ('scripts/41_audit_road_model_inputs.py',
                  'src/quiet_uk/road_input_audit.py', 'src/quiet_uk/phase2c_road.py',
                  'src/quiet_uk/phase2b_road.py', 'src/quiet_uk/source_pilot.py',
                  'src/quiet_uk/evidence_semantics.py')]
    identities = {str(path.relative_to(ROOT)).replace('\\', '/'): file_hash(path)
                  for path in source_paths + code_paths}
    print('Input and code identities captured', flush=True)
    for record in target_records:
        if identities[str((pilot_root / record['path']).relative_to(ROOT)).replace('\\', '/')] != record['sha256']:
            raise CatalogueIntegrityError('Native road target checksum mismatch')
    if pyproj.CRS.from_wkt(mrdb.with_suffix('.prj').read_text()) != pyproj.CRS.from_epsg(27700):
        raise ValueError('Traffic geometry CRS mismatch')
    rows, traffic_summary = load_exact_traffic(traffic, 2021)
    local_points = point_rows_within(rows, HALO)
    print(f'Loaded exact 2021 traffic: {len(rows)} usable rows, {len(local_points)} local points', flush=True)
    with sqlite3.connect(f'file:{roads.as_posix()}?mode=ro', uri=True) as connection:
        if connection.execute("SELECT srs_id FROM gpkg_contents WHERE table_name='road_link'").fetchone() != (27700,):
            raise ValueError('Road geometry CRS mismatch')
        columns = [item[1] for item in connection.execute('PRAGMA table_info(road_link)')]
        attributes = {row[0]: {'fictitious': bool(row[1]), 'road_structure': row[2] or '',
                                'form_of_way': row[3] or ''}
                      for row in connection.execute(
                          'SELECT r.fid,r.fictitious,r.road_structure,r.form_of_way FROM road_link r '
                          'JOIN rtree_road_link_geometry idx ON r.fid=idx.id '
                          'WHERE idx.maxx>=? AND idx.minx<=? AND idx.maxy>=? AND idx.miny<=?',
                          (HALO[0], HALO[2], HALO[1], HALO[3]))}
    links = [link for link in load_os_open_roads_links(roads, HALO)
             if link['geometry'].intersection(box(*HALO)).length > 0]
    if len(links) > 30000 or len({link['link_id'] for link in links}) != len(links):
        raise ValueError('Unexpected road inventory size or duplicate identities')
    for link in links:
        link.update(attributes[link['fid']])
    fictitious = [link for link in links if link['fictitious']]
    physical_links = [link for link in links if not link['fictitious']]
    dft = load_dft_mrdb_sources_phase2c(rows, mrdb, HALO, margin_m=500)
    assigned, assignment_counts = assign_traffic_two_pass(physical_links, dft, 'urban')
    print(f'Inspected {len(links)} road links; assigned {len(assigned)} physical links for diagnostics', flush=True)
    core_attribution, halo_attribution = clipped_attribution(assigned, CORE), clipped_attribution(assigned, HALO)
    matched = {source['matched_dft_id'] for source in assigned if source['traffic_assignment_source'] == 'direct'}
    tree = STRtree([link['geometry'] for link in physical_links])
    points_inventory = []
    for row in local_points:
        point = Point(row['easting'], row['northing'])
        nearest = physical_links[int(tree.nearest(point))]
        points_inventory.append({key: row[key] for key in ('count_point_id', 'year', 'road_name', 'road_type',
                                 'road_category', 'easting', 'northing', 'estimation_method',
                                 'estimation_method_detailed', 'flow', 'hgv_flow')} | {
            'inside_core': int(CORE[0] <= point.x < CORE[2] and CORE[1] < point.y <= CORE[3]),
            'used_by_existing_line_matcher': int(row['count_point_id'] in matched),
            'nearest_os_link_id': nearest['link_id'], 'nearest_os_class': nearest['road_class'],
            'nearest_os_distance_m': float(point.distance(nearest['geometry'])),
            **row['vehicle_categories']})
    link_inventory = []
    for source in assigned:
        link_inventory.append({key: source[key] for key in ('link_id', 'road_number', 'road_class', 'form_of_way',
                              'road_structure', 'traffic_assignment_source', 'traffic_source', 'matched_dft_id',
                              'match_distance_m', 'match_score', 'imputation_method', 'speed_source')} | {
            'core_length_m': float(source['geometry'].intersection(box(*CORE)).length),
            'halo_length_m': float(source['geometry'].intersection(box(*HALO)).length)})
    targets = []
    for record in target_records:
        owned = box(*record['core_bounds']).intersection(box(*CORE)).bounds
        with rasterio.open(pilot_root / record['path']) as raster:
            require_identity_encoding(raster)
            if raster.crs != rasterio.crs.CRS.from_epsg(27700) or raster.res != (10, 10):
                raise ValueError('Unexpected native road target grid')
            data = raster.read(1, window=window_for_centres(raster, owned), masked=True)
            mask = np.ma.getmaskarray(data)
            values = data.data
            if (~mask & (~np.isfinite(values) | (values < 0) | (values > 160))).any():
                raise ValueError('Unexpected road target encoding')
            targets.append({'record_id': record['id'], 'metric': record['metric'], 'cells': int(data.size),
                            'reported_cells': int((~mask & (values > 0)).sum()),
                            'zero_code_cells': int((~mask & (values == 0)).sum()),
                            'nodata_cells': int(mask.sum())})
    for metric in ('Lden', 'Lday', 'Lnight'):
        if sum(item['cells'] for item in targets if item['metric'] == metric) != 40000:
            raise ValueError('Road target support does not cover exactly the owned 2 km study core')
    with rasterio.open(terrain) as raster:
        if raster.crs != rasterio.crs.CRS.from_epsg(27700) or raster.count != 1 or raster.res != (10, 10):
            raise ValueError('Expected cached native 10 m terrain output')
        dtm = {'core': terrain_inventory(raster, CORE), 'source_halo': terrain_inventory(raster, HALO),
               'bounds': list(raster.bounds), 'resolution_m': list(raster.res), 'crs': str(raster.crs)}
    before_period = json.loads((ROOT / 'data/processed/phase2b_road/phase2b_manifest.json').read_bytes())
    candidate_names = [str(path.relative_to(ROOT)).replace('\\', '/')
                       for directory in (ROOT / 'data/raw', ROOT / 'data/processed')
                       for path in directory.rglob('*') if path.is_file() and any(
                           token in path.name.lower() for token in ('building', 'barrier', 'openmap', 'dsm',
                                                                  'landcover', 'webtris', 'surface', 'speed'))]
    report = {
        'schema_version': 1, 'status': 'input_audit_only_no_noise_predictions',
        'study': {'name': 'North Heathrow / West Drayton', 'crs': 'EPSG:27700',
                  'core_bounds': list(CORE), 'core_area_km2': 4, 'source_halo_m': HALO_M,
                  'source_halo_bounds': list(HALO), 'source_halo_area_km2': 144,
                  'selection': 'Fixed before inspecting input coverage; reuses an existing terrain extract and installed road targets.',
                  'halo_note': 'A 5 km source radius is an audit configuration inherited from the earlier prototype, not a validated acoustic truncation limit.'},
        'source_and_code_sha256': identities,
        'native_release_id': manifest['release_id'], 'native_manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
        'runtime': {'python': sys.version, 'numpy': np.__version__, 'rasterio': rasterio.__version__,
                    'gdal': rasterio.__gdal_version__, 'shapely': shapely.__version__, 'pyproj': pyproj.__version__},
        'traffic': traffic_summary | {
            'local_points': len(local_points), 'local_points_by_type': dict(Counter(row['road_type'] for row in local_points)),
            'local_points_by_quality': dict(Counter(row['estimation_method'] for row in local_points)),
            'local_points_used_by_line_matcher': sum(row['used_by_existing_line_matcher'] for row in points_inventory),
            'vehicle_category_missing_local': {name: sum(row[name] is None for row in points_inventory) for name in VEHICLE_FIELDS},
            'time_support': 'Annual average daily flow, both directions; no hour/day/evening/night columns in cached AADF.',
            'minor_point_note': 'Unused count points are acquisition/linkage leads, not assigned road flows. Nearest geometry alone does not certify a match.'},
        'road_geometry': {'columns': columns, 'rtree_candidates': len(attributes),
                          'valid_intersecting_links': len(links), 'excluded_fictitious_links': len(fictitious),
                          'provider_release_declared_in_prior_manifest': before_period['source_provenance']['os_open_roads']['release'],
                          'geometry_reference_2021': 'not_established',
                          'structure_counts': dict(Counter(link['road_structure'] for link in physical_links)),
                          'form_of_way_counts': dict(Counter(link['form_of_way'] for link in physical_links))},
        'existing_matcher_diagnostics': {'halo_assignment_counts': assignment_counts,
                                       'core': core_attribution, 'source_halo': halo_attribution,
                                       'independent_validation': False,
                                       'speed_observations': 0, 'source': 'Class-reference speeds for every assigned link.'},
        'terrain': dtm | {'source': 'EA 2 m composite requested as a 10 m WCS output in the prior prototype.',
                         'local_survey_dates': 'not_established',
                         'building_or_barrier_height_source': False,
                         'original_wcs_request_response_hash_linkage': 'not_present_in_prior_prototype_manifest'},
        'road_targets': targets,
        'missing_input_inventory': {'name_scan_scope': ['data/raw', 'data/processed'], 'candidate_files': candidate_names,
            'qualified_building_footprints': False, 'qualified_building_heights': False,
            'qualified_barriers': False, 'qualified_road_surfaces': False,
            'qualified_ground_absorption': False, 'qualified_meteorology': False,
            'qualified_day_evening_night_flows': False, 'qualified_observed_speeds': False,
            'note': 'File-name candidates are diagnostic hints; no source is qualified merely by a name or an ablation result.'},
        'decision': {'ready_for_absolute_gap_predictions': False,
                     'ready_for_bounded_input_and_linkage_experiments': True,
                     'priority': ['Audit 2021 minor-point attribution and carriageway/direction handling.',
                                  'Obtain source-year temporal traffic profiles and speed evidence.',
                                  'Acquire terrain and matched DSM/building inputs for the full source halo with survey metadata.',
                                  'Qualify road surface, ground/barrier and propagation conventions.',
                                  'Use reported-only spatial holdouts for feasibility; require qualified quiet-end truth before gap accuracy claims.']},
        'limitations': [
            'Traffic assignment summaries describe the existing matcher, not independently verified traffic on every road.',
            'Counted AADF is an official annual estimate informed by counts, not continuous all-year observation.',
            'Current road geometry is not established as the 2021 network.',
            'The cached DTM removes surface objects and cannot supply building shielding.',
            'Road target zeros and TIFF nodata remain unknown, not labels for below-cutoff regression.',
            'No new road model is fitted, no gap is predicted and no analytical release is modified.'
        ]}
    for name, digest in identities.items():
        if file_hash(ROOT / name) != digest:
            raise CatalogueIntegrityError('Audit input or code changed during evaluation: ' + name)
    if (pilot_root / 'manifest.json').read_bytes() != manifest_bytes:
        raise CatalogueIntegrityError('Native release manifest changed during evaluation')
    output.mkdir(parents=True)
    (output / 'report.json').write_bytes(json_bytes(report))
    write_csv(output / 'road_link_evidence.csv', link_inventory)
    write_csv(output / 'local_2021_count_points.csv', points_inventory)
    write_csv(output / 'traffic_by_class_and_quality.csv',
              [dict(area=area, **row) for area, summary in (('core', core_attribution), ('halo', halo_attribution))
               for row in summary['groups']])
    print(json.dumps({'report': str(output / 'report.json'), 'core': core_attribution,
                      'terrain': dtm, 'traffic_points': len(local_points)}, indent=2))
    return report, assigned, points_inventory


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/road_input_audit_v1')
    args = parser.parse_args()
    run(args.output)
