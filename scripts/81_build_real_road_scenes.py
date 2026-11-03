"""Build source lines and bounded geographic path readiness from frozen real inputs."""
import argparse
from collections import Counter
import csv
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import pyproj
import rasterio
from shapely import from_wkt
from shapely.geometry import Point, box

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.ground_cover import CLASSES,validate_native
from quiet_uk.physical_scene import assert_native_grid
from quiet_uk.road_scene_builder import NativeTerrain,RoadSceneBuilder,segment_road
from quiet_uk.road_line_integration import point_path_transfer
from quiet_uk import generations as helper

# Moved to the package; re-exported for scripts that load this file by path.
from quiet_uk.real_scene_inputs import load_builder, read, rows  # noqa: E402


def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use a fresh workspace output')
    names=('physical_scene_inputs_v2','ground_cover_inputs_v3','road_direction_inputs_v2','road_emission_assumptions_v2',
        'screened_line_checks_v3','screened_line_review_v2','ground_barrier_audit_v1')
    parents={n:ROOT/'artifacts'/n for n in names}
    for p in parents.values():helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    physical,ground,roads,emissions=[parents[n] for n in names[:4]]
    ledger=rows(roads/'road_representation_ledger_bng.csv');budgets=read(roads/'carriageway_budgets.json')['groups']
    index=read(emissions/'group_evaluation_index.json')['groups'];emission_links={c['link_id']:c for c in index}
    groups={r['link_id']:g for g in budgets for r in g['representations']}
    if len(groups)!=len(budgets) or set(groups)!=set(emission_links):raise ValueError('Nonunique or unmatched source budget membership')
    selected=[r for r in ledger if r['link_id'] in groups]
    if len(selected)!=len(groups) or len(selected)!=722:raise ValueError('Unexpected retained source inventory')
    anchor={e['evaluation_id']:e for e in read(emissions/'donor_source_scenarios.json')['evaluations'] if e['case_id']=='anchor'}
    study=read(roads/'report.json')['study'];bounds=study['source_halo_bounds'];extent=box(*bounds)
    west,south,east,north=study['core_bounds'];receiver=[(west+east)/2,(south+north)/2]
    builder=load_builder(physical,ground);sources=[];source_counts=Counter()
    for row in selected:
        original=from_wkt(row['geometry_wkt_bng']);clipped=original.intersection(extent)
        cells=segment_road(row['link_id'],clipped);assignment=emission_links[row['link_id']]
        emission_id='anchor:'+assignment['evaluation_donor_id'];assert emission_id in anchor
        sources.append(dict(link_id=row['link_id'],group_id=row['group_id'],geometry_wkt_bng=clipped.wkt,
            original_geometry_wkt_bng=row['geometry_wkt_bng'],original_length_m=original.length,prepared_length_m=clipped.length,
            excluded_outside_extract_length_m=max(0.,original.length-clipped.length),source_cells=cells,
            source_emission_id=emission_id,traffic_budget=groups[row['link_id']],
            source_power_status='conditional_emission_case_only',qualified_relative_power_per_m=None,
            source_elevation_status='unknown_road_surface_or_deck; blank structure is not at-grade evidence',
            original_traffic_consume_policy='Components once per physical representation; published convenience total is audit metadata.',
            blockers=['provisional_constant_flow_link_association','unqualified_2021_2026_road_compatibility','unknown_local_period_traffic_speed_pavement',
                'source_gradient_and_junction_dynamics_unqualified','unknown_source_road_surface_or_deck','unknown_complete_network_contributions']))
        source_counts['source_cells']+=len(cells);source_counts['partly_outside_extract']+=int(original.length>clipped.length+1e-8)
    output.mkdir(parents=True)
    (output/'source_lines.json').write_bytes(json_bytes(dict(source_roads=sources,emission_cases=anchor,
        emission_qualification='Frozen anchor scenario retained unchanged: national timing and assumed 70km/h/reference surface/classification; source power per metre, not observed period inputs or receiver level.')))
    shutil.copyfile(roads/'road_representation_ledger_bng.csv',output/'road_input_inventory_bng.csv')
    summary=Counter();ready=[]
    with (output/'prepared_paths.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
            for i,source in enumerate(sources):
                geometry=from_wkt(source['geometry_wkt_bng']);position=geometry.project(Point(receiver));point=geometry.interpolate(position)
                if point.distance(Point(receiver))==0:
                    ready.append(dict(link_id=source['link_id'],status='withheld',receiver_level_db=None,blockers=['receiver_on_source_line']));summary['receiver_on_source_line']+=1;continue
                scene=builder.prepare_point([point.x,point.y],receiver)
                source_z=scene['direct']['kernel_scene']['source'][1];receiver_z=scene['direct']['kernel_scene']['receiver'][1]
                if source_z is None or receiver_z is None:
                    result={s:dict(status='withheld',reason='unknown_endpoint_elevation',attenuation_db=None) for s in ('homogeneous','favourable')}
                else:result=point_path_transfer([point.x,point.y,source_z],[*receiver,receiver_z],1000,
                    direct_scene=scene['direct']['kernel_scene'],reflections=[],reflection_support_complete=False,air=None)
                assert all(result[s]['status']=='withheld' for s in ('homogeneous','favourable'))
                record=dict(link_id=source['link_id'],receiver_id='existing-core-centre',source_point_chainage_m=position,
                    source_point_selection='Closest point on retained clipped line; geographic readiness snapshot, not source quadrature.',scene=scene,
                    source_emission_id=source['source_emission_id'],point_transfer_probe=result,
                    road_receiver_contributions={p:None for p in ('day','evening','night')},annual_noise_prediction=None)
                stream.write((json.dumps(record,allow_nan=False,separators=(',',':'))+'\n').encode('utf-8'))
                profile=scene['direct'];refs=scene['reflections'];search=scene['reflection_search']
                summary['direct_paths']+=1;summary['reflection_profiles']+=len(refs)
                summary['direct_terrain_intervals']+=len(profile['terrain_native_intervals'])
                summary['reflection_terrain_intervals']+=sum(len(r['profile']['terrain_native_intervals']) for r in refs)
                summary['direct_building_crossings']+=len([c for c in profile['crossings'] if c['kind']=='building'])
                summary['direct_barrier_crossings']+=len([c for c in profile['crossings'] if c['kind']=='barrier'])
                summary['reflection_budget_exceeded']+=int(search['reason']=='reflection_search_budget_exceeded')
                summary['eligible_plan_reflections']+=search.get('eligible_plan_candidates') or 0
                summary['unprepared_plan_reflections']+=search.get('unprepared_candidates') or 0
                summary['paths_with_missing_terrain']+=int(any(z is None for _,_,_,_,z in profile['terrain_native_intervals']))
                blockers=sorted(set(source['blockers']+scene['blockers']))
                ready.append(dict(link_id=source['link_id'],group_id=source['group_id'],source_emission_id=source['source_emission_id'],
                    source_easting=point.x,source_northing=point.y,source_chainage_m=position,direct_distance_m=profile['length_m'],
                    direct_building_crossings=sum(c['kind']=='building' for c in profile['crossings']),direct_barrier_crossings=sum(c['kind']=='barrier' for c in profile['crossings']),
                    prepared_reflections=len(refs),unprepared_reflections=search.get('unprepared_candidates'),
                    status='withheld',receiver_level_db=None,blockers=blockers))
                if (i+1)%100==0:print(f'Prepared {i+1}/{len(sources)} retained road paths',flush=True)
    (output/'road_location_readiness.json').write_bytes(json_bytes(dict(receiver=dict(receiver_id='existing-core-centre',xy_bng=receiver,
        height_agl_m=4.,height_status='declared assumption',location_status='Existing geometric core-centre target; not a measured noise receiver.'),
        roads=ready,network_status='withheld_incomplete_sources_and_inputs',combined_road_noise_db=None,annual_noise_prediction=None)))
    with (output/'road_location_readiness.csv').open('w',encoding='utf-8',newline='') as f:
        fields=['link_id','group_id','source_emission_id','direct_distance_m','direct_building_crossings','direct_barrier_crossings','prepared_reflections','status','receiver_level_db','blockers']
        writer=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');writer.writeheader()
        for r in ready:writer.writerow(dict(r,blockers=';'.join(r['blockers'])))
    protocol=dict(study=study,receiver_xy_bng=receiver,source_height_agl_m=.05,receiver_height_agl_m=4.,source_cell_maximum_length_m=25.,
        source_length_basis='horizontal BNG; all original polyline turns preserved',ground_midpoint_step_m=2.,
        reflection_extra_plan_length_m=100.,maximum_prepared_reflections_per_path=2,maximum_bounded_plan_candidate_facets_per_path=20000,
        reflection_scope='Finite generalised exterior-facade plan hits only, ordered by unfolded distance/identity. Conservative distance lower-bound and same-side filtering precede the mirror-solve budget. Coincident facades retain all identities once per physical plane. Extra-path-length/output caps are preparation budgets, not acoustic cutoffs. No qualified reflection support or catalogue completeness.',
        terrain_policy='Exact containing-cell intervals; sampled-midpoint polyline reconstruction is unqualified for real diffraction.',
        emission_case=next(c for c in read(emissions/'protocol.json')['cases'] if c['case_id']=='anchor'),
        path_selection='Every retained source road, closest point to existing core centre. These snapshots demonstrate geographic preparation; source-line quadrature is not executed with unqualified real inputs.',
        no_real_inputs_relabelled_synthetic=True)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    names=['src/quiet_uk/road_scene_builder.py','scripts/81_build_real_road_scenes.py']
    report=dict(status='prepared_real_geographic_scenes_acoustic_levels_withheld',summary=dict(summary,**source_counts,
        road_inventory_records=len(ledger),retained_source_roads=len(sources),withheld_source_roads=len(ledger)-len(sources),conditional_emission_donors=len(anchor),
        source_road_length_m=math.fsum(s['prepared_length_m'] for s in sources)),blocker_counts=dict(Counter(b for r in ready for b in r['blockers'])),
        parent_inventory_sha256=hashes,code_sha256={n:file_hash(ROOT/n) for n in names},
        source_licences={n:read(p/'report.json').get('licences',read(p/'report.json').get('source_licences',read(p/'report.json').get('licenses'))) for n,p in parents.items()},
        eligible_for_absolute_noise_release=False,external_engine_executed=False,real_area_noise_validated=False,full_current_method_qualified=False)
    (output/'report.json').write_bytes(json_bytes(report))
    for n in names:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(dict(generation_id=helper.seal(output,'real-road-scenes-'),summary=report['summary']),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=str(ROOT/'artifacts/real_road_scenes_v2'))
    run(parser.parse_args().output)
