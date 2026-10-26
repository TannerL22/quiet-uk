"""Preserve native land-cover classes and qualify historical barrier candidates.

Exports diagnostic class support, never observed G or real-area sound levels.
"""
import argparse
from collections import Counter, defaultdict
import csv
import importlib.util
import json
import math
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import pyproj
from pyproj.enums import TransformDirection
from pyproj.transformer import TransformerGroup
import rasterio
from rasterio.windows import transform as window_transform
from shapely import from_wkt
from shapely.geometry import LineString, box
from shapely.ops import transform as geometry_transform
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.ground_cover import (CLASSES, crosswalk, validate_native, native_window,
    sample_class, midpoint_samples, conditional_ground, barrier_candidate, snapshot_consistency)

spec = importlib.util.spec_from_file_location('ground_sources', ROOT / 'scripts/57_acquire_ground_cover_sources.py')
acquisition = importlib.util.module_from_spec(spec)
spec.loader.exec_module(acquisition)


def read(path):
    return json.loads(Path(path).read_bytes())


def boundary(bounds, step=100.):
    w, s, e, n = bounds
    corners = [(w, s), (e, s), (e, n), (w, n), (w, s)]
    return [((1-t)*a[0]+t*b[0], (1-t)*a[1]+t*b[1])
            for a, b in zip(corners, corners[1:])
            for t in np.linspace(0, 1, math.ceil(math.dist(a, b)/step)+1)]


def run(sources, output):
    sources, output = Path(sources).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    audit = ROOT / 'artifacts/ground_barrier_audit_v1'
    physical = ROOT / 'artifacts/physical_scene_inputs_v2'
    method = ROOT / 'artifacts/ground_barrier_method_review_v1'
    emissions = ROOT / 'artifacts/road_emission_sources_v1'
    parents = {p.name: p for p in (sources, audit, physical, method, emissions)}
    for p in parents.values():
        acquisition.verified(p)
    hashes = {n: file_hash(p / 'inventory.json') for n, p in parents.items()}
    manifest = read(sources / 'manifest.json')
    if manifest['parent_inventory_sha256'] != {audit.name: hashes[audit.name]}:
        raise ValueError('Source selection does not match audit')
    study = manifest['study']
    halo, core = study['source_halo_bounds'], study['core_bounds']
    # Pin the documented 2m registry operation even on hosts with extra grids.
    # This is a registry accuracy descriptor, not a local confidence/error bound.
    group = TransformerGroup(27700, 4326, always_xy=True)
    choices = [t for t in group.transformers if 'OSGB36 to WGS 84 (6)' in t.description and t.accuracy == 2.]
    if len(choices) != 1:
        raise ValueError('Expected pinned OSGB36/WGS84 operation (6)')
    forward = choices[0]
    inverse = lambda x, y: forward.transform(x, y, direction=TransformDirection.INVERSE)
    xy = np.array(boundary(halo))
    lon, lat = forward.transform(xy[:, 0], xy[:, 1])
    envelope = [float(min(lon)), float(min(lat)), float(max(lon)), float(max(lat))]
    south, west, north, east = manifest['osm_bbox_wgs84_south_west_north_east']
    if not (west < envelope[0] < envelope[2] < east and south < envelope[1] < envelope[3] < north):
        raise ValueError('OSM bbox must contain complete halo envelope')
    output.mkdir(parents=True)
    shutil.copytree(ROOT / 'artifacts/ground_cover_discovery_v1/documentation', output / 'documentation')
    # Original PDF pages/table review stays traceable to the sealed originals.
    shutil.copytree(ROOT / 'artifacts/ground_cover_pdf_review_v1', output / 'pdf-review')
    with rasterio.open(sources / manifest['map_file']) as source:
        validate_native(source)
        tags = source.tags()
        if tags.get('product_tile') != manifest['tile'] or tags.get('time_start') != '2021-01-01T00:00:00Z' or tags.get('product_version') != 'V2.0.0':
            raise ValueError('Unexpected map identity/year')
        window = native_window(source.transform, source.width, source.height, envelope)
        values, mask = source.read(1, window=window), source.read_masks(1, window=window)
        transform = window_transform(window, source.transform)
        profile = source.profile.copy()
        profile.update(width=values.shape[1], height=values.shape[0], transform=transform)
        with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=True):
            with rasterio.open(output / 'landcover_native_wgs84.tif', 'w', **profile) as dest:
                dest.write(values, 1)
                dest.write_mask(mask)
                dest.update_tags(**tags)
                dest.update_tags(extract='Integer native window; no resampling. Zero is provider nodata, not acoustic ground G=0.')
    longitude = transform.c + (np.arange(values.shape[1]) + .5) * transform.a
    latitude = transform.f + (np.arange(values.shape[0]) + .5) * transform.e
    lon_grid, lat_grid = np.meshgrid(longitude, latitude)
    x_grid, y_grid = inverse(lon_grid, lat_grid)
    def membership(bounds):
        w, s, e, n = bounds
        return (x_grid >= w) & (x_grid < e) & (y_grid > s) & (y_grid <= n)
    halo_mask, core_mask = membership(halo), membership(core)
    def counts(region):
        return dict(native_centres=int(region.sum()), provider_valid_centres=int((region & (mask != 0)).sum()),
                    code_counts={str(int(c)): int(n) for c, n in zip(*np.unique(values[region], return_counts=True))},
                    meaning='Counts of native pixel centres in the BNG rectangle, not equal-metre area, acoustic classification or local accuracy.')
    with rasterio.open(output / 'halo_membership_native_wgs84.tif', 'w', **{**profile, 'nodata': None}) as dest:
        dest.write(halo_mask.astype(np.uint8), 1)
        dest.update_tags(meaning='1=centre inside BNG source halo; 0=fringe centre outside. Does not change source land-cover mask.')
    geod = pyproj.Geod(ellps='WGS84')
    mid_lon, mid_lat = float(np.mean(lon)), float(np.mean(lat))
    width_m = geod.inv(mid_lon, mid_lat, mid_lon+transform.a, mid_lat)[2]
    height_m = geod.inv(mid_lon, mid_lat, mid_lon, mid_lat+abs(transform.e))[2]
    raster_info = dict(file='landcover_native_wgs84.tif', crs='EPSG:4326', transform=list(transform),
        shape=list(values.shape), native_resolution_degrees=[transform.a, abs(transform.e)],
        pixel_dimensions_at_study_centre_m=[width_m, height_m], source_window=list(window.flatten()),
        envelope_wgs84=envelope, provider_tags=tags, nodata_code=0, halo=counts(halo_mask), core=counts(core_mask),
        class_posterior_probability=None, input_quality_file=manifest['quality_file'], input_quality_state=manifest['quality_state'],
        input_quality_definition='Three EO input-observation/invalid-observation bands at 1/2000 degree; not class probabilities or acoustic confidence.',
        local_landcover_accuracy=None, observed_ground_g=None)
    (output / 'native_raster.json').write_bytes(json_bytes(raster_info))
    (output / 'acoustic_crosswalk.json').write_bytes(json_bytes(dict(classes=crosswalk(),
        source='ESA WorldCover 2021 v200 PUM table 3 (p15); JRC2012 table VI.1 (PDF p86) and path-length convention (p87). Surface analogies, not calibrated class/G equivalence.',
        road_platform='Local source/road-platform convention remains in emission. Do not infer platform width/deck elevation from this raster.',
        limitations='No per-pixel or local acoustic uncertainty probabilities. Tree canopy can cover water or built-up substrate. Mixed pixels and source-year compatibility remain unqualified.')))

    snapshots = []
    for s in manifest['osm_sources']:
        elements = read(sources / s['response_file'])['elements'] if s['response_file'] else None
        if elements is not None and len({e['id'] for e in elements}) != len(elements):
            raise ValueError('Duplicate historical OSM way')
        snapshots.append({e['id']: e for e in elements} if elements is not None else None)
    start, end = snapshots
    candidates, candidate_geoms = [], []
    for id_, e in sorted((end or {}).items()):
        geometry = LineString([(p['lon'], p['lat']) for p in e['geometry']])
        g = geometry_transform(inverse, geometry)
        if not g.is_valid or g.length == 0:
            raise ValueError('Invalid historical candidate geometry')
        candidate = dict(osm_way_id=id_, version=e['version'], edit_timestamp_utc=e['timestamp'],
            requested_snapshot_utc=manifest['osm_sources'][1]['snapshot_utc'], tags=e['tags'],
            geometry_wkt_wgs84=geometry.wkt, geometry_wkt_bng=g.wkt,
            intersects_halo=g.intersects(box(*halo)), intersects_core=g.intersects(box(*core)),
            halo_length_m=g.intersection(box(*halo)).length,
            start_snapshot_version=start[id_]['version'] if start is not None and id_ in start else None,
            snapshot_consistency=snapshot_consistency(start.get(id_) if start is not None else None, e) if start is not None else 'start_snapshot_unavailable',
            **barrier_candidate(e['tags']))
        candidates.append(candidate)
        candidate_geoms.append(g)
    removed = [dict(osm_way_id=id_, snapshot_consistency=snapshot_consistency(e, None))
               for id_, e in sorted((start or {}).items()) if end is not None and id_ not in end]
    (output / 'barrier_candidates_bng.json').write_bytes(json_bytes(dict(candidates=candidates, start_only_records=removed,
        scope=manifest['osm_scope'], catalogue_complete=None, acoustic_barrier_absence_established=False,
        source_files=manifest['osm_sources'])))
    barrier_tree = STRtree(candidate_geoms)
    probes = read(audit / 'path_geometry_audit.json')['probes']
    profiles = []
    fields = ['probe_id', 'chainage_m', 'represented_length_m', 'easting', 'northing', 'longitude', 'latitude',
              'row', 'column', 'code', 'state', 'observed_ground_g']
    with (output / 'path_class_samples.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for probe in probes:
            line = from_wkt(probe['geometry_wkt_bng'])
            def profile(step, dx=0., dy=0., export=False):
                lengths = defaultdict(float)
                codes = []
                for chainage, weight, x, y in midpoint_samples(line, step):
                    lon, lat = forward.transform(x+dx, y+dy)
                    sample = sample_class(values, mask, transform, lon, lat)
                    code = sample['code'] if sample['state'] == 'reported_class' else None
                    codes.append(code)
                    lengths[code] += weight
                    if export:
                        writer.writerow(dict(probe_id=probe['probe_id'], chainage_m=chainage, represented_length_m=weight,
                            easting=x, northing=y, longitude=lon, latitude=lat, **sample, observed_ground_g=None))
                return lengths, codes
            base, base_codes = profile(2., export=True)
            finer, _ = profile(1.)
            fractions = lambda d: {str(k) if k is not None else 'unknown': v / line.length for k, v in sorted(d.items(), key=lambda r: str(r[0]))}
            refinement_difference = .5 * sum(abs(base.get(k, 0.)-finer.get(k, 0.)) for k in set(base)|set(finer)) / line.length
            registration = []
            for dx, dy in ((-2., 0.), (2., 0.), (0., -2.), (0., 2.)):
                _, shifted = profile(2., dx, dy)
                registration.append(dict(dx_m=dx, dy_m=dy, changed_class_fraction=sum(a!=b for a, b in zip(base_codes, shifted))/len(base_codes)))
            hits = [candidates[int(i)] for i in sorted(barrier_tree.query(line, predicate='intersects'))]
            ground = conditional_ground(base)
            profiles.append(dict(probe_id=probe['probe_id'], link_id=probe['link_id'], geometry_wkt_bng=line.wkt,
                length_m=line.length, diagnostic_maximum_spacing_m=2., diagnostic_samples=len(base_codes),
                class_length_fractions=fractions(base), refined_1m_class_length_fractions=fractions(finer),
                refinement_total_variation=refinement_difference, registration_stress_cases=registration,
                registration_meaning='Declared +/-2m BNG stress, not a confidence interval or a bound on source/transform error.',
                **ground, qualified_propagation_ground_g=None,
                conditional_use_gate='Ground-only analogy; footprint intersections, source deck/receiver geometry and annual ground conditions remain unqualified.',
                intersects_generalised_footprint=bool(probe['intersecting_generalised_footprints']),
                receptor_inside_generalised_footprint=probe['receptor_inside_generalised_footprint'],
                intersecting_candidate_way_ids=[c['osm_way_id'] for c in hits],
                intersecting_candidate_categories=dict(Counter(c['category'] for c in hits)),
                barrier_absence_established=False, qualified_noise_prediction=None))
    (output / 'path_ground_profiles.json').write_bytes(json_bytes(dict(probes=profiles)))
    summary = dict(halo_native_class_centres=raster_info['halo']['native_centres'], core_native_class_centres=raster_info['core']['native_centres'],
        native_class_counts_halo=raster_info['halo']['code_counts'], native_class_counts_core=raster_info['core']['code_counts'],
        start_snapshot_ways=len(start) if start is not None else None, end_snapshot_ways=len(end) if end is not None else None,
        barrier_candidate_categories=dict(Counter(c['category'] for c in candidates)),
        halo_candidate_categories=dict(Counter(c['category'] for c in candidates if c['intersects_halo'])),
        core_candidate_categories=dict(Counter(c['category'] for c in candidates if c['intersects_core'])),
        snapshot_consistency_counts=dict(Counter(c['snapshot_consistency'] for c in candidates)),
        start_only_records=len(removed), explicit_metre_height_tags=sum(c['tagged_height_m'] is not None for c in candidates),
        explicit_noise_barrier_candidates=sum(c['category']=='explicit_noise_barrier_candidate' for c in candidates),
        qualified_acoustic_barriers=0, qualified_observed_ground_g=0, geometry_probes=len(profiles),
        probes_with_barrier_candidate_intersection=sum(bool(p['intersecting_candidate_way_ids']) for p in profiles),
        probes_with_wall_candidate_intersection=sum(bool(p['intersecting_candidate_categories'].get('generic_wall_candidate')) for p in profiles),
        paths_with_fully_mapped_conditional_analogy=sum(p['conditional_analogy_g'] is not None for p in profiles),
        maximum_1m_refinement_total_variation=max(p['refinement_total_variation'] for p in profiles),
        maximum_registration_stress_changed_fraction=max(c['changed_class_fraction'] for p in profiles for c in p['registration_stress_cases']),
        noise_predictions_created=False)
    code_names = ['scripts/57_acquire_ground_cover_sources.py', 'scripts/58_build_ground_cover_inputs.py',
                  'src/quiet_uk/ground_cover.py', 'src/quiet_uk/explorer.py']
    report = dict(schema_version=1, status='qualified_native_landcover_classes_and_unqualified_barrier_candidates',
        study=study, summary=summary, parent_inventory_sha256=hashes,
        coordinate_transform=dict(pipeline=forward.definition, description=forward.description,
            registry_accuracy_m=forward.accuracy, best_available=group.best_available,
            local_error_bound_m=None, note='Pinned operation (6), no PROJ network grids fetched; registry descriptor does not certify local accuracy.'),
        native_raster=raster_info, licences=manifest['licences'], attributions=manifest['attributions'],
        parent_licences=read(physical / 'report.json')['licenses'], parent_attributions=read(physical / 'report.json')['attributions'],
        national_highways_searches=manifest['national_highways_searches'], ukceh_status=manifest['ukceh_status'],
        road_dates='Traffic/land cover 2021; road/building geometry 2026-04, LiDAR survey inference 2020/21. Compatibility unestablished.',
        eligible_for_absolute_noise_release=False,
        runtime=dict(python=sys.version, platform=platform.platform(), numpy=np.__version__, rasterio=rasterio.__version__,
                     pyproj=pyproj.__version__, proj=pyproj.proj_version_str),
        code_sha256={name: file_hash(ROOT / name) for name in code_names})
    (output / 'report.json').write_bytes(json_bytes(report))
    for name in code_names:
        destination = output / 'code' / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for name, parent in parents.items():
        acquisition.verified(parent)
        if file_hash(parent / 'inventory.json') != hashes[name]:
            raise ValueError('Parent changed during extraction')
    if any(file_hash(ROOT / n) != h for n, h in report['code_sha256'].items()):
        raise ValueError('Code changed during extraction')
    identity = acquisition.seal(output, 'ground-cover-inputs-')
    print(json.dumps(dict(generation_id=identity, summary=summary)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default=str(ROOT / 'artifacts/ground_cover_sources_v1'))
    parser.add_argument('--output', default=str(ROOT / 'artifacts/ground_cover_inputs_v3'))
    args = parser.parse_args()
    run(args.sources, args.output)
