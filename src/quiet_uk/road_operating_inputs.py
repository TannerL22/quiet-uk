"""Evidence-labelled traffic, speed and air for conditional road calculations.

Observed station weather is distinct from uniform path air. National timing and
free-flow speed transfers remain assumptions. No receiver prediction is made.
"""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from importlib.resources import files
import io
import math
import re
from zoneinfo import ZoneInfo

import numpy as np

from .road_emission import (PERIOD_HOURS, PROFILE_GROUPS, allocate_periods, model_category,
                            vehicle_power)
from .road_input_audit import VEHICLE_FIELDS
from .road_temporal import DAYS, number

LONDON_TZIF = files('tzdata').joinpath('zoneinfo/Europe/London').read_bytes()
LONDON = ZoneInfo.from_file(io.BytesIO(LONDON_TZIF), key='Europe/London')
GOOD_QC = frozenset(('1', '5'))
FALLBACK_AIR = dict(temperature_c=10., relative_humidity_percent=70., pressure_kpa=101.325)


def period_for_hour(hour):
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError('Use a civil hour in 0..23')
    return next(p for p, hours in PERIOD_HOURS.items() if hour in hours)


def relative_humidity(temperature_c, dewpoint_c):
    """Water-based Magnus approximation, derived from the retained NWS equations.

    RH=100*exp(17.67*Td/(Td+243.5)-17.67*T/(T+243.5)). Dewpoint > T,
    missing or out-of-domain data stays unknown. No saturation clipping.
    """
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (temperature_c, dewpoint_c)):
        return None
    if not -20 <= temperature_c <= 50 or not -80 <= dewpoint_c <= temperature_c:
        return None
    return 100 * math.exp(17.67 * dewpoint_c / (dewpoint_c + 243.5)
                         - 17.67 * temperature_c / (temperature_c + 243.5))


def _field(text, missing, scale, qc, bounds):
    raw = int(text)
    value = None if raw == missing or qc not in GOOD_QC else raw / scale
    if value is not None and not bounds[0] <= value <= bounds[1]: value = None
    return dict(original_cell=text, quality_code=qc, value=value,
                state='accepted_station_observation' if value is not None else 'missing_or_rejected')


def parse_isd_record(line, record_number, year=2021, station='03772099999'):
    if len(line) < 105 or line[4:15] != station:
        raise ValueError('Truncated record or unexpected station identity')
    stamp = datetime.strptime(line[15:27], '%Y%m%d%H%M').replace(tzinfo=timezone.utc)
    if stamp.year != year: raise ValueError('Wrong observation year')
    if line[41:46] not in ('FM-12', 'FM-15'): raise ValueError('Unqualified report type')
    t = _field(line[87:92], 9999, 10, line[92], (-93.2, 61.8))
    td = _field(line[93:98], 9999, 10, line[98], (-98.2, 36.8))
    speed = _field(line[65:69], 9999, 10, line[69], (0, 90))
    direction = _field(line[60:63], 999, 1, line[63], (1, 360))
    if line[64] in ('C', 'V', '9'): direction['value'] = None; direction['state'] = 'calm_variable_or_unknown_direction'
    slp = _field(line[99:104], 99999, 100, line[104], (86, 110))
    extra = line[105:]
    if extra and not extra.startswith(('ADD', 'REM', 'EQD')):
        raise ValueError('Unqualified optional-section prefix')
    extra = re.split('REM|EQD', extra, maxsplit=1)[0]
    matches = list(re.finditer(r'MA1([0-9]{5})([0-9A-Z])([0-9]{5})([0-9A-Z])', extra))
    if len(matches) > 1: raise ValueError('Duplicate station-pressure section')
    pressure = _field(matches[0][3], 99999, 100, matches[0][4], (45, 109)) if matches else dict(
        original_cell=None, quality_code=None, value=None, state='no_station_pressure_section')
    rh = relative_humidity(t['value'], td['value'])
    return dict(record_number=record_number, observed_utc=stamp.isoformat(), report_type=line[41:46],
                source_flag=line[27], observation_location=dict(latitude=int(line[28:34])/1000,
                    longitude=int(line[34:41])/1000, elevation_m=int(line[46:51])),
                temperature_c=t, dewpoint_c=td, station_pressure_kpa=pressure,
                sea_level_pressure_kpa=slp, wind_speed_m_s=speed, wind_from_degrees=direction,
                wind_type=line[64], derived_relative_humidity_percent=rh,
                humidity_status='derived_water_Magnus_approximation' if rh is not None else 'unknown')


def air_candidate(observation):
    """Per-field choices remain separate from the original station evidence."""
    if observation is None:
        return dict(**FALLBACK_AIR, uniform_air=True, status='assumed_gap_air',
                    field_basis={k: 'explicit_gap_reference' for k in FALLBACK_AIR})
    t = observation['temperature_c']['value']
    rh = observation['derived_relative_humidity_percent']
    p = observation['station_pressure_kpa']['value']
    temperature_ok = t is not None and -20 <= t <= 50
    humidity_ok = rh is not None and 10 <= rh <= 100
    pressure_ok = p is not None and 50 <= p <= 120
    return dict(temperature_c=t if temperature_ok else FALLBACK_AIR['temperature_c'],
                relative_humidity_percent=rh if temperature_ok and humidity_ok else FALLBACK_AIR['relative_humidity_percent'],
                pressure_kpa=p if pressure_ok else FALLBACK_AIR['pressure_kpa'], uniform_air=True,
                status='station_derived_uniform_path_assumption' if temperature_ok and humidity_ok and pressure_ok else 'explicit_partial_gap_air',
                field_basis=dict(temperature_c='station_transfer' if temperature_ok else 'explicit_gap_reference',
                    relative_humidity_percent='derived_station_transfer' if temperature_ok and humidity_ok else 'explicit_gap_reference',
                    pressure_kpa='station_transfer' if pressure_ok else 'explicit_standard_pressure_reference'))


def absorption_air_parameters(row, *, accept_uniform_station_transfer=False):
    """Supply the existing absorption API only after explicit assumption consent.

    Evidence metadata stays on the input row; callers can inspect its field_basis.
    False returns unknown air, allowing the existing attenuation API to withhold.
    """
    if not isinstance(accept_uniform_station_transfer, bool):
        raise ValueError('Assumption acceptance must be an explicit boolean')
    if not accept_uniform_station_transfer: return None
    chosen = row['candidate_absorption_air']
    return {k: chosen[k] for k in (*FALLBACK_AIR, 'uniform_air')}


def hourly_weather(observations, year=2021):
    """One original report per nearest UTC hour; no interpolation or gap removal.

    Reports map to their nearest hour (half-hour ties go forward). Within a slot,
    a complete strict-QC air tuple precedes other records; then nearest timestamp,
    SYNOP before METAR, then source row. Fields are never spliced across reports.
    """
    by_slot = defaultdict(list)
    for obs in observations:
        instant = datetime.fromisoformat(obs['observed_utc'])
        if instant.utcoffset() != timedelta(0) or instant.year != year:
            raise ValueError('Expected exact-year UTC observations')
        slot = (instant + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
        if slot.year == year: by_slot[slot].append(obs)
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    end = datetime(year+1, 1, 1, tzinfo=timezone.utc)
    rows = []
    for i in range(int((end-start).total_seconds()/3600)):
        slot = start + timedelta(hours=i)
        options = by_slot.get(slot, [])
        def order(o):
            candidate = air_candidate(o)
            return (candidate['status'] != 'station_derived_uniform_path_assumption',
                    abs((datetime.fromisoformat(o['observed_utc'])-slot).total_seconds()),
                    o['report_type'] != 'FM-12', o['record_number'])
        obs = min(options, key=order) if options else None
        local = slot.astimezone(LONDON)
        rows.append(dict(slot_utc=slot.isoformat(), local_time=local.isoformat(), local_fold=local.fold,
                         period=period_for_hour(local.hour), original_reports_in_slot=len(options),
                         chosen_observation=obs, candidate_absorption_air=air_candidate(obs)))
    return rows


def joint_calendar_profiles(candidates, weather, night_multiplier=1., year=2021):
    """Full-year hourly traffic weights aligned with the retained air samples.

    National weekday/civil-hour indices apply to every elapsed UTC hour, including
    the repeated autumn hour and absent spring hour. Normalise the entire year;
    no monthly/seasonal traffic factors, gap dropping or daily normalisation.
    AADF*days_in_year*slot_weight gives conditional vehicles in that one hour.
    """
    multiplier = number(night_multiplier)
    if multiplier is None or multiplier <= 0: raise ValueError('Positive night multiplier required')
    start = datetime(year, 1, 1, tzinfo=timezone.utc)
    expected = int((datetime(year+1, 1, 1, tzinfo=timezone.utc)-start).total_seconds()/3600)
    if len(weather) != expected: raise ValueError('Complete calendar support required')
    local_times = []
    for i, row in enumerate(weather):
        utc = start + timedelta(hours=i)
        if row['slot_utc'] != utc.isoformat(): raise ValueError('Missing/duplicate/out-of-order hour')
        local = utc.astimezone(LONDON)
        if row['local_time'] != local.isoformat() or row['period'] != period_for_hour(local.hour):
            raise ValueError('Wrong local clock or noise period')
        local_times.append(local)
    groups, profiles = {}, {}
    for group in ('All motor vehicles', 'Cars and Taxis', 'Light Commercial Vehicles', 'Heavy Goods Vehicles'):
        table = candidates['timing']['tra0307' if group == 'All motor vehicles' else 'tra0308']['rows']
        rows = [r for r in table if r['vehicle_group'] == group]
        if len(rows) != 24 or {r['hour_start'] for r in rows} != set(range(24)):
            raise ValueError('Incomplete/duplicate national curve')
        lookup = {}
        for r in rows:
            if (r['year'] != year or r['units'] != 'published index, not vehicles/hour'
                    or set(r['weekday_indices']) != set(DAYS)):
                raise ValueError('Wrong year, units or weekday schema')
            for d, v in r['weekday_indices'].items():
                if number(v) is None: raise ValueError('Missing/invalid index')
                lookup[(d, r['hour_start'])] = v
        raw = [lookup[(DAYS[t.weekday()], t.hour)] * (multiplier if row['period'] == 'night' else 1.)
               for t, row in zip(local_times, weather)]
        total = math.fsum(raw)
        if total <= 0: raise ValueError('Zero calendar profile support')
        weights = [v/total for v in raw]
        fractions = {p: math.fsum(w for w, row in zip(weights, weather) if row['period'] == p) for p in PERIOD_HOURS}
        groups[group] = dict(slot_weights=weights, period_fractions=fractions,
                             night_multiplier=multiplier, days_in_year=expected/24)
    for field in VEHICLE_FIELDS:
        group = PROFILE_GROUPS.get(field, 'All motor vehicles')
        profiles[field] = dict(profile_group=group, period_fractions=groups[group]['period_fractions'],
            proxy_for_missing_vehicle_profile=field not in PROFILE_GROUPS,
            status='assumed_national_transfer_aligned_to_elapsed_weather_calendar')
    return dict(groups=groups, vehicle_profiles=profiles, clock='Europe/London, elapsed UTC-hour calendar',
                annual_flow_rule='AADF * days_in_year * slot_weight; not an observed hourly time series',
                seasonal_traffic_factors='unknown; no seasonal factor applied')


SPEED_COLUMNS = {
    'two_wheeled_motor_vehicles': 'Motorcycles', 'cars_and_taxis': 'Cars',
    'LGVs': 'Light Commercial Vehicles', 'HGVs_2_rigid_axle': 'Rigid Heavy Goods Vehicles 2 axles',
    'HGVs_3_rigid_axle': 'Rigid Heavy Goods Vehicles 3 or more axles',
    'HGVs_4_or_more_rigid_axle': 'Rigid Heavy Goods Vehicles 3 or more axles',
    'HGVs_3_or_4_articulated_axle': 'Articulated Heavy Goods Vehicles 3 or 4 axles',
    'HGVs_5_articulated_axle': 'Articulated Heavy Goods Vehicles 5 or more axles',
    'HGVs_6_articulated_axle': 'Articulated Heavy Goods Vehicles 5 or more axles',
}


def choose_speeds(road, donor_road_name, free_flow_rows):
    """Mainline motorway transfer only; other roads get a declared comparison.

    Mismatch, ramps and roundabouts never inherit national motorway speeds.
    40/70 km/h gap references are research choices, not inferred legal limits.
    """
    motorway = (bool(re.fullmatch(r'M[0-9]+', road['road_number']))
                and donor_road_name == road['road_number']
                and road['form_of_way'] in ('Dual Carriageway', 'Collapsed Dual Carriageway'))
    selected = [r for r in free_flow_rows if r['road_type'] == 'Motorways' and r['year'] == 2021]
    if len(selected) != 1: raise ValueError('Missing/duplicate exact-year motorway speed row')
    cells = selected[0]['published_vehicle_values']
    choices = {}
    for field in VEHICLE_FIELDS:
        speed, original = None, None
        if motorway and field in SPEED_COLUMNS:
            matched = [v for k, v in cells.items() if k.split(' [')[0] == SPEED_COLUMNS[field]]
            if len(matched) != 1: raise ValueError('Unqualified vehicle speed crosswalk')
            original = matched[0]['original_cell']
            speed = matched[0]['speed_kmh_if_same_vehicle_and_road_type']
        if speed is None:
            speed = 70. if motorway else 40.
            basis = 'explicit_motorway_missing_vehicle_reference' if motorway else 'explicit_unknown_road_speed_reference'
        else: basis = '2021_national_motorway_free_flow_transfer'
        if number(speed) is None or not 20 <= speed <= 130: raise ValueError('Out-of-domain candidate speed')
        choices[field] = dict(candidate_kmh=speed, basis=basis, original_national_cell=original,
                              observed_local_kmh=None)
    return dict(status='conditional_speed_assumptions', motorway_identity_candidate=motorway,
                period_policy='Same candidate speed in all periods; congestion and dynamics unknown', choices=choices)


def operating_spectrum(categories, profiles, speeds, coefficients):
    """Per-original-class speeds and Q/(1000v); conditional reference surface.

    The source-temperature reference stays 20 C. Observation air is used by the
    propagation adapter separately; source temperature corrections are unresolved.
    """
    if set(categories) != set(VEHICLE_FIELDS) or set(speeds) != set(VEHICLE_FIELDS):
        raise ValueError('Incomplete original vehicle schema')
    period_traffic = allocate_periods(categories, profiles)
    if any(number(v) is None for v in categories.values()):
        return dict(status='withheld', reason='missing_original_vehicle_category', period_traffic=period_traffic, period_spectra=None)
    power = {p: np.zeros(len(coefficients['frequencies_hz'])) for p in PERIOD_HOURS}
    assignments = {}
    for field in VEHICLE_FIELDS:
        speed = number(speeds[field])
        if speed is None or not 20 <= speed <= 130:
            return dict(status='withheld', reason='unsupported_speed', period_traffic=period_traffic, period_spectra=None)
        category = model_category(field, dict(bus_category='2', two_wheeler_category='4b'))
        assignments[field] = dict(model_category=category, candidate_speed_kmh=speed)
        if categories[field] == 0: continue
        single = vehicle_power(coefficients, category, speed, 'Reference road surface')
        for p in PERIOD_HOURS:
            power[p] += single * period_traffic[field][p]['assumed_average_vehicles_per_hour'] / (1000*speed)
    den = (12*power['day']+4*10**.5*power['evening']+8*10*power['night'])/24
    spectra = {p: dict(relative_power_per_m=v.tolist(), sound_power_db_re_1e_minus_12_W_per_m=[
                    10*math.log10(x) if x > 0 else None for x in v], zero_power=bool(np.all(v == 0)))
               for p, v in dict(power, den_penalised_source=den).items()}
    return dict(status='conditional_research_source_emission', period_traffic=period_traffic,
                category_and_speed_assumptions=assignments, period_spectra=spectra,
                reference_surface='assumed_reference_surface', source_temperature_c=20.,
                actual_source_temperature_c=None, receiver_pressure_db=None, regulatory_compliance=False)


def wind_sector_diagnostics(weather, bearing_degrees, half_angle_degrees=60., minimum_speed_m_s=.5):
    """Station downwind-sector frequency, never a CNOSSOS favourable probability.

    Bearing is source->receiver clockwise from TRUE north. Wind is reported FROM.
    Calm/variable/rejected directions remain unknown; speed below the declared
    threshold is outside this windy-sector diagnostic, not homogeneous acoustics.
    Bounds cover unobserved sector membership only, not uncertainty in sound.
    """
    if (isinstance(bearing_degrees, bool) or not isinstance(bearing_degrees, (int, float))
            or not math.isfinite(bearing_degrees) or not 0 <= bearing_degrees < 360
            or not 0 < half_angle_degrees <= 180 or minimum_speed_m_s <= 0):
        raise ValueError('Invalid true-north sector definition')
    counts = {p: Counter() for p in PERIOD_HOURS}
    for row in weather:
        c = counts[row['period']]; c['expected_hours'] += 1
        obs = row['chosen_observation']
        speed = obs['wind_speed_m_s']['value'] if obs else None
        direction = obs['wind_from_degrees']['value'] if obs else None
        if speed is None: c['unknown_hours'] += 1
        elif speed < minimum_speed_m_s: c['below_speed_threshold_hours'] += 1
        elif direction is None: c['unknown_hours'] += 1
        else:
            delta = abs(((direction+180-bearing_degrees+180) % 360)-180)
            c['known_direction_hours'] += 1
            if delta <= half_angle_degrees: c['downwind_sector_hours'] += 1
    result = {}
    for p, c in counts.items():
        n, unknown, hits = c['expected_hours'], c['unknown_hours'], c['downwind_sector_hours']
        result[p] = dict(c, conditional_fraction_known_hours=hits/(n-unknown) if n > unknown else None,
                         full_period_membership_lower=hits/n if n else None,
                         full_period_membership_upper=(hits+unknown)/n if n else None,
                         probability_favourable=None, status='station_wind_sector_diagnostic_only')
    return result
