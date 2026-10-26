"""Build traceable conditional operating inputs from retained original evidence."""
import argparse
from collections import Counter
import csv
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
import pyproj
import tzdata

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.favourable_propagation import atmospheric_absorption
from quiet_uk.road_emission import aggregate_components, survey_shape_check
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_operating_inputs import (FALLBACK_AIR, LONDON_TZIF, choose_speeds,
    absorption_air_parameters, hourly_weather, joint_calendar_profiles, operating_spectrum, parse_isd_record, wind_sector_diagnostics)
from quiet_uk.road_temporal import audit_temporal_records

spec = importlib.util.spec_from_file_location('inventory_helper', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)


def read(path): return json.loads(Path(path).read_bytes())


def describe(values):
    return dict(n=len(values), **{k: float(v) for k, v in zip(('min','p10','median','p90','max'),
                np.percentile(values, [0,10,50,90,100]))}) if values else dict(n=0)


def run(output, evidence):
    output, evidence = Path(output).resolve(), Path(evidence).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    names = ('real_road_scenes_v2','road_direction_inputs_v2','road_temporal_audit_v2','road_emission_assumptions_v2')
    parents = {n: ROOT/'artifacts'/n for n in names}; parents[evidence.name] = evidence
    for p in parents.values(): helper.verified(p)
    hashes = {n: file_hash(p/'inventory.json') for n, p in parents.items()}
    manifest = read(evidence/'manifest.json')
    for n, expected in manifest['parent_inventory_sha256'].items():
        if hashes[n] != expected: raise ValueError('Evidence has different parents')
    scenes, direction, temporal, emission = (parents[n] for n in names)
    candidates = read(temporal/'national_2021_assumption_candidates.json')
    if candidates['reference_year'] != 2021: raise ValueError('Wrong national reference year')
    partitions = {p['count_point_id']: p for p in read(direction/'donor_partitions.json')['points']}
    existing = read(temporal/'local_survey_day_inputs.json')['points']
    new_ids = set(manifest['selected_additional_donors'])
    originals = [p['reference_all_direction_aadf'] for i, p in partitions.items() if i in new_ids]
    raw = [r for p in sorted(evidence.glob('raw-counts-page-*.json')) for r in read(p)['data']]
    extra = audit_temporal_records(originals, raw, [r for i in new_ids for r in partitions[i]['original_records']])
    surveys = {p['count_point_id']: p for p in existing + extra}
    if set(partitions)-set(surveys): raise ValueError('Unexamined directional donor')
    compressed = evidence/'heathrow-2021.gz'
    with gzip.open(compressed, 'rb') as stream:
        body = stream.read(64*1024**2+1)
    if len(body) > 64*1024**2: raise ValueError('Uncompressed weather budget exceeded')
    raw_weather = body.decode('ascii').splitlines()
    observations = [parse_isd_record(line, i+1) for i, line in enumerate(raw_weather)]
    weather = hourly_weather(observations)
    calendars = {key: joint_calendar_profiles(candidates, weather, multiplier)
                 for key, multiplier in (('national_vehicle', 1.), ('night_half', .5), ('night_double', 2.))}
    freeflow = read(emission/'conditional_national_free_flow_candidates.json')['rows']
    coefficients = read(emission/'published_2021_coefficients.json')
    source_roads = read(scenes/'source_lines.json')['source_roads']
    with (direction/'road_representation_ledger_bng.csv').open(encoding='utf-8', newline='') as f:
        ledger = {r['link_id']: r for r in csv.DictReader(f)}
    output.mkdir(parents=True)
    (output/'London.tzif').write_bytes(LONDON_TZIF)
    (output/'hourly_weather.json').write_bytes(json_bytes(dict(reference_year=2021, rows=weather,
        clock='One elapsed UTC hour per slot, Europe/London civil periods; exact time offsets/folds retained',
        source='Historical ISD 037720-99999; original quality flags and pressure kind retained',
        humidity_formula='100*exp(17.67*Td/(Td+243.5)-17.67*T/(T+243.5)); water approximation',
        site_to_path_transfer='Assumed uniform path air; airport observations do not observe each road path',
        missing_policy='Original observation remains null; gap-reference values exist only in candidate_absorption_air')))
    (output/'traffic_weather_calendars.json').write_bytes(json_bytes(dict(reference_year=2021,
        slot_reference='hourly_weather.json row order, all 8760 slots including missing reports', calendars=calendars)))
    (output/'additional_donor_surveys.json').write_bytes(json_bytes(dict(points=extra,
        interpretation='One-day 07-19 surveys, not annual night flows or typical speeds')))
    used_donors = {r['traffic_budget']['count_point_id'] for r in source_roads}
    shape = [x for i in sorted(used_donors, key=int) for x in survey_shape_check(surveys[i], candidates)]
    (output/'survey_profile_checks.json').write_bytes(json_bytes(dict(diagnostics=shape,
        permitted_use='Daytime shape check only; no annual night validation or fitting',
        national_total_variation=describe([x['national_total_variation_distance'] for x in shape]),
        uniform_total_variation=describe([x['uniform_total_variation_distance'] for x in shape]))))
    records, assignment_counts, case_counts, spreads = [], Counter(), Counter(), []
    for source in source_roads:
        link, budget = source['link_id'], source['traffic_budget']
        representations = [r for r in budget['representations'] if r['link_id'] == link]
        if len(representations) != 1: raise ValueError('Nonunique physical representation')
        components = representations[0]['components']
        categories = aggregate_components(components)
        donor = budget['count_point_id']; p = partitions[donor]
        speeds = choose_speeds(ledger[link], p['reference_all_direction_aadf']['road_name'], freeflow)
        speedmap = {k: v['candidate_kmh'] for k, v in speeds['choices'].items()}
        assignment_counts['motorway_transfer_roads' if speeds['motorway_identity_candidate'] else 'unknown_road_speed_reference_roads'] += 1
        for choice in speeds['choices'].values(): assignment_counts[choice['basis']] += 1
        definitions = [('baseline','national_vehicle',speedmap)]
        definitions += [(f'common_speed_{s}','national_vehicle', dict.fromkeys(VEHICLE_FIELDS, float(s))) for s in (20,40,70,100)]
        definitions += [('night_half','night_half',speedmap), ('night_double','night_double',speedmap)]
        cases = []
        for identity, calendar, case_speeds in definitions:
            result = operating_spectrum(categories, calendars[calendar]['vehicle_profiles'], case_speeds, coefficients)
            case_counts[result['status']] += 1
            # All original budgets are usable under declared assumptions; unknown categories would remain held.
            cases.append(dict(case_id=identity, calendar_id=calendar, candidate_speeds_kmh=case_speeds,
                              status=result['status'], period_spectra=result['period_spectra']))
            if identity == 'baseline': baseline = result
        numeric = [c['period_spectra']['den_penalised_source']['sound_power_db_re_1e_minus_12_W_per_m'][4]
                   for c in cases if c['period_spectra'] is not None]
        numeric = [v for v in numeric if v is not None]
        spread = max(numeric)-min(numeric) if numeric else None
        if spread is not None: spreads.append(spread)
        has_survey = any(s['complete_published_07_19_support'] and s['annual_input_identity_compatible']
                         for s in surveys[donor]['survey_days'])
        records.append(dict(link_id=link, group_id=source['group_id'], count_point_id=donor,
            original_source_emission_id=source['source_emission_id'], original_traffic_components=components,
            annual_categories=categories, aadf_quality=p['reference_all_direction_aadf']['estimation_method'],
            source_cell_ids=[c['segment_id'] for c in source['source_cells']],
            observed_survey_day_support=has_survey, observed_local_period_flows=None, observed_local_speed_kmh=None,
            traffic_status='conditional_annual_national_timing_transfer', speed=speeds,
            conditional_baseline=baseline, conditional_cases=cases,
            source_scenario_spread_1000hz_db=spread, spread_interpretation='Engineering scenario spread; not a confidence interval or guaranteed bound',
            weather_reference='hourly_weather.json; joint traffic weights in traffic_weather_calendars.json',
            favourable_probability_cases=[0., .5, 1.], qualified_favourable_probability=None,
            receiver_noise_db=None, release_status='withheld_unqualified_physical_scene_and_incomplete_network',
            unresolved=['constant_flow_link_transfer','2021_2026_geometry_compatibility','source_deck_gradient_junction',
                'actual_pavement_and_source_temperature_correction','local_observed_annual_timing_and_speed',
                'path_air_spatial_transfer_and_gap_references','vertical_weather_stability_and_favourable_frequency',
                'ground_obstacle_reflection_qualification','missing_source_roads']))
    (output/'road_operating_inputs.json').write_bytes(json_bytes(dict(reference_year=2021, roads=records,
        status='ready_for_explicit_conditional_research', combined_road_noise_db=None,
        original_scene_inventory_sha256=hashes['real_road_scenes_v2'])))
    # Compact table for reviewing data versus assumptions road by road.
    with (output/'road_operating_inputs.csv').open('w', encoding='utf-8', newline='') as f:
        fields=['link_id','count_point_id','aadf_quality','observed_survey_day_support','traffic_status',
                'speed_basis','car_candidate_speed_kmh','hgv_2_axle_candidate_speed_kmh','bus_candidate_speed_kmh',
                'source_scenario_spread_1000hz_db','observed_local_speed_kmh','receiver_noise_db','release_status']
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore'); writer.writeheader()
        for r in records:
            choices = r['speed']['choices']
            writer.writerow(dict(r, speed_basis=choices['cars_and_taxis']['basis'],
                car_candidate_speed_kmh=choices['cars_and_taxis']['candidate_kmh'],
                hgv_2_axle_candidate_speed_kmh=choices['HGVs_2_rigid_axle']['candidate_kmh'],
                bus_candidate_speed_kmh=choices['buses_and_coaches']['candidate_kmh']))
    winds = {str(b): wind_sector_diagnostics(weather, b) for b in range(0,360,15)}
    (output/'station_wind_sectors.json').write_bytes(json_bytes(dict(bearings=winds,
        bearing_basis='source to receiver, clockwise from true north; BNG grid bearing must first be transformed',
        half_angle_degrees=60., minimum_speed_m_s=.5,
        interpretation='Station windy-sector membership only; no vertical thermal/wind gradient or CNOSSOS favourable probability')))
    air_stats = {}
    for period in ('day','evening','night'):
        subset = [r for r in weather if r['period'] == period]
        original_full = [r for r in subset if r['candidate_absorption_air']['status'] == 'station_derived_uniform_path_assumption']
        air_stats[period] = dict(expected_hours=len(subset), station_reports_selected=sum(r['chosen_observation'] is not None for r in subset),
            strict_joint_air_hours=len(original_full), candidate_fields={k:describe([r['candidate_absorption_air'][k] for r in subset]) for k in FALLBACK_AIR},
            gap_field_counts={k:sum('reference' in r['candidate_absorption_air']['field_basis'][k] for r in subset) for k in FALLBACK_AIR})
    # Air-only illustration uses a single 1 km homogeneous path, not actual roads.
    absorption = []
    for period in ('day','evening','night'):
        subset = [r for r in weather if r['period'] == period]
        for k, nominal in enumerate(coefficients['frequencies_hz']):
            exact = 1000*10**(.3*(k-4))
            values = [atmospheric_absorption(exact, **absorption_air_parameters(r, accept_uniform_station_transfer=True)) for r in subset]
            if any(v['status'] == 'withheld' for v in values): raise ValueError('Unsupported declared candidate air')
            alphas = [v['alpha_db_per_km'] for v in values]
            weights = [calendars['national_vehicle']['groups']['All motor vehicles']['slot_weights'][i]
                       for i, r in enumerate(weather) if r['period'] == period]
            norm = math.fsum(weights)
            transmission = math.fsum(w*10**(-a/10) for w,a in zip(weights,alphas))/norm
            absorption.append(dict(period=period, nominal_label_hz=nominal, absorption_frequency_hz=exact,
                frequency_policy='exact_base10', diagnostic_distance_m=1000,
                traffic_weighted_air_only_attenuation_db=-10*math.log10(transmission), coefficient_distribution=describe(alphas),
                status='conditional_air_only_diagnostic', receiver_noise_db=None))
    (output/'air_absorption_diagnostics.json').write_bytes(json_bytes(dict(cases=absorption,
        interpretation='Joint hourly energy average using all-motor national timing; not mean-air absorption, source-category weighting or a real receiver level')))
    with (evidence/'isd-history.csv').open(encoding='utf-8', newline='') as f:
        station = [r for r in csv.DictReader(f) if r['USAF']=='037720' and r['WBAN']=='99999']
    if len(station)!=1: raise ValueError('Nonunique station metadata')
    locations = Counter(tuple(o['observation_location'].values()) for o in observations)
    geod = pyproj.Geod(ellps='WGS84')
    metadata_lat, metadata_lon = float(station[0]['LAT']), float(station[0]['LON'])
    location_rows = [dict(latitude=lat, longitude=lon, elevation_m=z, reports=n,
        distance_from_current_station_metadata_m=geod.inv(metadata_lon,metadata_lat,lon,lat)[2])
        for (lat,lon,z),n in sorted(locations.items())]
    protocol = dict(reference_year=2021, baseline_timing='Exact-year national vehicle curves aligned with elapsed hourly weather; proxy bus/motorcycles; no seasonal traffic factors',
        baseline_speed='Identity-compatible M-road collapsed/dual mainlines: national per-vehicle motorway free-flow candidates; missing bus=70 km/h. Other roads, ramps, mismatch: 40 km/h explicit reference',
        speed_comparisons_kmh=[20,40,70,100], night_weight_comparisons=[.5,1.,2.],
        source_surface='Reference surface assumption; actual pavement unknown', source_temperature_c=20.,
        source_temperature_correction='Not applied; actual ambient correction remains unqualified',
        class_assumptions='Bus category 2; powered two wheelers 4b; 2-axle rigid HGV category 2 with twin rear tyres assumed',
        weather_hour_selection='Nearest-hour bins, complete strict-QC air tuple preferred, then offset, SYNOP, source row; one unsplit report per slot',
        accepted_quality_codes=['1','5'], excluded_quality='All other QC codes retained but not accepted, including gross-limit-only and suspect values',
        gap_air_reference=FALLBACK_AIR, pressure_kind='MA1 station pressure; never SLP or altimeter',
        humidity='Derived water-Magnus RH; no clipping supersaturation; approximation and spatial transfer explicit',
        uniform_path_air=True, favourable_probability_reference=.5, favourable_probability_comparisons=[0.,.5,1.],
        probability_status='Declared comparison assumptions; wind-sector frequency cannot qualify thermal/stability propagation',
        missing_road_flows='13,206 roads stay without source budgets; no class-median/default flow',
        original_scene_generation_unchanged=True, annual_receiver_noise=None, scenario_range_is_statistical_interval=False)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    shutil.copyfile(scenes/'road_input_inventory_bng.csv', output/'road_input_inventory_bng.csv')
    code_paths = ['src/quiet_uk/road_operating_inputs.py','scripts/85_resolve_road_operating_inputs.py','pyproject.toml']
    report = dict(status='resolved_conditional_operating_inputs_observation_gaps_explicit',
        summary=dict(retained_source_roads=len(records), traffic_donors=len(used_donors), additional_raw_count_rows=len(raw),
            additional_examined_donors=len(extra), additional_donors_with_surveys=sum(bool(p['survey_days']) for p in extra),
            retained_donors_with_complete_surveys=len(shape), retained_roads_with_survey_support=sum(r['observed_survey_day_support'] for r in records),
            original_weather_reports=len(observations), expected_weather_hours=len(weather),
            weather_hours_without_reports=sum(r['chosen_observation'] is None for r in weather),
            strict_joint_station_air_hours=sum(r['candidate_absorption_air']['status']=='station_derived_uniform_path_assumption' for r in weather),
            unbudgeted_roads=len(ledger)-len(records), conditional_source_cases=sum(case_counts.values())),
        speed_assignment_counts=dict(assignment_counts), source_case_states=dict(case_counts),
        source_scenario_spread_1000hz_db=describe(spreads), air_coverage=air_stats,
        station_metadata=station[0], observation_location_variants=location_rows,
        station_spatial_compatibility='Different coordinates in SYNOP/METAR headers and current station history; retained, not co-located or averaged. Path-air transfer remains assumed.',
        tzdata_version=tzdata.__version__, London_tzif_sha256=hashlib.sha256(LONDON_TZIF).hexdigest(),
        parent_inventory_sha256=hashes, code_sha256={n:file_hash(ROOT/n) for n in code_paths},
        qualified_local_annual_period_flows=0, qualified_local_typical_speeds=0, qualified_favourable_probabilities=0,
        all_original_source_budgets_preserved=True, eligible_for_absolute_noise_release=False,
        full_current_method_qualified=False, external_engine_executed=False, real_area_noise_validated=False)
    (output/'report.json').write_bytes(json_bytes(report))
    for n in code_paths:
        target=output/'code'/n; target.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(ROOT/n,target)
    for n,p in parents.items(): helper.verified(p); assert file_hash(p/'inventory.json')==hashes[n]
    print(dict(generation_id=helper.seal(output,'road-operating-inputs-'),summary=report['summary']),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/road_operating_inputs_v2')
    parser.add_argument('--evidence',type=Path,default=ROOT/'artifacts/road_operating_evidence_v2')
    args=parser.parse_args(); run(args.output,args.evidence)
