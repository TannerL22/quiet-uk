"""Independent native/scalar review; does not import evaluated policy or kernels."""
import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from contextlib import ExitStack
import csv
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from shapely import contains_xy, from_wkt
from shapely.geometry import Point, box
from shapely.strtree import STRtree

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


def read(path):return json.loads(Path(path).read_bytes())


def close(a,b):
    if a is None or b is None:assert a is None and b is None,(a,b)
    else:assert math.isclose(a,b,rel_tol=2e-10,abs_tol=2e-9),(a,b)


def scalar_top(feature,case):
    if feature['kind']=='building':
        if feature['original_height_status']=='provisional_surface_height':
            return feature['native_summary']['surface_odn_m'][case['roof_statistic']]
        base=feature['native_summary']['reference_base_odn_m'];height=case['missing_building_height_m']
        return base+height if base is not None and height is not None else None
    included=feature['category']=='generic_wall_candidate' and case['barrier_mode'] in ('solid_walls','solid_fences_and_walls')
    included|=feature['category']=='generic_fence_candidate' and case['barrier_mode']=='solid_fences_and_walls'
    if not included:return None
    height=feature['tagged_height_m'] if feature['tagged_height_m'] is not None else case['barrier_reference_height_m']
    return feature['reference_base_odn_m']+height if feature['reference_base_odn_m'] is not None and height is not None else None


def scalar_source(road,coeff,surface):
    power={p:[0.]*8 for p in ('day','evening','night')};category_fields=road['annual_categories']
    for field,count in category_fields.items():
        if count==0:continue
        category=('4b' if field=='two_wheeled_motor_vehicles' else '2' if field in ('buses_and_coaches','HGVs_2_rigid_axle') else '1' if field in ('cars_and_taxis','LGVs') else '3')
        v=road['speed']['choices'][field]['candidate_kmh'];s=coeff['surfaces'][surface]
        if (s['minimum_speed_kmh'] is not None and v<s['minimum_speed_kmh']) or (s['maximum_speed_kmh'] is not None and v>s['maximum_speed_kmh']):return None
        c=coeff['categories'][category];a=s['categories'][category]['alpha'];b=s['categories'][category]['beta']
        for band in range(8):
            prop=10**((c['AP'][band]+c['BP'][band]*(v-70)/70+min(a[band],0))/10)
            rolling=0 if category=='4b' else 10**((c['AR'][band]+(c['BR'][band]+b)*math.log10(v/70)+a[band])/10)
            for p in power:
                q=road['conditional_baseline']['period_traffic'][field][p]['assumed_average_vehicles_per_hour']
                power[p][band]+=(rolling+prop)*q/(1000*v)
    power['den_penalised_source']=[(12*power['day'][i]+4*math.sqrt(10)*power['evening'][i]+80*power['night'][i])/24 for i in range(8)]
    return power


def native_selection(geometry,xx,yy,transform,identity,role,counts,ties):
    """Independently check centre containment; preserve GDAL's boundary tie rule.

    Strict Shapely containment excludes every boundary point; the retained GDAL
    polygon burn uses its own edge convention. Only differences within 1e-7 m
    of the mathematical boundary may use that convention, and each is recorded.
    All other pixel membership is checked independently, not waived by counts.
    """
    strict=contains_xy(geometry,xx,yy)
    if geometry.is_empty:return strict
    native=rasterize([(geometry,1)],out_shape=strict.shape,transform=transform,fill=0,dtype='uint8',all_touched=False).astype(bool)
    differences=np.argwhere(strict!=native)
    if len(differences):
        cells=[]
        for i,j in differences:
            x,y=float(xx[i,j]),float(yy[i,j]);distance=geometry.boundary.distance(Point(x,y))
            assert distance<=1e-7,(identity,role,x,y,distance)
            cells.append(dict(x_bng=x,y_bng=y,boundary_distance_m=distance,strict_contains=bool(strict[i,j]),retained_gdal_selected=bool(native[i,j])))
        ties.append(dict(feature_id=identity,role=role,cells=cells))
        counts['native_polygon_boundary_tie_pixels']+=len(cells)
    counts['independent_centre_membership_agreements']+=strict.size-len(differences)
    return native


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace review required')
    helper.verified(inputs); report=read(inputs/'report.json'); parents={n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==report['parent_inventory_sha256'][n]
    for name,digest in report['code_sha256'].items():assert file_hash(ROOT/name)==digest
    features=read(inputs/'physical_features.json')['features'];cases=read(inputs/'protocol.json')['cases'];counts=Counter();boundary_ties=[]
    physical=parents['physical_scene_inputs_v2'];ground=parents['ground_cover_inputs_v3']
    with (physical/'building_heights_bng.csv').open(encoding='utf-8',newline='') as f:buildings=list(csv.DictReader(f))
    with ExitStack() as stack:
        datasets=[stack.enter_context(rasterio.open(physical/n)) for n in ('terrain_odn_2m.tif','surface_odn_2m.tif','dtm_survey_code_2m.tif','dsm_survey_code_2m.tif')]
        dtm,dsm,tc,sc=datasets
        for row in buildings:
            identity='os:'+row['source_id']+':'+row['source_record_number'];r=features[identity]
            assert r['original_height_status']==row['status'] and r['acoustic_obstacle_height_m'] is None and r['qualified_top_elevation_odn_m'] is None
            geometry=from_wkt(row['geometry_wkt_bng']);g=geometry.buffer(-2.);clipped=geometry.intersection(box(*dtm.bounds))
            if clipped.is_empty:
                assert r['native_summary']['reference_base_odn_m'] is None
                continue
            w=from_bounds(*clipped.bounds,transform=dtm.transform)
            left,top=math.floor(w.col_off),math.floor(w.row_off);right,bottom=math.ceil(w.col_off+w.width),math.ceil(w.row_off+w.height)
            window=Window(left,top,right-left,bottom-top);transform=dtm.window_transform(window)
            xx,yy=np.meshgrid(transform.c+(np.arange(right-left)+.5)*2,transform.f-(np.arange(bottom-top)+.5)*2)
            interior=native_selection(g,xx,yy,transform,identity,'buffered_interior',counts,boundary_ties)
            footprint=native_selection(geometry,xx,yy,transform,identity,'footprint_reference_base',counts,boundary_ties)
            t,s=dtm.read(1,window=window),dsm.read(1,window=window);tcode,scode=tc.read(1,window=window),sc.read(1,window=window)
            valid=(dtm.read_masks(1,window=window)>0)&(dsm.read_masks(1,window=window)>0)&np.isfinite(t)&np.isfinite(s)
            paired=interior&valid&(tcode==scode)&(tcode>0)&(tcode!=65535)
            assert int(paired.sum())==r['native_summary']['paired_pixels'],(identity,int(paired.sum()),r['native_summary']['paired_pixels'])
            if row['status']=='provisional_surface_height':
                assert int(paired.sum())==int(row['paired_pixels'])
                for name,value in zip(('p10','p50','p90'),np.percentile(s[paired].astype(float),[10,50,90])):close(r['native_summary']['surface_odn_m'][name],float(value))
                counts['accepted_native_building_absolute_summaries']+=1
            base=t[footprint&(dtm.read_masks(1,window=window)>0)&np.isfinite(t)]
            expected=float(base.max()) if len(base) and box(*dtm.bounds).covers(geometry) else None
            close(r['native_summary']['reference_base_odn_m'],expected)
            counts['native_building_reference_bases']+=1
            if counts['native_building_reference_bases']%10000==0:print(f'Independent native summaries {counts["native_building_reference_bases"]}',flush=True)
    barriers=read(ground/'barrier_candidates_bng.json')['candidates']
    with rasterio.open(physical/'terrain_odn_2m.tif') as dataset:
        terrain=dataset.read(1);mask=dataset.read_masks(1);west,south,east,north=dataset.bounds
    for row in barriers:
        f=features['osm:'+str(row['osm_way_id'])];geometry=from_wkt(row['geometry_wkt_bng']);values=[]
        lines=list(geometry.geoms) if geometry.geom_type=='MultiLineString' else [geometry]
        for line in lines:
            for a,b in zip(line.coords,list(line.coords)[1:]):
                fractions={0.,1.}
                for k,origin in ((0,west),(1,south)):
                    if a[k]==b[k]:continue
                    for i in range(math.floor((min(a[k],b[k])-origin)/2)+1,math.ceil((max(a[k],b[k])-origin)/2)):
                        fraction=(origin+2*i-a[k])/(b[k]-a[k])
                        if 0<fraction<1:fractions.add(fraction)
                cuts=sorted(fractions)
                for lo,hi in zip(cuts,cuts[1:]):
                    fraction=(lo+hi)/2;x=a[0]+fraction*(b[0]-a[0]);y=a[1]+fraction*(b[1]-a[1])
                    if not west<=x<east or not south<y<=north:continue
                    rr,cc=math.floor((north-y)/2),math.floor((x-west)/2)
                    if mask[rr,cc] and math.isfinite(float(terrain[rr,cc])):values.append(float(terrain[rr,cc]))
        expected=max(values) if values and box(west,south,east,north).covers(geometry) else None
        close(f['reference_base_odn_m'],expected)
        assert f['category']==row['category'] and f['tagged_height_m']==row['tagged_height_m']
        assert f['opaque_and_sealed'] is None and f['acoustic_absorption'] is None
        counts['original_barrier_identities']+=1;counts['native_barrier_reference_bases']+=1
    del terrain,mask
    source_inputs=read(inputs/'source_physical_inputs.json')['roads']
    source_roads=read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']
    operating={r['link_id']:r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coefficients=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    ways=read(parents['road_direction_inputs_v2']/'osm_way_evidence_bng.json')['ways']
    structures=[w for w in ways if any(w['tags'].get(k,'no') not in ('no','false','0','') for k in ('bridge','tunnel'))]
    buffers=[from_wkt(w['geometry_wkt_bng']).buffer(3.) for w in structures];tree=STRtree(buffers)
    for source in source_roads:
        link=source['link_id'];actual=source_inputs[link];geometry=from_wkt(source['geometry_wkt_bng'])
        hits={structures[int(i)]['way_id']:geometry.intersection(buffers[int(i)]).length for i in tree.query(geometry,predicate='intersects')}
        hits={k:v for k,v in hits.items() if v>0}
        contexts=actual['physical_source']['positive_structure_context']
        assert {c['osm_way_id'] for c in contexts}==set(hits)
        for c in contexts:close(c['source_length_within_3m_m'],hits[c['osm_way_id']])
        assert actual['physical_source']['status']==('withheld_structure_proximity_conflict' if hits else 'conditional_at_grade_reference')
        assert actual['physical_source']['qualified_deck_odn_m'] is None
        for p in actual['pavement_cases']:
            surface=p['surface']
            expected=scalar_source(operating[link],coefficients,surface)
            if expected is None:assert p['status']=='withheld' and p['period_spectra'] is None;counts['withheld_pavement_cases']+=1
            else:
                assert p['status']=='conditional_research_source_emission'
                for period,values in expected.items():
                    for band,value in enumerate(values):
                        close(p['period_spectra'][period]['relative_power_per_m'][band],value)
                        close(p['period_spectra'][period]['sound_power_db_re_1e_minus_12_W_per_m'][band],10*math.log10(value) if value>0 else None)
                        counts['source_surface_band_values']+=1
            counts['pavement_cases']+=1
        counts['road_structure_contexts']+=1
    summaries=read(inputs/'profile_sensitivity.json')['profiles'];lookup={(r['link_id'],r['profile_id']):r for r in summaries}
    assert len(lookup)==len(summaries)
    with gzip.open(parents['real_road_scenes_v2']/'prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            road=json.loads(line);geographic=road['scene'];link=road['link_id']
            profiles=[('direct',geographic['direct'],None)]+[(f'reflection:{i}',r['profile'],r) for i,r in enumerate(geographic['reflections'])]
            for identity,profile,reflection in profiles:
                result=lookup[(link,identity)];assert result['receiver_noise_db'] is None and result['acoustic_path_support_complete'] is False
                points=profile['kernel_scene']['points'];length=profile['length_m'];source=profile['kernel_scene']['source'][1];receiver=profile['kernel_scene']['receiver'][1]
                source_ok=source_inputs[link]['physical_source']['status']=='conditional_at_grade_reference'
                for case,record in zip(cases,result['case_results'],strict=True):
                    assert case['case_id']==record['case_id'];vertices=[];unresolved=set();omitted=set();obstacles=0
                    total,weighted,missing=0.,0.,0.;basis_lengths=Counter();cursor=0.
                    for a,b,_,_,code,state in profile['ground_native_samples']:
                        close(a,cursor);cursor=b;total+=b-a
                        if state!='reported_class' or code not in (10,20,30,40,50,60,70,80,90,95,100):
                            missing+=b-a;basis='missing_or_unsupported_native_class';g=None
                        elif case['ground_mode']=='uniform_recognised':
                            g=case['ambiguous_ground_g'];basis='uniform_ground_stress_case'
                        elif code in (30,40):g=1.;basis='assumed_porous_grass_or_crop_substrate'
                        elif code in (50,80):g=0.;basis='assumed_sealed_built_ground_or_water'
                        else:g=case['ambiguous_ground_g'];basis='unobserved_substrate_reference'
                        if g is not None:weighted+=(b-a)*g
                        basis_lengths[basis]+=b-a
                    close(total,length);close(record['candidate_ground']['unknown_length_m'],missing)
                    close(record['candidate_ground']['full_length_candidate_mean_g'],weighted/length if missing==0 else None)
                    assert set(basis_lengths)==set(record['candidate_ground']['basis_lengths_m'])
                    for k,v in basis_lengths.items():close(v,record['candidate_ground']['basis_lengths_m'][k])
                    assert record['candidate_ground']['observed_g'] is None
                    counts['profile_case_ground_support']+=1
                    for crossing in profile['crossings']:
                        feature=features[crossing['feature_id']];top=scalar_top(feature,case)
                        if feature['kind']=='barrier':
                            included=(feature['category']=='generic_wall_candidate' and case['barrier_mode'] in ('solid_walls','solid_fences_and_walls')) or (feature['category']=='generic_fence_candidate' and case['barrier_mode']=='solid_fences_and_walls')
                            if not included:omitted.add(crossing['feature_id']);continue
                        contacts=crossing['contacts_m']
                        if reflection and crossing['feature_id'] in reflection['coincident_feature_ids']:
                            contacts=[x for x in contacts if not any(math.isclose(x,p,abs_tol=1e-7) for p in (0.,length,reflection['wall']['unfolded_x_m']))]
                        if not contacts and not crossing['intervals_m']:continue
                        if top is None:unresolved.add(crossing['feature_id']);continue
                        obstacles+=len(contacts)+len(crossing['intervals_m'])
                        vertices.extend((x,top,crossing['feature_id']) for x in contacts)
                        vertices.extend((x,top,crossing['feature_id']) for ab in crossing['intervals_m'] for x in ab)
                    diagnostic=record['geometry_diagnostic'];assert diagnostic['candidate_obstacles']==obstacles
                    assert set(record['unresolved_feature_ids'])==unresolved and set(record['omitted_screen_feature_ids'])==omitted
                    if source_ok and source is not None and receiver is not None:
                        def gap(x,z):return z-source-(receiver-source)*x/length
                        heights=[gap(x,z) for x,z,_ in vertices]
                        close(diagnostic['maximum_obstacle_clearance_m'],max(heights,default=None))
                        close(diagnostic['maximum_terrain_clearance_m'],max((gap(x,z) for x,z in points if z is not None),default=None))
                        assert diagnostic['obstacles_above_endpoint_sightline']==len({i for x,z,i in vertices if gap(x,z)>0})
                    else:
                        assert diagnostic['maximum_obstacle_clearance_m'] is None and diagnostic['maximum_terrain_clearance_m'] is None
                    if unresolved:assert 'missing_building_or_screen_height' in record['geometry_blockers']
                    if not source_ok:assert 'source_surface_or_deck_unresolved' in record['geometry_blockers']
                    assert record['status']==('withheld_geometry_candidate' if record['geometry_blockers'] else 'conditional_geometry_candidate')
                    assert record['receiver_noise_db'] is None
                    if reflection:
                        tops=[scalar_top(features[f],case) for f in reflection['coincident_feature_ids']]
                        expected=tops[0] if all(t==tops[0] for t in tops) else None
                        close(record['candidate_wall']['top_z_m'],expected)
                        assert record['candidate_wall']['absorption_by_band'] is None and record['candidate_wall']['incident_view_height_m'] is None
                    counts['profile_case_geometry']+=1
                counts['profiles']+=1
    assert counts['profiles']==report['profiles'] and counts['profile_case_geometry']==report['profiles']*report['physical_cases']
    assert counts['road_structure_contexts']==722 and len(features)==len(buildings)+len(barriers)
    assert report['receiver_noise_db'] is None and report['eligible_for_absolute_noise_release'] is False
    output.mkdir(parents=True)
    review=dict(status='passed_native_and_scalar_review',counts=dict(counts),input_inventory_sha256=file_hash(inputs/'inventory.json'),
                parent_inventory_sha256=report['parent_inventory_sha256'],review_code_sha256=file_hash(Path(__file__)),
                scope='All accepted native absolute building summaries, barrier identities, road structure context, all pavement powers and every prepared profile/case sightline. Construction fidelity, not acoustic accuracy.',
                real_area_noise_validated=False,external_engine_executed=False,eligible_for_absolute_noise_release=False)
    (output/'review.json').write_bytes(json_bytes(review));shutil.copyfile(__file__,output/Path(__file__).name)
    (output/'native_polygon_boundary_ties.json').write_bytes(json_bytes(dict(interpretation='GDAL edge convention retained only for documented near-exact boundary ties; nonboundary pixels independently checked by geometric containment',tolerance_m=1e-7,records=boundary_ties)))
    for p in parents.values():helper.verified(p)
    helper.verified(inputs)
    print(dict(generation_id=helper.seal(output,'physical-scene-assumption-review-'),counts=review['counts']),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',default=str(ROOT/'artifacts/physical_scene_assumptions_v2'));parser.add_argument('--output',default=str(ROOT/'artifacts/physical_scene_assumption_review_v1'))
    args=parser.parse_args();run(args.inputs,args.output)
