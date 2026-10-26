"""Freeze single-edge homogeneous boundary cases and public benchmarks."""
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
from quiet_uk.terrain_boundary import METHOD, single_edge_boundary

spec = importlib.util.spec_from_file_location('sources', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


def read(path):
    return json.loads(Path(path).read_bytes())


def public_array(text, test, name):
    block = text.split('public void '+test+'()', 1)[1].split('public void ', 1)[0]
    match = re.search(r'\b'+name+r'\s*=\s*new double\[\]\s*\{([^}]+)\}', block)
    assert match, (test, name)
    return [float(v.strip()) for v in match.group(1).split(',')]


def fixture(sources, test):
    raw = read(sources/(test+'_Direct.json'))['cutPoints']
    origin = raw[0]['coordinate']
    xs = [math.hypot(c['coordinate']['x']-origin['x'], c['coordinate']['y']-origin['y']) for c in raw]
    points = [[x, c['zGround']] for x, c in zip(xs, raw)]
    ground = [[a, b, c['groundCoefficient']] for a, b, c in zip(xs, xs[1:], raw)]
    index = next(i for i, c in enumerate(raw) if c['type']=='Wall') if test=='TC07' else next(i for i, c in enumerate(raw) if c['zGround']==10)
    return dict(case_id=test, points=points, source=[0., origin['z']], receiver=[xs[-1], raw[-1]['coordinate']['z']],
        edge=[xs[index], raw[index]['coordinate']['z']], ground_segments=ground, source_g=raw[0]['groundCoefficient'],
        scene_kind='published_reference', edge_width_m=0., opaque=True, infinite_lateral_extent=True,
        scope='Supplied homogeneous direct horizontal-edge boundary term only. The original TC07 wall has finite geometry; full scene/lateral paths, atmosphere and levels are excluded.')


def synthetic_cases():
    def case(name, points=((0, 0), (100, 0)), source=(0, 1), receiver=(100, 4), edge=(50, 6), **kwargs):
        result = dict(case_id=name, points=points, source=source, receiver=receiver, edge=edge,
            source_g=0., scene_kind='synthetic', edge_width_m=0., opaque=True, infinite_lateral_extent=True)
        result.update(kwargs); return result
    return [
        case('flat-no-edge', edge=None), case('flat-screen-six-metres'), case('low-clear-screen', edge=(50, 1)),
        case('symmetric-ridge', points=[(0, 0), (50, 10), (100, 0)], edge=(50, 10)),
        case('offcentre-ridge', points=[(0, 0), (20, 0), (40, 8), (70, 0), (100, 0)], edge=(40, 8)),
        case('narrow-ridge', points=[(0, 0), (48, 0), (50, 10), (52, 0), (100, 0)], edge=(50, 10)),
        case('sloping-ridge', points=[(0, 20), (50, 35), (100, 40)], source=(0, 21), receiver=(100, 44), edge=(50, 35)),
        case('sloping-screen', points=[(0, 20), (100, 40)], source=(0, 21), receiver=(100, 44), edge=(50, 36)),
        case('clear-terrain-crest', points=[(0, 0), (80, 5), (100, 5)], source=(0, 1), receiver=(100, 6.5), edge=(80, 5)),
        case('tangent-edge', source=(0, 1), receiver=(100, 1), edge=(50, 1)),
        case('main-cap-probe', edge=(50, 100)),
        case('source-below-mean', points=[(0, 0), (10, 10), (200, 10)], source=(0, 1), receiver=(200, 14), edge=(170, 400)),
        case('receiver-below-mean', points=[(0, 10), (190, 10), (200, 0)], source=(0, 14), receiver=(200, 1), edge=(30, 400)),
        case('both-below-mean', points=[(0, 0), (10, 10), (190, 10), (200, 0)], source=(0, 1), receiver=(200, 1), edge=(100, 400)),
        case('unknown-terrain', points=[(0, 0), (50, None), (100, 0)]),
        case('unknown-source', source=None), case('unknown-ground', unknown_ground=True),
        case('unknown-source-ground', source_g=None), case('unknown-edge-height', edge=(50, None)),
        case('unknown-edge-width', edge_width_m=None), case('unknown-opacity', opaque=None),
        case('finite-width', edge_width_m=2), case('finite-lateral-extent', infinite_lateral_extent=False),
        case('transmissive-edge', opaque=False), case('favourable-weather', conditions='favourable'),
        case('unqualified-real-scene', scene_kind='real_area'),
        case('two-controlling-ridges', points=[(0, 0), (25, 10), (50, 1), (75, 10), (100, 0)], edge=(25, 10)),
        case('clear-valley-no-qualified-edge', points=[(0, 0), (50, -10), (100, 0)], edge=None),
        case('wrong-clear-edge', points=[(0, 0), (20, 3), (50, 0), (100, 0)], source=(0, 10), receiver=(100, 10), edge=(50, 1)),
    ]


def evaluate(scene, frequency, ground=None):
    config = {k: v for k, v in scene.items() if k not in ('case_id', 'scope', 'unknown_ground')}
    if ground is not None or 'ground_segments' not in config:
        config['ground_segments'] = [(scene['points'][0][0], scene['points'][-1][0], ground)]
    return single_edge_boundary(frequency_hz=frequency, **config)


def run(sources, output):
    sources, output = Path(sources).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents = {n: ROOT/'artifacts'/n for n in ('terrain_profile_checks_v2', 'terrain_profile_review_v1',
        'ground_barrier_audit_v1', 'ground_cover_inputs_v3', 'ground_cover_review_v3')}
    parents[sources.name] = sources
    for p in parents.values(): helper.verified(p)
    hashes = {n: file_hash(p/'inventory.json') for n, p in parents.items()}
    cases = synthetic_cases()
    results = []
    for c in cases:
        for g in ([None] if c.get('unknown_ground') else [0., .3, .7, 1.]):
            for f in BANDS:
                results.append(dict(case_id=c['case_id'], ground_case_g=g, frequency_hz=f, **evaluate(c, f, g)))
    originals = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    fixtures = [fixture(sources, n) for n in ('TC05', 'TC06', 'TC07')]
    benchmarks = []
    fields = {'main_diffraction_uncapped_db':'expectedDeltaDiffSR', 'source_ground_db':'expectedAGroundSO',
        'receiver_ground_db':'expectedAGroundOR', 'source_image_diffraction_db':'expectedDeltaDiffSPrimeR',
        'receiver_image_diffraction_db':'expectedDeltaDiffSRPrime', 'source_ground_coupling_db':'expectedDeltaGroundSO',
        'receiver_ground_coupling_db':'expectedDeltaGroundOR'}
    for c in fixtures:
        test = c['case_id']
        boundary = public_array(originals, test, 'expectedABoundaryH')
        comparisons = {'boundary_db': dict(expected=boundary, original_variable='expectedABoundaryH', tolerance_db=.02 if test=='TC05' else .1)}
        if test!='TC05':
            for field, prefix in fields.items():
                variable = prefix+('H' if test=='TC07' else '')
                comparisons[field] = dict(expected=public_array(originals, test, variable), original_variable=variable,
                    tolerance_db=.1 if field in ('source_ground_db', 'source_ground_coupling_db', 'receiver_ground_coupling_db') else .02)
        for index, f in enumerate(BANDS):
            result = evaluate(c, f)
            assert result['status']=='conditional_research', (test, f, result)
            checked = []
            for field, specification in comparisons.items():
                if field!='boundary_db' and result['branch']!='diffraction_including_ground':
                    continue
                expected = specification['expected'][index]
                error = result[field]-expected
                assert abs(error)<=specification['tolerance_db'], (test, f, field, result[field], expected)
                checked.append(dict(field=field, expected=expected, actual=result[field], error_db=error,
                    tolerance_db=specification['tolerance_db'], original_variable=specification['original_variable']))
            benchmarks.append(dict(case_id=test, frequency_hz=f, result=result, comparisons=checked,
                external_engine_executed=False, source_commit=read(sources/'manifest.json')['commit']))
    # No source, absorption or screen qualification is inferred for real paths.
    native = read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']
    readiness = [dict(probe_id=p['probe_id'], status='withheld', boundary_db=None, noise_prediction=None,
        qualified_source_elevation_odn_m=p['qualified_source_elevation_odn_m'], qualified_ground_g=p['qualified_ground_g'],
        qualified_horizontal_edges=None, reason='Physical source, ground and edge properties unqualified; native cell steps are not qualified edges.') for p in native]
    summary = dict(synthetic_scenes=len(cases), synthetic_results=len(results), states=dict(Counter(r['status'] for r in results)),
        branches=dict(Counter(r['branch'] for r in results if r['status']=='conditional_research')),
        withheld_reasons=dict(Counter(r['reason'] for r in results if r['status']=='withheld')),
        reference_frequency_cases=len(benchmarks), reference_scalar_comparisons=sum(len(b['comparisons']) for b in benchmarks),
        maximum_reference_error_db=max(abs(c['error_db']) for b in benchmarks for c in b['comparisons']),
        real_paths_withheld=len(readiness), real_area_noise_predictions=False)
    output.mkdir(parents=True)
    (output/'protocol.json').write_bytes(json_bytes(dict(schema_version=1, method=METHOD, bands_hz=list(BANDS),
        synthetic_scenes=cases, ground_cases=[0., .3, .7, 1.], reference_scenes=fixtures,
        limits='Homogeneous direct boundary term only. Extruded opaque single edge or explicitly planar ground. No annual noise, favourable weather, finite/multiple edges, reflections or lateral paths.')))
    for name, data in [('synthetic_results.json', dict(results=results)), ('reference_results.json', dict(benchmarks=benchmarks)),
        ('real_path_readiness.json', dict(probes=readiness))]:
        (output/name).write_bytes(json_bytes(data))
    code_names = ['scripts/57_acquire_ground_cover_sources.py', 'scripts/63_acquire_diffraction_method_sources.py',
        'scripts/64_run_terrain_boundary_checks.py', 'src/quiet_uk/terrain_boundary.py', 'src/quiet_uk/terrain_profile.py',
        'src/quiet_uk/propagation_canary.py', 'src/quiet_uk/explorer.py']
    report = dict(schema_version=1, status='bounded_homogeneous_single_edge_research_checks', summary=summary,
        parent_inventory_sha256=hashes, method=METHOD, external_engine_executed=False, ISO_certification=False,
        regulatory_implementation_qualified=False, eligible_for_absolute_noise_release=False,
        method_documents='Retained original 2020 Annex II plus 2021 amendment. Fresh EUR-Lex PDF attempts returned access-check HTML and were excluded; receipts retained.',
        credits='EU Annex II and 2021/1226; NoiseModelling originals GPLv3 under their original licence. Prior native terrain retains Environment Agency OGL/OS credits.',
        code_sha256={n:file_hash(ROOT/n) for n in code_names}, runtime=dict(python=sys.version, platform=platform.platform()))
    (output/'report.json').write_bytes(json_bytes(report))
    for n in code_names:
        dest = output/'code'/n; dest.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(ROOT/n, dest)
    for n, p in parents.items():
        helper.verified(p); assert file_hash(p/'inventory.json')==hashes[n]
    assert all(file_hash(ROOT/n)==h for n, h in report['code_sha256'].items())
    print(json.dumps(dict(generation_id=helper.seal(output, 'terrain-boundary-checks-'), summary=summary)), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default=str(ROOT/'artifacts/diffraction_method_sources_v1'))
    parser.add_argument('--output', default=str(ROOT/'artifacts/terrain_boundary_checks_v1'))
    args = parser.parse_args(); run(args.sources, args.output)
