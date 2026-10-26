"""Positive, bounded quadrature for conditional direct road components.

Error indicators compare distinct interior rules, summing absolute local changes
before A weighting. They are diagnostics, not a rigorous error bound: unknown
terrain/path transitions and unsampled narrow features remain possible.
"""
import math
from collections import Counter

import numpy as np
from shapely.geometry import LineString, MultiPoint

from quiet_uk.conditional_road_propagation import A_WEIGHT_DB, receiver_metrics
from quiet_uk.road_scene_builder import parts

METHOD = 'partitioned-adaptive-midpoint-with-offset-gauss-check-v1'
DEFAULTS = dict(tolerance_db=.1, maximum_panel_m=4., minimum_panel_m=.01,
                maximum_evaluations=2048, maximum_refinements=512)


def _positive(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        raise ValueError('Finite positive numerical control required')
    return float(value)


def validated_cells(source):
    cells = source['source_cells']; identities = set()
    if not cells: raise ValueError('Positive source geometry required')
    for cell in cells:
        identity = cell['segment_id']
        if identity in identities: raise ValueError('Duplicate source cell')
        identities.add(identity)
        a,b = np.asarray(cell['start_xy_bng'],dtype=float),np.asarray(cell['end_xy_bng'],dtype=float)
        if a.shape!=(2,) or b.shape!=(2,) or not np.isfinite([a,b]).all(): raise ValueError('Finite planar source required')
        length = _positive(cell['length_m'])
        if not math.isclose(length,math.dist(a,b),rel_tol=1e-10,abs_tol=1e-8): raise ValueError('Source length mismatch')
    total=math.fsum(c['length_m'] for c in cells)
    if not math.isclose(total,_positive(source['prepared_length_m']),rel_tol=1e-10,abs_tol=1e-8): raise ValueError('Incomplete source length')
    return cells,total


def geometry_breakpoints(source,receiver,builder):
    """Retain turns; split source DTM cells and receiver-to-obstacle vertex rays.

    These are geographic discontinuities of supplied geometry. The partition
    does not enumerate acoustic branches, terrain-ray cells or ground classes.
    """
    cells,_=validated_cells(source); r=np.asarray(receiver,dtype=float)
    if r.shape!=(2,) or not np.isfinite(r).all(): raise ValueError('Finite receiver required')
    w,s,e,n=builder.terrain.bounds; step=builder.terrain.resolution
    result={}; count=Counter()
    for cell in cells:
        a,b=np.asarray(cell['start_xy_bng']),np.asarray(cell['end_xy_bng']); d=b-a
        cuts={0.:['endpoint'],1.:['endpoint']}
        def add(t,reason):
            if math.isfinite(t) and 1e-11<t<1-1e-11:
                cuts.setdefault(float(t),[]).append(reason); count[reason]+=1
        for k,origin in ((0,w),(1,n)):
            if d[k]==0: continue
            lower,upper=sorted((a[k],b[k]))
            for j in range(math.floor((lower-origin)/step)+1,math.ceil((upper-origin)/step)):
                add((origin+j*step-a[k])/d[k],'source_terrain_grid')
        fan=MultiPoint([list(a),list(b),list(r)]).convex_hull
        for tree,records in ((builder.building_tree,builder.buildings),(builder.barrier_tree,builder.barriers)):
            for index in sorted(tree.query(fan,predicate='intersects')):
                g=records[int(index)]['geometry']; vertices=[]
                for polygon in parts(g,'Polygon'):
                    vertices.extend(polygon.exterior.coords)
                    for ring in polygon.interiors: vertices.extend(ring.coords)
                for line in parts(g,'LineString'): vertices.extend(line.coords)
                for vertex in vertices:
                    v=np.asarray(vertex)-r
                    determinant=d[0]*v[1]-d[1]*v[0]
                    if abs(determinant)<=1e-12*max(1.,np.linalg.norm(d)*np.linalg.norm(v)): continue
                    q=r-a
                    t=(q[0]*v[1]-q[1]*v[0])/determinant
                    ray=(q[0]*d[1]-q[1]*d[0])/determinant
                    if ray>=1-1e-10: add(t,'obstacle_vertex_ray')
        nearest=float(np.dot(r-a,d)/np.dot(d,d)); add(nearest,'nearest_receiver')
        # Merge only floating point duplicates, not nearby positive intervals.
        ordered=[]
        for t,reasons in sorted(cuts.items()):
            if ordered and abs(t-ordered[-1]['fraction'])<1e-11:
                ordered[-1]['reasons']=sorted(set(ordered[-1]['reasons']+reasons))
            else: ordered.append(dict(fraction=t,reasons=sorted(set(reasons))))
        result[cell['segment_id']]=ordered
    return dict(cells=result,breakpoint_reason_counts=dict(count),
        partition_complete=False,terrain_path_and_acoustic_branch_transitions_complete=False)


def _a_components(energy):
    value=(np.asarray(energy)*10**(A_WEIGHT_DB/10)).sum(axis=-1)
    den=(12*value[:,0]+4*math.sqrt(10)*value[:,1]+80*value[:,2])/24
    return np.column_stack((value,den))


def indicator_db(total,error):
    """Largest downward dB change suggested by a nonnegative energy proxy."""
    t,e=_a_components(total),_a_components(error)
    if np.any((t==0)&(e>0)) or np.any((t>0)&(e>=t)): return None
    ratio=np.divide(e,t,out=np.zeros_like(e),where=t>0)
    return float(np.max(-10*np.log10(1-ratio)))


def integrate_adaptive(source,evaluate,*,partition=None,controls=None,seed_evaluations=()):
    cells,total_length=validated_cells(source); config=dict(DEFAULTS)
    if controls:
        if set(controls)-set(config): raise ValueError('Unknown numerical control')
        config.update(controls)
    for key in ('tolerance_db','maximum_panel_m','minimum_panel_m'): config[key]=_positive(config[key])
    if config['minimum_panel_m']>config['maximum_panel_m']: raise ValueError('Invalid panel limits')
    for key in ('maximum_evaluations','maximum_refinements'):
        if isinstance(config[key],bool) or not isinstance(config[key],int) or config[key]<1: raise ValueError('Positive integer budget required')
    if partition is None: partition=dict(cells={c['segment_id']:[dict(fraction=0.,reasons=['endpoint']),dict(fraction=1.,reasons=['endpoint'])] for c in cells},partition_complete=False)
    if set(partition['cells'])!={c['segment_id'] for c in cells}: raise ValueError('Partition does not cover source')
    cache={}; nodes=[]; leaves=[]; reasons=Counter(); unresolved_observed=False
    class Budget(Exception): pass
    class Unknown(Exception): pass
    class InitialBudget(Exception): pass
    def insert(ci,t,record,origin):
        nonlocal unresolved_observed
        key=(ci,t)
        cell=cells[ci]; a,b=cell['start_xy_bng'],cell['end_xy_bng']
        xy=[x+(y-x)*t for x,y in zip(a,b)]
        record=dict(record); energy=None
        if record['status']=='conditional_research':
            energy=np.asarray(record['relative_energy_per_m'],dtype=float)
            if energy.shape!=(2,3,8) or not np.isfinite(energy).all() or np.any(energy<0): raise ValueError('Finite nonnegative H/F period bands required')
        elif record['status']=='withheld':
            unresolved_observed=True; reasons.update(record.get('reasons',['unknown_positive_contribution']))
        else: raise ValueError('Unsupported point status')
        record['relative_energy_per_m']=None if energy is None else energy.tolist()
        record.update(node_id=len(nodes),source_cell_id=cell['segment_id'],fraction=t,xy_bng=xy,evaluation_origin=origin)
        nodes.append(record); cache[key]=(record['node_id'],energy)
        return cache[key]
    def sample(ci,t):
        key=(ci,t)
        if key in cache: return cache[key]
        if len(nodes)>=config['maximum_evaluations']: raise Budget()
        cell=cells[ci];a,b=cell['start_xy_bng'],cell['end_xy_bng']
        return insert(ci,t,evaluate([x+(y-x)*t for x,y in zip(a,b)]),'new_geographic_evaluation')
    cell_indexes={c['segment_id']:i for i,c in enumerate(cells)}
    for seed in seed_evaluations:
        ci=cell_indexes[seed['source_cell_id']];t=seed['fraction']
        if isinstance(t,bool) or not math.isfinite(t) or not 0<t<1: raise ValueError('Interior seed fraction required')
        if (ci,t) in cache: raise ValueError('Duplicate seed observation')
        xy=[x+(y-x)*t for x,y in zip(cells[ci]['start_xy_bng'],cells[ci]['end_xy_bng'])]
        if not np.allclose(xy,seed['xy_bng'],rtol=0,atol=1e-8): raise ValueError('Seed observation moved')
        if len(nodes)>=config['maximum_evaluations']: raise ValueError('Seed observations exceed budget')
        insert(ci,t,seed,'verified_prior_generation')
    def panel(ci,left,right,depth=0):
        mid=(left+right)/2; width=(right-left)*cells[ci]['length_m']
        fractions=[mid,(3*left+right)/4,(left+3*right)/4,
            mid-(right-left)/(2*math.sqrt(3)),mid+(right-left)/(2*math.sqrt(3))]
        values=[sample(ci,t) for t in fractions]
        known=lambda ids: sum((values[i][1]*width/2 for i in ids if values[i][1] is not None),start=np.zeros((2,3,8)))
        fine=known((1,2)); offset=known((3,4)); coarse=np.zeros((2,3,8)) if values[0][1] is None else values[0][1]*width
        complete=all(v[1] is not None for v in values)
        error=np.maximum(abs(fine-coarse),abs(offset-fine)) if complete else np.zeros((2,3,8))
        probe_ids=[value[0] for (index,t),value in cache.items() if index==ci and left<t<right and value[1] is not None]
        if complete:
            # A narrower rule can miss a peak already observed by its parent.
            # Keep every historical interior probe in the refinement diagnostic.
            # This range excess is still a proxy, not an unsampled-function bound.
            current=np.stack([v[1] for v in values]); low,high=current.min(axis=0),current.max(axis=0)
            probes=[np.asarray(nodes[i]['relative_energy_per_m']) for i in probe_ids]
            prior=np.stack(probes)
            excess=np.maximum(0.,np.maximum(prior.max(axis=0)-high,low-prior.min(axis=0)))
            error=np.maximum(error,excess*width)
        return dict(cell_index=ci,left_fraction=left,right_fraction=right,depth=depth,length_m=width,
            node_ids=[v[0] for v in values],historical_probe_ids=probe_ids,energy=fine,offset_energy=offset,error=error,complete=complete)
    base=[]
    for ci,cell in enumerate(cells):
        cuts=[v['fraction'] for v in partition['cells'][cell['segment_id']]]
        if cuts[0]!=0 or cuts[-1]!=1 or any(not math.isfinite(t) or not 0<=t<=1 for t in cuts) or any(a>=b for a,b in zip(cuts,cuts[1:])): raise ValueError('Ordered full partition required')
        for a,b in zip(cuts,cuts[1:]):
            count=max(1,math.ceil((b-a)*cell['length_m']/config['maximum_panel_m']))
            base.extend((ci,a+(b-a)*j/count,a+(b-a)*(j+1)/count) for j in range(count))
    history=[]; termination=None; refinements=0; base_complete=False
    try:
        if unresolved_observed:raise Unknown()
        # Root intervals have five distinct interior nodes each. Reused points
        # reduce fresh calls, but cannot reduce this observation lower bound.
        if 5*len(base)>config['maximum_evaluations']:raise InitialBudget()
        for ci,a,b in base:
            leaves.append(panel(ci,a,b))
            if unresolved_observed:raise Unknown()
        base_complete=True
        while True:
            energy=sum((p['energy'] for p in leaves),start=np.zeros((2,3,8)))
            error=sum((p['error'] for p in leaves),start=np.zeros((2,3,8)))
            proxy=indicator_db(energy,error)
            if not history or refinements%25==0: history.append(dict(refinements=refinements,evaluations=len(nodes),indicator_db=proxy))
            if unresolved_observed: termination='unknown_positive_contribution_observed'; break
            if proxy is not None and proxy<=config['tolerance_db']: termination='diagnostic_tolerance_met'; break
            if refinements>=config['maximum_refinements']: termination='refinement_budget_exhausted'; break
            available=[(i,p) for i,p in enumerate(leaves) if p['length_m']/2>=config['minimum_panel_m']]
            if not available: termination='minimum_panel_reached'; break
            totals=_a_components(energy)
            def priority(item):
                e=_a_components(item[1]['error'])
                return float(np.max(np.divide(e,totals,out=np.zeros_like(e),where=totals>0)))
            i,p=max(available,key=priority)
            if priority((i,p))==0.: termination='minimum_panel_reached'; break
            mid=(p['left_fraction']+p['right_fraction'])/2
            # Transactional replacement: a budget interruption retains the full
            # old partition. Any new unknown sample still withholds the road.
            children=[panel(p['cell_index'],p['left_fraction'],mid,p['depth']+1),panel(p['cell_index'],mid,p['right_fraction'],p['depth']+1)]
            leaves[i:i+1]=children; refinements+=1
    except Unknown: termination='unknown_positive_contribution_observed'
    except InitialBudget: termination='initial_partition_budget_exhausted'
    except Budget: termination='unknown_positive_contribution_observed' if unresolved_observed else 'evaluation_budget_exhausted'
    energy=sum((p['energy'] for p in leaves),start=np.zeros((2,3,8)))
    offset=sum((p['offset_energy'] for p in leaves),start=np.zeros((2,3,8)))
    error=sum((p['error'] for p in leaves),start=np.zeros((2,3,8)))
    covered=math.fsum(p['length_m'] for p in leaves)
    all_known=base_complete and not unresolved_observed and all(p['complete'] for p in leaves)
    met=all_known and termination=='diagnostic_tolerance_met'
    whole=energy.tolist() if met else None
    partial=energy.tolist()
    leaf_records=[]
    for p in leaves:
        rec={k:v for k,v in p.items() if k not in ('energy','offset_energy','error')}
        rec.update(source_cell_id=cells[p['cell_index']]['segment_id'],relative_energy=p['energy'].tolist(),offset_relative_energy=p['offset_energy'].tolist(),absolute_local_error_proxy=p['error'].tolist())
        leaf_records.append(rec)
    metrics={state:receiver_metrics(energy[i]) for i,state in enumerate(('homogeneous','favourable'))}
    offset_metrics={state:receiver_metrics(offset[i]) for i,state in enumerate(('homogeneous','favourable'))}
    changes={s:{k:None if metrics[s][k] is None or offset_metrics[s][k] is None else offset_metrics[s][k]-metrics[s][k] for k in metrics[s]} for s in metrics}
    history.append(dict(refinements=refinements,evaluations=len(nodes),indicator_db=indicator_db(energy,error)))
    return dict(method=METHOD,status='conditional_direct_component_numerical_diagnostic_met' if met else 'withheld_numerical_or_physical_support',
        termination=termination,controls=config,minimum_initial_observations=5*len(base),initial_partition_exceeds_budget=5*len(base)>config['maximum_evaluations'],source_length_m=total_length,covered_source_length_m=covered,
        unevaluated_source_length_m=max(0.,total_length-covered),partition=partition,leaves=leaf_records,evaluations=nodes,
        evaluation_count=len(nodes),new_evaluation_count=sum(n['evaluation_origin']=='new_geographic_evaluation' for n in nodes),refinements=refinements,history=history,reason_counts=dict(reasons),
        unknown_positive_sample_observed=unresolved_observed,diagnostic_tolerance_met=met,
        absolute_error_proxy=error.tolist(),indicator_db=indicator_db(energy,error),offset_change_db=changes,
        whole_road_relative_energy=whole,known_evaluated_relative_energy=partial,offset_relative_energy=offset.tolist(),
        refinement_convergence_proven=False,path_transition_partition_complete=False,rigorous_noise_error_bound_db=None,
        scope='direct_only_reflections_omitted',complete_physical_path_support=False,real_area_validated=False,all_inclusive_noise_db=None)
