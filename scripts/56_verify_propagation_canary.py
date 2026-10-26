"""Independently check audit exports and scalar synthetic propagation results.

No evaluated propagation/audit functions are imported. Fidelity verification is
not independent acoustic accuracy validation.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.patches import Polygon as PlotPolygon
import numpy as np
import rasterio
from shapely import from_wkt
from shapely.geometry import Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes


def read_json(path):
    return json.loads(Path(path).read_bytes())


def verified(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use workspace inputs')
    inv = read_json(path / 'inventory.json')
    for name, digest in inv['files'].items():
        p = (path / name).resolve()
        if not p.is_relative_to(path) or file_hash(p) != digest:
            raise ValueError('Inventory mismatch: ' + name)
    return inv


def close(a, b, relative=1e-12, absolute=1e-10):
    if not math.isclose(a, b, rel_tol=relative, abs_tol=absolute):
        raise ValueError(f'Scalar verification differs: {a} versus {b}')


def ground(f, distance, zs, zr, g, receiver_side=False):
    if g == 0:
        return -3.
    gp = g if receiver_side else g * min(distance / (30 * (zs + zr)), 1.) if zs + zr else g
    k = 2 * math.pi * f / 340
    w = .0185 * f ** 2.5 * gp ** 2.6 / (f ** 1.5 * gp ** 2.6 + 1.3e3 * f ** .75 * gp ** 1.3 + 1.16e6)
    cf = distance * (1 + 3 * w * distance * math.exp(-(w * distance) ** .5)) / (1 + w * distance)
    source = zs * zs - math.sqrt(2 * cf / k) * zs + cf / k
    receiver = zr * zr - math.sqrt(2 * cf / k) * zr + cf / k
    return max(-10 * math.log10(4 * k * k * source * receiver / distance ** 2), -3 * (1 - gp))


def delta(distance, zs, zr, x, h):
    excess = math.dist((0, zs), (x, h)) + math.dist((x, h), (distance, zr)) - math.dist((0, zs), (distance, zr))
    blocked = h >= zs + (zr - zs) * x / distance
    return max(excess, 0) if blocked else -max(excess, 0), blocked


def dif(f, difference, h):
    argument = 40 * difference * f / 340
    return 0. if argument < -2 else max(10 * min(f * h / 250, 1) * math.log10(3 + argument), 0)


def boundary(s):
    f, g, distance, zs, zr = (s[k] for k in ('frequency_hz', 'ground_g', 'horizontal_distance_m', 'source_height_m', 'receiver_height_m'))
    if g is None or (s['screen'] is not None and s['screen']['height_m'] is None):
        return None, 'withheld'
    if s['screen'] is None:
        return ground(f, distance, zs, zr, g), 'ground_only'
    x, h = s['screen']['x_m'], s['screen']['height_m']
    d, blocked = delta(distance, zs, zr, x, h)
    mirror = delta(distance, -zs, -zr, x, h)[0]
    if not blocked and not (d > -340 / f / 20 and d > 340 / f / 4 - mirror):
        return ground(f, distance, zs, zr, g), 'ground_only_clear_edge'
    main = dif(f, d, h)
    image_s = dif(f, delta(distance, -zs, zr, x, h)[0], h)
    image_r = dif(f, delta(distance, zs, -zr, x, h)[0], h)
    a_source = ground(f, x, zs, h, g)
    a_receiver = ground(f, distance - x, h, zr, g, True)
    coupling_s = -20 * math.log10(1 + (10 ** (-a_source / 20) - 1) * 10 ** ((main - image_s) / 20))
    coupling_r = -20 * math.log10(1 + (10 ** (-a_receiver / 20) - 1) * 10 ** ((main - image_r) / 20))
    for key, value in [('main_diffraction_uncapped_db', main), ('main_diffraction_db', min(main, 25)),
                        ('source_ground_coupling_db', coupling_s), ('receiver_ground_coupling_db', coupling_r)]:
        close(s[key], value)
    if s['separate_ground_db'] != 0:
        raise ValueError('Ground counted twice')
    return min(main, 25) + coupling_s + coupling_r, 'diffraction_including_ground'


def parts(g):
    if g.geom_type == 'LineString':
        return [g] if g.length > 0 else []
    return [p for child in getattr(g, 'geoms', []) for p in parts(child)]


def run(audit, canary, output):
    audit, canary, output = (Path(p).resolve() for p in (audit, canary, output))
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace review')
    audit_inv, canary_inv = verified(audit), verified(canary)
    audit_report, protocol = read_json(audit / 'report.json'), read_json(canary / 'protocol.json')
    for report in (audit_report, protocol):
        for name, digest in report['parent_inventory_sha256'].items():
            p = ROOT / 'artifacts' / name
            verified(p)
            if file_hash(p / 'inventory.json') != digest:
                raise ValueError('Parent generation identity changed')
        for name, digest in report['code_sha256'].items():
            if file_hash(ROOT / name) != digest or file_hash((audit if report is audit_report else canary) / 'code' / name) != digest:
                raise ValueError('Code snapshot differs')
    if protocol['parent_inventory_sha256'][audit.name] != file_hash(audit / 'inventory.json'):
        raise ValueError('Canary did not follow this audit')
    method = read_json(ROOT / 'artifacts/ground_barrier_method_review_v1/verification.json')
    if file_hash(ROOT / method['source_pdf_path']) != method['source_pdf_sha256']:
        raise ValueError('Method PDF changed')
    for name, digest in method['parent_inventory_sha256'].items():
        verified(ROOT / 'artifacts' / name)
        if file_hash(ROOT / 'artifacts' / name / 'inventory.json') != digest:
            raise ValueError('Method source identity changed')
    physical, road_inputs = ROOT / 'artifacts/physical_scene_inputs_v2', ROOT / 'artifacts/road_direction_inputs_v2'
    with (road_inputs / 'road_representation_ledger_bng.csv').open(encoding='utf-8', newline='') as f:
        ledger = {r['link_id']: r for r in csv.DictReader(f)}
    groups = read_json(road_inputs / 'carriageway_budgets.json')['groups']
    accepted = {r['link_id'] for g in groups for r in g['representations']}
    bounds = audit_report['study']['source_halo_bounds']
    geoms = {identity: from_wkt(ledger[identity]['geometry_wkt_bng']).intersection(box(*bounds)) for identity in accepted}
    with (physical / 'building_heights_bng.csv').open(encoding='utf-8', newline='') as f:
        buildings = list(csv.DictReader(f))
    building_tree = STRtree([from_wkt(b['geometry_wkt_bng']) for b in buildings])
    arrays, masks = [], []
    for name in ('terrain_odn_2m.tif', 'surface_odn_2m.tif'):
        with rasterio.open(physical / name) as ds:
            if ds.crs.to_epsg() != 27700 or ds.shape != (6000, 6000) or tuple(ds.bounds) != tuple(bounds) or ds.res != (2, 2):
                raise ValueError('Native grid changed')
            arrays.append(ds.read(1))
            masks.append(ds.read_masks(1))
            transform = ds.transform
    samples = Counter()
    road_terrain = {identity: [] for identity in accepted}
    slots, numeric_values = 0, 0
    outside, negatives, elevated = 0, 0, 0
    with (audit / 'road_native_samples_bng.csv').open(encoding='utf-8', newline='') as f:
        for r in csv.DictReader(f):
            identity = r['link_id']
            if identity not in accepted:
                raise ValueError('Unqualified road included')
            x, y = float(r['easting']), float(r['northing'])
            line = parts(geoms[identity])[int(r['part'])]
            point = line.interpolate(float(r['chainage_m']))
            if point.distance(Point(x, y)) > 1e-7:
                raise ValueError('Sample left original road geometry')
            inside = bounds[0] <= x < bounds[2] and bounds[1] < y <= bounds[3]
            if inside:
                row, col = rasterio.transform.rowcol(transform, x, y)
                if int(r['row']) != row or int(r['column']) != col:
                    raise ValueError('Native cell ownership changed')
            else:
                outside += 1
                if r['row'] or r['column']:
                    raise ValueError('Boundary sample falsely inside')
            pair = []
            for key, a, mask in zip(('terrain_odn_m', 'surface_odn_m'), arrays, masks):
                expected = float(a[row, col]) if inside and mask[row, col] and np.isfinite(a[row, col]) else None
                observed = float(r[key]) if r[key] else None
                if observed != expected:
                    raise ValueError('Original elevation/mask differs')
                numeric_values += expected is not None
                slots += 1
                pair.append(expected)
            if all(v is not None for v in pair):
                negatives += pair[1] - pair[0] < 0
                elevated += pair[1] - pair[0] >= 2
            if pair[0] is not None:
                road_terrain[identity].append(pair[0])
            samples[identity] += 1
    links = read_json(audit / 'road_physical_audit.json')['links']
    if len(links) != len(accepted) or {r['link_id'] for r in links} != accepted:
        raise ValueError('Lost or duplicated retained roads')

    def check_hits(geometry, observed):
        expected = {buildings[int(i)]['source_id']: buildings[int(i)] for i in building_tree.query(geometry, predicate='intersects')}
        if len(observed) != len(expected) or {b['source_id'] for b in observed} != set(expected):
            raise ValueError('Obstacle footprint omitted or duplicated')
        for b in observed:
            original = expected[b['source_id']]
            height = float(original['provisional_height_agl_m']) if original['provisional_height_agl_m'] else None
            if b['height_status'] != original['status'] or b['provisional_height_agl_m'] != height or b['acoustic_obstacle_height_m'] is not None:
                raise ValueError('Obstacle height/missing state changed')
            if b['source_record_number'] != int(original['source_record_number']) or b['withheld_reason'] != (original['withheld_reason'] or None):
                raise ValueError('Obstacle original identity/reason changed')

    for r in links:
        geom = geoms[r['link_id']]
        expected_samples = sum(math.ceil(p.length / 2) + 1 for p in parts(geom))
        if r['sample_count'] != expected_samples or samples[r['link_id']] != expected_samples:
            raise ValueError('Road diagnostic sample count mismatch')
        close(r['halo_length_m'], geom.length)
        heights = road_terrain[r['link_id']]
        if r['terrain_min_odn_m'] != (min(heights) if heights else None) or r['terrain_max_odn_m'] != (max(heights) if heights else None):
            raise ValueError('Terrain range differs')
        if r['missing_or_outside_terrain_samples'] != expected_samples - len(heights):
            raise ValueError('Missing terrain count differs')
        if any(r[k] is not None for k in ('road_surface_elevation_odn_m', 'acoustic_source_elevation_odn_m', 'ground_absorption_g', 'noise_barrier_inventory_complete')):
            raise ValueError('Unobserved road physical property assigned')
        check_hits(geom, r['intersecting_generalised_footprints'])
    probes = read_json(audit / 'path_geometry_audit.json')['probes']
    if len(probes) != 100 or len({p['probe_id'] for p in probes}) != 100:
        raise ValueError('Probe set incomplete')

    def original_sample(x, y):
        inside = bounds[0] <= x < bounds[2] and bounds[1] < y <= bounds[3]
        row, col = rasterio.transform.rowcol(transform, x, y) if inside else (None, None)
        result = dict(row=int(row) if inside else None, column=int(col) if inside else None)
        for key, values, mask in zip(('terrain_odn_m', 'surface_odn_m'), arrays, masks):
            result[key] = float(values[row, col]) if inside and mask[row, col] and np.isfinite(values[row, col]) else None
        return result

    for p in probes:
        geom = from_wkt(p['geometry_wkt_bng'])
        check_hits(geom, p['intersecting_generalised_footprints'])
        close(p['length_m'], geom.length)
        if p['noise_prediction'] is not None or p['qualified_source_elevation_odn_m'] is not None or not p['unknown_standalone_barriers'] or not p['unknown_ground_absorption']:
            raise ValueError('Unqualified probe state became prediction')
        inside = bool(len(building_tree.query(Point(geom.coords[-1]), predicate='intersects')))
        if inside != p['receptor_inside_generalised_footprint']:
            raise ValueError('Inside-building probe flag lost')
        source, receptor = Point(geom.coords[0]), Point(geom.coords[-1])
        if source.distance(geoms[p['link_id']]) > 1e-7:
            raise ValueError('Probe source left original road')
        source_native, receiver_native = original_sample(source.x, source.y), original_sample(receptor.x, receptor.y)
        if source_native != p['source_terrain'] or receiver_native != p['receptor_terrain']:
            raise ValueError('Probe native terrain/surface differs')
        if p['assumed_source_height_agl_m'] != .05 or p['assumed_receiver_height_agl_m'] != 4:
            raise ValueError('Probe assumptions changed')
        if source_native['terrain_odn_m'] is not None and receiver_native['terrain_odn_m'] is not None:
            if geom.length <= 0:
                obstructed, missing = 0, 0
            else:
                obstructed, missing = 0, 0
                n = max(1, math.ceil(geom.length / 2))
                for distance in np.linspace(0, geom.length, n + 1):
                    point = geom.interpolate(float(distance))
                    native = original_sample(point.x, point.y)['terrain_odn_m']
                    if native is None:
                        missing += 1
                    else:
                        fraction = float(distance) / geom.length
                        ray = (1 - fraction) * (source_native['terrain_odn_m'] + .05) + fraction * (receiver_native['terrain_odn_m'] + 4)
                        obstructed += native > ray
            if obstructed != p['conditional_at_grade_terrain_above_direct_ray_samples'] or missing != p['missing_path_terrain_samples']:
                raise ValueError('Probe geometric terrain-ray diagnostic differs')
    counts = audit_report['summary']['native_sample_counts']
    if counts['samples'] != slots // 2 or counts['boundary_outside_samples'] != outside or counts['negative_surface_differences'] != negatives or counts['surface_excess_over_2m'] != elevated:
        raise ValueError('Native sample report differs')
    scenarios = read_json(canary / 'boundary_scenarios.json')['scenarios']
    if len(scenarios) != 264 or len({(s['frequency_hz'], s['ground_g'], s['screen_case']) for s in scenarios}) != 264:
        raise ValueError('Scenario set incomplete')
    max_error, checked, withheld = 0., 0, 0
    for s in scenarios:
        expected, branch = boundary(s)
        if expected is None:
            withheld += 1
            if s['boundary_db'] is not None or s['energy_transfer_ratio'] is not None or s['status'] != 'withheld':
                raise ValueError('Missing physics became numeric')
        else:
            close(expected, s['boundary_db'])
            close(s['energy_transfer_ratio'], 10 ** (-1.1 - expected / 10) / (100 ** 2 + 3.95 ** 2), absolute=1e-18)
            if s['branch'] != branch:
                raise ValueError('Boundary selector differs')
            max_error = max(max_error, abs(expected - s['boundary_db']))
            checked += 1
    margin = read_json(canary / 'line_margin_cases.json')['cases']
    # Independent adaptive quadrature, rather than evaluated midpoint or analytic routines.
    from scipy.integrate import quad
    quad_errors = []
    for m in margin:
        a, half = m['perpendicular_distance_m'], m['half_length_m']
        q = m['power_per_m']
        scalar, _ = quad(lambda x: q * 10 ** -1.1 / (x * x + a * a), -half, half, epsabs=1e-14, epsrel=1e-12)
        close(scalar, m['analytic_power_ratio'], absolute=1e-14)
        close(m['infinite_line_fraction'], 2 * math.atan(half / a) / math.pi)
        close(m['infinite_line_level_deficit_db'], -10 * math.log10(m['infinite_line_fraction']))
        quad_errors.append(abs(scalar / m['analytic_power_ratio'] - 1))
    for s in read_json(canary / 'segmentation_cases.json')['cases']:
        n = math.ceil(2 * s['half_length_m'] / s['maximum_step_m'])
        width = 2 * s['half_length_m'] / n
        independent = math.fsum(s['power_per_m'] * width * 10 ** -1.1 / ((-s['half_length_m'] + (i + .5) * width) ** 2 + s['perpendicular_distance_m'] ** 2) for i in range(n))
        close(s['numeric_power_ratio'], independent)
    output.mkdir(parents=True)
    # Display-only core review; analytic inputs remain original native grid/BNG geometry.
    fig, ax = plt.subplots(figsize=(8, 8), constrained_layout=True)
    cb = audit_report['study']['core_bounds']
    row0, col0 = rasterio.transform.rowcol(transform, cb[0], cb[3])
    ax.imshow(arrays[0][row0:row0 + 1000, col0:col0 + 1000], extent=(cb[0], cb[2], cb[1], cb[3]), cmap='Greys', vmin=20, vmax=40, alpha=.45)
    for i in building_tree.query(box(*cb), predicate='intersects'):
        b, geom = buildings[int(i)], from_wkt(buildings[int(i)]['geometry_wkt_bng'])
        for polygon in ([geom] if geom.geom_type == 'Polygon' else geom.geoms):
            ax.add_patch(PlotPolygon(np.asarray(polygon.exterior.coords), facecolor='#da9b55' if b['status'] == 'withheld' else '#6b8999', edgecolor='none', alpha=.65))
    lines = [np.asarray(part.coords) for g in geoms.values() for part in parts(g.intersection(box(*cb)))]
    ax.add_collection(LineCollection(lines, colors='#168578', linewidths=2, label='Retained provisional road budgets'))
    first = [p for p in probes if p['probe_id'].endswith('road-1')]
    for inside, colour, marker, label in ((False, '#3f4954', 'o', 'Probe outside footprint'), (True, '#bb3838', 'x', 'Probe inside footprint: location unqualified')):
        xy = [from_wkt(p['geometry_wkt_bng']).coords[-1] for p in first if p['receptor_inside_generalised_footprint'] == inside]
        if xy:
            ax.scatter(*zip(*xy), s=35, c=colour, marker=marker, label=label)
    ax.set(xlim=(cb[0], cb[2]), ylim=(cb[1], cb[3]), aspect='equal', xlabel='BNG easting (m)', ylabel='BNG northing (m)',
        title='Ground/barrier geometry audit | fixed 4 km2 core\nBlue-grey: provisional roof surfaces; orange: withheld heights')
    ax.ticklabel_format(style='plain', useOffset=False)
    ax.legend(loc='lower right', fontsize=8)
    fig.savefig(output / 'ground_barrier_geometry_review.png', dpi=170)
    plt.close(fig)
    receipt = dict(audit_generation_id=audit_inv['generation_id'], canary_generation_id=canary_inv['generation_id'],
        audit_inventory_sha256=file_hash(audit / 'inventory.json'), canary_inventory_sha256=file_hash(canary / 'inventory.json'),
        original_native_elevation_slots_verified=slots, original_numeric_elevations_verified=numeric_values,
        boundary_outside_samples_preserved=outside, road_representations_verified=len(links), geometry_probe_obstacle_sets_verified=len(probes),
        geometry_probe_native_values_and_ray_diagnostics_verified=True,
        scalar_boundary_results_verified=checked, withheld_results_verified=withheld, max_scalar_boundary_difference_db=max_error,
        independent_line_quadratures_verified=len(margin), maximum_quadrature_relative_difference=max(quad_errors),
        code_sha256=file_hash(Path(__file__)), independent_noise_accuracy_validation=False,
        scipy_version=__import__('scipy').__version__, plot_note='Display-only native terrain and original footprints; no exposure, qualified barrier absence or validated receptor is depicted.')
    (output / 'verification.json').write_bytes(json_bytes(receipt))
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    if file_hash(audit / 'inventory.json') != receipt['audit_inventory_sha256'] or file_hash(canary / 'inventory.json') != receipt['canary_inventory_sha256']:
        raise ValueError('Inputs changed during verification')
    verified(audit)
    verified(canary)
    files = {p.relative_to(output).as_posix(): file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output / 'inventory.json').write_bytes(json_bytes(dict(generation_id='propagation-review-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20], files=files)))
    print(json.dumps(receipt), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--audit', default=str(ROOT / 'artifacts/ground_barrier_audit_v1'))
    parser.add_argument('--canary', default=str(ROOT / 'artifacts/synthetic_propagation_v2'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/propagation_review_v2'))
    args = parser.parse_args()
    run(args.audit, args.canary, args.output)
