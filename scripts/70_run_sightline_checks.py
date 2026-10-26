"""Freeze clear, blocked and fixed-image-chain homogeneous research checks."""
import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import platform
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.sightline_boundary import METHOD,sightline_boundary
spec = importlib.util.spec_from_file_location('prior_cases',ROOT/'scripts/67_run_multiple_edge_checks.py')
prior = importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
helper,read,public_array = prior.helper,prior.read,prior.public_array
TESTS = ('TC05','TC06','TC07','TC10','TC11','TC12','TC13','TC14','TC15','TC16','TC21','TC23')
FIELDS = {'main_diffraction_uncapped_db':'expectedDeltaDiffSR','source_ground_db':'expectedAGroundSO',
    'receiver_ground_db':'expectedAGroundOR','source_image_diffraction_db':'expectedDeltaDiffSPrimeR',
    'receiver_image_diffraction_db':'expectedDeltaDiffSRPrime','source_ground_coupling_db':'expectedDeltaGroundSO',
    'receiver_ground_coupling_db':'expectedDeltaGroundOR'}


def fixture(sources,test):
    raw = read(sources/(test+'_Direct.json'))['cutPoints'];origin = raw[0]['coordinate']
    xs = [math.hypot(c['coordinate']['x']-origin['x'],c['coordinate']['y']-origin['y']) for c in raw]
    points = []
    for x,c in zip(xs,raw):
        if points and x==points[-1][0]: assert points[-1][1]==c['zGround']
        else: points.append([x,c['zGround']])
    ground = [[a,b,c['groundCoefficient']] for a,b,c in zip(xs,xs[1:],raw) if b>a]
    obstacles,enter = [],None
    for x,c in zip(xs,raw):
        if c['type']!='Wall': continue
        kind = c.get('intersectionType')
        if kind=='BUILDING_ENTER':
            assert enter is None;enter = [x,c['coordinate']['z']]
        elif kind=='BUILDING_EXIT':
            assert enter is not None
            obstacles.append(dict(vertices=[enter,[x,c['coordinate']['z']]],roof_g=0.));enter = None
        else:
            assert enter is None
            obstacles.append(dict(vertices=[[x,c['coordinate']['z']]]))
    assert enter is None
    return dict(case_id=test,points=points,source=[0.,origin['z']],receiver=[xs[-1],raw[-1]['coordinate']['z']],
        ground_segments=ground,source_g=raw[0]['groundCoefficient'],obstacles=obstacles,opaque=True,infinite_lateral_extent=True,
        scene_kind='published_reference',scope='Supplied DIRECT homogeneous boundary only. Opaque lateral extrusion and roof G=0 are explicit calculation assumptions. No full-scene levels or lateral/reflection paths.')


def synthetic_cases():
    cases = json.loads(json.dumps(prior.synthetic_cases()))
    for c in cases:
        for o in c['obstacles'] or []:
            if o['vertices'] is not None and len(o['vertices'])>1: o['roof_g'] = 0.
    def case(name,**kwargs):
        c = dict(case_id=name,points=[(0,0),(100,0)],source=(0,1),receiver=(100,4),source_g=0.,
            obstacles=[dict(vertices=[(30,1.6),(70,1.6)],roof_g=0.)],opaque=True,infinite_lateral_extent=True,scene_kind='synthetic')
        c.update(kwargs);return c
    cases.extend([
        case('near-clear-roof'),
        case('clear-two-screens',obstacles=[dict(vertices=[(30,1.6)]),dict(vertices=[(70,1.6)])]),
        case('clear-roof-redundant',obstacles=[dict(vertices=[(30,1.6),(50,1.6),(70,1.6)],roof_g=0.)]),
        case('clear-roof-ground-one',obstacles=[dict(vertices=[(30,1.6),(70,1.6)],roof_g=1.)]),
        case('tangent-two-roof-edges',source=(0,1),receiver=(100,1),obstacles=[dict(vertices=[(30,1),(70,1)],roof_g=0.)]),
        case('tangent-one-screen',source=(0,1),receiver=(100,1),obstacles=[dict(vertices=[(50,1)])]),
        case('clear-planar-sampling',points=[(0,0),(20,0),(40,0),(60,0),(100,0)],obstacles=[]),
        case('clear-valley',points=[(0,0),(50,-10),(100,0)],obstacles=[]),
        case('clear-valley-sampling',points=[(0,0),(25,-5),(50,-10),(75,-5),(100,0)],obstacles=[]),
        case('receiver-image-before-roof-edge',points=[(0,0),(20,0)],source=(0,1),receiver=(20,23),
             obstacles=[dict(vertices=[(5,10),(11,10)],roof_g=0.)]),
        case('unknown-roof-ground',obstacles=[dict(vertices=[(30,1.6),(70,1.6)],roof_g=None)]),
        case('unknown-lateral-property',infinite_lateral_extent=None),
    ])
    return cases


def evaluate(scene,f,ground=None):
    config = {k:v for k,v in scene.items() if k not in ('case_id','scope','unknown_ground')}
    if 'ground_segments' not in config:
        config['ground_segments'] = [(scene['points'][0][0],scene['points'][-1][0],ground)]
    return sightline_boundary(frequency_hz=f,**config)


def run(sources,output):
    sources,output = Path(sources).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    parents = {n:ROOT/'artifacts'/n for n in ('multiple_edge_checks_v1','multiple_edge_review_v1','terrain_boundary_checks_v1',
               'terrain_profile_checks_v2','ground_cover_inputs_v3','ground_cover_review_v3')}
    parents[sources.name] = sources
    for p in parents.values(): helper.verified(p)
    hashes = {n:file_hash(p/'inventory.json') for n,p in parents.items()}
    cases = synthetic_cases();results = []
    for c in cases:
        for g in ([None] if c.get('unknown_ground') or 'ground_segments' in c else [0.,.3,.7,1.]):
            for f in BANDS: results.append(dict(case_id=c['case_id'],ground_case_g=g,frequency_hz=f,**evaluate(c,f,g)))
    original = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    fixtures = [fixture(sources,test) for test in TESTS];benchmarks = []
    for c in fixtures:
        test = c['case_id'];boundary = public_array(original,test,'expectedABoundaryH')
        tolerance = .05 if test in ('TC12','TC14') else .02
        for index,f in enumerate(BANDS):
            result = evaluate(c,f);assert result['status']=='conditional_research',(test,f,result)
            fields = {'boundary_db':'expectedABoundaryH'}
            if result['branch']=='diffraction_including_ground':
                fields.update({key:name+('' if test=='TC06' else 'H') for key,name in FIELDS.items()})
            comparisons = []
            for field,variable in fields.items():
                expected = boundary[index] if field=='boundary_db' else public_array(original,test,variable)[index]
                error = result[field]-expected
                assert abs(error)<=tolerance,(test,f,field,result[field],expected)
                comparisons.append(dict(field=field,original_variable=variable,expected=expected,actual=result[field],error_db=error,tolerance_db=tolerance))
            benchmarks.append(dict(case_id=test,frequency_hz=f,result=result,comparisons=comparisons,
                source_commit=read(sources/'manifest.json')['commit'],external_engine_executed=False))
    native = read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']
    readiness = [dict(probe_id=p['probe_id'],status='withheld',boundary_db=None,noise_prediction=None,
        qualified_source_elevation_odn_m=p['qualified_source_elevation_odn_m'],qualified_ground_g=p['qualified_ground_g'],
        qualified_obstacle_cross_section=None,reason='Unknown qualified source, ground and complete historical acoustic obstacle/roof geometry.') for p in native]
    summary = dict(synthetic_scenes=len(cases),synthetic_results=len(results),states=dict(Counter(r['status'] for r in results)),
        branches=dict(Counter(r['branch'] for r in results if r['status']=='conditional_research')),
        withheld_reasons=dict(Counter(r['reason'] for r in results if r['status']=='withheld')),
        reference_frequency_cases=len(benchmarks),reference_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_reference_error_db=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons']),real_paths_withheld=len(readiness))
    output.mkdir(parents=True)
    protocol = dict(schema_version=1,method=METHOD,bands_hz=list(BANDS),ground_cases=[0.,.3,.7,1.],synthetic_scenes=cases,
        reference_scenes=fixtures,comparison_policy='TC12/14 0.05 dB, remaining cases 0.02 dB. Original expected values unchanged. TC11/14 positive below-plane edge-height table entries excluded; Annex clipping retained.',
        limits='Homogeneous DIRECT cross section only; explicit opacity, lateral extrusion, roof G and complete scene required. No annual noise, lateral, weather or reflection paths.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    for name,data in [('synthetic_results.json',dict(results=results)),('reference_results.json',dict(benchmarks=benchmarks)),('real_path_readiness.json',dict(probes=readiness))]:
        (output/name).write_bytes(json_bytes(data))
    names = ['scripts/57_acquire_ground_cover_sources.py','scripts/67_run_multiple_edge_checks.py','scripts/69_acquire_sightline_sources.py',
             'scripts/70_run_sightline_checks.py','src/quiet_uk/sightline_boundary.py','src/quiet_uk/multiple_edge_boundary.py',
             'src/quiet_uk/terrain_boundary.py','src/quiet_uk/terrain_profile.py','src/quiet_uk/propagation_canary.py','src/quiet_uk/explorer.py']
    report = dict(schema_version=1,status='bounded_homogeneous_sightline_checks',method=METHOD,summary=summary,parent_inventory_sha256=hashes,
        external_engine_executed=False,ISO_certification=False,full_current_method_qualified=False,real_area_noise_validated=False,
        eligible_for_absolute_noise_release=False,code_sha256={n:file_hash(ROOT/n) for n in names},
        credits='EU Annex II / original 2021/1226; NoiseModelling originals under GPLv3. Earlier physical-source credits retained.',
        runtime=dict(python=sys.version,platform=platform.platform()))
    (output/'report.json').write_bytes(json_bytes(report))
    for n in names:
        dest = output/'code'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dest)
    for n,p in parents.items():
        helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output,'sightline-checks-'),summary=summary)),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',default=str(ROOT/'artifacts/sightline_sources_v1'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/sightline_checks_v1'))
    args = parser.parse_args();run(args.sources,args.output)
