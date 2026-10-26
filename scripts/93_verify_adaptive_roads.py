"""Independent adaptive quadrature accounting and sampled frozen GIS/kernel replay.

Does not import the adaptive integrator. The frozen acoustic replay checks parity,
not field accuracy. Error indicators are not promoted to rigorous error bounds.
"""
import argparse
from collections import Counter,defaultdict
import gzip
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import types
import numpy as np
from shapely.geometry import LineString,Point,MultiPoint

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
prior=module('prior_independent_road_review',ROOT/'scripts/90_verify_conditional_road_integration.py')
helper=prior.helper
AW=np.power(10.,np.asarray(prior.AW)/10)

def read(path):return json.loads(Path(path).read_bytes())
def close(a,b):prior.close(a,b)
def array_close(a,b):assert np.allclose(a,b,rtol=3e-10,atol=1e-280)
def components(e):
    x=(np.asarray(e)*AW).sum(axis=-1)
    return np.column_stack((x,(12*x[:,0]+4*math.sqrt(10)*x[:,1]+80*x[:,2])/24))
def indicator(total,error):
    t,e=components(total),components(error)
    if np.any((t==0)&(e>0)) or np.any((t>0)&(e>=t)):return None
    ratios=np.divide(e,t,out=np.zeros_like(e),where=t>0)
    return float(np.max(-10*np.log10(1-ratios)))
def worst(row):return max(abs(v) for state in row['spacing_change_db'].values() for v in state.values() if v is not None) if row['spacing_change_db'] else None

def vertices(g):
    if g.geom_type=='Polygon':
        return [*g.exterior.coords,*[p for ring in g.interiors for p in ring.coords]]
    if g.geom_type=='LineString':return list(g.coords)
    if hasattr(g,'geoms'):return [p for child in g.geoms for p in vertices(child)]
    return []

def partition_check(source,row,builder,receiver,counts):
    for cell in source['source_cells']:
        a,b=cell['start_xy_bng'],cell['end_xy_bng'];line=LineString([a,b]);length=line.length
        # Native source-line traversal independently provides grid transitions.
        expected=[0.,1.,*[v[1]/length for v in builder.terrain.intervals(a,b)[:-1]]]
        nearest=line.project(Point(receiver))/length
        if 1e-11<nearest<1-1e-11:expected.append(nearest)
        fan=MultiPoint([a,b,receiver]).convex_hull
        for tree,records in ((builder.building_tree,builder.buildings),(builder.barrier_tree,builder.barriers)):
            for i in tree.query(fan,predicate='intersects'):
                for vertex in vertices(records[int(i)]['geometry']):
                    distance=math.dist(vertex,receiver)
                    if distance==0:continue
                    extension=2*max(math.dist(receiver,a),math.dist(receiver,b))/distance+2
                    end=[r+(v-r)*extension for r,v in zip(receiver,vertex)]
                    ray=LineString([receiver,end]);hit=ray.intersection(line)
                    points=[hit] if hit.geom_type=='Point' else list(hit.geoms) if hit.geom_type=='MultiPoint' else []
                    for point in points:
                        t=line.project(point)/length
                        if ray.project(point)>=distance-1e-8 and 1e-11<t<1-1e-11:expected.append(t)
        actual=[v['fraction'] for v in row['partition']['cells'][cell['segment_id']]]
        assert all(any(abs(v-u)<1e-8 for u in actual) for v in expected),(cell['segment_id'],'missing partition')
        assert all(any(abs(v-u)<1e-8 for u in expected) for v in actual),(cell['segment_id'],'unexpected partition')
        counts['independently_reconstructed_partition_cells']+=1

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace review required')
    helper.verified(inputs); input_hash=file_hash(inputs/'inventory.json')
    protocol=read(inputs/'protocol.json');parents={n:ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==protocol['parent_inventory_sha256'][n]
    corrected=protocol.get('kind')=='corrected_road_integration_v1'
    budget=protocol.get('kind')=='budget_road_integration_v1'
    budget_verifier=None
    review_hashes={}
    if budget:
        budget_verifier=module('budget_population_review',ROOT/'scripts/110_verify_budget_road_observations.py')
        budget_verifier.verified_inputs(inputs)
        observed=parents[protocol['budget_observation_directory']]
        observed_review=parents[protocol['budget_observation_review_directory']]
        observed_report=read(observed_review/'report.json')
        assert observed_report['status']=='passed_independent_budget_observation_review'
        assert observed_report['input_inventory_sha256']==file_hash(observed/'inventory.json')
        review_paths=[Path(__file__),ROOT/'scripts/110_verify_budget_road_observations.py',*[ROOT/'scripts'/n for n in ('90_verify_conditional_road_integration.py','86_verify_road_operating_inputs.py','88_verify_physical_scene_assumptions.py','81_build_real_road_scenes.py','57_acquire_ground_cover_sources.py')],*(ROOT/'src/quiet_uk').glob('*.py')]
        review_hashes={path:file_hash(path) for path in review_paths}
    corrected_cache=None
    if corrected:
        corrected_cache=parents[protocol['corrected_observation_directory']]
        corrected_audit=parents[protocol['corrected_observation_review_directory']]
        assert read(corrected_audit/'report.json')['status']=='passed_corrected_observation_accounting'
        assert read(corrected_audit/'report.json')['input_inventory_sha256']==file_hash(corrected_cache/'inventory.json')
    if budget:
        original=ROOT/'artifacts/conditional_road_integration_v4';helper.verified(original)
        assert file_hash(original/'inventory.json')==read(parents['adaptive_road_integration_v3']/'protocol.json')['parent_inventory_sha256'][original.name]
    else:original=parents['conditional_road_integration_v4']
    original_rows=read(original/'road_contributions.json')['roads']
    for name in ('air_tables.json','air_tables.npz'):assert file_hash(inputs/name)==file_hash(original/name)
    for path in (original/'code').glob('*.py'):
        expected=corrected_cache/'code'/path.name if corrected and path.name=='favourable_propagation.py' else path
        if budget and path.name in ('favourable_propagation.py','terrain_boundary.py'):
            expected=ROOT/'artifacts/ground_average_replay_v1/code'/path.name
        assert file_hash(expected)==file_hash(inputs/'code'/path.name),'Unauthorised executed physics change: '+path.name
    rows=read(inputs/'refined_roads.json')['roads'];counts=Counter();report=read(inputs/'report.json')
    config=protocol['controls']
    if budget:
        held=[r for name in ('adaptive_road_integration_v3','corrected_road_integration_v1') for r in read(parents[name]/'refined_roads.json')['roads'] if r['termination']=='initial_partition_budget_exhausted']
        held.sort(key=lambda r:(r['minimum_initial_observations'],r['link_id']+'|'+r['physical_case_id']))
        identities=[r['link_id']+'|'+r['physical_case_id'] for r in held]
        assert len(set(identities))==len(identities)==24
        assert identities==[r['link_id']+'|'+r['physical_case_id'] for r in protocol['selected_identities']]
        originals={r['link_id']+'|'+r['physical_case_id']:r for r in original_rows}
        selected=[originals[k] for k in identities]
        observed_plan=read(observed/'protocol.json')['selected_identities']
        assert protocol['selected_identities']==observed_plan
        assert protocol['case_controls']=={r['link_id']+'|'+r['physical_case_id']:r['controls'] for r in observed_plan}
    elif corrected:
        witness_parent=parents['curved_envelope_witnesses_v1']
        affected={r['link_id']+'|'+r['physical_case_id'] for r in read(witness_parent/'witnesses.json')['witnesses']}
        assert affected==set(read(corrected_cache/'protocol.json')['affected_identities']) and len(affected)==59
        selected=sorted([r for r in original_rows if r['link_id']+'|'+r['physical_case_id'] in affected],key=lambda r:(-worst(r),r['link_id'],r['physical_case_id']))
    else:
        selected=sorted([r for r in original_rows if worst(r) is not None and worst(r)>protocol['selection']['prior_absolute_spacing_change_greater_than_db']],key=lambda r:(-worst(r),r['link_id'],r['physical_case_id']))
        if protocol['selection']['maximum_cases'] is not None:selected=selected[:protocol['selection']['maximum_cases']]
    keys=[(r['link_id'],r['physical_case_id']) for r in rows]
    assert keys==[(r['link_id'],r['physical_case_id']) for r in selected]
    source_map={r['link_id']:r for r in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']}
    roads={r['link_id']:r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    calendar=read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    coeff=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json');groups=read(inputs/'air_tables.json')['groups']
    with np.load(inputs/'air_tables.npz',allow_pickle=False) as archive:air={key:archive[key] for key in archive.files}
    powers={link:prior.group_source_power(roads[link],coeff,calendar,groups) for link in {r['link_id'] for r in rows}}
    seeds=read(inputs/'seed_evaluations.json');expected_seeds={k:[] for k in seeds}
    if budget:
        expected_seeds=read(observed/'seed_evaluations.json')
        counts['verified_native_replayed_budget_seed_observations']=sum(map(len,expected_seeds.values()))
    elif corrected:
        validated_seeds=read(corrected_cache/'seed_evaluations.json')
        expected_seeds={k:validated_seeds[k] for k in seeds}
        counts['verified_corrected_cache_seed_observations']=sum(map(len,expected_seeds.values()))
    else:
        with gzip.open(original/'node_evaluations.jsonl.gz','rt') as stream:
            for line in stream:
                node=json.loads(line);key=node['link_id']+'|'+node['physical_case_id']
                if key in seeds:
                    node['fraction']=(node['subdivision']+.5)/node['subdivisions']
                    expected_seeds[key].append(node);counts['verified_original_grid_observations']+=1
    reused=None if corrected or budget else protocol.get('reused_observation_directory')
    if reused:
        observed=parents[reused];receipt=read(observed/'receipt.json')
        assert all(protocol['parent_inventory_sha256'][n]==sha for n,sha in receipt['parent_inventory_sha256'].items())
        for path in (observed/'code').glob('*.py'):
            if path.name in ('92_refine_conditional_roads.py','97_capture_development_road_observations.py','adaptive_road_integration.py'):continue
            assert file_hash(path)==file_hash(inputs/'code'/path.name)
        seen={k:{(n['source_cell_id'],n['fraction']) for n in nodes} for k,nodes in expected_seeds.items()}
        def retain(key,nodes):
            if key not in expected_seeds:return
            unknown=[n for n in nodes if n['status']=='withheld']
            for node in unknown[:1] if unknown else nodes:
                identity=(node['source_cell_id'],node['fraction'])
                if identity not in seen[key]:expected_seeds[key].append(node);seen[key].add(identity);counts['verified_reused_development_observations']+=1
        with gzip.open(observed/'point_evaluations.jsonl.gz','rt') as stream:
            current=None;cached=[]
            for line in stream:
                node=json.loads(line);key=node['link_id']+'|'+node['physical_case_id']
                if current is not None and key!=current:retain(current,cached);cached=[]
                current=key;cached.append(node)
            if current is not None:retain(current,cached)
    assert expected_seeds==seeds
    counts['verified_prior_seed_observations']=sum(len(v) for v in seeds.values())
    samples=[];all_reasons=Counter()
    with gzip.open(inputs/'node_evaluations.jsonl.gz','rt') as stream:
        for row,old in zip(rows,selected):
            key=(row['link_id'],row['physical_case_id']);link,case=key;source=source_map[link]
            if budget:config=protocol['case_controls'][link+'|'+case]
            cells={c['segment_id']:c for c in source['source_cells']};observations=[];seen=set();reasons=Counter();sampled=False
            row_seeds=seeds[link+'|'+case]
            scene_path='point_scenes/'+link+'__'+case+'.jsonl.gz'
            scene_stream=gzip.open(inputs/scene_path,'rt',encoding='utf-8') if budget else None
            for ni in range(row['evaluation_count']):
                node=json.loads(next(stream));assert (node['link_id'],node['physical_case_id'])==key and node['node_id']==ni
                cell=cells[node['source_cell_id']];t=node['fraction'];assert 0<t<1
                identity=(node['source_cell_id'],t);assert identity not in seen;seen.add(identity)
                xy=[a+(b-a)*t for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])];assert node['xy_bng']==xy
                if ni<len(row_seeds):
                    original_node=row_seeds[ni]
                    assert node['evaluation_origin']=='verified_prior_generation'
                    assert all(node[k]==v for k,v in original_node.items() if k not in ('node_id','evaluation_origin'))
                else:
                    assert node['evaluation_origin']=='new_geographic_evaluation'
                    if budget:
                        assert node['kernel_scene_reference']==dict(directory=inputs.name,path=scene_path,record_id=ni-len(row_seeds))
                        captured=json.loads(next(scene_stream))
                        assert captured['record_id']==ni-len(row_seeds) and captured['xy_bng']==xy
                        assert captured['native_profile_sha256']==node['native_profile_sha256']
                        counts['streamed_native_scene_references_checked']+=1
                if node['status']=='withheld':
                    assert node['relative_energy_per_m'] is None;reasons.update(node['reasons']);energy=None
                else:
                    assert node['status']=='conditional_research' and node['input_scene_kind']=='conditional_real_input'
                    expected=np.zeros((3,8));distance=node['direct_distance_m']
                    for pi,p in enumerate(prior.PERIODS):
                        for bi,f in enumerate(prior.BANDS):
                            expected[pi,bi]=math.fsum(powers[link][g,pi,bi]*np.interp(distance,air[f'distance_{f}'],air[f'{p}_{f}'][g]) for g in range(len(groups)))
                    array_close(expected,node['joint_hourly_air_source_power_per_m'])
                    energy=np.asarray(node['relative_energy_per_m']);array_close(energy,np.asarray(node['relative_transfer'])[:,None,:]*expected[None,:,:])
                    counts['independent_source_air_band_values']+=24;counts['point_energy_values_checked']+=48
                    if not sampled and node['evaluation_origin']=='new_geographic_evaluation' and len(samples)<12:
                        if budget:node=dict(node,candidate_kernel_scene=captured['candidate_kernel_scene'])
                        samples.append(node);sampled=True
                observations.append(dict(source_cell_id=node['source_cell_id'],fraction=t,energy=energy))
                counts['point_identities_checked']+=1
            if scene_stream is not None:
                assert next(scene_stream,None) is None
                scene_stream.close()
            assert row['new_evaluation_count']==row['evaluation_count']-len(row_seeds)
            assert row['evaluation_count']<=config['maximum_evaluations'];assert row['controls']==config
            minimum=5*sum(max(1,math.ceil((b['fraction']-a['fraction'])*cell['length_m']/config['maximum_panel_m'])) for cell in source['source_cells'] for a,b in zip(row['partition']['cells'][cell['segment_id']],row['partition']['cells'][cell['segment_id']][1:]))
            assert minimum==row['minimum_initial_observations']
            assert row['initial_partition_exceeds_budget']==(minimum>config['maximum_evaluations'])
            if row['termination']=='initial_partition_budget_exhausted':
                assert minimum>config['maximum_evaluations'] and row['new_evaluation_count']==0 and not row['leaves']
            assert row['reason_counts']==dict(reasons);all_reasons.update(reasons)
            assert row['unknown_positive_sample_observed']==bool(reasons)
            total=np.zeros((2,3,8));offset=np.zeros_like(total);error=np.zeros_like(total);lengths=defaultdict(list)
            for leaf in row['leaves']:
                cell=cells[leaf['source_cell_id']];a,b=leaf['left_fraction'],leaf['right_fraction'];length=(b-a)*cell['length_m']
                close(length,leaf['length_m']);assert length>0 and length<=config['maximum_panel_m']+1e-8
                lengths[leaf['source_cell_id']].append((a,b));mid=(a+b)/2
                fractions=[mid,(3*a+b)/4,(a+3*b)/4,mid-(b-a)/(2*math.sqrt(3)),mid+(b-a)/(2*math.sqrt(3))]
                values=[observations[i] for i in leaf['node_ids']]
                for value,t in zip(values,fractions):assert value['source_cell_id']==leaf['source_cell_id'];close(value['fraction'],t)
                fine=sum((v['energy']*length/2 for v in values[1:3] if v['energy'] is not None),start=np.zeros_like(total))
                check=sum((v['energy']*length/2 for v in values[3:5] if v['energy'] is not None),start=np.zeros_like(total))
                coarse=np.zeros_like(total) if values[0]['energy'] is None else values[0]['energy']*length
                complete=all(v['energy'] is not None for v in values);assert complete==leaf['complete']
                local=np.maximum(abs(fine-coarse),abs(check-fine)) if complete else np.zeros_like(total)
                if complete:
                    probes=[observations[i] for i in leaf['historical_probe_ids']]
                    assert all(p['source_cell_id']==leaf['source_cell_id'] and a<p['fraction']<b and p['energy'] is not None for p in probes)
                    prefix=max(len(row_seeds)-1,*leaf['node_ids'],*leaf['historical_probe_ids'])
                    expected_probes={i for i,p in enumerate(observations[:prefix+1]) if p['source_cell_id']==leaf['source_cell_id'] and a<p['fraction']<b and p['energy'] is not None}
                    assert expected_probes==set(leaf['historical_probe_ids']),'Historical probe omitted'
                    current=np.stack([v['energy'] for v in values]);historical=np.stack([p['energy'] for p in probes])
                    excess=np.maximum(0.,np.maximum(historical.max(axis=0)-current.max(axis=0),current.min(axis=0)-historical.min(axis=0)))
                    local=np.maximum(local,length*excess)
                array_close(fine,leaf['relative_energy']);array_close(check,leaf['offset_relative_energy']);array_close(local,leaf['absolute_local_error_proxy'])
                total+=fine;offset+=check;error+=local;counts['leaf_rules_and_weights_checked']+=1
            covered=0.
            for identity,intervals in lengths.items():
                ordered=sorted(intervals);assert ordered[0][0]==0.
                for first,second in zip(ordered,ordered[1:]):close(first[1],second[0])
                covered+=cells[identity]['length_m']*ordered[-1][1]
            close(covered,row['covered_source_length_m']);close(source['prepared_length_m'],row['source_length_m'])
            close(max(0.,source['prepared_length_m']-covered),row['unevaluated_source_length_m'])
            array_close(total,row['known_evaluated_relative_energy']);array_close(offset,row['offset_relative_energy']);array_close(error,row['absolute_error_proxy'])
            proxy=indicator(total,error);close(proxy,row['indicator_db']);close(proxy,row['history'][-1]['indicator_db'])
            met=row['termination']=='diagnostic_tolerance_met'
            assert row['diagnostic_tolerance_met']==met
            if met:
                assert not reasons and proxy is not None and proxy<=config['tolerance_db']
                close(covered,source['prepared_length_m']);array_close(total,row['whole_road_relative_energy'])
                assert all(p['complete'] for p in row['leaves'])
            else:assert row['whole_road_relative_energy'] is None
            for si,state in enumerate(('homogeneous','favourable')):
                m,c=prior.metrics(total[si]),prior.metrics(offset[si])
                previous=prior.metrics(old['whole_road_relative_energy'][si])
                for metric in m:
                    close(None if m[metric] is None or c[metric] is None else c[metric]-m[metric],row['offset_change_db'][state][metric])
                    if met:close(None if m[metric] is None or previous[metric] is None else m[metric]-previous[metric],row['change_from_prior_fine_db'][state][metric])
            if not met:assert row['change_from_prior_fine_db'] is None
            assert not row['refinement_convergence_proven'] and not row['path_transition_partition_complete']
            assert not row['complete_physical_path_support'] and not row['real_area_validated']
            assert row['rigorous_noise_error_bound_db'] is None and row['all_inclusive_noise_db'] is None
            counts['road_case_quadratures_checked']+=1
        assert next(stream,None) is None
    assert report['evaluations']==counts['point_identities_checked']
    assert report['new_evaluations']==report['evaluations']-counts['verified_prior_seed_observations']
    assert report['selected_road_cases']==len(rows)
    assert report['terminations']==dict(Counter(r['termination'] for r in rows))
    assert report['diagnostic_met_cases']==sum(r['diagnostic_tolerance_met'] for r in rows)
    assert report['new_unknown_cases']==sum(r['unknown_positive_sample_observed'] for r in rows)
    close(report['maximum_met_indicator_db'],max((r['indicator_db'] for r in rows if r['diagnostic_tolerance_met']),default=None))
    close(report['maximum_met_offset_change_db'],max((abs(v) for r in rows if r['diagnostic_tolerance_met'] for s in r['offset_change_db'].values() for v in s.values() if v is not None),default=None))
    updated=read(inputs/'road_ledger.json')['roads'];assert len(updated)==1444
    refinements={key:r for key,r in zip(keys,rows)}
    ledger_parent=read(ROOT/protocol['old_ledger_path'])['roads'] if budget else read(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json')['roads'] if corrected else original_rows
    if corrected:assert file_hash(ROOT/'artifacts/adaptive_road_postcheck_ledger_v1.json')==protocol['old_ledger_sha256']
    for row,old in zip(updated,ledger_parent):
        assert (row['link_id'],row['physical_case_id'])==(old['link_id'],old['physical_case_id'])
        new=refinements.get((row['link_id'],row['physical_case_id']))
        expected=new['whole_road_relative_energy'] if new else old['whole_road_relative_energy']
        if expected is None:assert row['whole_road_relative_energy'] is None
        else:array_close(expected,row['whole_road_relative_energy'])
        assert row['status']==(new['status'] if new else old['status'])
        if (corrected or budget) and new is None:
            assert row==old,'Carried reviewed ledger entry changed'
        else:
            assert row['integration_accuracy_outstanding']==(new is None or not new['diagnostic_tolerance_met'])
            assert row['integration_stage']==('adaptive_refined' if new else 'prior_finite_grid_only')
        counts['ledger_no_fallback_checks']+=1
    print(f'Independent accounting passed for {report["evaluations"]} observations; checking geographic partitions and {len(samples)} native profiles',flush=True)
    builder=prior.builder_module.load_builder(parents['physical_scene_inputs_v2'],parents['ground_cover_inputs_v3'],maximum_reflections=0)
    for row in rows:partition_check(source_map[row['link_id']],row,builder,protocol['receiver_xy_bng'],counts)
    features=read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    cases={c['case_id']:c for c in protocol['physical_cases']}
    for node in samples:prior.native_replay(node,builder,features,cases[node['physical_case_id']],protocol['receiver_xy_bng'],counts)
    for name in list(sys.modules):
        if name=='quiet_uk' or name.startswith('quiet_uk.'):del sys.modules[name]
    package=types.ModuleType('quiet_uk');package.__path__=[str(inputs/'code')];sys.modules['quiet_uk']=package
    core=importlib.import_module('quiet_uk.favourable_propagation');domain=importlib.import_module('quiet_uk.conditional_scene_domain')
    for node in samples:
        scene=dict(node['candidate_kernel_scene'],conditional_input_policy=domain.execution_policy(cases[node['physical_case_id']]))
        for bi,f in enumerate(prior.BANDS):
            result=core.direct_attenuation(scene,f,dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True),absorption_frequency_policy='exact_base10')
            assert result['status']=='conditional_research'
            for si,state in enumerate(('homogeneous','favourable')):
                value=10**(-(result[state+'_attenuation_db']-result['atmospheric_attenuation_db'])/10)
                assert math.isclose(value,node['relative_transfer'][si][bi],rel_tol=3e-11,abs_tol=1e-280)
            counts['frozen_uncached_kernel_bands_replayed']+=1
    output.mkdir(parents=True)
    for script in ('93_verify_adaptive_roads.py','90_verify_conditional_road_integration.py','86_verify_road_operating_inputs.py','88_verify_physical_scene_assumptions.py','81_build_real_road_scenes.py','57_acquire_ground_cover_sources.py'):
        shutil.copyfile(ROOT/'scripts'/script,output/script)
    if budget:
        budget_verifier.verified_inputs(inputs)
        assert all(file_hash(path)==sha for path,sha in review_hashes.items()),'Executed review code changed'
        (output/'code').mkdir()
        for path in review_hashes:shutil.copyfile(path,output/'code'/path.name)
    (output/'report.json').write_bytes(json_bytes(dict(status='passed_independent_adaptive_accounting_and_sampled_replay',
        input_inventory_sha256=input_hash,input_generation=read(inputs/'inventory.json')['generation_id'],counts=counts,reason_counts=all_reasons,
        input_summary=report,old_physics_code_preserved=not (corrected or budget),air_tables_byte_identical=True,
        authorised_physics_revision=protocol.get('model_validation_sha256') if budget else protocol.get('physics_revision') if corrected else None,
        review_code_sha256={str(path.relative_to(ROOT)).replace('\\','/'):sha for path,sha in review_hashes.items()},
        independent_scope='Selected population, every seed identity, every node/source-air product, complete positive leaf rules and weights, all local absolute proxies, road sums, physical/numerical withholding, original ledger carry-forward; source grid traversal and obstacle rays independently reconstructed.',
        sampled_scope='Native profile hash, terrain simplification, roof/G reconstruction and frozen uncached acoustic kernel parity.',
        diagnostic_tolerance_is_rigorous_error_bound=False,field_validation=False,external_engine_executed=False,all_inclusive_noise_db=None)))
    (output/'sampled_nodes.json').write_bytes(json_bytes([dict(link_id=n['link_id'],physical_case_id=n['physical_case_id'],node_id=n['node_id'],source_cell_id=n['source_cell_id'],fraction=n['fraction']) for n in samples]))
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==input_hash
    generation=helper.seal(output,'adaptive-road-review-');print(json.dumps(dict(generation_id=generation,counts=counts)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
