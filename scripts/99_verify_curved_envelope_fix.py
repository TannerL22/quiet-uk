"""Independent circle visibility and exhaustive path checks for the hull fix.

The reference uses circle centres, explicit clearance of every original point,
and exhaustive path enumeration. It never calls the evaluated hull selector.
Qhull reduction is used only for large real scenes; small cases use all points.
"""
import argparse
from collections import Counter
import gzip
import importlib.util
import itertools
import json
import math
from pathlib import Path
import shutil
import sys
import time

import numpy as np
from scipy.spatial import ConvexHull,QhullError

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk import favourable_propagation as evaluated
from quiet_uk.conditional_scene_domain import execution_policy
from quiet_uk.physical_scene_assumptions import assumption_cases
from quiet_uk.propagation_geometry_cache import geometry_cache
from quiet_uk.sightline_boundary import physical_edge_candidates,validated_roofs
from quiet_uk.terrain_profile import linear_segments

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
helper=module('curved_inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
AIR=dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)

def read(path):return json.loads(Path(path).read_bytes())

def circle_leg(a,b,radius,points=()):
    """Clockwise minor arc from its centre and endpoint tangent vectors."""
    a,b=np.asarray(a,dtype=float),np.asarray(b,dtype=float);v=b-a;chord=float(np.linalg.norm(v))
    if v[0]<=0 or chord<=0 or chord>=2*radius:raise ValueError('No supported minor arc')
    normal=np.asarray([-v[1],v[0]])/chord
    center=(a+b)/2-normal*math.sqrt(radius*radius-chord*chord/4)
    tangent_a=np.asarray([a[1]-center[1],center[0]-a[0]])
    tangent_b=np.asarray([b[1]-center[1],center[0]-b[0]])
    if tangent_a[0]<=0 or tangent_b[0]<=0:raise ValueError('Arc is not a height graph')
    minimum=None
    if len(points):
        points=np.asarray(points,dtype=float)
        interior=points[(points[:,0]>a[0])&(points[:,0]<b[0])]
        if len(interior):
            discriminant=radius*radius-(interior[:,0]-center[0])**2
            if np.any(discriminant<0):raise ValueError('Outside circular support')
            margin=center[1]+np.sqrt(discriminant)-interior[:,1]
            minimum=float(margin.min())
            if minimum < -1e-8:raise ValueError('Arc does not enclose supplied support')
    return dict(departure=math.atan2(tangent_a[1],tangent_a[0]),arrival=math.atan2(tangent_b[1],tangent_b[0]),
        length=2*radius*math.asin(chord/(2*radius)),minimum_clearance_m=minimum)

def upper_support_qhull(nodes):
    """Independent polygon boundary reduction; no stack or turn predicates."""
    a=np.asarray(nodes,dtype=float)
    if len(a)<=2:return [list(p) for p in a]
    try:indices=list(ConvexHull(a).vertices)
    except QhullError:
        # Collinear support has no interior upper polygon corner.
        cross=(a[:,0]-a[0,0])*(a[-1,1]-a[0,1])-(a[:,1]-a[0,1])*(a[-1,0]-a[0,0])
        if np.max(np.abs(cross))>1e-8:raise
        return [list(a[0]),list(a[-1])]
    start=indices.index(0);end=indices.index(len(a)-1);paths=[]
    for step in (1,-1):
        path=[];i=start
        while True:
            path.append(list(a[indices[i]]))
            if i==end:break
            i=(i+step)%len(indices)
        paths.append(path)
    def area(path):
        return math.fsum((b[0]-a[0])*(a[1]+b[1])/2 for a,b in zip(path,path[1:]))
    return max(paths,key=area)

def independent_envelope(candidates,s,r,radius,*,reduce=False):
    original=[list(s),*[list(p) for p in candidates],list(r)]
    nodes=upper_support_qhull(original) if reduce else original
    if len(nodes)>16:raise ValueError('Exhaustive reference budget exceeded')
    legs={}
    for ia,ib in itertools.combinations(range(len(nodes)),2):
        try:legs[ia,ib]=circle_leg(nodes[ia],nodes[ib],radius,original)
        except ValueError:continue
    admissible=[]
    for mask in range(1<<(len(nodes)-2)):
        indexes=[0]+[i for i in range(1,len(nodes)-1) if mask>>(i-1)&1]+[len(nodes)-1]
        path=[legs.get(pair) for pair in zip(indexes,indexes[1:])]
        if any(leg is None for leg in path):continue
        if any(a['arrival']-b['departure']<=1e-12 for a,b in zip(path,path[1:])):continue
        admissible.append((math.fsum(leg['length'] for leg in path),indexes))
    if not admissible:raise ValueError('No supported enclosing path')
    length,indexes=min(admissible)
    return dict(path=[nodes[i] for i in indexes],length_m=length,search_nodes=len(nodes),admissible_paths=len(admissible))

def configuration(node,cases):
    assert node['candidate_kernel_scene']['scene_kind']=='conditional_real_input'
    return dict(node['candidate_kernel_scene'],conditional_input_policy=execution_policy(cases[node['physical_case_id']]))

def geometry(scene):
    roofs=validated_roofs(linear_segments(scene['points']),scene['obstacles'])
    return physical_edge_candidates(scene['points'],roofs)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve();start=time.perf_counter()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    helper.verified(inputs);input_hash=file_hash(inputs/'inventory.json');protocol=read(inputs/'report.json')
    parents={n:ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==protocol['parent_inventory_sha256'][n]
    current_paths=[*(ROOT/'src/quiet_uk').glob('*.py'),Path(__file__),ROOT/'scripts/57_acquire_ground_cover_sources.py']
    current_hashes={p:file_hash(p) for p in current_paths}
    original=inputs/'original_code'
    for path in original.glob('*.py'):
        current=ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        if path.name!='favourable_propagation.py':assert file_hash(current)==file_hash(path),'Unrelated implementation changed'
    frozen=module('original_curved_propagation',original/'favourable_propagation.py')
    cases={c['case_id']:c for c in assumption_cases()};rows=[];counts=Counter();reasons=Counter();samples=[];known_differences=[]
    for witness in read(inputs/'witnesses.json')['witnesses']:
        scene=configuration(witness,cases);s,r=scene['source'],scene['receiver'];candidates=geometry(scene);radius=max(1000.,8*math.dist(s,r))
        assert candidates==witness['candidates'] and radius==witness['radius_m']
        path=evaluated.circular_envelope(candidates,s,r,radius)
        independent=independent_envelope(candidates,s,r,radius,reduce=True)
        assert path==independent['path'],'Selected path differs from exhaustive circular visibility reference'
        counts['independent_envelopes_checked']+=1;counts['all_original_candidate_clearances_checked']+=len(candidates)
        results=[]
        with geometry_cache():
            for f in evaluated.EXACT_CENTRES:
                old=frozen.direct_attenuation(scene,f,AIR,absorption_frequency_policy='exact_base10')
                assert old['status']=='withheld' and old['favourable']['reason']=='unsupported_circular_envelope'
                new=evaluated.direct_attenuation(scene,f,AIR,absorption_frequency_policy='exact_base10')
                if new['status']=='withheld':
                    why=[v['reason'] for k in ('homogeneous','favourable') if (v:=new[k])['status']=='withheld']
                    reasons.update(why);results.append(dict(frequency_hz=f,status='withheld',reasons=why))
                else:
                    assert new['favourable']['controlling_edges']==path[1:-1] or not path[1:-1]
                    results.append(dict(frequency_hz=f,status='conditional_research',homogeneous_attenuation_db=new['homogeneous_attenuation_db'],
                        favourable_attenuation_db=new['favourable_attenuation_db'],homogeneous_boundary_db=new['homogeneous']['boundary_db'],
                        favourable_boundary_db=new['favourable']['boundary_db'],favourable_branch=new['favourable']['branch']))
                counts['paired_witness_octaves_replayed']+=1
        recovered=all(r['status']=='conditional_research' for r in results)
        counts['numeric_witnesses_recovered' if recovered else 'witnesses_still_withheld']+=1
        rows.append(dict(input_directory=witness['input_directory'],input_node_id=witness['input_node_id'],link_id=witness['link_id'],physical_case_id=witness['physical_case_id'],
            original_reason=witness['original_reason'],scene_kind=scene['scene_kind'],selected_chain=path,reference=independent,
            status='recovered_conditional_point_attenuation' if recovered else 'withheld',octaves=results,annual_noise_prediction=None,all_inclusive_noise_db=None))
    print(f'{len(rows)} envelopes verified; {counts["numeric_witnesses_recovered"]} conditional witnesses recovered',flush=True)
    # Every previously supported point must select exactly the same path. Only
    # this selector and a diagnostic label changed in the evaluated module.
    for directory in (parents['adaptive_road_integration_v3'],parents['adaptive_road_spacing_check_v1']):
        with gzip.open(directory/'node_evaluations.jsonl.gz','rt') as stream:
            for line in stream:
                node=json.loads(line)
                if node['status']!='conditional_research':continue
                scene=configuration(node,cases);candidates=geometry(scene);s,r=scene['source'],scene['receiver'];radius=max(1000.,8*math.dist(s,r))
                assert evaluated.circular_envelope(candidates,s,r,radius)==frozen.circular_envelope(candidates,s,r,radius)
                counts['prior_numeric_point_chains_unchanged']+=1
                if len(samples)<12 and node['link_id'] not in {n['link_id'] for n in samples}:samples.append(node)
                if counts['prior_numeric_point_chains_unchanged']%5000==0:print(f'{counts["prior_numeric_point_chains_unchanged"]} supported point chains unchanged',flush=True)
    for node in samples:
        scene=configuration(node,cases)
        with geometry_cache():
            for f in evaluated.EXACT_CENTRES:
                new=evaluated.direct_attenuation(scene,f,AIR,absorption_frequency_policy='exact_base10')
                assert new['status']=='conditional_research'
                for i,state in enumerate(('homogeneous','favourable')):
                    loss=new[state+'_attenuation_db']-new['atmospheric_attenuation_db']
                    expected=-10*math.log10(node['relative_transfer'][i][list(evaluated.EXACT_CENTRES).index(f)])
                    difference=abs(loss-expected);assert difference<=1e-9
                    known_differences.append(difference)
                counts['prior_numeric_point_octaves_replayed']+=1
    report=dict(status='passed_independent_curved_envelope_geometry_and_conditional_replay',counts=counts,new_withheld_reasons=reasons,
        maximum_known_transfer_difference_db=max(known_differences,default=None),envelope_algorithm=evaluated.ENVELOPE_ALGORITHM,
        input_inventory_sha256=input_hash,parent_inventory_sha256=protocol['parent_inventory_sha256'],
        independent_scope='Circle-centre tangents, full original point clearance and exhaustive downward path search on an independently obtained upper support polygon; every previously numeric point has unchanged selected chain.',
        parity_scope='Eight-band conditional witness replay and 12 previously numeric road-point replays. No external engine or field validation.',
        witness_road_integrals_recomputed=False,previous_ledgers_modified=False,reflections_included=False,field_validation=False,all_inclusive_noise_db=None,map_updated=False,
        elapsed_seconds=time.perf_counter()-start)
    if any(file_hash(p)!=h for p,h in current_hashes.items()):raise ValueError('Executed implementation changed during checks')
    output.mkdir(parents=True);(output/'report.json').write_bytes(json_bytes(report));(output/'witness_replay.json').write_bytes(json_bytes(dict(witnesses=rows)))
    (output/'sampled_prior_numeric_points.json').write_bytes(json_bytes([dict(link_id=n['link_id'],physical_case_id=n['physical_case_id'],node_id=n['node_id']) for n in samples]))
    code=output/'code';code.mkdir()
    for p in current_paths:shutil.copyfile(p,code/p.name)
    (output/'provenance.json').write_bytes(json_bytes(dict(input_generation=read(inputs/'inventory.json')['generation_id'],input_inventory_sha256=input_hash,
        code_sha256={p.name:file_hash(p) for p in code.iterdir()},changed_physics_module='favourable_propagation.py',
        source_specific_licences='Preserve source attribution and licence manifests from the unchanged parent generations.')))
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==protocol['parent_inventory_sha256'][n]
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==input_hash
    generation=helper.seal(output,'curved-envelope-fix-review-');print(json.dumps(dict(generation_id=generation,report=report)),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
