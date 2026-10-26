"""Verified observation reuse, corrected integration and full finer checks.

Fresh, sealed stages preserve prior ledgers and the fixed acoustic equations.
Only points whose old/new controlling chain is identical can reuse numeric
transfer. Every historical unknown is evaluated again with native geometry.
"""
import argparse
from collections import Counter,defaultdict,deque
from concurrent.futures import ProcessPoolExecutor
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk import favourable_propagation as physics
from quiet_uk.sightline_boundary import physical_edge_candidates,validated_roofs
from quiet_uk.terrain_profile import linear_segments
from quiet_uk.conditional_road_propagation import receiver_metrics

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
refiner=module('corrected_geographic_runner',ROOT/'scripts/92_refine_conditional_roads.py')
checker=module('corrected_finer_rule',ROOT/'scripts/95_check_refined_road_spacing.py')
helper=refiner.prior.helper
TASK={}
CONTROLS=[('39445FD4-C51D-4767-B9A1-7203C773C48C','combined_reference'),('A22C07BF-4FD2-47B4-B590-E27D6B87B335','combined_reference')]
BASE='adaptive_road_integration_v3'
FINE='adaptive_road_spacing_check_v1'
DEVELOPMENT='development_road_observations_v1'
WITNESSES='curved_envelope_witnesses_v1'
BRIDGE='curved_envelope_fix_checks_v1'

def read(path):return json.loads(Path(path).read_bytes())
def case_key(r):return r['link_id']+'|'+r['physical_case_id']
def point_key(r):return (r['source_cell_id'],r['fraction'])

def compute(job):
    # Windows spawn must pickle a function defined in this importable script,
    # rather than the dynamically loaded evaluator module.
    return refiner.compute(job)

def bounded(pool,jobs):
    jobs=iter(jobs);pending=deque()
    for _ in range(4):
        job=next(jobs,None)
        if job is not None:pending.append(pool.submit(compute,job))
    while pending:
        result=pending.popleft().result();job=next(jobs,None)
        if job is not None:pending.append(pool.submit(compute,job))
        yield result

def compact_seed(node):
    # Full scenes stay in the sealed observation generation, with an exact ID.
    # Avoid copying thousands of profiles into both worker heaps and new files.
    return {k:v for k,v in node.items() if k!='candidate_kernel_scene'}

def observation_signature(node):
    names=('status','xy_bng','native_profile_sha256','relative_transfer','joint_hourly_air_source_power_per_m','relative_energy_per_m','reasons')
    return json_bytes({k:node.get(k) for k in names})

def verified_model():
    receipt=read(ROOT/'artifacts/curved_envelope_validation_v1.json')
    assert receipt['status']=='passed_curved_envelope_fix_validation'
    bridge=ROOT/'artifacts'/BRIDGE;helper.verified(bridge)
    assert file_hash(bridge/'inventory.json')==receipt['review_inventory_sha256']
    for path in (bridge/'code').glob('*.py'):
        live=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(path)==file_hash(live),'Reviewed numerical implementation changed'
    for name,sha in receipt['unchanged_original_code_sha256'].items():assert file_hash(ROOT/name)==sha
    assert file_hash(ROOT/'src/quiet_uk/favourable_propagation.py')==receipt['code_sha256']['src/quiet_uk/favourable_propagation.py']
    assert file_hash(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json')==receipt['unchanged_prior_ledger_sha256']
    return receipt

def code_paths():
    return [Path(__file__),*[ROOT/'scripts'/n for n in ('92_refine_conditional_roads.py','95_check_refined_road_spacing.py','89_integrate_conditional_roads.py','81_build_real_road_scenes.py','57_acquire_ground_cover_sources.py')],*(ROOT/'src/quiet_uk').glob('*.py')]

def prepare(output,parents):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace generation required')
    verified_model();hashes={}
    for directory in parents:
        helper.verified(directory);hashes[directory.name]=file_hash(directory/'inventory.json')
    output.mkdir(parents=True)
    for name in ('air_tables.json','air_tables.npz'):shutil.copyfile(ROOT/'artifacts'/BASE/name,output/name)
    return output,hashes,{p:file_hash(p) for p in code_paths()}

def finish(output,hashes,code_hashes,prefix):
    assert all(file_hash(p)==sha for p,sha in code_hashes.items()),'Executed code changed'
    for name,sha in hashes.items():helper.verified(ROOT/'artifacts'/name);assert file_hash(ROOT/'artifacts'/name/'inventory.json')==sha
    code=output/'code';code.mkdir()
    for p in code_hashes:shutil.copyfile(p,code/p.name)
    (output/'provenance.json').write_bytes(json_bytes(dict(parent_inventory_sha256=hashes,code_sha256={p.name:file_hash(p) for p in code.iterdir()},licences='Preserve every source-specific licence and attribution manifest in the immutable parents.')))
    generation=helper.seal(output,prefix);print(json.dumps(dict(generation_id=generation,report=read(output/'report.json'))),flush=True)

def collect_unknowns(source,evaluate,*,partition,controls,seed_evaluations):
    records=[]
    for old in TASK['unknowns'][source['link_id']+'|'+seed_evaluations[0]['physical_case_id']]:
        fresh=dict(evaluate(old['xy_bng']))
        assert fresh['native_profile_sha256']==old['native_profile_sha256'],'Native input changed at reevaluated point'
        if 'candidate_kernel_scene' in old:assert fresh['candidate_kernel_scene']==old['candidate_kernel_scene']
        fresh.update(link_id=old['link_id'],physical_case_id=old['physical_case_id'],source_cell_id=old['source_cell_id'],fraction=old['fraction'],xy_bng=old['xy_bng'],
            model_reuse=dict(kind='native_reevaluation_of_historical_unknown',prior_directory=old['_directory'],prior_node_id=old['node_id'],prior_reasons=old['reasons']))
        records.append(fresh)
    return dict(evaluations=records)

def initialise_unknowns(output):
    refiner.initialise(output);TASK['unknowns']=read(Path(output)/'unknown_observations.json')
    refiner.integrate_adaptive=collect_unknowns;refiner.geometry_breakpoints=lambda *args,**kw:None

def cache(output,resume=None):
    start=time.perf_counter();physical=list(refiner.prior.PARENTS)
    parents=[ROOT/'artifacts'/n for n in [BASE,FINE,DEVELOPMENT,WITNESSES,BRIDGE,'conditional_road_integration_v4',*physical]]
    if resume:
        resume=Path(resume).resolve();helper.verified(resume);parents.append(resume)
    output,hashes,codes=prepare(output,parents)
    witnesses=read(ROOT/'artifacts'/WITNESSES/'witnesses.json')['witnesses'];affected={case_key(r) for r in witnesses};allowed=affected|{'|'.join(k) for k in CONTROLS}
    sources={r['link_id']:r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    cells={link:{c['segment_id']:c for c in source['source_cells']} for link,source in sources.items()}
    original=module('original_reuse_geometry',ROOT/'artifacts'/BASE/'code/favourable_propagation.py')
    for path in (ROOT/'artifacts'/DEVELOPMENT/'code').glob('*.py'):
        if path.name in ('92_refine_conditional_roads.py','97_capture_development_road_observations.py','adaptive_road_integration.py'):continue
        assert file_hash(path)==file_hash(ROOT/'artifacts'/BASE/'code'/path.name),'Historical observations used different physics'
    seen=defaultdict(dict);seeds={key:[] for key in allowed};unknowns=defaultdict(list);counts=Counter();record_id=0
    streams=[(BASE,'node_evaluations.jsonl.gz'),(FINE,'node_evaluations.jsonl.gz'),(DEVELOPMENT,'point_evaluations.jsonl.gz')]
    protocol=dict(kind='corrected_road_observations_v1',parent_inventory_sha256=hashes,controls={},
        selection='All road/case identities with a recorded circular-envelope failure, plus the two previously passed finer-check controls.',
        affected_identities=sorted(affected),control_identities=['|'.join(k) for k in CONTROLS],observation_streams=[dict(directory=d,file=f) for d,f in streams],
        model_validation_sha256=file_hash(ROOT/'artifacts/curved_envelope_validation_v1.json'),old_ledger_sha256=file_hash(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json'),
        reuse_rule='Known points require exact old/new controlling-chain equality; every historical unknown is replayed natively. All distinct history is retained, including observed peaks and remaining unknowns.',
        physics_revision=physics.ENVELOPE_ALGORITHM,all_inclusive_noise_db=None,map_updated=False)
    if resume:protocol['recovered_observation_prefix']=resume.name
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    with (output/'point_observations.jsonl.gz').open('wb') as raw, gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as target:
        def write_record(node):
            nonlocal record_id
            node=dict(node,cache_record_id=record_id,cache_generation_directory=output.name);record_id+=1
            seeds[case_key(node)].append(compact_seed(node));target.write((json.dumps(node,allow_nan=False,separators=(',',':'))+'\n').encode())
        if resume:
            recovery_protocol=read(resume/'protocol.json');recovery_report=read(resume/'report.json')
            assert recovery_protocol['affected_identities']==protocol['affected_identities'] and recovery_protocol['control_identities']==protocol['control_identities']
            assert recovery_protocol['model_validation_sha256']==protocol['model_validation_sha256']
            assert all(hashes[n]==sha for n,sha in recovery_protocol['parent_inventory_sha256'].items())
            for path in (resume/'interrupted_code').glob('*.py'):
                if path.name=='101_rerun_corrected_roads.py':continue
                assert file_hash(path)==file_hash(ROOT/'src/quiet_uk'/path.name),'Recovered observation physics changed'
            with gzip.open(resume/'point_observations.jsonl.gz','rt') as stream:
                for line in stream:
                    node=json.loads(line);key=case_key(node);identity=point_key(node)
                    assert key in allowed and identity not in seen[key] and node['status']=='conditional_research'
                    seen[key][identity]=observation_signature(node)
                    node={k:v for k,v in node.items() if k not in ('cache_record_id','cache_generation_directory')}
                    write_record(node);counts['compatible_numeric_observations']+=1
            assert counts['compatible_numeric_observations']==recovery_report['numeric_observations']
            unknowns=read(resume/'unknown_observations.json')
            assert sum(map(len,unknowns.values()))==recovery_report['pending_native_unknowns']
            print(f'Restored {record_id} saved numeric observations; {sum(map(len,unknowns.values()))} native unknowns queued',flush=True)
        for directory,filename in (streams if resume is None else []):
            with gzip.open(ROOT/'artifacts'/directory/filename,'rt') as stream:
                for line in stream:
                    node=json.loads(line);key=case_key(node)
                    if key not in allowed:continue
                    cell=cells[node['link_id']][node['source_cell_id']];xy=[a+(b-a)*node['fraction'] for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
                    assert xy==node['xy_bng'];identity=point_key(node);signature=observation_signature(node)
                    if identity in seen[key]:assert seen[key][identity]==signature,'Contradictory duplicate observation';counts['duplicate_observations_verified']+=1;continue
                    seen[key][identity]=signature
                    if node['status']=='withheld':unknowns[key].append(dict(node,_directory=directory));continue
                    assert node['status']=='conditional_research' and node['input_scene_kind']=='conditional_real_input'
                    scene=node['candidate_kernel_scene'];roofs=validated_roofs(linear_segments(scene['points']),scene['obstacles']);candidates=physical_edge_candidates(scene['points'],roofs)
                    s,r=scene['source'],scene['receiver'];radius=physics.ray_radius(s,r)
                    assert original.circular_envelope(candidates,s,r,radius)==physics.circular_envelope(candidates,s,r,radius),'Numeric history needs full reevaluation'
                    node=dict(node,model_reuse=dict(kind='numeric_same_chain_verified',prior_directory=directory,prior_node_id=node['node_id']))
                    write_record(node);counts['compatible_numeric_observations']+=1
                    if counts['compatible_numeric_observations']%5000==0:print(f'{counts["compatible_numeric_observations"]} numeric observations verified for reuse',flush=True)
            print(f'{directory} scanned; {sum(map(len,unknowns.values()))} distinct historical unknowns queued',flush=True)
        (output/'unknown_observations.json').write_bytes(json_bytes(unknowns));(output/'seed_evaluations.json').write_bytes(json_bytes(unknowns))
        jobs=[(sources[key.split('|')[0]],key.split('|')[1]) for key in sorted(unknowns)]
        with ProcessPoolExecutor(max_workers=2,initializer=initialise_unknowns,initargs=(str(output),)) as pool:
            for i,row in enumerate(bounded(pool,jobs)):
                for node in row['evaluations']:write_record(node);counts['historical_unknowns_reevaluated']+=1;counts['reevaluated_'+node['status']]+=1
                target.flush();print(f'{i+1}/{len(jobs)} historical-unknown road cases reevaluated',flush=True)
    assert max(map(len,seeds.values()))<=refiner.DEFAULTS['maximum_evaluations'],'All history must fit the unchanged observation budget'
    (output/'seed_evaluations.json').write_bytes(json_bytes(seeds))
    (output/'report.json').write_bytes(json_bytes(dict(status='prepared_corrected_road_observations',counts=counts,observations=record_id,affected_cases=len(affected),control_cases=len(CONTROLS),
        maximum_observations_per_case=max(map(len,seeds.values())),elapsed_seconds=time.perf_counter()-start,all_inclusive_noise_db=None,map_updated=False)))
    finish(output,hashes,codes,'corrected-road-observations-')

def corrected_selection(observed):
    affected=set(read(observed/'protocol.json')['affected_identities'])
    old=read(ROOT/'artifacts/conditional_road_integration_v4/road_contributions.json')['roads']
    selected=sorted([r for r in old if case_key(r) in affected],key=lambda r:(-refiner.worst_change(r),r['link_id'],r['physical_case_id']))
    if len(selected)!=len(affected):raise ValueError('Incomplete affected population')
    return selected

def integrate(observed,audit,output):
    start=time.perf_counter();observed,audit=Path(observed).resolve(),Path(audit).resolve()
    helper.verified(observed);helper.verified(audit)
    reviewed=read(audit/'report.json');assert reviewed['status']=='passed_corrected_observation_accounting'
    assert reviewed['input_inventory_sha256']==file_hash(observed/'inventory.json')
    parent_names=read(observed/'protocol.json')['parent_inventory_sha256']
    parents=[observed,audit,*[ROOT/'artifacts'/n for n in parent_names]]
    output,hashes,codes=prepare(output,parents)
    selected=corrected_selection(observed);keys={case_key(r) for r in selected}
    cache_seeds=read(observed/'seed_evaluations.json');seeds={k:cache_seeds[k] for k in keys}
    (output/'seed_evaluations.json').write_bytes(json_bytes(seeds))
    protocol=dict(kind='corrected_road_integration_v1',parent_inventory_sha256=hashes,controls=refiner.DEFAULTS,
        corrected_observation_directory=observed.name,corrected_observation_review_directory=audit.name,
        selected_identities=[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in selected],
        selection=dict(kind='all_prior_circular_envelope_failure_cases',selected_cases=len(selected)),
        receiver_xy_bng=refiner.prior.RECEIVER,physical_cases=refiner.prior.CASES,
        old_ledger_sha256=file_hash(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json'),
        model_validation_sha256=file_hash(ROOT/'artifacts/curved_envelope_validation_v1.json'),physics_revision=physics.ENVELOPE_ALGORITHM,
        source_traffic_speed_weather_terrain_assumptions='Unchanged; old/new source, ground, height, weather, air tables and side-plane equations preserved.',
        observation_policy='All independently checked unique history is retained; old unknowns are replaced solely by native reevaluation. Maximum 2048 observations including history, target indicator 0.1dB, initial panels <=4m, minimum child 0.01m.',
        finer_selection='Every affected case meeting the primary diagnostic plus two prior finer-check controls.',
        scope='direct_only_reflections_omitted',complete_physical_path_support=False,rigorous_error_bound_db=None,refinement_convergence_proven=False,
        field_validation=False,all_inclusive_noise_db=None,map_updated=False)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    old_main=read(ROOT/'artifacts'/BASE/'refined_roads.json')['roads'];control_keys={'|'.join(k) for k in CONTROLS}
    controls=[r for r in old_main if case_key(r) in control_keys];assert len(controls)==2 and all(r['diagnostic_tolerance_met'] for r in controls)
    (output/'control_roads.json').write_bytes(json_bytes(dict(roads=controls)))
    sources={r['link_id']:r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    rows=[];print(f'Reintegrating all {len(selected)} affected road cases',flush=True)
    with (output/'node_evaluations.jsonl.gz').open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as stream:
        with ProcessPoolExecutor(max_workers=2,initializer=refiner.initialise,initargs=(str(output),)) as pool:
            for i,row in enumerate(bounded(pool,[(sources[r['link_id']],r['physical_case_id']) for r in selected])):
                for node in row.pop('evaluations'):stream.write((json.dumps(dict(node,link_id=row['link_id'],physical_case_id=row['physical_case_id']),allow_nan=False,separators=(',',':'))+'\n').encode())
                old=selected[i];row['prior_worst_spacing_change_db']=refiner.worst_change(old)
                previous=np.asarray(old['whole_road_relative_energy']);current=row['whole_road_relative_energy']
                row['change_from_prior_fine_db']=None if current is None else {state:{k:None if value is None or receiver_metrics(previous[j])[k] is None else value-receiver_metrics(previous[j])[k] for k,value in receiver_metrics(np.asarray(current)[j]).items()} for j,state in enumerate(('homogeneous','favourable'))}
                rows.append(row);stream.flush()
                (output/'progress.json').write_bytes(json_bytes(dict(completed=len(rows),selected=len(selected),evaluations=sum(r['evaluation_count'] for r in rows),new_evaluations=sum(r['new_evaluation_count'] for r in rows),terminations=dict(Counter(r['termination'] for r in rows)))))
                print(f'{len(rows)}/{len(selected)} {row["link_id"]}: {row["termination"]}; {row["evaluation_count"]} observations, {row["new_evaluation_count"]} new; elapsed {time.perf_counter()-start:.1f}s',flush=True)
    by_key={case_key(r):r for r in rows};ledger=[]
    for old in read(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json')['roads']:
        replacement=by_key.get(case_key(old));row=dict(old)
        if replacement:
            row={k:v for k,v in row.items() if not k.startswith('finer_spacing_')}
            row.update(integration_stage='adaptive_refined',integration_accuracy_outstanding=not replacement['diagnostic_tolerance_met'],
                status=replacement['status'],whole_road_relative_energy=replacement['whole_road_relative_energy'],physics_revision=physics.ENVELOPE_ALGORITHM)
        ledger.append(row)
    (output/'refined_roads.json').write_bytes(json_bytes(dict(roads=rows)));(output/'road_ledger.json').write_bytes(json_bytes(dict(roads=ledger,all_inclusive_noise_db=None)))
    report=dict(selected_road_cases=len(rows),evaluations=sum(r['evaluation_count'] for r in rows),new_evaluations=sum(r['new_evaluation_count'] for r in rows),
        selected_source_length_m=sum(r['source_length_m'] for r in rows),terminations=dict(Counter(r['termination'] for r in rows)),diagnostic_met_cases=sum(r['diagnostic_tolerance_met'] for r in rows),
        new_unknown_cases=sum(r['unknown_positive_sample_observed'] for r in rows),maximum_met_indicator_db=max((r['indicator_db'] for r in rows if r['diagnostic_tolerance_met']),default=None),
        maximum_met_offset_change_db=max((abs(v) for r in rows if r['diagnostic_tolerance_met'] for state in r['offset_change_db'].values() for v in state.values() if v is not None),default=None),
        prior_unselected_case_count=len(ledger)-len(rows),elapsed_seconds=time.perf_counter()-start,all_inclusive_noise_db=None,map_updated=False,field_validation=False)
    (output/'report.json').write_bytes(json_bytes(report));finish(output,hashes,codes,'corrected-road-integration-')

def unique_xy_cache(nodes):
    grouped=defaultdict(list)
    for node in nodes:grouped[tuple(node['xy_bng'])].append(node)
    return {xy:items[0] for xy,items in grouped.items() if len({point_key(n) for n in items})==1}

def cached_finer_rule(source,evaluate,**kwargs):
    case=kwargs['seed_evaluations'][0]['physical_case_id'];key=source['link_id']+'|'+case
    def observe(xy):
        cached=TASK['xy_cache'][key].get(tuple(xy))
        if cached is None:return dict(evaluate(xy),finer_observation_origin='new_native_geographic_evaluation')
        return dict(cached,finer_observation_origin='verified_corrected_observation',finer_cached_source_cell_id=cached['source_cell_id'],finer_cached_fraction=cached['fraction'])
    return checker.finer_rule(source,observe,**kwargs)

def initialise_finer(output):
    refiner.initialise(output);protocol=read(Path(output)/'protocol.json')
    TASK['xy_cache']={k:unique_xy_cache(v) for k,v in read(Path(output)/'seed_evaluations.json').items()}
    rows=read(Path(output)/'parent_roads.json')['roads'];checker.BANK.update({(r['link_id'],r['physical_case_id']):r for r in rows})
    refiner.integrate_adaptive=cached_finer_rule;refiner.geometry_breakpoints=lambda *args,**kw:None

def finer(inputs,output):
    start=time.perf_counter();inputs=Path(inputs).resolve();helper.verified(inputs)
    protocol=read(inputs/'protocol.json');observed=ROOT/'artifacts'/protocol['corrected_observation_directory']
    output,hashes,codes=prepare(output,[inputs,observed]);main=read(inputs/'refined_roads.json')['roads']
    eligible=sorted([r for r in main if r['diagnostic_tolerance_met']]+read(inputs/'control_roads.json')['roads'],key=lambda r:(-r['prior_worst_spacing_change_db'],r['link_id'],r['physical_case_id']))
    assert len({case_key(r) for r in eligible})==len(eligible)
    seeds=read(observed/'seed_evaluations.json');keys={case_key(r) for r in eligible}
    # Seed the observation lookup with new primary observations as well.
    observed_points={k:{point_key(n):n for n in seeds[k]} for k in keys}
    with gzip.open(inputs/'node_evaluations.jsonl.gz','rt') as stream:
        for line in stream:
            n=json.loads(line);key=case_key(n)
            if key in keys:observed_points[key][point_key(n)]=compact_seed(n)
    seeds={k:list(v.values()) for k,v in observed_points.items()}
    (output/'seed_evaluations.json').write_bytes(json_bytes(seeds));(output/'parent_roads.json').write_bytes(json_bytes(dict(roads=eligible)))
    protocol=dict(kind='corrected_road_spacing_check_v1',input_directory=inputs.name,input_inventory_sha256=file_hash(inputs/'inventory.json'),
        corrected_observation_directory=observed.name,parent_inventory_sha256=hashes,controls={},selection='Every newly primary-met affected case, plus the two prior passed controls.',
        selected_identities=[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in eligible],criterion_db=.1,
        method='Four positive equally weighted midpoints on each final adaptive leaf; half accepted midpoint spacing. Exact compatible observations reused; all other points evaluated natively.',
        field_validation=False,all_inclusive_noise_db=None,map_updated=False)
    (output/'protocol.json').write_bytes(json_bytes(protocol));sources={r['link_id']:r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']};rows=[]
    with (output/'node_evaluations.jsonl.gz').open('wb') as raw,gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as stream:
        with ProcessPoolExecutor(max_workers=2,initializer=initialise_finer,initargs=(str(output),)) as pool:
            for row in bounded(pool,[(sources[r['link_id']],r['physical_case_id']) for r in eligible]):
                for node in row.pop('evaluations'):stream.write((json.dumps(dict(node,link_id=row['link_id'],physical_case_id=row['physical_case_id']),allow_nan=False,separators=(',',':'))+'\n').encode())
                rows.append(row);stream.flush();(output/'progress.json').write_bytes(json_bytes(dict(completed=len(rows),selected=len(eligible),passed=sum(r['status']=='finer_spacing_check_passed' for r in rows))))
                print(f'{len(rows)}/{len(eligible)} finer {row["link_id"]}: {row["status"]}; change {row["maximum_absolute_change_db"]}; elapsed {time.perf_counter()-start:.1f}s',flush=True)
    (output/'road_checks.json').write_bytes(json_bytes(dict(roads=rows)))
    (output/'report.json').write_bytes(json_bytes(dict(checked_cases=len(rows),passed_cases=sum(r['status']=='finer_spacing_check_passed' for r in rows),evaluations=sum(r['evaluation_count'] for r in rows),
        maximum_known_change_db=max((r['maximum_absolute_change_db'] for r in rows if r['maximum_absolute_change_db'] is not None),default=None),elapsed_seconds=time.perf_counter()-start,all_inclusive_noise_db=None,map_updated=False,field_validation=False)))
    finish(output,hashes,codes,'corrected-road-spacing-')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--stage',choices=('cache','integrate','finer'),required=True);parser.add_argument('--inputs');parser.add_argument('--review');parser.add_argument('--resume-observations');parser.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.stage=='cache':cache(args.output,args.resume_observations)
    elif args.stage=='integrate':integrate(args.inputs,args.review,args.output)
    else:finer(args.inputs,args.output)
