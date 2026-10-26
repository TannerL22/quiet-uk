"""Independently verify bounded single-wall reflection research checks.

Imports only earlier independent references, never the evaluated reflection
module or input builder. Uses vector plan mirrors, a two-by-two intersection,
QUADPACK circular line integrals, numerical mean planes and Decimal absorption.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import math
from pathlib import Path
import re
import shutil

import numpy as np
from shapely.geometry import LineString,Point

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('weather_reference', ROOT/'scripts/74_verify_weather_checks.py')
prior = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prior)
geometry = prior.geometry
read, file_hash, json_bytes, close = prior.read, prior.file_hash, prior.json_bytes, prior.close
BANDS = (63,125,250,500,1000,2000,4000,8000)


def original_reflection_array(text, test, variable):
    """The direct and reflected paths reuse names; take the final assignment."""
    block = re.search(r'public void '+re.escape(test)+r'\(\).*?(?=public void|\Z)', text, re.S).group()
    values = re.findall(r'\b'+re.escape(variable)+r'\s*=\s*new\s+double\s*\[\s*\]\s*\{([^}]+)\}', block)
    assert values, (test, variable)
    return [float(token.strip()) for token in values[-1].split(',')]


def plan_image(source, receiver, wall_start, wall_end):
    """Reflect the receiver and solve two intersecting parametric lines."""
    s, r, a, b = [np.array(v[:2], dtype=float) for v in (source,receiver,wall_start,wall_end)]
    tangent = (b-a)/np.linalg.norm(b-a)
    normal = np.array([-tangent[1],tangent[0]])
    mirror = r-2*normal*np.dot(r-a,normal)
    t,u = np.linalg.solve(np.column_stack((mirror-s,-(b-a))),a-s)
    hit = s+t*(mirror-s)
    first,second = float(np.linalg.norm(hit-s)),float(np.linalg.norm(r-hit))
    return dict(source_xy=s.tolist(),receiver_xy=r.tolist(),image_receiver_xy=mirror.tolist(),
        reflection_xy=hit.tolist(),wall_fraction=float(u),first_plan_leg_m=first,
        second_plan_leg_m=second,unfolded_plan_distance_m=first+second,
        hit_within_wall_segment=bool(0<=u<=1),same_side=bool(np.dot(s-a,normal)*np.dot(r-a,normal)>0))


def original_fixture(sources, test):
    """Reconstruct the original cut profile independently in its unfolded plane."""
    raw = read(sources/(test+'_Reflection.json'))
    assert raw['profileType']=='REFLECTION'
    cuts = raw['cutPoints']; refs=[c for c in cuts if c['type']=='Reflection']; assert len(refs)==1
    reflection = refs[0]
    coordinate = lambda c:[c['coordinate'][k] for k in ('x','y','z')]
    s,r=coordinate(cuts[0]),coordinate(cuts[-1])
    start=[reflection['wall']['p0'][k] for k in ('x','y','z')]
    end=[reflection['wall']['p1'][k] for k in ('x','y','z')]
    plan=plan_image(s,r,start,end)
    assert plan['same_side'] and plan['hit_within_wall_segment']
    original_hit=coordinate(reflection)[:2]
    plan['original_hit_difference_m']=math.dist(original_hit,plan['reflection_xy'])
    assert plan['original_hit_difference_m']<.002
    index=cuts.index(reflection)
    # Preserve the public original cut rather than replacing its millimetre
    # rounding with the exact mirror intersection. GEOS leg projections are
    # independent of the evaluated cumulative adjacent-distance construction.
    first_line=LineString([s[:2],original_hit]);second_line=LineString([original_hit,r[:2]])
    first=float(first_line.length);total=first+float(second_line.length);xs=[]
    for i,c in enumerate(cuts):
        point=Point(coordinate(c)[:2]);line=first_line if i<=index else second_line
        assert line.distance(point)<1e-8
        xs.append(float(line.project(point))+(0 if i<=index else first))
    support={}
    intervals=[]
    for i,(x,c) in enumerate(zip(xs,cuts)):
        if x in support: assert support[x]==c['zGround']
        support[x]=c['zGround']
        if i+1<len(xs) and xs[i+1]>x: intervals.append([x,xs[i+1],c['groundCoefficient']])
    wall_line=LineString([start[:2],end[:2]])
    u=wall_line.project(Point(original_hit))/wall_line.length;top=(1-u)*start[2]+u*end[2]
    scene=dict(case_id=test,scene_kind='published_reference',points=[[x,z] for x,z in sorted(support.items())],
        source=[0.,s[2]],receiver=[total,r[2]],ground_segments=intervals,source_g=cuts[0]['groundCoefficient'],
        obstacles=[],opaque=True,infinite_lateral_extent=True)
    wall=dict(unfolded_x_m=first,base_z_m=reflection['zGround'],top_z_m=top,
        slope_from_vertical_deg=0.,absorption_by_band=reflection['wallAlpha'],unfolded_geometry_confirmed=True,
        incident_view_height_m=dict(homogeneous=1.,favourable=1.),incident_view_width_m=dict(homogeneous=1.,favourable=1.))
    assert abs(reflection['coordinate']['z']-(s[2]+(r[2]-s[2])*first/total))<.002
    return scene,wall,plan


def ray_at_wall(scene,wall,condition):
    s,r=np.array(scene['source'],dtype=float),np.array(scene['receiver'],dtype=float)
    x=wall['unfolded_x_m']; chord=float(np.linalg.norm(r-s))
    if condition=='homogeneous':
        height=float(np.interp(x,[s[0],r[0]],[s[1],r[1]]))
        return dict(reflection_point=[x,height],direct_distance_m=chord,radius_m=None)
    radius=max(1000.,8*chord)
    center,_,_=prior.leg(s,r,radius)
    radicand=radius**2-(x-center[0])**2
    if radicand<0: raise ValueError('wall outside circular ray domain')
    height=float(center[1]+math.sqrt(radicand))
    return dict(reflection_point=[x,height],direct_distance_m=chord,radius_m=radius)


def retrodiffraction(scene,wall,condition,frequency):
    s,r=np.array(scene['source'],dtype=float),np.array(scene['receiver'],dtype=float)
    top=np.array([wall['unfolded_x_m'],wall['top_z_m']],dtype=float)
    distances=[float(np.linalg.norm(top-s)),float(np.linalg.norm(r-top))]
    direct=float(np.linalg.norm(r-s))
    if condition=='homogeneous': delta=direct-sum(distances)
    else:
        radius=max(1000.,8*direct)
        delta=prior.arc(direct,radius)-sum(prior.arc(v,radius) for v in distances)
    if abs(delta)<1e-12: delta=0.
    q=40*delta/(340/frequency)
    loss=10*math.log10(3+q) if q>=-2 else 0.
    return dict(retrodiffraction_path_difference_m=delta,retrodiffraction_db=loss)


def independent_branch(scene,wall,frequency,air,policy,condition):
    def state(status,reason,**values): return dict(status=status,reason=reason,attenuation_db=None,**values)
    if scene['scene_kind'] not in ('synthetic','published_reference'): return state('withheld','real_area_scene_not_qualified')
    if wall.get('unfolded_geometry_confirmed') is not True: return state('withheld','unknown_unfolded_reflection_geometry')
    if any(wall.get(k) is None for k in ('unfolded_x_m','base_z_m','top_z_m','slope_from_vertical_deg')):
        return state('withheld','unknown_wall_geometry')
    if scene.get('obstacles')!=[]: return state('withheld','reflection_diffraction_interaction_not_implemented')
    if wall['slope_from_vertical_deg']>=15: return state('absent','wall_slope_outside_reflection_domain')
    if wall['slope_from_vertical_deg']!=0: return state('withheld','inclined_wall_not_implemented')
    try: ray=ray_at_wall(scene,wall,condition)
    except ValueError: return state('withheld','unsupported_curved_reflection_geometry')
    height=ray['reflection_point'][1];details=dict(hit_height_m=height)
    if height<wall['base_z_m']-1e-10 or height>wall['top_z_m']+1e-10:
        return state('absent','ray_passes_outside_wall_height',**details)
    dimensions=[(wall.get(key) or {}).get(condition) for key in ('incident_view_height_m','incident_view_width_m')]
    if any(v is None for v in dimensions): return state('withheld','unknown_incident_view_wall_dimensions',**details)
    if min(dimensions)<.5: return state('absent','incident_view_wall_dimension_below_minimum',**details)
    alpha=None if wall.get('absorption_by_band') is None else wall['absorption_by_band'][BANDS.index(frequency)]
    if alpha is None: return state('withheld','unknown_wall_absorption',**details)
    if alpha==1: return state('absent','perfectly_absorbing_wall_zero_energy_extension',**details)
    boundary=(prior.prior.independent if condition=='homogeneous' else prior.independent)(scene,frequency)
    if 'reason' in boundary: return state('withheld','incomplete_unfolded_boundary_inputs',**details)
    if boundary['branch']=='diffraction_including_ground': return state('withheld','reflection_diffraction_interaction_not_implemented',**details)
    if policy not in ('nominal','exact_base10'): return state('withheld','unknown_absorption_frequency_policy',**details)
    iso_frequency=frequency if policy=='nominal' else 1000*10**(.3*(BANDS.index(frequency)-4))
    atmosphere=prior.absorption(iso_frequency,air)
    if 'reason' in atmosphere: return state('withheld','unknown_or_unsupported_absorption_air',**details)
    retro=retrodiffraction(scene,wall,condition,frequency);distance=ray['direct_distance_m']
    # Compute transfer power as a product, independently of attenuation sums.
    geometric=10**(-1.1)/distance**2
    atmospheric=10**(-atmosphere['alpha_db_per_km']*distance/1000/10)
    ground=10**(-boundary['boundary_db']/10)
    loss=10**(-retro['retrodiffraction_db']/10)
    transfer=geometric*atmospheric*ground*(1-alpha)*loss
    power=10*math.log10(1-alpha)
    return dict(status='conditional_research',attenuation_db=-10*math.log10(transfer),
        hit_height_m=height,reflected_distance_m=distance,divergence_db=-10*math.log10(geometric),
        atmospheric_attenuation_db=-10*math.log10(atmospheric),boundary_db=boundary['boundary_db'],
        absorption_loss_db=-power,reflection_power_correction_db=power,retrodiffraction_db=retro['retrodiffraction_db'],
        retrodiffraction_delta_prime_m=retro['retrodiffraction_path_difference_m'])


def independent_mix(branches,weights):
    weighted=[(b,w) for b,w in zip(branches,weights) if w>0]
    if any(b['status']=='withheld' for b,w in weighted): return dict(status='withheld',attenuation_db=None)
    energy=math.fsum(w*10**(-b['attenuation_db']/10) for b,w in weighted if b['status']=='conditional_research')
    return dict(status='conditional_research',attenuation_db=-10*math.log10(energy)) if energy else dict(status='absent',attenuation_db=None)


def compare_branch(actual,expected,counts,errors,geometry_errors):
    assert actual['status']==expected['status'],(actual,expected)
    counts[expected['status']+'_branches_checked']+=1
    if expected['status']!='conditional_research':
        assert actual['reason']==expected['reason'] and actual['attenuation_db'] is None
        assert actual['energy_ratio']==(0. if expected['status']=='absent' else None)
    for key,value in expected.items():
        if key in ('reason','status','attenuation_db') and value is None: continue
        if key in ('reason','status'): continue
        a=actual['boundary']['boundary_db'] if key=='boundary_db' else actual[key]
        close(a,value)
        (errors if key.endswith('_db') else geometry_errors).append(abs(a-value))


def compare_result(actual,scene,wall,f,air,policy,probability,counts,errors,geometry_errors):
    expected=[independent_branch(scene,wall,f,air,policy,c) for c in ('homogeneous','favourable')]
    for condition,b in zip(('homogeneous','favourable'),expected): compare_branch(actual[condition],b,counts,errors,geometry_errors)
    if 'meteorological_mix' not in actual:
        assert actual['status']=='withheld' and actual['attenuation_db'] is None
        assert all(b['status']=='withheld' for b in expected)
        assert actual['annual_noise_prediction'] is None
        return expected
    if probability is None:
        assert actual['attenuation_db'] is None and actual['meteorological_mix']['status']=='withheld'
    else:
        mixed=independent_mix(expected,[1-probability,probability]);assert actual['meteorological_mix']['status']==mixed['status']
        if mixed['attenuation_db'] is None: assert actual['attenuation_db'] is None
        else:
            close(actual['attenuation_db'],mixed['attenuation_db']);errors.append(abs(actual['attenuation_db']-mixed['attenuation_db']))
        assert actual['status']==mixed['status']
    assert actual['annual_noise_prediction'] is None and not actual['regulatory_implementation_qualified']
    return expected


def compare_plan(actual,inputs,counts,errors):
    if any(v is None for v in inputs.values()):
        assert actual['status']=='withheld';counts['plan_unknown_checked']+=1;return
    s,r,a,b=[np.array(inputs[k],dtype=float) for k in ('source_xy','receiver_xy','wall_start_xy','wall_end_xy')]
    # Decide side eligibility before solving a parallel/on-plane intersection.
    n=np.array([-(b-a)[1],(b-a)[0]])
    tolerance=64*math.ulp(max(1.,*(abs(v) for p in (s,r,a,b) for v in p)))
    side_distances=[np.dot(p-a,n)/np.linalg.norm(n) for p in (s,r)]
    if any(abs(v)<=tolerance for v in side_distances) or side_distances[0]*side_distances[1]<=0:
        assert actual['status']=='absent';counts['plan_absent_checked']+=1;return
    expected=plan_image(s,r,a,b)
    if expected['wall_fraction'] < -tolerance/np.linalg.norm(b-a) or expected['wall_fraction'] > 1+tolerance/np.linalg.norm(b-a):
        assert actual['status']=='absent';counts['plan_absent_checked']+=1;return
    assert actual['status']=='conditional_research'
    for key,value in [('wall_fraction',expected['wall_fraction']),('reflection_distance_from_source_m',expected['first_plan_leg_m']),
                      ('unfolded_horizontal_distance_m',expected['unfolded_plan_distance_m'])]:
        close(actual[key],value);errors.append(abs(actual[key]-value))
    for a,b in zip(actual['reflection_xy'],expected['reflection_xy']): close(a,b);errors.append(abs(a-b))
    counts['numeric_plan_checked']+=1


def final_total(text,test):
    block=re.search(r'public void '+test+r'\(\).*?(?=public void|\Z)',text,re.S).group()
    literals=re.findall(r'assertArrayEquals\(\s*new\s+double\s*\[\s*\]\s*\{([^}]+)\}',block)
    return [float(x) for x in literals[-1].split(',')]


def figure(output,scenes,benchmarks):
    plt=geometry.plt;plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,3,figsize=(14,4.8))
    for ax,test in zip(axes[:2],('TC16','TC26')):
        scene,wall,_=scenes[test];x=wall['unfolded_x_m'];xs=np.linspace(scene['source'][0],scene['receiver'][0],300)
        s,r=np.array(scene['source']),np.array(scene['receiver']);radius=max(1000,8*np.linalg.norm(r-s));center,_,_=prior.leg(s,r,radius)
        ax.plot(xs,np.interp(xs,[s[0],r[0]],[s[1],r[1]]),'--',label='Homogeneous ray')
        ax.plot(xs,center[1]+np.sqrt(radius**2-(xs-center[0])**2),label='Favourable ray')
        ax.plot(*zip(*scene['points']),color='#777777',label='Unfolded ground')
        ax.plot([x,x],[wall['base_z_m'],wall['top_z_m']],linewidth=4,color='#444444',label='Reflecting wall')
        ax.set_title(test+(' — both wall hits' if test=='TC16' else ' — favourable ray misses'),loc='left')
        ax.set_xlabel('Cumulative physical-leg distance (m)');ax.set_ylabel('Height in supplied datum (m)');ax.legend(fontsize=8)
    rows=[b for b in benchmarks if b['case_id']=='TC16']
    for state in ('homogeneous','favourable'):
        axes[2].plot(BANDS,[b['result'][state]['retrodiffraction_db'] for b in rows],'o-',label=state.capitalize())
    axes[2].set_title('TC16: loss from limited wall top',loc='left');axes[2].set_xscale('log')
    axes[2].set_xticks(BANDS,[str(f) if f<1000 else str(f//1000)+'k' for f in BANDS]);axes[2].set_xlabel('Octave band (Hz)')
    axes[2].set_ylabel('Retrodiffraction attenuation (dB)');axes[2].legend(fontsize=8)
    fig.text(.05,.02,'Controlled supplied public paths and explicit wall eligibility assumptions. No observed or annual noise levels.\nOriginal NoiseModelling TC16/TC26; own EU Annex II / 2018 / 2021 equations; exact base10 atmospheric centres.',fontsize=9)
    fig.tight_layout(rect=[0,.10,1,1]);fig.savefig(output/'reflection_checks.png',dpi=170);plt.close(fig)


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    digest=geometry.check_generation(inputs);report,protocol=read(inputs/'report.json'),read(inputs/'protocol.json')
    parents={n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    for n,p in parents.items(): assert geometry.check_generation(p)==report['parent_inventory_sha256'][n]
    sources=next(p for p in parents.values() if (p/'TC26_Reflection.json').exists());source_manifest=read(sources/'manifest.json')
    for n,h in source_manifest['parent_inventory_sha256'].items(): assert geometry.check_generation(ROOT/'artifacts'/n)==h
    for n,h in report['code_sha256'].items(): assert file_hash(ROOT/n)==file_hash(inputs/'code'/n)==h
    # Frozen predecessor production and independent references must still match.
    for n,h in read(parents['weather_checks_v2']/'report.json')['code_sha256'].items(): assert file_hash(ROOT/n)==h
    for n,h in read(parents['weather_review_v2']/'verification.json')['code_sha256'].items(): assert file_hash(ROOT/'scripts'/n)==h
    script_names=('77_verify_reflection_checks.py','74_verify_weather_checks.py','71_verify_sightline_checks.py','68_verify_multiple_edge_checks.py','65_verify_terrain_boundary_checks.py','62_verify_terrain_profile_checks.py')
    script_sha={n:file_hash(ROOT/'scripts'/n) for n in script_names}
    counts,errors,geometry_errors=Counter(),[],[]
    cases={c['case_id']:c for c in protocol['synthetic_scenes']};assert len(cases)==41
    results=read(inputs/'synthetic_results.json')['results']
    assert len(results)==328 and {(r['case_id'],r['frequency_hz']) for r in results}=={(c,f) for c in cases for f in BANDS}
    for row in results:
        c=cases[row['case_id']];compare_result(row['result'],c['scene'],c['wall'],row['frequency_hz'],c['air'],c['policy'],c['probability'],counts,errors,geometry_errors)
    plans=read(inputs/'plan_results.json')['results'];assert len(plans)==19
    assert {p['case_id'] for p in plans}=={p['case_id'] for p in protocol['plan_cases']}
    for p in plans: compare_plan(p['result'],p['input'],counts,geometry_errors)
    scenes={t:original_fixture(sources,t) for t in ('TC16','TC26')}
    for c in protocol['reference_scenes']:
        scene,wall,plan=scenes[c['case_id']]
        prior.nested(c['scene'],scene,geometry_errors);prior.nested(c['wall'],wall,geometry_errors)
        compare_plan(c['plan'],c['plan_input'],counts,geometry_errors)
        close(c['plan_hit_difference_m'],plan['original_hit_difference_m'])
    text=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    benchmarks=read(inputs/'reference_results.json')['benchmarks'];assert len(benchmarks)==16
    assert {(b['case_id'],b['frequency_hz']) for b in benchmarks}=={(t,f) for t in scenes for f in BANDS}
    public_errors=[];public_keys=set();a_weight=(-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1)
    for b in benchmarks:
        t,f=b['case_id'],b['frequency_hz'];scene,wall,_=scenes[t]
        reflected=compare_result(b['result'],scene,wall,f,protocol['reference_air'],'exact_base10',.5,counts,errors,geometry_errors)
        dscene=prior.prior.reference_fixture(sources,next(c['direct_scene'] for c in protocol['reference_scenes'] if c['case_id']==t))
        direct=b['direct'];prior.combined(direct,dscene,f,protocol['reference_air'],.5,counts,geometry_errors,errors)
        combined=[]
        for condition,rb in zip(('homogeneous','favourable'),reflected):
            db=direct[condition+'_attenuation_db'];cb=independent_mix([dict(status='conditional_research',attenuation_db=db),rb],[1.,1.])
            actual=b['combined'][condition];assert actual['status']==cb['status'];close(actual['attenuation_db'],cb['attenuation_db'])
            errors.append(abs(actual['attenuation_db']-cb['attenuation_db']));combined.append(cb)
        mixed=independent_mix(combined,[.5,.5]);close(b['mixed_combined']['attenuation_db'],mixed['attenuation_db'])
        expected_keys={(c,'boundary_db','expectedABoundary'+suffix) for c,suffix in [('homogeneous','H'),('favourable','F')] if t=='TC16' or c=='homogeneous'}
        expected_keys|={(c,'source_relative_level_db','expectedL'+suffix) for c,suffix in [('homogeneous','H'),('favourable','F')] if t=='TC16' or c=='homogeneous'}
        expected_keys|={('homogeneous','divergence_db','expectedADiv'),('homogeneous','atmospheric_attenuation_db','expectedAAtm'),('mixed_reflection','source_relative_level_db','expectedL'),('combined_paths','a_weighted_source_relative_level_db','final_total_a_weighted')}
        if t=='TC16': expected_keys|={(c,'retrodiffraction_db','expectedRetroDiff'+suffix) for c,suffix in [('homogeneous','H'),('favourable','F')]}|{('homogeneous','reflection_power_correction_db','expectedLabs')}
        assert len(b['comparisons'])==len(expected_keys) and {(c['component'],c['field'],c['original_variable']) for c in b['comparisons']}==expected_keys
        for c in b['comparisons']:
            state,field,var=c['component'],c['field'],c['original_variable'];i=BANDS.index(f)
            expected=final_total(text,t)[i] if var=='final_total_a_weighted' else original_reflection_array(text,t,var)[i]
            if state=='mixed_reflection': actual=93-b['result']['attenuation_db']
            elif state=='combined_paths': actual=93+a_weight[i]-mixed['attenuation_db']
            elif field=='boundary_db': actual=b['result'][state]['boundary']['boundary_db']
            elif field=='source_relative_level_db': actual=93-b['result'][state]['attenuation_db']
            else: actual=b['result'][state][field]
            assert c['expected']==expected and c['tolerance_db']==.02
            close(c['actual'],actual);close(c['error_db'],actual-expected);assert abs(actual-expected)<=.02
            public_keys.add((t,f,state,field));public_errors.append(abs(actual-expected))
        assert not b['external_engine_executed'] and b['source_commit']==source_manifest['commit']
    assert len(public_keys)==136;counts['public_scalar_comparisons']=136
    ready=read(inputs/'real_path_readiness.json')['probes'];old=read(parents['weather_checks_v2']/'real_path_readiness.json')['probes']
    assert len(ready)==100 and {p['probe_id'] for p in ready}=={p['probe_id'] for p in old}
    for p in ready:
        previous=next(q for q in old if q['probe_id']==p['probe_id'])
        for k,v in previous.items(): assert p[k]==v
        for k in ('qualified_wall_plan_geometry','qualified_wall_top_and_base_datum','qualified_incident_view_dimensions','qualified_wall_absorption_by_band','qualified_wall_historical_presence','reflection_attenuation_db'):
            assert p[k] is None
        assert p['status']=='withheld' and p['noise_prediction'] is None
    counts['real_paths_withheld_checked']=100
    assert report['summary']['branch_states']=={k:counts[k+'_branches_checked']-sum(b['result'][c]['status']==k for b in benchmarks for c in ('homogeneous','favourable')) for k in ('conditional_research','absent','withheld')}
    assert report['summary']['public_scalar_comparisons']==136 and not report['eligible_for_absolute_noise_release']
    output.mkdir(parents=True);figure(output,scenes,benchmarks)
    receipt=dict(status='passed_independent_single_wall_reflection_checks',input_inventory_sha256=digest,
        parent_inventory_sha256=report['parent_inventory_sha256'],source_parent_inventory_sha256=source_manifest['parent_inventory_sha256'],
        counts=dict(counts),maximum_independent_db_difference=max(errors),maximum_independent_numeric_difference_mixed_units=max(geometry_errors),
        maximum_public_db_difference=max(public_errors),original_plan_hit_differences_m={t:p[2]['original_hit_difference_m'] for t,p in scenes.items()},
        methods=['Reflect receiver and solve 2x2 wall intersection','GEOS projection of unchanged supplied physical legs','Independent upper-circle centre/tangents',
                 'QUADPACK circular arc line integrals','Numerical mean-plane and expanded ground references','50-digit Decimal air absorption','Multiplication and addition of sound-energy transfers','Independent final original expected-array parsing'],
        code_sha256=script_sha,external_engine_executed=False,full_current_method_qualified=False,real_area_noise_validated=False)
    (output/'verification.json').write_bytes(json_bytes(receipt))
    for n in script_names: shutil.copyfile(ROOT/'scripts'/n,output/n)
    assert geometry.check_generation(inputs)==digest
    for n,p in parents.items(): assert geometry.check_generation(p)==report['parent_inventory_sha256'][n]
    for n,h in report['code_sha256'].items(): assert file_hash(ROOT/n)==h
    assert all(file_hash(ROOT/'scripts'/n)==h for n,h in script_sha.items())
    files={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity='reflection-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity,files=files)))
    print(dict(generation_id=identity,counts=dict(counts),maximum_independent_db_difference=max(errors),maximum_public_db_difference=max(public_errors)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',default=str(ROOT/'artifacts/reflection_checks_v3'))
    p.add_argument('--output',default=str(ROOT/'artifacts/reflection_review_v4'))
    args=p.parse_args();run(args.inputs,args.output)
