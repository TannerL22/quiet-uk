"""Record reviewed hull repair, full regression evidence and unchanged ledgers."""
import argparse
import ast
import copy
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('curved_validation_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def read(path):return json.loads(Path(path).read_bytes())

def numerical_functions_preserved(original,current):
    old={n.name:n for n in ast.parse(Path(original).read_text()).body if isinstance(n,ast.FunctionDef)}
    new={n.name:n for n in ast.parse(Path(current).read_text()).body if isinstance(n,ast.FunctionDef)}
    assert old.keys()==new.keys()
    boundary=copy.deepcopy(new['favourable_boundary']);removed=0
    for node in ast.walk(boundary):
        if isinstance(node,ast.Call):
            kept=[kw for kw in node.keywords if kw.arg!='circular_envelope_algorithm']
            removed+=len(node.keywords)-len(kept);node.keywords=kept
        if isinstance(node,ast.Dict):
            pairs=[(k,v) for k,v in zip(node.keys,node.values) if not isinstance(k,ast.Constant) or k.value!='circular_envelope_algorithm']
            removed+=len(node.keys)-len(pairs);node.keys=[k for k,v in pairs];node.values=[v for k,v in pairs]
    assert removed==1,'Only the declared diagnostic label may differ outside the selector'
    new['favourable_boundary']=boundary
    same=[]
    for name in old.keys()-{'circular_envelope'}:
        assert ast.dump(old[name])==ast.dump(new[name]),'Unrelated numerical function changed: '+name
        same.append(name)
    return sorted(same)

def run(inputs,review,junit,output):
    inputs,review,junit,output=map(lambda p:Path(p).resolve(),(inputs,review,junit,output))
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace validation receipt required')
    helper.verified(inputs);helper.verified(review)
    report=read(review/'report.json');assert report['status']=='passed_independent_curved_envelope_geometry_and_conditional_replay'
    assert report['input_inventory_sha256']==file_hash(inputs/'inventory.json')
    assert report['counts']['independent_envelopes_checked']==report['counts']['numeric_witnesses_recovered']==65
    assert report['counts']['paired_witness_octaves_replayed']==520
    assert report['counts']['prior_numeric_point_chains_unchanged']==23958
    assert report['counts']['prior_numeric_point_octaves_replayed']==96
    for path in (review/'code').glob('*.py'):
        current=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(path)==file_hash(current),'Reviewed implementation changed'
    unchanged={}
    for path in (inputs/'original_code').glob('*.py'):
        if path.name=='favourable_propagation.py':continue
        current=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(path)==file_hash(current),'Unrelated original implementation changed'
        unchanged[str(current.relative_to(ROOT)).replace('\\','/')]=file_hash(current)
    numerical=numerical_functions_preserved(inputs/'original_code/favourable_propagation.py',ROOT/'src/quiet_uk/favourable_propagation.py')
    suites=ET.parse(junit).getroot();totals={k:sum(int(s.attrib.get(k,0)) for s in suites.iter('testsuite')) for k in ('tests','errors','failures','skipped')}
    assert totals['tests']==1005 and not any(totals[k] for k in ('errors','failures','skipped'))
    prior=ROOT/'artifacts/adaptive_road_validation_v1.json';prior_receipt=read(prior)
    ledger=ROOT/prior_receipt['postcheck_ledger']['path'];assert file_hash(ledger)==prior_receipt['postcheck_ledger']['sha256']
    scripts=[Path(__file__),ROOT/'scripts/98_audit_curved_envelope_failures.py',ROOT/'scripts/99_verify_curved_envelope_fix.py',ROOT/'tests/test_circular_envelope_support.py',ROOT/'src/quiet_uk/favourable_propagation.py']
    result=dict(status='passed_curved_envelope_fix_validation',tests=totals,new_focused_tests=44,junit_sha256=file_hash(junit),
        witness_generation=read(inputs/'inventory.json')['generation_id'],review_generation=read(review/'inventory.json')['generation_id'],
        witness_inventory_sha256=file_hash(inputs/'inventory.json'),review_inventory_sha256=file_hash(review/'inventory.json'),review_summary=report,
        prior_validation_sha256=file_hash(prior),unchanged_prior_ledger_sha256=file_hash(ledger),
        unchanged_original_code_sha256=unchanged,unchanged_numerical_functions=numerical,
        intentional_change='Only circular-envelope candidate selection plus its diagnostic label; straight upper support hull removes noncontrolling corners before minor-arc validation.',
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):file_hash(p) for p in scripts},
        method_source=dict(parent_directory='weather_sources_v1',file='eu-2021-amendment.pdf',sha256=file_hash(ROOT/'artifacts/weather_sources_v1/eu-2021-amendment.pdf'),
            page=7,url='https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32021L1226',
            interpretation='Upper graph arcs and downward junctions give a concave majorant; points below the straight upper hull cannot control it. This reduction is a derived implementation argument, not an extra prescribed equation.'),
        scope='Recorded failed point geometry and conditional attenuation, every previously numeric point chain, sampled numerical parity and full related regression suite.',
        new_road_integrals_computed=False,previous_ledgers_modified=False,reflections_included=False,field_validation=False,all_inclusive_noise_db=None,map_updated=False)
    output.write_bytes(json_bytes(result));print(json.dumps(dict(output=str(output),tests=totals,unchanged_numerical_functions=numerical,unchanged_original_files=len(unchanged))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('inputs','review','junit','output'):parser.add_argument('--'+name,required=True)
    args=parser.parse_args();run(args.inputs,args.review,args.junit,args.output)
