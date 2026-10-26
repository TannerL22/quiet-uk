"""Freeze single-wall reflection, known absent paths and real-input readiness."""
import argparse
from collections import Counter
import copy
import importlib.util
import math
from pathlib import Path
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.favourable_propagation import direct_attenuation
from quiet_uk.reflection_path import METHOD,combine_paths_attenuation,mix_conditions,single_wall_attenuation,specular_plan,upper_ray_height
spec = importlib.util.spec_from_file_location('prior_cases',ROOT/'scripts/73_run_weather_checks.py')
prior = importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
helper,read,AIR = prior.helper,prior.read,prior.AIR
TESTS = ('TC16','TC26')
WEIGHTING = (-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1)


def original_arrays(text,test):
    body=text.split('public void '+test+'()')[1].split('public void ')[0]
    arrays={}
    for name,values in re.findall(r'(\w+)\s*=\s*new double\s*\[\s*\]\s*\{([^}]+)\}',body):
        arrays[name]=[float(v.strip()) for v in values.split(',') if v.strip()]
    totals=re.findall(r'assertArrayEquals\(\s*new double\[\]\s*\{([^}]+)\}',body)
    arrays['final_total_a_weighted']=[float(v.strip()) for v in totals[-1].split(',')]
    return arrays


def reflection_fixture(sources,test):
    raw=read(sources/(test+'_Reflection.json'))['cutPoints']
    xs=[0.]
    for a,b in zip(raw,raw[1:]):
        xs.append(xs[-1]+math.hypot(b['coordinate']['x']-a['coordinate']['x'],b['coordinate']['y']-a['coordinate']['y']))
    assert sum(c['type']=='Reflection' for c in raw)==1
    index=next(i for i,c in enumerate(raw) if c['type']=='Reflection');ref=raw[index]
    wall=ref['wall'];a,b=wall['p0'],wall['p1'];hit=ref['coordinate']
    vx,vy=b['x']-a['x'],b['y']-a['y']
    u=((hit['x']-a['x'])*vx+(hit['y']-a['y'])*vy)/(vx*vx+vy*vy)
    top=a['z']+u*(b['z']-a['z'])
    xy=lambda c:[c['x'],c['y']]
    plan_input=dict(source_xy=xy(raw[0]['coordinate']),receiver_xy=xy(raw[-1]['coordinate']),wall_start_xy=xy(a),wall_end_xy=xy(b))
    plan=specular_plan(**plan_input);assert plan['status']=='conditional_research'
    assert math.dist(plan['reflection_xy'],xy(hit))<.002
    assert abs(plan['unfolded_horizontal_distance_m']-xs[-1])<.002
    points=[[x,c['zGround']] for x,c in zip(xs,raw)]
    # Planar chaining follows both physical legs; no wall-top bump in ground.
    scene=dict(case_id=test,points=points,source=[0.,raw[0]['coordinate']['z']],receiver=[xs[-1],raw[-1]['coordinate']['z']],
        ground_segments=[[x,y,c['groundCoefficient']] for x,y,c in zip(xs,xs[1:],raw)],source_g=raw[0]['groundCoefficient'],
        obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='published_reference')
    inputs=dict(unfolded_x_m=xs[index],base_z_m=ref['zGround'],top_z_m=top,slope_from_vertical_deg=0.,
        incident_view_height_m=dict(homogeneous=1.,favourable=1.),incident_view_width_m=dict(homogeneous=1.,favourable=1.),
        absorption_by_band=ref['wallAlpha'],unfolded_geometry_confirmed=True)
    return dict(case_id=test,scene=scene,wall=inputs,direct_scene=prior.prior.fixture(sources,test),plan_input=plan_input,plan=plan,
        original_reflection_xy=xy(hit),original_reflection_height_m=hit['z'],plan_hit_difference_m=math.dist(plan['reflection_xy'],xy(hit)),
        view_dimensions_qualification='Explicit >=0.5 m eligibility assumption for supplied public path; no dimensions recovered as measured facade data.',
        wall_base_assumption='Local supplied zGround at the reflection point, not surveyed wall base.',
        original_cut_preserved=True)


def scenes():
    scene=dict(points=[[0.,0.],[100.,0.]],source=[0.,1.],receiver=[100.,4.],source_g=.5,
        ground_segments=[[0.,100.,.5]],obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
    wall=dict(unfolded_x_m=50.,base_z_m=0.,top_z_m=40.,slope_from_vertical_deg=0.,
        incident_view_height_m=dict(homogeneous=10.,favourable=10.),incident_view_width_m=dict(homogeneous=10.,favourable=10.),
        absorption_by_band=[0.]*8,unfolded_geometry_confirmed=True)
    cases=[]
    def add(name,scene_change=None,wall_change=None,air_change=None,p=.5,policy='exact_base10'):
        c=dict(case_id=name,scene=copy.deepcopy(scene),wall=copy.deepcopy(wall),air=dict(AIR),probability=p,policy=policy)
        c['scene'].update(scene_change or {});c['wall'].update(wall_change or {});c['air'].update(air_change or {});cases.append(c)
    add('perfect-reflector')
    for alpha in (.5,1.,1-1e-12): add('alpha-'+str(alpha),wall_change=dict(absorption_by_band=[alpha]*8))
    add('frequency-dependent-absorption',wall_change=dict(absorption_by_band=[.1,.2,.3,.4,.5,.6,.7,.5]))
    add('unknown-absorption',wall_change=dict(absorption_by_band=None))
    add('one-unknown-absorption-band',wall_change=dict(absorption_by_band=[0.,0.,0.,0.,None,0.,0.,0.]))
    hh=upper_ray_height(scene['source'],scene['receiver'],50.,'homogeneous')['height_m']
    fh=upper_ray_height(scene['source'],scene['receiver'],50.,'favourable')['height_m']
    for name,height in [('below-h-hit',hh-.001),('at-h-hit',hh),('above-h-hit',hh+.001),
                        ('below-f-hit',fh-.001),('at-f-hit',fh),('above-f-hit',fh+.001)]:
        add(name,wall_change=dict(top_z_m=height))
    add('wall-base-above-ray',wall_change=dict(base_z_m=4.))
    for g in (0.,.3,.7,1.): add('ground-'+str(g),scene_change=dict(ground_segments=[[0.,100.,g]],source_g=g))
    add('varying-ground',scene_change=dict(ground_segments=[[0.,25.,.9],[25.,75.,.5],[75.,100.,.2]],source_g=.9))
    add('dimension-at-minimum',wall_change=dict(incident_view_height_m=dict(homogeneous=.5,favourable=.5),incident_view_width_m=dict(homogeneous=.5,favourable=.5)))
    add('narrow-apparent-width',wall_change=dict(incident_view_width_m=dict(homogeneous=.49,favourable=.49)))
    add('short-apparent-height',wall_change=dict(incident_view_height_m=dict(homogeneous=.49,favourable=.49)))
    add('unknown-h-view-height',wall_change=dict(incident_view_height_m=dict(homogeneous=None,favourable=10.)))
    add('slope-outside-method',wall_change=dict(slope_from_vertical_deg=15.))
    add('inclined-wall-outside-increment',wall_change=dict(slope_from_vertical_deg=1.))
    add('unknown-top',wall_change=dict(top_z_m=None))
    add('unknown-unfolding',wall_change=dict(unfolded_geometry_confirmed=None))
    add('unqualified-real-scene',scene_change=dict(scene_kind='unqualified'))
    add('screen-reflection-interaction',scene_change=dict(obstacles=[dict(vertices=[[20.,6.]])]))
    add('terrain-diffraction-interaction',scene_change=dict(points=[[0.,0.],[50.,5.],[100.,0.]],receiver=[100.,1.]),wall_change=dict(unfolded_x_m=25.))
    add('unknown-ground',scene_change=dict(ground_segments=[[0.,100.,None]]))
    add('unknown-air',air_change=dict(temperature_c=None))
    add('unknown-frequency-policy',policy=None)
    add('unknown-weather-probability',p=None)
    for p in (0.,.25,.75,1.): add('probability-'+str(p),p=p)
    add('zero-weight-unknown-favourable',p=0.,wall_change=dict(incident_view_height_m=dict(homogeneous=10.,favourable=None)))
    add('zero-weight-unknown-homogeneous',p=1.,wall_change=dict(incident_view_height_m=dict(homogeneous=None,favourable=10.)))
    add('vertical-datum-shift',scene_change=dict(points=[[0.,100.],[100.,100.]],source=[0.,101.],receiver=[100.,104.]),
        wall_change=dict(base_z_m=100.,top_z_m=140.))
    return cases


def plan_cases():
    base=dict(source_xy=[2.,1.],receiver_xy=[2.,9.],wall_start_xy=[0.,0.],wall_end_xy=[0.,10.])
    cases=[dict(case_id='base',input=base)]
    for angle in (0.,.3,1.2,math.pi/2,math.pi):
        for shift in ([0.,0.],[400000.,200000.]):
            transformed={key:[p[0]*math.cos(angle)-p[1]*math.sin(angle)+shift[0],p[0]*math.sin(angle)+p[1]*math.cos(angle)+shift[1]] for key,p in base.items()}
            cases.append(dict(case_id='rotation-'+str(angle)+'-shift-'+str(shift),input=transformed))
    cases.append(dict(case_id='reverse-wall',input=dict(base,wall_start_xy=base['wall_end_xy'],wall_end_xy=base['wall_start_xy'])))
    cases.append(dict(case_id='opposite-sides',input=dict(base,receiver_xy=[-2.,9.])))
    cases.append(dict(case_id='source-on-plane',input=dict(base,source_xy=[0.,1.])))
    cases.append(dict(case_id='hit-outside-segment',input=dict(base,receiver_xy=[2.,29.])))
    cases.append(dict(case_id='unknown-wall',input=dict(base,wall_end_xy=None)))
    for name,s,r,angle,shift in [('rotated-on-plane',[0.,1.],[2.,9.],.1,[400000.,200000.]),
                                ('rotated-wall-start-hit',[2.,-3.],[6.,9.],.1,[0.,0.]),
                                ('rotated-wall-end-hit',[2.,9.],[6.,13.],.4,[0.,0.])]:
        values=dict(base,source_xy=s,receiver_xy=r)
        transformed={k:[p[0]*math.cos(angle)-p[1]*math.sin(angle)+shift[0],p[0]*math.sin(angle)+p[1]*math.cos(angle)+shift[1]] for k,p in values.items()}
        cases.append(dict(case_id=name,input=transformed))
    return cases


def run(sources,output):
    sources,output=Path(sources).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    parents={n:ROOT/'artifacts'/n for n in ('weather_checks_v2','weather_review_v2','physical_scene_inputs_v2','ground_cover_inputs_v3','ground_cover_review_v3')}
    parents[sources.name]=sources
    for path in parents.values(): helper.verified(path)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    cases=scenes();results=[]
    for c in cases:
        for f in BANDS:
            result=single_wall_attenuation(c['scene'],c['wall'],f,c['air'],probability_favourable=c['probability'],absorption_frequency_policy=c['policy'])
            results.append(dict(case_id=c['case_id'],frequency_hz=f,result=result))
    plans=[dict(**c,result=specular_plan(**c['input'])) for c in plan_cases()]
    public=[reflection_fixture(sources,t) for t in TESTS];benchmarks=[]
    text=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    for c in public:
        t=c['case_id'];arrays=original_arrays(text,t)
        for i,f in enumerate(BANDS):
            r=single_wall_attenuation(c['scene'],c['wall'],f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            assert r['homogeneous']['status']=='conditional_research'
            assert r['favourable']['status']==('absent' if t=='TC26' else 'conditional_research')
            direct=direct_attenuation(c['direct_scene'],f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            assert direct['status']=='conditional_research'
            combined={state:combine_paths_attenuation([dict(status='conditional_research',attenuation_db=direct[state+'_attenuation_db']),r[state]]) for state in ('homogeneous','favourable')}
            mixed=mix_conditions(combined['homogeneous'],combined['favourable'],.5)
            comparisons=[]
            def check(state,field,variable,actual,expected=None,tol=.02):
                expected=arrays[variable][i] if expected is None else expected;error=actual-expected
                assert abs(error)<=tol,(t,f,state,field,actual,expected,error)
                comparisons.append(dict(component=state,field=field,original_variable=variable,actual=actual,expected=expected,error_db=error,tolerance_db=tol))
            for state,suffix in [('homogeneous','H'),('favourable','F')]:
                branch=r[state]
                if branch['status']=='absent': continue
                check(state,'boundary_db','expectedABoundary'+suffix,branch['boundary']['boundary_db'])
                check(state,'source_relative_level_db','expectedL'+suffix,93-branch['attenuation_db'])
                if t=='TC16': check(state,'retrodiffraction_db','expectedRetroDiff'+suffix,branch['retrodiffraction_db'])
                if state=='homogeneous':
                    check(state,'divergence_db','expectedADiv',branch['divergence_db'])
                    check(state,'atmospheric_attenuation_db','expectedAAtm',branch['atmospheric_attenuation_db'])
                    if t=='TC16': check(state,'reflection_power_correction_db','expectedLabs',branch['reflection_power_correction_db'])
            check('mixed_reflection','source_relative_level_db','expectedL',93-r['attenuation_db'])
            check('combined_paths','a_weighted_source_relative_level_db','final_total_a_weighted',93+WEIGHTING[i]-mixed['attenuation_db'])
            benchmarks.append(dict(case_id=t,frequency_hz=f,result=r,direct=direct,combined=combined,mixed_combined=mixed,
                comparisons=comparisons,source_commit=read(sources/'manifest.json')['commit'],external_engine_executed=False))
    readiness=[dict(p,qualified_wall_plan_geometry=None,qualified_wall_top_and_base_datum=None,qualified_incident_view_dimensions=None,
        qualified_wall_absorption_by_band=None,qualified_wall_historical_presence=None,reflection_attenuation_db=None)
        for p in read(parents['weather_checks_v2']/'real_path_readiness.json')['probes']]
    summary=dict(synthetic_scenes=len(cases),synthetic_frequency_cases=len(results),plan_cases=len(plans),
        branch_states=dict(Counter(r['result'][s]['status'] for r in results for s in ('homogeneous','favourable'))),
        branch_withheld_reasons=dict(Counter(r['result'][s]['reason'] for r in results for s in ('homogeneous','favourable') if r['result'][s]['status']=='withheld')),
        public_frequency_cases=len(benchmarks),public_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_public_db_difference=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons']),
        real_paths_withheld=len(readiness))
    output.mkdir(parents=True)
    protocol=dict(schema_version=1,method=METHOD,bands_hz=list(BANDS),synthetic_scenes=cases,plan_cases=plan_cases(),
        reference_scenes=public,reference_air=AIR,reference_probability=.5,
        reference_tolerance_db=.02,original_plan_hit_tolerance_m=.002,
        limits='Exactly vertical wall, supplied incident-view eligibility, known single reflection and no selected diffraction interaction. No inclined/lateral/multiple/rail-body/line-source/real-area level.',
        alpha_one_policy='Known zero reflected energy extension; Annex equation alpha<1 unchanged.',
        reference_policy='Original cut xy/ground unchanged; cumulative physical-leg chainage. Exact mirror hit compared separately within2mm; eligibility dimensions assumed explicitly, not sourced facade observations.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    for name,data in [('synthetic_results.json',dict(results=results)),('plan_results.json',dict(results=plans)),
                      ('reference_results.json',dict(benchmarks=benchmarks)),('real_path_readiness.json',dict(probes=readiness))]:
        (output/name).write_bytes(json_bytes(data))
    names=['scripts/75_acquire_reflection_sources.py','scripts/76_run_reflection_checks.py','scripts/73_run_weather_checks.py','scripts/70_run_sightline_checks.py',
        'src/quiet_uk/reflection_path.py','src/quiet_uk/favourable_propagation.py','src/quiet_uk/sightline_boundary.py',
        'src/quiet_uk/multiple_edge_boundary.py','src/quiet_uk/terrain_boundary.py','src/quiet_uk/terrain_profile.py','src/quiet_uk/propagation_canary.py',
        'src/quiet_uk/explorer.py','scripts/57_acquire_ground_cover_sources.py']
    report=dict(status='bounded_single_wall_reflection_checks',method=METHOD,summary=summary,parent_inventory_sha256=hashes,
        code_sha256={n:file_hash(ROOT/n) for n in names},eligible_for_absolute_noise_release=False,full_current_method_qualified=False,
        external_engine_executed=False,real_area_noise_validated=False)
    (output/'report.json').write_bytes(json_bytes(report))
    for n in names:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    for n,p in parents.items(): helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(dict(generation_id=helper.seal(output,'reflection-checks-'),summary=summary),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',default=str(ROOT/'artifacts/reflection_sources_v1'))
    p.add_argument('--output',default=str(ROOT/'artifacts/reflection_checks_v3'))
    a=p.parse_args();run(a.sources,a.output)
