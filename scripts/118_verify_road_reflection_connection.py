"""Independent closure of the two bounded reflection-connection blocks.

No evaluated connection module or runner is imported. Retained independent
boundary, circular-arc and air references support adaptive Gauss-Kronrod line
quadrature. Native attachment is checked against sealed inputs and prior audits.
"""
import argparse
from collections import Counter
from functools import lru_cache
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import time
import xml.etree.ElementTree as ET

import numpy as np
from scipy.integrate import quad_vec

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('independent_screened_reference',ROOT/'scripts/80_verify_screened_line_checks.py')
ind=importlib.util.module_from_spec(spec);spec.loader.exec_module(ind)
read,file_hash,json_bytes=ind.read,ind.file_hash,ind.json_bytes
BANDS=(63,125,250,500,1000,2000,4000,8000)
STATES=('homogeneous','favourable')
AIR=dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)
PRIOR_CHECKER_SHA='050ea852352756cbc5195e64e5f3d972ea74c99c094578418e6d6ffd440e0aa6'


def verified(directory):
    directory=Path(directory).resolve()
    if not directory.is_relative_to(ROOT/'artifacts'):raise ValueError('Workspace artifact required')
    inventory=read(directory/'inventory.json')
    for name,sha in inventory['files'].items():
        path=(directory/name).resolve()
        assert path.is_relative_to(directory) and file_hash(path)==sha,(directory,name)
    return inventory


def close_array(actual,expected):
    a,b=np.asarray(actual,dtype=float),np.asarray(expected,dtype=float)
    assert a.shape==b.shape and np.isfinite(a).all() and np.isfinite(b).all()
    assert np.allclose(a,b,rtol=3e-10,atol=1e-14),float(np.max(np.abs(a-b)))


def joint_power(distance,protocol):
    groups=protocol['source_group_power_per_m'];alpha=protocol['air_alpha_db_per_km']
    return np.array([[math.fsum(groups[p][g][b]*10**(-alpha[g][b]*distance/10000) for g in range(2))
                      for b in range(8)] for p in range(3)])


def independent_direct(scene,f):
    distance=float(np.linalg.norm(np.asarray(scene['receiver'])-scene['source']))
    result=[]
    for state in STATES:
        boundary=(ind.weather.prior.independent if state=='homogeneous' else ind.weather.independent)(scene,f)
        assert 'reason' not in boundary
        result.append(10**(-1.1)/distance**2*10**(-boundary['boundary_db']/10))
    return np.array(result),distance


def independent_reflection(scene,wall,f):
    values=[];distance=float(np.linalg.norm(np.asarray(scene['receiver'])-scene['source']))
    for state in STATES:
        result=ind.independent_branch(scene,wall,f,AIR,'exact_base10',state)
        assert result['status'] in ('absent','conditional_research')
        values.append(0. if result['status']=='absent' else
                      10**(-(result['attenuation_db']-result['atmospheric_attenuation_db'])/10))
    return np.array(values),distance


def adapter_scene(original,metadata,retained=None):
    """Verify the declared vertical approximation separately from acoustics.

    Direct adapter output retains counts/error, not its knots. Reconstruct its
    bounded endpoint-preserving selection with a scalar interval work list.
    Reflected output retains the actual knots, which are checked at every
    original ordinate. The independent physics then uses that actual geometry.
    """
    points=original['points'];knots={0,len(points)-1};intervals=[(0,len(points)-1)]
    while intervals:
        left,right=intervals.pop()
        if right-left<2:continue
        a,b=points[left],points[right]
        deviations=[(abs(points[i][1]-(a[1]+(b[1]-a[1])*(points[i][0]-a[0])/(b[0]-a[0]))),i)
                    for i in range(left+1,right)]
        worst,index=max(deviations,key=lambda p:p[0])
        if worst>.1:knots.add(index);intervals.extend(((left,index),(index,right)))
    candidate=dict(original,points=[points[i] for i in sorted(knots)])
    if retained is not None:
        for key in retained:
            if key!='points':ind.weather.nested(retained[key],original[key],[])
        candidate=dict(retained)
    a=np.asarray(points);b=np.asarray(candidate['points'])
    assert np.all(np.diff(b[:,0])>0)
    close_array(b[[0,-1]],a[[0,-1]])
    error=float(np.max(np.abs(a[:,1]-np.interp(a[:,0],b[:,0],b[:,1]))))
    assert error<=.1+1e-9 and abs(error-metadata['maximum_vertical_reconstruction_error_m'])<1e-9
    if 'candidate_terrain_points' in metadata:
        assert metadata['native_terrain_points']==len(points) and metadata['candidate_terrain_points']==len(b)
    return candidate


def control_geometry(name,x):
    distance=math.hypot(x,30.)
    plan=ind.prior.plan_image([x,0.],[0.,30.],[-100.,-20.],[100.,-20.])
    length=plan['unfolded_plan_distance_m']
    def scene(d,obstacles):
        return dict(points=[[0.,0.],[d,0.]],source=[0.,.05],receiver=[d,4.],source_g=0.,
            ground_segments=[[0.,d,0.]],obstacles=obstacles,opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    direct=scene(distance,[dict(vertices=[[distance*12/30,6.]])] if name=='screen_after_reflection' else [])
    reflection=scene(length,[dict(vertices=[[length*52/70,6.]])] if name=='screen_after_reflection' else [])
    wall=dict(unfolded_x_m=plan['first_plan_leg_m'],base_z_m=0.,top_z_m=8.,slope_from_vertical_deg=0.,
        absorption_by_band=[.2]*8,incident_view_height_m={s:1. for s in STATES},incident_view_width_m={s:1. for s in STATES},
        unfolded_geometry_confirmed=True)
    return direct,reflection,wall


def top(feature,case):
    if feature['original_height_status']=='provisional_surface_height':
        return feature['native_summary']['surface_odn_m'][case['roof_statistic']]
    base=feature['native_summary']['reference_base_odn_m'];height=case['missing_building_height_m']
    return base+height if base is not None and height is not None else None


def run(inputs,output,junit):
    inputs,output,junit=Path(inputs).resolve(),Path(output).resolve(),Path(junit).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh review required')
    start=time.perf_counter();inventory=verified(inputs);report=read(inputs/'report.json');protocol=read(inputs/'protocol.json')
    effective_codes=dict(report['code_sha256']);checker='scripts/118_verify_road_reflection_connection.py'
    checker_correction=None
    for name,sha in report['code_sha256'].items():
        if name==checker and file_hash(ROOT/name)!=sha:
            assert sha==PRIOR_CHECKER_SHA and file_hash(inputs/'code'/name)==sha
            effective_codes[name]=file_hash(ROOT/name)
            checker_correction=dict(prior_sha256=sha,current_sha256=effective_codes[name],
                reason='Reference accounting now evaluates the retained bounded adapter geometry; original unsimplified public comparisons remain separate. No production calculation or tolerance changed.')
        else:assert file_hash(ROOT/name)==sha
    for name,sha in report['parent_inventory_sha256'].items():
        parent=ROOT/'artifacts'/name;verified(parent);assert file_hash(parent/'inventory.json')==sha
    for path in (ROOT/'artifacts/screened_line_review_v2/code/scripts').glob('*.py'):
        assert file_hash(ROOT/'scripts'/path.name)==file_hash(path)
    prior=read(ROOT/'artifacts/remaining_road_validation_v1.json')
    assert file_hash(ROOT/'artifacts/remaining_road_validation_v1.json')==protocol['prior_validation_sha256']
    ledger=protocol['unchanged_direct_ledger'];assert ledger==prior['postcheck_ledger']
    assert file_hash(ROOT/ledger['path'])==ledger['sha256']
    changed=[n for n,sha in prior['code_sha256'].items() if file_hash(ROOT/n)!=sha]
    assert changed==['src/quiet_uk/screened_reflection.py']
    counts=Counter();errors=[];legacy_errors=[];exceptions=[];approximation_changes=[]
    sources=ROOT/'artifacts/screened_line_sources_v1'
    fixture_lookup={c['case_id']:c for c in read(ROOT/'artifacts/screened_line_checks_v3/protocol.json')['reference_scenes']}
    java=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    for row in read(inputs/'public_connection.json')['cases']:
        name=row['case_id'];scene,wall,_=ind.original_fixture(sources,name)
        direct=ind.weather.prior.reference_fixture(sources,fixture_lookup[name]['direct_scene'])
        adapted_direct=adapter_scene(direct,row['direct'])
        adapted_reflection=adapter_scene(scene,row['reflected'],row['reflected']['candidate_kernel_scene'])
        expected=np.zeros((2,3,8))
        for bi,f in enumerate(BANDS):
            dt,dd=independent_direct(adapted_direct,f);rt,rd=independent_reflection(adapted_reflection,wall,f)
            raw_dt,raw_dd=independent_direct(direct,f);raw_rt,raw_rd=independent_reflection(scene,wall,f)
            for actual,raw in ((dt,raw_dt),(rt,raw_rt)):
                known=(actual>0)&(raw>0)
                approximation_changes.extend(np.abs(10*np.log10(actual[known]/raw[known])).tolist())
            close_array(np.asarray(row['direct']['relative_transfer'])[:,bi],dt)
            close_array(np.asarray(row['reflected']['relative_transfer'])[:,bi],rt)
            expected[:,:,bi]=dt[:,None]*joint_power(dd,protocol)[:,bi]+rt[:,None]*joint_power(rd,protocol)[:,bi]
            original=row['original_reference'][bi];assert original['frequency_hz']==f
            # This reference uses retained public air, distinct from de-aired connection inputs.
            boundaries={s:ind.independent_branch(scene,wall,f,dict(AIR,temperature_c=10.),'exact_base10',s) for s in STATES}
            for state,b in boundaries.items():
                assert original['result'][state]['status']==b['status']
                if b['status']=='conditional_research':
                    difference=abs(original['result'][state]['attenuation_db']-b['attenuation_db'])
                    assert difference<2e-8;legacy_errors.append(difference)
            direct_energy=[]
            old_air=ind.air_absorption(1000*10**(.3*(bi-4)),10.,70.,101.325,True)['alpha_db_per_km']
            for si,state in enumerate(STATES):direct_energy.append(raw_dt[si]*10**(-old_air*raw_dd/10000))
            ref_energy=[0. if boundaries[s]['status']=='absent' else 10**(-boundaries[s]['attenuation_db']/10) for s in STATES]
            for comparison in original['retained_comparisons']:
                component,field=comparison['component'],comparison['field']
                if component in STATES:
                    branch=boundaries[component]
                    if field=='source_relative_level_db':value=93-branch['attenuation_db']
                    elif field in branch:value=branch[field]
                    else:
                        b=(ind.weather.prior.independent if component=='homogeneous' else ind.weather.independent)(scene,f)
                        value=b[field]
                else:
                    energy=math.fsum(ref_energy)/2 if component=='mixed_reflection' else math.fsum(direct_energy+ref_energy)/2
                    value=93+(-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1)[bi]+10*math.log10(energy)
                assert abs(value-comparison['actual'])<2e-8,(name,f,field,value,comparison['actual'])
                variable=comparison['original_variable']
                target=(ind.prior.original_reflection_array(java,name,'expectedFullLA') if name=='TC27' else ind.prior.final_total(java,name))[bi] if variable=='final_total_a_weighted' else ind.prior.original_reflection_array(java,name,variable)[bi]
                assert target==comparison['expected']
                tolerance=.1 if name=='TC27' and 'level' in field else .02
                assert comparison['tolerance_db']==tolerance and abs(value-target)<=tolerance
                if abs(value-target)>.02:exceptions.append(dict(case_id=name,frequency_hz=f,field=field,difference_db=value-target,retained_tolerance_db=tolerance))
                counts['published_scalar_comparisons']+=1
            counts['public_octave_connections']+=1
        close_array(row['density']['relative_energy_per_m'],expected)
        actual=np.asarray(row['density']['relative_energy_per_m'])
        known=expected>0;errors.extend(np.abs(10*np.log10(actual[known]/expected[known])).tolist())
        assert row['density']['all_inclusive_noise_db'] is None and not row['density']['complete_physical_path_support']
        counts['public_period_band_condition_densities']+=expected.size
    print('Independent block: public paths, powers and all 448 original comparisons passed',flush=True)
    line_errors=[];references=[]
    lines=read(inputs/'line_connection.json');cells={c['segment_id']:c for c in lines['source']['source_cells']}
    assert math.isclose(sum(c['length_m'] for c in cells.values()),60.,abs_tol=1e-12)
    for row in lines['cases']:
        name=row['case_id'];result=row['result'];history={n:np.zeros((2,3,8)) for n in (8,16,32)}
        seen=set()
        for node in result['evaluations']:
            cell=cells[node['source_cell_id']];n=node['subdivisions'];j=node['subdivision']
            assert (node['source_cell_id'],n,j) not in seen;seen.add((node['source_cell_id'],n,j))
            fraction=(j+.5)/n
            xy=[a+(b-a)*fraction for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
            close_array(node['xy_bng'],xy);width=math.dist(cell['start_xy_bng'],cell['end_xy_bng'])/n
            assert abs(node['weight_length_m']-width)<1e-12
            expected=np.zeros((2,3,8));paths=node['paths'];assert [p['path_id'] for p in paths]==['direct','facade']
            for p in paths:
                distance=math.sqrt(xy[0]**2+(30. if p['path_id']=='direct' else 70.)**2+3.95**2)
                assert abs(p['distance_m']-distance)<1e-9
                power=joint_power(distance,protocol);close_array(p['joint_source_air_power_per_m'],power)
                density=np.asarray(p['relative_transfer'])[:,None,:]*power[None,:,:]
                close_array(p['relative_energy_per_m'],density);expected+=density
            close_array(node['relative_energy_per_m'],expected);history[n]+=expected*width
            counts['line_weighted_source_points']+=1
        for h,n in zip(result['quadrature_history'],(8,16,32)):
            close_array(h['whole_road_relative_energy'],history[n])
            assert h['unknown_source_length_m']==0 and abs(h['known_source_length_m']-60)<1e-9
        close_array(result['selected_paths_whole_line_relative_energy'],history[32])
        @lru_cache(maxsize=512)
        def integrand(x):
            direct,reflection,wall=control_geometry(name,x);energy=np.zeros((2,3,8))
            for bi,f in enumerate(BANDS):
                dt,dd=independent_direct(direct,f);rt,rd=independent_reflection(reflection,wall,f)
                energy[:,:,bi]=dt[:,None]*joint_power(dd,protocol)[:,bi]+rt[:,None]*joint_power(rd,protocol)[:,bi]
            return energy.ravel()
        reference,estimate=quad_vec(integrand,-30.,30.,epsabs=1e-10,epsrel=1e-8,limit=100)
        reference=reference.reshape((2,3,8));difference=10*np.log10(history[32]/reference)
        assert np.max(np.abs(difference))<=protocol['line_tolerance_db'],(name,float(np.max(np.abs(difference))))
        line_errors.extend(np.abs(difference).ravel().tolist())
        references.append(dict(case_id=name,independent_energy=reference.tolist(),difference_db=difference.tolist(),
                               adaptive_quadrature_error_estimate=float(estimate),independent_positions=integrand.cache_info().currsize))
        assert result['whole_road_relative_energy'] is None and result['all_inclusive_noise_db'] is None
        counts['independent_line_period_band_condition_integrals']+=48
        print('Independent block: '+name+' line agreed with adaptive reference quadrature',flush=True)
    attachment=read(inputs/'native_input_attachment.json')
    lookup={r['link_id']:r for r in attachment['roads']};assert len(lookup)==722
    features=read(ROOT/'artifacts/physical_scene_assumptions_v2/physical_features.json')['features']
    old={(r['link_id'],r['profile_id']):r for r in read(ROOT/'artifacts/physical_scene_assumptions_v2/profile_sensitivity.json')['profiles']}
    physical=protocol['physical_case'];reason_counts=Counter()
    with gzip.open(ROOT/'artifacts/real_road_scenes_v2/prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            raw=json.loads(line);link=raw['link_id'];scene=raw['scene'];road=lookup.pop(link)
            assert road['reflection_search']==scene['reflection_search'] and road['whole_road_relative_energy'] is None
            assert road['all_inclusive_noise_db'] is None and not road['reflection_search']['support_complete']
            assert len(road['reflections'])==len(scene['reflections'])
            for index,(actual,ref) in enumerate(zip(road['reflections'],scene['reflections'])):
                assert actual['facet_id']==ref['facet_id']
                assert actual['native_profile_sha256']==hashlib.sha256(json.dumps(ref['profile'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
                assert actual['candidate_scene_kind']=='conditional_real_input'
                original=next(c for c in old[(link,'reflection:'+str(index))]['case_results'] if c['case_id']==physical['case_id'])
                assert actual['geometry_diagnostic']==original['geometry_diagnostic']
                tops=[top(features[f],physical) for f in ref['coincident_feature_ids']]
                expected=set(original['geometry_blockers'])-{'missing_or_ambiguous_reflecting_wall_top','reflecting_top_below_native_base'}
                if any(t is None for t in tops):expected.add('missing_reflector_height')
                elif len(set(tops))!=1:expected.add('coincident_reflector_top_ambiguity')
                t=tops[0];base=ref['wall']['base_z_m'];x=ref['wall']['unfolded_x_m']
                if base is None or t is None:expected.add('missing_reflector_vertical_geometry')
                elif t<=base:expected.add('reflector_top_not_above_local_terrain')
                elif t-base<1.:expected.add('physical_wall_height_below_assumed_view_height')
                pi=ref['plan_input']
                if math.dist(pi['wall_start_xy'],pi['wall_end_xy'])<1.:expected.add('physical_facade_width_below_assumed_view_width')
                if any(c['feature_id'] in ref['coincident_feature_ids'] and any(a-1e-7<=x<=b+1e-7 for a,b in c['intervals_m']) for c in ref['profile']['crossings']):
                    expected.add('reflector_hit_adjacent_to_own_footprint_interior')
                assert set(actual['geometry_blockers'])==expected,(link,index,actual['geometry_blockers'],expected)
                assert actual['wall']['top_z_m']==t and actual['wall']['base_z_m']==base
                assert actual['wall']['absorption_by_band']==[.2]*8
                for dimension in ('incident_view_height_m','incident_view_width_m'):assert actual['wall'][dimension]=={s:1. for s in STATES}
                pi=ref['plan_input'];plan=ind.prior.plan_image(pi['source_xy'],pi['receiver_xy'],pi['wall_start_xy'],pi['wall_end_xy'])
                assert plan['same_side'] and plan['hit_within_wall_segment']
                assert abs(plan['unfolded_plan_distance_m']-ref['profile']['length_m'])<.002
                assert abs(plan['first_plan_leg_m']-x)<.002
                counts['native_reflection_attachments']+=1;reason_counts.update(expected)
            counts['native_road_snapshots']+=1
    assert not lookup and counts['native_reflection_attachments']==1418 and reason_counts==attachment['reason_counts']
    tests=list(ET.parse(junit).getroot().iter('testcase'))
    assert tests and all(not any(t.find(k) is not None for k in ('failure','error','skipped')) for t in tests)
    for name,sha in effective_codes.items():assert file_hash(ROOT/name)==sha
    assert file_hash(ROOT/ledger['path'])==ledger['sha256']
    output.mkdir(parents=True)
    summary=dict(counts=counts,current_focused_tests=len(tests),maximum_public_connection_db_difference=max(errors),
        maximum_independent_published_path_db_difference=max(legacy_errors),maximum_line_vs_independent_reference_db_difference=max(line_errors),
        maximum_adapter_vs_unsimplified_public_geometry_change_db=max(approximation_changes),
        retained_public_target_exceptions=exceptions,elapsed_seconds=time.perf_counter()-start)
    review=dict(status='passed_two_bounded_reflection_blocks',summary=summary,
        connection_inventory_sha256=file_hash(inputs/'inventory.json'),connection_generation_id=inventory['generation_id'],
        code_sha256=effective_codes,checker_correction=checker_correction,
        junit_sha256={str(junit.relative_to(ROOT)).replace('\\','/'):file_hash(junit)},
        unchanged_direct_ledger=ledger,complete_physical_path_support=False,field_validation=False,
        external_engine_executed=False,all_inclusive_noise_db=None,map_updated=False,
        closure='These two numerical validation blocks are closed. Native snapshots attach inputs only; whole-real-road reflection quadrature and complete path/source support have not been computed or qualified.')
    (output/'report.json').write_bytes(json_bytes(review));(output/'independent_line_references.json').write_bytes(json_bytes(dict(cases=references)))
    for name in ('scripts/118_verify_road_reflection_connection.py',):
        dest=output/'code'/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/name,dest)
    files={str(p.relative_to(output)).replace('\\','/'):file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    generation='road-reflection-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=generation,files=files)))
    print(json_bytes(dict(generation_id=generation,summary=summary)).decode(),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',default=str(ROOT/'artifacts/road_reflection_connection_v2'))
    p.add_argument('--output',default=str(ROOT/'artifacts/road_reflection_connection_review_v1'))
    p.add_argument('--junit',default=str(ROOT/'artifacts/road_reflection_connection_tests_v2.xml'))
    a=p.parse_args();run(a.inputs,a.output,a.junit)
