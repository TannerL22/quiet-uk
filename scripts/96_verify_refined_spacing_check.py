"""Independent length, four-point weight, source-air and spacing-check accounting."""
import argparse
from collections import Counter
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('prior_review',ROOT/'scripts/90_verify_conditional_road_integration.py')
review=importlib.util.module_from_spec(spec);spec.loader.exec_module(review)

def read(path):return json.loads(Path(path).read_bytes())
def close(a,b):review.close(a,b)
def array_close(a,b):assert np.allclose(a,b,rtol=3e-10,atol=1e-280)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace review required')
    review.helper.verified(inputs);input_hash=file_hash(inputs/'inventory.json');protocol=read(inputs/'protocol.json')
    primary=ROOT/'artifacts'/protocol['input_directory'];review.helper.verified(primary)
    assert file_hash(primary/'inventory.json')==protocol['input_inventory_sha256']
    for name in ('air_tables.json','air_tables.npz'):assert file_hash(inputs/name)==file_hash(primary/name)
    for path in (primary/'code').glob('*.py'):assert file_hash(path)==file_hash(inputs/'code'/path.name)
    corrected=protocol.get('kind')=='corrected_road_spacing_check_v1'
    budget=protocol.get('kind')=='budget_road_spacing_check_v1'
    budget_verifier=None;review_hashes={}
    if budget:
        budget_spec=importlib.util.spec_from_file_location('budget_finer_provenance_review',ROOT/'scripts/110_verify_budget_road_observations.py')
        budget_verifier=importlib.util.module_from_spec(budget_spec);budget_spec.loader.exec_module(budget_verifier)
        budget_verifier.verified_inputs(inputs)
        source_review=ROOT/'artifacts'/protocol['source_review'];review.helper.verified(source_review)
        source_report=read(source_review/'report.json')
        assert source_report['status']=='passed_independent_adaptive_accounting_and_sampled_replay'
        assert source_report['input_inventory_sha256']==file_hash(primary/'inventory.json')
        review_paths=[Path(__file__),ROOT/'scripts/110_verify_budget_road_observations.py',*[ROOT/'scripts'/n for n in ('90_verify_conditional_road_integration.py','86_verify_road_operating_inputs.py','88_verify_physical_scene_assumptions.py','81_build_real_road_scenes.py','57_acquire_ground_cover_sources.py')],*(ROOT/'src/quiet_uk').glob('*.py')]
        review_hashes={p:file_hash(p) for p in review_paths}
    settled=[r for r in read(primary/'refined_roads.json')['roads'] if r['diagnostic_tolerance_met']]
    if corrected:settled+=read(primary/'control_roads.json')['roads']
    if not budget:settled=sorted(settled,key=lambda r:(-r['prior_worst_spacing_change_db'],r['link_id'],r['physical_case_id']))
    if not corrected and not budget:settled=settled[:3]
    rows=read(inputs/'road_checks.json')['roads'];counts=Counter();assert len(rows)==len(settled)
    if budget:
        assert read(inputs/'primary_roads.json')['roads']==settled
        assert protocol['selected_identities']==[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in settled]
    elif not corrected:assert len(rows)==3
    else:
        assert read(inputs/'parent_roads.json')['roads']==settled
        assert protocol['selected_identities']==[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in settled]
        observed=Path(ROOT/'artifacts'/protocol['corrected_observation_directory']);review.helper.verified(observed)
        expected_cache={k:{(n['source_cell_id'],n['fraction']):n for n in v} for k,v in read(observed/'seed_evaluations.json').items()}
        with gzip.open(primary/'node_evaluations.jsonl.gz','rt') as old_stream:
            for old_line in old_stream:
                old_node=json.loads(old_line);old_key=old_node['link_id']+'|'+old_node['physical_case_id']
                expected_cache[old_key][old_node['source_cell_id'],old_node['fraction']]={k:v for k,v in old_node.items() if k!='candidate_kernel_scene'}
        declared_cache=read(inputs/'seed_evaluations.json')
        assert set(declared_cache)=={r['link_id']+'|'+r['physical_case_id'] for r in settled}
        for old_key,items in declared_cache.items():assert items==list(expected_cache[old_key].values()),'Finer lookup was not built from verified observations'
    parents={n:ROOT/'artifacts'/n for n in read(primary/'protocol.json')['parent_inventory_sha256']}
    sources={s['link_id']:s for s in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']}
    roads={r['link_id']:r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coefficients=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    calendar=read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    groups=read(inputs/'air_tables.json')['groups']
    with np.load(inputs/'air_tables.npz',allow_pickle=False) as archive:air={k:archive[k] for k in archive.files}
    with gzip.open(inputs/'node_evaluations.jsonl.gz','rt') as stream:
        for row,prior in zip(rows,settled):
            identity=(row['link_id'],row['physical_case_id']);assert identity==(prior['link_id'],prior['physical_case_id'])
            scene_path='point_scenes/'+identity[0]+'__'+identity[1]+'.jsonl.gz'
            scene_stream=gzip.open(inputs/scene_path,'rt',encoding='utf-8') if budget else None
            cells={c['segment_id']:c for c in sources[identity[0]]['source_cells']}
            power=review.group_source_power(roads[identity[0]],coefficients,calendar,groups)
            total=np.zeros((2,3,8));reasons=Counter();length=0.;ni=0
            for li,leaf in enumerate(prior['leaves']):
                cell=cells[leaf['source_cell_id']];a,b=leaf['left_fraction'],leaf['right_fraction']
                close(leaf['length_m'],(b-a)*cell['length_m'])
                for j in range(4):
                    node=json.loads(next(stream));assert (node['link_id'],node['physical_case_id'])==identity
                    assert node['node_id']==ni and node['leaf_index']==li and node['quadrature_j']==j and node['quadrature_n']==4
                    assert node['source_cell_id']==leaf['source_cell_id'];t=a+(b-a)*(j+.5)/4
                    close(t,node['fraction']);xy=[x+(y-x)*t for x,y in zip(cell['start_xy_bng'],cell['end_xy_bng'])];assert xy==node['xy_bng']
                    weight=leaf['length_m']/4;close(weight,node['weight_length_m']);length+=weight;ni+=1
                    if budget:
                        assert node['kernel_scene_reference']==dict(directory=inputs.name,path=scene_path,record_id=ni-1)
                        captured=json.loads(next(scene_stream))
                        assert captured['record_id']==ni-1 and captured['xy_bng']==xy
                        assert captured['native_profile_sha256']==node['native_profile_sha256']
                        counts['fresh_native_scene_references_checked']+=1
                    if corrected:
                        assert node['finer_observation_origin'] in ('verified_corrected_observation','new_native_geographic_evaluation')
                        counts[node['finer_observation_origin']]+=1
                        if node['finer_observation_origin']=='verified_corrected_observation':
                            cached=expected_cache[identity[0]+'|'+identity[1]][node['finer_cached_source_cell_id'],node['finer_cached_fraction']]
                            assert cached['xy_bng']==xy
                            for k,v in cached.items():
                                if k not in ('node_id','leaf_index','fraction','source_cell_id','quadrature_j','quadrature_n','weight_length_m','link_id','physical_case_id'):
                                    assert node[k]==v,'Cached finer observation changed: '+k
                    if node['status']=='withheld':
                        assert node['relative_energy_per_m'] is None;reasons.update(node['reasons'])
                    else:
                        assert node['status']=='conditional_research' and node['input_scene_kind']=='conditional_real_input'
                        expected=np.zeros((3,8));distance=node['direct_distance_m']
                        for pi,p in enumerate(review.PERIODS):
                            for bi,f in enumerate(review.BANDS):
                                expected[pi,bi]=math.fsum(power[g,pi,bi]*np.interp(distance,air[f'distance_{f}'],air[f'{p}_{f}'][g]) for g in range(len(groups)))
                        array_close(expected,node['joint_hourly_air_source_power_per_m'])
                        energy=np.asarray(node['relative_energy_per_m']);array_close(energy,np.asarray(node['relative_transfer'])[:,None,:]*expected[None,:,:])
                        total+=energy*weight;counts['independent_source_air_band_values']+=24;counts['point_energy_values_checked']+=48
                    counts['four_point_nodes_and_weights_checked']+=1
            if scene_stream is not None:
                assert next(scene_stream,None) is None
                scene_stream.close()
            close(length,sources[identity[0]]['prepared_length_m']);assert ni==row['evaluation_count']
            assert dict(reasons)==row['unknown_reason_counts'];array_close(total,row['known_evaluated_relative_energy'])
            if reasons:
                assert row['whole_road_relative_energy'] is None and row['change_from_adaptive_db'] is None and row['maximum_absolute_change_db'] is None
            else:
                array_close(total,row['whole_road_relative_energy']);maximum=0.
                for i,state in enumerate(('homogeneous','favourable')):
                    actual=review.metrics(total[i]);previous=review.metrics(prior['whole_road_relative_energy'][i])
                    for metric,value in actual.items():
                        difference=None if value is None or previous[metric] is None else value-previous[metric]
                        close(difference,row['change_from_adaptive_db'][state][metric])
                        if difference is not None:maximum=max(maximum,abs(difference))
                close(maximum,row['maximum_absolute_change_db'])
            passed=not reasons and row['maximum_absolute_change_db']<=.1
            assert row['status']==('finer_spacing_check_passed' if passed else 'finer_spacing_check_withheld_or_changed')
            assert row['all_inclusive_noise_db'] is None and row['rigorous_noise_error_bound_db'] is None and not row['real_area_validated'] and not row['refinement_convergence_proven']
            counts['confirmed_road_cases_checked']+=1
        assert next(stream,None) is None
    report=read(inputs/'report.json');assert report['evaluations']==counts['four_point_nodes_and_weights_checked']
    assert report['passed_cases']==sum(r['status']=='finer_spacing_check_passed' for r in rows)
    close(report['maximum_known_change_db'],max((r['maximum_absolute_change_db'] for r in rows if r['maximum_absolute_change_db'] is not None),default=None))
    if budget:
        checks={(r['link_id'],r['physical_case_id']):r for r in rows}
        primary_ledger=read(primary/'road_ledger.json')['roads'];final_ledger=read(inputs/'road_ledger.json')['roads']
        assert len(primary_ledger)==len(final_ledger)==1444
        for old,new in zip(primary_ledger,final_ledger):
            identity=(old['link_id'],old['physical_case_id']);check=checks.get(identity);expected=dict(old)
            if check is not None:
                expected.update(finer_spacing_check_status=check['status'],
                    finer_spacing_maximum_absolute_change_db=check['maximum_absolute_change_db'],
                    finer_spacing_unknown_reason_counts=check['unknown_reason_counts'])
                if check['status']!='finer_spacing_check_passed':
                    expected.update(whole_road_relative_energy=None,integration_accuracy_outstanding=True,
                        status='withheld_after_finer_spacing_check',integration_stage='adaptive_refined_finer_check_withheld')
            assert new==expected,'Finer ledger changed an unrelated entry or restored a rejected value'
            counts['final_ledger_entries_checked']+=1
        selected={(r['link_id'],r['physical_case_id']) for r in read(primary/'refined_roads.json')['roads']}
        original=read(ROOT/protocol['old_ledger_path'])['roads']
        for old,new in zip(original,final_ledger):
            if (old['link_id'],old['physical_case_id']) not in selected:
                assert old==new
                counts['earlier_ledger_entries_exactly_preserved']+=1
        assert counts['earlier_ledger_entries_exactly_preserved']==1420
        budget_verifier.verified_inputs(inputs)
        assert all(file_hash(p)==sha for p,sha in review_hashes.items()),'Executed review changed'
    output.mkdir(parents=True);shutil.copyfile(__file__,output/Path(__file__).name)
    if budget:
        (output/'code').mkdir()
        for p in review_hashes:shutil.copyfile(p,output/'code'/p.name)
    (output/'report.json').write_bytes(json_bytes(dict(status='passed_independent_finer_spacing_accounting',input_inventory_sha256=input_hash,
        input_generation=read(inputs/'inventory.json')['generation_id'],counts=counts,input_summary=report,
        review_code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):sha for p,sha in review_hashes.items()},
        scope='All selected cases, original lengths, four interior node locations/weights per leaf, category-specific source-air equations, energies and H/F period/Lden changes.',
        independent_acoustics=False,rigorous_error_bound_db=None,field_validation=False,all_inclusive_noise_db=None)))
    review.helper.verified(inputs);assert input_hash==file_hash(inputs/'inventory.json')
    generation=review.helper.seal(output,'adaptive-spacing-review-');print(json.dumps(dict(generation_id=generation,counts=counts)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
