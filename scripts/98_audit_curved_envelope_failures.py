"""Freeze every unsupported circular-envelope witness in the reviewed runs.

Captures conditional real scenes without relabelling them or accepting any road
integral. The original executed tangents and radius are loaded from the frozen
calculation, so this audit also reproduces after the selector is repaired.
"""
import argparse
from collections import Counter
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.sightline_boundary import physical_edge_candidates,validated_roofs
from quiet_uk.terrain_profile import linear_segments
from quiet_uk.multiple_edge_boundary import convex_chain
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def trace(nodes,radius,arc_tangents):
    chain=[]
    try:
        for p in nodes:
            while len(chain)>1:
                incoming=arc_tangents(chain[-2],chain[-1],radius)[1]
                outgoing=arc_tangents(chain[-1],p,radius)[0]
                if incoming-outgoing>1e-12:break
                chain.pop()
            if chain:arc_tangents(chain[-1],p,radius)
            chain.append(p)
    except ValueError as error:
        # Reconstruct the first invalid pair, rather than guessing from a reason.
        pairs=list(zip(chain,chain[1:]))+([(chain[-1],p)] if chain else [])
        invalid=[]
        for a,b in pairs:
            try:arc_tangents(a,b,radius)
            except ValueError as e:
                invalid.append(dict(a=a,b=b,dx_m=b[0]-a[0],dz_m=b[1]-a[1],chord_m=math.dist(a,b),reason=str(e)))
        return dict(status='withheld',exception=str(error),invalid_pairs=invalid)
    return dict(status='supported',chain=chain)

def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    inputs=[ROOT/'artifacts'/n for n in ('adaptive_road_integration_v3','adaptive_road_spacing_check_v1','weather_sources_v1')]
    for p in inputs:helper.verified(p)
    parents={p.name:file_hash(p/'inventory.json') for p in inputs}
    original=inputs[0]/'code'
    for path in original.glob('*.py'):
        current=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        if path.name!='favourable_propagation.py':
            assert file_hash(current)==file_hash(path),'Unrelated audited implementation changed'
    frozen_spec=importlib.util.spec_from_file_location('frozen_failure_tangents',original/'favourable_propagation.py')
    frozen=importlib.util.module_from_spec(frozen_spec);frozen_spec.loader.exec_module(frozen)
    rows=[];reasons=Counter();pruned=Counter()
    for directory in inputs[:2]:
        with gzip.open(directory/'node_evaluations.jsonl.gz','rt') as stream:
            for line in stream:
                node=json.loads(line)
                if node['status']!='withheld' or 'unsupported_circular_envelope' not in node['reasons']:continue
                scene=node['candidate_kernel_scene'];roofs=validated_roofs(linear_segments(scene['points']),scene['obstacles'])
                candidates=physical_edge_candidates(scene['points'],roofs);s,r=scene['source'],scene['receiver'];radius=frozen.ray_radius(s,r)
                original_trace=trace([s,*candidates,r],radius,frozen.arc_tangents)
                assert original_trace['status']=='withheld'
                straight=convex_chain(candidates,s,r)
                filtered_trace=trace(straight,radius,frozen.arc_tangents)
                reasons.update([original_trace['exception']]);pruned.update([filtered_trace['status']])
                rows.append(dict(input_directory=directory.name,input_node_id=node['node_id'],link_id=node['link_id'],physical_case_id=node['physical_case_id'],
                    source_cell_id=node['source_cell_id'],fraction=node['fraction'],xy_bng=node['xy_bng'],candidate_kernel_scene=scene,
                    original_reason=node['reasons'],radius_m=radius,candidates=candidates,original_trace=original_trace,
                    straight_hull_candidate_count=len(straight)-2,filtered_trace=filtered_trace))
    assert len(rows)==65 and len({(r['link_id'],r['physical_case_id']) for r in rows})==59
    output.mkdir(parents=True);(output/'witnesses.json').write_bytes(json_bytes(dict(witnesses=rows)))
    report=dict(witnesses=len(rows),road_cases=59,original_failure_types=reasons,straight_hull_prefilter_outcomes=pruned,
        largest_original_candidate_count=max(len(r['candidates']) for r in rows),largest_hull_candidate_count=max(r['straight_hull_candidate_count'] for r in rows),
        purpose='Diagnose preserved unsupported observations; supported trace is geometry only, not new attenuation, road integration or field validation.',
        scene_identity_preserved=True,parent_inventory_sha256=parents,all_inclusive_noise_db=None,map_updated=False)
    (output/'report.json').write_bytes(json_bytes(report));shutil.copytree(original,output/'original_code');shutil.copyfile(__file__,output/Path(__file__).name)
    for p in inputs:helper.verified(p);assert parents[p.name]==file_hash(p/'inventory.json')
    generation=helper.seal(output,'curved-envelope-witnesses-');print(json.dumps(dict(generation_id=generation,report=report)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.output)
