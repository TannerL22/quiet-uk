"""Record regression, sealed-generation and narrowly scoped core-change evidence."""
import argparse
import ast
import importlib.util
import json
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
CHANGED=('terrain_profile.py','multiple_edge_boundary.py','sightline_boundary.py','favourable_propagation.py')


def normalise_adapter_additions(source):
    """Remove only the declared wrappers and explicit scene gate additions.

    Exact remaining AST equality checks the complete computational bodies,
    constants and previous signatures, rather than sampling equation outputs.
    """
    tree=ast.parse(source)
    tree.body=[n for n in tree.body if not isinstance(n,ast.ImportFrom) or n.module not in ('quiet_uk.propagation_geometry_cache','quiet_uk.conditional_scene_domain')]
    for node in ast.walk(tree):
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
            node.decorator_list=[d for d in node.decorator_list if not (isinstance(d,ast.Name) and d.id=='cached_geometry')]
            for i in reversed(range(len(node.args.kwonlyargs))):
                if node.args.kwonlyargs[i].arg=='conditional_input_policy':
                    del node.args.kwonlyargs[i];del node.args.kw_defaults[i]
        if isinstance(node,ast.If) and isinstance(node.test,ast.UnaryOp) and isinstance(node.test.op,ast.Not) and isinstance(node.test.operand,ast.Call) and isinstance(node.test.operand.func,ast.Name) and node.test.operand.func.id=='scene_supported':
            assert ast.dump(node.test.operand.args[0])==ast.dump(ast.Name(id='scene_kind',ctx=ast.Load()))
            assert len(node.test.operand.args)==2 and isinstance(node.test.operand.args[1],ast.Name) and node.test.operand.args[1].id=='conditional_input_policy'
            node.test=ast.parse("scene_kind not in ('synthetic','published_reference')",mode='eval').body
    return ast.dump(tree,include_attributes=False)


def run(inputs,review,junit,output):
    inputs,review,junit,output=map(lambda p:Path(p).resolve(),(inputs,review,junit,output))
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace receipt required')
    inventories={p.name:helper.verified(p) for p in (inputs,review)}
    audit=json.loads((review/'report.json').read_bytes());assert audit['input_inventory_sha256']==file_hash(inputs/'inventory.json')
    suites=list(ET.parse(junit).getroot().iter('testsuite'))
    tests={key:sum(int(s.get(key,0)) for s in suites) for key in ('tests','errors','failures','skipped')}
    assert tests['tests']>=902 and tests['errors']==tests['failures']==tests['skipped']==0
    prior_path=ROOT/'artifacts/physical_scene_assumption_validation_v1.json';prior=json.loads(prior_path.read_bytes())
    preserved={};changed={}
    prior_hashes=dict(prior['preserved_code_sha256'],**prior['code_sha256'])
    for name,digest in prior_hashes.items():
        current=ROOT/name
        if current.name not in CHANGED:
            assert file_hash(current)==digest,name;preserved[name]=digest;continue
        candidates=[ROOT/'artifacts'/generation/'code/src/quiet_uk'/current.name for generation in ('weather_checks_v2','weather_checks_v1','reflection_checks_v3','reflection_checks_v2','reflection_checks_v1','sightline_checks_v1','multiple_edge_checks_v1')]
        old=next((p for p in candidates if p.is_file() and file_hash(p)==digest),None)
        assert old is not None,'Missing matching prior frozen code: '+name
        assert normalise_adapter_additions(current.read_text())==ast.dump(ast.parse(old.read_text()),include_attributes=False),name
        changed[name]=dict(prior_sha256=digest,current_sha256=file_hash(current),matched_prior_snapshot=str(old.relative_to(ROOT)).replace('\\','/'),
            computational_AST_preserved=True,changes='Per-path pure geometry decorators; explicit conditional real input keyword/gate on H/F direct boundaries only.')
    code_paths=[ROOT/'src/quiet_uk'/n for n in (*CHANGED,'conditional_road_propagation.py','conditional_scene_domain.py','propagation_geometry_cache.py')]
    code_paths.extend([ROOT/'scripts'/n for n in ('89_integrate_conditional_roads.py','90_verify_conditional_road_integration.py','91_record_conditional_road_validation.py')])
    code_paths.append(ROOT/'tests/test_conditional_road_propagation.py')
    for path in code_paths:
        frozen=inputs/'code'/path.name
        if frozen.is_file():assert file_hash(path)==file_hash(frozen),'Executed implementation differs: '+path.name
    receipt=dict(status='passed_conditional_direct_road_implementation_validation',tests=tests,new_focused_tests=45,
        junit_sha256=file_hash(junit),prior_validation_sha256=file_hash(prior_path),preserved_prior_code_sha256=preserved,changed_core_modules=changed,
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):file_hash(p) for p in code_paths},
        generations={n:i['generation_id'] for n,i in inventories.items()},generation_inventory_sha256={p.name:file_hash(p/'inventory.json') for p in (inputs,review)},
        accounting_and_replay=audit,scope='Conditional direct-only receiver components integrated over complete retained road geometry; explicit finite-cell quadrature, unknown support and omitted paths.',
        refinement_convergence_proven=False,real_area_noise_validated=False,external_engine_executed=False,absolute_release_eligible=False,combined_road_noise_db=None,all_inclusive_noise_db=None)
    output.write_bytes(json_bytes(receipt));print(json.dumps(dict(output=str(output),tests=tests,byte_preserved_prior_files=len(preserved),scoped_core_changes=len(changed))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',default=str(ROOT/'artifacts/conditional_road_integration_v4'));parser.add_argument('--review',default=str(ROOT/'artifacts/conditional_road_integration_review_v1'))
    parser.add_argument('--junit',default=str(ROOT/'artifacts/conditional_road_validation_v1.xml'));parser.add_argument('--output',default=str(ROOT/'artifacts/conditional_road_validation_v1.json'))
    args=parser.parse_args();run(args.inputs,args.review,args.junit,args.output)
