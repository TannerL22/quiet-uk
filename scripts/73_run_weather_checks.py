"""Freeze favourable DIRECT and explicit-air absorption research checks."""
import argparse
from collections import Counter
import importlib.util
import itertools
import json
from pathlib import Path
import platform
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.favourable_propagation import METHOD,EXACT_CENTRES,atmospheric_absorption,direct_attenuation,favourable_boundary
spec=importlib.util.spec_from_file_location('prior_builder',ROOT/'scripts/70_run_sightline_checks.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
helper,read,public_array=prior.helper,prior.read,prior.public_array
TESTS=('TC01','TC02','TC03','TC04',*prior.TESTS)
AIR=dict(temperature_c=10.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)


def scenes():
    cases=prior.synthetic_cases()
    def case(name,**kw):
        c=dict(case_id=name,points=[[0,0],[1000,0]],source=[0,1],receiver=[1000,4],source_g=0.,
               obstacles=[],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
        c.update(kw);return c
    cases.extend([
        case('curved-arc-clears-screen',obstacles=[dict(vertices=[[500,12.]])]),
        case('curved-envelope-removes-middle',obstacles=[dict(vertices=[[200,40.]]),dict(vertices=[[500,44.]]),dict(vertices=[[800,40.]])]),
        case('curved-envelope-retains-middle',obstacles=[dict(vertices=[[200,40.]]),dict(vertices=[[500,60.]]),dict(vertices=[[800,40.]])]),
        case('null-height-ground',source=[0,0],receiver=[1000,0]),
        case('extreme-screen-circle-domain',obstacles=[dict(vertices=[[500,20000.]])]),
        case('unsupported-weather',conditions='upward_refraction'),
    ])
    return cases


def configuration(c,g=None):
    config={k:v for k,v in c.items() if k not in ('case_id','scope','unknown_ground')}
    config.setdefault('ground_segments',[[c['points'][0][0],c['points'][-1][0],g]])
    return config


def reference_fields(test,result):
    fields={'boundary_db':'expectedABoundaryF'}
    # TC15 publishes only H intermediates; they are not favourable expectations.
    if result['branch']=='diffraction_including_ground' and test!='TC15':
        fields.update({field:name+'F' for field,name in prior.FIELDS.items()})
    return fields


def run(sources,output):
    sources,output=Path(sources).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    parents={n:ROOT/'artifacts'/n for n in ('sightline_checks_v1','sightline_review_v1','terrain_profile_checks_v2')}
    parents[sources.name]=sources
    for p in parents.values(): helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    cases=scenes();results=[]
    for c in cases:
        for g in ([None] if c.get('unknown_ground') or 'ground_segments' in c else [0.,.3,.7,1.]):
            for f in BANDS:
                result=favourable_boundary(frequency_hz=f,**configuration(c,g))
                results.append(dict(case_id=c['case_id'],ground_case_g=g,frequency_hz=f,**result))
    fixtures=[prior.fixture(sources,test) for test in TESTS]
    text=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8');benchmarks=[]
    for c in fixtures:
        test=c['case_id'];tolerance=.05 if test in ('TC12','TC14') else .03 if test=='TC13' else .02
        for i,f in enumerate(BANDS):
            combined=direct_attenuation(c,f,AIR,probability_favourable=.5,absorption_frequency_policy='exact_base10')
            assert combined['status']=='conditional_research',(test,f,combined)
            result=combined['favourable'];comparisons=[]
            for field,var in reference_fields(test,result).items():
                expected=public_array(text,test,var)[i];error=result[field]-expected
                assert abs(error)<=tolerance,(test,f,field,result[field],expected)
                comparisons.append(dict(component='favourable',field=field,original_variable=var,expected=expected,actual=result[field],error_db=error,tolerance=tolerance,unit='dB'))
            for component,field,var in [('absorption','alpha_db_per_km','expectedAlphaAtm'),('combined','atmospheric_attenuation_db','expectedAAtm'),('combined','divergence_db','expectedADiv')]:
                actual=combined['absorption'][field] if component=='absorption' else combined[field]
                expected=public_array(text,test,var)[i];error=actual-expected
                assert abs(error)<=.02,(test,f,field,actual,expected)
                comparisons.append(dict(component=component,field=field,original_variable=var,expected=expected,actual=actual,error_db=error,tolerance=.02,unit='dB/km' if field=='alpha_db_per_km' else 'dB'))
            benchmarks.append(dict(case_id=test,frequency_hz=f,result=combined,comparisons=comparisons,source_commit=read(sources/'manifest.json')['commit'],external_engine_executed=False))
    atmospheric=[]
    for t,rh,p,policy,f in itertools.product([-20.,0.,10.,20.,50.],[10.,50.,70.,100.],[80.,101.325,120.],['nominal','exact_base10'],BANDS):
        air=dict(temperature_c=t,relative_humidity_percent=rh,pressure_kpa=p,uniform_air=True)
        actual_f=EXACT_CENTRES[f] if policy=='exact_base10' else f
        atmospheric.append(dict(nominal_frequency_hz=f,policy=policy,air=air,result=atmospheric_absorption(actual_f,**air)))
    guards=[]
    for key in AIR:
        air=dict(AIR);air[key]=None
        guards.append(dict(case_id='unknown-'+key,air=air,result=direct_attenuation(fixtures[0],1000,air,probability_favourable=.5,absorption_frequency_policy='exact_base10')))
    for name,change in [('nonuniform',dict(uniform_air=False)),('temperature-domain',dict(temperature_c=51)),('humidity-domain',dict(relative_humidity_percent=9)),('pressure-domain',dict(pressure_kpa=121))]:
        air=dict(AIR,**change);guards.append(dict(case_id=name,air=air,result=direct_attenuation(fixtures[0],1000,air,probability_favourable=.5,absorption_frequency_policy='exact_base10')))
    guard_mix=[dict(probability=p,result=direct_attenuation(fixtures[0],1000,AIR,probability_favourable=p,absorption_frequency_policy='exact_base10')) for p in (None,0.,.25,.5,.75,1.)]
    readiness=[dict(p,status='withheld',qualified_absorption_air=None,qualified_directional_favourable_probability=None,noise_prediction=None)
               for p in read(parents['sightline_checks_v1']/'real_path_readiness.json')['probes']]
    summary=dict(synthetic_scenes=len(cases),synthetic_results=len(results),states=dict(Counter(r['status'] for r in results)),
        branches=dict(Counter(r['branch'] for r in results if r['status']=='conditional_research')),
        withheld_reasons=dict(Counter(r['reason'] for r in results if r['status']=='withheld')),
        reference_frequency_cases=len(benchmarks),reference_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_reference_db_error=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons'] if c['unit']=='dB'),
        maximum_reference_alpha_error_db_per_km=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons'] if c['unit']=='dB/km'),
        atmospheric_grid_cases=len(atmospheric),real_paths_withheld=len(readiness))
    output.mkdir(parents=True)
    protocol=dict(schema_version=1,method=METHOD,bands_hz=list(BANDS),exact_absorption_centres_hz=EXACT_CENTRES,ground_cases=[0.,.3,.7,1.],
        synthetic_scenes=cases,reference_scenes=fixtures,reference_air=AIR,reference_probability=.5,
        comparison_policy='0.02 dB / dB per km; boundary/intermediate TC12/14 0.05 dB, TC13 0.03 dB. TC15 F intermediate arrays absent, excluded. Original expected values unchanged.',
        limits='DIRECT cross sections only. Explicit air/frequency policy/probability. No reflection/lateral/line integration/annual real-area release.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    for name,data in [('synthetic_results.json',dict(results=results)),('reference_results.json',dict(benchmarks=benchmarks)),
        ('atmospheric_results.json',dict(results=atmospheric,guards=guards,meteorology_guards=guard_mix)),('real_path_readiness.json',dict(probes=readiness))]:
        (output/name).write_bytes(json_bytes(data))
    names=['scripts/72_acquire_weather_sources.py','scripts/73_run_weather_checks.py','scripts/70_run_sightline_checks.py',
           'scripts/67_run_multiple_edge_checks.py','scripts/57_acquire_ground_cover_sources.py','src/quiet_uk/favourable_propagation.py',
           'src/quiet_uk/sightline_boundary.py','src/quiet_uk/multiple_edge_boundary.py','src/quiet_uk/terrain_boundary.py',
           'src/quiet_uk/terrain_profile.py','src/quiet_uk/propagation_canary.py','src/quiet_uk/explorer.py']
    report=dict(schema_version=1,status='bounded_favourable_and_absorption_checks',method=METHOD,summary=summary,parent_inventory_sha256=hashes,
        code_sha256={n:file_hash(ROOT/n) for n in names},external_engine_executed=False,full_current_method_qualified=False,
        real_area_noise_validated=False,eligible_for_absolute_noise_release=False,runtime=dict(python=sys.version,platform=platform.platform()))
    (output/'report.json').write_bytes(json_bytes(report))
    for n in names:
        dest=output/'code'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dest)
    for n,p in parents.items(): helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output,'weather-checks-'),summary=summary)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources',default=str(ROOT/'artifacts/weather_sources_v1'))
    p.add_argument('--output',default=str(ROOT/'artifacts/weather_checks_v2'))
    a=p.parse_args();run(a.sources,a.output)
