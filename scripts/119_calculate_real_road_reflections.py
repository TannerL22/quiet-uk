"""Calculate the declared direct + single-reflection model on real road lines.

Existing acoustic kernels and receipts are unchanged. This is an offline finite
path model, not all-road/all-source sound. Closed observation chunks and completed
roads survive restarts; resume requires exactly the same code, inputs and policy.

v5: the run pins its engine import closure, not every repository file. Changed
closure files need an identical-replay receipt from script 121 against v4.
Cached observations are held as compact JSON text to bound worker memory.
Scheduling (workers, pause/stop, memory) is scripts/calculation_monitor.py.
"""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import csv
import gzip
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import shutil
import sys
import time

import numpy as np
from shapely.geometry import Point, Polygon, box

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk import generations as helper
from quiet_uk import road_calculation_inputs as prior
from quiet_uk import run_identity
from quiet_uk.adaptive_road_integration import DEFAULTS, geometry_breakpoints, integrate_adaptive
from quiet_uk.conditional_reflection_domain import SCOPE, reflection_cases
from quiet_uk.conditional_road_propagation import receiver_metrics, road_guard, source_joint_power
from quiet_uk.conditional_road_reflections import road_point_energy
from quiet_uk.digests import file_hash, json_bytes
from quiet_uk.physical_scene_assumptions import assumption_cases
from quiet_uk.road_scene_builder import NativeTerrain, RoadSceneBuilder, parts

RECEIPT=ROOT/'artifacts/road_reflection_connection_validation_v1.json'
EQUIVALENCE=ROOT/'artifacts/engine_equivalence_v5.json'
PHYSICAL=next(c for c in assumption_cases() if c['case_id']=='combined_reference')
FACADE=next(c for c in reflection_cases() if c['case_id']=='reference_facade')
RECEIVER=[507000.,179000.]
RUN={}


read=lambda p:json.loads(Path(p).read_bytes())


def plain(value):
    if isinstance(value,np.ndarray):return value.tolist()
    if isinstance(value,dict):return {k:plain(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):return [plain(v) for v in value]
    return value


def array_list(value):
    """json default: the same output as plain(), without copying every node first."""
    if isinstance(value,np.ndarray):return value.tolist()
    raise TypeError(f'Not JSON serialisable: {type(value).__name__}')


def atomic_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.writing')
    tmp.write_bytes(json_bytes(plain(value)));os.replace(tmp,path)


def equivalence():
    return read(EQUIVALENCE) if EQUIVALENCE.exists() else None


def code_identity():
    return run_identity.import_closure(ROOT,Path(__file__))


def guard(codes):
    """Every file the engine runs is validated, or replayed identically against v4."""
    receipt=read(RECEIPT)
    assert receipt['status']=='passed_two_bounded_road_reflection_validation_blocks'
    unaccepted=run_identity.unaccepted_files(codes,receipt['code_sha256'],equivalence())
    if unaccepted:raise ValueError('Engine code is neither validated nor shown identical (script 121): '+', '.join(unaccepted))
    ledger=receipt['unchanged_direct_ledger']
    assert file_hash(ROOT/ledger['path'])==ledger['sha256']
    return receipt


def front_side(wall,point):
    """Strict exterior half-plane of an oriented generalised exterior ring."""
    a,b=wall['start'],wall['end'];dx,dy=b[0]-a[0],b[1]-a[1]
    length=math.hypot(dx,dy)
    signed=(dx*(point[1]-a[1])-dy*(point[0]-a[0]))/length
    tolerance=64*math.ulp(max(1.,*(abs(x) for p in (a,b,point) for x in p)))
    return signed < -tolerance if wall['interior_is_left'] else signed > tolerance


def runtime():
    return dict(python=sys.version,packages={n:version(n) for n in ('numpy','shapely','rasterio','pyproj')})


class ExteriorFacadeSearch:
    """Filter opaque building backs before native profile preparation.

    Every eligible facade inside the declared detour is retained. The old two
    prepared-profile cap is removed; the unchanged mirror-solve budget remains.
    Ring orientation is geometric, not an acoustic-material qualification.
    """
    def __init__(self,builder):
        self.original=builder.reflection_candidates
        builder.maximum_reflections=builder.maximum_wall_candidates
        self.walls={}
        for wall in builder.walls:
            part_index=int(wall['facet_id'].rsplit(':',2)[1])
            polygon=parts(wall['record']['geometry'],'Polygon')[part_index]
            self.walls[wall['facet_id']]=dict(wall,interior_is_left=bool(polygon.exterior.is_ccw))

    def __call__(self,source,receiver):
        search=self.original(source,receiver);accepted=[];rejected=[]
        for candidate in search['candidates']:
            if all(front_side(self.walls[i],source) and front_side(self.walls[i],receiver)
                   for i in candidate['coincident_facet_ids']):accepted.append(candidate)
            else:rejected.append(candidate['facet_id'])
        return dict(search,candidates=accepted,eligible_plan_candidates_before_exterior_filter=search['eligible_plan_candidates'],
            eligible_plan_candidates=len(accepted) if search['eligible_plan_candidates'] is not None else None,
            non_exterior_facade_plans=len(rejected),non_exterior_facet_ids=rejected,
            reason=search['reason'] if search['reason']=='reflection_search_budget_exceeded' else 'bounded_exterior_facade_candidates',
            support_complete=False)


def normalize_reflector_contacts(prepared):
    """Separate representational contact slivers from positive roof extents.

    Both plan legs must already approach the strict exterior facade half-plane.
    Only an interior-of-facet hit and a complete interval within 64 coordinate
    ulps of that hit can become a contact. Larger/other intervals stay intact.
    Native terrain, original kernel evidence and source/receiver positions are
    untouched. Every replaced interval is retained in the normalization record.
    """
    reflections=[]
    for ref in prepared['reflections']:
        pi=ref['plan_input'];bound=64*math.ulp(max(1.,*(abs(v) for p in pi.values() for v in p)))
        margin=min(ref['plan']['wall_fraction'],1-ref['plan']['wall_fraction'])*math.dist(pi['wall_start_xy'],pi['wall_end_xy'])
        changes=[];crossings=[];x=ref['wall']['unfolded_x_m'];identities=set(ref['coincident_feature_ids'])
        for crossing in ref['profile']['crossings']:
            kept=[];removed=[]
            for a,b in crossing['intervals_m']:
                if (crossing['feature_id'] in identities and margin>bound and
                        0<=b-a<=bound and max(abs(a-x),abs(b-x))<=bound):
                    removed.append([a,b])
                else:kept.append([a,b])
            if removed:
                crossings.append(dict(crossing,intervals_m=kept,contacts_m=sorted(set([*crossing['contacts_m'],x]))))
                changes.append(dict(feature_id=crossing['feature_id'],physical_leg=crossing['physical_leg'],
                    original_intervals_m=removed,contact_x_m=x,coordinate_precision_bound_m=bound))
            else:crossings.append(crossing)
        reflections.append(dict(ref,profile=dict(ref['profile'],crossings=crossings),contact_normalization=changes))
    return dict(prepared,reflections=reflections)


class ExteriorProfilePreparation:
    def __init__(self,builder):self.original=builder.prepare_point
    def __call__(self,*args,**kwargs):return normalize_reflector_contacts(self.original(*args,**kwargs))


def reflection_partitions(source,receiver,builder,partition):
    """Add finite-mirror visibility and detour-limit changes along source cells.

    Reflection visibility changes on the facade plane or a ray from the mirrored
    receiver through a facade endpoint. The detour boundary solves
    |S-Rimage|-|S-R|=D; squaring its linear difference of squared distances gives
    a quadratic. Clip each source cell to the finite facade's visibility cone
    first: infinite extensions of irrelevant facades must not create panels.
    Terrain/diffraction/ground branch transitions are still not exhaustive.
    """
    facade_search=builder.reflection_candidates
    assert isinstance(facade_search,ExteriorFacadeSearch)
    positions=[p for cell in source['source_cells'] for p in (cell['start_xy_bng'],cell['end_xy_bng'])]
    radius=max(math.dist(p,receiver) for p in positions)+builder.detour
    ids=builder.wall_tree.query(box(receiver[0]-radius,receiver[1]-radius,receiver[0]+radius,receiver[1]+radius))
    walls=[facade_search.walls[builder.walls[int(i)]['facet_id']] for i in ids]
    walls=[w for w in walls if front_side(w,receiver)]
    result={k:[dict(v) for v in values] for k,values in partition['cells'].items()};added=0
    if not walls:return dict(partition,cells=result,reflected_visibility_breakpoints=0)
    starts=np.array([w['start'] for w in walls]);ends=np.array([w['end'] for w in walls]);r=np.array(receiver)
    tangents=ends-starts;norm=np.linalg.norm(tangents,axis=1)
    normals=np.column_stack((-tangents[:,1],tangents[:,0]))/norm[:,None]
    exterior_sign=np.array([-1. if w['interior_is_left'] else 1. for w in walls])
    signed_r=np.sum((r-starts)*normals,axis=1)
    images=r-2*signed_r[:,None]*normals
    def cross(a,b):return a[...,0]*b[...,1]-a[...,1]*b[...,0]
    for cell in source['source_cells']:
        a=np.array(cell['start_xy_bng']);v=np.array(cell['end_xy_bng'])-a
        side_a=exterior_sign*np.sum((a-starts)*normals,axis=1)
        side_b=exterior_sign*np.sum((a+v-starts)*normals,axis=1)
        lo=np.zeros(len(walls));hi=np.ones(len(walls));valid=np.ones(len(walls),dtype=bool)
        with np.errstate(divide='ignore',invalid='ignore'):
            # Intersect the exterior half-plane and the two finite endpoint
            # half-planes. Each is a linear inequality in source fraction t.
            orientation=np.sign(cross(ends-images,starts-images))
            inequalities=[(side_a,side_b-side_a)]
            for vertices,sign in ((starts,orientation),(ends,-orientation)):
                ray=vertices-images
                inequalities.append((sign*cross(a-images,ray),sign*cross(np.broadcast_to(v,ray.shape),ray)))
            for z,dz in inequalities:
                valid &= (dz!=0)|(z>=0)
                root=-z/dz
                lo=np.maximum(lo,np.where(dz>0,root,0.))
                hi=np.minimum(hi,np.where(dz<0,root,1.))
            possible=valid & (orientation!=0) & (lo<hi) & (lo<1) & (hi>0)
            bounds=[lo,hi]
            d=builder.detour
            l0=np.sum((a-images)**2,axis=1)-float(np.sum((a-r)**2))-d*d
            l1=2*np.sum(v*(r-images),axis=1)
            qa=l1*l1-4*d*d*float(v@v)
            qb=2*l0*l1-8*d*d*float(v@(a-r))
            qc=l0*l0-4*d*d*float((a-r)@(a-r))
            discriminant=qb*qb-4*qa*qc
            square=np.sqrt(np.maximum(0.,discriminant))
            q=-.5*(qb+np.where(qb>=0,square,-square))
            for values in (q/qa,qc/q,np.where(qa==0,-qc/qb,np.nan)):
                values=np.where(((discriminant>=0)|(qa==0)) & (values>=lo) & (values<=hi),values,np.nan)
                bounds.append(np.where(np.isfinite(values),values,hi))
            # Finite visibility alone is insufficient: a distant facade can
            # have a narrow mirror cone but exceed the detour everywhere.
            # Split at its quadratic roots, then retain only valid subintervals.
            ordered=np.sort(np.stack(bounds,axis=1),axis=1)
            left,right=ordered[:,:-1],ordered[:,1:]
            mid=(left+right)/2
            positions=a+mid[:,:,None]*v
            detours=np.linalg.norm(positions-images[:,None,:],axis=2)-np.linalg.norm(positions-r,axis=2)
            allowed=possible[:,None] & (left<right) & (detours<=d)
        cuts=list(result[cell['segment_id']])
        for values in (left[allowed],right[allowed]):
            for t in values[np.isfinite(values) & (values>0) & (values<1)]:
                cuts.append(dict(fraction=float(t),reasons=['reflected_finite_model_visibility']));added+=1
        merged=[]
        for cut in sorted(cuts,key=lambda x:x['fraction']):
            if merged and abs(cut['fraction']-merged[-1]['fraction'])<1e-11:
                merged[-1]['reasons']=sorted(set(merged[-1]['reasons']+cut['reasons']))
                if cut['fraction'] in (0.,1.):merged[-1]['fraction']=cut['fraction']
            else:merged.append(cut)
        result[cell['segment_id']]=merged
    return dict(partition,cells=result,reflected_visibility_breakpoints=added,
                reflection_terrain_and_acoustic_transition_partition_complete=False)


def text(value):
    return json.dumps(value,separators=(',',':'),allow_nan=False)


class PointJournal:
    """Exact-XY memoization; partial final JSON lines never become observations.

    Values are cached as their compact JSON text, about a third of the memory of
    the decoded dicts; value() decodes one on use.
    """
    def __init__(self,directory,fingerprint):
        self.directory=Path(directory);self.directory.mkdir(parents=True,exist_ok=True)
        self.fingerprint=fingerprint;self.cache={};self.pending=[];self.sequence=0
        for path in sorted(self.directory.glob('chunk-*.jsonl.gz')):
            self.sequence=max(self.sequence,int(path.name.split('-')[1].split('.')[0]))
            with gzip.open(path,'rt',encoding='utf-8') as stream:
                for line in stream:self.restore(json.loads(line))
        self.active=self.directory/'active.jsonl'
        if self.active.exists():
            raw=self.active.read_bytes();complete=[]
            for line in raw.splitlines(keepends=True):
                if not line.endswith(b'\n'):break
                value=json.loads(line);self.restore(value);complete.append(line);self.pending.append(value)
            # This is an unsealed work journal, never an immutable generation.
            self.active.write_bytes(b''.join(complete))
        self.stream=self.active.open('ab')

    @staticmethod
    def key(xy):return tuple(float(v).hex() for v in xy)

    def restore(self,record):
        assert record['fingerprint']==self.fingerprint
        key=self.key(record['xy_bng']);value=text(record['value'])
        if key in self.cache:assert self.cache[key]==value,'Conflicting cached observation'
        else:self.cache[key]=value

    def value(self,key):return json.loads(self.cache[key])

    def store(self,xy,value):
        value=plain(value);record=dict(fingerprint=self.fingerprint,xy_bng=list(xy),value=value)
        key=self.key(xy);encoded=text(value)
        if key in self.cache:assert self.cache[key]==encoded;return
        self.cache[key]=encoded;self.pending.append(record)
        self.stream.write((json.dumps(record,separators=(',',':'),allow_nan=False)+'\n').encode());self.stream.flush()
        if len(self.pending)>=100:self.rotate()

    def rotate(self):
        if not self.pending:return
        self.sequence+=1;path=self.directory/f'chunk-{self.sequence:06d}.jsonl.gz'
        assert not path.exists()
        temporary=path.with_name(path.name+'.writing')
        with temporary.open('wb') as raw:
            with gzip.GzipFile(filename='',fileobj=raw,mode='wb',mtime=0,compresslevel=1) as stream:
                for value in self.pending:stream.write((json.dumps(value,separators=(',',':'),allow_nan=False)+'\n').encode())
        os.replace(temporary,path)
        self.stream.close();self.active.write_bytes(b'');self.stream=self.active.open('ab');self.pending=[]

    def close(self):self.rotate();self.stream.close()


def initialise(output):
    prior.initialise(output)
    builder=prior.STATE['builder'];builder.reflection_candidates=ExteriorFacadeSearch(builder)
    builder.prepare_point=ExteriorProfilePreparation(builder)
    RUN.update(output=Path(output),fingerprint=read(Path(output)/'protocol.json')['fingerprint'],
               previous_rows=read(Path(output)/'previous_direct_rows.json'))


def thin_point(result):
    return dict(status=result['status'],reasons=result['reasons'],relative_energy_per_m=plain(result['relative_energy_per_m']),
        paths=result['paths'],reflection_search={k:v for k,v in result['reflection_search'].items() if k!='non_exterior_facet_ids'},
        reflected_inputs=[dict(facet_id=e['facet_id'],geometry_blockers=e['candidate']['geometry_blockers'],wall=e['candidate']['wall'],
            native_terrain_points=len(e['candidate']['candidate_scene']['points']),
            transfer_status=e['transfer']['status'],transfer_reasons=e['transfer'].get('reasons',[])) for e in result['reflected_evidence']],
        scope=SCOPE,complete_physical_path_support=False,all_inclusive_noise_db=None)


def minimum_initial(source,partition):
    total=0
    for cell in source['source_cells']:
        cuts=partition['cells'][cell['segment_id']]
        total+=sum(max(1,math.ceil((b['fraction']-a['fraction'])*cell['length_m']/DEFAULTS['maximum_panel_m'])) for a,b in zip(cuts,cuts[1:]))
    return 5*total


def checked_density(node):
    """Scalar source x transfer accounting, independent of the array combiner."""
    if node['status']=='withheld':return None
    expected=np.zeros((2,3,8));direct=np.zeros_like(expected)
    assert sum(p['path_id']=='direct' for p in node['paths'])==1
    for si in range(2):
        for pi in range(3):
            for bi in range(8):
                terms=[]
                for path in node['paths']:
                    assert path['status']=='conditional_research'
                    value=path['relative_transfer'][si][bi]*path['joint_source_air_power_per_m'][pi][bi]
                    assert math.isclose(path['relative_energy_per_m'][si][pi][bi],value,rel_tol=3e-12,abs_tol=0.)
                    terms.append(value)
                    if path['path_id']=='direct':direct[si,pi,bi]=value
                expected[si,pi,bi]=math.fsum(terms)
    actual=np.asarray(node['relative_energy_per_m'])
    assert np.allclose(actual,expected,rtol=3e-12,atol=0.)
    return expected,direct


def component_integrals(weighted_nodes):
    total=np.zeros((2,3,8));direct=np.zeros_like(total);length=0.
    for weight,node in weighted_nodes:
        assert math.isfinite(weight) and weight>0
        result=checked_density(node)
        if result is None:raise ValueError('Unknown point cannot enter an accepted integral')
        value,d=result;total+=weight*value;direct+=weight*d;length+=weight
    return total,direct,length


def metrics(energy):
    return {state:receiver_metrics(energy[i]) for i,state in enumerate(('homogeneous','favourable'))}


def differences(a,b):
    am,bm=metrics(a),metrics(b)
    return {s:{p:am[s][p]-bm[s][p] if am[s][p] is not None and bm[s][p] is not None else
               0. if am[s][p] is None and bm[s][p] is None else None for p in am[s]} for s in am}


def reflected_integral(weighted_nodes):
    result=np.zeros((2,3,8))
    for weight,node in weighted_nodes:
        for path in node['paths']:
            if path['path_id']!='direct':result+=weight*np.asarray(path['relative_energy_per_m'])
    return result


def compute(source):
    start=time.perf_counter();s=prior.STATE;link=source['link_id'];directory=RUN['output']/'roads'/link
    directory.mkdir(parents=True,exist_ok=True);policy=s['policies'][link]['physical_source'];road=s['roads'][link]
    old=RUN['previous_rows'][link]
    common=dict(link_id=link,physical_case_id=PHYSICAL['case_id'],reflection_case_id=FACADE['case_id'],
        source_length_m=source['prepared_length_m'],scope=SCOPE,complete_physical_path_support=False,
        all_inclusive_noise_db=None,full_road_all_paths_noise_db=None,field_validation=False,
        fingerprint=RUN['fingerprint'],previous_direct_integration_stage=old['integration_stage'])
    def finish(row):
        result=dict(common,**plain(row),elapsed_seconds=time.perf_counter()-start)
        atomic_json(directory/'result.json',result)
        return dict(link_id=link,status=result['status'],elapsed_seconds=result['elapsed_seconds'],
                    evaluations=result.get('evaluation_count',0),source_length_m=source['prepared_length_m'])
    # Preserve all known physical holds, including the diagnosed roof/terrain
    # conflict. New sample positions cannot silently erase old unknown support.
    reasons=[] if old['whole_road_relative_energy'] is not None else ['previous_direct_component_unresolved']
    reasons+=road_guard(source,s['builder'],s['features'],PHYSICAL,policy,RECEIVER)
    if reasons:return finish(dict(status='withheld_existing_input_support',reasons=sorted(set(reasons)),selected_model_relative_energy=None,evaluation_count=0))
    journal=PointJournal(directory/'observations',RUN['fingerprint']);new=0;reused=0;phase='primary'
    def evaluate(xy):
        nonlocal new,reused
        key=journal.key(xy)
        if key in journal.cache:reused+=1;return journal.value(key)
        prepared=None
        try:
            prepared=s['builder'].prepare_point(xy,RECEIVER,reflections=True)
            r=road_point_energy(prepared,s['features'],PHYSICAL,policy,FACADE,
                lambda distance:source_joint_power(road,s['coefficients'],s['calendar'],s['air'],distance))
            value=thin_point(r)
            value['reflector_contact_normalizations']=[dict(facet_id=f['facet_id'],changes=f['contact_normalization'])
                for f in prepared['reflections'] if f.get('contact_normalization')]
        except (ValueError,OverflowError,ZeroDivisionError) as exc:
            value=dict(status='withheld',reasons=['unsupported_prepared_geometry:'+str(exc)],relative_energy_per_m=None,paths=[],scope=SCOPE)
        if value['status']=='withheld' and prepared is not None and not (directory/'first_held_profile.json.gz').exists():
            witness=directory/'first_held_profile.json.gz'
            temporary=witness.with_name(witness.name+'.writing')
            with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=1) as stream:
                json.dump(dict(xy_bng=xy,reasons=value['reasons'],prepared=prepared),stream,separators=(',',':'),allow_nan=False)
            os.replace(temporary,witness)
        journal.store(xy,value);new+=1
        if new==1 or new%20==0:
            atomic_json(directory/'activity.json',dict(link_id=link,phase=phase,new_native_points=new,reused_points=reused,
                elapsed_seconds=time.perf_counter()-start,last_xy_bng=xy,last_status=value['status']))
        return value
    try:
        atomic_json(directory/'activity.json',dict(link_id=link,phase='preparing_source_partition',new_native_points=0,reused_points=0))
        cached_partition=RUN['output']/'source_partitions'/(link+'.json.gz')
        if cached_partition.exists():
            planned=json.loads(gzip.decompress(cached_partition.read_bytes()))
            assert planned['fingerprint']==RUN['fingerprint'] and planned['link_id']==link
            partition=planned['partition']
        else:
            partition=geometry_breakpoints(source,RECEIVER,s['builder'])
            partition=reflection_partitions(source,RECEIVER,s['builder'],partition)
        initial=minimum_initial(source,partition)
        controls=dict(DEFAULTS,maximum_evaluations=initial+10*DEFAULTS['maximum_refinements'])
        primary=integrate_adaptive(source,evaluate,partition=partition,controls=controls)
        primary.update(scope=SCOPE,physical_case_id=PHYSICAL['case_id'],reflection_case_id=FACADE['case_id'],
            status='conditional_selected_path_component_numerical_diagnostic_met' if primary['diagnostic_tolerance_met'] else 'withheld_numerical_or_physical_support')
        nodes=primary.pop('evaluations')
        with gzip.open(directory/'primary_nodes.json.gz','wt',encoding='utf-8',compresslevel=1) as stream:
            json.dump(nodes,stream,separators=(',',':'),allow_nan=False,default=array_list)
        atomic_json(directory/'primary.json',primary)
        if not primary['diagnostic_tolerance_met']:
            return finish(dict(status='withheld_primary_support_or_numerics',reasons=sorted(primary['reason_counts']) or [primary['termination']],
                selected_model_relative_energy=None,evaluation_count=len(nodes),primary_termination=primary['termination'],
                primary_indicator_db=primary['indicator_db'],new_native_points=new,reused_points=reused))
        weighted=[(leaf['length_m']/2,nodes[i]) for leaf in primary['leaves'] for i in leaf['node_ids'][1:3]]
        ptotal,pdirect,length=component_integrals(weighted)
        assert abs(length-source['prepared_length_m'])<1e-7
        assert np.allclose(ptotal,primary['whole_road_relative_energy'],rtol=3e-12,atol=0.)
        phase='finer';fine_nodes=[];reasons=[]
        cell_lookup={c['segment_id']:c for c in source['source_cells']}
        for index,leaf in enumerate(primary['leaves']):
            cell=cell_lookup[leaf['source_cell_id']]
            for j in range(4):
                t=leaf['left_fraction']+(leaf['right_fraction']-leaf['left_fraction'])*(j+.5)/4
                xy=[a+(b-a)*t for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
                node=dict(evaluate(xy),source_cell_id=cell['segment_id'],fraction=t,leaf_index=index,quadrature_j=j,
                          weight_length_m=leaf['length_m']/4,xy_bng=xy)
                fine_nodes.append(node)
                if node['status']=='withheld':reasons.extend(node['reasons']);break
            if reasons:break
        with gzip.open(directory/'finer_nodes.json.gz','wt',encoding='utf-8',compresslevel=1) as stream:
            json.dump(fine_nodes,stream,separators=(',',':'),allow_nan=False,default=array_list)
        if reasons:return finish(dict(status='withheld_finer_input_support',reasons=sorted(set(reasons)),
            selected_model_relative_energy=None,evaluation_count=len(nodes)+len(fine_nodes),new_native_points=new,reused_points=reused))
        weighted_fine=[(n['weight_length_m'],n) for n in fine_nodes]
        total,direct,length=component_integrals(weighted_fine);reflected=reflected_integral(weighted_fine)
        assert abs(length-source['prepared_length_m'])<1e-7
        assert np.allclose(total,direct+reflected,rtol=3e-12,atol=0.)
        changes=differences(total,ptotal)
        maximum=max((abs(v) for state in changes.values() for v in state.values() if v is not None),default=0.)
        accepted=maximum<=.1
        return finish(dict(status='conditional_selected_path_road_calculation' if accepted else 'withheld_finer_spacing_change',
            reasons=[] if accepted else ['finer_change_exceeds_0_1_db'],selected_model_relative_energy=total if accepted else None,
            direct_relative_energy=direct if accepted else None,selected_reflected_relative_energy=reflected if accepted else None,
            selected_model_metrics=metrics(total) if accepted else None,direct_metrics=metrics(direct) if accepted else None,
            selected_reflection_increment_db=differences(total,direct) if accepted else None,
            primary_indicator_db=primary['indicator_db'],finer_change_db=changes,maximum_finer_change_db=maximum,
            evaluation_count=len(nodes)+len(fine_nodes),new_native_points=new,reused_points=reused,
            primary_termination=primary['termination'],refinement_convergence_proven=False,rigorous_noise_error_bound_db=None))
    finally:journal.close()


def prepare(output,resume):
    output=Path(output).resolve()
    if not output.is_relative_to(helper.artifacts_root()):raise ValueError('Workspace artifact required')
    codes=code_identity();receipt=guard(codes)
    if output.exists():
        if not resume or (output/'inventory.json').exists():raise ValueError('Fresh output, or explicit resume of an unsealed run, required')
        protocol=read(output/'protocol.json')
        assert protocol['code_sha256']==codes,'Resume code changed'
        assert protocol['validated_connection_sha256']==file_hash(RECEIPT)
        assert protocol['engine_equivalence_sha256']==(file_hash(EQUIVALENCE) if EQUIVALENCE.exists() else None)
        assert protocol['runtime']==runtime(),'Resume numerical runtime changed'
        for name,sha in protocol['parent_inventory_sha256'].items():
            helper.verified(ROOT/'artifacts'/name);assert file_hash(ROOT/'artifacts'/name/'inventory.json')==sha
        for name,sha in protocol['local_input_sha256'].items():assert file_hash(output/name)==sha
        return output,protocol
    parents=set(prior.PARENTS)|{'adaptive_road_integration_v3','remaining_road_spacing_check_v1',
        'road_reflection_connection_v2','road_reflection_connection_review_v1'}
    for name in sorted(parents):helper.verified(ROOT/'artifacts'/name)
    parent_hashes={n:file_hash(ROOT/'artifacts'/n/'inventory.json') for n in sorted(parents)}
    output.mkdir(parents=True);(output/'roads').mkdir()
    for name in ('air_tables.json','air_tables.npz'):
        shutil.copyfile(ROOT/'artifacts/adaptive_road_integration_v3'/name,output/name)
    previous={r['link_id']:r for r in read(ROOT/receipt['unchanged_direct_ledger']['path'])['roads'] if r['physical_case_id']==PHYSICAL['case_id']}
    assert len(previous)==722
    (output/'previous_direct_rows.json').write_bytes(json_bytes(previous))
    shutil.copyfile(ROOT/'artifacts/real_road_scenes_v2/source_lines.json',output/'source_lines.json')
    shutil.copyfile(ROOT/'artifacts/real_road_scenes_v2/road_input_inventory_bng.csv',output/'road_input_inventory_bng.csv')
    local={n:file_hash(output/n) for n in ('air_tables.json','air_tables.npz','previous_direct_rows.json','source_lines.json','road_input_inventory_bng.csv')}
    definition=dict(code_sha256=codes,parent_inventory_sha256=parent_hashes,local_input_sha256=local,
        runtime=runtime(),
        physical_case=PHYSICAL,facade_case=FACADE,receiver_xy_bng=RECEIVER,
        reflection_extra_plan_length_m=100.,prepared_reflection_output_cap=None,mirror_solve_budget=20000,
        facade_policy='Both endpoints strictly outside every coincident exterior ring half-plane. All eligible exterior plans inside the declared detour are prepared; unknown geometry is not zero.',
        reflection_partition='Facade half-plane crossings, mirrored receiver-to-facade-end rays, and quadratic 100m detour crossings; remaining terrain/acoustic transitions remain non-exhaustive.',
        contact_policy='For strict exterior approach and a hit interior to its finite facet, classify only intervals wholly within 64 coordinate ulps of that hit as contacts. Preserve raw intervals in a separate record, all larger roof extents and all terrain heights.',
        controls=dict(DEFAULTS,maximum_evaluations='minimum_initial_observations + 10 * maximum_refinements'),
        finer_rule='Four equally weighted interior positions on every accepted primary leaf; 0.1dB H/F period/Lden diagnostic.',
        preexisting_holds='Preserve all 128 combined-reference roads without a previous direct component, including the diagnosed roof/terrain conflict.',
        scope=SCOPE,reference_year=2021,validated_connection_sha256=file_hash(RECEIPT),
        engine_equivalence_sha256=file_hash(EQUIVALENCE) if EQUIVALENCE.exists() else None,
        code_identity='Import closure of scripts/119 (run_identity.import_closure).',
        complete_physical_path_support=False,all_inclusive_noise_db=None,map_updated=False)
    fingerprint=hashlib.sha256(json_bytes(definition)).hexdigest()
    protocol=dict(definition,fingerprint=fingerprint)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    for name in codes:
        dest=output/'code'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    return output,protocol


def progress(output,started,completed,total):
    summaries=[];active=[]
    for directory in sorted((output/'roads').iterdir()):
        if (directory/'result.json').exists():
            r=read(directory/'result.json');summaries.append(dict(link_id=r['link_id'],status=r['status'],
                evaluations=r.get('evaluation_count',0),source_length_m=r['source_length_m'],elapsed_seconds=r['elapsed_seconds']))
        elif (directory/'activity.json').exists():active.append(read(directory/'activity.json'))
    counts=Counter(r['status'] for r in summaries)
    atomic_json(output/'progress.json',dict(status='running',completed_roads=len(summaries),total_roads=total,
        status_counts=counts,active=active,evaluations=sum(r['evaluations'] for r in summaries),
        elapsed_current_process_seconds=time.perf_counter()-started,all_inclusive_noise_db=None,map_updated=False))
    return counts


def plan_workload(output,resume):
    """Prepare/cache actual source partitions before expensive acoustic work."""
    started=time.perf_counter();output,protocol=prepare(output,resume);initialise(str(output))
    sources=read(output/'source_lines.json')['source_roads'];previous=RUN['previous_rows'];s=prior.STATE
    directory=output/'source_partitions';directory.mkdir(exist_ok=True);rows=[]
    for index,source in enumerate(sorted(sources,key=lambda r:r['link_id'])):
        link=source['link_id'];eligible=previous[link]['whole_road_relative_energy'] is not None
        if eligible:
            policy=s['policies'][link]['physical_source']
            eligible=not road_guard(source,s['builder'],s['features'],PHYSICAL,policy,RECEIVER)
        if eligible:
            path=directory/(link+'.json.gz')
            if path.exists():
                record=json.loads(gzip.decompress(path.read_bytes()));assert record['fingerprint']==protocol['fingerprint']
            else:
                partition=geometry_breakpoints(source,RECEIVER,s['builder'])
                partition=reflection_partitions(source,RECEIVER,s['builder'],partition)
                record=dict(link_id=link,fingerprint=protocol['fingerprint'],partition=partition)
                temporary=path.with_name(path.name+'.writing')
                with gzip.open(temporary,'wt',encoding='utf-8',compresslevel=1) as stream:
                    json.dump(record,stream,separators=(',',':'),allow_nan=False)
                os.replace(temporary,path)
            count=minimum_initial(source,record['partition'])
            rows.append(dict(link_id=link,source_length_m=source['prepared_length_m'],minimum_primary_observations=count,
                minimum_primary_plus_finer_observations=count*9//5,partition_sha256=file_hash(path)))
        if (index+1)%10==0:
            data=dict(status='planning_actual_source_partitions',planned_roads=index+1,total_roads=722,
                eligible_so_far=len(rows),minimum_observations_so_far=sum(r['minimum_primary_plus_finer_observations'] for r in rows),
                elapsed_seconds=time.perf_counter()-started)
            atomic_json(output/'progress.json',data)
            if (index+1)%100==0:print(json.dumps(data),flush=True)
    summary=dict(fingerprint=protocol['fingerprint'],eligible_roads=len(rows),roads=rows,
        minimum_primary_observations=sum(r['minimum_primary_observations'] for r in rows),
        minimum_primary_plus_finer_observations_if_inputs_resolve=sum(r['minimum_primary_plus_finer_observations'] for r in rows),
        note='Actual cached geometry partitions. Physical holds may reduce work and adaptive refinement may increase it.',
        elapsed_seconds=time.perf_counter()-started)
    assert code_identity()==protocol['code_sha256']
    atomic_json(output/'workload_plan.json',summary)
    print(json.dumps({k:v for k,v in summary.items() if k!='roads'},indent=2),flush=True)


def close_run(output,protocol,started):
    rows=[read(p) for p in sorted((output/'roads').glob('*/result.json'))]
    assert len(rows)==len({r['link_id'] for r in rows})==722
    assert all(r['fingerprint']==protocol['fingerprint'] for r in rows)
    assert code_identity()==protocol['code_sha256'],'Engine code changed during the run'
    guard(protocol['code_sha256'])
    assert file_hash(RECEIPT)==protocol['validated_connection_sha256']
    for name,sha in protocol['parent_inventory_sha256'].items():assert file_hash(ROOT/'artifacts'/name/'inventory.json')==sha
    for name,sha in protocol['local_input_sha256'].items():assert file_hash(output/name)==sha
    accepted=[r for r in rows if r['selected_model_relative_energy'] is not None]
    known=np.sum([r['selected_model_relative_energy'] for r in accepted],axis=0) if accepted else np.zeros((2,3,8))
    (output/'road_results.json').write_bytes(json_bytes(dict(roads=rows,scope=SCOPE,
        known_accepted_subset_relative_energy=known.tolist(),known_accepted_subset_metrics=metrics(known),
        whole_road_network_relative_energy=None,all_inclusive_noise_db=None)))
    summary=dict(total_roads=722,accepted_conditional_selected_path_components=len(accepted),
        status_counts=dict(Counter(r['status'] for r in rows)),evaluations=sum(r.get('evaluation_count',0) for r in rows),
        source_roads_without_accepted_budgets=13206,maximum_accepted_finer_change_db=max((r['maximum_finer_change_db'] for r in accepted),default=None))
    report=dict(status='completed_actual_road_selected_reflection_calculation',summary=summary,
        fingerprint=protocol['fingerprint'],code_sha256=protocol['code_sha256'],
        elapsed_final_process_seconds=time.perf_counter()-started,complete_physical_path_support=False,
        field_validation=False,external_engine_executed=False,all_inclusive_noise_db=None,map_updated=False)
    atomic_json(output/'report.json',report);atomic_json(output/'progress.json',dict(status='complete',summary=summary))
    generation=helper.seal(output,'real-road-selected-reflections-')
    print(json_bytes(dict(generation_id=generation,summary=summary)).decode(),flush=True)


def run(output,resume,workers):
    if workers<1:raise ValueError('At least one worker is required')
    started=time.perf_counter();output,protocol=prepare(output,resume)
    sources=read(output/'source_lines.json')['source_roads']
    done={p.parent.name for p in (output/'roads').glob('*/result.json')}
    for link in done:assert read(output/'roads'/link/'result.json')['fingerprint']==protocol['fingerprint']
    # Start with short actual roads to make progress and observed runtime visible.
    # Every retained road remains in the fixed run; this is not a new pilot.
    jobs=iter(sorted([r for r in sources if r['link_id'] not in done],key=lambda r:(r['prepared_length_m'],r['link_id'])))
    print(f'Actual-road calculation: {len(done)}/722 saved roads; {workers} local workers',flush=True)
    with ProcessPoolExecutor(max_workers=workers,initializer=initialise,initargs=(str(output),)) as pool:
        pending={}
        for _ in range(workers):
            source=next(jobs,None)
            if source is not None:pending[pool.submit(compute,source)]=source['link_id']
        while pending:
            ready,_=wait(pending,timeout=20,return_when=FIRST_COMPLETED)
            if not ready:progress(output,started,done,722);continue
            for future in ready:
                result=future.result();link=pending.pop(future);done.add(link)
                print(f"{len(done)}/722 {link}: {result['status']}; {result['evaluations']} observations; {result['elapsed_seconds']:.1f}s",flush=True)
                source=next(jobs,None)
                if source is not None:pending[pool.submit(compute,source)]=source['link_id']
            progress(output,started,done,722)
    close_run(output,protocol,started)


def controller_check(directory):
    directory=Path(directory).resolve()
    if directory.exists() or not directory.is_relative_to(helper.artifacts_root()):raise ValueError('Fresh check directory required')
    directory.mkdir(parents=True);checks=[]
    def builder(polygon):
        terrain=NativeTerrain(np.zeros((100,100)),np.ones((100,100)),(-100,-100,100,100),synthetic=True)
        b=RoadSceneBuilder(terrain,lambda xy:[],[dict(feature_id='building',geometry=polygon)],[],maximum_reflections=2)
        b.reflection_candidates=ExteriorFacadeSearch(b);return b
    for label,polygon in [('ccw',box(-80.,-30.,80.,-20.)),('cw',Polygon(list(box(-80.,-30.,80.,-20.).exterior.coords)[::-1]))]:
        search=builder(polygon).reflection_candidates([0.,0.],[0.,30.])
        assert len(search['candidates'])==1 and search['non_exterior_facade_plans']==1
        assert all(abs(p[1]+20)<1e-12 for k,p in search['candidates'][0]['plan_input'].items() if k.startswith('wall_'))
        assert search['unprepared_candidates']==0 and search['support_complete'] is False
        checks.append('exterior_geometry_'+label)
    b=builder(box(-1.,-30.,1.,-20.))
    source=dict(source_cells=[dict(segment_id='long',start_xy_bng=[-30.,0.],end_xy_bng=[30.,0.],length_m=60.)])
    partition=dict(cells={'long':[dict(fraction=0.,reasons=['endpoint']),dict(fraction=1.,reasons=['endpoint'])]},partition_complete=False)
    refined=reflection_partitions(source,[0.,30.],b,partition)
    fractions=[v['fraction'] for v in refined['cells']['long']]
    assert any(abs(t-(30-1.4)/60)<1e-10 for t in fractions)
    assert any(abs(t-(30+1.4)/60)<1e-10 for t in fractions)
    checks.append('narrow_finite_reflection_visibility_is_partitioned')
    irrelevant=builder(box(70.,-30.,80.,-20.))
    clipped=reflection_partitions(source,[0.,30.],irrelevant,partition)
    assert [v['fraction'] for v in clipped['cells']['long']]==[0.,1.]
    checks.append('irrelevant_extended_facades_do_not_create_panels')
    distant=builder(box(-1.,-200.,1.,-190.))
    wide=dict(source_cells=[dict(segment_id='long',start_xy_bng=[-200.,0.],end_xy_bng=[200.,0.],length_m=400.)])
    excluded=reflection_partitions(wide,[0.,30.],distant,partition)
    assert [v['fraction'] for v in excluded['cells']['long']]==[0.,1.]
    checks.append('finite_facade_beyond_detour_does_not_create_panels')
    ref=dict(plan_input=dict(source_xy=[507000.,179000.],receiver_xy=[507000.,179030.],
        wall_start_xy=[506990.,178980.],wall_end_xy=[507010.,178980.]),plan=dict(wall_fraction=.5),
        wall=dict(unfolded_x_m=100.),coincident_feature_ids=['building'],
        profile=dict(crossings=[dict(feature_id='building',physical_leg=1,intervals_m=[[100.,100.+2e-11],[100.,100.001]],contacts_m=[])]))
    prepared=dict(reflections=[ref]);normalized=normalize_reflector_contacts(prepared)
    crossing=normalized['reflections'][0]['profile']['crossings'][0]
    assert crossing['intervals_m']==[[100.,100.001]] and crossing['contacts_m']==[100.]
    assert len(prepared['reflections'][0]['profile']['crossings'][0]['intervals_m'])==2
    checks.append('only_coordinate_precision_contact_sliver_is_normalized')
    ref['plan']['wall_fraction']=0.
    assert not normalize_reflector_contacts(prepared)['reflections'][0]['contact_normalization']
    checks.append('corner_and_real_roof_extent_remain_withheld')
    journal=PointJournal(directory/'journal','check');value=dict(status='conditional_research',relative_energy_per_m=np.ones((2,3,8)).tolist())
    xy=[507000.123456789,179000.987654321];journal.store(xy,value);journal.close()
    with (directory/'journal'/'active.jsonl').open('ab') as f:f.write(b'{"partial":')
    restored=PointJournal(directory/'journal','check');assert restored.value(restored.key(xy))==value
    assert restored.key(xy)!=restored.key([math.nextafter(xy[0],math.inf),xy[1]])
    restored.close();checks.append('exact_coordinate_restart_and_partial_tail')
    try:PointJournal(directory/'journal','different')
    except AssertionError:checks.append('changed_run_identity_rejected')
    else:raise AssertionError('Changed input identity accepted')
    (directory/'report.json').write_bytes(json_bytes(dict(status='passed_controller_checks',checks=checks)))
    print(json_bytes(dict(status='passed_controller_checks',checks=checks)).decode())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default=str(ROOT/'artifacts/real_road_reflection_calculation_v5'))
    p.add_argument('--resume',action='store_true');p.add_argument('--workers',type=int,default=2)
    p.add_argument('--check-controller',action='store_true')
    p.add_argument('--plan-only',action='store_true')
    a=p.parse_args()
    if a.check_controller:controller_check(a.output)
    elif a.plan_only:plan_workload(a.output,a.resume)
    else:run(a.output,a.resume,a.workers)
