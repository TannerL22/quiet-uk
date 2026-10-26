"""Native primary replay and finer checks for the 14 remaining older cases.

Keep the validated acoustic equations and old complete positive leaf partitions.
Revalidate all recorded history with fresh native observations before comparing
four fresh midpoint nodes per leaf. This does not qualify complete-area noise.
"""
import argparse
from collections import Counter
import copy
import gzip
import importlib.util
import json
import math
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('remaining_native_pipeline', ROOT/'scripts/109_integrate_budget_held_roads.py')
pipeline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pipeline
spec.loader.exec_module(pipeline)
from quiet_uk.adaptive_road_integration import indicator_db
from quiet_uk.conditional_road_propagation import receiver_metrics

read, key = pipeline.read, pipeline.key
file_hash, json_bytes = pipeline.file_hash, pipeline.json_bytes
RECEIPT = ROOT/'artifacts/budget_road_validation_v2.json'
BASE = 'adaptive_road_integration_v3'
BASE_REVIEW = 'adaptive_road_integration_review_v1'


def latest():
    receipt = read(RECEIPT)
    assert receipt['status'] == 'passed_budget_road_integration_validation'
    for name, sha in receipt['code_sha256'].items():
        assert file_hash(ROOT/name) == sha, 'Validated source changed: '+name
    assert file_hash(ROOT/receipt['postcheck_ledger']['path']) == receipt['postcheck_ledger']['sha256']
    return receipt


def selection():
    receipt = latest()
    rows = read(ROOT/receipt['postcheck_ledger']['path'])['roads']
    chosen = [r for r in rows if not r['integration_accuracy_outstanding']
              and r.get('finer_spacing_check_status') == 'not_selected']
    assert len(chosen) == len({key(r) for r in chosen}) == 14
    old = {key(r): r for r in read(ROOT/'artifacts'/BASE/'refined_roads.json')['roads']}
    result = [old[key(r)] for r in chosen]
    for ledger, row in zip(chosen, result):
        assert row['diagnostic_tolerance_met'] and row['whole_road_relative_energy'] == ledger['whole_road_relative_energy']
    return sorted(result, key=key)


def revalidated_row(original, observations):
    """Recompute the complete old leaf rules using fresh, full-history densities."""
    row = copy.deepcopy(original)
    if len(observations) != row['evaluation_count']:
        raise ValueError('Complete primary history required')
    energies = []
    for ni, node in enumerate(observations):
        if node['node_id'] != ni or node['status'] != 'conditional_research':
            raise ValueError('Every recorded primary position must remain numeric')
        energy = np.asarray(node['relative_energy_per_m'])
        if energy.shape != (2, 3, 8) or not np.isfinite(energy).all() or np.any(energy < 0):
            raise ValueError('Finite nonnegative primary density required')
        energies.append(energy)
    total = np.zeros((2, 3, 8)); offset = np.zeros_like(total); error = np.zeros_like(total)
    for leaf in row['leaves']:
        indices = leaf['node_ids']; width = leaf['length_m']
        values = [energies[i] for i in indices]
        fine = (values[1]+values[2])*width/2
        check = (values[3]+values[4])*width/2
        coarse = values[0]*width
        local = np.maximum(abs(fine-coarse), abs(check-fine))
        probes = [i for i, n in enumerate(observations) if n['source_cell_id'] == leaf['source_cell_id']
                  and leaf['left_fraction'] < n['fraction'] < leaf['right_fraction']]
        if not probes:
            raise ValueError('Missing complete leaf history')
        current = np.stack(values); historical = np.stack([energies[i] for i in probes])
        excess = np.maximum(0., np.maximum(historical.max(axis=0)-current.max(axis=0),
                                          current.min(axis=0)-historical.min(axis=0)))
        local = np.maximum(local, width*excess)
        leaf.update(historical_probe_ids=probes, relative_energy=fine.tolist(),
                    offset_relative_energy=check.tolist(), absolute_local_error_proxy=local.tolist(), complete=True)
        total += fine; offset += check; error += local
    proxy = indicator_db(total, error)
    if proxy is None or proxy > row['controls']['tolerance_db']:
        raise ValueError('Replayed full-history primary diagnostic no longer met')
    if not np.allclose(total, original['whole_road_relative_energy'], rtol=3e-11, atol=1e-280):
        raise ValueError('Replayed primary integral changed beyond implementation parity')
    changes = {}
    for si, state in enumerate(('homogeneous', 'favourable')):
        original_metrics, checked_metrics = receiver_metrics(total[si]), receiver_metrics(offset[si])
        changes[state] = {m: None if v is None or checked_metrics[m] is None else checked_metrics[m]-v
                          for m, v in original_metrics.items()}
    row.update(whole_road_relative_energy=total.tolist(), known_evaluated_relative_energy=total.tolist(),
        offset_relative_energy=offset.tolist(), absolute_error_proxy=error.tolist(), indicator_db=proxy,
        offset_change_db=changes, native_primary_revalidation=True,
        full_history_revalidated=True, original_primary_indicator_db=original['indicator_db'])
    return row


def configure():
    old_paths = pipeline.code_paths
    old_protocol = pipeline.protocol_common
    old_finish = pipeline.finish

    def code_paths():
        return [*old_paths(), Path(__file__), ROOT/'tests/test_remaining_road_controller.py']

    def protocol_common(kind, parents):
        receipt = latest()
        protocol = old_protocol(kind, parents)
        protocol.update(latest_validation_sha256=file_hash(RECEIPT),
            old_ledger_path=receipt['postcheck_ledger']['path'],
            old_ledger_sha256=receipt['postcheck_ledger']['sha256'])
        if kind == 'budget_road_spacing_check_v1':
            protocol['kind'] = 'remaining_road_spacing_check_v1'
        return protocol

    def finish(output, parents, codes, prefix):
        latest()
        if read(output/'protocol.json')['kind'] == 'remaining_road_spacing_check_v1':
            report = read(output/'report.json')
            report['status'] = 'completed_remaining_road_finer_spacing_check'
            (output/'report.json').write_bytes(json_bytes(report))
        old_finish(output, parents, codes, prefix)

    pipeline.code_paths = code_paths
    pipeline.protocol_common = protocol_common
    pipeline.finish = finish


def primary(output):
    started = time.perf_counter()
    selected = selection(); identities = {key(r) for r in selected}
    receipt = latest()
    parents = [BASE, BASE_REVIEW, 'development_road_observations_v1',
        *receipt['generations'], *pipeline.runner.prior.PARENTS]
    output, hashes, codes = pipeline.prepare_directory(output, parents)
    prior_nodes = {k: [] for k in identities}
    with gzip.open(ROOT/'artifacts'/BASE/'node_evaluations.jsonl.gz', 'rt') as stream:
        for line in stream:
            node = json.loads(line)
            if key(node) in identities:
                prior_nodes[key(node)].append({k: v for k, v in node.items() if k != 'candidate_kernel_scene'})
    development = read(ROOT/'artifacts/development_road_observations_v1/receipt.json')['case_counts']
    for row in selected:
        assert len(prior_nodes[key(row)]) == row['evaluation_count']
        assert key(row) not in development or development[key(row)] == row['evaluation_count']
        assert all(n['status'] == 'conditional_research' for n in prior_nodes[key(row)])
    (output/'seed_evaluations.json').write_bytes(json_bytes(prior_nodes))
    (output/'prior_primary_roads.json').write_bytes(json_bytes(dict(roads=selected)))
    protocol = pipeline.protocol_common('remaining_road_primary_replay_v1', hashes)
    protocol.update(original_primary_directory=BASE, original_primary_review_directory=BASE_REVIEW,
        selected_identities=[dict(link_id=r['link_id'], physical_case_id=r['physical_case_id']) for r in selected],
        selection='Exactly every primary-met ledger case still lacking a finer-spacing check.',
        primary_replay='All original recorded native positions, with complete history and unchanged accepted leaf partitions.',
        criterion_db=.1, planned_primary_observations=sum(r['evaluation_count'] for r in selected),
        planned_finer_observations=sum(4*len(r['leaves']) for r in selected))
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    sources = {r['link_id']: r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    fresh_seeds = {}; rows = []; counts = Counter()
    print(f'Replaying {protocol["planned_primary_observations"]} primary positions for {len(selected)} cases', flush=True)
    with gzip.open(output/'node_evaluations.jsonl.gz', 'wt', encoding='utf-8', compresslevel=1) as stream:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(max_workers=2, initializer=pipeline.initialise, initargs=(str(output), 'prepare')) as pool:
            for i, result in enumerate(pipeline.bounded(pool, [(sources[r['link_id']], r['physical_case_id']) for r in selected])):
                identity = key(result); observations = result['evaluations']
                for old, node in zip(prior_nodes[identity], observations):
                    assert node['status'] == 'conditional_research', node.get('reasons')
                    for field in ('relative_transfer', 'joint_hourly_air_source_power_per_m', 'relative_energy_per_m'):
                        assert np.allclose(old[field], node[field], rtol=3e-11, atol=1e-280), 'Primary numeric regression: '+field
                    counts['bit_identical_primary_observations'] += all(np.array_equal(old[f], node[f]) for f in (
                        'relative_transfer', 'joint_hourly_air_source_power_per_m', 'relative_energy_per_m'))
                    node['node_id'] = old['node_id']
                    node['evaluation_origin'] = 'new_native_primary_revalidation'
                row = revalidated_row(selected[i], observations)
                fresh_seeds[identity] = observations
                pipeline.write_nodes(stream, result)
                rows.append(row)
                (output/'progress.json').write_bytes(json_bytes(dict(completed=len(rows), selected=len(selected),
                    evaluations=sum(r['evaluation_count'] for r in rows), elapsed_seconds=time.perf_counter()-started)))
                (output/'completed_roads.json').write_bytes(json_bytes(dict(roads=rows)))
                print(f'Primary replay {len(rows)}/{len(selected)}: {identity}; {row["indicator_db"]}dB', flush=True)
    (output/'seed_evaluations.json').write_bytes(json_bytes(fresh_seeds))
    (output/'refined_roads.json').write_bytes(json_bytes(dict(roads=rows)))
    replacements = {key(r): r for r in rows}
    ledger = read(ROOT/protocol['old_ledger_path'])
    for row in ledger['roads']:
        if key(row) in replacements:
            row['whole_road_relative_energy'] = replacements[key(row)]['whole_road_relative_energy']
    (output/'road_ledger.json').write_bytes(json_bytes(ledger))
    (output/'report.json').write_bytes(json_bytes(dict(status='completed_remaining_road_primary_native_replay',
        selected_road_cases=len(rows), evaluations=sum(r['evaluation_count'] for r in rows),
        diagnostic_met_cases=len(rows), bit_identical_primary_observations=counts['bit_identical_primary_observations'],
        maximum_met_indicator_db=max(r['indicator_db'] for r in rows), elapsed_seconds=time.perf_counter()-started,
        all_inclusive_noise_db=None, map_updated=False)))
    pipeline.finish(output, hashes, codes, 'remaining-road-primary-replay-')


def complete():
    model = latest(); start_hash = file_hash(Path(__file__))
    verifier = ROOT/'scripts/114_verify_remaining_road_checks.py'
    verifier_hash = file_hash(verifier)
    junit=ROOT/'artifacts/remaining_road_controller_tests_v1.xml'
    test_path=ROOT/'tests/test_remaining_road_controller.py'
    junit_hash,test_hash=file_hash(junit),file_hash(test_path)
    test_cases=list(ET.parse(junit).getroot().iter('testcase'))
    assert len(test_cases)==7
    assert all(c.attrib.get('classname')=='tests.test_remaining_road_controller' and not any(c.find(k) is not None for k in ('failure','error','skipped')) for c in test_cases)
    p = ROOT/'artifacts/remaining_road_primary_replay_v1'
    a = ROOT/'artifacts/remaining_road_primary_review_v1'
    f = ROOT/'artifacts/remaining_road_spacing_check_v1'
    v = ROOT/'artifacts/remaining_road_spacing_review_v1'
    receipt_path = ROOT/'artifacts/remaining_road_validation_v1.json'
    for path in (p, a, f, v, receipt_path):
        if path.exists():
            raise ValueError('Fresh completion outputs required')
    primary(p)
    subprocess.run([sys.executable, '-u', str(verifier), '--stage', 'primary', '--inputs', str(p), '--output', str(a)], cwd=ROOT, check=True)
    pipeline.finer(p, a, f)
    subprocess.run([sys.executable, '-u', str(verifier), '--stage', 'finer', '--inputs', str(f), '--output', str(v)], cwd=ROOT, check=True)
    latest()
    assert file_hash(Path(__file__)) == start_hash and file_hash(verifier) == verifier_hash
    assert file_hash(junit)==junit_hash and file_hash(test_path)==test_hash
    generations = {path.name: pipeline.helper.verified(path) for path in (p, a, f, v)}
    ledger = read(f/'road_ledger.json')['roads']
    checks = read(f/'road_checks.json')['roads']
    summary = dict(selected_cases=14, selected_passing_both_checks=sum(r['status']=='finer_spacing_check_passed' for r in checks),
        selected_withheld=sum(r['status']!='finer_spacing_check_passed' for r in checks),
        total_finer_passed_cases=sum(r.get('finer_spacing_check_status')=='finer_spacing_check_passed' for r in ledger),
        primary_met_cases_without_finer_check=sum(not r['integration_accuracy_outstanding'] and r.get('finer_spacing_check_status')=='not_selected' for r in ledger),
        other_ledger_entries_exactly_preserved=1430)
    receipt_path.write_bytes(json_bytes(dict(status='passed_remaining_road_check_validation',
        prior_validation_sha256=file_hash(RECEIPT), summary=summary,
        generations={n: inv['generation_id'] for n, inv in generations.items()},
        generation_inventory_sha256={path.name: file_hash(path/'inventory.json') for path in (p,a,f,v)},
        code_sha256={**model['code_sha256'], 'scripts/113_complete_remaining_road_checks.py': start_hash,
                     'scripts/114_verify_remaining_road_checks.py': verifier_hash,
                     'tests/test_remaining_road_controller.py':test_hash},
        tests=dict(preserved_prior_unique_tests=model['tests']['total_unique_tests'],new_focused_tests=7,
            total_unique_tests=model['tests']['total_unique_tests']+7),
        junit_sha256={str(junit.relative_to(ROOT)).replace('\\','/'):junit_hash},
        postcheck_ledger=dict(path=str((f/'road_ledger.json').relative_to(ROOT)).replace('\\','/'), sha256=file_hash(f/'road_ledger.json')),
        primary=read(p/'report.json'), finer=read(f/'report.json'),
        field_validation=False, complete_physical_path_support=False, rigorous_noise_error_bound_db=None,
        all_inclusive_noise_db=None, map_updated=False)))
    print(json.dumps(dict(receipt=str(receipt_path), summary=summary)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('primary', 'complete', 'spawn-probe'), required=True)
    parser.add_argument('--output')
    args = parser.parse_args()
    configure()
    if args.stage == 'spawn-probe':
        pipeline.spawn_probe()
    elif args.stage == 'primary':
        primary(args.output)
    else:
        complete()
