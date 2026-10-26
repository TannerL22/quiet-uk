"""Independent screened-reflection and road-line verification.

Never imports the evaluated modules or builder. Reconstructs public profiles
using GEOS projections; retains earlier numerical mean-plane/ground references,
QUADPACK circular line elements and Decimal air absorption. Road integration
uses adaptive Gauss-Kronrod quadrature, independently of midpoint subdivision.
"""
import argparse
from collections import Counter
from functools import lru_cache
import hashlib
import importlib.util
import math
from pathlib import Path
import re
import shutil

import numpy as np
from scipy.integrate import quad_vec

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('frozen_independent',ROOT/'scripts/77_verify_reflection_checks.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
weather=prior.prior;geometry=weather.geometry
read,file_hash,json_bytes,close=prior.read,prior.file_hash,prior.json_bytes,prior.close
STATES=('homogeneous','favourable')


def original_fixture(sources,test):
    scene,wall,plan=prior.original_fixture(sources,test)
    raw=read(sources/(test+'_Reflection.json'))['cutPoints'];index=next(i for i,c in enumerate(raw) if c['type']=='Reflection')
    coordinates=lambda c:[c['coordinate'][k] for k in ('x','y')]
    first=prior.LineString([coordinates(raw[0]),coordinates(raw[index])]);second=prior.LineString([coordinates(raw[index]),coordinates(raw[-1])])
    obstacles=[];enter=None
    for i,c in enumerate(raw):
        if c['type']!='Wall':continue
        line=first if i<=index else second;x=line.project(prior.Point(coordinates(c)))+(0 if i<=index else first.length)
        if c.get('intersectionType')=='BUILDING_ENTER':enter=[x,c['coordinate']['z']]
        elif c.get('intersectionType')=='BUILDING_EXIT':
            assert enter is not None;obstacles.append(dict(vertices=[enter,[x,c['coordinate']['z']]],roof_g=0.));enter=None
        else:obstacles.append(dict(vertices=[[x,c['coordinate']['z']]]))
    assert enter is None;scene['obstacles']=obstacles
    return scene,wall,plan


@lru_cache(maxsize=64)
def air_absorption(frequency,temperature,humidity,pressure,uniform):
    return weather.absorption(frequency,dict(temperature_c=temperature,relative_humidity_percent=humidity,pressure_kpa=pressure,uniform_air=uniform))


def independent_branch(scene,wall,frequency,air,policy,condition):
    def state(status,reason,**values):return dict(status=status,reason=reason,attenuation_db=None,**values)
    if scene['scene_kind'] not in ('synthetic','published_reference'):return state('withheld','real_area_scene_not_qualified')
    if wall['slope_from_vertical_deg']>=15:return state('absent','wall_slope_outside_reflection_domain')
    if wall['slope_from_vertical_deg']!=0:return state('withheld','inclined_wall_not_implemented')
    boundary=(weather.prior.independent if condition=='homogeneous' else weather.independent)(scene,frequency)
    if 'reason' in boundary:return state('withheld','incomplete_unfolded_boundary_inputs')
    nodes=[scene['source'],*(boundary['controlling_edges'] if boundary['branch']=='diffraction_including_ground' else []),scene['receiver']]
    x=wall['unfolded_x_m'];tol=64*math.ulp(max(1.,abs(x),*(abs(v) for point in nodes for v in point)))
    if any(abs(x-a[0])<=tol for a in nodes):return state('withheld','coincident_reflection_and_diffraction_or_unsupported_order')
    a,b=next((a,b) for a,b in zip(nodes,nodes[1:]) if a[0]<x<b[0])
    distance=float(np.linalg.norm(np.array(scene['receiver'])-scene['source']))
    radius=max(1000.,8*distance) if condition=='favourable' else None
    if radius is None:height=float(np.interp(x,[a[0],b[0]],[a[1],b[1]]))
    else:
        center,_,_=weather.leg(a,b,radius);height=float(center[1]+math.sqrt(radius**2-(x-center[0])**2))
    details=dict(hit_height_m=height)
    if height<wall['base_z_m']-tol or height>wall['top_z_m']+tol:return state('absent','ray_passes_outside_wall_height',**details)
    dimensions=[wall.get(k,{}).get(condition) for k in ('incident_view_height_m','incident_view_width_m')]
    if any(v is None for v in dimensions):return state('withheld','unknown_incident_view_wall_dimensions',**details)
    if min(dimensions)<.5:return state('absent','incident_view_wall_dimension_below_minimum',**details)
    alpha=wall['absorption_by_band'][prior.BANDS.index(frequency)]
    if alpha is None:return state('withheld','unknown_wall_absorption',**details)
    if alpha==1:return state('absent','perfectly_absorbing_wall_zero_energy_extension',**details)
    iso_frequency=frequency if policy=='nominal' else 1000*10**(.3*(prior.BANDS.index(frequency)-4))
    atmosphere=air_absorption(iso_frequency,air['temperature_c'],air['relative_humidity_percent'],air['pressure_kpa'],air['uniform_air'])
    top=[x,wall['top_z_m']];distances=[math.dist(a,top),math.dist(top,b)];direct=math.dist(a,b)
    delta=direct-sum(distances) if radius is None else weather.arc(direct,radius)-sum(weather.arc(d,radius) for d in distances)
    q=40*delta/(340/frequency);retro=10*math.log10(3+q) if q>=-2 else 0.
    geometric=10**(-1.1)/distance**2;atmospheric=10**(-atmosphere['alpha_db_per_km']*distance/1000/10)
    transfer=geometric*atmospheric*10**(-boundary['boundary_db']/10)*(1-alpha)*10**(-retro/10)
    return dict(status='conditional_research',attenuation_db=-10*math.log10(transfer),hit_height_m=height,
        reflected_distance_m=distance,divergence_db=-10*math.log10(geometric),atmospheric_attenuation_db=-10*math.log10(atmospheric),
        boundary_db=boundary['boundary_db'],absorption_loss_db=-10*math.log10(1-alpha),reflection_power_correction_db=10*math.log10(1-alpha),
        retrodiffraction_db=retro,retrodiffraction_delta_prime_m=delta)


def compare_result(actual,scene,wall,f,air,policy,p,counts,errors,geometry_errors):
    expected=[independent_branch(scene,wall,f,air,policy,c) for c in STATES]
    for c,b in zip(STATES,expected):prior.compare_branch(actual[c],b,counts,errors,geometry_errors)
    if p is None:assert actual['attenuation_db'] is None
    else:
        mixed=prior.independent_mix(expected,[1-p,p]);assert actual['status']==mixed['status']
        if mixed['attenuation_db'] is not None:
            close(actual['attenuation_db'],mixed['attenuation_db']);errors.append(abs(actual['attenuation_db']-mixed['attenuation_db']))
        else:assert actual['attenuation_db'] is None
    assert actual['annual_noise_prediction'] is None and not actual['regulatory_implementation_qualified']
    return expected


def line_transfer(model,source,receiver,f,air,p):
    s,r=list(source),list(receiver);distance=math.dist(s,r)
    if model in ('freefield','directional-weather'):
        h=10**(-1.1)/distance**2;fav=2*h
        p=(.2 if s[0]<0 else .8) if model=='directional-weather' else p
        return np.array([h,fav,(1-p)*h+p*fav])
    length=float(prior.LineString([s[:2],r[:2]]).length)
    scene=dict(points=[[0.,0.],[length,0.]],source=[0.,s[2]],receiver=[length,r[2]],source_g=.5,
        ground_segments=[[0.,length,.5]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    if model in ('screen','screen-reflection'):scene['obstacles']=[dict(vertices=[[length*.4,6.]])]
    alpha=air_absorption(1000*10**(.3*(prior.BANDS.index(f)-4)),air['temperature_c'],air['relative_humidity_percent'],air['pressure_kpa'],air['uniform_air'])['alpha_db_per_km']
    attenuation_common=10**(-1.1)/distance**2*10**(-alpha*distance/1000/10)
    conditions=[weather.prior.independent(scene,f),weather.independent(scene,f)]
    energies=[attenuation_common*10**(-b['boundary_db']/10) for b in conditions]
    if model in ('reflection','screen-reflection'):
        plan=prior.plan_image(s,r,[-1000.,-20.],[1000.,-20.]);total=plan['unfolded_plan_distance_m']
        ref_scene=dict(scene,points=[[0.,0.],[total,0.]],receiver=[total,r[2]],ground_segments=[[0.,total,.5]],
            obstacles=[dict(vertices=[[total*52/70,6.]])] if model=='screen-reflection' else [])
        wall=dict(unfolded_x_m=plan['first_plan_leg_m'],base_z_m=0.,top_z_m=8.,slope_from_vertical_deg=0.,absorption_by_band=[.2]*8,
            incident_view_height_m=dict(homogeneous=8.,favourable=8.),incident_view_width_m=dict(homogeneous=1000.,favourable=1000.),unfolded_geometry_confirmed=True)
        for i,c in enumerate(STATES):
            b=independent_branch(ref_scene,wall,f,air,'exact_base10',c)
            assert b['status']=='conditional_research';energies[i]+=10**(-b['attenuation_db']/10)
    return np.array([*energies,(1-p)*energies[0]+p*energies[1]])


def figure(output,benchmarks,line_results):
    plt=geometry.plt;plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(12,4.6))
    b=next(b for b in benchmarks if b['case_id']=='TC18' and b['frequency_hz']==63)
    for state,color in zip(STATES,('#216c83','#9c3856')):
        branch=b['result'][state];a,end=branch['reflecting_leg'];xs=np.linspace(a[0],end[0],100)
        if state=='homogeneous':zs=np.interp(xs,[a[0],end[0]],[a[1],end[1]])
        else:
            center,_,_=weather.leg(a,end,branch['ray_radius_m']);zs=center[1]+np.sqrt(branch['ray_radius_m']**2-(xs-center[0])**2)
        axes[0].plot(xs,zs,color=color,label=state.title()+' physical leg')
    axes[0].plot([129.74960684335466]*2,[0,15],color='#333333',linewidth=3,label='Reflector (15 m top)')
    axes[0].scatter([85.16596373948585],[12],color='#216c83',label='Screen edge')
    axes[0].set(title='TC18: reflection after the screen',xlabel='Unfolded chainage (m)',ylabel='Elevation (m)',ylim=(10,16));axes[0].legend(fontsize=8)
    for model in ('freefield','screen','screen-reflection'):
        row=next(r for r in line_results if r['case_id']==model and r['frequency_hz']==1000)
        axes[1].plot([h['source_points'] for h in row['result']['history']],[10*math.log10(h['relative_energy']['mixed']) for h in row['result']['history']],'.-',label=model)
    axes[1].set(title='Road integral converges under subdivision',xlabel='Source points per refinement',ylabel='Conditional synthetic octave level (dB)');axes[1].legend(fontsize=8)
    fig.text(.04,.02,'Controlled research cases only. Numerical convergence does not establish real-world accuracy or annual noise.',fontsize=10)
    fig.tight_layout(rect=(0,.055,1,1));fig.savefig(output/'screened_line_verification.png',dpi=170);plt.close(fig)


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use a fresh workspace generation')
    geometry.check_generation(inputs);sha=file_hash(inputs/'inventory.json');report=read(inputs/'report.json');protocol=read(inputs/'protocol.json')
    for name,parent_sha in report['parent_inventory_sha256'].items():
        path=ROOT/'artifacts'/name;geometry.check_generation(path);assert file_hash(path/'inventory.json')==parent_sha
    for name,code_sha in report['code_sha256'].items():assert file_hash(inputs/'code'/name)==code_sha and file_hash(ROOT/name)==code_sha
    sources=ROOT/'artifacts/screened_line_sources_v1';counts=Counter();errors=[];geometry_errors=[]
    synth={c['case_id']:c for c in protocol['synthetic_scenes']}
    for row in read(inputs/'synthetic_results.json')['results']:
        c=synth[row['case_id']];compare_result(row['result'],c['scene'],c['wall'],row['frequency_hz'],c['air'],c['policy'],c['probability'],counts,errors,geometry_errors)
    public={c['case_id']:c for c in protocol['reference_scenes']};benchmarks=read(inputs/'reference_results.json')['benchmarks'];text=(sources/'NoiseModelling-CnossosTests.java').read_text()
    strict_exceptions=[]
    for row in benchmarks:
        c=public[row['case_id']];scene,wall,_=original_fixture(sources,c['case_id'])
        weather.nested(c['scene'],scene,geometry_errors);weather.nested(c['wall'],wall,geometry_errors)
        expected=compare_result(row['result'],scene,wall,row['frequency_hz'],protocol['air'],'exact_base10',.5,counts,errors,geometry_errors)
        direct_scene=weather.prior.reference_fixture(sources,c['direct_scene'])
        direct_energy=[]
        original_boundaries={}
        for state in STATES:
            b=(weather.prior.independent if state=='homogeneous' else weather.independent)(direct_scene,row['frequency_hz'])
            actual=row['direct'][state];assert actual['branch']==b['branch'];close(actual['boundary_db'],b['boundary_db'])
            original_boundaries[state]=(weather.prior.independent if state=='homogeneous' else weather.independent)(scene,row['frequency_hz'])
            distance=math.dist(direct_scene['source'],direct_scene['receiver'])
            air=protocol['air'];alpha=air_absorption(1000*10**(.3*(prior.BANDS.index(row['frequency_hz'])-4)),air['temperature_c'],air['relative_humidity_percent'],air['pressure_kpa'],air['uniform_air'])['alpha_db_per_km']
            energy=10**(-1.1)/distance**2*10**(-(b['boundary_db']+alpha*distance/1000)/10)
            direct_energy.append(energy);close(row['direct'][state+'_attenuation_db'],-10*math.log10(energy))
        reflected_energy=[0. if b['status']=='absent' else 10**(-b['attenuation_db']/10) for b in expected]
        reflected_mix=sum(reflected_energy)/2
        combined_mix=sum(direct_energy+reflected_energy)/2
        for i,state in enumerate(STATES):close(row['combined'][state]['attenuation_db'],-10*math.log10(direct_energy[i]+reflected_energy[i]))
        close(row['mixed_combined']['attenuation_db'],-10*math.log10(combined_mix))
        i=prior.BANDS.index(row['frequency_hz'])
        for comparison in row['comparisons']:
            component,field=comparison['component'],comparison['field']
            if component in STATES:
                if field=='source_relative_level_db':value=93-expected[STATES.index(component)]['attenuation_db']
                elif field in expected[STATES.index(component)]:value=expected[STATES.index(component)][field]
                else:value=original_boundaries[component][field]
            else:value=93+(-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1)[i]+10*math.log10(reflected_mix if component=='mixed_reflection' else combined_mix)
            close(comparison['actual'],value)
            variable=comparison['original_variable']
            if variable=='final_total_a_weighted':
                target=(prior.original_reflection_array(text,c['case_id'],'expectedFullLA') if c['case_id']=='TC27' else prior.final_total(text,c['case_id']))[i]
            else:target=prior.original_reflection_array(text,c['case_id'],variable)[i]
            close(comparison['expected'],target);close(comparison['error_db'],comparison['actual']-target)
            tolerance=.1 if c['case_id']=='TC27' and 'level' in comparison['field'] else .02
            assert comparison['tolerance_db']==tolerance and abs(comparison['error_db'])<=tolerance
            assert comparison['tighter_audit_target_met']==(abs(comparison['error_db'])<=.02)
            if not comparison['tighter_audit_target_met']:strict_exceptions.append(dict(case_id=c['case_id'],frequency_hz=row['frequency_hz'],**comparison))
            counts['original_scalar_comparisons_checked']+=1
    lines={c['case_id']:c for c in protocol['line_scenes']};line_results=read(inputs/'line_results.json')['results'];line_errors=[];quadrature_errors=[]
    for row in line_results:
        c=lines[row['case_id']];f=row['frequency_hz'];total=np.zeros(3);source_power=0.
        if 'source_emission_inputs' in c:
            original=c['source_emission_inputs'];coef=read(inputs/'code/tests/fixtures/road_emission_2021_coefficients.json');cat=coef['categories']['1']
            assert original['case']['speed_kmh']==70. and original['case']['surface']=='Reference road surface'
            assert all(v==(2400. if k=='cars_and_taxis' else 0.) for k,v in original['annual_categories'].items())
            period=original['period'];flow=2400*original['profiles']['cars_and_taxis']['period_fractions'][period]/{'day':12,'evening':4,'night':8}[period]
            i=prior.BANDS.index(f);power=(10**(cat['AR'][i]/10)+10**(cat['AP'][i]/10))*flow/(1000*70)
            close(c['segments'][0]['relative_power_per_m'][i],power);counts['independent_emission_to_line_band_checks']+=1
        for seg in c['segments']:
            a,b=np.array(seg['start']),np.array(seg['end']);length=float(np.linalg.norm((b-a)[:2] if seg['length_basis']=='horizontal' else b-a))
            power=seg['relative_power_per_m'][prior.BANDS.index(f)];source_power+=power*length
            @lru_cache(maxsize=4096)
            def integrand(t):return power*length*line_transfer(c['model'],a+t*(b-a),c['receiver'],f,protocol['air'],c['probability'])
            values,error=quad_vec(integrand,0.,1.,epsabs=1e-8,epsrel=1e-7,limit=500,points=seg['breakpoints'])
            total+=values;quadrature_errors.append(float(error))
        actual=row['result'];close(actual['source_relative_power'],source_power)
        assert actual['converged'] and actual['status']=='conditional_research' and actual['annual_noise_prediction'] is None
        for i,state in enumerate((*STATES,'mixed')):
            difference=actual['branches'][state]['receiver_level_db']-10*math.log10(total[i]);assert abs(difference)<=.005,(c['case_id'],f,state,difference)
            line_errors.append(abs(difference));counts['independent_line_integrals_checked']+=1
        if c['model']=='freefield':
            a=math.hypot(30.,3.95);exact=1000*10**(-1.1)*2*math.atan(50/a)/a
            close(total[0],exact);counts['analytic_finite_line_checks']+=1
    for row in read(inputs/'real_path_readiness.json')['probes']:
        assert row['screened_reflection_attenuation_db'] is None and row['road_integrated_receiver_level_db'] is None
        counts['real_paths_withheld_checked']+=1
    summary=dict(counts=counts,maximum_independent_reflection_db_difference=max(errors),maximum_independent_geometry_difference=max(geometry_errors),
        maximum_line_vs_independent_quadrature_db_difference=max(line_errors),maximum_adaptive_quadrature_absolute_energy_error_estimate=max(quadrature_errors),
        tighter_public_target_exceptions=strict_exceptions,numerical_error_is_field_uncertainty=False)
    output.mkdir(parents=True);figure(output,benchmarks,line_results)
    (output/'report.json').write_bytes(json_bytes(dict(summary=summary,parent_inventory_sha256=sha,
        external_engine_executed=False,real_area_noise_validated=False,full_current_method_qualified=False,
        method='Earlier independent numerical references; GEOS public geometry, Decimal ISO air, QUADPACK arcs and adaptive Gauss-Kronrod road integration. No evaluated module/builder import.')))
    names=['scripts/80_verify_screened_line_checks.py','scripts/77_verify_reflection_checks.py','scripts/74_verify_weather_checks.py','scripts/71_verify_sightline_checks.py',
        'scripts/68_verify_multiple_edge_checks.py','scripts/65_verify_terrain_boundary_checks.py','scripts/62_verify_terrain_profile_checks.py']
    for n in names:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    geometry.check_generation(inputs);assert file_hash(inputs/'inventory.json')==sha
    for name,parent_sha in report['parent_inventory_sha256'].items():assert geometry.check_generation(ROOT/'artifacts'/name)==parent_sha
    for name,code_sha in report['code_sha256'].items():assert file_hash(ROOT/name)==code_sha
    files={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity='screened-line-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity,files=files)))
    print(dict(generation_id=identity,summary=summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',default=str(ROOT/'artifacts/screened_line_checks_v3'));p.add_argument('--output',default=str(ROOT/'artifacts/screened_line_review_v2'))
    a=p.parse_args();run(a.inputs,a.output)
