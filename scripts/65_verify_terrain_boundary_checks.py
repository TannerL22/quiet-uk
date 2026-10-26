"""Independently verify the bounded single-edge homogeneous boundary subset.

Imports only the prior independent quadrature/GEOS verifier and byte utilities;
never imports evaluated propagation/terrain modules or the input builder.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys

import numpy as np
from shapely.geometry import LineString, Point

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('geometry_reference', ROOT/'scripts/62_verify_terrain_profile_checks.py')
geometry = importlib.util.module_from_spec(spec); spec.loader.exec_module(geometry)
file_hash, json_bytes, read, close = geometry.file_hash, geometry.json_bytes, geometry.read, geometry.close
BANDS = (63, 125, 250, 500, 1000, 2000, 4000, 8000)


def numerical_delta(source, receiver, edge):
    s, r, e = (np.array(p) for p in (source, receiver, edge))
    orientation = float(np.linalg.det(np.column_stack((r-s, e-s))))
    extra = max(0., float(np.linalg.norm(e-s)+np.linalg.norm(r-e)-np.linalg.norm(r-s)))
    return extra if orientation>=0 else -extra, orientation>0


def reference_ground(f, frame, g, source_g, *, receiver_side=False):
    if g==0:
        return -3.
    distance = frame['projected_distance_m']
    zs, zr = frame['source']['equivalent_height_m'], frame['receiver']['equivalent_height_m']
    fraction = min(1., distance/(30*(zs+zr))) if zs+zr else 1.
    gp = g if receiver_side else fraction*g+(1-fraction)*source_g
    k = 2*math.pi*f/340
    w = .0185*f**2.5*gp**2.6/(f**1.5*gp**2.6+1300*f**.75*gp**1.3+1160000)
    cf = distance*(1+3*w*distance*math.exp(-math.sqrt(w*distance)))/(1+w*distance)
    source = zs**2-math.sqrt(2*cf/k)*zs+cf/k
    receiver = zr**2-math.sqrt(2*cf/k)*zr+cf/k
    return max(-10*math.log10(4*k*k*source*receiver/distance**2), -3*(1-gp))


def ground_mean(segments, start, end):
    lengths = np.array([max(0., min(b, end)-max(a, start)) for a, b, _ in segments])
    factors = np.array([g for _, _, g in segments])
    return float(np.dot(lengths, factors)/(end-start))


def independent(scene, f, ground=None):
    if scene.get('conditions', 'homogeneous')!='homogeneous':
        return dict(reason='unsupported_propagation_conditions')
    if scene['scene_kind'] not in ('synthetic', 'published_reference'):
        return dict(reason='real_area_scene_not_qualified')
    points, s, r, edge = scene['points'], scene['source'], scene['receiver'], scene['edge']
    if any(z is None for _, z in points):
        return dict(reason='unknown_terrain_support')
    if s is None or r is None or s[1] is None or r[1] is None:
        return dict(reason='unknown_endpoint_elevation')
    if f not in BANDS:
        return dict(reason='unsupported_nominal_frequency')
    ground_segments = scene.get('ground_segments', [(points[0][0], points[-1][0], ground)])
    source_g = scene['source_g']
    if any(g is None for _, _, g in ground_segments) or source_g is None:
        return dict(reason='unknown_acoustic_ground')
    plane = geometry.numerical_plane(geometry.polyline_segments(points), adaptive_check=True)
    common = geometry.independent_frame(plane, s, r)
    g = ground_mean(ground_segments, points[0][0], points[-1][0])
    result = dict(common_plane=plane, common_frame=common, g_path=g, source_g=source_g)

    def no_diffraction(branch):
        return dict(**result, branch=branch, boundary_db=reference_ground(f, common, g, source_g))

    if edge is None:
        if plane['rms_residual_m']>1e-10:
            return dict(reason='nonplanar_terrain_without_declared_controlling_edge')
        return no_diffraction('ground_only_planar')
    if edge[1] is None:
        return dict(reason='unknown_edge_elevation')
    if any(scene[key] is None for key in ('edge_width_m', 'opaque', 'infinite_lateral_extent')):
        return dict(reason='unknown_edge_properties')
    if scene['edge_width_m']!=0 or not scene['opaque'] or not scene['infinite_lateral_extent']:
        return dict(reason='unsupported_edge_width_transmission_or_lateral_extent')
    x, _ = edge
    left, right, _ = geometry.independent_split(points, x)
    ps, pr = [geometry.numerical_plane(parts, adaptive_check=True) for parts in (left, right)]
    sf, rf = geometry.independent_frame(ps, s, edge), geometry.independent_frame(pr, edge, r)
    profile_with_edge = sorted([p for p in points if p[0]!=x]+[edge], key=lambda p: p[0])
    hull = geometry.geos_upper_path(profile_with_edge, s, r)
    if len(hull)>3 or (len(hull)==3 and hull[1]!=list(edge)):
        return dict(reason='multiple_or_other_controlling_edges')
    delta, blocked = numerical_delta(s, r, edge)
    if not blocked:
        for a, b, c in zip(points, points[1:], points[2:]):
            before = math.atan2(b[1]-a[1], b[0]-a[0])
            after = math.atan2(c[1]-b[1], c[0]-b[0])
            if b[0]!=x and before-after>1e-12 and numerical_delta(s, r, b)[0]>delta+1e-10:
                return dict(reason='another_edge_requires_selection')
    sm, rm = sf['source']['mirror_point'], rf['receiver']['mirror_point']
    if not sm[0]<x<rm[0]:
        return dict(reason='unsupported_mirror_point_order')
    mirror = numerical_delta(sm, rm, edge)[0]
    wavelength = 340/f
    first, second = delta>-.05*wavelength, delta+mirror>.25*wavelength
    selection = dict(use_diffraction=blocked or (first and second), blocked=blocked,
        wavelength_m=wavelength, first_condition=first, rayleigh_condition=second,
        first_margin_m=delta+.05*wavelength, rayleigh_margin_m=delta+mirror-.25*wavelength)
    result.update(delta_m=delta, mirror_delta_m=mirror, selection=selection, source_plane=ps,
        receiver_plane=pr, source_frame=sf, receiver_frame=rf, source_mirror=sm, receiver_mirror=rm)
    if not selection['use_diffraction']:
        return no_diffraction('ground_only_clear_edge')
    gs, gr = ground_mean(ground_segments, points[0][0], x), ground_mean(ground_segments, x, points[-1][0])
    sg = reference_ground(f, sf, gs, source_g)
    rg = reference_ground(f, rf, gr, source_g, receiver_side=True)

    def diffraction(d):
        argument = 3+40*d/wavelength
        return max(0., 10*math.log10(argument)) if argument>=1 else 0.

    main = diffraction(delta)
    ds, dr = numerical_delta(sm, r, edge)[0], numerical_delta(s, rm, edge)[0]
    sb, rb = sf['source']['signed_height_m']<0, rf['receiver']['signed_height_m']<0
    si, ri = main if sb else diffraction(ds), main if rb else diffraction(dr)

    def coupling(ag, image, below):
        if below:
            return ag
        ratio = 10**((main-image)/20)
        amplitude = (1-ratio)+ratio*10**(-ag/20)
        return -20*math.log10(amplitude) if amplitude>0 else None

    sc, rc = coupling(sg, si, sb), coupling(rg, ri, rb)
    if sc is None or rc is None:
        return dict(reason='nonpositive_ground_diffraction_coupling')
    height_sum = sf['source']['equivalent_height_m']+sf['receiver']['equivalent_height_m']
    ratio = min(1., sf['projected_distance_m']/(30*height_sum)) if height_sum else 1.
    result.update(branch='diffraction_including_ground', source_side_g=gs, receiver_side_g=gr,
        source_side_corrected_g=ratio*gs+(1-ratio)*source_g,
        main_diffraction_uncapped_db=main, main_diffraction_db=min(main, 25.),
        source_image_diffraction_db=si, receiver_image_diffraction_db=ri,
        source_image_delta_m=ds, receiver_image_delta_m=dr, source_ground_db=sg, receiver_ground_db=rg,
        source_ground_coupling_db=sc, receiver_ground_coupling_db=rc,
        source_below_mean_plane_rule=sb, receiver_below_mean_plane_rule=rb,
        separate_ground_db=0., boundary_db=min(main, 25.)+sc+rc)
    return result


def compare(actual, expected, counts, errors):
    if 'reason' in expected:
        assert actual['status']=='withheld' and actual['reason']==expected['reason']
        assert actual['boundary_db'] is None and actual['annual_noise_prediction'] is None
        counts['held_results_checked']+=1
        return
    assert actual['status']=='conditional_research' and actual['branch']==expected['branch']
    assert actual['annual_noise_prediction'] is None and not actual['regulatory_implementation_qualified']
    for key, value in expected.items():
        if key.endswith('_plane') and isinstance(value, dict):
            geometry.compare_plane(actual[key], value)
        elif key.endswith('_frame'):
            # Original chainage is independently reconstructed by GEOS in the
            # public fixtures. Hypot/projection roundoff must not be mistaken
            # for a changed endpoint. Check it numerically, then use the prior
            # verifier for every derived frame field.
            checked_frame = dict(value)
            for end in ('source', 'receiver'):
                for a, b in zip(actual[key][end]['original'], value[end]['original']):
                    close(a, b)
                checked_frame[end] = dict(value[end], original=actual[key][end]['original'])
            geometry.compare_frame(actual[key], checked_frame)
        elif key=='selection':
            for k, v in value.items():
                if isinstance(v, bool):
                    assert actual[key][k]==v, (k, actual[key][k], v)
                else:
                    close(actual[key][k], v)
        elif isinstance(value, list):
            for a, b in zip(actual[key], value): close(a, b)
        elif isinstance(value, bool) or isinstance(value, str):
            assert actual[key]==value, key
        else:
            close(actual[key], value)
            if key.endswith('_db'):
                errors.append(abs(actual[key]-value))
    counts['numeric_results_checked']+=1
    if expected['branch']=='diffraction_including_ground':
        counts['main_cap_cases_checked']+=int(expected['main_diffraction_uncapped_db']>25)
        counts['source_below_mean_cases_checked']+=int(expected['source_below_mean_plane_rule'])
        counts['receiver_below_mean_cases_checked']+=int(expected['receiver_below_mean_plane_rule'])


def reference_fixture(sources, scene):
    raw = read(sources/(scene['case_id']+'_Direct.json'))['cutPoints']
    s, r = raw[0]['coordinate'], raw[-1]['coordinate']
    line = LineString([(s['x'], s['y']), (r['x'], r['y'])])
    xs = []
    for record in raw:
        p = Point(record['coordinate']['x'], record['coordinate']['y'])
        assert line.distance(p)<1e-9
        xs.append(line.project(p))
    points = [[x, record['zGround']] for x, record in zip(xs, raw)]
    ground = [[a, b, record['groundCoefficient']] for a, b, record in zip(xs, xs[1:], raw)]
    for a, b in zip(scene['points'], points):
        for x, y in zip(a, b): close(x, y)
    for a, b in zip(scene['ground_segments'], ground):
        for x, y in zip(a, b): close(x, y)
    assert scene['source']==[0., s['z']] and scene['source_g']==raw[0]['groundCoefficient']
    for x, y in zip(scene['receiver'], [xs[-1], r['z']]): close(x, y)
    idx = next(i for i, record in enumerate(raw) if record['type']=='Wall') if scene['case_id']=='TC07' else next(i for i, record in enumerate(raw) if record['zGround']==10)
    for x, y in zip(scene['edge'], [xs[idx], raw[idx]['coordinate']['z']]): close(x, y)
    result = dict(scene); result.update(points=points, ground_segments=ground,
        receiver=[xs[-1], r['z']], edge=[xs[idx], raw[idx]['coordinate']['z']])
    return result


def original_array(text, test, variable):
    block = re.search(r'public void '+test+r'\(\).*?(?=public void|\Z)', text, flags=re.S).group()
    line = next(line for line in block.splitlines() if re.match(r'\s*double\[\]\s+'+variable+r'\s*=', line))
    return [float(token) for token in line.split('{', 1)[1].split('}', 1)[0].split(',')]


def figure(output, benchmarks):
    plt = geometry.plt
    plt.rcParams.update({'font.size':10, 'axes.spines.top':False, 'axes.spines.right':False})
    fig, axes = plt.subplots(2, 2, figsize=(13, 8.5))
    records = {test: sorted([b for b in benchmarks if b['case_id']==test], key=lambda b:b['frequency_hz']) for test in ('TC05', 'TC06', 'TC07')}
    r = records['TC06'][0]['result']
    # Ground polyline comes from the independently reconstructed scene.
    points = figure.scene['points']
    x, z = np.array(points).T
    ax = axes[0, 0]
    ax.plot(x, z, color='#756451', lw=2, label='Supplied terrain')
    source, receiver = figure.scene['source'], figure.scene['receiver']
    ax.plot(*np.array([source, receiver]).T, color='#9c3856', lw=1.7, label='Clear direct ray')
    edge = r['edge']; split = edge[0]
    for p, domain, label in [(r['source_plane'], [0, split], 'Source mean plane'), (r['receiver_plane'], [split, x[-1]], 'Receiver mean plane')]:
        ax.plot(domain, [p['intercept']+p['slope']*v for v in domain], '--', lw=1.5, label=label)
    ax.scatter(*np.array([source, receiver, edge]).T, color='#222a35', s=25, zorder=4)
    ax.scatter(*np.array([r['source_mirror'], r['receiver_mirror']]).T, color='#267968', marker='x', s=50, label='Separate-plane mirrors')
    ax.set_title('TC06: clear ray with a supplied terrain crest', loc='left')
    ax.set_xlabel('Horizontal chainage (m)'); ax.set_ylabel('Elevation (m)')
    ax.margins(y=.2); ax.legend(fontsize=8, loc='upper left')
    ax = axes[0, 1]
    fs = list(BANDS)
    first = [b['result']['selection']['first_margin_m'] for b in records['TC06']]
    second = [b['result']['selection']['rayleigh_margin_m'] for b in records['TC06']]
    ax.axhline(0, color='#777777', lw=.8)
    ax.plot(fs, first, 'o-', label='First-condition margin')
    ax.plot(fs, second, 's-', label='Rayleigh-condition margin')
    ax.set_xscale('log'); ax.set_yscale('symlog', linthresh=.01)
    ax.set_xticks(fs, [str(f) if f<1000 else str(f//1000)+'k' for f in fs])
    ax.set_title('TC06: both margins positive at 500 / 1000 Hz', loc='left')
    ax.set_xlabel('Nominal octave centre (Hz)'); ax.set_ylabel('Margin (m; symmetric log scale)')
    ax.legend(fontsize=8, loc='lower right')
    ax = axes[1, 0]
    for test in ('TC05', 'TC06', 'TC07'):
        values = [b['result']['boundary_db'] for b in records[test]]
        ax.plot(fs, values, 'o-', label=test)
    ax.set_xscale('log'); ax.set_xticks(fs, [str(f) if f<1000 else str(f//1000)+'k' for f in fs])
    ax.set_title('Published direct boundary terms reproduced', loc='left')
    ax.set_xlabel('Nominal octave centre (Hz)'); ax.set_ylabel('Boundary attenuation (dB)')
    ax.legend(fontsize=8, loc='upper left')
    ax = axes[1, 1]
    for field, label in [('main_diffraction_db', 'Main diffraction'), ('source_ground_coupling_db', 'Source ground coupling'),
        ('receiver_ground_coupling_db', 'Receiver ground coupling'), ('boundary_db', 'Combined boundary term')]:
        ax.plot(fs, [b['result'][field] for b in records['TC07']], 'o-', label=label)
    ax.set_xscale('log'); ax.set_xticks(fs, [str(f) if f<1000 else str(f//1000)+'k' for f in fs])
    ax.set_title('TC07: one boundary term includes ground', loc='left')
    ax.set_xlabel('Nominal octave centre (Hz)'); ax.set_ylabel('Attenuation contribution (dB)')
    ax.legend(fontsize=8, loc='upper left')
    fig.text(.04, .025, 'Conditional homogeneous direct-path boundary terms; these are not measured or annual noise levels.\nEU Annex II / 2021 amendment; public NoiseModelling reference cases (originals retained under GPLv3).', fontsize=10)
    fig.tight_layout(rect=[0, .085, 1, 1]); fig.savefig(output/'single_edge_boundary_checks.png', dpi=170); plt.close(fig)


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    own_sha, geometry_sha = file_hash(Path(__file__)), file_hash(ROOT/'scripts/62_verify_terrain_profile_checks.py')
    input_sha = geometry.check_generation(inputs)
    report, protocol = read(inputs/'report.json'), read(inputs/'protocol.json')
    parents = {n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n,p in parents.items())
    sources = next(p for p in parents.values() if (p/'TC07_Direct.json').exists())
    manifest = read(sources/'manifest.json')
    source_parents = {n:ROOT/'artifacts'/n for n in manifest['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n,p in source_parents.items())
    assert read(parents['terrain_profile_review_v1']/'verification.json')['verifier_code_sha256']==geometry_sha
    for n,h in report['code_sha256'].items():
        assert file_hash(ROOT/n)==file_hash(inputs/'code'/n)==h
    cases = {c['case_id']:c for c in protocol['synthetic_scenes']}
    assert len(cases)==29 and tuple(protocol['bands_hz'])==BANDS
    results = read(inputs/'synthetic_results.json')['results']
    expected_keys = {(c['case_id'], g, f) for c in cases.values()
        for g in ([None] if c.get('unknown_ground') else protocol['ground_cases']) for f in BANDS}
    assert len(results)==len(expected_keys)==904
    assert {(r['case_id'], r['ground_case_g'], r['frequency_hz']) for r in results}==expected_keys
    counts, errors = Counter(), []
    for r in results:
        expected = independent(cases[r['case_id']], r['frequency_hz'], r['ground_case_g'])
        compare(r, expected, counts, errors)
    benchmarks = read(inputs/'reference_results.json')['benchmarks']
    original = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    scenes = {c['case_id']:reference_fixture(sources, c) for c in protocol['reference_scenes']}
    assert set(scenes)=={'TC05','TC06','TC07'} and len(benchmarks)==24
    assert {(b['case_id'], b['frequency_hz']) for b in benchmarks}=={(test, f) for test in scenes for f in BANDS}
    fields = {'main_diffraction_uncapped_db':'expectedDeltaDiffSR', 'source_ground_db':'expectedAGroundSO',
        'receiver_ground_db':'expectedAGroundOR', 'source_image_diffraction_db':'expectedDeltaDiffSPrimeR',
        'receiver_image_diffraction_db':'expectedDeltaDiffSRPrime', 'source_ground_coupling_db':'expectedDeltaGroundSO',
        'receiver_ground_coupling_db':'expectedDeltaGroundOR'}
    max_public_error = 0.; comparison_keys = set()
    for b in benchmarks:
        test, f = b['case_id'], b['frequency_hz']
        expected = independent(scenes[test], f)
        compare(b['result'], expected, counts, errors)
        assert b['source_commit']==manifest['commit'] and not b['external_engine_executed']
        expected_fields = {'boundary_db'}| (set(fields) if expected['branch']=='diffraction_including_ground' else set())
        assert {c['field'] for c in b['comparisons']}==expected_fields
        assert len(b['comparisons'])==len(expected_fields)
        for c in b['comparisons']:
            variable = 'expectedABoundaryH' if c['field']=='boundary_db' else fields[c['field']]+('H' if test=='TC07' else '')
            assert c['original_variable']==variable
            reference = original_array(original, test, variable)[BANDS.index(f)]
            tolerance = (.02 if test=='TC05' else .1) if c['field']=='boundary_db' else (.1 if c['field'] in ('source_ground_db','source_ground_coupling_db','receiver_ground_coupling_db') else .02)
            assert c['expected']==reference and c['tolerance_db']==tolerance
            close(c['actual'], b['result'][c['field']]); close(c['error_db'], c['actual']-reference)
            assert abs(expected[c['field']]-reference)<=tolerance
            max_public_error = max(max_public_error, abs(c['error_db']))
            comparison_keys.add((test, f, c['field'])); counts['public_scalar_comparisons_checked']+=1
    assert len(comparison_keys)==counts['public_scalar_comparisons_checked']==94
    assert [b['frequency_hz'] for b in benchmarks if b['case_id']=='TC06' and b['result']['branch']=='diffraction_including_ground']==[500,1000]
    readiness = read(inputs/'real_path_readiness.json')['probes']
    native = {p['probe_id']:p for p in read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']}
    assert len(readiness)==100 and {p['probe_id'] for p in readiness}==set(native)
    for p in readiness:
        assert p['status']=='withheld' and p['boundary_db'] is None and p['noise_prediction'] is None
        assert p['qualified_horizontal_edges'] is None
        for k in ('qualified_source_elevation_odn_m','qualified_ground_g'):
            assert p[k]==native[p['probe_id']][k] is None
        counts['real_paths_verified_withheld']+=1
    summary = report['summary']
    assert summary['states']==dict(Counter(r['status'] for r in results))
    assert summary['branches']==dict(Counter(r['branch'] for r in results if r['status']=='conditional_research'))
    assert summary['withheld_reasons']==dict(Counter(r['reason'] for r in results if r['status']=='withheld'))
    close(summary['maximum_reference_error_db'], max_public_error)
    assert not summary['real_area_noise_predictions'] and not report['eligible_for_absolute_noise_release']
    assert not report['external_engine_executed'] and not report['ISO_certification']
    output.mkdir(parents=True)
    figure.scene = scenes['TC06']; figure(output, benchmarks)
    receipt = dict(schema_version=1, status='passed_independent_single_edge_boundary_checks', input_inventory_sha256=input_sha,
        parent_inventory_sha256=report['parent_inventory_sha256'], source_parent_inventory_sha256=manifest['parent_inventory_sha256'],
        counts=dict(counts), maximum_independent_db_difference=max(errors), maximum_public_rounded_error_db=max_public_error,
        comparison_tolerances_db=[.02,.1], methods=['Prior independent Gauss/QUADPACK fits and vector projections',
            'GEOS convex-hull geometry', 'NumPy distances/orientation determinants', 'Independent expanded ground equation',
            'Amplitude-sum ground coupling and uncapped/capped terms', 'Original fixture geometry and original public array extraction'],
        full_current_method_qualified=False, external_engine_executed=False, ISO_certification=False, real_area_noise_validated=False,
        code_sha256={Path(__file__).name:own_sha,'62_verify_terrain_profile_checks.py':geometry_sha},
        runtime=dict(python=sys.version, numpy=np.__version__, scipy=geometry.scipy.__version__, shapely=geometry.shapely.__version__))
    (output/'verification.json').write_bytes(json_bytes(receipt))
    for script in (Path(__file__), ROOT/'scripts/62_verify_terrain_profile_checks.py'):
        shutil.copyfile(script, output/script.name)
    assert file_hash(Path(__file__))==own_sha and file_hash(ROOT/'scripts/62_verify_terrain_profile_checks.py')==geometry_sha
    assert geometry.check_generation(inputs)==input_sha
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n,p in parents.items())
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n,p in source_parents.items())
    assert all(file_hash(ROOT/n)==h for n,h in report['code_sha256'].items())
    files = {p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity = 'terrain-boundary-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity, files=files)))
    print(json.dumps(dict(generation_id=identity, verification=receipt)), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', default=str(ROOT/'artifacts/terrain_boundary_checks_v1'))
    parser.add_argument('--output', default=str(ROOT/'artifacts/terrain_boundary_review_v1'))
    args = parser.parse_args(); run(args.inputs, args.output)
