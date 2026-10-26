"""Preserve completed point observations from an interrupted cache preparation.

This is recovery evidence, not an independently reviewed cache or road integral.
The subsequent observation auditor checks every original record and physics.
"""
import argparse
from collections import Counter
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('corrected_prefix_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace recovery required')
    protocol=json.loads((inputs/'protocol.json').read_bytes())
    for name,sha in protocol['parent_inventory_sha256'].items():
        helper.verified(ROOT/'artifacts'/name);assert file_hash(ROOT/'artifacts'/name/'inventory.json')==sha
    assert file_hash(ROOT/'artifacts/curved_envelope_validation_v1.json')==protocol['model_validation_sha256']
    output.mkdir(parents=True);counts=Counter();seen=set()
    with gzip.open(inputs/'point_observations.jsonl.gz','rt') as stream:
        for line in stream:
            node=json.loads(line);assert node['status']=='conditional_research' and node['model_reuse']['kind']=='numeric_same_chain_verified'
            identity=(node['link_id'],node['physical_case_id'],node['source_cell_id'],node['fraction']);assert identity not in seen;seen.add(identity)
            counts[node['link_id']+'|'+node['physical_case_id']]+=1
    for name in ('point_observations.jsonl.gz','unknown_observations.json','protocol.json'):shutil.copyfile(inputs/name,output/name)
    code=output/'interrupted_code';code.mkdir()
    for path in [ROOT/'scripts/101_rerun_corrected_roads.py',*(ROOT/'src/quiet_uk').glob('*.py')]:shutil.copyfile(path,code/path.name)
    unknowns=json.loads((output/'unknown_observations.json').read_bytes())
    (output/'report.json').write_bytes(json_bytes(dict(status='captured_interrupted_corrected_observation_prefix',numeric_observations=sum(counts.values()),
        pending_native_unknowns=sum(map(len,unknowns.values())),per_case_numeric_counts=counts,parent_inventory_sha256=protocol['parent_inventory_sha256'],
        physical_model_validation_sha256=protocol['model_validation_sha256'],independently_reviewed=False,road_integrals_complete=False,all_inclusive_noise_db=None,map_updated=False)))
    shutil.copyfile(__file__,output/Path(__file__).name)
    print(json.dumps(dict(generation_id=helper.seal(output,'corrected-observation-recovery-'),numeric_observations=sum(counts.values()),pending_native_unknowns=sum(map(len,unknowns.values())))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
