"""Finish independently reviewed checks from a sealed complete primary replay.

This continuation does not rerun or modify the existing native observations.
Every review and output must be fresh; all original withholding rules remain.
"""
import importlib.util
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('remaining_completion',ROOT/'scripts/113_complete_remaining_road_checks.py')
controller=importlib.util.module_from_spec(spec);sys.modules[spec.name]=controller;spec.loader.exec_module(controller)
pipeline=controller.pipeline
read,file_hash,json_bytes=pipeline.read,pipeline.file_hash,pipeline.json_bytes


def run():
    controller.configure()
    model=controller.latest()
    p=ROOT/'artifacts/remaining_road_primary_replay_v1'
    a=ROOT/'artifacts/remaining_road_primary_review_v1'
    f=ROOT/'artifacts/remaining_road_spacing_check_v1'
    v=ROOT/'artifacts/remaining_road_spacing_review_v1'
    receipt_path=ROOT/'artifacts/remaining_road_validation_v1.json'
    for path in (a,f,v,receipt_path):
        if path.exists():raise ValueError('Fresh review and continuation outputs required')
    pipeline.helper.verified(p)
    assert read(p/'report.json')['status']=='completed_remaining_road_primary_native_replay'
    assert read(p/'report.json')['evaluations']==6016
    assert read(p/'protocol.json')['latest_validation_sha256']==file_hash(controller.RECEIPT)
    for relative,sha in read(p/'provenance.json')['code_sha256'].items():assert file_hash(ROOT/relative)==sha
    paths=[Path(__file__),*[ROOT/'scripts'/n for n in ('113_complete_remaining_road_checks.py','114_verify_remaining_road_checks.py')],ROOT/'tests/test_remaining_road_controller.py']
    hashes={path:file_hash(path) for path in paths}
    junit=ROOT/'artifacts/remaining_road_controller_tests_v1.xml';junit_hash=file_hash(junit)
    tests=list(ET.parse(junit).getroot().iter('testcase'))
    assert len(tests)==7 and all(not any(c.find(k) is not None for k in ('failure','error','skipped')) for c in tests)
    verifier=ROOT/'scripts/114_verify_remaining_road_checks.py'
    subprocess.run([sys.executable,'-u',str(verifier),'--stage','primary','--inputs',str(p),'--output',str(a)],cwd=ROOT,check=True)
    pipeline.finer(p,a,f)
    subprocess.run([sys.executable,'-u',str(verifier),'--stage','finer','--inputs',str(f),'--output',str(v)],cwd=ROOT,check=True)
    controller.latest()
    assert all(file_hash(path)==sha for path,sha in hashes.items())
    assert file_hash(junit)==junit_hash
    generations={path.name:pipeline.helper.verified(path) for path in (p,a,f,v)}
    ledger=read(f/'road_ledger.json')['roads'];checks=read(f/'road_checks.json')['roads']
    summary=dict(selected_cases=14,selected_passing_both_checks=sum(r['status']=='finer_spacing_check_passed' for r in checks),
        selected_withheld=sum(r['status']!='finer_spacing_check_passed' for r in checks),
        total_finer_passed_cases=sum(r.get('finer_spacing_check_status')=='finer_spacing_check_passed' for r in ledger),
        primary_met_cases_without_finer_check=sum(not r['integration_accuracy_outstanding'] and r.get('finer_spacing_check_status')=='not_selected' for r in ledger),
        other_ledger_entries_exactly_preserved=1430)
    receipt_path.write_bytes(json_bytes(dict(status='passed_remaining_road_check_validation',
        prior_validation_sha256=file_hash(controller.RECEIPT),summary=summary,
        generations={n:inv['generation_id'] for n,inv in generations.items()},
        generation_inventory_sha256={path.name:file_hash(path/'inventory.json') for path in (p,a,f,v)},
        code_sha256={**model['code_sha256'],**{str(path.relative_to(ROOT)).replace('\\','/'):sha for path,sha in hashes.items()}},
        tests=dict(preserved_prior_unique_tests=model['tests']['total_unique_tests'],new_focused_tests=7,total_unique_tests=model['tests']['total_unique_tests']+7),
        junit_sha256={str(junit.relative_to(ROOT)).replace('\\','/'):junit_hash},
        postcheck_ledger=dict(path=str((f/'road_ledger.json').relative_to(ROOT)).replace('\\','/'),sha256=file_hash(f/'road_ledger.json')),
        primary=read(p/'report.json'),finer=read(f/'report.json'),
        continuation='Sealed primary replay reused after correcting only pair-rule arithmetic association in the independent accounting checker; tolerances unchanged.',
        field_validation=False,complete_physical_path_support=False,rigorous_noise_error_bound_db=None,all_inclusive_noise_db=None,map_updated=False)))
    print(json_bytes(dict(receipt=str(receipt_path),summary=summary)).decode(),flush=True)


if __name__=='__main__':run()
