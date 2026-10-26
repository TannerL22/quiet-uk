"""Run integration, independent review, finer checks and the final receipt.

Each stage must pass before the next starts. This is a foreground numerical
job which can continue while the conversational agent is paused.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes

spec = importlib.util.spec_from_file_location('budget_completion_provenance', ROOT/'scripts/110_verify_budget_road_observations.py')
verifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verifier)


def read(path):
    return json.loads(Path(path).read_bytes())


def tests(path):
    root = ET.parse(path).getroot()
    names = set()
    for case in root.iter('testcase'):
        assert not any(case.find(k) is not None for k in ('failure', 'error', 'skipped'))
        identity = case.attrib.get('classname'), case.attrib['name']
        assert identity not in names
        names.add(identity)
    assert names
    return names


def execute(script, *arguments):
    subprocess.run([sys.executable, '-u', str(ROOT/'scripts'/script), *map(str, arguments)], cwd=ROOT, check=True)


def run(observations, observation_review, prefix, junit, output, recovery=None):
    observations, observation_review = Path(observations).resolve(), Path(observation_review).resolve()
    output, junit = Path(output).resolve(), Path(junit).resolve()
    if not prefix or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in prefix):
        raise ValueError('Simple lowercase artifact prefix required')
    primary = ROOT/'artifacts'/(prefix+'_integration_v1')
    primary_review = ROOT/'artifacts'/(prefix+'_integration_review_v1')
    finer = ROOT/'artifacts'/(prefix+'_spacing_check_v1')
    finer_review = ROOT/'artifacts'/(prefix+'_spacing_review_v1')
    for path in (primary, primary_review, finer, finer_review, output):
        if path.exists() or not path.is_relative_to(ROOT/'artifacts'):
            raise ValueError('Distinct fresh workspace outputs required')
    verifier.verified_inputs(observations)
    verifier.review.helper.verified(observation_review)
    reviewed = read(observation_review/'report.json')
    assert reviewed['status'] == 'passed_independent_budget_observation_review'
    assert reviewed['input_inventory_sha256'] == file_hash(observations/'inventory.json')
    old_receipt = read(verifier.MODEL_RECEIPT)
    prior_junit = ROOT/'artifacts/ground_average_validation_v1.xml'
    assert file_hash(prior_junit) == old_receipt['junit_sha256']
    prior_names = tests(prior_junit)
    new_names = tests(junit)
    assert len(prior_names) == old_receipt['tests']['tests'] == 1050
    assert len(new_names) == 68 and len(new_names-prior_names) == 9
    assert all(name[0] == 'tests.test_budget_road_controller' for name in new_names-prior_names)
    paths = [Path(__file__), ROOT/'tests/test_budget_road_controller.py',
        *[ROOT/'scripts'/n for n in ('109_integrate_budget_held_roads.py',
            '110_verify_budget_road_observations.py', '93_verify_adaptive_roads.py',
            '96_verify_refined_spacing_check.py')]]
    if recovery:
        recovery=Path(recovery).resolve()
        verifier.review.helper.verified(recovery)
        assert read(recovery/'protocol.json')['kind']=='recovered_budget_primary_prefix_v1'
        assert read(recovery/'protocol.json')['model_validation_sha256']==file_hash(verifier.MODEL_RECEIPT)
        paths.append(ROOT/'scripts/112_resume_budget_road_checks.py')
    hashes = {p: file_hash(p) for p in paths}
    junit_hash = file_hash(junit)
    prior_junit_hash = file_hash(prior_junit)
    prior_validation_hash = file_hash(verifier.MODEL_RECEIPT)
    # Current source hashes are checked by every producer and independent review.
    stage_script='112_resume_budget_road_checks.py' if recovery else '109_integrate_budget_held_roads.py'
    recovery_args=['--recovery',recovery] if recovery else []
    execute(stage_script, '--stage', 'integrate', '--inputs', observations,
            '--review', observation_review, '--output', primary,*recovery_args)
    execute('93_verify_adaptive_roads.py', '--inputs', primary, '--output', primary_review)
    execute(stage_script, '--stage', 'finer', '--inputs', primary,
            '--review', primary_review, '--output', finer,*recovery_args)
    execute('96_verify_refined_spacing_check.py', '--inputs', finer, '--output', finer_review)
    assert all(file_hash(p) == sha for p, sha in hashes.items()), 'Controller or review changed during the study'
    assert file_hash(junit) == junit_hash and file_hash(prior_junit) == prior_junit_hash
    assert file_hash(verifier.MODEL_RECEIPT) == prior_validation_hash
    generations = [observations, observation_review, primary, primary_review, finer, finer_review]
    inventories = {p.name: verifier.review.helper.verified(p) for p in generations}
    final_ledger = read(finer/'road_ledger.json')
    original_ledger = read(ROOT/old_receipt['postcheck_ledger']['path'])
    assert len(final_ledger['roads']) == len(original_ledger['roads']) == 1444
    primary_rows = read(primary/'refined_roads.json')['roads']
    selected = {(r['link_id'], r['physical_case_id']) for r in primary_rows}
    assert len(selected) == 24
    untouched = 0
    for old, new in zip(original_ledger['roads'], final_ledger['roads']):
        assert (old['link_id'], old['physical_case_id']) == (new['link_id'], new['physical_case_id'])
        if (old['link_id'], old['physical_case_id']) not in selected:
            assert old == new
            untouched += 1
    assert untouched == 1420
    checks = read(finer/'road_checks.json')['roads']
    passed = sum(r['status'] == 'finer_spacing_check_passed' for r in checks)
    receipt = dict(status='passed_budget_road_integration_validation',
        prior_model_validation_sha256=prior_validation_hash,
        generations={name: inv['generation_id'] for name, inv in inventories.items()},
        generation_inventory_sha256={p.name: file_hash(p/'inventory.json') for p in generations},
        tests=dict(preserved_model_tests=1050, controller_and_adaptive_tests=68,
                   new_unique_tests=9, total_unique_tests=len(prior_names|new_names)),
        junit_sha256={str(p.relative_to(ROOT)).replace('\\', '/'): file_hash(p) for p in (prior_junit, junit)},
        code_sha256={**old_receipt['code_sha256'], **{str(p.relative_to(ROOT)).replace('\\', '/'): sha for p, sha in hashes.items()}},
        summary=dict(selected_road_cases=24, selected_primary_met_cases=sum(r['diagnostic_tolerance_met'] for r in primary_rows),
            selected_passing_both_checks=passed, selected_still_withheld=24-passed,
            other_ledger_rows_exactly_preserved=untouched,
            finer_passed_road_cases_total=sum(r.get('finer_spacing_check_status') == 'finer_spacing_check_passed' for r in final_ledger['roads'])),
        postcheck_ledger=dict(path=str((finer/'road_ledger.json').relative_to(ROOT)).replace('\\', '/'), sha256=file_hash(finer/'road_ledger.json')),
        primary=read(primary/'report.json'), primary_review=read(primary_review/'report.json'),
        finer=read(finer/'report.json'), finer_review=read(finer_review/'report.json'),
        field_validation=False, complete_physical_path_support=False,
        rigorous_noise_error_bound_db=None, all_inclusive_noise_db=None, map_updated=False)
    if recovery:
        receipt['restart_recovery']=dict(directory=recovery.name,
            inventory_sha256=file_hash(recovery/'inventory.json'),report=read(recovery/'report.json'))
    output.write_bytes(json_bytes(receipt))
    print(json.dumps(dict(receipt=str(output), summary=receipt['summary'], tests=receipt['tests'])), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--observations', required=True)
    parser.add_argument('--observation-review', required=True)
    parser.add_argument('--prefix', default='budget_road')
    parser.add_argument('--junit', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--recovery')
    args = parser.parse_args()
    run(args.observations, args.observation_review, args.prefix, args.junit, args.output,args.recovery)
