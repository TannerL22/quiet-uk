"""Independent accounting and sampled native/kernel replay for remaining cases.

Does not import the controller or adaptive quadrature implementation. Reuses the
previous independently reviewed source/air equations and partition checker.
"""
import argparse
from collections import Counter, defaultdict
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


accounting = module('remaining_independent_accounting', '93_verify_adaptive_roads.py')
review = accounting.prior
read = accounting.read
RECEIPT = ROOT/'artifacts/budget_road_validation_v2.json'


def key(row):
    return row['link_id']+'|'+row['physical_case_id']


def array_close(a, b):
    assert np.allclose(a, b, rtol=3e-10, atol=1e-280)


def context(inputs):
    inputs = Path(inputs).resolve()
    review.helper.verified(inputs)
    protocol = read(inputs/'protocol.json')
    receipt = read(RECEIPT)
    assert receipt['status'] == 'passed_budget_road_integration_validation'
    assert protocol['latest_validation_sha256'] == file_hash(RECEIPT)
    assert protocol['old_ledger_path'] == receipt['postcheck_ledger']['path']
    assert protocol['old_ledger_sha256'] == receipt['postcheck_ledger']['sha256']
    assert file_hash(ROOT/protocol['old_ledger_path']) == protocol['old_ledger_sha256']
    for name, sha in receipt['code_sha256'].items():
        assert file_hash(ROOT/name) == sha
    for relative, sha in read(inputs/'provenance.json')['code_sha256'].items():
        assert file_hash(ROOT/relative) == sha and file_hash(inputs/'code'/Path(relative).name) == sha
    parents = {name: ROOT/'artifacts'/name for name in protocol['parent_inventory_sha256']}
    for name, path in parents.items():
        review.helper.verified(path)
        assert file_hash(path/'inventory.json') == protocol['parent_inventory_sha256'][name]
    sources = {r['link_id']: r for r in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']}
    roads = {r['link_id']: r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coeff = read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    calendar = read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    groups = read(inputs/'air_tables.json')['groups']
    with np.load(inputs/'air_tables.npz', allow_pickle=False) as archive:
        air = {k: archive[k] for k in archive.files}
    original = parents['adaptive_road_integration_v3']
    for name in ('air_tables.json', 'air_tables.npz'):
        assert file_hash(inputs/name) == file_hash(original/name)
    return protocol, parents, sources, roads, coeff, calendar, groups, air


def point_energy(node, power, groups, air, counts):
    if node['status'] == 'withheld':
        assert node.get('relative_energy_per_m') is None and node['reasons']
        return None
    assert node['status'] == 'conditional_research' and node['input_scene_kind'] == 'conditional_real_input'
    expected = np.zeros((3, 8))
    distance = node['direct_distance_m']
    for pi, period in enumerate(review.PERIODS):
        for bi, frequency in enumerate(review.BANDS):
            expected[pi, bi] = math.fsum(power[g, pi, bi]*np.interp(distance,
                air[f'distance_{frequency}'], air[f'{period}_{frequency}'][g]) for g in range(len(groups)))
    array_close(expected, node['joint_hourly_air_source_power_per_m'])
    transfer = np.asarray(node['relative_transfer'])
    assert transfer.shape == (2, 8) and np.isfinite(transfer).all() and np.all(transfer >= 0)
    energy = np.asarray(node['relative_energy_per_m'])
    array_close(energy, transfer[:, None, :]*expected[None, :, :])
    counts['independent_source_air_band_values'] += 24
    counts['independent_point_energy_values'] += 48
    return energy


def capture(node, stream, index, inputs, identity):
    scene = json.loads(next(stream))
    assert node['kernel_scene_reference'] == dict(directory=inputs.name,
        path='point_scenes/'+identity.replace('|', '__')+'.jsonl.gz', record_id=index)
    assert scene['record_id'] == index and scene['xy_bng'] == node['xy_bng']
    assert scene['native_profile_sha256'] == node['native_profile_sha256']
    return dict(node, candidate_kernel_scene=scene['candidate_kernel_scene'])


def primary(inputs, data, counts):
    protocol, parents, sources, roads, coeff, calendar, groups, air = data
    assert protocol['kind'] == 'remaining_road_primary_replay_v1'
    old_ledger = read(ROOT/protocol['old_ledger_path'])['roads']
    selected = sorted([r for r in old_ledger if not r['integration_accuracy_outstanding'] and r.get('finer_spacing_check_status')=='not_selected'], key=key)
    assert len(selected) == 14
    assert protocol['selected_identities'] == [dict(link_id=r['link_id'], physical_case_id=r['physical_case_id']) for r in selected]
    original = parents[protocol['original_primary_directory']]
    original_review = parents[protocol['original_primary_review_directory']]
    assert read(original_review/'report.json')['status'] == 'passed_independent_adaptive_accounting_and_sampled_replay'
    assert read(original_review/'report.json')['input_inventory_sha256'] == file_hash(original/'inventory.json')
    old_rows = {key(r): r for r in read(original/'refined_roads.json')['roads']}
    assert read(inputs/'prior_primary_roads.json')['roads'] == [old_rows[key(r)] for r in selected]
    old_nodes = {key(r): [] for r in selected}
    with gzip.open(original/'node_evaluations.jsonl.gz', 'rt') as stream:
        for line in stream:
            n = json.loads(line)
            if key(n) in old_nodes:
                old_nodes[key(n)].append({k: v for k, v in n.items() if k != 'candidate_kernel_scene'})
    rows = read(inputs/'refined_roads.json')['roads']
    seeds = read(inputs/'seed_evaluations.json')
    assert [key(r) for r in rows] == [key(r) for r in selected]
    samples = []; exact = 0
    with gzip.open(inputs/'node_evaluations.jsonl.gz', 'rt') as nodes:
        for row in rows:
            identity = key(row); original_row = old_rows[identity]
            assert row['partition'] == original_row['partition'] and row['controls'] == original_row['controls']
            assert row['evaluation_count'] == len(old_nodes[identity]) == len(seeds[identity])
            cells = {c['segment_id']: c for c in sources[row['link_id']]['source_cells']}
            power = review.group_source_power(roads[row['link_id']], coeff, calendar, groups)
            energies = []; observations = []
            with gzip.open(inputs/'point_scenes'/(identity.replace('|','__')+'.jsonl.gz'), 'rt') as scenes:
                for ni, old in enumerate(old_nodes[identity]):
                    node = json.loads(next(nodes))
                    assert node == seeds[identity][ni] and key(node) == identity and node['node_id'] == ni
                    assert node['evaluation_origin'] == 'new_native_primary_revalidation'
                    assert (node['source_cell_id'], node['fraction']) == (old['source_cell_id'], old['fraction'])
                    cell = cells[node['source_cell_id']]; t = node['fraction']
                    assert 0 < t < 1
                    assert node['xy_bng'] == old['xy_bng'] == [a+(b-a)*t for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
                    assert node['native_profile_sha256'] == old['native_profile_sha256']
                    assert node['historical_observation_sha256'] == hashlib.sha256(json_bytes(old)).hexdigest()
                    assert node['historical_status'] == old['status'] == 'conditional_research'
                    full = capture(node, scenes, ni, inputs, identity)
                    if ni == 0:
                        samples.append(full)
                    for field in ('relative_transfer', 'joint_hourly_air_source_power_per_m', 'relative_energy_per_m'):
                        assert np.allclose(old[field], node[field], rtol=3e-11, atol=1e-280)
                    exact += all(np.array_equal(old[f], node[f]) for f in ('relative_transfer','joint_hourly_air_source_power_per_m','relative_energy_per_m'))
                    energy = point_energy(node, power, groups, air, counts)
                    assert energy is not None
                    energies.append(energy); observations.append(node)
                    counts['primary_native_observations_checked'] += 1
                assert next(scenes, None) is None
            total = np.zeros((2,3,8)); offset = np.zeros_like(total); error = np.zeros_like(total)
            intervals = defaultdict(list)
            assert len(row['leaves']) == len(original_row['leaves'])
            for leaf, old_leaf in zip(row['leaves'], original_row['leaves']):
                for field in ('source_cell_id','left_fraction','right_fraction','node_ids','length_m'):
                    assert leaf[field] == old_leaf[field]
                cell = cells[leaf['source_cell_id']]; a,b = leaf['left_fraction'],leaf['right_fraction']
                length = (b-a)*cell['length_m']; review.close(length,leaf['length_m'])
                fractions = [(a+b)/2,(3*a+b)/4,(a+3*b)/4,(a+b)/2-(b-a)/(2*math.sqrt(3)),(a+b)/2+(b-a)/(2*math.sqrt(3))]
                for index, t in zip(leaf['node_ids'], fractions):
                    assert observations[index]['source_cell_id'] == leaf['source_cell_id']
                    review.close(observations[index]['fraction'],t)
                values = [energies[i] for i in leaf['node_ids']]
                # Match the declared pair-rule association before subtracting
                # nearly equal integrals. Distributing the weight changes the
                # last bit and can dominate a tiny local difference proxy.
                fine = (values[1]+values[2])*length/2
                checked = (values[3]+values[4])*length/2
                local = np.maximum(abs(fine-values[0]*length),abs(checked-fine))
                expected = [i for i,n in enumerate(observations) if n['source_cell_id']==leaf['source_cell_id'] and a<n['fraction']<b]
                assert expected == leaf['historical_probe_ids']
                history = np.stack([energies[i] for i in expected]); current = np.stack(values)
                local = np.maximum(local,length*np.maximum(0.,np.maximum(history.max(axis=0)-current.max(axis=0),current.min(axis=0)-history.min(axis=0))))
                array_close(fine,leaf['relative_energy']);array_close(checked,leaf['offset_relative_energy']);array_close(local,leaf['absolute_local_error_proxy'])
                assert leaf['complete']
                total+=fine;offset+=checked;error+=local
                intervals[leaf['source_cell_id']].append((a,b))
                counts['complete_primary_leaf_rules_checked']+=1
            for identity_cell,cell in cells.items():
                spans=sorted(intervals[identity_cell]);assert spans[0][0]==0 and spans[-1][1]==1
                for a,b in zip(spans,spans[1:]):review.close(a[1],b[0])
            array_close(total,row['whole_road_relative_energy']);array_close(total,row['known_evaluated_relative_energy'])
            array_close(offset,row['offset_relative_energy']);array_close(error,row['absolute_error_proxy'])
            array_close(total,original_row['whole_road_relative_energy'])
            proxy=accounting.indicator(total,error);review.close(proxy,row['indicator_db'])
            assert proxy is not None and proxy<=.1 and row['diagnostic_tolerance_met'] and row['full_history_revalidated']
            for si,state in enumerate(('homogeneous','favourable')):
                first,second=review.metrics(total[si]),review.metrics(offset[si])
                for metric,value in first.items():review.close(None if value is None or second[metric] is None else second[metric]-value,row['offset_change_db'][state][metric])
            counts['primary_cases_checked']+=1
        assert next(nodes,None) is None
    report=read(inputs/'report.json')
    assert report['evaluations']==counts['primary_native_observations_checked']==6016
    assert report['bit_identical_primary_observations']==exact
    ledger=read(inputs/'road_ledger.json')['roads'];assert len(ledger)==len(old_ledger)==1444
    replacements={key(r):r for r in rows}
    for old,new in zip(old_ledger,ledger):
        expected=dict(old)
        if key(old) in replacements:expected['whole_road_relative_energy']=replacements[key(old)]['whole_road_relative_energy']
        assert new==expected
        counts['primary_ledger_entries_checked']+=1
    return samples,rows


def finer(inputs,data,counts):
    protocol,parents,sources,roads,coeff,calendar,groups,air=data
    assert protocol['kind']=='remaining_road_spacing_check_v1'
    source=parents[protocol['input_directory']]
    source_review=parents[protocol['source_review']]
    assert read(source_review/'report.json')['status']=='passed_independent_adaptive_accounting_and_sampled_replay'
    assert read(source_review/'report.json')['input_inventory_sha256']==file_hash(source/'inventory.json')
    rows=read(source/'refined_roads.json')['roads'];checks=read(inputs/'road_checks.json')['roads']
    assert len(rows)==len(checks)==14 and read(inputs/'primary_roads.json')['roads']==rows
    assert protocol['selected_identities']==[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in rows]
    for path in (source/'code').glob('*.py'):assert file_hash(path)==file_hash(inputs/'code'/path.name)
    with gzip.open(inputs/'node_evaluations.jsonl.gz','rt') as stream:
        for row,check in zip(rows,checks):
            identity=key(row);assert key(check)==identity
            power=review.group_source_power(roads[row['link_id']],coeff,calendar,groups)
            cells={c['segment_id']:c for c in sources[row['link_id']]['source_cells']}
            total=np.zeros((2,3,8));reasons=Counter();ni=0;length=0.
            with gzip.open(inputs/'point_scenes'/(identity.replace('|','__')+'.jsonl.gz'),'rt') as scenes:
                for li,leaf in enumerate(row['leaves']):
                    cell=cells[leaf['source_cell_id']];a,b=leaf['left_fraction'],leaf['right_fraction']
                    for j in range(4):
                        node=json.loads(next(stream));assert key(node)==identity and node['node_id']==ni
                        assert node['leaf_index']==li and node['quadrature_j']==j and node['quadrature_n']==4
                        assert node['source_cell_id']==leaf['source_cell_id']
                        t=a+(b-a)*(j+.5)/4;review.close(t,node['fraction'])
                        assert node['xy_bng']==[x+(y-x)*t for x,y in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
                        capture(node,scenes,ni,inputs,identity)
                        weight=leaf['length_m']/4;review.close(weight,node['weight_length_m']);length+=weight
                        energy=point_energy(node,power,groups,air,counts)
                        if energy is None:reasons.update(node['reasons'])
                        else:total+=energy*weight
                        ni+=1;counts['finer_nodes_and_weights_checked']+=1
                assert next(scenes,None) is None
            review.close(length,sources[row['link_id']]['prepared_length_m']);assert ni==check['evaluation_count']
            array_close(total,check['known_evaluated_relative_energy']);assert dict(reasons)==check['unknown_reason_counts']
            maximum=None
            if reasons:
                assert check['whole_road_relative_energy'] is None and check['maximum_absolute_change_db'] is None
            else:
                array_close(total,check['whole_road_relative_energy']);maximum=0.
                for si,state in enumerate(('homogeneous','favourable')):
                    current,old=review.metrics(total[si]),review.metrics(row['whole_road_relative_energy'][si])
                    for metric,value in current.items():
                        delta=None if value is None or old[metric] is None else value-old[metric]
                        review.close(delta,check['change_from_adaptive_db'][state][metric])
                        if delta is not None:maximum=max(maximum,abs(delta))
                review.close(maximum,check['maximum_absolute_change_db'])
            passed=not reasons and maximum is not None and maximum<=.1
            assert check['status']==('finer_spacing_check_passed' if passed else 'finer_spacing_check_withheld_or_changed')
            counts['finer_cases_checked']+=1
        assert next(stream,None) is None
    report=read(inputs/'report.json')
    assert report['evaluations']==counts['finer_nodes_and_weights_checked']==4680
    assert report['passed_cases']==sum(c['status']=='finer_spacing_check_passed' for c in checks)
    old_ledger=read(source/'road_ledger.json')['roads'];ledger=read(inputs/'road_ledger.json')['roads']
    assert len(old_ledger)==len(ledger)==1444
    by_key={key(c):c for c in checks}
    for old,new in zip(old_ledger,ledger):
        expected=dict(old);check=by_key.get(key(old))
        if check:
            expected.update(finer_spacing_check_status=check['status'],finer_spacing_maximum_absolute_change_db=check['maximum_absolute_change_db'],finer_spacing_unknown_reason_counts=check['unknown_reason_counts'])
            if check['status']!='finer_spacing_check_passed':expected.update(whole_road_relative_energy=None,integration_accuracy_outstanding=True,status='withheld_after_finer_spacing_check',integration_stage='adaptive_refined_finer_check_withheld')
        assert new==expected
        counts['final_ledger_entries_checked']+=1
    original=read(ROOT/protocol['old_ledger_path'])['roads']
    for old,new in zip(original,ledger):
        if key(old) not in by_key:
            assert old==new;counts['earlier_ledger_entries_exactly_preserved']+=1
    assert counts['earlier_ledger_entries_exactly_preserved']==1430
    return [],[]


def run(stage,inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace review required')
    data=context(inputs);input_hash=file_hash(inputs/'inventory.json')
    paths=[Path(__file__),*[ROOT/'scripts'/n for n in ('93_verify_adaptive_roads.py','90_verify_conditional_road_integration.py','86_verify_road_operating_inputs.py','88_verify_physical_scene_assumptions.py','81_build_real_road_scenes.py','57_acquire_ground_cover_sources.py')],*(ROOT/'src/quiet_uk').glob('*.py')]
    hashes={p:file_hash(p) for p in paths};counts=Counter()
    samples,rows=(primary if stage=='primary' else finer)(inputs,data,counts)
    if stage=='primary':
        protocol,parents,sources,*_=data
        builder=review.builder_module.load_builder(parents['physical_scene_inputs_v2'],parents['ground_cover_inputs_v3'],maximum_reflections=0)
        features=read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
        cases={c['case_id']:c for c in protocol['physical_cases']}
        for row in rows:accounting.partition_check(sources[row['link_id']],row,builder,protocol['receiver_xy_bng'],counts)
        from quiet_uk import favourable_propagation as core
        from quiet_uk.conditional_scene_domain import execution_policy
        for node in samples:
            case=cases[node['physical_case_id']]
            review.native_replay(node,builder,features,case,protocol['receiver_xy_bng'],counts)
            scene=dict(node['candidate_kernel_scene'],conditional_input_policy=execution_policy(case))
            for bi,frequency in enumerate(review.BANDS):
                result=core.direct_attenuation(scene,frequency,dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True),absorption_frequency_policy='exact_base10')
                assert result['status']=='conditional_research'
                for si,state in enumerate(('homogeneous','favourable')):
                    value=10**(-(result[state+'_attenuation_db']-result['atmospheric_attenuation_db'])/10)
                    assert math.isclose(value,node['relative_transfer'][si][bi],rel_tol=3e-11,abs_tol=1e-280)
                counts['uncached_kernel_bands_replayed']+=1
    context(inputs);assert input_hash==file_hash(inputs/'inventory.json')
    assert all(file_hash(p)==sha for p,sha in hashes.items())
    output.mkdir(parents=True);(output/'code').mkdir()
    for path in hashes:shutil.copyfile(path,output/'code'/path.name)
    status='passed_independent_adaptive_accounting_and_sampled_replay' if stage=='primary' else 'passed_independent_finer_spacing_accounting'
    (output/'report.json').write_bytes(json_bytes(dict(status=status,input_inventory_sha256=input_hash,
        input_generation=read(inputs/'inventory.json')['generation_id'],counts=counts,
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):sha for p,sha in hashes.items()},
        field_validation=False,rigorous_noise_error_bound_db=None,all_inclusive_noise_db=None,map_updated=False)))
    generation=review.helper.seal(output,'remaining-road-'+stage+'-review-')
    print(json.dumps(dict(generation_id=generation,counts=counts)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('primary','finer'),required=True)
    parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.stage,args.inputs,args.output)
