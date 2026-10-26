"""Refine the previously unstable retained road cases, offline and immutably."""
import argparse
from collections import Counter,deque
from concurrent.futures import ProcessPoolExecutor
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.adaptive_road_integration import DEFAULTS,geometry_breakpoints,integrate_adaptive
from quiet_uk.conditional_road_propagation import direct_transfer,road_guard,source_joint_power,receiver_metrics
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.physical_scene_assumptions import materialize_profile

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
prior=module('prior_conditional_runner',ROOT/'scripts/89_integrate_conditional_roads.py')
STATE={}

def read(path):return json.loads(Path(path).read_bytes())

def worst_change(row):
    changes=row.get('spacing_change_db')
    return None if not changes else max(abs(v) for state in changes.values() for v in state.values() if v is not None)

def initialise(output):
    prior.initialise(output);STATE.update(prior.STATE)
    STATE['protocol']=read(Path(output)/'protocol.json')
    STATE['seeds']=read(Path(output)/'seed_evaluations.json')

def compute(job):
    source,case_id=job;start=time.perf_counter();s=STATE;link=source['link_id']
    case=next(c for c in prior.CASES if c['case_id']==case_id)
    policy=s['policies'][link]['physical_source'];road=s['roads'][link]
    guard=road_guard(source,s['builder'],s['features'],case,policy,prior.RECEIVER)
    if guard:raise ValueError('Previously numeric selected source now failed whole-source guard')
    partition=geometry_breakpoints(source,prior.RECEIVER,s['builder'])
    def evaluate(xy):
        p=s['builder'].prepare_point(xy,prior.RECEIVER,reflections=False)
        candidate=materialize_profile(p['direct'],s['features'],case,source_policy=policy,endpoint_ids=p['endpoint_footprint_ids'])
        transfer=direct_transfer(candidate,case,retain_kernel_scene=True)
        metadata={k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in transfer.items()}
        metadata.update(native_profile_sha256=hashlib.sha256(json_bytes(p['direct'])).hexdigest(),
            omitted_screen_feature_ids=candidate['omitted_screen_feature_ids'],
            native_terrain_intervals=len(p['direct']['terrain_native_intervals']),native_ground_runs=len(p['direct']['ground_native_samples']))
        if transfer['status']=='withheld':return metadata
        power=source_joint_power(road,s['coefficients'],s['calendar'],s['air'],transfer['direct_distance_m'])
        if power is None:return dict(metadata,status='withheld',reasons=['missing_or_unsupported_joint_source_air'])
        return dict(metadata,joint_hourly_air_source_power_per_m=power.tolist(),relative_energy_per_m=(transfer['relative_transfer'][:,None,:]*power[None,:,:]).tolist())
    integrated=integrate_adaptive(source,evaluate,partition=partition,controls=s['protocol']['controls'],seed_evaluations=s['seeds'][link+'|'+case_id])
    integrated.update(link_id=link,physical_case_id=case_id,elapsed_seconds=time.perf_counter()-start)
    return integrated

def bounded(pool,jobs):
    inputs=iter(jobs);pending=deque()
    for _ in range(4):
        job=next(inputs,None)
        if job is not None:pending.append(pool.submit(compute,job))
    while pending:
        result=pending.popleft().result()
        job=next(inputs,None)
        if job is not None:pending.append(pool.submit(compute,job))
        yield result

def run(output,threshold,limit,workers,tolerance,maximum_panel,reuse_observations=None):
    output=Path(output).resolve();start=time.perf_counter();parent=ROOT/'artifacts/conditional_road_integration_v4'
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    if workers not in (1,2):raise ValueError('Worker budget must be 1 or 2')
    paths=[Path(__file__),ROOT/'scripts/89_integrate_conditional_roads.py',ROOT/'scripts/57_acquire_ground_cover_sources.py',ROOT/'scripts/81_build_real_road_scenes.py',*(ROOT/'src/quiet_uk').glob('*.py')]
    code_hashes={p:file_hash(p) for p in paths}
    parents=[parent,ROOT/'artifacts/conditional_road_integration_review_v1',*[ROOT/'artifacts'/n for n in prior.PARENTS]]
    if reuse_observations:
        observations=Path(reuse_observations).resolve()
        if not observations.is_relative_to(ROOT/'artifacts'):raise ValueError('Workspace observation generation required')
        parents.append(observations)
    inventories={p.name:prior.helper.verified(p) for p in parents}
    hashes={p.name:file_hash(p/'inventory.json') for p in parents}
    if reuse_observations:
        receipt=read(observations/'receipt.json')
        assert all(hashes[n]==sha for n,sha in receipt['parent_inventory_sha256'].items())
        # Controller changes are allowed; all numerical physics is unchanged.
        for path in (observations/'code').glob('*.py'):
            if path.name in ('92_refine_conditional_roads.py','97_capture_development_road_observations.py','adaptive_road_integration.py'):continue
            live=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
            assert file_hash(path)==file_hash(live),'Reused observation physics changed'
    ledger=read(parent/'road_contributions.json')['roads']
    selected=sorted([r for r in ledger if worst_change(r) is not None and worst_change(r)>threshold],key=lambda r:(-worst_change(r),r['link_id'],r['physical_case_id']))
    if limit is not None:selected=selected[:limit]
    if not selected:raise ValueError('No selected unstable roads')
    sources={s['link_id']:s for s in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    jobs=[(sources[r['link_id']],r['physical_case_id']) for r in selected]
    output.mkdir(parents=True)
    seeds={r['link_id']+'|'+r['physical_case_id']:[] for r in selected}
    with gzip.open(parent/'node_evaluations.jsonl.gz','rt') as stream:
        for line in stream:
            node=json.loads(line);key=node['link_id']+'|'+node['physical_case_id']
            if key in seeds:
                node['fraction']=(node['subdivision']+.5)/node['subdivisions']
                seeds[key].append(node)
    if reuse_observations:
        seen={k:{(n['source_cell_id'],n['fraction']) for n in nodes} for k,nodes in seeds.items()}
        def retain_cached(key,nodes):
            if key not in seeds:return
            unknown=next((n for n in nodes if n['status']=='withheld'),None)
            # An unknown is sufficient evidence to hold the whole road. Keep
            # its first witness, rather than load thousands of unused points.
            retained=[unknown] if unknown is not None else nodes
            for node in retained:
                identity=(node['source_cell_id'],node['fraction'])
                if identity not in seen[key]:seeds[key].append(node);seen[key].add(identity)
        with gzip.open(observations/'point_evaluations.jsonl.gz','rt') as stream:
            current=None;cached=[]
            for line in stream:
                node=json.loads(line);key=node['link_id']+'|'+node['physical_case_id']
                if current is not None and key!=current:retain_cached(current,cached);cached=[]
                current=key;cached.append(node)
            if current is not None:retain_cached(current,cached)
    (output/'seed_evaluations.json').write_bytes(json_bytes(seeds))
    for filename in ('air_tables.json','air_tables.npz'):shutil.copyfile(parent/filename,output/filename)
    controls=dict(DEFAULTS,tolerance_db=tolerance,maximum_panel_m=maximum_panel)
    protocol=dict(parent_inventory_sha256=hashes,controls=controls,
        reused_observation_directory=observations.name if reuse_observations else None,
        reused_observation_policy='All points of fully known cached cases; first unknown witness only in cases with any held point. Original coarse/fine points always retained.',
        executed_code_sha256={p.name:h for p,h in code_hashes.items()},
        selection=dict(prior_absolute_spacing_change_greater_than_db=threshold,maximum_cases=limit,selected_cases=len(selected),eligible_prior_cases=sum(worst_change(r) is not None and worst_change(r)>threshold for r in ledger)),
        selected_identities=[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id'],prior_worst_change_db=worst_change(r)) for r in selected],
        receiver_xy_bng=prior.RECEIVER,physical_cases=prior.CASES,
        physical_operating_and_terrain_policy='Unchanged from conditional_road_integration_v4; identical copied air tables and prior source, speed, weather, 0.1m terrain simplification and direct kernel.',
        quadrature='Source terrain grid and supplied obstacle vertex rays; <=4m panels; midpoint versus two quarter points and two independent interior Gauss points; absolute local energy proxies and historical probe retention; largest-error refinement.',
        indicator_scope='A-weighted day/evening/night/Lden, H and F separately. Bandwise absolute local differences precede weighting; not a per-octave relative tolerance.',
        rigorous_error_bound_db=None,refinement_convergence_proven=False,path_transition_partition_complete=False,
        held_when='Any observed unknown positive sample, missing coverage or numerical diagnostic not met. No fallback to old numeric value for selected failures.',
        unselected_cases='Prior finite-grid diagnostics retained for traceability; finer integration remains outstanding.',
        scope='direct_only_reflections_omitted',unknown_network_sources=13206,complete_physical_path_support=False,real_area_validated=False,all_inclusive_noise_db=None,
        worker_budget=workers)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    print(f'Refining {len(jobs)} previously unstable road cases',flush=True)
    refined=[]
    with (output/'node_evaluations.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
            def consume(results):
                for i,r in enumerate(results):
                    for node in r.pop('evaluations'):stream.write((json.dumps(dict(node,link_id=r['link_id'],physical_case_id=r['physical_case_id']),allow_nan=False,separators=(',',':'))+'\n').encode())
                    old=selected[i];r['prior_worst_spacing_change_db']=worst_change(old)
                    previous=np.asarray(old['whole_road_relative_energy']);current=r['whole_road_relative_energy']
                    r['change_from_prior_fine_db']=None if current is None else {
                        state:{k:None if value is None or receiver_metrics(previous[j])[k] is None else value-receiver_metrics(previous[j])[k] for k,value in receiver_metrics(np.asarray(current)[j]).items()}
                        for j,state in enumerate(('homogeneous','favourable'))}
                    refined.append(r);stream.flush()
                    print(f'{i+1}/{len(jobs)} {r["link_id"]} {r["termination"]}; {r["evaluation_count"]} nodes; indicator {r["indicator_db"]}; elapsed {time.perf_counter()-start:.1f}s',flush=True)
                    (output/'progress.json').write_bytes(json_bytes(dict(completed=i+1,selected=len(jobs),evaluations=sum(v['evaluation_count'] for v in refined),terminations=dict(Counter(v['termination'] for v in refined)))))
            if workers==1:initialise(output);consume(map(compute,jobs))
            else:
                with ProcessPoolExecutor(max_workers=workers,initializer=initialise,initargs=(str(output),)) as pool:consume(bounded(pool,jobs))
    by_id={(r['link_id'],r['physical_case_id']):r for r in refined}
    updated=[]
    for r in ledger:
        new=by_id.get((r['link_id'],r['physical_case_id']))
        updated.append(dict(link_id=r['link_id'],physical_case_id=r['physical_case_id'],
            integration_stage='adaptive_refined' if new else 'prior_finite_grid_only',
            integration_accuracy_outstanding=new is None or not new['diagnostic_tolerance_met'],
            status=new['status'] if new else r['status'],whole_road_relative_energy=new['whole_road_relative_energy'] if new else r['whole_road_relative_energy']))
    report=dict(selected_road_cases=len(refined),selected_source_length_m=sum(r['source_length_m'] for r in refined),
        evaluations=sum(r['evaluation_count'] for r in refined),new_evaluations=sum(r['new_evaluation_count'] for r in refined),terminations=dict(Counter(r['termination'] for r in refined)),
        diagnostic_met_cases=sum(r['diagnostic_tolerance_met'] for r in refined),new_unknown_cases=sum(r['unknown_positive_sample_observed'] for r in refined),
        maximum_met_indicator_db=max((r['indicator_db'] for r in refined if r['diagnostic_tolerance_met']),default=None),
        maximum_met_offset_change_db=max((abs(v) for r in refined if r['diagnostic_tolerance_met'] for state in r['offset_change_db'].values() for v in state.values() if v is not None),default=None),
        prior_unselected_case_count=len(ledger)-len(refined),elapsed_seconds=time.perf_counter()-start,
        map_updated=False,all_inclusive_noise_db=None,real_area_validated=False,rigorous_noise_error_bound_db=None)
    (output/'refined_roads.json').write_bytes(json_bytes(dict(roads=refined)))
    (output/'road_ledger.json').write_bytes(json_bytes(dict(roads=updated,all_inclusive_noise_db=None)))
    (output/'report.json').write_bytes(json_bytes(report))
    if any(file_hash(p)!=h for p,h in code_hashes.items()):raise ValueError('Executed implementation changed during run')
    code=output/'code';code.mkdir()
    for path in paths:shutil.copyfile(path,code/path.name)
    (output/'provenance.json').write_bytes(json_bytes(dict(parent_inventory_sha256=hashes,code_sha256={p.name:file_hash(p) for p in code.glob('*.py')},licences='Preserve all source-specific attribution and licence manifests from immutable parents.')))
    for p in parents:
        prior.helper.verified(p)
        if file_hash(p/'inventory.json')!=hashes[p.name]:raise ValueError('Parent changed')
    generation=prior.helper.seal(output,'adaptive-road-integration-')
    print(json.dumps(dict(generation_id=generation,report=report)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True);parser.add_argument('--threshold-db',type=float,default=1.)
    parser.add_argument('--limit',type=int);parser.add_argument('--workers',type=int,default=2)
    parser.add_argument('--tolerance-db',type=float,default=.1);parser.add_argument('--maximum-panel-m',type=float,default=4.)
    parser.add_argument('--reuse-observations')
    args=parser.parse_args();run(args.output,args.threshold_db,args.limit,args.workers,args.tolerance_db,args.maximum_panel_m,args.reuse_observations)
