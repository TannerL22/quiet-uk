"""Offline carriageway budgets from frozen road, annual direction and OSM evidence.

Every physical link receives a decision; only accepted provisional groups receive
primary traffic. No imputation, period allocation, speed or acoustic prediction.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys

import pyproj
import shapely
from shapely.geometry import LineString, box, mapping
from shapely.ops import transform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.phase2c_road import load_os_open_roads_links
from quiet_uk.road_direction import allocate_carriageways, partition_aadf
from quiet_uk.road_input_audit import load_exact_traffic

ROADS = 'data/raw/phase2b_road/os_open_roads/Data/oproad_gb.gpkg'
TRAFFIC = 'data/raw/phase2b_road/aadf/dft_traffic_counts_aadf.csv'


def read_json(path):
    return json.loads(Path(path).read_bytes())


def verify_inventory(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use workspace immutable artifacts')
    inventory = read_json(directory / 'inventory.json')
    for name, digest in inventory['files'].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or file_hash(path) != digest:
            raise ValueError('Input inventory mismatch: ' + str(path))
    return inventory


def physical_links(bounds):
    roads = ROOT / ROADS
    with sqlite3.connect(f'file:{roads.as_posix()}?mode=ro', uri=True) as connection:
        if connection.execute("SELECT srs_id FROM gpkg_contents WHERE table_name='road_link'").fetchone() != (27700,):
            raise ValueError('Expected BNG road geometry')
        attributes = {row[0]: dict(fictitious=bool(row[1]), form_of_way=row[2] or '',
                                  road_structure=row[3] or '', start_node=row[4], end_node=row[5], loop=bool(row[6]))
                      for row in connection.execute(
                          'SELECT r.fid,r.fictitious,r.form_of_way,r.road_structure,r.start_node,r.end_node,r.loop '
                          'FROM road_link r JOIN rtree_road_link_geometry idx ON r.fid=idx.id '
                          'WHERE idx.maxx>=? AND idx.minx<=? AND idx.maxy>=? AND idx.miny<=?',
                          (bounds[0],bounds[2],bounds[1],bounds[3]))}
    area = box(*bounds)
    links = load_os_open_roads_links(roads, bounds)
    for link in links:
        link.update(attributes[link['fid']])
    return [l for l in links if not l['fictitious'] and l['geometry'].intersection(area).length > 0]


def coverage(links, decisions, bounds):
    area, by_id = box(*bounds), {d['link_id']:d for d in decisions}
    counts, lengths = Counter(), defaultdict(float)
    for link in links:
        length = link['geometry'].intersection(area).length
        if length <= 0:
            continue
        status = by_id[link['link_id']]['status']
        counts[status] += 1
        lengths[status] += length
    total = sum(lengths.values())
    accepted = sum(v for k,v in lengths.items() if k.startswith('provisional_'))
    return {'links':sum(counts.values()), 'road_length_m':total, 'status_link_counts':dict(counts),
            'status_length_m':dict(lengths), 'provisional_length_m':accepted,
            'provisional_length_percent':100*accepted/total}


def run(output, sources):
    output, sources = Path(output).resolve(), Path(sources).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace output generation')
    parents = {name:ROOT/'artifacts'/name for name in ('road_input_audit_v1','minor_road_linkage_v3',
               'road_temporal_audit_v2','road_temporal_sources_v1')}
    parents[sources.name] = sources
    inventories = {name:verify_inventory(p) for name,p in parents.items()}
    parent_hashes = {name:file_hash(p/'inventory.json') for name,p in parents.items()}
    source_manifest = read_json(sources/'manifest.json')
    if any(parent_hashes[n] != h for n,h in source_manifest['parent_inventory_sha256'].items()):
        raise ValueError('Directional source parent identity mismatch')
    audit = read_json(parents['road_input_audit_v1']/'report.json')
    core, halo = audit['study']['core_bounds'], audit['study']['source_halo_bounds']
    source_hashes = {name:file_hash(ROOT/name) for name in (ROADS,TRAFFIC)}
    if any(h != audit['source_and_code_sha256'][n] for n,h in source_hashes.items()):
        raise ValueError('Original road/traffic snapshot changed')
    code_names = ['scripts/49_build_road_direction_inputs.py','src/quiet_uk/road_direction.py',
                  'src/quiet_uk/road_input_audit.py','src/quiet_uk/road_temporal.py',
                  'src/quiet_uk/phase2c_road.py','src/quiet_uk/phase2b_road.py','src/quiet_uk/explorer.py']
    code_hashes = {n:file_hash(ROOT/n) for n in code_names}
    with (parents['road_input_audit_v1']/'road_link_evidence.csv').open(encoding='utf-8',newline='') as stream:
        baseline = list(csv.DictReader(stream))
    links = physical_links(halo)
    if not 0 < len(links) <= 30000 or {l['link_id'] for l in links} != {r['link_id'] for r in baseline}:
        raise ValueError('Physical network differs from baseline')
    associations = [dict(link_id=r['link_id'],count_point_id=r['matched_dft_id'],kind='baseline_direct',
                         prior_match_distance_m=r['match_distance_m'],prior_match_score=r['match_score'])
                    for r in baseline if r['traffic_assignment_source']=='direct']
    minor = read_json(parents['minor_road_linkage_v3']/'provisional_traffic_inputs.json')
    associations += [dict(link_id=r['link_id'],count_point_id=r['count_point_id'],kind='provisional_minor',
                          prior_generation_id=inventories['minor_road_linkage_v3']['generation_id'])
                     for r in minor['associations']]
    temporal = read_json(parents['road_temporal_audit_v2']/'local_survey_day_inputs.json')
    selected = {str(p['count_point_id']) for p in temporal['points']} | set(source_manifest['additional_donor_ids'])
    rows, traffic_inventory = load_exact_traffic(ROOT/TRAFFIC,2021)
    points = {p['count_point_id']:p for p in rows if p['count_point_id'] in selected}
    if set(points) != selected or not {a['count_point_id'] for a in associations}.issubset(points):
        raise ValueError('Missing original annual donor')
    direction_rows = []
    for name in ('average-annual-daily-flow-by-direction-page-01.json','average-annual-daily-flow-by-direction-page-02.json'):
        direction_rows += read_json(parents['road_temporal_sources_v1']/name)['data']
    for path in sorted(sources.glob('additional-aadf-directions-*.json')):
        direction_rows += read_json(path)['data']
    if len(direction_rows) > 500 or any(str(r['count_point_id']) not in selected for r in direction_rows):
        raise ValueError('Unbounded or non-selected direction source')
    by_point = defaultdict(list)
    for row in direction_rows:
        by_point[str(row['count_point_id'])].append(row)
    partitions = {identity:partition_aadf(point,by_point[identity]) for identity,point in sorted(points.items())}

    ways, osm_records, osm_withheld = [], [], []
    project = pyproj.Transformer.from_crs(4326,27700,always_xy=True)
    if source_manifest['osm_response_file']:
        raw = read_json(sources/source_manifest['osm_response_file'])
        if raw.get('remark') or len(raw['elements']) > 10000:
            raise ValueError('OSM incomplete or unbounded response')
        seen = set()
        for element in raw['elements']:
            identity = element['id']
            if element['type'] != 'way' or identity in seen or element['timestamp'] > source_manifest['osm_requested_snapshot_utc']:
                raise ValueError('OSM identity/date/type mismatch')
            seen.add(identity)
            coords = [(p['lon'],p['lat']) for p in element['geometry']]
            geometry = transform(project.transform,LineString(coords)) if len(coords)>=2 else None
            if geometry is None or geometry.is_empty or not geometry.is_valid or geometry.length <= 0:
                osm_withheld.append(identity)
                continue
            way = dict(way_id=identity,geometry=geometry,tags=element.get('tags',{}))
            ways.append(way)
            osm_records.append(dict(way_id=identity,version=element['version'],last_edit_utc=element['timestamp'],
                                    tags=way['tags'],geometry_wkt_bng=geometry.wkt))
    result = allocate_carriageways(links,associations,partitions,ways)
    by_link = {l['link_id']:l for l in links}
    by_decision = {d['link_id']:d for d in result['decisions']}
    counts_by_form = defaultdict(Counter)
    for a in associations:
        counts_by_form[by_link[a['link_id']]['form_of_way']][by_decision[a['link_id']]['status']] += 1
    licences = {
        'DfT':{'licence':'Open Government Licence v3.0','attribution':'Source: Department for Transport road traffic statistics, 2021.',
               'url':'https://roadtraffic.dft.gov.uk/about'},
        'OS':{'licence':'Open Government Licence v3.0','attribution':'Contains OS data \u00a9 Crown copyright and database right 2026.',
              'url':'https://www.ordnancesurvey.co.uk/products/os-open-roads'},
        'OSM':{'licence':'Open Database Licence 1.0','attribution':'\u00a9 OpenStreetMap contributors',
               'url':'https://www.openstreetmap.org/copyright',
               'redistribution':'OSM-assisted derived database must follow applicable ODbL attribution/share-alike requirements; originals retain their respective licences.'}}
    report = dict(schema_version=1,status='provisional_road_representation_inputs_no_noise_predictions',
                  study=audit['study'],reference_year=2021,geometry_release='2026-04',
                  osm_snapshot_utc=source_manifest['osm_requested_snapshot_utc'],
                  parent_inventory_sha256=parent_hashes,source_sha256=source_hashes,code_sha256=code_hashes,
                  source_licences=licences,policy=result['policy'],traffic_inventory=traffic_inventory,
                  summary=dict(physical_links=len(links),retained_associations=len(associations),
                               retained_donors=len({a['count_point_id'] for a in associations}),
                               selected_annual_points=len(points),official_directional_records=len(direction_rows),
                               additional_official_directional_records=source_manifest['additional_directional_records'],
                               partition_statuses=dict(Counter(p['status'] for p in partitions.values())),
                               status_counts=result['status_counts'],withheld_reasons=result['withheld_reasons'],
                               group_kinds=dict(Counter(g['kind'] for g in result['groups'])),
                               associated_form_statuses={k:dict(v) for k,v in counts_by_form.items()},
                               osm_ways=len(ways),osm_invalid_geometry_withheld_ids=osm_withheld),
                  coverage={name:coverage(links,result['decisions'],bounds) for name,bounds in (('core',core),('source_halo',halo))},
                  annual_day_evening_night_flows=None,observed_speed_kmh=None,noise_predictions_created=False,
                  runtime=dict(python=sys.version,shapely=shapely.__version__,pyproj=pyproj.__version__),
                  limitations=[
                      'An annual count supports its point. Prior link matching and constant flow along each associated link remain provisional assumptions.',
                      '2026 OS links are generalised; historical geometry compatibility with 2021 is not established.',
                      'Year-end 2021 OSM tags are corroboration, not authoritative or full-year traffic restrictions; geometric matching is provisional.',
                      'A dual allocation needs two local separate members, opposite published directions, explicit compatible one-way geometry and no overlapping mixed representation.',
                      'Collapsed dual has one unsplit budget. No inferred separation, equal split, default one-way or opposite-direction zero is introduced.',
                      'Slip roads, roundabouts, tunnels and unresolved associations receive no inherited primary flow.',
                      'Published directional streams are consumed once per representation group. Reference total is audit metadata; rounded residuals are not redistributed.',
                      'Traffic can continue through sequential links. Summing vehicle flows over links/groups is not a network total.',
                      'This covers retained count associations only; unresolved links have null primary flow. No class-median traffic, observed speed, annual period flow or acoustic prediction is produced.'])
    output.mkdir(parents=True)
    for n in code_names:
        target = output/'code'/n
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/n,target)
    for n in ('osm-query.overpassql','manifest.json'):
        shutil.copyfile(sources/n,output/('source-'+n))
    (output/'donor_partitions.json').write_bytes(json_bytes(dict(reference_year=2021,points=list(partitions.values()))))
    (output/'osm_way_evidence_bng.json').write_bytes(json_bytes(dict(crs='EPSG:27700',projection=project.description,
        snapshot_utc=report['osm_snapshot_utc'],ways=osm_records,invalid_geometry_withheld_ids=osm_withheld)))
    (output/'carriageway_budgets.json').write_bytes(json_bytes(dict(schema_version=1,status='provisional',
        source_licences=licences,groups=result['groups'],consumption='Use components once per group; primary_flow is a convenience total, not an additional emission source.')))
    (output/'allocation_decisions.json').write_bytes(json_bytes(dict(decisions=result['decisions'])))
    columns = ['link_id','count_point_id','form_of_way','road_structure','status','reason','group_id','primary_flow',
               'road_number','start_node','end_node','geometry_wkt_bng','core_length_m','halo_length_m']
    with (output/'road_representation_ledger_bng.csv').open('w',encoding='utf-8',newline='') as stream:
        writer = csv.DictWriter(stream,fieldnames=columns)
        writer.writeheader()
        for decision in result['decisions']:
            l = by_link[decision['link_id']]
            row = {k:decision.get(k) for k in columns[:8]}
            row.update({k:l.get(k) for k in ('road_number','start_node','end_node')})
            row.update(geometry_wkt_bng=l['geometry'].wkt,core_length_m=l['geometry'].intersection(box(*core)).length,
                       halo_length_m=l['geometry'].intersection(box(*halo)).length)
            writer.writerow(row)
    to_wgs = pyproj.Transformer.from_crs(27700,4326,always_xy=True)
    features = [dict(type='Feature',geometry=mapping(transform(to_wgs.transform,l['geometry'])),
                     properties={k:by_decision[l['link_id']][k] for k in columns[:8]})
                for l in links if by_decision[l['link_id']]['association']]
    (output/'associated_roads_review_wgs84.geojson').write_bytes(json_bytes(dict(type='FeatureCollection',features=features)))
    (output/'report.json').write_bytes(json_bytes(report))
    for n,h in {**source_hashes,**code_hashes}.items():
        if file_hash(ROOT/n) != h:
            raise ValueError('Source/code changed during generation')
    for n,h in parent_hashes.items():
        verify_inventory(parents[n])
        if file_hash(parents[n]/'inventory.json') != h:
            raise ValueError('Parent changed during generation')
    files = {p.relative_to(output).as_posix():file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output/'inventory.json').write_bytes(json_bytes(dict(
        generation_id='road-direction-inputs-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20],files=files,noise_predictions_created=False)))
    print(json.dumps({'summary':report['summary'],'coverage':report['coverage']}),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'artifacts/road_direction_inputs_v2'))
    parser.add_argument('--sources',default=str(ROOT/'artifacts/road_direction_sources_v2'))
    args = parser.parse_args()
    run(args.output,args.sources)
