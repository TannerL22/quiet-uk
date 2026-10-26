"""Link corrected integration, every finer check and the final no-fallback ledger."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
helper=module('corrected_receipt_inventory',ROOT/'scripts/57_acquire_ground_cover_sources.py')
ledger_rule=module('corrected_receipt_ledger_rule',ROOT/'scripts/94_record_adaptive_validation.py')
def read(path):return json.loads(Path(path).read_bytes())
def key(r):return (r['link_id'],r['physical_case_id'])

def run(observations,observation_review,inputs,review,finer,finer_review,junit,output,ledger_output):
    paths=list(map(lambda p:Path(p).resolve(),(observations,observation_review,inputs,review,finer,finer_review)))
    observations,observation_review,inputs,review,finer,finer_review=paths;junit=Path(junit).resolve();output,ledger_output=Path(output).resolve(),Path(ledger_output).resolve()
    if output==ledger_output or any(p.exists() or not p.is_relative_to(ROOT/'artifacts') for p in (output,ledger_output)):raise ValueError('Distinct fresh workspace receipt and ledger required')
    inventories={p.name:helper.verified(p) for p in paths}
    statuses=['passed_corrected_observation_accounting','passed_independent_adaptive_accounting_and_sampled_replay','passed_independent_finer_spacing_accounting']
    for source,audit,status in zip((observations,inputs,finer),(observation_review,review,finer_review),statuses):
        report=read(audit/'report.json');assert report['status']==status and report['input_inventory_sha256']==file_hash(source/'inventory.json')
    assert read(inputs/'protocol.json')['corrected_observation_directory']==observations.name
    assert read(finer/'protocol.json')['input_inventory_sha256']==file_hash(inputs/'inventory.json')
    for p in (observations,inputs,finer):
        for code in (p/'code').glob('*.py'):
            live=ROOT/('scripts' if code.name[:2].isdigit() else 'src/quiet_uk')/code.name
            assert file_hash(live)==file_hash(code),'Executed implementation changed: '+code.name
    for p,script in ((observation_review,'102_verify_corrected_observations.py'),(review,'93_verify_adaptive_roads.py'),(finer_review,'96_verify_refined_spacing_check.py')):
        assert file_hash(p/script)==file_hash(ROOT/'scripts'/script)
    xml=ET.parse(junit).getroot();tests={k:sum(int(s.attrib.get(k,0)) for s in xml.iter('testsuite')) for k in ('tests','errors','failures','skipped')}
    assert tests['tests']==1013 and not any(tests[k] for k in ('errors','failures','skipped'))
    rows=read(inputs/'refined_roads.json')['roads'];checks=read(finer/'road_checks.json')['roads'];checked={key(r):r for r in checks}
    assert all(key(r) in checked for r in rows if r['diagnostic_tolerance_met'])
    base=read(inputs/'road_ledger.json')['roads'];final=ledger_rule.postcheck_ledger(base,checks)
    affected={key(r) for r in rows};survivors=[r for r in final if key(r) in affected and r['whole_road_relative_energy'] is not None and not r['integration_accuracy_outstanding']]
    assert all(r['finer_spacing_check_status']=='finer_spacing_check_passed' for r in survivors)
    summary=dict(affected_cases=len(rows),primary_met_cases=sum(r['diagnostic_tolerance_met'] for r in rows),affected_cases_passing_finer_check=len(survivors),
        affected_cases_still_withheld=len(rows)-len(survivors),finer_cases_including_controls=len(checks),finer_passed_including_controls=sum(r['status']=='finer_spacing_check_passed' for r in checks),
        newly_withheld_by_finer_check=sum(key(r) in affected and r['status']=='withheld_after_finer_spacing_check' for r in final),road_ledger_cases=len(final))
    ledger=dict(roads=final,summary=summary,parent_inventory_sha256={p.name:file_hash(p/'inventory.json') for p in paths},
        rule='All newly primary-met affected cases receive the finer check; any failed finer check withholds the earlier numeric value. No partial or older integral is a fallback.',
        field_validation=False,complete_physical_path_support=False,refinement_convergence_proven=False,rigorous_noise_error_bound_db=None,all_inclusive_noise_db=None,map_updated=False)
    ledger_output.write_bytes(json_bytes(ledger))
    code_paths=[Path(__file__),*[ROOT/'scripts'/n for n in ('101_rerun_corrected_roads.py','102_verify_corrected_observations.py','104_capture_corrected_observation_prefix.py','106_launch_corrected_road_stage.py','93_verify_adaptive_roads.py','96_verify_refined_spacing_check.py','94_record_adaptive_validation.py')],ROOT/'tests/test_corrected_road_reuse.py']
    receipt=dict(status='passed_corrected_road_integration_validation',tests=tests,new_reuse_tests=8,junit_sha256=file_hash(junit),
        generations={name:i['generation_id'] for name,i in inventories.items()},generation_inventory_sha256={p.name:file_hash(p/'inventory.json') for p in paths},
        model_revision_validation_sha256=file_hash(ROOT/'artifacts/curved_envelope_validation_v1.json'),summary=summary,
        observation_summary=read(observations/'report.json'),primary_summary=read(inputs/'report.json'),finer_summary=read(finer/'report.json'),
        postcheck_ledger=dict(path=str(ledger_output.relative_to(ROOT)).replace('\\','/'),sha256=file_hash(ledger_output)),
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):file_hash(p) for p in code_paths},
        scope='All 59 previously circular-envelope-held road cases. Independently validated full history, native unknown reevaluation, corrected positive integration and every newly primary-met case checked at half spacing.',
        all_retained_roads_refined=False,field_validation=False,reflections_included=False,rigorous_noise_error_bound_db=None,all_inclusive_noise_db=None,map_updated=False)
    output.write_bytes(json_bytes(receipt));print(json.dumps(dict(output=str(output),tests=tests,summary=summary)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('observations','observation-review','inputs','review','finer','finer-review','junit','output','ledger-output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();run(args.observations,args.observation_review,args.inputs,args.review,args.finer,args.finer_review,args.junit,args.output,args.ledger_output)
