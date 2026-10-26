"""Independently reconcile carriageway exports with original geometry/count rows.

Produces a separate verification receipt and input-qualification map; no acoustic
accuracy claim and no modifications to the analytical input generation.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Patch, Rectangle
from shapely import from_wkt
from shapely.geometry import box
from shapely.geometry import LineString
from shapely.ops import transform
import pyproj

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.phase2c_road import load_os_open_roads_links
from quiet_uk.road_input_audit import VEHICLE_FIELDS, load_exact_traffic
from quiet_uk.road_temporal import number, sum_preserving_missing


def read_json(path):
    return json.loads(Path(path).read_bytes())


def verify_inventory(directory):
    directory = Path(directory).resolve()
    inventory = read_json(directory/'inventory.json')
    for name,digest in inventory['files'].items():
        p = (directory/name).resolve()
        if not p.is_relative_to(directory) or not p.is_file() or file_hash(p)!=digest:
            raise ValueError('Inventory failed: '+str(p))
    return inventory


def run(inputs,output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if (not inputs.is_relative_to(ROOT/'artifacts') or not output.is_relative_to(ROOT/'artifacts')
            or output.exists() or output.is_relative_to(inputs)):
        raise ValueError('Use immutable workspace inputs and a fresh separate review directory')
    inventory = verify_inventory(inputs)
    input_hash = file_hash(inputs/'inventory.json')
    report = read_json(inputs/'report.json')
    for name,digest in report['source_sha256'].items():
        p = (ROOT/name).resolve()
        if not p.is_relative_to(ROOT/'data') or file_hash(p)!=digest:
            raise ValueError('Original snapshot failed')
    for name,digest in report['parent_inventory_sha256'].items():
        p = ROOT/'artifacts'/name
        verify_inventory(p)
        if file_hash(p/'inventory.json')!=digest:
            raise ValueError('Parent failed')
    with (inputs/'road_representation_ledger_bng.csv').open(encoding='utf-8',newline='') as f:
        rows = list(csv.DictReader(f))
    if not 0 < len(rows) <= 30000:
        raise ValueError('Unbounded ledger')
    ledger = {r['link_id']:r for r in rows}
    if len(ledger)!=len(rows):
        raise ValueError('Duplicate physical identity')
    halo, core = box(*report['study']['source_halo_bounds']), box(*report['study']['core_bounds'])
    roads_name = next(n for n in report['source_sha256'] if n.endswith('.gpkg'))
    original = {l['link_id']:l for l in load_os_open_roads_links(ROOT/roads_name,halo.bounds)
                if l['geometry'].intersection(halo).length>0}
    with sqlite3.connect(f'file:{(ROOT/roads_name).as_posix()}?mode=ro',uri=True) as connection:
        attrs = {r[0]:r[1:] for r in connection.execute(
            'SELECT id,form_of_way,road_structure,start_node,end_node,fictitious,loop FROM road_link '
            'WHERE fid IN (SELECT id FROM rtree_road_link_geometry WHERE maxx>=? AND minx<=? AND maxy>=? AND miny<=?)',
            (halo.bounds[0],halo.bounds[2],halo.bounds[1],halo.bounds[3]))}
    original = {i:l for i,l in original.items() if not attrs[i][4]}
    if set(original)!=set(ledger):
        raise ValueError('Network coverage differs from original')
    decisions_list = read_json(inputs/'allocation_decisions.json')['decisions']
    decisions = {d['link_id']:d for d in decisions_list}
    if len(decisions)!=len(decisions_list) or set(decisions)!=set(ledger):
        raise ValueError('Decision identities differ')
    measured = {n:dict(counts=Counter(),lengths=defaultdict(float)) for n in ('core','source_halo')}
    for identity,row in ledger.items():
        geometry, decision = from_wkt(row['geometry_wkt_bng']), decisions[identity]
        a = attrs[identity]
        if (not geometry.equals_exact(original[identity]['geometry'],tolerance=0)
                or row['form_of_way']!=(a[0] or '') or row['road_structure']!=(a[1] or '')
                or row['start_node']!=a[2] or row['end_node']!=a[3]
                or row['road_number']!=(original[identity].get('road_number') or '')):
            raise ValueError('Original road geometry or attributes changed')
        for key in ('count_point_id','form_of_way','road_structure','status','reason','group_id'):
            if row[key]!=(decision[key] or ''):
                raise ValueError('CSV and JSON disagree')
        primary = float(row['primary_flow']) if row['primary_flow'] else None
        if primary!=decision['primary_flow'] or (row['status']=='withheld')!=(primary is None):
            raise ValueError('Unknown/zero/primary flow states disagree')
        if row['status']=='withheld' and (row['group_id'] or not row['reason']):
            raise ValueError('Withheld road has a budget or no reason')
        for region,area,length_column in (('core',core,'core_length_m'),('source_halo',halo,'halo_length_m')):
            length = geometry.intersection(area).length
            if not math.isclose(float(row[length_column]),length,abs_tol=1e-9):
                raise ValueError('Clipped length changed')
            if length>0:
                measured[region]['counts'][row['status']] += 1
                measured[region]['lengths'][row['status']] += length
    for region,m in measured.items():
        c = report['coverage'][region]
        if dict(m['counts'])!=c['status_link_counts'] or set(m['lengths'])!=set(c['status_length_m']):
            raise ValueError('Report status counts differ')
        for key,length in m['lengths'].items():
            if not math.isclose(length,c['status_length_m'][key],abs_tol=1e-7):
                raise ValueError('Report status lengths differ')

    partitions_list = read_json(inputs/'donor_partitions.json')['points']
    partitions = {p['count_point_id']:p for p in partitions_list}
    if len(partitions)!=len(partitions_list):
        raise ValueError('Duplicate donor')
    traffic_name = next(n for n in report['source_sha256'] if n.endswith('.csv'))
    traffic,_ = load_exact_traffic(ROOT/traffic_name,2021)
    traffic = {p['count_point_id']:p for p in traffic}
    source_dir = ROOT/'artifacts'/next(n for n in report['parent_inventory_sha256'] if n.startswith('road_direction_sources_'))
    directional = []
    for p in sorted((ROOT/'artifacts/road_temporal_sources_v1').glob('average-annual-daily-flow-by-direction-page-*.json')):
        directional += read_json(p)['data']
    for p in sorted(source_dir.glob('additional-aadf-directions-*.json')):
        directional += read_json(p)['data']
    original_streams = {(str(r['count_point_id']),r['direction_of_travel']):r for r in directional}
    if len(original_streams)!=len(directional):
        raise ValueError('Duplicate original directional record')
    for identity,p in partitions.items():
        if p['reference_all_direction_aadf']!=traffic[identity]:
            raise ValueError('Original annual point changed')
        source_rows = [r for r in directional if str(r['count_point_id'])==identity]
        if p['original_records']!=source_rows or len(p['streams'])!=len(source_rows):
            raise ValueError('Original directional records changed')
        for s in p['streams']:
            r = original_streams[identity,s['direction']]
            if (s['flow']!=number(r['all_motor_vehicles']) or s['hgv_flow']!=number(r['all_hgvs'])
                    or s['vehicle_categories']!={f:number(r.get(f.lower())) for f in VEHICLE_FIELDS}
                    or s['estimation_method']!=r['estimation_method']
                    or s['estimation_method_detailed']!=r['estimation_method_detailed']
                    or s['source_record_id']!=r.get('id')):
                raise ValueError('Published stream altered')

    groups = read_json(inputs/'carriageway_budgets.json')['groups']
    used_links, group_ids = set(), set()
    for g in groups:
        if g['group_id'] in group_ids:
            raise ValueError('Duplicate budget')
        group_ids.add(g['group_id'])
        partition = partitions[g['count_point_id']]
        expected = {s['direction']:s for s in partition['streams']}
        components = []
        for representation in g['representations']:
            identity = representation['link_id']
            if identity in used_links:
                raise ValueError('Physical road consumes multiple budgets')
            used_links.add(identity)
            decision = decisions[identity]
            if (decision['group_id']!=g['group_id'] or decision['count_point_id']!=g['count_point_id']
                    or decision['primary_flow']!=representation['flow']):
                raise ValueError('Ledger and budget disagree')
            if representation['flow']!=sum(c['flow'] for c in representation['components']):
                raise ValueError('Primary sum duplicates or drops a stream')
            for c in representation['components']:
                if {k:c[k] for k in expected[c['direction']]}!=expected[c['direction']]:
                    raise ValueError('Allocated stream altered')
            components += representation['components']
        if Counter(c['direction'] for c in components)!=Counter(expected.keys()):
            raise ValueError('Directions lost, zero-filled or duplicated in one budget')
        for field in ('flow','hgv_flow',*VEHICLE_FIELDS):
            get = lambda c:c[field] if field in ('flow','hgv_flow') else c['vehicle_categories'][field]
            if sum_preserving_missing(get(c) for c in components)!=sum_preserving_missing(get(s) for s in expected.values()):
                raise ValueError('Vehicle-category conservation failed')
        total = sum(c['flow'] for c in components)
        if (g['published_stream_total']!=total or g['reference_total_for_audit_only']!=traffic[g['count_point_id']]['flow']
                or g['rounding_residual']!=total-g['reference_total_for_audit_only']):
            raise ValueError('Audit reference or rounding altered')
        if g['kind']=='one_unsplit_centreline':
            if len(g['representations'])!=1 or ledger[g['representations'][0]['link_id']]['form_of_way'] not in ('Single Carriageway','Collapsed Dual Carriageway'):
                raise ValueError('Invalid unsplit representation')
        elif g['kind']=='historical_oneway_supported_dual_pair':
            if len(g['representations'])!=2 or any(len(r['components'])!=1 for r in g['representations']):
                raise ValueError('Invalid split representation')
            for r in g['representations']:
                d = decisions[r['link_id']]
                if (d['form_of_way']!='Dual Carriageway' or d['oneway_evidence']['forward_sign'] is None
                        or r['components'][0]['geometry_alignment']['forward_sign']!=d['oneway_evidence']['forward_sign']):
                    raise ValueError('Dual direction evidence differs')
        else:
            raise ValueError('Unknown budget kind')
    if used_links!={i for i,d in decisions.items() if d['primary_flow'] is not None}:
        raise ValueError('Primary flow outside explicit budgets')

    raw_osm = read_json(source_dir/read_json(source_dir/'manifest.json')['osm_response_file'])['elements']
    by_osm = {r['id']:r for r in raw_osm}
    exported_osm = read_json(inputs/'osm_way_evidence_bng.json')['ways']
    project = pyproj.Transformer.from_crs(4326,27700,always_xy=True)
    for w in exported_osm:
        r = by_osm[w['way_id']]
        projected = transform(project.transform,LineString([(p['lon'],p['lat']) for p in r['geometry']]))
        if (w['version']!=r['version'] or w['last_edit_utc']!=r['timestamp'] or w['tags']!=r.get('tags',{})
                or not from_wkt(w['geometry_wkt_bng']).equals_exact(projected,tolerance=0)):
            raise ValueError('OSM evidence attributes changed')
    for d in decisions.values():
        e = d['oneway_evidence']
        if e and any(i not in by_osm for i in e['way_ids']):
            raise ValueError('Invented OSM support')

    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__),output/Path(__file__).name)
    colours = {'unknown':'#d5d9dd','withheld':'#ce7044','provisional':'#168581','dual':'#6a50a7'}
    category_lines = defaultdict(list)
    for identity,row in ledger.items():
        category = ('unknown' if not row['count_point_id'] else 'withheld' if row['status']=='withheld'
                    else 'dual' if row['status']=='provisional_dual_direction_allocation' else 'provisional')
        category_lines[category].append([(x/1000,y/1000) for x,y in original[identity]['geometry'].coords])
    fig,axes = plt.subplots(1,2,figsize=(13,7),constrained_layout=True)
    for ax,area,title in ((axes[0],halo,'144 km² source area'),(axes[1],core,'4 km² study core')):
        for category in ('unknown','withheld','provisional','dual'):
            ax.add_collection(LineCollection(category_lines[category],colors=colours[category],
                                             linewidths=.45 if category=='unknown' else 1.3,zorder=1 if category=='unknown' else 2))
        ax.add_patch(Rectangle((core.bounds[0]/1000,core.bounds[1]/1000),2,2,
                              fill=False,edgecolor='#222222',linewidth=1.4,zorder=3))
        ax.set_xlim(area.bounds[0]/1000,area.bounds[2]/1000)
        ax.set_ylim(area.bounds[1]/1000,area.bounds[3]/1000)
        ax.set_aspect('equal'); ax.set_title(title)
        ax.set_xlabel('BNG easting (km)'); ax.set_ylabel('BNG northing (km)')
    handles = [Patch(color=colours[k],label=label) for k,label in (
        ('unknown','No retained count association'),('withheld','Count associated; allocation withheld'),
        ('provisional','Provisional unsplit traffic budget'),('dual','Provisional directional dual pair'))]
    axes[0].legend(handles=handles,loc='lower left',fontsize=8,framealpha=.95)
    fig.suptitle('Road carriageway input qualification | no noise predictions\n'
                 'Annual traffic 2021; generalised OS geometry 2026-04; OSM snapshot 31 Dec 2021',fontsize=12)
    fig.savefig(output/'road_direction_review.png',dpi=170)
    plt.close(fig)
    receipt = dict(input_generation_id=inventory['generation_id'],input_inventory_sha256=input_hash,
        original_road_geometries_verified=len(rows),exact_bng_geometry_comparison_tolerance_m=0,
        original_annual_points_verified=len(partitions),original_directional_records_verified=len(directional),
        provisional_groups_verified=len(groups),allocated_physical_links_verified=len(used_links),
        all_vehicle_categories_and_hgv_conserved_per_group=True,rounded_streams_not_redistributed=True,
        unknown_and_primary_flow_states_verified=True,coverage_counts_and_lengths_verified=True,
        osm_versions_and_tags_verified=len(exported_osm),verification_code_sha256=file_hash(Path(__file__)),
        independent_historic_traffic_or_acoustic_accuracy_validation=False)
    (output/'verification.json').write_bytes(json_bytes(receipt))
    verify_inventory(inputs)
    if file_hash(inputs/'inventory.json')!=input_hash:
        raise ValueError('Verification changed analytical inputs')
    files = {p.name:file_hash(p) for p in output.iterdir() if p.is_file()}
    (output/'inventory.json').write_bytes(json_bytes(dict(
        generation_id='road-direction-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20],files=files)))
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',default=str(ROOT/'artifacts/road_direction_inputs_v2'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/road_direction_review_v1'))
    args = parser.parse_args()
    run(args.inputs,args.output)
