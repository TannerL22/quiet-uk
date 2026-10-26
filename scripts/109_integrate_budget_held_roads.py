"""Bounded rerun of the 24 initial-partition budget holds, with frozen physics.

Every retained historical location is reevaluated natively before integration.
Large kernel scenes are streamed separately so larger sample budgets do not
require retaining all terrain/obstacle profiles in worker memory.
"""
import argparse
from collections import Counter, deque
from concurrent.futures import ProcessPoolExecutor
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts'/filename)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


runner = module('budget_geographic_runner', '92_refine_conditional_roads.py')
checker = module('budget_finer_rule', '95_check_refined_road_spacing.py')
recorder = module('budget_ledger_recorder', '94_record_adaptive_validation.py')
helper = runner.prior.helper
ORIGINAL_INTEGRATOR = runner.integrate_adaptive
BASE = 'adaptive_road_integration_v3'
CORRECTED = 'corrected_road_integration_v1'
CACHE = 'corrected_road_observations_v2'
MODEL_RECEIPT = ROOT/'artifacts/ground_average_validation_v2.json'
WORK = {}


def read(path):
    return json.loads(Path(path).read_bytes())


def key(row):
    return row['link_id']+'|'+row['physical_case_id']


def point_key(node):
    return node['source_cell_id'], node['fraction']


def bounded_controls(minimum_initial, retained):
    """Cover every initial rule, all history, and at most 512 ten-node splits."""
    for value in (minimum_initial, retained):
        if type(value) is not int or value < 0:
            raise ValueError('Nonnegative integer observation counts required')
    if minimum_initial == 0 or minimum_initial % 5:
        raise ValueError('Five-node initial rules required')
    return dict(runner.DEFAULTS,
                maximum_evaluations=minimum_initial+retained+10*runner.DEFAULTS['maximum_refinements'])


def selected_rows():
    rows = [r for folder in (BASE, CORRECTED)
            for r in read(ROOT/'artifacts'/folder/'refined_roads.json')['roads']
            if r['termination'] == 'initial_partition_budget_exhausted']
    assert len(rows) == len({key(r) for r in rows}) == 24
    receipt = read(MODEL_RECEIPT)
    ledger = {key(r): r for r in read(ROOT/receipt['postcheck_ledger']['path'])['roads']}
    assert all(ledger[key(r)]['whole_road_relative_energy'] is None and
               ledger[key(r)]['integration_accuracy_outstanding'] for r in rows)
    return sorted(rows, key=lambda r: (r['minimum_initial_observations'], key(r)))


def verified_model():
    receipt = read(MODEL_RECEIPT)
    assert receipt['status'] == 'passed_ground_average_fix_validation'
    for name, sha in receipt['code_sha256'].items():
        assert file_hash(ROOT/name) == sha, 'Validated implementation changed: '+name
    assert file_hash(ROOT/receipt['postcheck_ledger']['path']) == receipt['postcheck_ledger']['sha256']
    for name, sha in receipt['generation_inventory_sha256'].items():
        directory = ROOT/'artifacts'/name
        helper.verified(directory)
        assert file_hash(directory/'inventory.json') == sha
    # Evaluator and quadrature controllers are also pinned by the native replay.
    snapshot = ROOT/'artifacts/ground_average_replay_v1/code'
    for filename in ('92_refine_conditional_roads.py', '95_check_refined_road_spacing.py',
                     '94_record_adaptive_validation.py', '89_integrate_conditional_roads.py'):
        assert file_hash(ROOT/'scripts'/filename) == file_hash(snapshot/filename)
    return receipt


def code_paths():
    return [Path(__file__), *[ROOT/'scripts'/name for name in (
        '92_refine_conditional_roads.py', '95_check_refined_road_spacing.py',
        '94_record_adaptive_validation.py', '89_integrate_conditional_roads.py',
        '81_build_real_road_scenes.py', '57_acquire_ground_cover_sources.py')],
        *(ROOT/'src/quiet_uk').glob('*.py')]


def prepare_directory(output, parents):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Fresh workspace generation required')
    verified_model()
    hashes = {}
    for name in sorted(set(parents)):
        directory = ROOT/'artifacts'/name
        helper.verified(directory)
        hashes[name] = file_hash(directory/'inventory.json')
    codes = {path: file_hash(path) for path in code_paths()}
    output.mkdir(parents=True)
    (output/'point_scenes').mkdir()
    (output/'case_progress').mkdir()
    for name in ('air_tables.json', 'air_tables.npz'):
        shutil.copyfile(ROOT/'artifacts'/BASE/name, output/name)
    return output, hashes, codes


def finish(output, parents, codes, prefix):
    assert all(file_hash(path) == sha for path, sha in codes.items()), 'Executed code changed'
    verified_model()
    for name, sha in parents.items():
        helper.verified(ROOT/'artifacts'/name)
        assert file_hash(ROOT/'artifacts'/name/'inventory.json') == sha
    (output/'code').mkdir()
    for path in codes:
        shutil.copyfile(path, output/'code'/path.name)
    (output/'provenance.json').write_bytes(json_bytes(dict(
        parent_inventory_sha256=parents,
        model_validation_sha256=file_hash(MODEL_RECEIPT),
        code_sha256={str(p.relative_to(ROOT)).replace('\\', '/'): sha for p, sha in codes.items()},
        licences='Preserve source-specific licences and attribution in all immutable parents.')))
    generation = helper.seal(output, prefix)
    print(json.dumps(dict(generation_id=generation, report=read(output/'report.json'))), flush=True)


def initialise(output, mode):
    runner.initialise(output)
    WORK.update(output=Path(output), mode=mode)
    if mode == 'finer':
        checker.BANK.clear()
        checker.BANK.update({(r['link_id'], r['physical_case_id']): r
                             for r in read(Path(output)/'primary_roads.json')['roads']})
    runner.integrate_adaptive = captured_integration


def captured_integration(source, evaluate, *, partition, controls, seed_evaluations):
    case_id = seed_evaluations[0]['physical_case_id']
    identity = source['link_id']+'|'+case_id
    output = WORK['output']
    filename = 'point_scenes/'+identity.replace('|', '__')+'.jsonl.gz'
    count = 0
    started = time.perf_counter()
    progress_path = output/'case_progress'/(identity.replace('|', '__')+'.json')
    with gzip.open(output/filename, 'wt', compresslevel=1, encoding='utf-8') as stream:
        def captured(xy):
            nonlocal count
            result = dict(evaluate(xy))
            scene = result.pop('candidate_kernel_scene', None)
            stream.write(json.dumps(dict(record_id=count, xy_bng=xy,
                native_profile_sha256=result['native_profile_sha256'],
                candidate_kernel_scene=scene), allow_nan=False, separators=(',', ':'))+'\n')
            result['kernel_scene_reference'] = dict(directory=output.name, path=filename, record_id=count)
            count += 1
            if count % 200 == 0:
                progress_path.write_bytes(json_bytes(dict(identity=identity, stage=WORK['mode'],
                    new_native_evaluations=count, elapsed_seconds=time.perf_counter()-started)))
            return result
        if WORK['mode'] == 'prepare':
            observations = []
            for old in seed_evaluations:
                fresh = captured(old['xy_bng'])
                assert fresh['native_profile_sha256'] == old['native_profile_sha256'], 'Historical native input changed'
                fresh.update(link_id=source['link_id'], physical_case_id=case_id,
                    source_cell_id=old['source_cell_id'], fraction=old['fraction'], xy_bng=old['xy_bng'],
                    historical_observation_sha256=hashlib.sha256(json_bytes(old)).hexdigest(),
                    historical_status=old['status'], historical_native_replay=True)
                observations.append(fresh)
            result = dict(evaluations=observations, evaluation_count=len(observations))
        elif WORK['mode'] == 'integrate':
            planned = runner.STATE['protocol']['case_controls'][identity]
            result = ORIGINAL_INTEGRATOR(source, captured, partition=partition,
                                        controls=planned, seed_evaluations=seed_evaluations)
        else:
            result = checker.finer_rule(source, captured, partition=partition,
                                        controls=controls, seed_evaluations=seed_evaluations)
    progress_path.write_bytes(json_bytes(dict(identity=identity, stage=WORK['mode'],
        new_native_evaluations=count, elapsed_seconds=time.perf_counter()-started,
        complete=True, status=result.get('status'))))
    return result


def compute(job):
    return runner.compute(job)


def bounded(pool, jobs):
    jobs = iter(jobs)
    pending = deque()
    for _ in range(4):
        job = next(jobs, None)
        if job is not None:
            pending.append(pool.submit(compute, job))
    while pending:
        result = pending.popleft().result()
        job = next(jobs, None)
        if job is not None:
            pending.append(pool.submit(compute, job))
        yield result


def write_nodes(stream, row):
    for node in row.pop('evaluations'):
        stream.write(json.dumps(dict(node, link_id=row['link_id'], physical_case_id=row['physical_case_id']),
                                allow_nan=False, separators=(',', ':'))+'\n')
    stream.flush()


def protocol_common(kind, parents):
    receipt = read(MODEL_RECEIPT)
    return dict(kind=kind, parent_inventory_sha256=parents,
        model_validation_sha256=file_hash(MODEL_RECEIPT),
        old_ledger_path=receipt['postcheck_ledger']['path'],
        old_ledger_sha256=receipt['postcheck_ledger']['sha256'],
        controls=runner.DEFAULTS, receiver_xy_bng=runner.prior.RECEIVER,
        physical_cases=runner.prior.CASES, worker_budget=2,
        scope='direct_only_reflections_omitted', complete_physical_path_support=False,
        rigorous_noise_error_bound_db=None, field_validation=False,
        all_inclusive_noise_db=None, map_updated=False)


def prepare(output):
    started = time.perf_counter()
    rows = selected_rows()
    identities = {key(r) for r in rows}
    parents = [BASE, CORRECTED, CACHE, 'corrected_road_observation_review_v1',
        'development_road_observations_v1', 'ground_average_replay_v1', 'ground_average_review_v2',
        *runner.prior.PARENTS]
    output, hashes, codes = prepare_directory(output, parents)
    baseline_seeds = read(ROOT/'artifacts'/BASE/'seed_evaluations.json')
    corrected_seeds = read(ROOT/'artifacts'/CACHE/'seed_evaluations.json')
    historical = {identity: corrected_seeds.get(identity, baseline_seeds[identity]) for identity in identities}
    # The earlier full-history audit covers both selected development cases.
    development = read(ROOT/'artifacts/development_road_observations_v1/receipt.json')['case_counts']
    for identity in identities:
        if identity in development:
            assert identity in corrected_seeds
            assert len(corrected_seeds[identity]) == development[identity] == 2048
        else:
            prior = next(r for r in rows if key(r) == identity)
            assert prior['evaluation_count'] == len(baseline_seeds[identity])
        chosen = {point_key(n): n for n in historical[identity]}
        assert len(chosen) == len(historical[identity])
        for old in baseline_seeds[identity]:
            new = chosen[point_key(old)]
            assert old['xy_bng'] == new['xy_bng'] and old['native_profile_sha256'] == new['native_profile_sha256']
    (output/'historical_seeds.json').write_bytes(json_bytes(historical))
    (output/'seed_evaluations.json').write_bytes(json_bytes(historical))
    plan = [dict(link_id=r['link_id'], physical_case_id=r['physical_case_id'],
        minimum_initial_observations=r['minimum_initial_observations'], historical_observations=len(historical[key(r)]),
        source_length_m=r['source_length_m'], controls=bounded_controls(r['minimum_initial_observations'], len(historical[key(r)]))) for r in rows]
    protocol = protocol_common('budget_road_observations_v1', hashes)
    protocol.update(selected_identities=plan,
        selection='Exactly the 22 original and two corrected initial-partition budget holds.',
        observation_policy='Every retained historical location reevaluated natively with the latest validated physics; no old numerical result reused.',
        budget_policy='Initial five-node rules + all retained history + ten nodes for each of at most 512 refinements; 0.1dB, 4m, 0.01m unchanged.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    sources = {r['link_id']: r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    fresh = {}
    print(f'Reevaluating {sum(map(len, historical.values()))} retained historical locations for {len(rows)} cases', flush=True)
    with gzip.open(output/'point_observations.jsonl.gz', 'wt', compresslevel=1, encoding='utf-8') as stream:
        with ProcessPoolExecutor(max_workers=2, initializer=initialise, initargs=(str(output), 'prepare')) as pool:
            for i, result in enumerate(bounded(pool, [(sources[r['link_id']], r['physical_case_id']) for r in rows])):
                fresh[key(result)] = result['evaluations']
                write_nodes(stream, result)
                (output/'progress.json').write_bytes(json_bytes(dict(completed=i+1, selected=len(rows),
                    native_replayed=sum(map(len, fresh.values())), elapsed_seconds=time.perf_counter()-started)))
                print(f'History {i+1}/{len(rows)}: {result["link_id"]}, {result["evaluation_count"]} native replays', flush=True)
    (output/'seed_evaluations.json').write_bytes(json_bytes(fresh))
    (output/'report.json').write_bytes(json_bytes(dict(status='prepared_native_budget_observations',
        road_cases=len(rows), native_replayed=sum(map(len, fresh.values())),
        unknown_observations=sum(n['status']=='withheld' for nodes in fresh.values() for n in nodes),
        minimum_initial_observations=sum(r['minimum_initial_observations'] for r in rows),
        maximum_total_observations=sum(r['controls']['maximum_evaluations'] for r in plan),
        maximum_case_observations=max(r['controls']['maximum_evaluations'] for r in plan),
        elapsed_seconds=time.perf_counter()-started, map_updated=False, all_inclusive_noise_db=None)))
    finish(output, hashes, codes, 'budget-road-observations-')


def integrate(inputs, review, output):
    started = time.perf_counter()
    inputs, review = Path(inputs).resolve(), Path(review).resolve()
    helper.verified(inputs)
    helper.verified(review)
    audit = read(review/'report.json')
    assert audit['status'] == 'passed_independent_budget_observation_review'
    assert audit['input_inventory_sha256'] == file_hash(inputs/'inventory.json')
    old_protocol = read(inputs/'protocol.json')
    output, hashes, codes = prepare_directory(output, [inputs.name, review.name, *old_protocol['parent_inventory_sha256']])
    seeds = read(inputs/'seed_evaluations.json')
    (output/'seed_evaluations.json').write_bytes(json_bytes(seeds))
    protocol = protocol_common('budget_road_integration_v1', hashes)
    plan = old_protocol['selected_identities']
    protocol.update(selected_identities=plan, case_controls={key(r): r['controls'] for r in plan},
        budget_observation_directory=inputs.name, budget_observation_review_directory=review.name,
        finer_selection='Every selected case meeting the primary diagnostic.',
        selection=dict(kind='all_initial_partition_budget_holds', selected_cases=len(plan)))
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    sources = {r['link_id']: r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    original = {key(r): r for r in read(ROOT/'artifacts/conditional_road_integration_v4/road_contributions.json')['roads']}
    rows = []
    print(f'Integrating {len(plan)} budget-held road cases', flush=True)
    with gzip.open(output/'node_evaluations.jsonl.gz', 'wt', compresslevel=1, encoding='utf-8') as stream:
        with ProcessPoolExecutor(max_workers=2, initializer=initialise, initargs=(str(output), 'integrate')) as pool:
            for row in bounded(pool, [(sources[r['link_id']], r['physical_case_id']) for r in plan]):
                write_nodes(stream, row)
                old = original[key(row)]
                row['prior_worst_spacing_change_db'] = runner.worst_change(old)
                current = row['whole_road_relative_energy']
                row['change_from_prior_fine_db'] = None if current is None else {
                    state: {k: None if value is None or runner.receiver_metrics(np.asarray(old['whole_road_relative_energy'])[j])[k] is None
                            else value-runner.receiver_metrics(np.asarray(old['whole_road_relative_energy'])[j])[k]
                            for k, value in runner.receiver_metrics(np.asarray(current)[j]).items()}
                    for j, state in enumerate(('homogeneous', 'favourable'))}
                rows.append(row)
                (output/'progress.json').write_bytes(json_bytes(dict(completed=len(rows), selected=len(plan),
                    evaluations=sum(r['evaluation_count'] for r in rows), elapsed_seconds=time.perf_counter()-started,
                    terminations=dict(Counter(r['termination'] for r in rows)))))
                (output/'completed_roads.json').write_bytes(json_bytes(dict(roads=rows)))
                print(f'Primary {len(rows)}/{len(plan)}: {row["link_id"]} {row["termination"]}; {row["evaluation_count"]} observations', flush=True)
    (output/'refined_roads.json').write_bytes(json_bytes(dict(roads=rows)))
    replacement = {key(r): r for r in rows}
    ledger = read(ROOT/protocol['old_ledger_path'])
    for i, old in enumerate(ledger['roads']):
        new = replacement.get(key(old))
        if new is not None:
            ledger['roads'][i] = dict(link_id=new['link_id'], physical_case_id=new['physical_case_id'],
                integration_stage='adaptive_refined', integration_accuracy_outstanding=not new['diagnostic_tolerance_met'],
                status=new['status'], whole_road_relative_energy=new['whole_road_relative_energy'],
                finer_spacing_check_status='not_selected')
    ledger['summary'] = dict(selected_road_cases=len(rows),
        selected_diagnostic_met_cases=sum(r['diagnostic_tolerance_met'] for r in rows),
        other_ledger_rows_exactly_preserved=len(ledger['roads'])-len(rows))
    (output/'road_ledger.json').write_bytes(json_bytes(ledger))
    met = [r for r in rows if r['diagnostic_tolerance_met']]
    (output/'report.json').write_bytes(json_bytes(dict(
        status='completed_budget_road_primary_integration', selected_road_cases=len(rows),
        evaluations=sum(r['evaluation_count'] for r in rows), new_evaluations=sum(r['new_evaluation_count'] for r in rows),
        diagnostic_met_cases=len(met), new_unknown_cases=sum(r['unknown_positive_sample_observed'] for r in rows),
        terminations=dict(Counter(r['termination'] for r in rows)),
        maximum_met_indicator_db=max((r['indicator_db'] for r in met), default=None),
        maximum_met_offset_change_db=max((abs(v) for r in met for s in r['offset_change_db'].values() for v in s.values() if v is not None), default=None),
        elapsed_seconds=time.perf_counter()-started, all_inclusive_noise_db=None, map_updated=False)))
    finish(output, hashes, codes, 'budget-road-integration-')


def finer(inputs, review, output):
    started = time.perf_counter()
    inputs, review = Path(inputs).resolve(), Path(review).resolve()
    helper.verified(inputs)
    helper.verified(review)
    audit = read(review/'report.json')
    assert audit['status'] == 'passed_independent_adaptive_accounting_and_sampled_replay'
    assert audit['input_inventory_sha256'] == file_hash(inputs/'inventory.json')
    old_protocol = read(inputs/'protocol.json')
    output, hashes, codes = prepare_directory(output, [inputs.name, review.name, *old_protocol['parent_inventory_sha256']])
    rows = [r for r in read(inputs/'refined_roads.json')['roads'] if r['diagnostic_tolerance_met']]
    seeds = read(inputs/'seed_evaluations.json')
    (output/'seed_evaluations.json').write_bytes(json_bytes({key(r): [dict(seeds[key(r)][0])] for r in rows}))
    (output/'primary_roads.json').write_bytes(json_bytes(dict(roads=rows)))
    protocol = protocol_common('budget_road_spacing_check_v1', hashes)
    protocol.update(input_directory=inputs.name, input_inventory_sha256=file_hash(inputs/'inventory.json'), source_review=review.name,
        selected_identities=[dict(link_id=r['link_id'], physical_case_id=r['physical_case_id']) for r in rows],
        criterion_db=0.1, quadrature='Four positive equal-weight interior midpoint nodes per final primary leaf; all fresh native evaluations.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    sources = {r['link_id']: r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    checks = []
    with gzip.open(output/'node_evaluations.jsonl.gz', 'wt', compresslevel=1, encoding='utf-8') as stream:
        with ProcessPoolExecutor(max_workers=2, initializer=initialise, initargs=(str(output), 'finer')) as pool:
            for row in bounded(pool, [(sources[r['link_id']], r['physical_case_id']) for r in rows]):
                write_nodes(stream, row)
                checks.append(row)
                (output/'progress.json').write_bytes(json_bytes(dict(completed=len(checks), selected=len(rows),
                    evaluations=sum(r['evaluation_count'] for r in checks), elapsed_seconds=time.perf_counter()-started)))
                (output/'completed_checks.json').write_bytes(json_bytes(dict(roads=checks)))
                print(f'Finer {len(checks)}/{len(rows)}: {row["link_id"]} {row["status"]}; {row["maximum_absolute_change_db"]}', flush=True)
    (output/'road_checks.json').write_bytes(json_bytes(dict(roads=checks)))
    # Only replace the checked subset: preserve every earlier finer check.
    primary_ledger = read(inputs/'road_ledger.json')
    selected = {key(r) for r in rows}
    updated = recorder.postcheck_ledger([r for r in primary_ledger['roads'] if key(r) in selected], checks)
    replacements = {key(r): r for r in updated}
    primary_ledger['roads'] = [replacements.get(key(r), r) for r in primary_ledger['roads']]
    primary_ledger['summary'] = dict(selected_road_cases=len(read(inputs/'refined_roads.json')['roads']),
        selected_primary_met_cases=len(rows), selected_finer_passed_cases=sum(r['status']=='finer_spacing_check_passed' for r in checks),
        other_ledger_rows_exactly_preserved=1444-len(read(inputs/'refined_roads.json')['roads']))
    (output/'road_ledger.json').write_bytes(json_bytes(primary_ledger))
    (output/'report.json').write_bytes(json_bytes(dict(status='completed_budget_road_finer_spacing_check',
        selected_road_cases=len(checks), evaluations=sum(r['evaluation_count'] for r in checks),
        passed_cases=sum(r['status']=='finer_spacing_check_passed' for r in checks),
        maximum_known_change_db=max((r['maximum_absolute_change_db'] for r in checks if r['maximum_absolute_change_db'] is not None), default=None),
        elapsed_seconds=time.perf_counter()-started, all_inclusive_noise_db=None, map_updated=False)))
    finish(output, hashes, codes, 'budget-road-spacing-')


def initialise_probe():
    runner.compute = lambda value: dict(spawn_probe=value)


def spawn_probe():
    with ProcessPoolExecutor(max_workers=1, initializer=initialise_probe) as pool:
        assert list(bounded(pool, [1, 2, 3])) == [dict(spawn_probe=i) for i in (1, 2, 3)]
    print('Windows spawn and bounded scheduler passed', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('prepare', 'integrate', 'finer', 'spawn-probe'), required=True)
    parser.add_argument('--inputs')
    parser.add_argument('--review')
    parser.add_argument('--output')
    args = parser.parse_args()
    if args.stage == 'spawn-probe':
        spawn_probe()
    elif args.stage == 'prepare':
        prepare(args.output)
    elif args.stage == 'integrate':
        integrate(args.inputs, args.review, args.output)
    else:
        finer(args.inputs, args.review, args.output)
