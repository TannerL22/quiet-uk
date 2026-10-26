"""Independent old-record, source-air and native replay audit for corrected reuse.

Imports the prior independent equations, not the integration/cache controller.
Old/new chain replay is explicitly a compatibility check, not field validation.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk import favourable_propagation as physics
from quiet_uk.conditional_scene_domain import execution_policy
from quiet_uk.physical_scene_assumptions import assumption_cases
from quiet_uk.sightline_boundary import physical_edge_candidates,validated_roofs
from quiet_uk.terrain_profile import linear_segments
from quiet_uk.propagation_geometry_cache import geometry_cache

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
prior=module('corrected_source_air_reference',ROOT/'scripts/90_verify_conditional_road_integration.py')
circle=module('independent_corrected_circle_reference',ROOT/'scripts/99_verify_curved_envelope_fix.py')
helper=prior.helper
def read(path):return json.loads(Path(path).read_bytes())
def key(r):return r['link_id']+'|'+r['physical_case_id']
def identity(r):return (key(r),r['source_cell_id'],r['fraction'])
def digest(r):return hashlib.sha256(json_bytes(r)).hexdigest()
def array_close(a,b):assert np.allclose(a,b,rtol=3e-10,atol=1e-280)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace audit required')
    helper.verified(inputs);input_hash=file_hash(inputs/'inventory.json');protocol=read(inputs/'protocol.json')
    parents={n:ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==protocol['parent_inventory_sha256'][n]
    witnesses=read(parents['curved_envelope_witnesses_v1']/'witnesses.json')['witnesses'];affected={key(r) for r in witnesses}
    controls={key(r) for r in read(parents['adaptive_road_spacing_check_v1']/'road_checks.json')['roads'] if r['status']=='finer_spacing_check_passed'}
    assert set(protocol['affected_identities'])==affected and set(protocol['control_identities'])==controls and len(affected)==59 and len(controls)==2
    allowed=affected|controls;original=parents['adaptive_road_integration_v3']/'code'
    frozen=module('original_corrected_reuse_physics',original/'favourable_propagation.py')
    bridge=parents['curved_envelope_fix_checks_v1'];validation=read(ROOT/'artifacts/curved_envelope_validation_v1.json')
    assert file_hash(ROOT/'artifacts/curved_envelope_validation_v1.json')==protocol['model_validation_sha256']
    assert file_hash(bridge/'inventory.json')==validation['review_inventory_sha256']
    for path in (bridge/'code').glob('*.py'):
        if path.name[:2].isdigit():continue
        assert file_hash(path)==file_hash(inputs/'code'/path.name)
    for path in original.glob('*.py'):
        if path.name=='favourable_propagation.py':continue
        assert file_hash(path)==file_hash(inputs/'code'/path.name)
    expected={};counts=Counter()
    # First occurrence and exact identity match the declared deterministic stream
    # order. Hash the entire original record; no profile field is silently lost.
    for definition in protocol['observation_streams']:
        directory=parents[definition['directory']]
        with gzip.open(directory/definition['file'],'rt') as stream:
            for line in stream:
                node=json.loads(line)
                if key(node) not in allowed:continue
                ident=identity(node)
                signature=digest({k:node.get(k) for k in ('status','xy_bng','native_profile_sha256','relative_transfer','joint_hourly_air_source_power_per_m','relative_energy_per_m','reasons')})
                if ident in expected:
                    assert signature==expected[ident]['physics_signature'],'Contradictory duplicate history'
                    counts['duplicate_physics_records_checked']+=1;continue
                expected[ident]=dict(status=node['status'],directory=directory.name,node_id=node['node_id'],record_sha256=digest(node),native_profile_sha256=node['native_profile_sha256'],
                    physics_signature=signature,original_reasons=node.get('reasons'),kernel_scene_sha256=digest(node['candidate_kernel_scene']) if 'candidate_kernel_scene' in node else None)
                counts['distinct_old_observations']+=1
        print(f'{directory.name}: original identities indexed',flush=True)
    seeds=read(inputs/'seed_evaluations.json');assert set(seeds)==allowed
    assert all(len(v)<=2048 for v in seeds.values())
    positions={k:0 for k in seeds};seen=set();verified_states={};samples=[];reasons=Counter()
    roads={r['link_id']:r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coeff=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    calendar=read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    groups=read(inputs/'air_tables.json')['groups'];powers={link:prior.group_source_power(roads[link],coeff,calendar,groups) for link in {r.split('|')[0] for r in allowed}}
    for name in ('air_tables.json','air_tables.npz'):assert file_hash(inputs/name)==file_hash(parents['adaptive_road_integration_v3']/name)
    with np.load(inputs/'air_tables.npz',allow_pickle=False) as a:air={k:a[k] for k in a.files}
    cases={c['case_id']:c for c in assumption_cases()}
    with gzip.open(inputs/'point_observations.jsonl.gz','rt') as stream:
        for ni,line in enumerate(stream):
            node=json.loads(line);ident=identity(node);assert ident in expected and ident not in seen;seen.add(ident)
            verified_states[ident]=node['status']
            assert node['cache_record_id']==ni and node['cache_generation_directory']==inputs.name
            expected_seed={k:v for k,v in node.items() if k!='candidate_kernel_scene'}
            assert seeds[key(node)][positions[key(node)]]==expected_seed;positions[key(node)]+=1
            old=expected[ident];claim=node['model_reuse'];assert claim['prior_directory']==old['directory'] and claim['prior_node_id']==old['node_id']
            assert node['native_profile_sha256']==old['native_profile_sha256']
            if old['status']=='conditional_research':
                assert claim['kind']=='numeric_same_chain_verified' and node['status']=='conditional_research'
                unwrapped={k:v for k,v in node.items() if k not in ('model_reuse','cache_record_id','cache_generation_directory')}
                assert digest(unwrapped)==old['record_sha256']
                scene=node['candidate_kernel_scene'];candidates=physical_edge_candidates(scene['points'],validated_roofs(linear_segments(scene['points']),scene['obstacles']))
                s,r=scene['source'],scene['receiver'];radius=physics.ray_radius(s,r)
                assert physics.circular_envelope(candidates,s,r,radius)==frozen.circular_envelope(candidates,s,r,radius)
                counts['numeric_record_and_chain_compatibility_checks']+=1
            else:
                assert claim['kind']=='native_reevaluation_of_historical_unknown' and claim['prior_reasons']==old['original_reasons']
                if old['kernel_scene_sha256'] is not None:assert digest(node['candidate_kernel_scene'])==old['kernel_scene_sha256']
                counts['historical_unknowns_reevaluated']+=1
                if node['status']=='conditional_research':
                    scene=node['candidate_kernel_scene'];candidates=physical_edge_candidates(scene['points'],validated_roofs(linear_segments(scene['points']),scene['obstacles']))
                    independent=circle.independent_envelope(candidates,scene['source'],scene['receiver'],physics.ray_radius(scene['source'],scene['receiver']),reduce=True)
                    assert independent['path']==physics.circular_envelope(candidates,scene['source'],scene['receiver'],physics.ray_radius(scene['source'],scene['receiver']))
                    counts['independent_recovered_envelopes_checked']+=1
            if node['status']=='withheld':
                assert node.get('relative_energy_per_m') is None;reasons.update(node['reasons']);counts['remaining_unknown_observations']+=1
            else:
                assert node['status']=='conditional_research' and node['input_scene_kind']=='conditional_real_input'
                if node['model_reuse']['kind']=='native_reevaluation_of_historical_unknown':counts['recovered_unknown_observations']+=1
                expected_power=np.zeros((3,8));distance=node['direct_distance_m']
                for pi,p in enumerate(prior.PERIODS):
                    for bi,f in enumerate(prior.BANDS):
                        expected_power[pi,bi]=math.fsum(powers[node['link_id']][g,pi,bi]*np.interp(distance,air[f'distance_{f}'],air[f'{p}_{f}'][g]) for g in range(len(groups)))
                array_close(expected_power,node['joint_hourly_air_source_power_per_m']);array_close(np.asarray(node['relative_transfer'])[:,None,:]*expected_power[None,:,:],node['relative_energy_per_m'])
                counts['independent_source_air_band_values']+=24;counts['point_energy_values_checked']+=48
                category=(old['status'],old['directory']);existing={(expected[identity(n)]['status'],expected[identity(n)]['directory']) for n in samples}
                new_road=node['link_id'] not in {n['link_id'] for n in samples}
                if len(samples)<12 and (category not in existing or (new_road and (len(samples)<6 or old['status']=='withheld'))):samples.append(node)
            counts['cache_points_checked']+=1
            if counts['cache_points_checked']%5000==0:print(f'{counts["cache_points_checked"]} corrected observations accounted for',flush=True)
    assert seen==set(expected) and all(positions[k]==len(v) for k,v in seeds.items())
    # Mandatory recorded failures are a subset of all retained historical failures.
    assert all((key(n),n['source_cell_id'],n['fraction']) in seen for n in witnesses)
    assert all(verified_states[identity(n)]=='conditional_research' for n in witnesses),'Mandatory repaired witnesses remain unsupported'
    builder=prior.builder_module.load_builder(parents['physical_scene_inputs_v2'],parents['ground_cover_inputs_v3'],maximum_reflections=0)
    features=read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    for node in samples:
        prior.native_replay(node,builder,features,cases[node['physical_case_id']],[507000.,179000.],counts)
        scene=dict(node['candidate_kernel_scene'],conditional_input_policy=execution_policy(cases[node['physical_case_id']]))
        with geometry_cache():
            for bi,f in enumerate(prior.BANDS):
                value=physics.direct_attenuation(scene,f,circle.AIR,absorption_frequency_policy='exact_base10');assert value['status']=='conditional_research'
                for si,state in enumerate(('homogeneous','favourable')):
                    expected_transfer=10**(-(value[state+'_attenuation_db']-value['atmospheric_attenuation_db'])/10)
                    assert math.isclose(expected_transfer,node['relative_transfer'][si][bi],rel_tol=3e-11,abs_tol=1e-280)
                counts['sampled_current_kernel_bands_replayed']+=1
    report=read(inputs/'report.json');assert report['observations']==counts['cache_points_checked']
    assert report['counts']['compatible_numeric_observations']==counts['numeric_record_and_chain_compatibility_checks']
    assert report['counts']['historical_unknowns_reevaluated']==counts['historical_unknowns_reevaluated']
    output.mkdir(parents=True);shutil.copyfile(__file__,output/Path(__file__).name)
    (output/'report.json').write_bytes(json_bytes(dict(status='passed_corrected_observation_accounting',input_inventory_sha256=input_hash,
        input_generation=read(inputs/'inventory.json')['generation_id'],counts=counts,remaining_reason_counts=reasons,
        scope='Exact original record identity, every known old/new chain, every unique historical unknown replacement, compact-seed parity, independent source-air products; independent recovered envelopes and sampled native/current-kernel parity.',
        field_validation=False,all_inclusive_noise_db=None,map_updated=False)))
    (output/'sampled_points.json').write_bytes(json_bytes([dict(cache_record_id=n['cache_record_id'],link_id=n['link_id'],physical_case_id=n['physical_case_id']) for n in samples]))
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==input_hash
    print(json.dumps(dict(generation_id=helper.seal(output,'corrected-observation-review-'),counts=counts)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
