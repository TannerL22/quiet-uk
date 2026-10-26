"""Explicit assumption cases and a research road-source sensitivity kernel.

This is conditional spectral sound power at the road, not receptor sound pressure,
regulatory CNOSSOS compliance, observed speeds or qualified annual period traffic.
No propagation, fitting, uncertainty interval or missing-cell prediction occurs.
"""
from collections import Counter
from copy import deepcopy
from datetime import date, timedelta
import math

import numpy as np

from .road_input_audit import VEHICLE_FIELDS
from .road_temporal import DAYS, number, sum_preserving_missing

PERIOD_HOURS = {'day':tuple(range(7,19)), 'evening':tuple(range(19,23)),
                'night':(23,0,1,2,3,4,5,6)}
PROFILE_GROUPS = {'cars_and_taxis':'Cars and Taxis', 'LGVs':'Light Commercial Vehicles',
                  **{f:'Heavy Goods Vehicles' for f in VEHICLE_FIELDS[4:]}}
BASE_CASE = dict(timing='national_vehicle',speed_kmh=70.,surface='Reference road surface',
                 bus_category='2',two_wheeler_category='4b',night_multiplier=1.)


def assumption_cases():
    """Predeclared comparisons; anchor is a numerical reference, not best estimate."""
    definitions = [('anchor',{}),('timing_all_motor',dict(timing='national_all')),
        ('timing_uniform_24h',dict(timing='uniform')),
        ('timing_night_half',dict(night_multiplier=.5)),('timing_night_double',dict(night_multiplier=2.)),
        ('speed_20',dict(speed_kmh=20.)),('speed_40',dict(speed_kmh=40.)),('speed_100',dict(speed_kmh=100.)),
        ('surface_thin_layer_a',dict(surface='Thin layer A')),('surface_worked',dict(surface='Worked surface')),
        ('buses_heavy',dict(bus_category='3')),('two_wheelers_mopeds',dict(two_wheeler_category='4a')),
        ('interaction_high',dict(speed_kmh=100.,surface='Worked surface',night_multiplier=2.,bus_category='3')),
        ('interaction_low',dict(speed_kmh=40.,surface='Thin layer A',night_multiplier=.5,two_wheeler_category='4a')),
        ('invalid_worked_at_40',dict(speed_kmh=40.,surface='Worked surface'))]
    return [{**BASE_CASE,**changes,'case_id':identity} for identity,changes in definitions]


def calendar_weekdays(year):
    if isinstance(year,bool) or not isinstance(year,int):
        raise ValueError('Use an exact calendar year')
    start,stop = date(year,1,1),date(year+1,1,1)
    return dict(Counter(DAYS[(start+timedelta(days=i)).weekday()] for i in range((stop-start).days)))


def national_hour_weights(rows,vehicle_group,year=2021,weekday=None):
    selected = [r for r in rows if r['vehicle_group']==vehicle_group]
    if len(selected)!=24 or {r['hour_start'] for r in selected}!=set(range(24)):
        raise ValueError('Missing/duplicate 24-hour national curve')
    if weekday is not None and weekday not in DAYS:
        raise ValueError('Unqualified weekday')
    counts = calendar_weekdays(year)
    weights = np.zeros(24)
    for r in selected:
        if r['year']!=year or r['units']!='published index, not vehicles/hour':
            raise ValueError('Wrong year or profile units')
        indices = r['weekday_indices']
        if set(indices)!=set(DAYS) or any(number(v) is None for v in indices.values()):
            raise ValueError('Missing/non-finite national index')
        weights[r['hour_start']] = (indices[weekday] if weekday else
                                    sum(counts[d]*indices[d] for d in DAYS))
    if weights.sum()<=0:
        raise ValueError('Zero national profile support')
    return (weights/weights.sum()).tolist()


def case_hour_weights(candidates,field,case,year=2021):
    if field not in VEHICLE_FIELDS:
        raise ValueError('Unknown original vehicle field')
    timing = case['timing']
    if timing=='uniform':
        weights = np.full(24,1./24)
        curve, fallback = 'uniform synthetic control',False
    else:
        if timing not in ('national_all','national_vehicle'):
            raise ValueError('Unknown timing assumption')
        curve = PROFILE_GROUPS.get(field,'All motor vehicles') if timing=='national_vehicle' else 'All motor vehicles'
        fallback = timing=='national_vehicle' and field not in PROFILE_GROUPS
        rows = candidates['timing']['tra0307' if curve=='All motor vehicles' else 'tra0308']['rows']
        weights = np.array(national_hour_weights(rows,curve,year))
    multiplier = number(case['night_multiplier'])
    if multiplier is None or multiplier<=0:
        raise ValueError('Night stress multiplier must be positive')
    weights[list(PERIOD_HOURS['night'])] *= multiplier
    weights /= weights.sum()
    return dict(hour_weights=weights.tolist(),period_fractions={p:float(weights[list(h)].sum()) for p,h in PERIOD_HOURS.items()},
                profile_group=curve,proxy_for_missing_vehicle_profile=fallback,
                status='assumed_local_transfer',calendar_weekday_occurrences=calendar_weekdays(year))


def allocate_periods(annual_categories,profiles):
    """Preserve original category AADF; no repairing rounded category totals."""
    result = {}
    for field in VEHICLE_FIELDS:
        value = number(annual_categories[field])
        fractions = profiles[field]['period_fractions']
        if set(fractions)!=set(PERIOD_HOURS) or any(number(v) is None for v in fractions.values()) or not math.isclose(sum(fractions.values()),1.,abs_tol=1e-12):
            raise ValueError('Period fractions do not conserve daily traffic')
        result[field] = {p:{'assumed_daily_vehicles_in_period':None if value is None else value*f,
                            'assumed_average_vehicles_per_hour':None if value is None else value*f/len(PERIOD_HOURS[p])}
                         for p,f in fractions.items()}
    return result


def model_category(field,case):
    if field not in VEHICLE_FIELDS or case['bus_category'] not in ('2','3') or case['two_wheeler_category'] not in ('4a','4b'):
        raise ValueError('Unknown vehicle/category assumption')
    if field=='two_wheeled_motor_vehicles': return case['two_wheeler_category']
    if field=='buses_and_coaches': return case['bus_category']
    if field in ('cars_and_taxis','LGVs'): return '1'
    return '2' if field=='HGVs_2_rigid_axle' else '3'


def vehicle_power(coefficients,category,speed_kmh,surface):
    """Relative power per band, using published coefficients and checked equations.

    Units: power / 1e-12 W. Reference flat, dry, steady, 20 C, no studded tyres.
    All selected F-4 corrections use alpha+beta*log10(v/70) for rolling and
    min(alpha,0) for propulsion. Category 4 has propulsion only.
    """
    speed = number(speed_kmh)
    if speed is None or not 20<=speed<=130 or category not in coefficients['categories']:
        raise ValueError('Source speed/category outside declared research support')
    s = coefficients['surfaces'][surface]
    if ((s['minimum_speed_kmh'] is not None and speed<s['minimum_speed_kmh'])
            or (s['maximum_speed_kmh'] is not None and speed>s['maximum_speed_kmh'])):
        raise ValueError('Surface coefficient speed range unsupported')
    c,correction = coefficients['categories'][category],s['categories'][category]
    alpha, beta = np.array(correction['alpha']),correction['beta']
    reference = coefficients['reference_speed_kmh']
    propulsion_db = np.array(c['AP'])+np.array(c['BP'])*(speed-reference)/reference+np.minimum(alpha,0.)
    propulsion = np.power(10.,propulsion_db/10.)
    if category in ('4a','4b'):
        return propulsion
    rolling_db = np.array(c['AR'])+np.array(c['BR'])*math.log10(speed/reference)+alpha+beta*math.log10(speed/reference)
    return propulsion+np.power(10.,rolling_db/10.)


def source_spectrum(annual_categories,profiles,case,coefficients):
    """Conditional source-line power only; missing categories withhold all periods."""
    if set(annual_categories)!=set(VEHICLE_FIELDS):
        raise ValueError('Incomplete vehicle schema')
    periods = allocate_periods(annual_categories,profiles)
    if any(number(v) is None for v in annual_categories.values()):
        return dict(status='withheld',reason='missing_original_vehicle_category',period_traffic=periods,period_spectra=None)
    power = {p:np.zeros(len(coefficients['frequencies_hz'])) for p in PERIOD_HOURS}
    assignments = {}
    for field in VEHICLE_FIELDS:
        category = model_category(field,case)
        # A declared all-moped case uses 40 km/h; it does not infer or clamp any
        # observed speed. Other categories use this case's comparison speed.
        speed = 40. if category=='4a' else case['speed_kmh']
        assignments[field] = dict(model_category=category,assumed_speed_kmh=speed)
        if annual_categories[field]==0:
            continue
        try:
            single = vehicle_power(coefficients,category,speed,case['surface'])
        except ValueError as error:
            return dict(status='withheld',reason=str(error),period_traffic=periods,period_spectra=None)
        for p in PERIOD_HOURS:
            flow = periods[field][p]['assumed_average_vehicles_per_hour']
            power[p] += single*flow/(1000.*speed)
    spectra = {p:{'relative_power_per_m':v.tolist(),
                  'sound_power_db_re_1e_minus_12_W_per_m':[float(10*math.log10(x)) if x>0 else None for x in v],
                  'zero_power':bool(np.all(v==0))} for p,v in power.items()}
    den = (12*power['day']+4*10**.5*power['evening']+8*10*power['night'])/24
    spectra['den_penalised_source'] = dict(relative_power_per_m=den.tolist(),
        sound_power_db_re_1e_minus_12_W_per_m=[float(10*math.log10(x)) if x>0 else None for x in den],
        zero_power=bool(np.all(den==0)))
    return dict(status='conditional_research_source_emission',reason=None,period_traffic=periods,
                category_and_speed_assumptions=assignments,period_spectra=spectra,
                category_sum_aadf=sum(annual_categories.values()),
                receiver_pressure_db=None,regulatory_compliance=False)


def aggregate_components(components):
    """Components once; convenience whole-road total never contributes twice."""
    return {f:sum_preserving_missing(c['vehicle_categories'][f] for c in components) for f in VEHICLE_FIELDS}


def survey_shape_check(point,candidates):
    """Compare conditional 07-19 shapes only; never expand a survey into the year."""
    results = []
    rows = candidates['timing']['tra0307']['rows']
    for survey in point['survey_days']:
        if not survey['complete_published_07_19_support'] or not survey['annual_input_identity_compatible']:
            continue
        observed = [sum(r['flow'] for r in point['raw_rows'] if r['count_date']==survey['count_date'] and r['hour_start']==h) for h in range(7,19)]
        if sum(observed)<=0:
            continue
        observed = np.array(observed)/sum(observed)
        national = np.array(national_hour_weights(rows,'All motor vehicles',weekday=survey['weekday']))[7:19]
        national /= national.sum()
        uniform = np.full(12,1/12)
        results.append(dict(count_point_id=point['count_point_id'],survey_date=survey['count_date'],weekday=survey['weekday'],
            observed_07_19_hour_shares=observed.tolist(),national_same_weekday_07_19_hour_shares=national.tolist(),
            national_total_variation_distance=float(np.abs(observed-national).sum()/2),
            uniform_total_variation_distance=float(np.abs(observed-uniform).sum()/2),
            status='one_survey_day_daytime_shape_diagnostic_only',annual_night_profile_validated=False))
    return results
