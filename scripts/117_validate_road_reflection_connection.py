"""Two bounded blocks: path/line connection and independently reviewable evidence.

Uses retained sources only. Native study snapshots are an input audit, never
one-point approximations to complete roads. All outputs require a fresh directory.
"""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import time

import numpy as np
from shapely.geometry import LineString

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.conditional_reflection_domain import reflection_cases, SCOPE
from quiet_uk.conditional_road_reflections import (combine_path_density,
    facade_inputs, integrate_selected_road_paths, reflected_transfer)
from quiet_uk.conditional_road_propagation import direct_transfer
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.physical_scene_assumptions import assumption_cases
from quiet_uk.propagation_canary import BANDS
from quiet_uk.reflection_path import specular_plan
from quiet_uk.road_scene_builder import segment_road
from quiet_uk.screened_reflection import screened_wall_attenuation


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec)
    sys.modules[name]=m;spec.loader.exec_module(m);return m


prior=module('retained_screened_examples',ROOT/'scripts/79_run_screened_line_checks.py')
helper=prior.helper
read=lambda p:json.loads(Path(p).read_bytes())
PHYSICAL=next(c for c in assumption_cases() if c['case_id']=='combined_reference')
REFLECTION=reflection_cases()[1]
SOURCE_POWER=np.array([[[100.+30*p+10*g+3*b for b in range(8)] for g in range(2)] for p in range(3)])
AIR_ALPHA=np.array([[.1,.2,.5,1.,2.,4.,10.,25.],[.2,.4,1.,2.,4.,8.,20.,50.]])
NEW_FILES=['src/quiet_uk/conditional_reflection_domain.py','src/quiet_uk/conditional_road_reflections.py',
           'scripts/117_validate_road_reflection_connection.py','scripts/118_verify_road_reflection_connection.py',
           'tests/test_conditional_road_reflections.py',
           *['scripts/'+n for n in ('80_verify_screened_line_checks.py','77_verify_reflection_checks.py',
               '74_verify_weather_checks.py','71_verify_sightline_checks.py','68_verify_multiple_edge_checks.py',
               '65_verify_terrain_boundary_checks.py','62_verify_terrain_profile_checks.py')]]
CHANGED='src/quiet_uk/screened_reflection.py'


def plain(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value


def power(distance):
    return np.sum(SOURCE_POWER*np.power(10.,-AIR_ALPHA[None,:,:]*distance/10000),axis=1)


def guard():
    model=read(ROOT/'artifacts/remaining_road_validation_v1.json')
    for name,sha in model['code_sha256'].items():
        if name!=CHANGED and file_hash(ROOT/name)!=sha:raise ValueError('Prior model changed: '+name)
    baseline=ROOT/'artifacts/remaining_road_primary_replay_v1/code/screened_reflection.py'
    assert file_hash(baseline)==model['code_sha256'][CHANGED]
    old=baseline.read_text(encoding='utf-8')
    expected=old.replace('from quiet_uk.propagation_canary import',
        'from quiet_uk.conditional_reflection_domain import reflection_scene_supported\nfrom quiet_uk.propagation_canary import',1)
    expected=expected.replace('absorption_frequency_policy=None):\n','absorption_frequency_policy=None,conditional_reflection_policy=None):\n',1)
    expected=expected.replace("scene.get('scene_kind') not in ('synthetic','published_reference')","not reflection_scene_supported(scene,wall,conditional_reflection_policy)",1)
    assert (ROOT/CHANGED).read_text(encoding='utf-8')==expected,'Only the explicit reflection-domain gate may change'
    ledger=model['postcheck_ledger'];assert file_hash(ROOT/ledger['path'])==ledger['sha256']
    return model


def controls(model,x):
    """Explicit flat, incoherent test road; no local observations are implied."""
    source=[x,0.];receiver=[0.,30.]
    plan_input=dict(source_xy=source,receiver_xy=receiver,wall_start_xy=[-100.,-20.],wall_end_xy=[100.,-20.])
    plan=specular_plan(**plan_input);direct_length=math.dist(source,receiver);length=plan['unfolded_horizontal_distance_m']
    def scene(distance,obstacles):
        return dict(points=[[0.,0.],[distance,0.]],source=[0.,.05],receiver=[distance,4.],source_g=0.,
            ground_segments=[[0.,distance,0.]],obstacles=obstacles,opaque=True,infinite_lateral_extent=True,
            scene_kind='conditional_real_input')
    direct_obstacles=[dict(vertices=[[.4*direct_length,6.]])] if model=='screen_after_reflection' else []
    reflected_obstacles=[dict(vertices=[[52/70*length,6.]])] if model=='screen_after_reflection' else []
    wall=dict(unfolded_x_m=plan['reflection_distance_from_source_m'],base_z_m=0.,top_z_m=8.,slope_from_vertical_deg=0.,
        absorption_by_band=[.2]*8,incident_view_height_m={s:1. for s in ('homogeneous','favourable')},
        incident_view_width_m={s:1. for s in ('homogeneous','favourable')},unfolded_geometry_confirmed=True)
    return scene(direct_length,direct_obstacles),scene(length,reflected_obstacles),wall,plan_input


def connected(direct_scene,ref_scene,wall,plan_input):
    d=direct_transfer(dict(geometry_blockers=[],candidate_scene=direct_scene),PHYSICAL)
    r=reflected_transfer(dict(geometry_blockers=[],candidate_scene=ref_scene,wall=wall),PHYSICAL,REFLECTION)
    density=combine_path_density([dict(path_id='direct',transfer=d),dict(path_id='facade',plan_input=plan_input,transfer=r)],power)
    return density,d,r


def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    start=time.perf_counter();model=guard()
    parents={n:ROOT/'artifacts'/n for n in ('screened_line_sources_v1','screened_line_checks_v3','screened_line_review_v2',
        'real_road_scenes_v2','physical_scene_assumptions_v2','physical_scene_assumption_review_v1')}
    for path in parents.values():helper.verified(path)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    codes={**model['code_sha256'],**{n:file_hash(ROOT/n) for n in [CHANGED,*NEW_FILES]}}
    output.mkdir(parents=True)
    public=[];original={(r['case_id'],r['frequency_hz']):r for r in read(parents['screened_line_checks_v3']/'reference_results.json')['benchmarks']}
    for test in prior.TESTS:
        c=prior.fixture(parents['screened_line_sources_v1'],test)
        density,d,r=connected(c['direct_scene'],c['scene'],c['wall'],c['plan_input'])
        assert density['status']=='conditional_research'
        legacy=[]
        for f in BANDS:
            result=screened_wall_attenuation(c['scene'],c['wall'],f,prior.AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            old=original[(test,f)]
            for state in ('homogeneous','favourable'):
                assert result[state]['status']==old['result'][state]['status']
                if result[state]['status']=='conditional_research':
                    assert abs(result[state]['attenuation_db']-old['result'][state]['attenuation_db'])<2e-8
            legacy.append(dict(frequency_hz=f,result=result,retained_comparisons=old['comparisons']))
        public.append(dict(case_id=test,density=density,direct=d,reflected=r,original_reference=legacy))
    (output/'public_connection.json').write_bytes(json_bytes(plain(dict(cases=public))))
    print('Connection block: five public paths, all octaves passed',flush=True)
    geometry=LineString([[-30.,0.],[0.,0.],[30.,0.]])
    source=dict(link_id='controlled-road',source_cells=segment_road('controlled-road',geometry),prepared_length_m=geometry.length)
    lines=[]
    for name in ('clear','screen_after_reflection'):
        def evaluate(xy):
            direct,ref,wall,plan=controls(name,xy[0]);return connected(direct,ref,wall,plan)[0]
        result=integrate_selected_road_paths(source,[0.,30.],evaluate,subdivisions=(8,16,32))
        assert result['selected_paths_whole_line_relative_energy'] is not None
        lines.append(dict(case_id=name,result=result))
        print('Connection block: controlled '+name+' road line completed',flush=True)
    (output/'line_connection.json').write_bytes(json_bytes(plain(dict(source=source,cases=lines))))
    features=read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    policies=read(parents['physical_scene_assumptions_v2']/'source_physical_inputs.json')['roads']
    with (parents['physical_scene_assumptions_v2']/'profile_sensitivity.csv').open(encoding='utf-8',newline='') as f:
        overlays={(r['link_id'],r['profile_id']):r for r in csv.DictReader(f) if r['case_id']==PHYSICAL['case_id']}
    counts=Counter();reasons=Counter();roads=[]
    with gzip.open(parents['real_road_scenes_v2']/'prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            native=json.loads(line);prepared=native['scene'];link=native['link_id'];refs=[]
            for index,ref in enumerate(prepared['reflections']):
                c=facade_inputs(ref,features,PHYSICAL,REFLECTION)
                overlay=overlays[(link,'reflection:'+str(index))]
                old_blockers=set(filter(None,overlay['geometry_blockers'].split(';')))-{'missing_or_ambiguous_reflecting_wall_top','reflecting_top_below_native_base'}
                c['geometry_blockers']=sorted(old_blockers|set(c['geometry_blockers']))
                diagnostic={k:float(overlay[k]) if overlay[k] else None for k in ('candidate_obstacles','obstacles_above_endpoint_sightline','maximum_obstacle_clearance_m','maximum_terrain_clearance_m')}
                row=dict(facet_id=ref['facet_id'],wall=c['wall'],geometry_blockers=c['geometry_blockers'],
                    geometry_diagnostic=diagnostic,native_profile_sha256=hashlib.sha256(json.dumps(ref['profile'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest(),
                    reflecting_feature_ids=c['reflecting_feature_ids'],candidate_scene_kind='conditional_real_input')
                refs.append(row);reasons.update(row['geometry_blockers']);counts['reflected_profiles']+=1
                counts['geometry_held' if row['geometry_blockers'] else 'geometry_candidate']+=1
            roads.append(dict(link_id=link,reflections=refs,reflection_search=prepared['reflection_search'],
                source_policy_status=policies[link]['physical_source']['status'],
                source_point_selection=native['source_point_selection'],whole_road_relative_energy=None,all_inclusive_noise_db=None))
            counts['road_snapshots']+=1
            if counts['road_snapshots']%100==0:print(f"Native input attachment: {counts['road_snapshots']}/722 snapshots",flush=True)
    assert counts['road_snapshots']==722 and counts['reflected_profiles']==1418
    (output/'native_input_attachment.json').write_bytes(json_bytes(dict(roads=roads,counts=counts,reason_counts=reasons)))
    protocol=dict(physical_case=PHYSICAL,reflection_cases=reflection_cases(),executed_reflection_case=REFLECTION,
        scope=SCOPE,source_group_power_per_m=SOURCE_POWER.tolist(),air_alpha_db_per_km=AIR_ALPHA.tolist(),
        controlled_line_models=['clear','screen_after_reflection'],line_tolerance_db=.005,
        controlled_line_geometry='x=-30..30,y=0,z=.05; receiver=(0,30,4); reflector y=-20,x=-100..100,base0,top8; G0. Screen, when present, crosses y=12,z6.',
        input_audit='All existing closest-point snapshots only; no new pilot and no one-point road integrals.',
        overlay_reuse='Unchanged, independently audited physical overlays reused from sealed profile_sensitivity.csv. Newly attached facade assumptions and guards are computed for all 1418 profiles; full materializer connection is exercised by focused checks.',
        wall_qualification='Vertical, solid facades and 1m H/F apparent view dimensions are explicit unverified hypotheses. Flat roof tops derive from the physical scenario. Absorption cases are sensitivity hypotheses, not measured materials or uncertainty bounds.',
        completion='Two bounded validation blocks only; independent review is required. No complete all-path or annual-map qualification.',
        prior_validation_sha256=file_hash(ROOT/'artifacts/remaining_road_validation_v1.json'),
        unchanged_direct_ledger=model['postcheck_ledger'])
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    summary=dict(public_paths=5,public_octave_cases=40,retained_public_scalar_comparisons=sum(len(r['comparisons']) for r in original.values()),
        controlled_road_lines=2,controlled_period_band_condition_integrals=96,native_input_attachment=counts,
        elapsed_seconds=time.perf_counter()-start)
    guard();assert all(file_hash(ROOT/n)==sha for n,sha in codes.items())
    for n,p in parents.items():assert file_hash(p/'inventory.json')==hashes[n]
    for n in codes:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    report=dict(status='completed_bounded_road_reflection_connection',summary=summary,parent_inventory_sha256=hashes,
        code_sha256=codes,changed_prior_files=[CHANGED],prior_unchanged_code_files=len(model['code_sha256'])-1,
        complete_physical_path_support=False,field_validation=False,external_engine_executed=False,
        all_inclusive_noise_db=None,map_updated=False)
    (output/'report.json').write_bytes(json_bytes(report))
    print(json_bytes(dict(generation_id=helper.seal(output,'road-reflection-connection-'),summary=summary)).decode(),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=str(ROOT/'artifacts/road_reflection_connection_v2'))
    run(parser.parse_args().output)
