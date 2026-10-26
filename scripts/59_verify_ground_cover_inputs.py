"""Independent original-value, geometry and missing-state export verification.

Does not import the evaluated ground-cover module or input builder.
This verifies export fidelity, not geodetic, acoustic or local land-cover accuracy.
"""
import argparse
from collections import Counter, defaultdict
import csv
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
from matplotlib.colors import ListedColormap
from matplotlib.lines import Line2D
import numpy as np
import pyproj
from pyproj.enums import TransformDirection
import rasterio
from rasterio.windows import Window
from shapely import from_wkt
from shapely.geometry import LineString, box
from shapely.ops import transform as geometry_transform

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes


def read(path):
    return json.loads(Path(path).read_bytes())


def check_generation(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Verify workspace artifacts only')
    inventory = read(path / 'inventory.json')
    for name, sha in inventory['files'].items():
        p = (path / name).resolve()
        assert p.is_relative_to(path) and file_hash(p) == sha, name
    return file_hash(path / 'inventory.json')


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-9), (a, b)


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    input_sha = check_generation(inputs)
    report = read(inputs / 'report.json')
    parents = {n: ROOT / 'artifacts' / n for n in report['parent_inventory_sha256']}
    assert all(check_generation(p) == report['parent_inventory_sha256'][n] for n, p in parents.items())
    source = next(p for p in parents.values() if (p / 'manifest.json').exists() and 'map_file' in read(p / 'manifest.json'))
    manifest = read(source / 'manifest.json')
    info = read(inputs / 'native_raster.json')
    window = Window(*info['source_window'])
    with rasterio.open(source / manifest['map_file']) as original, rasterio.open(inputs / info['file']) as extracted:
        assert extracted.crs == original.crs and extracted.dtypes == original.dtypes
        assert extracted.transform == original.window_transform(window)
        assert extracted.nodata == original.nodata == 0
        assert (extracted.height, extracted.width) == (window.height, window.width)
        values, mask = original.read(1, window=window), original.read_masks(1, window=window)
        assert np.array_equal(values, extracted.read(1))
        assert np.array_equal(mask, extracted.read_masks(1))
        assert all(extracted.tags()[k] == v for k, v in original.tags().items())
        transform = original.window_transform(window)
    # Explicit, independent semantic lookup; never infer it from display colours.
    names = {10: 'Tree cover', 20: 'Shrubland', 30: 'Grassland', 40: 'Cropland', 50: 'Built-up',
             60: 'Bare / sparse vegetation', 70: 'Snow and ice', 80: 'Permanent water bodies',
             90: 'Herbaceous wetland', 95: 'Mangroves', 100: 'Moss and lichen'}
    crosswalk = read(inputs / 'acoustic_crosswalk.json')['classes']
    analogies = {30: 1, 40: 1, 50: 0, 80: 0}
    assert {int(k) for k in crosswalk} == set(names)
    for key, r in crosswalk.items():
        assert r['class_name'] == names[int(key)] and r['observed_ground_g'] is None
        assert r['conditional_analogy_g'] == analogies.get(int(key))
        assert r['sensitivity_cases_g'] == [0, .3, .7, 1]
    converter = pyproj.Transformer.from_pipeline(report['coordinate_transform']['pipeline'])
    inverse = lambda x, y: converter.transform(x, y, direction=TransformDirection.INVERSE)
    longitudes = transform.c + (np.arange(values.shape[1])+.5)*transform.a
    latitudes = transform.f + (np.arange(values.shape[0])+.5)*transform.e
    xx, yy = inverse(*np.meshgrid(longitudes, latitudes))
    members = {}
    for region, key in [('halo', 'source_halo_bounds'), ('core', 'core_bounds')]:
        w, s, e, n = report['study'][key]
        inside = (xx >= w) & (xx < e) & (yy > s) & (yy <= n)
        members[region] = inside
        assert int(inside.sum()) == info[region]['native_centres']
        codes, counts = np.unique(values[inside], return_counts=True)
        assert {str(int(c)): int(v) for c, v in zip(codes, counts)} == info[region]['code_counts']
        assert int((inside & (mask != 0)).sum()) == info[region]['provider_valid_centres']
    with rasterio.open(inputs / 'halo_membership_native_wgs84.tif') as membership:
        assert membership.transform == transform and membership.crs.to_epsg() == 4326
        assert membership.nodata is None and np.array_equal(membership.read(1), members['halo'].astype('uint8'))
    counts = Counter()
    lengths, sequences = defaultdict(lambda: defaultdict(float)), defaultdict(list)
    profiles = {p['probe_id']: p for p in read(inputs / 'path_ground_profiles.json')['probes']}
    with (inputs / 'path_class_samples.csv').open(encoding='utf-8', newline='') as stream:
        for r in csv.DictReader(stream):
            assert r['observed_ground_g'] == ''
            counts['path_native_samples'] += 1
            lon, lat = converter.transform(float(r['easting']), float(r['northing']))
            close(lon, float(r['longitude'])); close(lat, float(r['latitude']))
            # GDAL native index, independently of the evaluated containing-cell function.
            row, col = rasterio.transform.rowcol(transform, lon, lat)
            if row < 0 or col < 0 or row >= values.shape[0] or col >= values.shape[1]:
                assert r['state'] == 'outside' and not r['row'] and not r['column'] and not r['code']
                code = None
            else:
                assert int(r['row']) == row and int(r['column']) == col and int(r['code']) == values[row, col]
                code = int(values[row, col])
                state = 'nodata' if not mask[row, col] or code == 0 else 'reported_class' if code in names else 'unsupported_class'
                assert r['state'] == state
                code = code if state == 'reported_class' else None
            p = profiles[r['probe_id']]
            line = from_wkt(p['geometry_wkt_bng'])
            point = line.interpolate(float(r['chainage_m']))
            close(point.x, float(r['easting'])); close(point.y, float(r['northing']))
            weight = float(r['represented_length_m'])
            assert 0 < weight <= 2
            lengths[r['probe_id']][code] += weight
            sequences[r['probe_id']].append((float(r['chainage_m']), code))

    snapshots = []
    for s in manifest['osm_sources']:
        snapshots.append({e['id']: e for e in read(source / s['response_file'])['elements']} if s['response_file'] else None)
    start, end = snapshots
    document = read(inputs / 'barrier_candidates_bng.json')
    barriers = document['candidates']
    assert {c['osm_way_id'] for c in barriers} == set(end or {})
    assert document['catalogue_complete'] is None and document['acoustic_barrier_absence_established'] is False
    assert {r['osm_way_id'] for r in document['start_only_records']} == (set(start or {})-set(end or {}))
    consistency, categories, core_categories, halo_categories = Counter(), Counter(), Counter(), Counter()
    for candidate in barriers:
        e = end[candidate['osm_way_id']]
        assert candidate['tags'] == e['tags'] and candidate['version'] == e['version']
        assert candidate['edit_timestamp_utc'] == e['timestamp'] <= manifest['osm_sources'][1]['snapshot_utc']
        geographic = LineString([(p['lon'], p['lat']) for p in e['geometry']])
        assert from_wkt(candidate['geometry_wkt_wgs84']).equals_exact(geographic, 1e-12)
        geometry = geometry_transform(inverse, geographic)
        assert from_wkt(candidate['geometry_wkt_bng']).equals_exact(geometry, 1e-7)
        for name, bounds in [('halo', 'source_halo_bounds'), ('core', 'core_bounds')]:
            assert candidate['intersects_'+name] == geometry.intersects(box(*report['study'][bounds]))
        close(candidate['halo_length_m'], geometry.intersection(box(*report['study']['source_halo_bounds'])).length)
        tags = e['tags']
        category = ('explicit_noise_barrier_candidate' if tags.get('wall') == 'noise_barrier'
            else 'generic_wall_candidate' if tags.get('barrier') in ('wall', 'retaining_wall')
            else 'generic_fence_candidate' if tags.get('barrier') in ('fence', 'wire_fence')
            else 'embankment_candidate' if tags.get('man_made') == 'embankment'
            else 'other_barrier_tag_no_acoustic_classification')
        assert candidate['category'] == category
        raw = tags.get('height', '').strip()
        match = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)\s*m?', raw)
        value = float(match[1]) if match else None
        value = value if value is not None and math.isfinite(value) and value > 0 else None
        assert candidate['tagged_height_m'] == value
        counts['explicit_metre_height_tags'] += int(value is not None)
        for key in ('acoustic_screen_height_m', 'acoustic_top_elevation_odn_m', 'acoustic_absorption', 'opaque_and_sealed'):
            assert candidate[key] is None
        previous = start.get(e['id']) if start else None
        state = ('start_snapshot_unavailable' if start is None else
            'not_in_start_snapshot; creation/presence date unestablished' if previous is None else
            'matching_geometry_and_tags_at_both_snapshots; physical persistence unestablished'
            if previous['geometry'] == e['geometry'] and previous['tags'] == e['tags'] else
            'geometry_or_tags_differ_at_snapshots; physical change date unestablished')
        assert candidate['snapshot_consistency'] == state
        assert candidate['start_snapshot_version'] == (previous['version'] if previous else None)
        categories[category] += 1; consistency[state] += 1
        if candidate['intersects_halo']: halo_categories[category] += 1
        if candidate['intersects_core']: core_categories[category] += 1
    summary = report['summary']
    assert summary['barrier_candidate_categories'] == dict(categories)
    assert summary['halo_candidate_categories'] == dict(halo_categories)
    assert summary['core_candidate_categories'] == dict(core_categories)
    assert summary['snapshot_consistency_counts'] == dict(consistency)
    assert summary['explicit_metre_height_tags'] == counts['explicit_metre_height_tags']
    assert summary['qualified_acoustic_barriers'] == summary['qualified_observed_ground_g'] == 0
    assert not summary['noise_predictions_created'] and not report['eligible_for_absolute_noise_release']

    def independent_profile(line, step, dx=0., dy=0.):
        n = math.ceil(line.length/step)
        weights = defaultdict(float)
        codes = []
        for i in range(n):
            p = line.interpolate((i+.5)*line.length/n)
            lon, lat = converter.transform(p.x+dx, p.y+dy)
            row, col = rasterio.transform.rowcol(transform, lon, lat)
            code = int(values[row, col]) if 0 <= row < values.shape[0] and 0 <= col < values.shape[1] and mask[row, col] else None
            code = code if code in names else None
            weights[code] += line.length/n
            codes.append(code)
        return weights, codes
    for id_, p in profiles.items():
        assert p['observed_ground_g'] is p['qualified_propagation_ground_g'] is p['qualified_noise_prediction'] is None
        assert p['barrier_absence_established'] is False
        line = from_wkt(p['geometry_wkt_bng'])
        close(line.length, p['length_m'])
        base, codes = independent_profile(line, 2.)
        assert [code for _, code in sequences[id_]] == codes
        assert p['diagnostic_samples'] == len(codes)
        for i, (chainage, _) in enumerate(sequences[id_]):
            close(chainage, (i+.5)*line.length/len(codes))
        close(sum(lengths[id_].values()), line.length)
        for k, v in base.items(): close(v, lengths[id_][k])
        fine, _ = independent_profile(line, 1.)
        for key, expected in [('class_length_fractions', base), ('refined_1m_class_length_fractions', fine)]:
            actual = p[key]
            expected = {str(k) if k is not None else 'unknown': v/line.length for k, v in expected.items()}
            assert set(actual) == set(expected)
            for k, v in expected.items(): close(v, actual[k])
        close(p['refinement_total_variation'], .5*sum(abs(base.get(k,0)-fine.get(k,0)) for k in set(base)|set(fine))/line.length)
        for case in p['registration_stress_cases']:
            _, shifted = independent_profile(line, 2., case['dx_m'], case['dy_m'])
            close(case['changed_class_fraction'], sum(a!=b for a,b in zip(codes,shifted))/len(codes))
        ambiguous = sum(v for k, v in base.items() if k not in analogies)
        close(p['ambiguous_or_missing_length_m'], ambiguous)
        if ambiguous:
            assert p['conditional_analogy_g'] is None
        else:
            close(p['conditional_analogy_g'], sum(analogies[k]*v for k,v in base.items())/sum(base.values()))
        hits = sorted(c['osm_way_id'] for c in barriers if from_wkt(c['geometry_wkt_bng']).intersects(line))
        assert sorted(p['intersecting_candidate_way_ids']) == hits
        assert p['intersecting_candidate_categories'] == dict(Counter(c['category'] for c in barriers if c['osm_way_id'] in hits))
        counts['probe_profiles_checked'] += 1
    assert summary['geometry_probes'] == counts['probe_profiles_checked']
    assert summary['paths_with_fully_mapped_conditional_analogy'] == sum(p['conditional_analogy_g'] is not None for p in profiles.values())
    assert summary['probes_with_barrier_candidate_intersection'] == sum(bool(p['intersecting_candidate_way_ids']) for p in profiles.values())
    assert summary['probes_with_wall_candidate_intersection'] == sum(bool(p['intersecting_candidate_categories'].get('generic_wall_candidate')) for p in profiles.values())

    output.mkdir(parents=True)
    # Native categorical rendering, with ordinary candidates labelled as such.
    palette = {10:'#356c45',20:'#cba253',30:'#bdd596',40:'#e6c166',50:'#a88b83',60:'#ddd1b6',80:'#789dbd',90:'#5ca5a2'}
    fig, (ax, bars) = plt.subplots(1, 2, figsize=(13, 6), gridspec_kw={'width_ratios':[1.6,1]})
    core = report['study']['core_bounds']
    core_xy = np.array([(core[0],core[1]),(core[2],core[1]),(core[2],core[3]),(core[0],core[3])])
    clons, clats = converter.transform(core_xy[:,0], core_xy[:,1])
    rows, cols = np.nonzero(members['core'])
    r0,r1,c0,c1 = min(rows),max(rows)+1,min(cols),max(cols)+1
    small = values[r0:r1,c0:c1]
    codes = sorted(palette)
    indexed = np.zeros(small.shape,dtype='uint8')
    for i,code in enumerate(codes,1): indexed[small==code]=i
    ax.imshow(indexed, origin='upper', interpolation='nearest', cmap=ListedColormap(['#f4f4f4']+[palette[k] for k in codes]),
        vmin=0,vmax=len(codes),extent=(transform.c+c0*transform.a,transform.c+c1*transform.a,transform.f+r1*transform.e,transform.f+r0*transform.e))
    for p in profiles.values():
        g=geometry_transform(converter.transform,from_wkt(p['geometry_wkt_bng'])); ax.plot(*g.xy,color='#25282d',lw=.55,alpha=.5)
    colours={'generic_wall_candidate':'#c63a90','generic_fence_candidate':'#176477','other_barrier_tag_no_acoustic_classification':'#484d58','embankment_candidate':'#805cad'}
    for c in barriers:
        if c['intersects_core']:
            g=from_wkt(c['geometry_wkt_wgs84']); ax.plot(*g.xy,color=colours.get(c['category'],'#c63a90'),lw=1.4)
    ax.set_xlim(min(clons),max(clons));ax.set_ylim(min(clats),max(clats));ax.set_aspect(1/math.cos(math.radians(np.mean(clats))))
    ax.set_xlabel('Longitude (WGS84)');ax.set_ylabel('Latitude (WGS84)')
    ax.set_title('2021 native land cover: north-Heathrow core',loc='left')
    ticks=sorted(int(k) for k in info['core']['code_counts'])
    bars.barh([names[k] for k in ticks],[100*info['core']['code_counts'][str(k)]/info['core']['native_centres'] for k in ticks],color=[palette[k] for k in ticks])
    bars.set_xlabel('% of native pixel centres inside the core'); bars.set_title('Class support; acoustic G remains unknown',loc='left');bars.invert_yaxis()
    handles=[Line2D([0],[0],color='#25282d',lw=.8,label='100 geometry probes'),
             Line2D([0],[0],color=colours['generic_fence_candidate'],lw=1.4,label='Historical fence candidates'),
             Line2D([0],[0],color=colours['generic_wall_candidate'],lw=1.4,label='Historical wall candidate')]
    ax.legend(handles=handles,loc='lower left',fontsize=8,framealpha=.95)
    fig.text(.04,.035,'Land-cover classes are not measured absorption. Candidate lines are not qualified noise barriers.\nESA WorldCover 2021 v200 / modified Copernicus Sentinel data; © OpenStreetMap contributors (ODbL).',fontsize=9)
    fig.tight_layout(rect=[0,.11,1,1]);fig.savefig(output/'ground_cover_and_candidates.png',dpi=170);plt.close(fig)
    receipt=dict(schema_version=1,status='passed_original_data_and_export_verification',input_inventory_sha256=input_sha,
        parent_inventory_sha256=report['parent_inventory_sha256'], native_pixels_compared=int(values.size),
        native_masks_compared=int(mask.size), counts=dict(counts), barrier_records_checked=len(barriers),
        scientifically_validated_noise=False, local_landcover_accuracy_validated=False,
        registry_coordinate_transform_independently_validated=False,
        verifier_code_sha256=file_hash(Path(__file__)),
        runtime=dict(python=sys.version,numpy=np.__version__,rasterio=rasterio.__version__,pyproj=pyproj.__version__))
    (output/'verification.json').write_bytes(json_bytes(receipt))
    shutil.copyfile(Path(__file__),output/Path(__file__).name)
    assert check_generation(inputs)==input_sha
    assert all(check_generation(p)==report['parent_inventory_sha256'][n] for n,p in parents.items())
    files={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity='ground-cover-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity,files=files)))
    print(json.dumps(dict(generation_id=identity,verification=receipt)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',default=str(ROOT/'artifacts/ground_cover_inputs_v3'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/ground_cover_review_v3'))
    args=parser.parse_args()
    run(args.inputs,args.output)
