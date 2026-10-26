"""Freeze blocked multiple-edge research cases and original public comparisons."""
import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import platform
import re
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.multiple_edge_boundary import METHOD, multiple_edge_boundary
spec = importlib.util.spec_from_file_location('sources', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


def read(path):
    return json.loads(Path(path).read_bytes())


def public_array(text, test, name):
    block = text.split('public void '+test+'()', 1)[1].split('public void ', 1)[0]
    match = re.search(r'\bdouble\[\]\s+'+name+r'\s*=\s*new double\[\]\s*\{([^}]+)\}', block)
    assert match, (test, name)
    return [float(v) for v in match.group(1).split(',')]


def fixture(sources, test):
    raw = read(sources/(test+'_Direct.json'))['cutPoints']
    origin = raw[0]['coordinate']
    xs = [math.hypot(c['coordinate']['x']-origin['x'], c['coordinate']['y']-origin['y']) for c in raw]
    # Coincident GroundEffect/Source or GroundEffect/Receiver records have zero
    # support. Retain their outgoing G semantics; no positive interval is lost.
    points = []
    for x, record in zip(xs, raw):
        if points and x == points[-1][0]:
            assert points[-1][1] == record['zGround']
        else:
            points.append([x, record['zGround']])
    ground = [[a, b, c['groundCoefficient']] for a, b, c in zip(xs, xs[1:], raw) if b > a]
    obstacles = []; entering = None
    for x, c in zip(xs, raw):
        if c['type'] != 'Wall': continue
        if c['intersectionType'] == 'BUILDING_ENTER':
            assert entering is None
            entering = [x, c['coordinate']['z']]
        else:
            assert c['intersectionType'] == 'BUILDING_EXIT' and entering is not None
            obstacles.append(dict(vertices=[entering, [x, c['coordinate']['z']]])); entering = None
    assert entering is None
    return dict(case_id=test, points=points, source=[0., origin['z']], receiver=[xs[-1], raw[-1]['coordinate']['z']],
        obstacles=obstacles, ground_segments=ground, source_g=raw[0]['groundCoefficient'], scene_kind='published_reference',
        opaque=True, infinite_lateral_extent=True,
        scope='Original DIRECT homogeneous horizontal boundary term only. Lateral extent is extruded for this term; full-scene paths, reflection, transmission, atmosphere and levels excluded.')


def synthetic_cases():
    def case(name, points=((0, 0), (100, 0)), source=(0, 1), receiver=(100, 4),
             obstacles=({'vertices':[(30, 10), (70, 10)]},), **kwargs):
        result = dict(case_id=name, points=points, source=source, receiver=receiver, obstacles=obstacles,
                      source_g=0., opaque=True, infinite_lateral_extent=True, scene_kind='synthetic')
        result.update(kwargs); return result
    return [
        case('flat-empty', obstacles=[]), case('thick-roof'),
        case('inclined-roof', obstacles=[dict(vertices=[(30, 10), (70, 15)])]),
        case('roof-crest', obstacles=[dict(vertices=[(30, 10), (50, 20), (70, 10)])]),
        case('redundant-roof-vertex', obstacles=[dict(vertices=[(30, 10), (50, 10), (70, 10)])]),
        case('two-thin-screens', obstacles=[dict(vertices=[(30, 10)]), dict(vertices=[(70, 10)])]),
        case('one-thin-screen', obstacles=[dict(vertices=[(50, 10)])]),
        case('shadowed-screen', obstacles=[dict(vertices=[(30, 10)]), dict(vertices=[(50, 2)]), dict(vertices=[(70, 10)])]),
        case('two-ridges', points=[(0, 0), (30, 10), (50, 0), (70, 10), (100, 0)], obstacles=[]),
        case('three-ridges', points=[(0, 0), (20, 10), (50, 15), (80, 10), (100, 0)], obstacles=[]),
        case('sloping-thick-roof', points=[(0, 20), (100, 40)], source=(0, 21), receiver=(100, 44),
             obstacles=[dict(vertices=[(30, 40), (70, 45)])]),
        case('short-e-at-threshold', obstacles=[dict(vertices=[(50, 10), (50.3, 10)])]),
        case('short-e-over-threshold', obstacles=[dict(vertices=[(50, 10), (50.31, 10)])]),
        case('high-roof-cap', obstacles=[dict(vertices=[(30, 100), (70, 100)])]),
        case('mixed-ground', ground_segments=[(0, 30, .9), (30, 70, 0), (70, 100, .2)], source_g=.9),
        case('source-below-plane', points=[(0, 0), (10, 10), (200, 10)], source=(0, 1), receiver=(200, 14),
             obstacles=[dict(vertices=[(150, 400), (180, 400)])]),
        case('receiver-below-plane', points=[(0, 10), (190, 10), (200, 0)], source=(0, 14), receiver=(200, 1),
             obstacles=[dict(vertices=[(20, 400), (50, 400)])]),
        case('both-below-plane', points=[(0, 0), (10, 10), (190, 10), (200, 0)], source=(0, 1), receiver=(200, 1),
             obstacles=[dict(vertices=[(70, 400), (130, 400)])]),
        case('image-changes-chain', obstacles=[dict(vertices=[(10, 5)]), dict(vertices=[(20, 10)]), dict(vertices=[(70, 10)])]),
        case('clear-thick-roof', source=(0, 20), receiver=(100, 20)),
        case('clear-terrain-multiple', points=[(0, 0), (30, 1), (70, 1), (100, 0)], source=(0, 20), receiver=(100, 20), obstacles=[]),
        case('unknown-terrain', points=[(0, 0), (50, None), (100, 0)]),
        case('unknown-endpoint', receiver=None), case('unknown-ground', unknown_ground=True),
        case('unknown-source-ground', source_g=None), case('unknown-obstacles', obstacles=None),
        case('unknown-roof-height', obstacles=[dict(vertices=[(30, 10), (70, None)])]),
        case('unknown-width', obstacles=[dict(vertices=None)]), case('unknown-opacity', opaque=None),
        case('finite-lateral-extent', infinite_lateral_extent=False), case('transmission', opaque=False),
        case('favourable-weather', conditions='favourable'), case('unqualified-real', scene_kind='real_area'),
    ]


def evaluate(scene, frequency, ground=None):
    config = {k:v for k, v in scene.items() if k not in ('case_id', 'scope', 'unknown_ground')}
    if 'ground_segments' not in config:
        config['ground_segments'] = [(scene['points'][0][0], scene['points'][-1][0], ground)]
    return multiple_edge_boundary(frequency_hz=frequency, **config)


def run(sources, output):
    sources, output = Path(sources).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents = {n:ROOT/'artifacts'/n for n in ('terrain_boundary_checks_v1', 'terrain_boundary_review_v1', 'terrain_profile_checks_v2', 'ground_cover_inputs_v3', 'ground_cover_review_v3')}
    parents[sources.name] = sources
    for p in parents.values(): helper.verified(p)
    hashes = {n:file_hash(p/'inventory.json') for n, p in parents.items()}
    cases = synthetic_cases(); results = []
    for c in cases:
        # A specified heterogeneous-ground case is evaluated once per band.
        gs = [None] if c.get('unknown_ground') or 'ground_segments' in c else [0., .3, .7, 1.]
        for g in gs:
            for f in BANDS:
                results.append(dict(case_id=c['case_id'], ground_case_g=g, frequency_hz=f, **evaluate(c, f, g)))
    originals = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    fixtures = [fixture(sources, test) for test in ('TC10', 'TC12', 'TC15')]
    fields = {'boundary_db':'expectedABoundaryH', 'main_diffraction_uncapped_db':'expectedDeltaDiffSRH',
        'source_ground_db':'expectedAGroundSOH', 'receiver_ground_db':'expectedAGroundORH',
        'source_image_diffraction_db':'expectedDeltaDiffSPrimeRH', 'receiver_image_diffraction_db':'expectedDeltaDiffSRPrimeH',
        'source_ground_coupling_db':'expectedDeltaGroundSOH', 'receiver_ground_coupling_db':'expectedDeltaGroundORH'}
    benchmarks = []
    for c in fixtures:
        test = c['case_id']; arrays = {k:public_array(originals, test, v) for k, v in fields.items()}
        tolerance = .05 if test == 'TC12' else .02
        for index, f in enumerate(BANDS):
            result = evaluate(c, f); assert result['status'] == 'conditional_research', (test, f, result)
            comparisons = []
            for field, expected in arrays.items():
                error = result[field]-expected[index]
                assert abs(error) <= tolerance, (test, f, field, result[field], expected[index])
                comparisons.append(dict(field=field, original_variable=fields[field], expected=expected[index],
                                        actual=result[field], error_db=error, tolerance_db=tolerance))
            benchmarks.append(dict(case_id=test, frequency_hz=f, result=result, comparisons=comparisons,
                source_commit=read(sources/'manifest.json')['commit'], external_engine_executed=False))
    native = read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']
    readiness = [dict(probe_id=p['probe_id'], status='withheld', boundary_db=None, noise_prediction=None,
        qualified_source_elevation_odn_m=p['qualified_source_elevation_odn_m'], qualified_ground_g=p['qualified_ground_g'],
        qualified_obstacle_cross_section=None, reason='No qualified complete obstacle/roof/source/G cross section; native cell steps and provisional roofs do not qualify diffraction.') for p in native]
    summary = dict(synthetic_scenes=len(cases), synthetic_results=len(results), states=dict(Counter(r['status'] for r in results)),
        controlling_edge_counts=dict(Counter(str(r['controlling_edge_count']) for r in results if 'controlling_edge_count' in r)),
        withheld_reasons=dict(Counter(r['reason'] for r in results if r['status']=='withheld')),
        reference_frequency_cases=len(benchmarks), reference_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_reference_error_db=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons']), real_paths_withheld=len(readiness))
    output.mkdir(parents=True)
    protocol = dict(schema_version=1, method=METHOD, bands_hz=list(BANDS), ground_cases=[0., .3, .7, 1.],
        synthetic_scenes=cases, reference_scenes=fixtures,
        comparison_policy='TC10/15 0.02 dB; TC12 0.05 dB (original rounded tables include 6.55 m e versus exact 6.51057 m from world coordinates). No expected values changed.',
        limits='Blocked homogeneous direct horizontal boundary only; unchanged controlling image chains. Clear multiple/finite-width, lateral, weather, reflection, transmission and annual levels withheld.')
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    for name, data in [('synthetic_results.json', dict(results=results)), ('reference_results.json', dict(benchmarks=benchmarks)), ('real_path_readiness.json', dict(probes=readiness))]:
        (output/name).write_bytes(json_bytes(data))
    code_names = ['scripts/57_acquire_ground_cover_sources.py', 'scripts/66_acquire_multiple_edge_sources.py', 'scripts/67_run_multiple_edge_checks.py',
        'src/quiet_uk/multiple_edge_boundary.py', 'src/quiet_uk/terrain_boundary.py', 'src/quiet_uk/terrain_profile.py', 'src/quiet_uk/propagation_canary.py', 'src/quiet_uk/explorer.py']
    report = dict(schema_version=1, status='bounded_blocked_multiple_edge_research_checks', summary=summary, parent_inventory_sha256=hashes,
        method=METHOD, external_engine_executed=False, ISO_certification=False, regulatory_implementation_qualified=False,
        eligible_for_absolute_noise_release=False, real_area_noise_validated=False,
        credits='Original EU 2021/1226 PDF and prior Annex II; NoiseModelling reference inputs and expected arrays GPLv3, originals retained.',
        code_sha256={n:file_hash(ROOT/n) for n in code_names}, runtime=dict(python=sys.version, platform=platform.platform()))
    (output/'report.json').write_bytes(json_bytes(report))
    for name in code_names:
        dest = output/'code'/name; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/name, dest)
    for n, p in parents.items():
        helper.verified(p); assert file_hash(p/'inventory.json') == hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output, 'multiple-edge-checks-'), summary=summary)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default=str(ROOT/'artifacts/multiple_edge_sources_v1'))
    parser.add_argument('--output', default=str(ROOT/'artifacts/multiple_edge_checks_v1'))
    args = parser.parse_args(); run(args.sources, args.output)
