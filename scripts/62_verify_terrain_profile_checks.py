"""Independent terrain geometry, native-cell and missing-state verification.

No import of the evaluated terrain, propagation or generation modules. Numerical
quadrature/linear algebra, GEOS hulls and rasterio cell indexing provide separate
calculation paths. Public rounded geometry fixtures are not engine execution,
field accuracy, ISO certification or a qualified acoustic propagation chain.
"""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import rasterio
import scipy
from scipy.integrate import quad
import shapely
from shapely import from_wkt
from shapely.geometry import LineString, MultiPoint, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes


def read(path):
    return json.loads(Path(path).read_bytes())


def check_generation(path):
    path = Path(path).resolve()
    assert path.is_relative_to(ROOT / 'artifacts')
    inventory = read(path / 'inventory.json')
    for name, sha in inventory['files'].items():
        p = (path / name).resolve()
        assert p.is_relative_to(path) and file_hash(p) == sha, name
    return file_hash(path / 'inventory.json')


def close(a, b, *, atol=1e-9):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=atol), (a, b)


def polyline_segments(points):
    return [(a[0], b[0], a[1], b[1]) for a, b in zip(points, points[1:])]


def numerical_plane(segments, *, adaptive_check=False):
    """Gauss quadrature normal equations; no evaluated analytic moments."""
    length = segments[-1][1] - segments[0][0]
    missing = sum(b-a for a, b, u, v in segments if u is None or v is None)
    if missing:
        return dict(status='withheld', profile_length_m=length, missing_length_m=missing)
    origin = segments[0][0]
    centre = origin + length/2
    reference = segments[0][2]
    nodes, weights = np.polynomial.legendre.leggauss(3)
    xs, zs, ws = [], [], []
    for a, b, u, v in segments:
        fractions = (nodes+1)/2
        xs.extend(a + fractions*(b-a) - centre)
        zs.extend(u-reference + fractions*(v-u))
        ws.extend(weights*(b-a)/2)
    xs, zs, ws = np.array(xs), np.array(zs), np.array(ws)
    basis = np.column_stack((np.ones(len(xs)), xs))
    constant, slope = np.linalg.solve(basis.T @ (basis*ws[:, None]), basis.T @ (ws*zs))
    residual = zs - basis @ np.array([constant, slope])
    rms = math.sqrt(float(np.dot(ws, residual**2))/length)
    at_origin = reference + constant-slope*length/2
    if adaptive_check:
        # QUADPACK separately verifies the normal-equation residual integrals.
        moments = [0., 0.]
        square_error = 0.
        for a, b, u, v in segments:
            def difference(x):
                z = u-reference + (v-u)*(x-a)/(b-a)
                return z-constant-slope*(x-centre)
            moments[0] += quad(difference, a, b, epsabs=1e-9)[0]
            moments[1] += quad(lambda x: (x-centre)*difference(x), a, b, epsabs=1e-8)[0]
            square_error += quad(lambda x: difference(x)**2, a, b, epsabs=1e-9)[0]
        assert abs(moments[0])/length < 1e-9
        assert abs(moments[1])/length**2 < 1e-9
        close(math.sqrt(max(0., square_error/length)), rms)
    return dict(status='conditional_geometry', origin_x_m=origin, slope=float(slope),
        height_at_origin_m=float(at_origin), intercept=float(at_origin-slope*origin),
        profile_length_m=length, missing_length_m=0., rms_residual_m=rms)


def compare_plane(actual, expected):
    assert actual['status'] == expected['status']
    for key in ('profile_length_m', 'missing_length_m'):
        close(actual[key], expected[key])
    if expected['status'] == 'withheld':
        assert actual['slope'] is None and actual['height_at_origin_m'] is None
        return
    for key in ('origin_x_m', 'slope', 'height_at_origin_m', 'intercept', 'rms_residual_m'):
        close(actual[key], expected[key])


def independent_frame(plane, source, receiver):
    if plane['status'] == 'withheld' or source is None or receiver is None or source[1] is None or receiver[1] is None:
        return dict(status='withheld')
    anchor = np.array([plane['origin_x_m'], plane['height_at_origin_m']])
    tangent = np.array([1., plane['slope']]) / math.hypot(1, plane['slope'])
    normal = np.array([-tangent[1], tangent[0]])
    def point(p):
        p = np.array(p)
        height = float(np.dot(p-anchor, normal))
        foot = p-height*normal
        positive = max(0., height)
        return dict(original=p.tolist(), signed_height_m=height, equivalent_height_m=positive,
            projection=foot.tolist(), equivalent_point=(foot+positive*normal).tolist(),
            mirror_point=(foot-positive*normal).tolist(), negative_equivalent_height_clipped=height < 0)
    return dict(status='conditional_geometry', source=point(source), receiver=point(receiver),
        projected_distance_m=float(np.dot(np.array(receiver)-np.array(source), tangent)),
        direct_distance_m=float(np.linalg.norm(np.array(receiver)-np.array(source))))


def compare_frame(actual, expected):
    assert actual['status'] == expected['status']
    if expected['status'] == 'withheld':
        assert actual['projected_distance_m'] is None
        return
    for key in ('projected_distance_m', 'direct_distance_m'):
        close(actual[key], expected[key])
    for end in ('source', 'receiver'):
        a, b = actual[end], expected[end]
        assert a['original'] == b['original']
        for key in ('signed_height_m', 'equivalent_height_m'):
            close(a[key], b[key])
        # The zero threshold can differ by roundoff for an exactly planar edge.
        if abs(b['signed_height_m']) > 1e-10:
            assert a['negative_equivalent_height_clipped'] == b['negative_equivalent_height_clipped']
        for key in ('projection', 'equivalent_point', 'mirror_point'):
            for x, y in zip(a[key], b[key]):
                close(x, y)


def geos_upper_path(points, source, receiver):
    """Select upper GEOS convex-hull chain independently of the evaluated stack."""
    hull = MultiPoint([source, receiver] + points[1:-1]).convex_hull
    if hull.geom_type == 'LineString':
        return [source, receiver]
    vertices = list(hull.exterior.coords)[:-1]
    start, end = vertices.index(tuple(source)), vertices.index(tuple(receiver))
    chains = []
    for step in (1, -1):
        chain, i = [], start
        while True:
            chain.append(vertices[i])
            if i == end:
                break
            i = (i+step) % len(vertices)
        area = sum((b[0]-a[0])*(a[1]+b[1])/2 for a, b in zip(chain, chain[1:]))
        chains.append((area, chain))
    return [list(p) for p in max(chains, key=lambda c: c[0])[1]]


def independent_split(points, x):
    left, right = [], []
    for a, b, u, v in polyline_segments(points):
        def part(lo, hi):
            endpoints = np.interp([lo, hi], [a, b], [u, v])
            return lo, hi, float(endpoints[0]), float(endpoints[1])
        if a < x:
            left.append(part(a, min(x, b)))
        if b > x:
            right.append(part(max(a, x), b))
    return left, right, [x, left[-1][3]]


def reference_ground(f, distance, zs, zr, g):
    if g == 0:
        return -3.
    gp = g*min(distance/(30*(zs+zr)), 1.) if zs+zr else g
    k = 2*math.pi*f/340
    w = .0185*f**2.5*gp**2.6/(f**1.5*gp**2.6 + 1300*f**.75*gp**1.3 + 1160000)
    cf = distance*(1+3*w*distance*math.exp(-math.sqrt(w*distance)))/(1+w*distance)
    source = zs**2-math.sqrt(2*cf/k)*zs+cf/k
    receiver = zr**2-math.sqrt(2*cf/k)*zr+cf/k
    return max(-10*math.log10(4*k*k*source*receiver/distance**2), -3*(1-gp))


def public_expectations(source_text, test, variable):
    block = source_text.split('public void '+test+'()', 1)[1].split('public void ', 1)[0]
    match = re.search(r'\b'+variable+r'\s*=\s*new double\[\]\[\]\s*\{(.*?)\n\s*\};', block, re.S)
    assert match, (test, variable)
    content = re.sub(r'//[^\n]*', '', match.group(1))
    rows = re.findall(r'\{([^{}]+)\}', content)
    return [[float(v.strip()) for v in row.split(',')[:5]] for row in rows]


def figures(output, cases, bias):
    plt.rcParams.update({'font.size': 10, 'axes.spines.top': False, 'axes.spines.right': False})
    fig, axes = plt.subplots(2, 2, figsize=(12, 8))
    for ax, key, title in [(axes[0, 0], 'plane-slope-0.5', 'Sloped ground: perpendicular heights'),
        (axes[0, 1], 'symmetric-ridge', 'Single ridge: separate side planes'),
        (axes[1, 0], 'two-ridges', 'Two ridges: upper path geometry')]:
        case = cases[key]
        xs, zs = np.array(case['points']).T
        ax.plot(xs, zs, color='#756451', lw=2, label='Supplied terrain')
        plane = case['mean_plane']
        ax.plot(xs, plane['intercept']+plane['slope']*xs, '--', color='#4474a4', label='Common mean plane')
        ax.plot(*np.array(case['upper_path']['path']).T, color='#933c56', lw=1.5, label='Upper path')
        ax.scatter(*np.array([case['source'], case['receiver']]).T, color='#222a35', s=30, zorder=5)
        if key.startswith('plane'):
            for endpoint in ('source', 'receiver'):
                p = case['common_frame'][endpoint]
                ax.plot(*np.array([p['original'], p['projection']]).T, color='#267968', lw=2)
            frame = case['common_frame']
            ax.text(.04, .95, f"Perpendicular heights: {frame['source']['equivalent_height_m']:.3f} / {frame['receiver']['equivalent_height_m']:.3f} m\nProjected distance: {frame['projected_distance_m']:.3f} m", transform=ax.transAxes, va='top', fontsize=9)
        elif key == 'symmetric-ridge':
            sides = case['split_planes'][0]
            for key_, domain in [('source_plane', [0, 50]), ('receiver_plane', [50, 100])]:
                p = sides[key_]
                ax.plot(domain, [p['intercept']+p['slope']*x for x in domain], ':', color='#267968', lw=3, label='Side mean planes' if key_=='source_plane' else None)
            ax.text(.04, .95, 'Ridge attenuation withheld\nCommon plane height: 5 m', transform=ax.transAxes, va='top', fontsize=9)
        else:
            ax.text(.04, .95, 'Multiple-edge attenuation withheld', transform=ax.transAxes, va='top', fontsize=9)
        ax.set_title(title, loc='left')
        ax.set_xlabel('Horizontal chainage (m)')
        ax.set_ylabel('Elevation (m)')
        ax.margins(y=.25)
        ax.legend(loc='lower right', fontsize=8)
    ax = axes[1, 1]
    xs, zs = np.array(bias['points']).T
    ax.plot(xs, zs, 'o-', color='#756451', label='Uneven vertices on same ridge')
    plane = bias['continuous_mean_plane']
    ols = bias['unweighted_point_ols']
    ax.plot(xs, plane['intercept']+plane['slope']*xs, '--', color='#4474a4', label='Distance-weighted mean plane')
    ax.plot(xs, np.polyval(ols, xs), ':', color='#933c56', lw=2, label='Unweighted vertex fit')
    ax.set_title('Extra vertices must not add ground weight', loc='left')
    ax.set_xlabel('Horizontal chainage (m)'); ax.set_ylabel('Elevation (m)')
    ax.margins(y=.2); ax.legend(loc='lower right', fontsize=8)
    fig.text(.04, .025, 'Controlled synthetic geometry. Terrain is supplied; ground and endpoint assumptions are conditional.\nRidge/valley geometry does not establish a qualified diffraction or annual noise result.', fontsize=10)
    fig.tight_layout(rect=[0, .085, 1, 1])
    fig.savefig(output/'terrain_geometry_checks.png', dpi=170)
    plt.close(fig)


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    code_sha = file_hash(Path(__file__))
    input_sha = check_generation(inputs)
    report, protocol = read(inputs/'report.json'), read(inputs/'protocol.json')
    parents = {n: ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    assert all(check_generation(p)==report['parent_inventory_sha256'][n] for n, p in parents.items())
    sources = next(p for p in parents.values() if (p/'NoiseModelling-CnossosTests.java').exists())
    manifest = read(sources/'manifest.json')
    source_parents = {n: ROOT/'artifacts'/n for n in manifest['parent_inventory_sha256']}
    assert all(check_generation(p)==manifest['parent_inventory_sha256'][n] for n, p in source_parents.items())
    assert file_hash(ROOT/manifest['jrc_pdf_path'])==manifest['jrc_pdf_sha256']
    pdf_review = read(inputs/'method-review/source.json')
    assert pdf_review['source_sha256']==manifest['jrc_pdf_sha256']
    for name, sha in report['code_sha256'].items():
        assert file_hash(inputs/'code'/name)==sha and file_hash(ROOT/name)==sha, name
    cases = {c['case_id']: c for c in read(inputs/'synthetic_geometry.json')['cases']}
    assert len(cases)==len(protocol['cases'])==15
    counts = Counter()
    for definition in protocol['cases']:
        c = cases[definition['case_id']]
        assert all(c[k]==v for k, v in definition.items())
        segments = polyline_segments(c['points'])
        plane = numerical_plane(segments, adaptive_check=True)
        compare_plane(c['mean_plane'], plane)
        frame = independent_frame(plane, c['source'], c['receiver'])
        compare_frame(c['common_frame'], frame)
        path = c['upper_path']
        if plane['status']=='withheld' or frame['status']=='withheld':
            assert path['status']=='withheld' and path['path'] is None and path['path_difference_m'] is None
            assert c['split_planes']==[]
        else:
            expected_path = geos_upper_path(c['points'], c['source'], c['receiver'])
            assert path['path']==expected_path
            direct = math.dist(c['source'], c['receiver'])
            routed = sum(math.dist(a, b) for a, b in zip(expected_path, expected_path[1:]))
            close(path['direct_distance_m'], direct); close(path['path_distance_m'], routed)
            close(path['path_difference_m'], max(0., routed-direct))
            clearance = min(float(np.interp(x, [c['source'][0], c['receiver'][0]], [c['source'][1], c['receiver'][1]]))-z for x, z in c['points'])
            close(path['minimum_vertical_clearance_m'], clearance)
            assert path['direct_ray_blocked']==(clearance<0) and path['touches_terrain']==(clearance==0)
            assert path['edge_count']==len(expected_path)-2 and path['noise_attenuation_db'] is None
            assert len(c['split_planes'])==path['edge_count']
            for sides, edge in zip(c['split_planes'], expected_path[1:-1]):
                left, right, ground_edge = independent_split(c['points'], edge[0])
                assert sides['edge']==ground_edge and sides['noise_attenuation_db'] is None
                for name, segs, endpoints in [('source', left, (c['source'], ground_edge)), ('receiver', right, (ground_edge, c['receiver']))]:
                    p = numerical_plane(segs, adaptive_check=True)
                    compare_plane(sides[name+'_plane'], p)
                    compare_frame(sides[name+'_frame'], independent_frame(p, *endpoints))
                    counts['separate_side_planes_checked']+=1
            counts['geos_upper_paths_checked']+=1
        assert c['qualified_noise_prediction'] is None
        counts['synthetic_geometry_cases_checked']+=1
    results = read(inputs/'synthetic_ground_results.json')['results']
    expected_keys = {(c['case_id'], g, f) for c in protocol['cases']
        for g in ([None] if c['unknown_ground'] else protocol['ground_cases']) for f in protocol['bands_hz']}
    assert {(r['case_id'], r['g'], r['frequency_hz']) for r in results}==expected_keys
    assert len(results)==len(expected_keys)  # Unknown ground must not duplicate four nominal cases.
    max_ground_error = 0.
    for r in results:
        c = cases[r['case_id']]
        if c['mean_plane']['status']=='withheld':
            reason = 'unknown_terrain'
        elif c['source'] is None or c['receiver'] is None:
            reason = 'unknown_endpoint_elevation'
        elif r['g'] is None:
            reason = 'unknown_ground'
        elif c['mean_plane']['rms_residual_m']>1e-10:
            reason = 'nonplanar_terrain_requires_qualified_diffraction_selector'
        elif not 63<=r['frequency_hz']<=4000:
            reason = 'outside_checked_JRC2012_frequency_scope'
        else:
            reason = None
        if reason:
            assert r['status']=='withheld' and r['reason']==reason and r['boundary_db'] is None
            counts['held_synthetic_ground_results_checked']+=1
        else:
            assert r['status']=='conditional_synthetic_research'
            p = numerical_plane(polyline_segments(c['points']))
            frame = independent_frame(p, c['source'], c['receiver'])
            compare_plane(r['mean_plane'], p); compare_frame(r['frame'], frame)
            value = reference_ground(r['frequency_hz'], frame['projected_distance_m'],
                frame['source']['equivalent_height_m'], frame['receiver']['equivalent_height_m'], r['g'])
            close(r['boundary_db'], value)
            max_ground_error = max(max_ground_error, abs(r['boundary_db']-value))
            assert r['annual_noise_prediction'] is None and not r['regulatory_implementation_qualified']
            counts['conditional_synthetic_ground_results_checked']+=1
    benchmarks = read(inputs/'external_mean_plane_benchmarks.json')['benchmarks']
    original_tests = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8')
    expectations = dict(TC05_Direct=public_expectations(original_tests, 'TC05', 'meanPlanes')[0],
        TC06_Direct=public_expectations(original_tests, 'TC06', 'srMeanPlanes')[0])
    side_expectations = public_expectations(original_tests, 'TC06', 'segmentsMeanPlanes')
    expectations['TC06_Direct-source-side'], expectations['TC06_Direct-receiver-side'] = side_expectations
    assert {b['case_id'] for b in benchmarks}==set(expectations) and len(benchmarks)==4
    max_external_error = 0.
    for b in benchmarks:
        raw = read(sources/b['fixture_file'])['cutPoints']
        origin = np.array([raw[0]['coordinate'][k] for k in ('x', 'y')])
        points = [[float(np.linalg.norm(np.array([r['coordinate'][k] for k in ('x', 'y')])-origin)), r['zGround']] for r in raw]
        source, receiver = [0., raw[0]['coordinate']['z']], [points[-1][0], raw[-1]['coordinate']['z']]
        for actual, expected in zip(b['points'], points):
            for x, y in zip(actual, expected): close(x, y)
        assert b['source']==source
        for x, y in zip(b['receiver'], receiver): close(x, y)
        segments = polyline_segments(points)
        if 'split_x_m' in b:
            left, right, edge = independent_split(points, b['split_x_m'])
            if b['case_id'].endswith('source-side'):
                segments, receiver = left, edge
            else:
                segments, source = right, edge
        plane = numerical_plane(segments, adaptive_check=True)
        frame = independent_frame(plane, source, receiver)
        actual = [plane['slope'], plane['intercept'], frame['source']['equivalent_height_m'],
                  frame['receiver']['equivalent_height_m'], frame['projected_distance_m']]
        assert b['expected_rounded']==expectations[b['case_id']]
        assert b['tolerance']==.0051 and b['source_commit']==manifest['commit'] and not b['external_engine_executed']
        for x, y, expected, error in zip(b['actual'], actual, b['expected_rounded'], b['error']):
            close(x, y); close(error, x-expected)
            assert abs(y-expected)<=.0051
            max_external_error = max(max_external_error, abs(y-expected))
            counts['public_geometry_scalar_checks']+=1
    bias = read(inputs/'sampling_bias_demonstration.json')
    compare_plane(bias['continuous_mean_plane'], numerical_plane(polyline_segments(bias['points']), adaptive_check=True))
    x, z = np.array(bias['points']).T
    vertex_fit = np.linalg.lstsq(np.column_stack((x, np.ones(len(x)))), z, rcond=None)[0]
    for a, b in zip(bias['unweighted_point_ols'], vertex_fit): close(a, b)
    assert abs(vertex_fit[1]-bias['continuous_mean_plane']['intercept'])>1
    # Original cell ownership, represented lengths and masks; no evaluated traversal.
    native = read(inputs/'native_terrain_profiles.json')['probes']
    original = {p['probe_id']: p for p in read(parents['ground_barrier_audit_v1']/'path_geometry_audit.json')['probes']}
    assert {p['probe_id'] for p in native}==set(original) and len(native)==100
    max_cell_error, missing_intervals = 0., 0
    with rasterio.open(parents['physical_scene_inputs_v2']/'terrain_odn_2m.tif') as dataset:
        assert dataset.crs.to_epsg()==27700 and dataset.res==(2., 2.)
        values, masks = dataset.read(1), dataset.read_masks(1)
        for p in native:
            original_probe = original[p['probe_id']]
            assert p['geometry_wkt_bng']==original_probe['geometry_wkt_bng']
            line = from_wkt(p['geometry_wkt_bng'])
            close(p['length_m'], line.length)
            end = 0.; segments = []; cell_lengths = defaultdict(float)
            for i in p['intervals']:
                assert i['start_m']==end and i['end_m']>i['start_m']
                weight = i['end_m']-i['start_m']; close(weight, i['represented_length_m'])
                midpoint = line.interpolate((i['start_m']+i['end_m'])/2)
                close(midpoint.x, i['easting']); close(midpoint.y, i['northing'])
                row, col = dataset.index(midpoint.x, midpoint.y)
                assert 0<=row<dataset.height and 0<=col<dataset.width
                assert (row, col)==(i['row'], i['column'])
                x0, y0 = dataset.transform*(col, row)
                cell = box(x0, y0-2, x0+2, y0)
                # Near a grid corner, distinct float events can share one native
                # cell. Verify the interval's own support first, then the whole
                # cell's aggregate length; never count a cell's full intersection
                # once per event. Sub-nanometre positions reach BNG float limits.
                subline = LineString([line.interpolate(i['start_m']), line.interpolate(i['end_m'])])
                close(subline.intersection(cell).length, weight, atol=1e-7)
                cell_lengths[(row, col)] += weight
                counts['sub_0.1_micrometre_intervals_retained'] += int(weight<1e-7)
                height = float(values[row, col]) if masks[row, col] and math.isfinite(float(values[row, col])) else None
                assert i['terrain_odn_m']==height and i['valid_original_terrain']==(height is not None)
                missing_intervals += height is None
                segments.append((i['start_m'], i['end_m'], height, height))
                end = i['end_m']; counts['original_native_intervals_checked']+=1
            close(end, line.length)
            for (row, col), weight in cell_lengths.items():
                x0, y0 = dataset.transform*(col, row)
                intersection_length = line.intersection(box(x0, y0-2, x0+2, y0)).length
                max_cell_error = max(max_cell_error, abs(intersection_length-weight))
                close(intersection_length, weight, atol=1e-7)
                counts['distinct_native_cell_supports_checked'] += 1
            plane = numerical_plane(segments)
            compare_plane(p['cell_constant_mean_plane'], plane)
            heights = []
            for endpoint, key, assumed_height in [(line.coords[0], 'source_terrain', .05), (line.coords[-1], 'receptor_terrain', 4.)]:
                row, col = dataset.index(*endpoint)
                original_height = float(values[row, col]) if masks[row, col] else None
                assert original_probe[key]['terrain_odn_m']==original_height
                heights.append(None if original_height is None else original_height+assumed_height)
            source = None if heights[0] is None else [0, heights[0]]
            receiver = None if heights[1] is None else [line.length, heights[1]]
            frame = independent_frame(plane, source, receiver)
            compare_frame(p['conditional_at_grade_frame'], frame)
            for endpoint in ('source', 'receiver'):
                if frame['status']!='withheld':
                    counts['conditional_'+endpoint+'_height_clips']+=int(frame[endpoint]['negative_equivalent_height_clipped'])
            assert p['qualified_source_elevation_odn_m'] is None and p['qualified_ground_g'] is None and p['noise_prediction'] is None
            assert p['status']=='native_terrain_geometry_diagnostic_only'
            counts['native_geometry_probes_checked']+=1
    summary = report['summary']
    assert summary['synthetic_ground_results']==len(results)
    assert summary['ground_result_states']==dict(Counter(r['status'] for r in results))
    assert summary['withheld_reasons']==dict(Counter(r['reason'] for r in results if r['status']=='withheld'))
    assert summary['external_geometry_scalar_checks']==counts['public_geometry_scalar_checks']
    close(summary['maximum_rounded_benchmark_error'], max_external_error)
    assert summary['native_counts']['native_intervals']==counts['original_native_intervals_checked']
    assert summary['native_counts']['missing_intervals']==missing_intervals
    for endpoint in ('source', 'receiver'):
        assert summary['native_counts']['conditional_'+endpoint+'_height_clips']==counts['conditional_'+endpoint+'_height_clips']
    assert not summary['real_area_noise_predictions'] and not report['eligible_for_absolute_noise_release']
    output.mkdir(parents=True)
    figures(output, cases, bias)
    receipt = dict(schema_version=1, status='passed_independent_terrain_geometry_and_original_cell_verification',
        input_inventory_sha256=input_sha, parent_inventory_sha256=report['parent_inventory_sha256'],
        source_parent_inventory_sha256=manifest['parent_inventory_sha256'], counts=dict(counts),
        maximum_conditional_ground_difference_db=max_ground_error, maximum_rounded_public_geometry_error=max_external_error,
        public_fixture_tolerance=.0051, maximum_native_cell_length_difference_m=max_cell_error,
        native_cell_length_absolute_tolerance_m=1e-7,
        methods=['Three-point Gauss-Legendre quadrature with solved normal equations', 'Adaptive QUADPACK moment/residual checks for synthetic/public polylines',
            'Vector tangent/normal projections', 'GEOS convex hull upper chains', 'Original GDAL cell indexing and GEOS cell intersection lengths',
            'Independent homogeneous ground formula on synthetic planes only'],
        external_engine_executed=False, full_ISO_certification=False, physical_terrain_accuracy_validated=False,
        real_area_noise_validated=False, full_current_acoustic_method_qualified=False,
        verifier_code_sha256=code_sha, runtime=dict(python=sys.version, numpy=np.__version__, scipy=scipy.__version__,
            rasterio=rasterio.__version__, shapely=shapely.__version__, matplotlib=matplotlib.__version__))
    (output/'verification.json').write_bytes(json_bytes(receipt))
    shutil.copyfile(Path(__file__), output/Path(__file__).name)
    assert file_hash(Path(__file__))==code_sha and check_generation(inputs)==input_sha
    assert all(check_generation(p)==report['parent_inventory_sha256'][n] for n, p in parents.items())
    assert all(check_generation(p)==manifest['parent_inventory_sha256'][n] for n, p in source_parents.items())
    for name, sha in report['code_sha256'].items():
        assert file_hash(ROOT/name)==sha
    files = {p.relative_to(output).as_posix(): file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity = 'terrain-profile-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity, files=files)))
    print(json.dumps(dict(generation_id=identity, verification=receipt)), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', default=str(ROOT/'artifacts/terrain_profile_checks_v2'))
    parser.add_argument('--output', default=str(ROOT/'artifacts/terrain_profile_review_v1'))
    args = parser.parse_args()
    run(args.inputs, args.output)
