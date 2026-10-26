"""Independent quadrature/GEOS verification; no evaluated module imports.

Reuses only the previously sealed independent geometry and expanded ground
references. Independently reconstructs world-coordinate fixtures and extracts
the first DIRECT expected arrays, before any later lateral reassignment.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prior_reference', ROOT/'scripts/65_verify_terrain_boundary_checks.py')
prior = importlib.util.module_from_spec(spec); spec.loader.exec_module(prior)
geometry = prior.geometry
file_hash, json_bytes, read, close = geometry.file_hash, geometry.json_bytes, geometry.read, geometry.close
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)


def geos_chain(candidates, s, r):
    return geometry.geos_upper_path([s]+candidates+[r], s, r)


def numerical_chain(s, r, edges):
    path = [list(s)]+edges+[list(r)]
    distances = np.linalg.norm(np.diff(np.array(path), axis=0), axis=1)
    direct = float(np.linalg.norm(np.array(r)-np.array(s)))
    internal = float(np.linalg.norm(np.diff(np.array(edges), axis=0), axis=1).sum()) if len(edges)>1 else 0.
    length = float(distances.sum())
    return dict(path=path, direct_distance_m=direct, path_distance_m=length, delta_m=max(0., length-direct), inter_edge_path_m=internal)


def independent(scene, f, ground=None):
    if scene.get('conditions', 'homogeneous') != 'homogeneous':
        return dict(reason='unsupported_propagation_conditions')
    if scene['scene_kind'] not in ('synthetic', 'published_reference'):
        return dict(reason='real_area_scene_not_qualified')
    points, s, r, obstacles = scene['points'], scene['source'], scene['receiver'], scene['obstacles']
    if any(z is None for _, z in points):
        return dict(reason='unknown_terrain_support')
    if s is None or r is None or s[1] is None or r[1] is None:
        return dict(reason='unknown_endpoint_elevation')
    ground_segments = scene.get('ground_segments', [(points[0][0], points[-1][0], ground)])
    source_g = scene['source_g']
    if any(g is None for _, _, g in ground_segments) or source_g is None:
        return dict(reason='unknown_acoustic_ground')
    if obstacles is None or scene['opaque'] is None or scene['infinite_lateral_extent'] is None:
        return dict(reason='unknown_obstacle_geometry_or_properties')
    if not scene['opaque'] or not scene['infinite_lateral_extent']:
        return dict(reason='unsupported_transmission_or_finite_lateral_extent')
    if any(o['vertices'] is None or any(z is None for _, z in o['vertices']) for o in obstacles):
        return dict(reason='unknown_obstacle_geometry_or_properties')
    g = prior.ground_mean(ground_segments, points[0][0], points[-1][0])
    pairs = points[1:-1]+[p for o in obstacles for p in o['vertices']]
    xs = sorted({p[0] for p in pairs})
    candidates = [[x, max(p[1] for p in pairs if p[0]==x)] for x in xs]
    edges = geos_chain(candidates, s, r)[1:-1]
    if not edges:
        plane = geometry.numerical_plane(geometry.polyline_segments(points), adaptive_check=True)
        if not obstacles and plane['rms_residual_m'] <= 1e-10:
            frame = geometry.independent_frame(plane, s, r)
            return dict(branch='ground_only_planar', common_plane=plane, common_frame=frame, g_path=g, source_g=source_g,
                        boundary_db=prior.reference_ground(f, frame, g, source_g))
        return dict(reason='clear_path_multiple_geometry_requires_selection')
    first, last = edges[0], edges[-1]
    # Independently split via NumPy interpolation; roofs never substitute for
    # the terrain supporting the two outer ground calculations.
    left = geometry.independent_split(points, first[0])[0]
    right = geometry.independent_split(points, last[0])[1]
    ps, pr = [geometry.numerical_plane(p, adaptive_check=True) for p in (left, right)]
    sf, rf = geometry.independent_frame(ps, s, first), geometry.independent_frame(pr, last, r)
    sm, rm = sf['source']['mirror_point'], rf['receiver']['mirror_point']
    sb, rb = sf['source']['signed_height_m']<0, rf['receiver']['signed_height_m']<0
    if candidates and not sm[0] < candidates[0][0] <= candidates[-1][0] < rm[0]:
        return dict(reason='unsupported_mirror_point_order')
    se, re = geos_chain(candidates, sm, r)[1:-1], geos_chain(candidates, s, rm)[1:-1]
    if (not sb and se!=edges) or (not rb and re!=edges):
        return dict(reason='mirror_changes_controlling_chain')
    main_geometry = numerical_chain(s, r, edges)
    source_geometry = main_geometry if sb else numerical_chain(sm, r, edges)
    receiver_geometry = main_geometry if rb else numerical_chain(s, rm, edges)
    wavelength = 340/f

    def coefficient(e):
        square = (5*wavelength/e)**2 if e>.3 else None
        return (1+square)/(1/3+square) if square is not None else 1.

    def pure(path):
        argument = 3+40*coefficient(path['inter_edge_path_m'])*path['delta_m']/wavelength
        return max(0., 10*math.log10(argument)) if argument>=1 else 0.

    main = pure(main_geometry)
    si, ri = main if sb else pure(source_geometry), main if rb else pure(receiver_geometry)
    gs = prior.ground_mean(ground_segments, points[0][0], first[0])
    gr = prior.ground_mean(ground_segments, last[0], points[-1][0])
    sg = prior.reference_ground(f, sf, gs, source_g)
    rg = prior.reference_ground(f, rf, gr, source_g, receiver_side=True)

    def coupling(ag, image, below):
        if below: return ag
        weight = 10**((main-image)/20)
        amplitude = (1-weight)+weight*10**(-ag/20)
        return -20*math.log10(amplitude) if amplitude>0 else None

    sc, rc = coupling(sg, si, sb), coupling(rg, ri, rb)
    if sc is None or rc is None:
        return dict(reason='nonpositive_ground_diffraction_coupling')
    height_sum = sf['source']['equivalent_height_m']+sf['receiver']['equivalent_height_m']
    ratio = min(1., sf['projected_distance_m']/(30*height_sum)) if height_sum else 1.
    return dict(branch='diffraction_including_ground', controlling_edges=edges, controlling_edge_count=len(edges),
        obstacle_widths_m=[o['vertices'][-1][0]-o['vertices'][0][0] for o in obstacles],
        main_geometry=main_geometry, source_image_geometry=source_geometry, receiver_image_geometry=receiver_geometry,
        multiple_diffraction_coefficient=coefficient(main_geometry['inter_edge_path_m']),
        source_plane=ps, receiver_plane=pr, source_frame=sf, receiver_frame=rf, source_mirror=sm, receiver_mirror=rm,
        g_path=g, source_g=source_g, source_side_g=gs, receiver_side_g=gr, source_side_corrected_g=ratio*gs+(1-ratio)*source_g,
        main_diffraction_uncapped_db=main, main_diffraction_db=min(main,25.), source_image_diffraction_db=si,
        receiver_image_diffraction_db=ri, source_ground_db=sg, receiver_ground_db=rg,
        source_ground_coupling_db=sc, receiver_ground_coupling_db=rc,
        source_below_mean_plane_rule=sb, receiver_below_mean_plane_rule=rb,
        separate_ground_db=0., boundary_db=min(main,25.)+sc+rc)


def compare(actual, expected, counts, errors):
    if 'reason' in expected:
        assert actual['status']=='withheld' and actual['reason']==expected['reason'], (actual, expected)
        assert actual['boundary_db'] is None and actual['annual_noise_prediction'] is None
        counts['withheld_results_checked'] += 1
        return
    assert actual['status']=='conditional_research' and actual['branch']==expected['branch']
    assert actual['annual_noise_prediction'] is None and not actual['regulatory_implementation_qualified']

    def nested(a, b):
        if isinstance(b, dict):
            for k, v in b.items(): nested(a[k], v)
        elif isinstance(b, (list, tuple)):
            assert len(a)==len(b)
            for x, y in zip(a, b): nested(x, y)
        elif isinstance(b, (str, bool)):
            assert a==b
        else:
            close(a, b)

    for key, value in expected.items():
        if key.endswith('_plane'):
            geometry.compare_plane(actual[key], value)
        elif key.endswith('_frame'):
            expected_frame = dict(value)
            for end in ('source', 'receiver'):
                nested(actual[key][end]['original'], value[end]['original'])
                expected_frame[end] = dict(value[end], original=actual[key][end]['original'])
            geometry.compare_frame(actual[key], expected_frame)
        else:
            nested(actual[key], value)
            if key.endswith('_db'): errors.append(abs(actual[key]-value))
    counts['numeric_results_checked'] += 1
    if expected['branch']=='diffraction_including_ground':
        counts['main_cap_cases_checked'] += int(expected['main_diffraction_uncapped_db']>25)
        counts['source_below_plane_cases_checked'] += int(expected['source_below_mean_plane_rule'])
        counts['receiver_below_plane_cases_checked'] += int(expected['receiver_below_mean_plane_rule'])
        counts['multi_edge_numeric_cases_checked'] += int(expected['controlling_edge_count']>1)


def reference_fixture(sources, scene):
    raw = read(sources/(scene['case_id']+'_Direct.json'))['cutPoints']
    origin, receiver = raw[0]['coordinate'], raw[-1]['coordinate']
    line = LineString([(origin['x'], origin['y']), (receiver['x'], receiver['y'])])
    xs = []
    for c in raw:
        p = Point(c['coordinate']['x'], c['coordinate']['y'])
        assert line.distance(p)<1e-9
        xs.append(line.project(p))
    support = {}; intervals = []; walls = []
    for i, (x, c) in enumerate(zip(xs, raw)):
        if x in support: assert support[x]==c['zGround']
        support[x] = c['zGround']
        if i+1<len(raw) and xs[i+1]>x:
            intervals.append([x, xs[i+1], c['groundCoefficient']])
        if c['type']=='Wall': walls.append((x, c))
    assert len(walls)%2==0
    obstacles = []
    for before, after in zip(walls[::2], walls[1::2]):
        assert before[1]['intersectionType']=='BUILDING_ENTER' and after[1]['intersectionType']=='BUILDING_EXIT'
        obstacles.append(dict(vertices=[[before[0], before[1]['coordinate']['z']], [after[0], after[1]['coordinate']['z']]]))
    points = [[x, z] for x, z in sorted(support.items())]
    reconstructed = dict(scene, points=points, ground_segments=intervals, obstacles=obstacles,
        source=[0., origin['z']], receiver=[line.length, receiver['z']], source_g=raw[0]['groundCoefficient'])
    assert len(scene['points'])==len(points) and len(scene['ground_segments'])==len(intervals)
    for a, b in zip(scene['points']+scene['ground_segments'], points+intervals):
        for x, y in zip(a, b): close(x, y)
    assert len(scene['obstacles'])==len(obstacles)
    for a, b in zip(scene['obstacles'], obstacles):
        for p, q in zip(a['vertices'], b['vertices']):
            for x, y in zip(p, q): close(x, y)
    for p, q in zip((scene['source'], scene['receiver']), (reconstructed['source'], reconstructed['receiver'])):
        for x, y in zip(p, q): close(x, y)
    assert scene['source_g']==reconstructed['source_g']
    return reconstructed


def figure(output, scenes, benchmarks):
    plt = geometry.plt
    plt.rcParams.update({'font.size':10, 'axes.spines.top':False, 'axes.spines.right':False})
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    for ax, test in zip(axes[0], ('TC10', 'TC15')):
        scene = scenes[test]
        for obstacle in scene['obstacles']:
            roof = obstacle['vertices']
            x, z = np.array(roof).T
            ax.fill_between(x, 0, z, color='#d9d5cf', edgecolor='#665d50')
        path = next(b['result']['main_geometry']['path'] for b in benchmarks if b['case_id']==test)
        ax.plot(*np.array(path).T, 'o-', color='#216c83', lw=2, label='Controlling horizontal path')
        ax.plot(*np.array([scene['source'], scene['receiver']]).T, '--', color='#9c3856', label='Blocked direct ray')
        ax.set_title(test+(': 10 m thick building' if test=='TC10' else ': four controlling edges, three crossed buildings'), loc='left')
        ax.set_xlabel('Horizontal chainage (m)'); ax.set_ylabel('Elevation (m)')
        ax.set_ylim(0, 15); ax.legend(fontsize=8, loc='upper right')
    fs = list(BANDS)
    ax = axes[1, 0]
    for test in scenes:
        records = [b for b in benchmarks if b['case_id']==test]
        values = [b['result']['boundary_db'] for b in records]
        ax.plot(fs, values, 'o-', label=test)
    ax.set_title('Published direct boundary terms reproduced', loc='left')
    ax.set_ylabel('Boundary attenuation (dB)'); ax.legend(fontsize=8, loc='lower right')
    ax = axes[1, 1]
    records = [b for b in benchmarks if b['case_id']=='TC10']
    for key, label in [('main_diffraction_uncapped_db', 'Pure main term, uncapped'), ('main_diffraction_db', 'Pure main term, capped'),
                       ('boundary_db', 'Boundary including ground')]:
        ax.plot(fs, [b['result'][key] for b in records], 'o-', label=label)
    ax.set_title('TC10: cap applied once; ground uses uncapped terms', loc='left')
    ax.set_ylabel('Attenuation (dB)'); ax.legend(fontsize=8, loc='upper left')
    for ax in axes[1]:
        ax.set_xscale('log'); ax.set_xticks(fs, [str(f) if f<1000 else str(f//1000)+'k' for f in fs])
        ax.set_xlabel('Nominal octave centre (Hz)')
    fig.text(.04, .025, 'Conditional blocked homogeneous boundary calculations; these are not measured or annual noise levels.\nEU Annex II / 2021 amendment; public NoiseModelling reference originals retained under GPLv3.', fontsize=10)
    fig.tight_layout(rect=[0, .085, 1, 1]); fig.savefig(output/'multiple_edge_checks.png', dpi=170); plt.close(fig)


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    own_sha = file_hash(Path(__file__))
    prior_sha, geometry_sha = [file_hash(ROOT/'scripts'/n) for n in ('65_verify_terrain_boundary_checks.py', '62_verify_terrain_profile_checks.py')]
    input_sha = geometry.check_generation(inputs)
    report, protocol = read(inputs/'report.json'), read(inputs/'protocol.json')
    parents = {n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n, p in parents.items())
    sources = next(p for p in parents.values() if (p/'TC10_Direct.json').exists())
    manifest = read(sources/'manifest.json')
    source_parents = {n:ROOT/'artifacts'/n for n in manifest['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n, p in source_parents.items())
    old_review = read(parents['terrain_boundary_review_v1']/'verification.json')
    assert old_review['code_sha256']['65_verify_terrain_boundary_checks.py']==prior_sha
    assert old_review['code_sha256']['62_verify_terrain_profile_checks.py']==geometry_sha
    assert (sources/'eu-2021-amendment.pdf').read_bytes().startswith(b'%PDF-')
    for n, h in report['code_sha256'].items():
        assert file_hash(ROOT/n)==file_hash(inputs/'code'/n)==h
    cases = {c['case_id']:c for c in protocol['synthetic_scenes']}
    assert len(cases)==33 and tuple(protocol['bands_hz'])==BANDS
    expected_keys = {(c['case_id'], g, f) for c in cases.values()
        for g in ([None] if c.get('unknown_ground') or 'ground_segments' in c else protocol['ground_cases']) for f in BANDS}
    results = read(inputs/'synthetic_results.json')['results']
    assert len(results)==len(expected_keys)==1008
    assert {(r['case_id'], r['ground_case_g'], r['frequency_hz']) for r in results}==expected_keys
    counts, errors = Counter(), []
    for r in results:
        compare(r, independent(cases[r['case_id']], r['frequency_hz'], r['ground_case_g']), counts, errors)
    benchmarks = read(inputs/'reference_results.json')['benchmarks']
    scenes = {c['case_id']:reference_fixture(sources, c) for c in protocol['reference_scenes']}
    assert set(scenes)=={'TC10','TC12','TC15'} and len(benchmarks)==24
    assert {(b['case_id'], b['frequency_hz']) for b in benchmarks}=={(test, f) for test in scenes for f in BANDS}
    fields = {'boundary_db':'expectedABoundaryH', 'main_diffraction_uncapped_db':'expectedDeltaDiffSRH',
        'source_ground_db':'expectedAGroundSOH', 'receiver_ground_db':'expectedAGroundORH',
        'source_image_diffraction_db':'expectedDeltaDiffSPrimeRH', 'receiver_image_diffraction_db':'expectedDeltaDiffSRPrimeH',
        'source_ground_coupling_db':'expectedDeltaGroundSOH', 'receiver_ground_coupling_db':'expectedDeltaGroundORH'}
    original = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    max_public_error = 0.; comparison_keys = set()
    for b in benchmarks:
        test, f = b['case_id'], b['frequency_hz']
        expected = independent(scenes[test], f)
        compare(b['result'], expected, counts, errors)
        assert not b['external_engine_executed'] and b['source_commit']==manifest['commit']
        assert len(b['comparisons'])==len(fields) and {c['field'] for c in b['comparisons']}==set(fields)
        for c in b['comparisons']:
            variable = fields[c['field']]
            reference = prior.original_array(original, test, variable)[BANDS.index(f)]
            tolerance = .05 if test=='TC12' else .02
            assert c['expected']==reference and c['original_variable']==variable and c['tolerance_db']==tolerance
            close(c['actual'], b['result'][c['field']]); close(c['error_db'], c['actual']-reference)
            assert abs(expected[c['field']]-reference)<=tolerance
            max_public_error = max(max_public_error, abs(c['error_db']))
            comparison_keys.add((test, f, c['field'])); counts['public_scalar_comparisons_checked'] += 1
    assert len(comparison_keys)==counts['public_scalar_comparisons_checked']==192
    assert {b['result']['controlling_edge_count'] for b in benchmarks if b['case_id']=='TC15'}=={4}
    readiness = read(inputs/'real_path_readiness.json')['probes']
    native = {p['probe_id']:p for p in read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']}
    assert len(readiness)==100 and {p['probe_id'] for p in readiness}==set(native)
    for p in readiness:
        assert p['status']=='withheld' and p['boundary_db'] is None and p['noise_prediction'] is None
        assert p['qualified_obstacle_cross_section'] is None
        for k in ('qualified_source_elevation_odn_m', 'qualified_ground_g'):
            assert p[k]==native[p['probe_id']][k] is None
        counts['real_paths_verified_withheld'] += 1
    summary = report['summary']
    assert summary['states']==dict(Counter(r['status'] for r in results))
    assert summary['withheld_reasons']==dict(Counter(r['reason'] for r in results if r['status']=='withheld'))
    assert summary['controlling_edge_counts']==dict(Counter(str(r['controlling_edge_count']) for r in results if 'controlling_edge_count' in r))
    assert summary['synthetic_scenes']==len(cases) and summary['synthetic_results']==len(results)
    assert summary['reference_frequency_cases']==24 and summary['reference_scalar_comparisons']==192 and summary['real_paths_withheld']==100
    close(summary['maximum_reference_error_db'], max_public_error)
    assert not report['eligible_for_absolute_noise_release'] and not report['external_engine_executed'] and not report['ISO_certification']
    output.mkdir(parents=True); figure(output, scenes, benchmarks)
    receipt = dict(schema_version=1, status='passed_independent_blocked_multiple_edge_checks', input_inventory_sha256=input_sha,
        parent_inventory_sha256=report['parent_inventory_sha256'], source_parent_inventory_sha256=manifest['parent_inventory_sha256'],
        counts=dict(counts), maximum_independent_db_difference=max(errors), maximum_public_error_db=max_public_error,
        public_comparison_tolerances_db=[.02,.05], methods=['GEOS upper convex envelope', 'NumPy path lengths and world-coordinate fixture projection',
            'Gauss/QUADPACK terrain fits and vector projections', 'Independently expanded ground equations and amplitude-sum coupling',
            'Original 2021 coefficient and original DIRECT expected-array extraction'],
        full_current_method_qualified=False, external_engine_executed=False, ISO_certification=False, real_area_noise_validated=False,
        code_sha256={Path(__file__).name:own_sha, '65_verify_terrain_boundary_checks.py':prior_sha, '62_verify_terrain_profile_checks.py':geometry_sha},
        runtime=dict(python=sys.version, numpy=np.__version__, scipy=geometry.scipy.__version__, shapely=geometry.shapely.__version__))
    (output/'verification.json').write_bytes(json_bytes(receipt))
    for script in (Path(__file__), ROOT/'scripts/65_verify_terrain_boundary_checks.py', ROOT/'scripts/62_verify_terrain_profile_checks.py'):
        shutil.copyfile(script, output/script.name)
    assert file_hash(Path(__file__))==own_sha and file_hash(ROOT/'scripts/65_verify_terrain_boundary_checks.py')==prior_sha
    assert file_hash(ROOT/'scripts/62_verify_terrain_profile_checks.py')==geometry_sha
    assert geometry.check_generation(inputs)==input_sha
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n, p in parents.items())
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n, p in source_parents.items())
    assert all(file_hash(ROOT/n)==h for n, h in report['code_sha256'].items())
    files = {p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity = 'multiple-edge-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity, files=files)))
    print(json.dumps(dict(generation_id=identity, counts=dict(counts), maximum_independent_db_difference=max(errors), maximum_public_error_db=max_public_error)), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', default=str(ROOT/'artifacts/multiple_edge_checks_v1'))
    parser.add_argument('--output', default=str(ROOT/'artifacts/multiple_edge_review_v1'))
    args = parser.parse_args(); run(args.inputs, args.output)
