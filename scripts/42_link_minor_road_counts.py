"""Build a bounded provisional minor-road traffic input generation, offline.

Reuses the frozen road-input audit. Exports point/link associations and traffic
categories, never acoustic predictions or a replacement native noise release.
"""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import sqlite3
import sys

import numpy as np
import pyproj
import shapely
from shapely.geometry import box, mapping
from shapely.ops import transform as transform_geometry

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.minor_road_linkage import link_minor_counts
from quiet_uk.phase2c_road import load_os_open_roads_links
from quiet_uk.road_input_audit import load_exact_traffic, point_rows_within


def coverage(baseline, links, accepted_ids, bounds):
    area = box(*bounds)
    total, old_direct, new_point = 0.0, 0.0, 0.0
    added_links = 0
    for link in links:
        identity = link['link_id']
        length = float(link['geometry'].intersection(area).length)
        total += length
        direct = baseline[identity]['traffic_assignment_source'] == 'direct'
        if direct:
            old_direct += length
        if identity in accepted_ids:
            if direct:
                raise ValueError('A provisional count cannot replace existing direct evidence')
            new_point += length
            added_links += int(length > 0)
    return {'road_length_m': total, 'baseline_direct_length_m': old_direct,
            'baseline_direct_length_percent': 100 * old_direct / total,
            'provisional_point_links': added_links, 'provisional_point_length_m': new_point,
            'provisional_point_length_percent': 100 * new_point / total,
            'baseline_plus_provisional_length_percent': 100 * (old_direct + new_point) / total,
            'interpretation': 'Availability under a constant-flow link assumption; provisional additions are not validated historic link flows.'}


def run(output, audit_root=ROOT / 'artifacts/road_input_audit_v1'):
    output, audit_root = Path(output).resolve(), Path(audit_root).resolve()
    if output.exists() or output.is_relative_to(ROOT / 'data') or output.is_relative_to(audit_root):
        raise ValueError('Use a new output outside frozen input directories')
    inventory = json.loads((audit_root / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        path = (audit_root / name).resolve()
        if not path.is_relative_to(audit_root) or file_hash(path) != digest:
            raise CatalogueIntegrityError('Road input audit identity mismatch')
    audit = json.loads((audit_root / 'report.json').read_bytes())
    halo, core = tuple(audit['study']['source_halo_bounds']), tuple(audit['study']['core_bounds'])
    roads_name = 'data/raw/phase2b_road/os_open_roads/Data/oproad_gb.gpkg'
    traffic_name = 'data/raw/phase2b_road/aadf/dft_traffic_counts_aadf.csv'
    source_names = (roads_name, traffic_name, 'data/raw/phase2b_road/os_open_roads/Doc/licence.txt',
                    'data/processed/phase2b_road/phase2b_manifest.json')
    source_hashes = {name: file_hash(ROOT / name) for name in source_names}
    if any(source_hashes[name] != audit['source_and_code_sha256'][name] for name in source_names):
        raise CatalogueIntegrityError('Road/traffic inputs changed since the audited snapshot')
    code_names = ('scripts/42_link_minor_road_counts.py', 'src/quiet_uk/minor_road_linkage.py',
                  'src/quiet_uk/phase2c_road.py', 'src/quiet_uk/phase2b_road.py',
                  'src/quiet_uk/road_input_audit.py', 'src/quiet_uk/explorer.py')
    code_hashes = {name: file_hash(ROOT / name) for name in code_names}
    prior_provenance = json.loads((ROOT / 'data/processed/phase2b_road/phase2b_manifest.json').read_bytes())['source_provenance']
    source_licences = {
        'traffic': {'licence': prior_provenance['dft_aadf']['licence'],
                    'licence_url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                    'source_url': prior_provenance['dft_aadf']['downloads_page'],
                    'attribution': 'Source: Department for Transport road traffic statistics, 2021 annual average daily flow.'},
        'geometry': {'licence': prior_provenance['os_open_roads']['licence'],
                     'licence_url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                     'source_url': prior_provenance['os_open_roads']['page'],
                     'attribution': 'Contains OS data © Crown Copyright and database rights 2026.',
                     'original_licence_file': 'data/raw/phase2b_road/os_open_roads/Doc/licence.txt'}
    }
    with (audit_root / 'road_link_evidence.csv').open(encoding='utf-8', newline='') as stream:
        baseline_rows = list(csv.DictReader(stream))
    baseline = {row['link_id']: row for row in baseline_rows}
    if len(baseline) != len(baseline_rows):
        raise ValueError('Duplicate baseline road identity')
    roads = ROOT / roads_name
    with sqlite3.connect(f'file:{roads.as_posix()}?mode=ro', uri=True) as connection:
        if connection.execute("SELECT srs_id FROM gpkg_contents WHERE table_name='road_link'").fetchone() != (27700,):
            raise ValueError('Road geometry CRS mismatch')
        attributes = {row[0]: {'fictitious': bool(row[1]), 'form_of_way': row[2] or '',
                                'road_structure': row[3] or '', 'start_node': row[4], 'end_node': row[5]}
                      for row in connection.execute(
                          'SELECT r.fid,r.fictitious,r.form_of_way,r.road_structure,r.start_node,r.end_node '
                          'FROM road_link r JOIN rtree_road_link_geometry idx ON r.fid=idx.id '
                          'WHERE idx.maxx>=? AND idx.minx<=? AND idx.maxy>=? AND idx.miny<=?',
                          (halo[0], halo[2], halo[1], halo[3]))}
    links = [link for link in load_os_open_roads_links(roads, halo)
             if link['geometry'].intersection(box(*halo)).length > 0]
    for link in links:
        link.update(attributes[link['fid']])
    physical = [link for link in links if not link['fictitious']]
    if len(physical) > 30000 or {link['link_id'] for link in physical} != set(baseline):
        raise ValueError('Physical road network differs from the audited baseline')
    rows, traffic_inventory = load_exact_traffic(ROOT / traffic_name, 2021)
    by_point = {row['count_point_id']: row for row in point_rows_within(rows, halo)}
    with (audit_root / 'local_2021_count_points.csv').open(encoding='utf-8', newline='') as stream:
        audited_points = list(csv.DictReader(stream))
    points = [by_point[row['count_point_id']] for row in audited_points
              if row['road_type'] == 'Minor' and row['used_by_existing_line_matcher'] == '0']
    if not 0 < len(points) <= 100:
        raise ValueError('Expected a bounded minor-point trial')
    occupied = {identity for identity, row in baseline.items() if row['traffic_assignment_source'] == 'direct'}
    result = link_minor_counts(points, physical, occupied_link_ids=occupied, reference_year=2021,
                               network_bounds=halo)
    by_link = {link['link_id']: link for link in physical}
    accepted = [decision for decision in result['decisions'] if decision['status'] == 'accepted_provisional']
    accepted_ids = {decision['accepted_link_id'] for decision in accepted}
    generations = []
    features = []
    for decision in accepted:
        link = by_link[decision['accepted_link_id']]
        old = baseline[link['link_id']]
        point = decision['traffic']
        properties = {
            'link_id': link['link_id'], 'count_point_id': point['count_point_id'],
            'road_name': link.get('road_name'), 'road_number': link.get('road_number'), 'road_class': link['road_class'],
            'point_road_name': point['road_name'], 'point_road_category': point['road_category'],
            'count_point_bng': {'easting': point['easting'], 'northing': point['northing'], 'crs': 'EPSG:27700'},
            'status': 'provisional_minor_point_association', 'traffic_reference_period': {'start': '2021-01-01', 'end': '2021-12-31'},
            'geometry_release': audit['road_geometry']['provider_release_declared_in_prior_manifest'],
            'historic_geometry_compatibility': 'not_established',
            'units': 'vehicles/day, annual daily average, both directions',
            'flow': point['flow'], 'hgv_flow': point['hgv_flow'], 'vehicle_categories': point['vehicle_categories'],
            'estimation_method': point['estimation_method'], 'estimation_method_detailed': point['estimation_method_detailed'],
            'distance_m': decision['distance_m'], 'endpoint_clearance_m': decision['endpoint_clearance_m'],
            'competing_road_margin_m': decision['competing_road_margin_m'],
            'network_margin_m': decision['network_margin_m'],
            'associated_link_length_m': float(link['geometry'].length),
            'source_support': 'count point; association to this link only; constant link flow requires a modelling assumption',
            'baseline_assignment': old['traffic_assignment_source'], 'baseline_imputation_method': old['imputation_method'],
            'form_of_way': link['form_of_way'], 'road_structure': link['road_structure'],
            'start_node': link['start_node'], 'end_node': link['end_node'],
            'day_evening_night_flows': None, 'observed_speed_kmh': None,
            'traffic_source_sha256': source_hashes[traffic_name], 'geometry_source_sha256': source_hashes[roads_name],
        }
        generations.append(properties)
        geometry = transform_geometry(pyproj.Transformer.from_crs(27700, 4326, always_xy=True).transform, link['geometry'])
        features.append({'type': 'Feature', 'geometry': mapping(geometry), 'properties': properties})
    old_core, old_halo = audit['existing_matcher_diagnostics']['core'], audit['existing_matcher_diagnostics']['source_halo']
    coverage_core = coverage(baseline, physical, accepted_ids, core)
    coverage_halo = coverage(baseline, physical, accepted_ids, halo)
    for calculated, previous in ((coverage_core, old_core), (coverage_halo, old_halo)):
        if abs(calculated['baseline_direct_length_percent'] - previous['direct_length_percent']) > 1e-8:
            raise ValueError('Baseline length attribution changed')
    report = {
        'schema_version': 1, 'status': 'provisional_input_generation_no_noise_predictions',
        'parent_audit_id': inventory['audit_id'], 'study': audit['study'], **result,
        'source_sha256': source_hashes, 'code_sha256': code_hashes,
        'source_licences': source_licences,
        'traffic_inventory': traffic_inventory,
        'runtime': {'python': sys.version, 'numpy': np.__version__, 'shapely': shapely.__version__, 'pyproj': pyproj.__version__},
        'coverage': {'core': coverage_core, 'source_halo': coverage_halo},
        'accepted_quality_flags': dict(Counter(row['estimation_method'] for row in generations)),
        'limitations': [
            'Accepted associations use fixed geometry rules, not independent historic road/traffic validation.',
            'The 2026 geometry is not established as the 2021 network.',
            'A count supports its point; constant flow on the associated link is an explicit future assumption.',
            'Provisional traffic is restricted to one single-carriageway link, without propagation to neighbours or class medians.',
            'The existing direct road-flow assignments are protected; no extra directional AADF is allocated to dual/roundabout/slip/tunnel links.',
            'Counted and Estimated AADF flags retain their original meaning; missing vehicle categories stay null.',
            'Annual daily flow does not establish day/evening/night flow or observed speed.',
            'No acoustic calculation, noise-grid replacement, quiet-end prediction or production UI integration occurs.'
        ]}
    for name, digest in {**source_hashes, **code_hashes}.items():
        if file_hash(ROOT / name) != digest:
            raise CatalogueIntegrityError('Source or linkage code changed during evaluation')
    for name, digest in inventory['files'].items():
        if file_hash(audit_root / name) != digest:
            raise CatalogueIntegrityError('Parent audit changed during evaluation')
    output.mkdir(parents=True)
    (output / 'report.json').write_bytes(json_bytes(report))
    (output / 'provisional_traffic_inputs.json').write_bytes(json_bytes({
        'schema_version': 1, 'status': 'provisional_minor_point_associations',
        'source_licences': source_licences, 'associations': generations}))
    (output / 'associations.geojson').write_bytes(json_bytes({
        'type': 'FeatureCollection', 'features': features, 'source_licences': source_licences,
        'analytical_geometry': False,
        'geometry_note': 'WGS84 review geometry. Analytical modelling must read the original EPSG:27700 geometry identified by its source hash.'}))
    files = {path.name: file_hash(path) for path in sorted(output.iterdir()) if path.is_file()}
    identity = 'minor-links-' + files['report.json'][:20]
    (output / 'inventory.json').write_bytes(json_bytes({'generation_id': identity, 'files': files, 'noise_predictions_created': False}))
    print(json.dumps({'generation_id': identity, 'summary': result['summary'], 'coverage': report['coverage'],
                      'output': str(output)}, indent=2))
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/minor_road_linkage_v3')
    args = parser.parse_args()
    run(args.output)
