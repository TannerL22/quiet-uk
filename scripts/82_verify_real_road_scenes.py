"""Verify geographic preparation from original rasters/vectors and traffic budgets.

Does not import the scene builder or its runner. Checks source conservation,
analytic ray/cell intersections, original raster values, independent categorical
sampling, raw vector intersections, receiver-mirror plans and all withheld states.
"""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import pyproj
import rasterio
from shapely import from_wkt
from shapely.geometry import LineString,Point,box
from shapely.strtree import STRtree

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('frozen_inventory',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
def read(p):return json.loads(Path(p).read_bytes())
def rows(p):
    with Path(p).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
def close(a,b):
    if not math.isclose(a,b,rel_tol=1e-10,abs_tol=1e-7):raise ValueError(('Independent geometry differs',a,b))
def pieces(g,kind):
    if g.is_empty:return []
    if g.geom_type==kind:return [g]
    return [p for v in getattr(g,'geoms',[]) for p in pieces(v,kind)]


def closest_point(line,receiver):
    choices=[];offset=0.
    for part in pieces(line,'LineString'):
        for a,b in zip(part.coords, list(part.coords)[1:]):
            a,b=np.array(a),np.array(b);v=b-a;length=float(np.linalg.norm(v))
            if length==0:continue
            t=max(0.,min(1.,float(np.dot(np.array(receiver)-a,v)/(length*length))))
            xy=a+t*v;choices.append((float(np.linalg.norm(xy-receiver)),offset+t*length,xy));offset+=length
    return min(choices,key=lambda q:(q[0],q[1]))


def mirror_plan(s,r,a,b):
    s,r,a,b=[np.asarray(v,dtype=float) for v in (s,r,a,b)];tangent=(b-a)/np.linalg.norm(b-a)
    normal=np.array([-tangent[1],tangent[0]]);mirror=r-2*normal*np.dot(r-a,normal)
    t,u=np.linalg.solve(np.column_stack((mirror-s,-(b-a))),a-s);hit=s+t*(mirror-s)
    return hit,float(u),float(np.linalg.norm(hit-s)),float(np.linalg.norm(hit-r))


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use fresh workspace verification output')
    helper.verified(inputs);digest=file_hash(inputs/'inventory.json');report=read(inputs/'report.json');protocol=read(inputs/'protocol.json')
    parents={n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==report['parent_inventory_sha256'][n]
    for n,h in report['code_sha256'].items():assert file_hash(ROOT/n)==h and file_hash(inputs/'code'/n)==h
    physical,ground,roads,emissions=[parents[n] for n in ('physical_scene_inputs_v2','ground_cover_inputs_v3','road_direction_inputs_v2','road_emission_assumptions_v2')]
    assert file_hash(inputs/'road_input_inventory_bng.csv')==file_hash(roads/'road_representation_ledger_bng.csv')
    ledger={r['link_id']:r for r in rows(roads/'road_representation_ledger_bng.csv')}
    budgets={r['link_id']:g for g in read(roads/'carriageway_budgets.json')['groups'] for r in g['representations']}
    assignments={g['link_id']:g for g in read(emissions/'group_evaluation_index.json')['groups']}
    source=read(inputs/'source_lines.json');original_anchor={e['evaluation_id']:e for e in read(emissions/'donor_source_scenarios.json')['evaluations'] if e['case_id']=='anchor'}
    assert source['emission_cases']==original_anchor
    sources={s['link_id']:s for s in source['source_roads']};assert len(sources)==len(source['source_roads']) and set(sources)==set(budgets)==set(assignments)
    counts=Counter();extent=box(*protocol['study']['source_halo_bounds']);receiver=np.array(protocol['receiver_xy_bng']);source_geoms={}
    for id,s in sources.items():
        original=from_wkt(ledger[id]['geometry_wkt_bng']);clipped=original.intersection(extent);geom=from_wkt(s['geometry_wkt_bng']);source_geoms[id]=geom
        assert geom.equals(clipped) and s['traffic_budget']==budgets[id] and s['original_geometry_wkt_bng']==ledger[id]['geometry_wkt_bng']
        assert s['source_emission_id']=='anchor:'+assignments[id]['evaluation_donor_id'];assert s['qualified_relative_power_per_m'] is None
        close(s['original_length_m'],original.length);close(s['prepared_length_m'],geom.length);close(s['excluded_outside_extract_length_m'],max(0.,original.length-geom.length))
        close(sum(c['length_m'] for c in s['source_cells']),geom.length);ids=set()
        for cell in s['source_cells']:
            assert cell['segment_id'] not in ids;ids.add(cell['segment_id'])
            a,b=cell['start_xy_bng'],cell['end_xy_bng'];close(cell['length_m'],math.dist(a,b))
            assert cell['length_m']<=protocol['source_cell_maximum_length_m']+1e-7
            assert geom.covers(LineString([a,b])) or geom.distance(LineString([a,b]))<1e-7
            for t in (.25,.5,.75):assert geom.distance(Point([(1-t)*x+t*y for x,y in zip(a,b)]))<1e-7
            close(cell['end_chainage_m']-cell['start_chainage_m'],cell['length_m'])
            assert cell['relative_power_per_m'] is None and cell['qualified_source_elevation_odn_m'] is None and cell['path_breakpoints'] is None
            counts['source_cells_checked']+=1
        counts['source_roads_checked']+=1
    building_rows=rows(physical/'building_heights_bng.csv');building_ids=['os:'+r['source_id']+':'+r['source_record_number'] for r in building_rows]
    buildings=[from_wkt(r['geometry_wkt_bng']) for r in building_rows];bt=STRtree(buildings)
    barrier_rows=read(ground/'barrier_candidates_bng.json')['candidates'];barrier_ids=['osm:'+str(r['osm_way_id']) for r in barrier_rows]
    barriers=[from_wkt(r['geometry_wkt_bng']) for r in barrier_rows];barriert=STRtree(barriers)
    facets={}
    for id,g in zip(building_ids,buildings):
        for i,polygon in enumerate(pieces(g,'Polygon')):
            coordinates=list(polygon.exterior.coords)
            for j,(a,b) in enumerate(zip(coordinates,coordinates[1:])):
                if math.dist(a,b)>0:facets[f'{id}:{i}:{j}']=(list(a),list(b))
    with rasterio.open(physical/'terrain_odn_2m.tif') as d:
        values,mask,transform,bounds=d.read(1),d.read_masks(1),d.transform,d.bounds
    with rasterio.open(ground/'landcover_native_wgs84.tif') as d:
        classes,class_mask,class_transform=d.read(1),d.read_masks(1),d.transform
    operation=pyproj.Transformer.from_pipeline(read(ground/'report.json')['coordinate_transform']['pipeline'])
    def cell_at(xy):
        x,y=xy;column=int(math.floor((x-bounds.left)/transform.a));row=int(math.floor((bounds.top-y)/(-transform.e)))
        if not 0<=row<values.shape[0] or not 0<=column<values.shape[1]:return None,None,None
        z=float(values[row,column]);return row,column,z if mask[row,column] and math.isfinite(z) else None
    def check_profile(profile,nodes):
        legs=[LineString([a,b]) for a,b in zip(nodes,nodes[1:])];lengths=[l.length for l in legs];length=sum(lengths);close(profile['length_m'],length)
        close(sum(b-a for a,b,*_ in profile['terrain_native_intervals']),length)
        offset=0.;expected_points=[];expected_ground=[];expected_crossings=[]
        for i,line in enumerate(legs):
            a,b=np.array(nodes[i]),np.array(nodes[i+1]);v=b-a;leglength=lengths[i]
            if i==0:expected_points.append([0.,cell_at(a)[2]])
            intervals=[p for p in profile['terrain_native_intervals'] if offset<= (p[0]+p[1])/2 <offset+leglength]
            previous=offset
            for start,end,row,col,z in intervals:
                close(start,previous);previous=end;mid=(start+end)/2-offset;xy=a+v*mid/leglength
                rr,cc,zz=cell_at(xy);assert row==rr and col==cc and z==zz
                if row is not None:
                    lower=np.array([bounds.left+col*transform.a,bounds.top+(row+1)*transform.e]);upper=lower+np.array([transform.a,-transform.e])
                    lo,hi=0.,1.
                    for k in range(2):
                        if v[k]:
                            t0,t1=sorted(((lower[k]-a[k])/v[k],(upper[k]-a[k])/v[k]));lo=max(lo,t0);hi=min(hi,t1)
                    close(start-offset,max(0.,lo)*leglength);close(end-offset,min(1.,hi)*leglength)
                expected_points.append([(start+end)/2,z]);counts['native_terrain_intervals_checked']+=1
            close(previous,offset+leglength);expected_points.append([offset+leglength,cell_at(b)[2]])
            n=math.ceil(leglength/protocol['ground_midpoint_step_m']);xy=a+(np.arange(n)[:,None]+.5)/n*v
            lon,lat=operation.transform(xy[:,0],xy[:,1]);columns=np.floor((lon-class_transform.c)/class_transform.a).astype(int);rs=np.floor((lat-class_transform.f)/class_transform.e).astype(int)
            for j,(row,col) in enumerate(zip(rs,columns)):
                if not 0<=row<classes.shape[0] or not 0<=col<classes.shape[1]:key=[None,None,None,'outside']
                else:
                    code=int(classes[row,col]);state='nodata' if not class_mask[row,col] or code==0 else 'reported_class' if code in (10,20,30,40,50,60,70,80,90,95,100) else 'unsupported_class';key=[int(row),int(col),code,state]
                start,end=offset+j*leglength/n,offset+(j+1)*leglength/n
                if expected_ground and expected_ground[-1][2:]==key and j>0:expected_ground[-1][1]=end
                else:expected_ground.append([start,end,*key])
                counts['native_ground_samples_checked']+=1
            for tree,geoms,ids,kind in ((bt,buildings,building_ids,'building'),(barriert,barriers,barrier_ids,'barrier')):
                for ix in tree.query(line,predicate='intersects'):
                    intersection=line.intersection(geoms[int(ix)]);expected_crossings.append((i,ids[int(ix)],kind))
                    actual=next(c for c in profile['crossings'] if c['physical_leg']==i and c['feature_id']==ids[int(ix)])
                    want=[]
                    for piece in pieces(intersection,'LineString'):
                        xs=[float(np.dot(np.array(p)-a,v)/leglength)+offset for p in piece.coords];want.append([min(xs),max(xs)])
                    assert len(want)==len(actual['intervals_m'])
                    for p,q in zip(sorted(want),sorted(actual['intervals_m'])):
                        for x,y in zip(p,q):close(x,y)
                    contacts=[float(np.dot(np.array(p.coords[0])-a,v)/leglength)+offset for p in pieces(intersection,'Point')]
                    assert len(contacts)==len(actual['contacts_m'])
                    for x,y in zip(sorted(contacts),sorted(actual['contacts_m'])):close(x,y)
                    assert actual['acoustic_top_elevation_odn_m'] is None and actual['opacity'] is None;counts['raw_obstacle_crossings_checked']+=1
            offset+=leglength
        assert sorted((c['physical_leg'],c['feature_id'],c['kind']) for c in profile['crossings'])==sorted(expected_crossings)
        assert len(expected_points)==len(profile['kernel_scene']['points'])
        for p,q in zip(expected_points,profile['kernel_scene']['points']):close(p[0],q[0]);assert p[1]==q[1]
        assert len(expected_ground)==len(profile['ground_native_samples'])
        for p,q in zip(expected_ground,profile['ground_native_samples']):close(p[0],q[0]);close(p[1],q[1]);assert p[2:]==q[2:]
        scene=profile['kernel_scene'];assert scene['scene_kind']=='unqualified' and scene['source_g'] is None and scene['opaque'] is None and scene['infinite_lateral_extent'] is None
        assert profile['qualified_source_elevation_odn_m'] is None and profile['observed_ground_g'] is None and profile['noise_prediction'] is None
        assert all(g is None for _,_,g in scene['ground_segments'])
        sz,rz=cell_at(nodes[0])[2],cell_at(nodes[-1])[2]
        assert scene['source'][1]==(None if sz is None else sz+.05) and scene['receiver'][1]==(None if rz is None else rz+4.)
        counts['geographic_profiles_checked']+=1
    seen=set()
    with gzip.open(inputs/'prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            record=json.loads(line);id=record['link_id'];assert id not in seen;seen.add(id);scene=record['scene'];s=scene['source_xy_bng'];r=scene['receiver_xy_bng'];assert r==receiver.tolist()
            distance,position,xy=closest_point(source_geoms[id],receiver);close(record['source_point_chainage_m'],position)
            for a,b in zip(xy,s):close(float(a),b)
            close(scene['direct']['length_m'],distance);check_profile(scene['direct'],[s,r])
            assert scene['status']=='withheld' and record['annual_noise_prediction'] is None and all(v is None for v in record['road_receiver_contributions'].values())
            assert all(record['point_transfer_probe'][c]['status']=='withheld' for c in ('homogeneous','favourable'))
            assert scene['reflection_search']['support_complete'] is False
            for reflected in scene['reflections']:
                a,b=facets[reflected['facet_id']];hit,u,first,second=mirror_plan(s,r,a,b)
                assert 0<=u<=1 and first+second<=distance+protocol['reflection_extra_plan_length_m']+1e-7
                for x,y in zip(hit,reflected['plan']['reflection_xy']):close(float(x),y)
                close(reflected['plan']['wall_fraction'],u);close(reflected['wall']['unfolded_x_m'],first)
                close(reflected['plan']['unfolded_horizontal_distance_m'],first+second)
                assert reflected['wall']['top_z_m'] is None and reflected['wall']['absorption_by_band'] is None and reflected['wall']['unfolded_geometry_confirmed'] is None
                for facet in reflected['coincident_facet_ids']:assert set(map(tuple,facets[facet]))==set(map(tuple,(a,b)))
                # Audit cells on the retained finite plan, after checking its
                # independent mirror solution; do not shift a native-grid hit.
                check_profile(reflected['profile'],[s,reflected['plan']['reflection_xy'],r]);counts['finite_reflection_plans_checked']+=1
            counts['road_location_snapshots_checked']+=1
            if len(seen)%100==0:print(f'Independently verified {len(seen)} road snapshots',flush=True)
    assert seen==set(sources)
    readiness=read(inputs/'road_location_readiness.json');assert readiness['combined_road_noise_db'] is None and readiness['annual_noise_prediction'] is None
    assert len(readiness['roads'])==len(sources) and all(r['status']=='withheld' and r['receiver_level_db'] is None for r in readiness['roads'])
    output.mkdir(parents=True)
    receipt=dict(status='passed_independent_real_geographic_preparation_checks',counts=dict(counts),input_inventory_sha256=digest,
        original_budget_memberships_and_conditional_emission_cases_preserved=True,all_real_receiver_contributions_withheld=True,
        methods=['Original traffic budget/anchor-case equality','Raw source geometry and length conservation','Scalar closest-point projections',
            'Analytic ray/cell slab intersections and native pixel/mask equality','Independent native categorical midpoint sampling','Original footprint/barrier spatial intersections','Reflect receiver and solve finite-wall intersection'],
        source_catalogue_completeness_qualified=False,full_current_method_qualified=False,external_engine_executed=False,real_area_noise_validated=False,
        verification_code_sha256=file_hash(Path(__file__)))
    (output/'verification.json').write_bytes(json_bytes(receipt));shutil.copyfile(Path(__file__),output/Path(__file__).name)
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==digest
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==report['parent_inventory_sha256'][n]
    for n,h in report['code_sha256'].items():assert file_hash(ROOT/n)==h
    print(dict(generation_id=helper.seal(output,'real-road-scene-review-'),counts=dict(counts)),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',default=str(ROOT/'artifacts/real_road_scenes_v2'));p.add_argument('--output',default=str(ROOT/'artifacts/real_road_scene_review_v1'))
    a=p.parse_args();run(a.inputs,a.output)
