"""Offline, direct-only full retained-road quadrature with explicit omissions.

No map publication. Every input generation is verified and immutable; fresh outputs
only. The two-worker option is local numerical execution, not a service or agent.
"""
import argparse
from collections import Counter,deque
from concurrent.futures import ProcessPoolExecutor
import csv
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.conditional_road_propagation import (A_WEIGHT_DB,PERIODS,build_air_tables,
    direct_transfer,integrate_source_cells,receiver_metrics,road_guard,source_joint_power)
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.physical_scene_assumptions import assumption_cases,materialize_profile
from quiet_uk.propagation_canary import BANDS


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


helper=module('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
builder_module=module('real_scene_runner',ROOT/'scripts/81_build_real_road_scenes.py')
PARENTS=('physical_scene_inputs_v2','ground_cover_inputs_v3','road_direction_inputs_v2',
    'road_emission_assumptions_v2','real_road_scenes_v2','road_operating_inputs_v2',
    'physical_scene_assumptions_v2','physical_scene_assumption_review_v1')
RECEIVER=[507000.,179000.]
CASES=[c for c in assumption_cases() if c['case_id'] in ('baseline','combined_reference')]
STATE={}


def read(path):return json.loads(Path(path).read_bytes())


def initialise(output):
    p=ROOT/'artifacts';start=time.perf_counter()
    STATE.update(builder=builder_module.load_builder(p/'physical_scene_inputs_v2',p/'ground_cover_inputs_v3',maximum_reflections=0),
        features=read(p/'physical_scene_assumptions_v2/physical_features.json')['features'],
        policies=read(p/'physical_scene_assumptions_v2/source_physical_inputs.json')['roads'],
        roads={r['link_id']:r for r in read(p/'road_operating_inputs_v2/road_operating_inputs.json')['roads']},
        calendar=read(p/'road_operating_inputs_v2/traffic_weather_calendars.json')['calendars']['national_vehicle'],
        coefficients=read(p/'road_emission_assumptions_v2/published_2021_coefficients.json'))
    air=read(Path(output)/'air_tables.json');data=np.load(Path(output)/'air_tables.npz',allow_pickle=False)
    STATE['air']=dict(maximum_distance_m=air['maximum_distance_m'],tables={
        (period,f):dict(distance_m=data[f'distance_{f}'],groups=air['groups'],transmission=data[f'{period}_{f}']) for period in PERIODS for f in BANDS})
    print(f'Worker {os.getpid()} ready in {time.perf_counter()-start:.1f}s',flush=True)


def compute_source(source):
    s=STATE;link=source['link_id'];road=s['roads'][link];policy=s['policies'][link]['physical_source']
    guards={c['case_id']:road_guard(source,s['builder'],s['features'],c,policy,RECEIVER) for c in CASES}
    # Baseline normally fails the conservative fan guard. Reuse native preparations
    # for both cases at the same node, but never reuse different materialized physics.
    prepared={};result=[]
    for case in CASES:
        guard=guards[case['case_id']]
        def evaluate(xy):
            if guard:return dict(status='withheld',reasons=guard,whole_source_guard=True)
            key=tuple(xy)
            if key not in prepared:prepared[key]=s['builder'].prepare_point(xy,RECEIVER,reflections=False)
            p=prepared[key]
            candidate=materialize_profile(p['direct'],s['features'],case,source_policy=policy,endpoint_ids=p['endpoint_footprint_ids'])
            transfer=direct_transfer(candidate,case,retain_kernel_scene=True)
            metadata={k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in transfer.items()}
            metadata.update(native_profile_sha256=hashlib.sha256(json_bytes(p['direct'])).hexdigest(),
                omitted_screen_feature_ids=candidate['omitted_screen_feature_ids'],
                native_terrain_intervals=len(p['direct']['terrain_native_intervals']),
                native_ground_runs=len(p['direct']['ground_native_samples']))
            if transfer['status']=='withheld':return metadata
            power=source_joint_power(road,s['coefficients'],s['calendar'],s['air'],transfer['direct_distance_m'])
            if power is None:return dict(status='withheld',reasons=['missing_or_unsupported_joint_source_air'],**{k:v for k,v in metadata.items() if k!='status'})
            return dict(metadata,joint_hourly_air_source_power_per_m=power.tolist(),
                relative_energy_per_m=transfer['relative_transfer'][:,None,:]*power[None,:,:])
        integrated=integrate_source_cells(source,RECEIVER,evaluate)
        whole=integrated['whole_road_relative_energy'];known=np.asarray(integrated['known_evaluated_relative_energy'])
        mixtures={str(p):dict(known_evaluated_component_db=receiver_metrics((1-p)*known[0]+p*known[1]),
            whole_road_direct_component_db=None if whole is None else receiver_metrics((1-p)*np.asarray(whole)[0]+p*np.asarray(whole)[1])) for p in (0.,.5,1.)}
        result.append(dict(link_id=link,group_id=source['group_id'],physical_case_id=case['case_id'],
            whole_source_guard_reasons=guard,source_emission_reference='road_operating_inputs_v2 conditional_baseline',
            assumed_favourable_probability_results=mixtures,observed_favourable_probability=None,**integrated))
    return result


def bounded_results(pool,sources):
    """At most four submitted roads; ordered output and bounded result memory.

    Avoid eagerly submitting every geographic road and buffering all receipts.
    Works on Python 3.12 as well as 3.14 without map's new buffersize argument.
    """
    inputs=iter(sources);pending=deque()
    for _ in range(4):
        source=next(inputs,None)
        if source is not None:pending.append(pool.submit(compute_source,source))
    while pending:
        result=pending.popleft().result()
        source=next(inputs,None)
        if source is not None:pending.append(pool.submit(compute_source,source))
        yield result


def node_record(link_id,case_id,evaluation):
    if evaluation.get('physical_case_id',case_id)!=case_id:raise ValueError('Node belongs to another physical case')
    return dict(evaluation,link_id=link_id,physical_case_id=case_id)


def run(output,workers):
    output=Path(output).resolve();start=time.perf_counter()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use a fresh workspace output')
    if workers not in (1,2):raise ValueError('Local worker budget is one or two')
    code_paths=[Path(__file__),*(ROOT/'src/quiet_uk').glob('*.py'),ROOT/'scripts/57_acquire_ground_cover_sources.py',ROOT/'scripts/81_build_real_road_scenes.py']
    start_code_hashes={p:file_hash(p) for p in code_paths}
    parents={n:ROOT/'artifacts'/n for n in PARENTS}
    for p in parents.values():helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    sources=read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']
    if len(sources)!=722 or len({s['link_id'] for s in sources})!=722:raise ValueError('Incomplete or duplicate retained road budgets')
    output.mkdir(parents=True)
    weather=read(parents['road_operating_inputs_v2']/'hourly_weather.json')['rows']
    calendar=read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    air=build_air_tables(weather,calendar);data={'alpha_db_per_km':air['alpha_db_per_km']}
    for (period,f),table in air['tables'].items():data[f'{period}_{f}']=table['transmission'];data[f'distance_{f}']=table['distance_m']
    np.savez_compressed(output/'air_tables.npz',**data)
    (output/'air_tables.json').write_bytes(json_bytes(dict(groups=air['tables'][('day',63)]['groups'],
        maximum_distance_m=air['maximum_distance_m'],maximum_interpolation_db=air['maximum_interpolation_db'],
        interpolation_bounds=air['interpolation_bounds'],basis='Traffic-group/period joint hourly transmission; positive normalised weights, candidate missing air retained.',
        absorption_frequency_policy='exact_base10',station_to_path_uniform_air_assumed=True)))
    protocol=dict(parent_inventory_sha256=hashes,receiver_xy_bng=RECEIVER,receiver_height_agl_m=4.,source_height_agl_m=.05,
        physical_cases=CASES,operating_case='conditional_baseline',reference_year=2021,
        surface='Reference road surface',source_temperature_c=20.,actual_source_temperature_c=None,
        slope_and_junction_emission_correction_db=0.,actual_slope_and_junction_correction_db=None,
        quadrature='Composite midpoint over every original <=25m cell, then two equal subdivisions; all turns retained; horizontal length weighting.',
        subdivisions=[1,2],quadrature_convergence_proven=False,path_transition_partition_complete=False,
        terrain_reconstruction='Native containing-cell midpoint polyline then max 0.1m vertical-residual simplification. Native inputs unchanged. Geometry bound only.',
        maximum_vertical_reconstruction_error_m=.1,terrain_noise_error_bound_db=None,
        whole_source_guards=['Unresolved source deck context','Positive source length in building footprint','Unknown candidate height anywhere in convex source-receiver fan'],
        air_scope='Uniform along each path using Heathrow hourly candidate tuples; unknown atmospheric gradients, long-term directional favourable probability.',
        favourable_probabilities=[0.,.5,1.],favourable_probability_observed=None,
        octave_bands_hz=list(BANDS),a_weight_db=A_WEIGHT_DB.tolist(),
        a_weight_source='https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32021L1226 section 2.5.12',
        scope='direct_only_reflections_omitted',omitted_paths=['specular_reflections','lateral_diffraction','transmission'],
        unknown_network_sources=13206,outside_halo_road_contributions=None,nonroad_noise=None,
        full_road_all_paths_noise_db=None,all_inclusive_noise_db=None,real_area_validated=False,absolute_release_eligible=False,
        worker_budget=workers,runtime=dict(python=platform.python_version(),numpy=np.__version__,platform=platform.platform()))
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    print('Air tables complete; starting all 722 retained roads',flush=True)
    totals={c['case_id']:dict(status_counts=Counter(),reasons=Counter(),known_energy=np.zeros((2,3,8)),complete_roads=0,held_roads=0,
        spacing_changes=[],source_length_m=0.,known_length_m=0.,unknown_length_m=0.) for c in CASES}
    roads=[]
    with (output/'node_evaluations.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
            def consume(results):
                for i,records in enumerate(results):
                    for r in records:
                        evaluations=r.pop('evaluations')
                        for e in evaluations:stream.write((json.dumps(node_record(r['link_id'],r['physical_case_id'],e),allow_nan=False,separators=(',',':'))+'\n').encode())
                        roads.append(r);t=totals[r['physical_case_id']];t['status_counts'][r['status']]+=1;t['reasons'].update(r['reason_counts'])
                        t['known_energy']+=np.asarray(r['known_evaluated_relative_energy']);t['source_length_m']+=r['source_length_m']
                        fine=r['quadrature_history'][-1];t['known_length_m']+=fine['known_source_length_m'];t['unknown_length_m']+=fine['unknown_source_length_m']
                        t['complete_roads']+=int(r['whole_road_relative_energy'] is not None);t['held_roads']+=int(r['whole_road_relative_energy'] is None)
                        if r['spacing_change_db']:t['spacing_changes'].extend(abs(v) for state in r['spacing_change_db'].values() for v in state.values() if v is not None)
                    if i<3 or (i+1)%25==0 or i+1==722:
                        stream.flush();print(f'Integrated {i+1}/722 roads; elapsed {time.perf_counter()-start:.1f}s',flush=True)
            if workers==1:initialise(output);consume(map(compute_source,sources))
            else:
                with ProcessPoolExecutor(max_workers=workers,initializer=initialise,initargs=(str(output),)) as pool:consume(bounded_results(pool,sources))
    summaries={}
    for case,t in totals.items():
        energy=t.pop('known_energy');changes=t.pop('spacing_changes')
        summaries[case]=dict(t,known_evaluated_relative_energy=energy.tolist(),known_evaluated_partial_direct_component_db={str(p):receiver_metrics((1-p)*energy[0]+p*energy[1]) for p in (0.,.5,1.)},
            maximum_road_spacing_change_db=max(changes) if changes else None,road_spacing_change_p95_db=float(np.quantile(changes,.95)) if changes else None,
            complete_road_network_noise_db=None,full_road_all_paths_noise_db=None,all_inclusive_noise_db=None)
    (output/'road_contributions.json').write_bytes(json_bytes(dict(roads=roads,case_summaries=summaries,
        scope='direct_only_reflections_omitted',complete_physical_path_support=False,real_area_validated=False,combined_road_noise_db=None,all_inclusive_noise_db=None)))
    with (output/'road_contributions.csv').open('w',newline='',encoding='utf-8') as stream:
        writer=csv.DictWriter(stream,fieldnames=['link_id','physical_case_id','status','source_length_m','known_source_length_m','unknown_source_length_m','whole_direct_p05_lden_db','known_evaluated_direct_p05_lden_db','reasons']);writer.writeheader()
        for r in roads:
            fine=r['quadrature_history'][-1];p=r['assumed_favourable_probability_results']['0.5']
            writer.writerow(dict(link_id=r['link_id'],physical_case_id=r['physical_case_id'],status=r['status'],source_length_m=r['source_length_m'],known_source_length_m=fine['known_source_length_m'],unknown_source_length_m=fine['unknown_source_length_m'],
                whole_direct_p05_lden_db=p['whole_road_direct_component_db']['den'] if p['whole_road_direct_component_db'] else None,
                known_evaluated_direct_p05_lden_db=p['known_evaluated_component_db']['den'],reasons=';'.join(r['reason_counts'])))
    (output/'report.json').write_bytes(json_bytes(dict(parent_inventory_sha256=hashes,cases=summaries,
        elapsed_seconds=time.perf_counter()-start,source_roads=len(sources),source_cells=sum(len(s['source_cells']) for s in sources),
        node_evaluations=2*3*sum(len(s['source_cells']) for s in sources),qualified_noise_predictions=False,map_updated=False)))
    code=output/'code';code.mkdir();shutil.copyfile(__file__,code/Path(__file__).name)
    if any(file_hash(p)!=h for p,h in start_code_hashes.items()):raise ValueError('Executed source changed during run; do not seal')
    for path in sorted((ROOT/'src/quiet_uk').glob('*.py')):shutil.copyfile(path,code/path.name)
    for n in ('57_acquire_ground_cover_sources.py','81_build_real_road_scenes.py'):shutil.copyfile(ROOT/'scripts'/n,code/n)
    (output/'provenance.json').write_bytes(json_bytes(dict(parent_inventory_sha256=hashes,
        attribution='Parent generations retain Environment Agency LiDAR/OGL, OS OpenMap Local/OGL, DfT/OGL, ESA WorldCover/CC BY 4.0, OpenStreetMap/ODbL, NOAA/ISD and published EU coefficient provenance. Preserve parent source-specific licence and attribution manifests.',
        code_sha256={p.name:file_hash(p) for p in sorted(code.glob('*.py'))})))
    for n,p in parents.items():
        helper.verified(p)
        if file_hash(p/'inventory.json')!=hashes[n]:raise ValueError('Frozen parent changed')
    identity=helper.seal(output,'conditional-road-integration-')
    print(json.dumps(dict(generation_id=identity,cases={c:{k:v for k,v in t.items() if k not in ('known_evaluated_relative_energy',)} for c,t in summaries.items()})),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=str(ROOT/'artifacts/conditional_road_integration_v1'));parser.add_argument('--workers',type=int,default=2)
    args=parser.parse_args();run(args.output,args.workers)
