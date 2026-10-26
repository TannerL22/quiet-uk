"""Resolve declared physical-input candidates and sensitivity over retained paths."""
import argparse
from collections import Counter
from contextlib import ExitStack
import csv
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from shapely import from_wkt
from shapely.geometry import box
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.physical_scene_assumptions import (POLICY_ID, absolute_surface_summary,
    assumption_cases, feature_top, materialize_profile, pavement_spectrum)
from quiet_uk.road_scene_builder import NativeTerrain, parts
spec = importlib.util.spec_from_file_location('inventory_helper', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


def read(path): return json.loads(Path(path).read_bytes())


def rows(path):
    with Path(path).open(encoding='utf-8', newline='') as f: return list(csv.DictReader(f))


def describe(values):
    return dict(n=len(values), **{p:float(v) for p,v in zip(('min','p10','median','p90','max'), np.percentile(values,[0,10,50,90,100]))}) if values else dict(n=0)


def building_summary(row, datasets):
    geometry = from_wkt(row['geometry_wkt_bng']); dtm,dsm,dtm_codes,dsm_codes = datasets
    bounds = box(*dtm.bounds); inside = bounds.covers(geometry)
    clipped = geometry.intersection(bounds)
    empty = dict(paired_pixels=0, surface_odn_m=None, difference_agl_m=None,
                 footprint_native_terrain_pixels=0, reference_base_odn_m=None)
    if clipped.is_empty: return empty
    raw = from_bounds(*clipped.bounds, transform=dtm.transform)
    left, top = max(0, math.floor(raw.col_off)), max(0, math.floor(raw.row_off))
    right, bottom = min(dtm.width, math.ceil(raw.col_off+raw.width)), min(dtm.height, math.ceil(raw.row_off+raw.height))
    if right <= left or bottom <= top: return empty
    window = Window(left,top,right-left,bottom-top); transform = dtm.window_transform(window)
    shape = (bottom-top,right-left)
    footprint = rasterize([(geometry,1)], out_shape=shape, transform=transform, fill=0, dtype='uint8', all_touched=False).astype(bool)
    inward = geometry.buffer(-2.)
    interior = rasterize([(inward,1)], out_shape=shape, transform=transform, fill=0, dtype='uint8', all_touched=False).astype(bool) if not inward.is_empty else np.zeros(shape,dtype=bool)
    result = absolute_surface_summary(dtm.read(1, window=window), dsm.read(1, window=window),
        dtm.read_masks(1, window=window), dsm.read_masks(1, window=window),
        dtm_codes.read(1,window=window),dsm_codes.read(1,window=window),interior,footprint)
    if not inside: result['reference_base_odn_m'] = None
    if row['status'] == 'provisional_surface_height':
        assert inside and result['paired_pixels'] == int(row['paired_pixels'])
        for key,field in (('p10','sample_p10_m'),('p50','sample_median_m'),('p90','sample_p90_m')):
            assert math.isclose(result['difference_agl_m'][key], float(row[field]), abs_tol=1e-5)
    return result


def road_context(source, tree, ways, buffers):
    line = from_wkt(source['geometry_wkt_bng']); candidates = []
    for i in sorted(tree.query(line,predicate='intersects')):
        way = ways[int(i)]; length = line.intersection(buffers[int(i)]).length
        if length <= 0: continue
        candidates.append(dict(osm_way_id=way['way_id'], version=way['version'], tags=way['tags'],
            source_length_within_3m_m=length, geometry_identity='proximity_only_not_established',
            historical_transfer='2021 OSM versus 2026 generalised road geometry unestablished'))
    positive = [c for c in candidates if any(c['tags'].get(k, 'no') not in ('no','false','0','') for k in ('bridge','tunnel'))]
    return dict(status='withheld_structure_proximity_conflict' if positive else 'conditional_at_grade_reference',
                positive_structure_context=positive, nearby_surface_tags=sorted(set(c['tags']['surface'] for c in candidates if 'surface' in c['tags'])),
                source_surface_odn_m=None, qualified_deck_odn_m=None,
                candidate_policy='DTM + 0.05 m only when no positive nearby bridge/tunnel tag; absence of a tag does not prove at-grade',
                search='3 m geometry corridor intersection, any positive source length; context only, no road identity match',
                applied_gradient_and_junction_correction_db=0., observed_gradient_and_junction_correction_db=None,
                source_dynamics_assumption='flat steady reference, no junction acceleration; not observed local dynamics',
                actual_pavement=None, assumed_source_temperature_c=20., actual_source_temperature_c=None)


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Fresh workspace generation required')
    names = ('physical_scene_inputs_v2','ground_cover_inputs_v3','real_road_scenes_v2',
             'road_direction_inputs_v2','road_operating_inputs_v2','road_emission_assumptions_v2')
    parents = {n:ROOT/'artifacts'/n for n in names}
    for path in parents.values(): helper.verified(path)
    hashes = {n:file_hash(p/'inventory.json') for n,p in parents.items()}
    physical, ground, scenes, direction, operating, emission = (parents[n] for n in names)
    output.mkdir(parents=True)
    cases = assumption_cases(); features = {}; counts = Counter()
    with ExitStack() as stack:
        datasets = [stack.enter_context(rasterio.open(physical/n)) for n in
                    ('terrain_odn_2m.tif','surface_odn_2m.tif','dtm_survey_code_2m.tif','dsm_survey_code_2m.tif')]
        for i,row in enumerate(rows(physical/'building_heights_bng.csv')):
            identity = 'os:'+row['source_id']+':'+row['source_record_number']
            features[identity] = dict(feature_id=identity,kind='building',original_height_status=row['status'],
                original_withheld_reason=row['withheld_reason'] or None,
                original_provisional_height_agl_m=float(row['provisional_height_agl_m']) if row['provisional_height_agl_m'] else None,
                original_height_uncertainty_m=None, acoustic_obstacle_height_m=None, qualified_top_elevation_odn_m=None,
                historical_compatibility=row['historical_footprint_compatibility'], native_summary=building_summary(row,datasets),
                original_row_reference='physical_scene_inputs_v2/building_heights_bng.csv',
                interpretation='Paired absolute DSM sample percentiles under a global flat-top assumption; last returns can include vegetation and non-roof structures. Not roof/eaves measurements or confidence intervals.')
            counts['buildings'] += 1; counts['building_'+row['status']] += 1
            if (i+1)%10000 == 0: print(f'Native absolute-height summaries {i+1}',flush=True)
        dtm = datasets[0]; terrain = NativeTerrain(dtm.read(1),dtm.read_masks(1),tuple(dtm.bounds))
    barriers = read(ground/'barrier_candidates_bng.json')['candidates']
    for row in barriers:
        geometry = from_wkt(row['geometry_wkt_bng']); values = []
        for part in parts(geometry, 'LineString'):
            for a,b in zip(part.coords,list(part.coords)[1:]):
                if a == b: continue
                values.extend(z for *_,z in terrain.intervals(a,b) if z is not None)
        complete = box(*terrain.bounds).covers(geometry)
        identity = 'osm:'+str(row['osm_way_id'])
        features[identity] = dict(feature_id=identity,kind='barrier',category=row['category'],tagged_height_m=row['tagged_height_m'],
            reference_base_odn_m=max(values) if values and complete else None,
            native_base_definition='Maximum containing-cell DTM along all retained line segments; a global horizontal-screen reference',
            snapshot_consistency=row['snapshot_consistency'], qualified_top_elevation_odn_m=None,
            acoustic_screen_height_m=None, opaque_and_sealed=None, acoustic_absorption=None,
            interpretation='Tag height is unverified. Generic fences only screen in the declared solid-fence stress case. Embankments are not added again above terrain.')
        counts['barriers'] += 1; counts[row['category']] += 1
    (output/'physical_features.json').write_bytes(json_bytes(dict(policy_id=POLICY_ID,features=features,
        percentile_interpretation='Sample spread only, not confidence limits; use the same absolute candidate top for every path')))
    osm = read(direction/'osm_way_evidence_bng.json')['ways']
    ways = [w for w in osm if any(k in w['tags'] for k in ('bridge','tunnel','surface'))]
    buffers = [from_wkt(w['geometry_wkt_bng']).buffer(3.) for w in ways]; tree = STRtree(buffers)
    sources = read(scenes/'source_lines.json')['source_roads']
    roads = {r['link_id']:r for r in read(operating/'road_operating_inputs.json')['roads']}
    coefficients = read(emission/'published_2021_coefficients.json'); source_records = {}; spreads = []
    for source in sources:
        link = source['link_id']; context = road_context(source,tree,ways,buffers)
        context['original_road_structure'] = ''  # All retained original ledger rows are checked below.
        pavement = [pavement_spectrum(roads[link],coefficients,s) for s in ('Reference road surface','Thin layer A','Worked surface')]
        for r in pavement:
            if r['period_spectra']:
                r['delta_from_reference_source_1000hz_db'] = {p:r['period_spectra'][p]['sound_power_db_re_1e_minus_12_W_per_m'][4]-roads[link]['conditional_baseline']['period_spectra'][p]['sound_power_db_re_1e_minus_12_W_per_m'][4] for p in r['period_spectra']}
                if r['surface'] == 'Reference road surface':
                    for p,v in r['period_spectra'].items():
                        np.testing.assert_allclose(v['relative_power_per_m'],roads[link]['conditional_baseline']['period_spectra'][p]['relative_power_per_m'],rtol=1e-12)
            counts['pavement_'+r['status']] += 1
        numeric = [r['period_spectra']['den_penalised_source']['sound_power_db_re_1e_minus_12_W_per_m'][4] for r in pavement if r['period_spectra']]
        spread = max(numeric)-min(numeric) if numeric else None
        if spread is not None: spreads.append(spread)
        source_records[link] = dict(link_id=link, physical_source=context,pavement_cases=pavement,
            source_surface_scenario_spread_1000hz_db=spread,receiver_noise_db=None,
            interpretation='Per-source 1 kHz band only; unsupported surfaces remain held. Not receiver noise or a bound.')
        counts['source_'+context['status']] += 1
    ledger = {r['link_id']:r for r in rows(scenes/'road_input_inventory_bng.csv')}
    assert set(source_records) == set(roads)
    assert all(ledger[link]['road_structure'] == '' for link in source_records)
    (output/'source_physical_inputs.json').write_bytes(json_bytes(dict(roads=source_records,combined_road_noise_db=None)))
    profile_counts = Counter(); blocked = Counter(); changes = Counter(); profile_summaries = []
    with gzip.open(scenes/'prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for index,line in enumerate(stream):
            record = json.loads(line); link = record['link_id']; geographic = record['scene']
            profiles = [('direct',geographic['direct'],None)]
            profiles += [(f'reflection:{i}',r['profile'],r) for i,r in enumerate(geographic['reflections'])]
            for identity,profile,reflection in profiles:
                diagnostics = []
                for case in cases:
                    result = materialize_profile(profile,features,case,
                        source_policy=source_records[link]['physical_source'],endpoint_ids=geographic['endpoint_footprint_ids'],
                        reflecting_feature_ids=reflection['coincident_feature_ids'] if reflection else (),
                        reflection_x_m=reflection['wall']['unfolded_x_m'] if reflection else None)
                    wall = None
                    if reflection:
                        candidates = [feature_top(features[f],case)[0] for f in reflection['coincident_feature_ids']]
                        top = candidates[0] if candidates and all(v == candidates[0] for v in candidates) else None
                        wall = dict(reflection['wall'],top_z_m=top, slope_from_vertical_deg=0.,
                                    absorption_by_band=None, incident_view_height_m=None,incident_view_width_m=None,
                                    unfolded_geometry_confirmed=None,qualification='Conditional vertical facade; incident view and coincident identity still unresolved')
                        if top is None: result['geometry_blockers'].append('missing_or_ambiguous_reflecting_wall_top')
                        if top is not None and wall['base_z_m'] is not None and top <= wall['base_z_m']:
                            result['geometry_blockers'].append('reflecting_top_below_native_base')
                    if result['geometry_blockers']: result['status'] = 'withheld_geometry_candidate'
                    result.pop('candidate_scene')  # Materializer reconstructs exact numeric overlay from sealed parent; avoid duplicating native terrain fifteen times.
                    result['candidate_ground'].pop('segments')  # Summary persists; exact segments are reconstructed by the materializer.
                    result['case_id'] = case['case_id']; result['candidate_wall'] = wall
                    diagnostics.append(result)
                    profile_counts[(case['case_id'],result['status'])] += 1
                    blocked.update((case['case_id'],b) for b in set(result['geometry_blockers']))
                baseline = diagnostics[0]['geometry_diagnostic']['obstacles_above_endpoint_sightline']
                for result in diagnostics[1:]:
                    if result['geometry_diagnostic']['obstacles_above_endpoint_sightline'] != baseline:
                        changes[result['case_id']] += 1
                profile_summaries.append(dict(link_id=link,profile_id=identity,source_point_chainage_m=record['source_point_chainage_m'],
                    length_m=profile['length_m'],case_results=diagnostics,
                    parent_profile_reference='real_road_scenes_v2/prepared_paths.jsonl.gz by link_id/profile_id',
                    observed_ground_g=None, qualified_source_elevation_odn_m=None, qualified_obstacle_tops=None,
                    reflection_support_complete=False, acoustic_path_support_complete=False,receiver_noise_db=None))
            if (index+1)%100 == 0: print(f'Physical scenarios for {index+1}/{len(sources)} source snapshots',flush=True)
    (output/'profile_sensitivity.json').write_bytes(json_bytes(dict(profiles=profile_summaries,
        status='conditional_input_and_sightline_diagnostics_only',receiver_noise_db=None)))
    with (output/'profile_sensitivity.csv').open('w',encoding='utf-8',newline='') as f:
        fields=['link_id','profile_id','case_id','status','candidate_obstacles','obstacles_above_endpoint_sightline','maximum_obstacle_clearance_m','maximum_terrain_clearance_m','geometry_blockers','receiver_noise_db']
        writer = csv.DictWriter(f,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
        for p in profile_summaries:
            for r in p['case_results']:writer.writerow({**p,**r,**r['geometry_diagnostic'],'geometry_blockers':';'.join(sorted(set(r['geometry_blockers'])))})
    protocol = dict(policy_id=POLICY_ID,cases=cases,
        baseline='Numerical reference, not most likely truth: conditional cover G, ambiguous substrate G=0.5; accepted paired DSM absolute median flat tops; unresolved building heights held; unverified screen effects omitted explicitly; hard roof/source G=0; at-grade source only without positive nearby bridge/tunnel context.',
        geometry_assumptions=['2021_2026_footprint_and_road_compatibility','flat_candidate_building_tops','opaque_buildings','vertical_facades','infinite_lateral_extent_profile_comparison','native_midpoint_piecewise_linear_terrain','2m_midpoint_native_landcover_support','outdoor_4m_receiver'],
        missing_height_references_m=[3,6,12], ambiguous_ground_reference_g=.5,
        roof_percentile_interpretation='Absolute DSM p10/p50/p90 sample spread, not a confidence interval or observed roof height',
        barrier_policy='Baseline omits unverified screen effects by declaration, not by assuming no barriers. Walls use unverified tagged heights or 1/3/5m references. Fences are solid only in a stress case. Unknown types and embankments are omitted, with terrain embankments not duplicated.',
        pavement_policy='Reference/Thin layer A/Worked surface from retained 2021 F-4; enforce per-class speed domains; OSM asphalt does not determine F-4 code, age, condition or acoustic absorption.',
        source_temperature_and_dynamics='20 C, flat, dry, steady, no junction acceleration or studded tyres; zero applied corrections are reference assumptions, actual corrections remain unknown.',
        numeric_scene_materialization='physical_scene_assumptions.materialize_profile with sealed parent profile, feature table, source policy and case. scene_kind remains conditional_real_input; current kernels reject it.',
        sensitivity_interpretation='One-factor comparisons and one combined reference; engineering scenarios, not statistical confidence, extrema or guaranteed noise bounds.',
        remaining_acoustic_work=['explicit_conditional_real_scene_adapter','reflection_incident_view_absorption_and_search_completeness','unsupported_finite_width_obstacle_configurations','complete_source_line_quadrature','complete_road_network_and_other_noise_sources'],
        no_real_input_relabelled_synthetic=True, absolute_release_eligible=False)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    files = ['src/quiet_uk/physical_scene_assumptions.py','scripts/87_resolve_physical_scene_assumptions.py']
    report = dict(status='physical_scene_assumption_policy_and_materializer_complete',counts=dict(counts),profiles=len(profile_summaries),
        physical_cases=len(cases), profile_case_counts={case['case_id']:{s:profile_counts[(case['case_id'],s)] for s in ('conditional_geometry_candidate','withheld_geometry_candidate')} for case in cases},
        profile_blocker_counts={case['case_id']:{b:n for (c,b),n in blocked.items() if c == case['case_id']} for case in cases},
        sightline_obstacle_count_changed_profiles=dict(changes), pavement_source_spread_1000hz_db=describe(spreads),
        parent_inventory_sha256=hashes,code_sha256={n:file_hash(ROOT/n) for n in files},
        licences=dict(terrain='Environment Agency OGL v3',buildings_roads='Ordnance Survey OGL v3',traffic='DfT OGL v3',landcover='ESA WorldCover CC BY 4.0',osm='OpenStreetMap contributors ODbL'),
        receiver_noise_db=None, combined_road_noise_db=None, real_area_noise_validated=False,external_engine_executed=False,full_current_method_qualified=False,eligible_for_absolute_noise_release=False)
    (output/'report.json').write_bytes(json_bytes(report))
    for n in files:
        dst=output/'code'/n;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dst)
    for n,path in parents.items():helper.verified(path);assert file_hash(path/'inventory.json') == hashes[n]
    print(dict(generation_id=helper.seal(output,'physical-scene-assumptions-'),counts=report['counts'],profiles=report['profiles']),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default=str(ROOT/'artifacts/physical_scene_assumptions_v2'))
    run(parser.parse_args().output)
