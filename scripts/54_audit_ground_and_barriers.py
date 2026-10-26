"""Audit native ground/surface and obstacle evidence; no acoustic predictions."""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import rasterio
from shapely import from_wkt
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.physical_scene import assert_native_grid
from quiet_uk.propagation_canary import native_cell


def read_json(path):
    return json.loads(Path(path).read_bytes())


def verified(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Inputs must be workspace artifacts')
    inv = read_json(path / 'inventory.json')
    for name, digest in inv['files'].items():
        p = (path / name).resolve()
        if not p.is_relative_to(path) or file_hash(p) != digest:
            raise ValueError('Input integrity failed: ' + name)
    return inv


def line_parts(geometry):
    if geometry.geom_type == 'LineString':
        return [geometry] if geometry.length else []
    if hasattr(geometry, 'geoms'):
        return [part for g in geometry.geoms for part in line_parts(g)]
    return []


def points_on_line(line, step=2.):
    n = max(1, math.ceil(line.length / step))
    for distance in np.linspace(0, line.length, n + 1):
        p = line.interpolate(float(distance))
        yield float(distance), p.x, p.y


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    names = ('physical_scene_inputs_v2', 'physical_scene_review_v1', 'road_direction_inputs_v2',
             'road_direction_review_v1', 'road_emission_assumptions_v2', 'ground_barrier_method_review_v1')
    parents = {n: ROOT / 'artifacts' / n for n in names}
    invs = {n: verified(p) for n, p in parents.items()}
    hashes = {n: file_hash(p / 'inventory.json') for n, p in parents.items()}
    if read_json(parents['physical_scene_review_v1'] / 'verification.json')['input_inventory_sha256'] != hashes['physical_scene_inputs_v2']:
        raise ValueError('Physical review does not match inputs')
    if read_json(parents['road_direction_review_v1'] / 'verification.json')['input_inventory_sha256'] != hashes['road_direction_inputs_v2']:
        raise ValueError('Road review does not match inputs')
    physical, roads = parents['physical_scene_inputs_v2'], parents['road_direction_inputs_v2']
    study = read_json(roads / 'report.json')['study']
    bounds, core = study['source_halo_bounds'], box(*study['core_bounds'])
    with (roads / 'road_representation_ledger_bng.csv').open(encoding='utf-8', newline='') as f:
        ledger = list(csv.DictReader(f))
    budget = read_json(roads / 'carriageway_budgets.json')['groups']
    accepted = {r['link_id'] for g in budget for r in g['representations']}
    selected = [r for r in ledger if r['link_id'] in accepted]
    if len(selected) != 722 or len(accepted) != len(selected):
        raise ValueError('Unexpected provisional study budget')
    with (physical / 'building_heights_bng.csv').open(encoding='utf-8', newline='') as f:
        buildings = list(csv.DictReader(f))
    if any(b['acoustic_obstacle_height_m'] or b['height_uncertainty_m'] for b in buildings):
        raise ValueError('Expected unqualified acoustic building heights')
    building_geoms = [from_wkt(b['geometry_wkt_bng']) for b in buildings]
    building_tree = STRtree(building_geoms)
    native = []
    for name in ('terrain_odn_2m.tif', 'surface_odn_2m.tif'):
        with rasterio.open(physical / name) as dataset:
            assert_native_grid(dataset, bounds)
            native.append((dataset.read(1), dataset.read_masks(1)))

    def sample(x, y):
        cell = native_cell(x, y, bounds)
        result = dict(row=None, column=None, terrain_odn_m=None, surface_odn_m=None)
        if cell is None:
            return result
        result['row'], result['column'] = cell
        for key, (values, mask) in zip(('terrain_odn_m', 'surface_odn_m'), native):
            value = float(values[cell])
            if mask[cell] and math.isfinite(value):
                result[key] = value
        return result

    def obstacle(i):
        b = buildings[int(i)]
        return dict(source_id=b['source_id'], source_record_number=int(b['source_record_number']),
            height_status=b['status'], provisional_height_agl_m=float(b['provisional_height_agl_m']) if b['provisional_height_agl_m'] else None,
            acoustic_obstacle_height_m=None, withheld_reason=b['withheld_reason'] or None)

    output.mkdir(parents=True)
    audits, count = [], Counter()
    clipped_lines = []
    with (output / 'road_native_samples_bng.csv').open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=['link_id', 'part', 'chainage_m', 'easting', 'northing',
            'row', 'column', 'terrain_odn_m', 'surface_odn_m'])
        writer.writeheader()
        for r in selected:
            original = from_wkt(r['geometry_wkt_bng'])
            geometry = original.intersection(box(*bounds))
            clipped_lines.append(geometry)
            samples = []
            for part, line in enumerate(line_parts(geometry)):
                for chainage, x, y in points_on_line(line):
                    s = sample(x, y)
                    writer.writerow(dict(link_id=r['link_id'], part=part, chainage_m=chainage, easting=x, northing=y, **s))
                    samples.append(s)
                    count['samples'] += 1
                    if s['row'] is None:
                        count['boundary_outside_samples'] += 1
                    elif s['terrain_odn_m'] is None:
                        count['missing_terrain_samples'] += 1
                    if s['terrain_odn_m'] is not None and s['surface_odn_m'] is not None:
                        count['paired_samples'] += 1
                        difference = s['surface_odn_m'] - s['terrain_odn_m']
                        count['negative_surface_differences'] += int(difference < 0)
                        count['surface_excess_over_2m'] += int(difference >= 2)
            hits = [obstacle(i) for i in building_tree.query(geometry, predicate='intersects')]
            heights = [s['terrain_odn_m'] for s in samples if s['terrain_odn_m'] is not None]
            audits.append(dict(link_id=r['link_id'], group_id=r['group_id'], road_structure=r['road_structure'] or None,
                halo_length_m=geometry.length, original_length_m=original.length, sample_count=len(samples),
                missing_or_outside_terrain_samples=sum(s['terrain_odn_m'] is None for s in samples),
                terrain_min_odn_m=min(heights) if heights else None, terrain_max_odn_m=max(heights) if heights else None,
                road_surface_elevation_odn_m=None, acoustic_source_elevation_odn_m=None,
                at_grade_status='unestablished; blank structure does not prove at grade',
                ground_absorption_g=None, noise_barrier_inventory_complete=None,
                intersecting_generalised_footprints=hits,
                interpretation='Geometry intersections are review flags; no obstacle removal or acoustic collision is inferred.'))
    # Fixed 5x5 core probes and four nearest distinct retained road representations.
    # These inspect geometry only; positions/heights are not validated noise receivers.
    probes = []
    for ix in range(5):
        for iy in range(5):
            receptor = Point(study['core_bounds'][0] + 200 + 400 * ix, study['core_bounds'][1] + 200 + 400 * iy)
            receptor_sample = sample(receptor.x, receptor.y)
            nearest = sorted(range(len(selected)), key=lambda i: (clipped_lines[i].distance(receptor), selected[i]['link_id']))[:4]
            for rank, index in enumerate(nearest, 1):
                g = clipped_lines[index]
                parts = line_parts(g)
                part = min(parts, key=lambda p: p.distance(receptor))
                source = part.interpolate(part.project(receptor))
                path = LineString([source, receptor])
                source_sample = sample(source.x, source.y)
                hits = [obstacle(i) for i in building_tree.query(path, predicate='intersects')]
                ray_obstructions = 0
                valid = source_sample['terrain_odn_m'] is not None and receptor_sample['terrain_odn_m'] is not None
                missing = 0
                if path.length > 0 and valid:
                    for position, x, y in points_on_line(path):
                        s = sample(x, y)
                        if s['terrain_odn_m'] is None:
                            missing += 1
                        else:
                            assumed_ray = source_sample['terrain_odn_m'] + .05 + position / path.length * (
                                receptor_sample['terrain_odn_m'] + 4 - source_sample['terrain_odn_m'] - .05)
                            ray_obstructions += int(s['terrain_odn_m'] > assumed_ray)
                probes.append(dict(probe_id=f'grid-{ix}-{iy}-road-{rank}', link_id=selected[index]['link_id'],
                    geometry_wkt_bng=path.wkt, length_m=path.length,
                    source_terrain=source_sample, receptor_terrain=receptor_sample,
                    assumed_source_height_agl_m=.05, assumed_receiver_height_agl_m=4.,
                    qualified_source_elevation_odn_m=None, noise_prediction=None,
                    conditional_at_grade_terrain_above_direct_ray_samples=ray_obstructions if valid else None,
                    missing_path_terrain_samples=missing if valid else None,
                    receptor_inside_generalised_footprint=bool(len(building_tree.query(receptor, predicate='intersects'))),
                    intersecting_generalised_footprints=hits,
                    unknown_standalone_barriers=True, unknown_ground_absorption=True,
                    status='geometry_probe_only; at_grade and receiver position/height are assumptions'))
    summary = dict(provisional_links=len(audits), native_sample_counts=dict(count),
        all_physical_structure_counts=dict(Counter(r['road_structure'] or 'unspecified' for r in ledger)),
        provisional_structure_counts=dict(Counter(r['road_structure'] or 'unspecified' for r in selected)),
        links_intersecting_generalised_footprints=sum(bool(a['intersecting_generalised_footprints']) for a in audits),
        links_intersecting_withheld_height_footprints=sum(any(b['height_status'] == 'withheld' for b in a['intersecting_generalised_footprints']) for a in audits),
        distinct_intersecting_footprints=len({b['source_id'] for a in audits for b in a['intersecting_generalised_footprints']}),
        geometry_probes=len(probes), probes_intersecting_footprints=sum(bool(p['intersecting_generalised_footprints']) for p in probes),
        probes_intersecting_withheld_heights=sum(any(b['height_status'] == 'withheld' for b in p['intersecting_generalised_footprints']) for p in probes),
        probes_with_terrain_above_assumed_ray=sum(bool(p['conditional_at_grade_terrain_above_direct_ray_samples']) for p in probes),
        probes_inside_generalised_footprints=sum(p['receptor_inside_generalised_footprint'] for p in probes),
        building_height_states=dict(Counter(b['status'] for b in buildings)),
        qualified_acoustic_building_heights=0, qualified_road_deck_elevations=0, qualified_ground_absorption_assignments=0,
        qualified_standalone_barrier_features=0, standalone_barrier_absence_established=False)
    conventions = dict(schema_version=1, status='ground_and_barrier_input_audit_no_noise_predictions', study=study,
        native_sampling='2m original containing-cell values and masks, with <=2m along-line diagnostic spacing. No resampling/interpolation; not a propagation mean-plane solver.',
        datum='EPSG:27700 horizontal, metres above ODN vertical. Absolute roof top is not obtained by blindly adding a footprint median to an arbitrary terrain pixel.',
        road_source='0.05m above actual road surface; terrain is conditional only after an explicit at-grade assumption. Bridges/viaducts need deck elevations; tunnels need another model.',
        receiver='Synthetic/probe 4m AGL is a declared study choice, not an observed receptor or facade location.',
        ground='G is dimensionless [0,1]; pavement/local propagation correction belongs in emission. Road platform Gs=0 in the checked method, not an observed off-road Gpath.',
        obstacles='Generalised footprints and roof medians are provisional. Withheld height means unknown shielding, not no building. DSM excess does not classify walls or vegetation.',
        boundary='For horizontal-edge diffraction use the integrated diffraction/ground branch, with separate ground zero. No independent extra road image/+3dB term.',
        reflection='Facade reflection, receiver own-facade exclusion, barrier absorption/transmission and lateral/thick/multiple paths remain unimplemented/unqualified.',
        source_margin='5km halo is a geometric acquisition setting. Synthetic convergence is required; it cannot certify missing network contributions or establish an acoustic cutoff.',
        dates='Traffic 2021, inferred LiDAR surveys 2020/2021 (composite 2022), road/building geometry 2026-04; compatibility unestablished.',
        method_version='Checked research subset of JRC2012 with explicit 2021 selector. Full current regulatory annex, all interactions and independent acoustic validation remain unqualified.',
        real_area_predictions_created=False, eligible_for_absolute_noise_release=False)
    code_names = ['scripts/54_audit_ground_and_barriers.py', 'src/quiet_uk/propagation_canary.py', 'src/quiet_uk/physical_scene.py', 'src/quiet_uk/explorer.py']
    report = dict(**conventions, summary=summary, parent_inventory_sha256=hashes,
        source_licences=read_json(roads / 'report.json')['source_licences'], physical_licences=read_json(physical / 'report.json')['licenses'],
        physical_attributions=read_json(physical / 'report.json')['attributions'],
        runtime=dict(python=sys.version, platform=platform.platform(), numpy=np.__version__, rasterio=rasterio.__version__),
        code_sha256={n: file_hash(ROOT / n) for n in code_names})
    for name, document in [('report.json', report), ('conventions.json', conventions), ('road_physical_audit.json', dict(links=audits)),
                            ('path_geometry_audit.json', dict(probes=probes))]:
        (output / name).write_bytes(json_bytes(document))
    for n in code_names:
        destination = output / 'code' / n
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / n, destination)
    for n, digest in hashes.items():
        verified(parents[n])
        if file_hash(parents[n] / 'inventory.json') != digest:
            raise ValueError('Parent changed during audit')
    for n, digest in report['code_sha256'].items():
        if file_hash(ROOT / n) != digest:
            raise ValueError('Code changed during audit')
    files = {p.relative_to(output).as_posix(): file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output / 'inventory.json').write_bytes(json_bytes(dict(generation_id='ground-barrier-audit-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20], files=files)))
    print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/ground_barrier_audit_v1'))
    run(parser.parse_args().output)
