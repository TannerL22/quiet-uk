"""Seal completed point observations from an interrupted development run.

No road integrals or run-completion claim. Inputs and executed physics snapshots
are retained; a subsequent independent review checks every reused point product.
"""
import gzip,hashlib,importlib.util,json,math,shutil,sys
from pathlib import Path
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def read(path):return json.loads(Path(path).read_bytes())
def run():
    development=ROOT/'artifacts/adaptive_road_integration_v2';output=ROOT/'artifacts/development_road_observations_v2'
    if output.exists():raise ValueError('Fresh observation generation required')
    protocol=read(development/'protocol.json');progress=read(development/'progress.json')
    selected=protocol['selected_identities'][:progress['completed']];keys={(r['link_id'],r['physical_case_id']) for r in selected}
    for n,sha in protocol['parent_inventory_sha256'].items():
        parent=ROOT/'artifacts'/n;helper.verified(parent);assert file_hash(parent/'inventory.json')==sha
    old=ROOT/'artifacts/conditional_road_integration_v4/code'
    for path in old.glob('*.py'):
        live=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(live)==file_hash(path)
    print('Parents and original physics verified; capturing completed observations',flush=True)
    output.mkdir();counts=Counter();total=0
    with (output/'point_evaluations.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0,compresslevel=1) as target:
            try:
                with gzip.open(development/'node_evaluations.jsonl.gz','rt') as source:
                    for line in source:
                        node=json.loads(line);key=(node['link_id'],node['physical_case_id'])
                        if key not in keys:break
                        assert node['node_id']==counts[key];counts[key]+=1;total+=1
                        target.write(line.encode())
                        if total%1000==0:target.flush();print(f'Captured {total} observations',flush=True)
            except EOFError:pass
    assert total==progress['evaluations'] and set(counts)==keys
    code=output/'code';code.mkdir()
    paths=[*(ROOT/'src/quiet_uk').glob('*.py'),*[ROOT/'scripts'/n for n in ('92_refine_conditional_roads.py','89_integrate_conditional_roads.py','57_acquire_ground_cover_sources.py','81_build_real_road_scenes.py','97_capture_development_road_observations.py')]]
    for path in paths:shutil.copyfile(path,code/path.name)
    (output/'receipt.json').write_bytes(json_bytes(dict(scope='Completed point observations only from interrupted development run; no accepted road integrals.',
        development_directory=development.name,completed_case_count=len(keys),point_observations=total,
        case_counts={k[0]+'|'+k[1]:v for k,v in counts.items()},parent_inventory_sha256=protocol['parent_inventory_sha256'],
        development_protocol_sha256=file_hash(development/'protocol.json'),development_progress_sha256=file_hash(development/'progress.json'),
        working_physics_byte_identical_to_prior_sealed_generation=True,code_sha256={p.name:file_hash(p) for p in code.glob('*.py')},
        independent_accounting_required_before_result_acceptance=True,field_validation=False,all_inclusive_noise_db=None)))
    shutil.copyfile(development/'protocol.json',output/'development_protocol.json');shutil.copyfile(development/'progress.json',output/'development_progress.json')
    print(json.dumps(dict(generation_id=helper.seal(output,'development-road-observations-'),point_observations=total,completed_cases=len(keys))),flush=True)
if __name__=='__main__':run()
