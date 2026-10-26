"""Freeze screened reflections and incoherent road source-line integration."""
import argparse
from collections import Counter
import copy
import importlib.util
import math
from pathlib import Path
import re
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.reflection_path import specular_plan,combine_paths_attenuation,mix_conditions
from quiet_uk.screened_reflection import screened_wall_attenuation
from quiet_uk.favourable_propagation import direct_attenuation
from quiet_uk.road_line_integration import integrate_road_line,point_path_transfer
from quiet_uk.road_emission import source_spectrum,BASE_CASE
from quiet_uk.road_input_audit import VEHICLE_FIELDS
spec=importlib.util.spec_from_file_location('frozen_builder',ROOT/'scripts/76_run_reflection_checks.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
read,helper,AIR=prior.read,prior.helper,prior.AIR
TESTS=('TC16','TC17','TC18','TC26','TC27')


def arrays(text,test):
    body=text.split('public void '+test+'()')[1].split('public void ')[0]
    result={}
    for name,values in re.findall(r'(\w+)\s*=\s*new double\s*\[\s*\]\s*\{([^}]+)\}',body):
        result[name]=[None if 'NaN' in v else float(v.strip()) for v in values.split(',') if v.strip()]
    totals=re.findall(r'assertArrayEquals\(\s*new double\[\]\s*\{([^}]+)\}',body)
    if test=='TC27': result['final_total_a_weighted']=result['expectedFullLA']
    else: result['final_total_a_weighted']=[float(v.strip()) for v in totals[-1].split(',')]
    return result


def fixture(sources,test):
    raw=read(sources/(test+'_Reflection.json'))['cutPoints'];xs=[0.]
    for a,b in zip(raw,raw[1:]):
        xs.append(xs[-1]+math.hypot(b['coordinate']['x']-a['coordinate']['x'],b['coordinate']['y']-a['coordinate']['y']))
    ref=next(c for c in raw if c['type']=='Reflection');index=raw.index(ref)
    xy=lambda c:[c['x'],c['y']]
    plan_input=dict(source_xy=xy(raw[0]['coordinate']),receiver_xy=xy(raw[-1]['coordinate']),wall_start_xy=xy(ref['wall']['p0']),wall_end_xy=xy(ref['wall']['p1']))
    plan=specular_plan(**plan_input);assert plan['status']=='conditional_research'
    assert math.dist(plan['reflection_xy'],xy(ref['coordinate']))<.002
    assert abs(plan['unfolded_horizontal_distance_m']-xs[-1])<.002
    a,b=ref['wall']['p0'],ref['wall']['p1'];hit=ref['coordinate']
    u=((hit['x']-a['x'])*(b['x']-a['x'])+(hit['y']-a['y'])*(b['y']-a['y']))/((b['x']-a['x'])**2+(b['y']-a['y'])**2)
    points=[];obstacles=[];enter=None
    for x,c in zip(xs,raw):
        if points and x==points[-1][0]: assert points[-1][1]==c['zGround']
        else: points.append([x,c['zGround']])
        if c['type']=='Wall':
            if c.get('intersectionType')=='BUILDING_ENTER': enter=[x,c['coordinate']['z']]
            elif c.get('intersectionType')=='BUILDING_EXIT':
                assert enter is not None;obstacles.append(dict(vertices=[enter,[x,c['coordinate']['z']]],roof_g=0.));enter=None
            else: obstacles.append(dict(vertices=[[x,c['coordinate']['z']]]))
    assert enter is None
    scene=dict(case_id=test,points=points,source=[0.,raw[0]['coordinate']['z']],receiver=[xs[-1],raw[-1]['coordinate']['z']],
        ground_segments=[[x,y,c['groundCoefficient']] for x,y,c in zip(xs,xs[1:],raw) if y>x],source_g=raw[0]['groundCoefficient'],
        obstacles=obstacles,opaque=True,infinite_lateral_extent=True,scene_kind='published_reference')
    wall=dict(unfolded_x_m=xs[index],base_z_m=ref['zGround'],top_z_m=a['z']+u*(b['z']-a['z']),slope_from_vertical_deg=0.,
        incident_view_height_m=dict(homogeneous=1.,favourable=1.),incident_view_width_m=dict(homogeneous=1.,favourable=1.),
        absorption_by_band=ref['wallAlpha'],unfolded_geometry_confirmed=True)
    return dict(case_id=test,scene=scene,wall=wall,direct_scene=prior.prior.prior.fixture(sources,test),plan_input=plan_input,
        eligibility_assumption='Explicit incident-view dimensions >=0.5m for supplied reference path, not recovered measurements.',
        wall_base_assumption='Supplied local zGround; opacity/lateral extent and roof G=0 declared calculation assumptions.')


def synthetic_cases():
    scene=dict(points=[[0.,0.],[200.,0.]],source=[0.,1.],receiver=[200.,4.],ground_segments=[[0.,200.,.5]],source_g=.5,
        obstacles=[dict(vertices=[[80.,12.]])],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    wall=dict(unfolded_x_m=130.,base_z_m=0.,top_z_m=20.,slope_from_vertical_deg=0.,absorption_by_band=[.2]*8,
        incident_view_height_m=dict(homogeneous=20.,favourable=20.),incident_view_width_m=dict(homogeneous=20.,favourable=20.),unfolded_geometry_confirmed=True)
    cases=[]
    def add(name,sc=None,wa=None,p=.5):
        s=copy.deepcopy(scene);w=copy.deepcopy(wall);s.update(sc or {});w.update(wa or {})
        cases.append(dict(case_id=name,scene=s,wall=w,air=AIR,probability=p,policy='exact_base10'))
    add('reflection-after-screen');add('reflection-before-screen',wa=dict(unfolded_x_m=40.))
    add('reflection-between-screens',sc=dict(obstacles=[dict(vertices=[[60.,12.]]),dict(vertices=[[150.,14.]])]),wa=dict(unfolded_x_m=100.))
    add('reflection-after-terrain',sc=dict(points=[[0.,0.],[80.,10.],[200.,0.]],obstacles=[]))
    add('reflection-before-terrain',sc=dict(points=[[0.,0.],[80.,10.],[200.,0.]],obstacles=[]),wa=dict(unfolded_x_m=40.))
    add('reflection-before-roof',sc=dict(obstacles=[dict(vertices=[[80.,12.],[140.,12.]],roof_g=0.)]),wa=dict(unfolded_x_m=40.))
    add('coincident-screen-and-reflector',wa=dict(unfolded_x_m=80.))
    add('wall-misses-selected-leg',wa=dict(top_z_m=2.));add('wall-top-retrodiffraction',wa=dict(top_z_m=12.))
    add('unknown-absorption',wa=dict(absorption_by_band=[None]*8));add('unknown-ground',sc=dict(ground_segments=[[0.,200.,None]]))
    add('unknown-roof-ground',sc=dict(obstacles=[dict(vertices=[[80.,12.],[140.,12.]],roof_g=None)]),wa=dict(unfolded_x_m=40.))
    add('unknown-view',wa=dict(incident_view_height_m={}));add('unknown-weather',p=None)
    add('real-scene',sc=dict(scene_kind='unqualified'));add('known-zero-reflection',wa=dict(absorption_by_band=[1.]*8))
    add('curvature-clears-screen',sc=dict(points=[[0.,0.],[1000.,0.]],receiver=[1000.,4.],ground_segments=[[0.,1000.,.5]],obstacles=[dict(vertices=[[500.,12.]])]),wa=dict(unfolded_x_m=750.,top_z_m=120.))
    return cases


def model_transfer(model,p=.3):
    def evaluate(s,r,f):
        if model in ('freefield','directional-weather'):
            attenuation=20*math.log10(math.dist(s,r))+11
            return dict(homogeneous=dict(status='conditional_research',attenuation_db=attenuation),
                favourable=dict(status='conditional_research',attenuation_db=attenuation-10*math.log10(2)),
                probability_favourable=(.2 if s[0]<0 else .8) if model=='directional-weather' else p)
        length=math.dist(s[:2],r[:2])
        scene=dict(points=[[0.,0.],[length,0.]],source=[0.,s[2]],receiver=[length,r[2]],source_g=.5,
            ground_segments=[[0.,length,.5]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
        if model in ('screen','screen-reflection'):scene['obstacles']=[dict(vertices=[[.4*length,6.]])]
        reflections=[]
        if model in ('reflection','screen-reflection'):
            plan_input=dict(source_xy=s[:2],receiver_xy=r[:2],wall_start_xy=[-1000.,-20.],wall_end_xy=[1000.,-20.])
            plan=specular_plan(**plan_input);total=plan['unfolded_horizontal_distance_m']
            ref_scene=copy.deepcopy(scene);ref_scene.update(points=[[0.,0.],[total,0.]],receiver=[total,r[2]],ground_segments=[[0.,total,.5]],
                obstacles=[dict(vertices=[[52/70*total,6.]])] if model=='screen-reflection' else [])
            wall=dict(unfolded_x_m=plan['reflection_distance_from_source_m'],base_z_m=0.,top_z_m=8.,slope_from_vertical_deg=0.,
                absorption_by_band=[.2]*8,incident_view_height_m=dict(homogeneous=8.,favourable=8.),
                incident_view_width_m=dict(homogeneous=1000.,favourable=1000.),unfolded_geometry_confirmed=True)
            reflections=[dict(scene=ref_scene,wall=wall,plan_input=plan_input)]
        return point_path_transfer(s,r,f,direct_scene=scene,reflections=reflections,reflection_support_complete=True,
            air=AIR,probability_favourable=p,absorption_frequency_policy='exact_base10')
    return evaluate


def line_cases():
    seg=dict(segment_id='road',start=[-50.,0.,.05],end=[50.,0.,.05],length_basis='horizontal',relative_power_per_m=[1000.]*8,breakpoints=[.5])
    result=[]
    for model in ('freefield','directional-weather','clear','screen','reflection','screen-reflection'):
        result.append(dict(case_id=model,model=model,segments=[copy.deepcopy(seg)],receiver=[0.,30.,4.],probability=.3))
    split=copy.deepcopy(seg);split['segment_id']='road-east';split['start'][0]=0.;split['breakpoints']=[]
    west=copy.deepcopy(seg);west['segment_id']='road-west';west['end'][0]=0.;west['breakpoints']=[]
    result.append(dict(case_id='split-freefield',model='freefield',segments=[west,split],receiver=[0.,30.,4.],probability=.3))
    annual={field:2400. if field=='cars_and_taxis' else 0. for field in VEHICLE_FIELDS}
    profiles={field:dict(period_fractions=dict(day=.6,evening=.2,night=.2)) for field in VEHICLE_FIELDS}
    coefficients=read(ROOT/'tests/fixtures/road_emission_2021_coefficients.json')
    emission=source_spectrum(annual,profiles,BASE_CASE,coefficients)
    for period in ('day','evening','night'):
        road=copy.deepcopy(seg);road['relative_power_per_m']=emission['period_spectra'][period]['relative_power_per_m']
        result.append(dict(case_id='synthetic-emission-'+period,model='screen-reflection',segments=[road],receiver=[0.,30.,4.],probability=.3,
            source_emission_inputs=dict(annual_categories=annual,profiles=profiles,case=BASE_CASE,period=period),
            qualification='Entire traffic/profile/speed/weather/road/screen/reflector example is synthetic. Conditional period output only; no qualified annual indicator.'))
    return result


def run(sources,output):
    sources,output=Path(sources).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use a fresh workspace generation')
    parents={p.name:p for p in (sources,ROOT/'artifacts/reflection_checks_v3',ROOT/'artifacts/reflection_review_v4',ROOT/'artifacts/weather_checks_v2')}
    for p in parents.values():helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    public=[fixture(sources,t) for t in TESTS];text=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8');benchmarks=[]
    for c in public:
        expected=arrays(text,c['case_id'])
        for i,f in enumerate(BANDS):
            r=screened_wall_attenuation(c['scene'],c['wall'],f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            direct=direct_attenuation(c['direct_scene'],f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            combined={state:combine_paths_attenuation([dict(status='conditional_research',attenuation_db=direct[state+'_attenuation_db']),r[state]]) for state in ('homogeneous','favourable')}
            mixed=mix_conditions(combined['homogeneous'],combined['favourable'],.5);comparisons=[]
            def check(state,field,variable,value):
                target=expected[variable][i]
                if target is None:return
                # TC27 published receiver assertions explicitly use VERY_LOW=.1.
                # Keep the tighter .02 target as a separate visible diagnostic.
                tolerance=.1 if c['case_id']=='TC27' and 'level' in field else .02
                error=value-target;assert abs(error)<=tolerance,(c['case_id'],f,state,field,value,target,error)
                comparisons.append(dict(component=state,field=field,original_variable=variable,actual=value,expected=target,error_db=error,tolerance_db=tolerance,
                    tighter_audit_target_db=.02,tighter_audit_target_met=abs(error)<=.02))
            for state,suffix in (('homogeneous','H'),('favourable','F')):
                branch=r[state]
                if branch['status']=='absent':continue
                assert branch['status']=='conditional_research',(c['case_id'],f,branch)
                check(state,'boundary_db','expectedABoundary'+suffix,branch['boundary']['boundary_db'])
                check(state,'source_relative_level_db','expectedL'+suffix,93-branch['attenuation_db'])
                if 'expectedRetroDiff'+suffix in expected:check(state,'retrodiffraction_db','expectedRetroDiff'+suffix,branch['retrodiffraction_db'])
                if state=='homogeneous':
                    check(state,'divergence_db','expectedADiv',branch['divergence_db'])
                    check(state,'atmospheric_attenuation_db','expectedAAtm',branch['atmospheric_attenuation_db'])
                if branch['boundary']['branch']=='diffraction_including_ground':
                    for field,variable in prior.prior.prior.FIELDS.items():
                        if variable+suffix in expected:check(state,field,variable+suffix,branch['boundary'][field])
            check('mixed_reflection','a_weighted_source_relative_level_db','expectedLA',93+prior.WEIGHTING[i]-r['attenuation_db'])
            check('combined','a_weighted_source_relative_level_db','final_total_a_weighted',93+prior.WEIGHTING[i]-mixed['attenuation_db'])
            benchmarks.append(dict(case_id=c['case_id'],frequency_hz=f,result=r,direct=direct,combined=combined,mixed_combined=mixed,comparisons=comparisons))
    cases=synthetic_cases();synthetics=[dict(case_id=c['case_id'],frequency_hz=f,result=screened_wall_attenuation(c['scene'],c['wall'],f,c['air'],probability_favourable=c['probability'],absorption_frequency_policy=c['policy'])) for c in cases for f in BANDS]
    lines=line_cases();line_results=[]
    for c in lines:
        for f in BANDS:
            result=integrate_road_line(c['segments'],c['receiver'],f,model_transfer(c['model'],c['probability']),scene_kind='synthetic',metre_coordinates_confirmed=True,path_support_complete=True)
            assert result['status']=='conditional_research',(c['case_id'],f,result)
            line_results.append(dict(case_id=c['case_id'],frequency_hz=f,result=result))
    readiness=read(parents['weather_checks_v2']/'real_path_readiness.json')['probes']
    readiness=[dict(p,screened_reflection_attenuation_db=None,road_integrated_receiver_level_db=None,qualified_complete_source_line_and_paths=None) for p in readiness]
    summary=dict(public_frequency_cases=len(benchmarks),public_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_public_db_difference=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons']),
        tighter_public_target_exceptions=sum(not c['tighter_audit_target_met'] for b in benchmarks for c in b['comparisons']),synthetic_frequency_cases=len(synthetics),
        synthetic_branch_states=dict(Counter(r['result'][s]['status'] for r in synthetics for s in ('homogeneous','favourable'))),
        line_frequency_cases=len(line_results),maximum_line_evaluations=max(r['result']['evaluations'] for r in line_results),real_paths_withheld=len(readiness))
    output.mkdir(parents=True)
    protocol=dict(reference_scenes=public,synthetic_scenes=cases,line_scenes=lines,air=AIR,reference_probability=.5,bands_hz=list(BANDS),
        public_tolerance_policy='0.02 dB, except TC27 receiver values use the original explicitly published VERY_LOW=0.1 dB assertion. All comparisons separately retain the 0.02 dB audit-target result.',numerical_refinement_tolerance_db=.005,
        limits='Single vertical reflection, declared opaque horizontal edges/terrain and full 2D support. No multiple/inclined/lateral reflections, automatic GIS path search, field accuracy or annual/real-area release.',
        line_policy='Incoherent per-metre power times segment length, sum paths in each condition, mix supplied weather at each source point then integrate. Midpoint subdivision is an explicit numerical choice, not an Annex segmentation prescription.',
        source_policy='Seven constant-power line cases use synthetic 1000 units/m/octave; three emission cases connect the frozen vehicle-power kernel using synthetic 2400 cars/day, 70km/h reference surface and explicit 0.6/0.2/0.2 period allocation. Public paths use 93dB point power. None represents observed traffic.',
        reference_policy='Raw cuts retained; duplicate ground coordinates merged, zero-length ground intervals omitted. NaN published intermediate expectations are excluded, not replaced by zero. Global unfolded S/R radius retained for every favourable physical reflection leg.')
    files={'protocol.json':protocol,'reference_results.json':dict(benchmarks=benchmarks),'synthetic_results.json':dict(results=synthetics),'line_results.json':dict(results=line_results),'real_path_readiness.json':dict(probes=readiness)}
    names=['scripts/78_acquire_screened_line_sources.py','scripts/79_run_screened_line_checks.py','src/quiet_uk/screened_reflection.py','src/quiet_uk/road_line_integration.py',
        'src/quiet_uk/road_emission.py','tests/fixtures/road_emission_2021_coefficients.json']
    report=dict(summary=summary,parent_inventory_sha256=hashes,code_sha256={n:file_hash(ROOT/n) for n in names},
        external_engine_executed=False,eligible_for_absolute_noise_release=False,full_current_method_qualified=False,real_area_noise_validated=False)
    files['report.json']=report
    for n,data in files.items():(output/n).write_bytes(json_bytes(data))
    for n in names:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(dict(generation_id=helper.seal(output,'screened-line-checks-'),summary=summary),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--sources',default=str(ROOT/'artifacts/screened_line_sources_v1'));parser.add_argument('--output',default=str(ROOT/'artifacts/screened_line_checks_v3'))
    a=parser.parse_args();run(a.sources,a.output)
