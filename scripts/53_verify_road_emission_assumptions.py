"""Verify scenario exports using scalar equations and original frozen components.

Does not call the evaluated source/profile functions. Numerical agreement proves
construction fidelity, not measured noise accuracy or local assumption validity.
"""
import argparse
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
import math
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes

FIELDS = ('two_wheeled_motor_vehicles','cars_and_taxis','buses_and_coaches','LGVs',
    'HGVs_2_rigid_axle','HGVs_3_rigid_axle','HGVs_4_or_more_rigid_axle',
    'HGVs_3_or_4_articulated_axle','HGVs_5_articulated_axle','HGVs_6_articulated_axle')
DAYS = ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
PERIODS = {'day':list(range(7,19)),'evening':list(range(19,23)),'night':[23,0,1,2,3,4,5,6]}


def read_json(p): return json.loads(Path(p).read_bytes())


def verified(p):
    p = Path(p).resolve()
    inv = read_json(p/'inventory.json')
    for name,digest in inv['files'].items():
        q = (p/name).resolve()
        if not q.is_relative_to(p) or file_hash(q)!=digest: raise ValueError('Inventory mismatch')
    return inv


def same(a,b,*,relative=1e-12,absolute=1e-10):
    if not math.isclose(a,b,rel_tol=relative,abs_tol=absolute):
        raise ValueError(f'Independent scalar check differs: {a} versus {b}')


def run(inputs,output):
    inputs,output = Path(inputs).resolve(),Path(output).resolve()
    if not inputs.is_relative_to(ROOT/'artifacts') or not output.is_relative_to(ROOT/'artifacts') or output.exists():
        raise ValueError('Use immutable inputs and fresh workspace verification')
    inv = verified(inputs)
    input_hash = file_hash(inputs/'inventory.json')
    protocol = read_json(inputs/'protocol.json')
    for name,digest in protocol['parent_inventory_sha256'].items():
        parent = ROOT/'artifacts'/name
        verified(parent)
        if file_hash(parent/'inventory.json')!=digest: raise ValueError('Parent identity changed')
    original = read_json(ROOT/'artifacts/road_direction_inputs_v2/donor_partitions.json')['points']
    original = {p['count_point_id']:p for p in original}
    budget_original = read_json(ROOT/'artifacts/road_direction_inputs_v2/carriageway_budgets.json')['groups']
    group_index = read_json(inputs/'group_evaluation_index.json')['groups']
    if Counter((g['group_id'],g['link_id'],g['evaluation_donor_id'],g['original_primary_flow']) for g in group_index)!=Counter(
        (g['group_id'],r['link_id'],g['count_point_id'],r['flow']) for g in budget_original for r in g['representations']):
        raise ValueError('Spatial group membership or primary flow changed')
    annual = {identity:{f:sum(s['vehicle_categories'][f] for s in p['streams']) if all(s['vehicle_categories'][f] is not None for s in p['streams']) else None for f in FIELDS}
              for identity,p in original.items() if identity in {g['evaluation_donor_id'] for g in group_index}}
    candidates = read_json(ROOT/'artifacts/road_temporal_audit_v2/national_2021_assumption_candidates.json')
    national_rows = candidates['timing']['tra0307']['rows']+candidates['timing']['tra0308']['rows']
    counts = Counter(DAYS[(date(2021,1,1)+timedelta(days=i)).weekday()] for i in range(365))
    if counts['Friday']!=53 or any(counts[d]!=52 for d in DAYS if d!='Friday'): raise ValueError('Calendar test failed')
    profiles = read_json(inputs/'case_period_profiles.json')
    cases = {c['case_id']:c for c in protocol['cases']}
    if len(cases)!=len(protocol['cases']) or len(cases)!=15: raise ValueError('Scenario identity mismatch')
    for case_id,case in cases.items():
        for f in FIELDS:
            if case['timing']=='uniform':
                raw = [1.]*24
            else:
                group = ('Cars and Taxis' if f=='cars_and_taxis' else 'Light Commercial Vehicles' if f=='LGVs'
                         else 'Heavy Goods Vehicles' if f in FIELDS[4:] else 'All motor vehicles')
                if case['timing']=='national_all': group = 'All motor vehicles'
                rows = {r['hour_start']:r for r in national_rows if r['vehicle_group']==group}
                if set(rows)!=set(range(24)): raise ValueError('Original profile incomplete')
                raw = [sum(counts[d]*rows[h]['weekday_indices'][d] for d in DAYS) for h in range(24)]
            for h in PERIODS['night']: raw[h] *= case['night_multiplier']
            hourly = [v/sum(raw) for v in raw]
            exported = profiles[case_id][f]
            for v,w in zip(hourly,exported['hour_weights']): same(v,w)
            for p,hours in PERIODS.items(): same(sum(hourly[h] for h in hours),exported['period_fractions'][p])

    coefficients = read_json(inputs/'published_2021_coefficients.json')
    coefficient_source = next(ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256'] if n.startswith('road_emission_sources_'))
    if coefficients!=read_json(coefficient_source/'published_2021_coefficients.json'):
        raise ValueError('Published coefficients changed')
    evaluations = read_json(inputs/'donor_source_scenarios.json')['evaluations']
    if len(evaluations)!=len(cases)*len(annual) or len({e['evaluation_id'] for e in evaluations})!=len(evaluations):
        raise ValueError('Duplicate/missing donor-case evaluation')
    checked_scalar_powers, max_relative_error = 0,0.
    for e in evaluations:
        case_id,identity = e['case_id'],e['count_point_id']
        case, categories = cases[case_id],annual[identity]
        if e['annual_categories']!=categories: raise ValueError('Original vehicle components changed')
        for f,value in categories.items():
            daily_sum = 0.
            for p,hours in PERIODS.items():
                expected = value*profiles[case_id][f]['period_fractions'][p] if value is not None else None
                actual = e['period_traffic'][f][p]
                if expected is None:
                    if actual['assumed_daily_vehicles_in_period'] is not None or actual['assumed_average_vehicles_per_hour'] is not None:
                        raise ValueError('Missing category became known')
                else:
                    same(expected,actual['assumed_daily_vehicles_in_period'])
                    same(expected/len(hours),actual['assumed_average_vehicles_per_hour'])
                    daily_sum += actual['assumed_average_vehicles_per_hour']*len(hours)
            if value is not None: same(value,daily_sum)
        missing = any(v is None for v in categories.values())
        invalid_surface = case_id=='invalid_worked_at_40'
        if e['status']=='withheld':
            if not missing and not invalid_surface: raise ValueError('Unexpected withheld case')
            if e['period_spectra'] is not None: raise ValueError('Withheld source has power')
            continue
        if missing or invalid_surface or e['receiver_pressure_db'] is not None or e['regulatory_compliance']:
            raise ValueError('Scenario crossed qualification boundary')
        expected_power = {p:[0.]*8 for p in PERIODS}
        for f,value in categories.items():
            cat = ('1' if f in ('cars_and_taxis','LGVs') else case['bus_category'] if f=='buses_and_coaches'
                   else case['two_wheeler_category'] if f=='two_wheeled_motor_vehicles' else '2' if f=='HGVs_2_rigid_axle' else '3')
            speed = 40. if cat=='4a' else case['speed_kmh']
            if e['category_and_speed_assumptions'][f]!={'model_category':cat,'assumed_speed_kmh':speed}:
                raise ValueError('Vehicle or speed assignment changed')
            c = coefficients['categories'][cat]
            surface = coefficients['surfaces'][case['surface']]['categories'][cat]
            for j in range(8):
                propulsion = 10**((c['AP'][j]+c['BP'][j]*(speed-70)/70+min(surface['alpha'][j],0))/10)
                rolling = 0. if cat in ('4a','4b') else 10**((c['AR'][j]+c['BR'][j]*math.log10(speed/70)+surface['alpha'][j]+surface['beta']*math.log10(speed/70))/10)
                for p,hours in PERIODS.items():
                    flow = value*profiles[case_id][f]['period_fractions'][p]/len(hours)
                    expected_power[p][j] += (rolling+propulsion)*flow/1000/speed
        expected_power['den_penalised_source'] = [(12*expected_power['day'][j]+4*math.sqrt(10)*expected_power['evening'][j]+80*expected_power['night'][j])/24 for j in range(8)]
        for p,power in expected_power.items():
            exported = e['period_spectra'][p]
            for value,actual,db in zip(power,exported['relative_power_per_m'],exported['sound_power_db_re_1e_minus_12_W_per_m']):
                same(value,actual)
                if value>0:
                    same(10*math.log10(value),db)
                    max_relative_error = max(max_relative_error,abs(actual-value)/value)
                elif db is not None or not exported['zero_power']: raise ValueError('Logarithmic zero lacks explicit zero state')
                checked_scalar_powers += 1

    free_flow = read_json(inputs/'conditional_national_free_flow_candidates.json')
    unavailable_cells = 0
    for original_row,exported in zip(candidates['free_flow_speeds']['rows'],free_flow['rows']):
        if exported['local_application'] is not None: raise ValueError('National speed was applied to a local road')
        for key,cell in original_row['published_vehicle_values'].items():
            v = exported['published_vehicle_values'][key]
            if v['original_cell']!=cell: raise ValueError('Published speed cell changed')
            try: expected = float(cell)*1.609344
            except ValueError: expected = None
            if expected is None:
                unavailable_cells += 1
                if v['speed_kmh_if_same_vehicle_and_road_type'] is not None: raise ValueError('Missing speed became zero')
            else: same(expected,v['speed_kmh_if_same_vehicle_and_road_type'])
    if protocol['eligible_for_absolute_noise_release'] or protocol['receptor_noise_predictions_created']:
        raise ValueError('Unqualified receiver release')
    receipt = dict(input_generation_id=inv['generation_id'],input_inventory_sha256=input_hash,
        original_spatial_budget_memberships_verified=len(group_index),unique_original_donors_verified=len(annual),
        donor_case_evaluations_verified=len(evaluations),independent_scalar_source_powers_verified=checked_scalar_powers,
        maximum_relative_source_power_difference=max_relative_error,
        original_categories_and_assumed_period_traffic_conserved=True,calendar_weighted_profiles_verified=True,
        unavailable_speed_cells_retained=unavailable_cells,independent_local_speed_or_noise_accuracy_validation=False,
        verification_code_sha256=file_hash(Path(__file__)))
    output.mkdir(parents=True)
    shutil.copyfile(Path(__file__),output/Path(__file__).name)
    (output/'verification.json').write_bytes(json_bytes(receipt))
    verified(inputs)
    if file_hash(inputs/'inventory.json')!=input_hash: raise ValueError('Verification changed the inputs')
    files = {p.name:file_hash(p) for p in output.iterdir() if p.is_file()}
    (output/'inventory.json').write_bytes(json_bytes(dict(
        generation_id='road-emission-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20],files=files)))
    print(json.dumps(receipt),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',default=str(ROOT/'artifacts/road_emission_assumptions_v2'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/road_emission_review_v2'))
    args = parser.parse_args()
    run(args.inputs,args.output)
