"""Link unchanged prior physics, regression tests and sealed refinement reviews."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def read(path):return json.loads(Path(path).read_bytes())

def postcheck_ledger(primary_rows,checks):
    """Carry finer-check rejection into the complete ledger without fallback."""
    identity=lambda row:(row['link_id'],row['physical_case_id'])
    original={identity(row):row for row in primary_rows}
    checked={identity(row):row for row in checks}
    if len(original)!=len(primary_rows) or len(checked)!=len(checks):
        raise ValueError('Duplicate road/case identity')
    if not checked.keys()<=original.keys():raise ValueError('Finer check outside primary ledger')
    rows=[]
    for raw in primary_rows:
        row=dict(raw);check=checked.get(identity(row))
        row['finer_spacing_check_status']='not_selected' if check is None else check['status']
        if check is not None:
            if raw['whole_road_relative_energy'] is None or raw['integration_accuracy_outstanding']:
                raise ValueError('Finer check must start from a primary diagnostic met case')
            maximum=check['maximum_absolute_change_db']
            passed=(not check['unknown_reason_counts'] and check['whole_road_relative_energy'] is not None
                and maximum is not None and math.isfinite(maximum) and 0<=maximum<=.1)
            expected='finer_spacing_check_passed' if passed else 'finer_spacing_check_withheld_or_changed'
            if check['status']!=expected:raise ValueError('Contradictory finer-check outcome')
            row['finer_spacing_maximum_absolute_change_db']=maximum
            row['finer_spacing_unknown_reason_counts']=check['unknown_reason_counts']
            if not passed:
                row.update(whole_road_relative_energy=None,integration_accuracy_outstanding=True,
                    status='withheld_after_finer_spacing_check',integration_stage='adaptive_refined_finer_check_withheld')
        rows.append(row)
    return rows

def run(inputs,review,confirmation,confirmation_review,junits,output,ledger_output):
    paths=[Path(p).resolve() for p in (inputs,review,confirmation,confirmation_review)];output=Path(output).resolve()
    ledger_output=Path(ledger_output).resolve()
    if output==ledger_output or any(p.exists() or not p.is_relative_to(ROOT/'artifacts') for p in (output,ledger_output)):
        raise ValueError('Distinct fresh workspace receipt and ledger required')
    inventories={p.name:helper.verified(p) for p in paths}
    primary,audit,check,check_audit=paths
    assert read(audit/'report.json')['input_inventory_sha256']==file_hash(primary/'inventory.json')
    assert read(check/'protocol.json')['input_inventory_sha256']==file_hash(primary/'inventory.json')
    assert read(check_audit/'report.json')['input_inventory_sha256']==file_hash(check/'inventory.json')
    assert read(audit/'report.json')['status']=='passed_independent_adaptive_accounting_and_sampled_replay'
    assert read(check_audit/'report.json')['status']=='passed_independent_finer_spacing_accounting'
    prior_path=ROOT/'artifacts/conditional_road_validation_v1.json';prior=read(prior_path);preserved={}
    for relative,sha in prior['code_sha256'].items():
        path=ROOT/relative;assert file_hash(path)==sha,'Prior implementation changed: '+relative
        preserved[relative]=sha
    tests=dict(tests=0,errors=0,failures=0,skipped=0);xml_hashes={}
    names=set()
    for filename in junits:
        path=Path(filename).resolve();root=ET.parse(path).getroot();xml_hashes[path.name]=file_hash(path)
        for suite in root.iter('testsuite'):
            for key in tests:tests[key]+=int(suite.attrib.get(key,0))
        for case in root.iter('testcase'):
            key=(case.attrib.get('classname'),case.attrib['name']);assert key not in names,'Duplicate test receipt';names.add(key)
    assert tests['tests']>=961 and not any(tests[k] for k in ('errors','failures','skipped'))
    code_paths=[ROOT/'src/quiet_uk/adaptive_road_integration.py',*[ROOT/'scripts'/name for name in ('92_refine_conditional_roads.py','93_verify_adaptive_roads.py','94_record_adaptive_validation.py','95_check_refined_road_spacing.py','96_verify_refined_spacing_check.py')],
        ROOT/'tests/test_adaptive_road_integration.py',ROOT/'tests/test_adaptive_spacing_check.py',ROOT/'tests/test_adaptive_validation.py']
    for path in code_paths:
        for generation in (primary,check):
            frozen=generation/'code'/path.name
            if frozen.is_file():assert file_hash(path)==file_hash(frozen),'Executed implementation changed'
    original_rows=read(primary/'road_ledger.json')['roads'];check_rows=read(check/'road_checks.json')['roads']
    final_rows=postcheck_ledger(original_rows,check_rows)
    summary=dict(road_cases=len(final_rows),finer_spacing_passed_cases=sum(r['finer_spacing_check_status']=='finer_spacing_check_passed' for r in final_rows),
        newly_withheld_after_finer_check=sum(r['status']=='withheld_after_finer_spacing_check' for r in final_rows),
        primary_met_cases_without_finer_check=sum(not r['integration_accuracy_outstanding'] and r['finer_spacing_check_status']=='not_selected' for r in final_rows),
        prior_finite_grid_only_cases=sum(r['integration_stage']=='prior_finite_grid_only' for r in final_rows),
        primary_met_cases_surviving_finer_check=sum(not r['integration_accuracy_outstanding'] and r['integration_stage']=='adaptive_refined' for r in final_rows))
    ledger=dict(roads=final_rows,summary=summary,parent_inventory_sha256={p.name:file_hash(p/'inventory.json') for p in paths},
        rule='Every failed finer check withholds the whole road; unselected and passing cases retain their primary energy. No earlier value or partial finer sum supplies a fallback.',
        refinement_convergence_proven=False,real_area_validated=False,complete_physical_path_support=False,all_inclusive_noise_db=None,map_updated=False)
    ledger_output.write_bytes(json_bytes(ledger))
    receipt=dict(status='passed_adaptive_road_integration_validation',tests=tests,new_focused_tests=59,junit_sha256=xml_hashes,
        prior_validation_sha256=file_hash(prior_path),preserved_prior_code_sha256=preserved,
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):file_hash(p) for p in code_paths},
        generation_inventory_sha256={p.name:file_hash(p/'inventory.json') for p in paths},generations={name:i['generation_id'] for name,i in inventories.items()},
        primary_summary=read(primary/'report.json'),independent_accounting=read(audit/'report.json'),finer_spacing_summary=read(check/'report.json'),finer_spacing_accounting=read(check_audit/'report.json'),
        postcheck_ledger=dict(path=str(ledger_output.relative_to(ROOT)).replace('\\','/'),sha256=file_hash(ledger_output),summary=summary),
        scope='Previously unstable retained road cases; bounded geographic partition, adaptive positive quadrature and explicit numerical/physical withholding. Three selected settled cases checked at half spacing.',
        all_retained_roads_refined=False,refinement_convergence_proven=False,diagnostic_tolerance_is_rigorous_error_bound=False,
        real_area_noise_validated=False,complete_physical_path_support=False,all_inclusive_noise_db=None,map_updated=False)
    output.write_bytes(json_bytes(receipt));print(json.dumps(dict(output=str(output),tests=tests,preserved_prior_files=len(preserved),postcheck_ledger=summary)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','review','confirmation','confirmation-review','output','ledger-output'):parser.add_argument('--'+name,required=True)
    parser.add_argument('--junit',action='append',required=True)
    args=parser.parse_args();run(args.inputs,args.review,args.confirmation,args.confirmation_review,args.junit,args.output,args.ledger_output)
