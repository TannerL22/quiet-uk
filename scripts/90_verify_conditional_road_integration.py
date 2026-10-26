"""Independent source, hourly air and quadrature accounting; sampled GIS replay.

Does not import the evaluated conditional adapter or source/air kernels. Frozen
kernel replay is an additional parity check, explicitly not independent acoustics
or field validation. All held roads and incomplete network terms remain unknown.
"""
import argparse
from collections import Counter,defaultdict
import csv
import gzip
import hashlib
import importlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import types

import numpy as np
from shapely import from_wkt
from shapely.geometry import MultiPoint

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
PERIODS=('day','evening','night');BANDS=(63,125,250,500,1000,2000,4000,8000)
AW=(-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1)


def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result


helper=module('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
prior_air_review=module('prior_air_review',ROOT/'scripts/86_verify_road_operating_inputs.py')
prior_physical_review=module('prior_physical_review',ROOT/'scripts/88_verify_physical_scene_assumptions.py')
builder_module=module('native_builder_replay',ROOT/'scripts/81_build_real_road_scenes.py')


def read(path):return json.loads(Path(path).read_bytes())


def close(a,b):
    if a is None or b is None:assert a is None and b is None,(a,b)
    else:assert math.isclose(a,b,rel_tol=3e-10,abs_tol=2e-9),(a,b)


def metrics(energy):
    period=[math.fsum(x*10**(aw/10) for x,aw in zip(row,AW)) for row in energy]
    den=(12*period[0]+4*math.sqrt(10)*period[1]+80*period[2])/24
    return {p:10*math.log10(v) if v>0 else None for p,v in zip((*PERIODS,'den'),(*period,den))}


def group_source_power(road,coeff,calendar,groups):
    powers=np.zeros((len(groups),3,8))
    for field,count in road['annual_categories'].items():
        assert count is not None and count>=0
        if count==0:continue
        category='4b' if field=='two_wheeled_motor_vehicles' else '2' if field in ('buses_and_coaches','HGVs_2_rigid_axle') else '1' if field in ('cars_and_taxis','LGVs') else '3'
        v=road['speed']['choices'][field]['candidate_kmh'];assert 20<=v<=130
        row=coeff['categories'][category];correction=coeff['surfaces']['Reference road surface']['categories'][category]
        g=groups.index(calendar['vehicle_profiles'][field]['profile_group'])
        fractions=calendar['vehicle_profiles'][field]['period_fractions']
        conserved=0.
        for pi,period in enumerate(PERIODS):
            q=road['conditional_baseline']['period_traffic'][field][period]['assumed_average_vehicles_per_hour']
            hours=(12,4,8)[pi];close(q,count*fractions[period]/hours);conserved+=hours*q
            for i in range(8):
                a,b=correction['alpha'][i],correction['beta']
                prop=10**((row['AP'][i]+row['BP'][i]*(v-70)/70+min(a,0))/10)
                roll=0. if category=='4b' else 10**((row['AR'][i]+(row['BR'][i]+b)*math.log10(v/70)+a)/10)
                powers[g,pi,i]+=(roll+prop)*q/(1000*v)
        close(conserved,count)
    baseline=road['conditional_baseline']['period_spectra']
    for pi,p in enumerate(PERIODS):assert np.allclose(powers[:,pi,:].sum(axis=0),baseline[p]['relative_power_per_m'],rtol=3e-12,atol=1e-9)
    return powers


def native_replay(node,builder,features,case,receiver,counts):
    prepared=builder.prepare_point(node['xy_bng'],receiver,reflections=False);profile=prepared['direct']
    assert hashlib.sha256(json_bytes(profile)).hexdigest()==node['native_profile_sha256']
    native=profile['kernel_scene'];actual=node['candidate_kernel_scene'];points=np.asarray(native['points']);reduced=np.asarray(actual['points'])
    assert np.array_equal(points[[0,-1]],reduced[[0,-1]]) and np.isfinite(points).all()
    error=float(np.max(np.abs(points[:,1]-np.interp(points[:,0],reduced[:,0],reduced[:,1]))))
    close(error,node['maximum_vertical_reconstruction_error_m']);assert error<=.1+1e-9
    assert node['native_terrain_points']==len(points) and node['candidate_terrain_points']==len(reduced)
    assert actual['source']==native['source'] and actual['receiver']==native['receiver']
    close(node['direct_distance_m'],math.dist(actual['source'],actual['receiver']))
    assert actual['scene_kind']=='conditional_real_input'
    ground=[]
    for a,b,_,_,code,state in profile['ground_native_samples']:
        assert state=='reported_class'
        g=1. if code in (30,40) else 0. if code in (50,80) else .5
        if ground and ground[-1][2]==g:ground[-1][1]=b
        else:ground.append([a,b,g])
    assert ground==actual['ground_segments']
    obstacles=[];omitted=[]
    for crossing in profile['crossings']:
        identity=crossing['feature_id'];feature=features[identity]
        top=prior_physical_review.scalar_top(feature,case)
        if feature['kind']=='barrier' and top is None:omitted.append(identity);continue
        assert top is not None
        for a,b in crossing['intervals_m']:obstacles.append(dict(vertices=[[a,top],[b,top]],roof_g=0.,feature_id=identity))
        for x in crossing['contacts_m']:obstacles.append(dict(vertices=[[x,top]],feature_id=identity))
    obstacles.sort(key=lambda r:(r['vertices'][0][0],r['vertices'][-1][0]))
    assert obstacles==actual['obstacles'] and sorted(omitted)==node['omitted_screen_feature_ids']
    counts['sampled_native_profiles_replayed']+=1;counts['sampled_native_terrain_knots_checked']+=len(points)
    return native['points']


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace review required')
    helper.verified(inputs);protocol=read(inputs/'protocol.json');parents={n:ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==protocol['parent_inventory_sha256'][n]
    counts=Counter();input_hash=file_hash(inputs/'inventory.json');report=read(inputs/'report.json');results=read(inputs/'road_contributions.json')
    assert results['combined_road_noise_db'] is None and results['all_inclusive_noise_db'] is None
    assert not results['real_area_validated'] and not results['complete_physical_path_support']
    assert protocol['a_weight_db']==list(AW) and protocol['unknown_network_sources']==13206
    sources={s['link_id']:s for s in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']}
    roads={r['link_id']:r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coeff=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    weather=read(parents['road_operating_inputs_v2']/'hourly_weather.json')['rows'];assert len(weather)==8760
    calendar=read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    air_meta=read(inputs/'air_tables.json');groups=air_meta['groups']
    # NPZ members are lazy compressed reads. Materialise the small tables once
    # so every node check does not repeatedly decompress the same hourly table.
    with np.load(inputs/'air_tables.npz',allow_pickle=False) as archive:air={name:archive[name] for name in archive.files}
    alphas=np.array([[prior_air_review.scalar_alpha(1000*10**(.3*(i-4)),w['candidate_absorption_air']['temperature_c'],w['candidate_absorption_air']['relative_humidity_percent'],w['candidate_absorption_air']['pressure_kpa']) for i in range(8)] for w in weather])
    assert np.allclose(alphas,air['alpha_db_per_km'],rtol=3e-13,atol=1e-12);counts['independent_hourly_air_coefficients']=alphas.size
    for bi,f in enumerate(BANDS):
        distance=air[f'distance_{f}'];assert distance[0]==0 and distance[-1]==10000 and np.all(np.diff(distance)>0)
        k=float(alphas[:,bi].max())*math.log(10)/10000;h=float(np.diff(distance).max());bound=10*math.log10(1+math.exp(k*h)*(k*h)**2/8)
        assert bound<=.002+1e-12;close(bound,air_meta['interpolation_bounds'][bi]['max_interpolation_db'])
        for pi,p in enumerate(PERIODS):
            ids=[i for i,w in enumerate(weather) if w['period']==p]
            weights=np.array([calendar['groups'][g]['slot_weights'] for g in groups])[:,ids];weights/=weights.sum(axis=1,keepdims=True)
            for start in range(0,len(distance),512):
                ds=distance[start:start+512]
                expected=weights@np.power(10.,-alphas[ids,bi,None]*ds[None,:]/10000)
                assert np.allclose(expected,air[f'{p}_{f}'][:,start:start+len(ds)],rtol=3e-10,atol=1e-280)
                counts['joint_hourly_transmission_table_values']+=expected.size
    powers={link:group_source_power(r,coeff,calendar,groups) for link,r in roads.items()};counts['source_traffic_budgets_checked']=len(powers)
    case_ids=[c['case_id'] for c in protocol['physical_cases']];cases={c['case_id']:c for c in protocol['physical_cases']}
    rows={(r['link_id'],r['physical_case_id']):r for r in results['roads']};assert len(rows)==1444 and set(rows)=={(s,c) for s in sources for c in case_ids}
    cells={(s,c['segment_id']):c for s,source in sources.items() for c in source['source_cells']}
    accum=defaultdict(lambda:dict(energy=np.zeros((2,3,8)),known=0.,unknown=0.,count=0));seen=set();samples=[];node_states=Counter();reason_counts=defaultdict(Counter)
    with gzip.open(inputs/'node_evaluations.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            node=json.loads(line);link,case,n,j=node['link_id'],node['physical_case_id'],node['subdivisions'],node['subdivision']
            identity=(link,case,node['source_cell_id'],n,j);assert identity not in seen;seen.add(identity)
            cell=cells[(link,node['source_cell_id'])];assert n in (1,2) and 0<=j<n
            expected_xy=[a+(b-a)*(j+.5)/n for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
            assert node['xy_bng']==expected_xy;close(node['weight_length_m'],cell['length_m']/n)
            a=accum[(link,case,n)];a['count']+=1;node_states[node['status']]+=1
            if node['status']=='withheld':
                a['unknown']+=node['weight_length_m'];assert node['reasons'] and 'relative_energy_per_m' not in node
                reason_counts[(link,case)].update(node['reasons'])
            else:
                assert node['status']=='conditional_research' and node['input_scene_kind']=='conditional_real_input' and node['scope']=='direct_only_reflections_omitted'
                assert 0<node['direct_distance_m']<=10000 and 0<=node['maximum_vertical_reconstruction_error_m']<=.1+1e-9
                distance=node['direct_distance_m'];expected=np.zeros((3,8))
                for pi,p in enumerate(PERIODS):
                    for bi,f in enumerate(BANDS):
                        expected[pi,bi]=math.fsum(powers[link][g,pi,bi]*np.interp(distance,air[f'distance_{f}'],air[f'{p}_{f}'][g]) for g in range(len(groups)))
                assert np.allclose(expected,node['joint_hourly_air_source_power_per_m'],rtol=3e-11,atol=1e-280)
                transfer=np.asarray(node['relative_transfer']);energy=np.asarray(node['relative_energy_per_m'])
                assert transfer.shape==(2,8) and np.isfinite(transfer).all() and np.all(transfer>0)
                assert np.allclose(transfer[:,None,:]*expected[None,:,:],energy,rtol=3e-11,atol=1e-280)
                a['known']+=node['weight_length_m'];a['energy']+=energy*node['weight_length_m']
                counts['independent_joint_source_band_values']+=24;counts['per_node_energy_values_checked']+=48
                if len(samples)<8 or int(hashlib.sha256(str(identity).encode()).hexdigest()[:8],16)%401==0:samples.append(node)
            counts['quadrature_nodes_checked']+=1
    assert len(seen)==report['node_evaluations']==32688
    combined={c:np.zeros((2,3,8)) for c in case_ids};known_lengths=Counter();unknown_lengths=Counter();complete_counts=Counter();held_counts=Counter()
    for (link,c),r in rows.items():
        length=math.fsum(cell['length_m'] for cell in sources[link]['source_cells']);close(length,from_wkt(sources[link]['geometry_wkt_bng']).length);close(length,r['source_length_m'])
        assert not r['refinement_convergence_proven'] and r['full_road_all_paths_noise_db'] is None and r['all_inclusive_noise_db'] is None
        assert dict(reason_counts[(link,c)])==r['reason_counts']
        for h in r['quadrature_history']:
            a=accum[(link,c,h['subdivisions'])];close(a['known']+a['unknown'],length);close(a['known'],h['known_source_length_m']);close(a['unknown'],h['unknown_source_length_m']);assert a['count']==h['evaluations']
            assert np.allclose(a['energy'],h['known_evaluated_relative_energy'],rtol=3e-11,atol=1e-280)
            assert (h['whole_road_relative_energy'] is None)==(a['unknown']>0)
            if h['whole_road_relative_energy'] is not None:assert np.allclose(a['energy'],h['whole_road_relative_energy'],rtol=3e-11,atol=1e-280)
        a=accum[(link,c,2)];assert (r['whole_road_relative_energy'] is None)==(a['unknown']>0)
        if a['unknown']>0:held_counts[c]+=1
        else:complete_counts[c]+=1
        assert np.allclose(a['energy'],r['known_evaluated_relative_energy'],rtol=3e-11,atol=1e-280)
        coarse=accum[(link,c,1)]
        if a['unknown'] or coarse['unknown']:assert r['spacing_change_db'] is None
        else:
            for si,state in enumerate(('homogeneous','favourable')):
                m1,m2=metrics(coarse['energy'][si]),metrics(a['energy'][si])
                for p in m1:
                    expected=m2[p]-m1[p] if m1[p] is not None and m2[p] is not None else 0. if m1[p] is None and m2[p] is None else None
                    close(expected,r['spacing_change_db'][state][p]);counts['independent_spacing_change_metrics_checked']+=1
        combined[c]+=a['energy'];known_lengths[c]+=a['known'];unknown_lengths[c]+=a['unknown']
        for p in (0.,.5,1.):
            expected=metrics((1-p)*a['energy'][0]+p*a['energy'][1]);actual=r['assumed_favourable_probability_results'][str(p)]
            for metric in expected:close(expected[metric],actual['known_evaluated_component_db'][metric])
            if a['unknown']:assert actual['whole_road_direct_component_db'] is None
            else:
                for metric in expected:close(expected[metric],actual['whole_road_direct_component_db'][metric])
        counts['whole_road_case_quadratures_checked']+=1
    for c,energy in combined.items():
        summary=results['case_summaries'][c];assert np.allclose(energy,summary['known_evaluated_relative_energy'],rtol=3e-11,atol=1e-280)
        close(known_lengths[c],summary['known_length_m']);close(unknown_lengths[c],summary['unknown_length_m'])
        assert complete_counts[c]==summary['complete_roads'] and held_counts[c]==summary['held_roads']
        for p in (0.,.5,1.):
            expected=metrics((1-p)*energy[0]+p*energy[1])
            for metric in expected:close(expected[metric],summary['known_evaluated_partial_direct_component_db'][str(p)][metric])
        assert summary['complete_road_network_noise_db'] is None and summary['all_inclusive_noise_db'] is None
    print(f'Independent accounting passed for {len(seen)} nodes; replaying {len(samples)} native profiles',flush=True)
    builder=builder_module.load_builder(parents['physical_scene_inputs_v2'],parents['ground_cover_inputs_v3'],maximum_reflections=0)
    features=read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    policies=read(parents['physical_scene_assumptions_v2']/'source_physical_inputs.json')['roads']
    for link,source in sources.items():
        geometry=from_wkt(source['geometry_wkt_bng'])
        footprint_hit=any(geometry.intersection(builder.buildings[int(i)]['geometry']).length>0 for i in builder.building_tree.query(geometry,predicate='intersects'))
        fan=MultiPoint([protocol['receiver_xy_bng'],*[p for part in ([geometry] if geometry.geom_type=='LineString' else geometry.geoms) for p in part.coords]]).convex_hull
        intersecting=[builder.buildings[int(i)]['feature_id'] for i in builder.building_tree.query(fan,predicate='intersects')]
        for c,case in cases.items():
            reason=['source_surface_or_deck_unresolved'] if policies[link]['physical_source']['status']!='conditional_at_grade_reference' else ['positive_source_length_intersects_generalised_building'] if footprint_hit else ['unresolved_building_height_in_source_receiver_fan'] if any(prior_physical_review.scalar_top(features[i],case) is None for i in intersecting) else []
            assert reason==rows[(link,c)]['whole_source_guard_reasons'];counts['whole_source_guards_independently_checked']+=1
    native_points=[native_replay(node,builder,features,cases[node['physical_case_id']],protocol['receiver_xy_bng'],counts) for node in samples]
    # Replay the sealed implementation without the adapter/cache scope. This is
    # parity, labelled separately from independent accounting and native checks.
    for name in list(sys.modules):
        if name=='quiet_uk' or name.startswith('quiet_uk.'):del sys.modules[name]
    package=types.ModuleType('quiet_uk');package.__path__=[str(inputs/'code')];sys.modules['quiet_uk']=package
    core=importlib.import_module('quiet_uk.favourable_propagation');domain=importlib.import_module('quiet_uk.conditional_scene_domain')
    cache_module=importlib.import_module('quiet_uk.propagation_geometry_cache');native_sensitivity=[];native_seen=set()
    for sample_i,node in enumerate(samples):
        scene=dict(node['candidate_kernel_scene'],conditional_input_policy=domain.execution_policy(cases[node['physical_case_id']]))
        for bi,f in enumerate(BANDS):
            result=core.direct_attenuation(scene,f,dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True),absorption_frequency_policy='exact_base10')
            assert result['status']=='conditional_research'
            for si,state in enumerate(('homogeneous','favourable')):
                assert math.isclose(node['relative_transfer'][si][bi],10**(-(result[state+'_attenuation_db']-result['atmospheric_attenuation_db'])/10),rel_tol=3e-11,abs_tol=1e-280)
            counts['frozen_uncached_transfer_bands_replayed']+=1
        key=(node['link_id'],node['physical_case_id'])
        if key not in native_seen and len(native_sensitivity)<12:
            native_seen.add(key);native_scene=dict(scene,points=native_points[sample_i]);values=np.zeros((2,8));held=[]
            with cache_module.geometry_cache():
                for bi,f in enumerate(BANDS):
                    try:r=core.direct_attenuation(native_scene,f,dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True),absorption_frequency_policy='exact_base10')
                    except (ValueError,OverflowError,ZeroDivisionError) as e:held.append(str(e));continue
                    if r['status']!='conditional_research':held.append(r['reason']);continue
                    for si,state in enumerate(('homogeneous','favourable')):values[si,bi]=10**(-(r[state+'_attenuation_db']-r['atmospheric_attenuation_db'])/10)
            entry=dict(link_id=node['link_id'],physical_case_id=node['physical_case_id'],native_terrain_points=len(native_points[sample_i]),candidate_terrain_points=node['candidate_terrain_points'],
                native_kernel_status='withheld' if held else 'conditional_research',native_withheld_reasons=sorted(set(held)))
            if not held:
                declared=np.asarray(node['relative_transfer']);power=np.asarray(node['joint_hourly_air_source_power_per_m'])
                differences=10*np.log10(declared/values)
                entry['maximum_absolute_octave_transfer_change_db']=float(np.max(np.abs(differences)))
                entry['simplified_minus_native_A_component_density_change_db']={state:{p:metrics(declared[si,None,:]*power)[p]-metrics(values[si,None,:]*power)[p] for p in (*PERIODS,'den')} for si,state in enumerate(('homogeneous','favourable'))}
                counts['sampled_full_native_transfer_bands_compared']+=8
            native_sensitivity.append(entry)
    output.mkdir(parents=True);shutil.copyfile(__file__,output/Path(__file__).name)
    (output/'report.json').write_bytes(json_bytes(dict(status='passed_independent_accounting_and_sampled_replay',
        input_inventory_sha256=input_hash,input_generation=read(inputs/'inventory.json')['generation_id'],counts=counts,node_status_counts=node_states,
        parent_inventory_sha256=protocol['parent_inventory_sha256'],complete_roads=complete_counts,held_roads=held_counts,
        independent_scope='Scalar source/category/flow equations, all hourly air coefficients and table knots, every node identity/length/source-air energy, all road/period/mixture/network accounting.',
        sampled_scope='Native geographic builder replay, independent terrain ordinate bound and global roof/G materialisation reconstruction; frozen uncached H/F kernel replay is parity only.',
        native_simplification_sensitivity=native_sensitivity,terrain_geometry_bound_is_acoustic_bound=False,
        field_validation=False,external_engine_executed=False,all_inclusive_noise_db=None,absolute_release_eligible=False)))
    (output/'sampled_nodes.json').write_bytes(json_bytes([dict(link_id=n['link_id'],physical_case_id=n['physical_case_id'],source_cell_id=n['source_cell_id'],subdivisions=n['subdivisions'],subdivision=n['subdivision']) for n in samples]))
    (output/'native_simplification_sensitivity.json').write_bytes(json_bytes(dict(comparisons=native_sensitivity,
        interpretation='Selected source-point component density sensitivity to the 0.1m profile model; not a full-road difference, acoustic error bound, confidence interval or field validation.')))
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==input_hash
    identity=helper.seal(output,'conditional-road-review-');print(json.dumps(dict(generation_id=identity,counts=counts)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',default=str(ROOT/'artifacts/conditional_road_integration_v4'));parser.add_argument('--output',default=str(ROOT/'artifacts/conditional_road_integration_review_v1'))
    args=parser.parse_args();run(args.inputs,args.output)
