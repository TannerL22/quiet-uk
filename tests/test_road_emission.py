from copy import deepcopy
import math
import json
from pathlib import Path

import numpy as np
import pytest

from quiet_uk.road_emission import (BASE_CASE, PERIOD_HOURS, aggregate_components,
    allocate_periods, assumption_cases, calendar_weekdays, case_hour_weights,
    model_category, national_hour_weights, source_spectrum, survey_shape_check, vehicle_power)
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_temporal import DAYS


def rows(group='All motor vehicles',year=2021):
    return [dict(year=year,hour_start=h,vehicle_group=group,
                 weekday_indices={d:float(1+h+(3 if d=='Friday' and h==23 else 0)) for d in DAYS},
                 units='published index, not vehicles/hour') for h in range(24)]


def candidates():
    return {'timing':{'tra0307':{'rows':rows()},'tra0308':{'rows':sum((rows(g) for g in
        ('Cars and Taxis','Light Commercial Vehicles','Heavy Goods Vehicles')),[])}}}


def coefficients():
    # Deliberately asymmetric synthetic coefficients expose summation mistakes.
    cats = {c:{'AR':[90.]*8,'BR':[30.]*8,'AP':[80.]*8,'BP':[7.]*8} for c in ('1','2','3','4a','4b')}
    return {'frequencies_hz':[63,125,250,500,1000,2000,4000,8000],'reference_speed_kmh':70,
        'categories':cats,'surfaces':{
            'Reference road surface':{'minimum_speed_kmh':None,'maximum_speed_kmh':None,
                'categories':{c:{'alpha':[0.]*8,'beta':0.} for c in cats}},
            'porous':{'minimum_speed_kmh':50.,'maximum_speed_kmh':100.,
                'categories':{c:{'alpha':[-3.]*8,'beta':2.} for c in cats}}}}


def profiles(case=BASE_CASE):
    return {f:case_hour_weights(candidates(),f,case) for f in VEHICLE_FIELDS}


def traffic(**changes):
    return {**{f:0. for f in VEHICLE_FIELDS},**changes}


def test_period_boundaries_and_calendar_include_night_wrap_and_leap_day():
    assert sorted(h for hrs in PERIOD_HOURS.values() for h in hrs)==list(range(24))
    assert PERIOD_HOURS['night']==(23,0,1,2,3,4,5,6)
    assert calendar_weekdays(2021)=={**{d:52 for d in DAYS},'Friday':53}
    assert sum(calendar_weekdays(2020).values())==366
    with pytest.raises(ValueError): calendar_weekdays(True)


def test_joint_calendar_normalisation_preserves_weekday_volume():
    r = rows()
    w = national_hour_weights(r,'All motor vehicles')
    expected = [(52*sum(row['weekday_indices'][d] for d in DAYS)+row['weekday_indices']['Friday']) for row in r]
    assert w==pytest.approx(np.array(expected)/sum(expected))
    individually_normalised = np.mean([np.array([row['weekday_indices'][d] for row in r])/sum(row['weekday_indices'][d] for row in r) for d in DAYS],axis=0)
    assert not np.allclose(w,individually_normalised,atol=1e-7)


@pytest.mark.parametrize('fault',['duplicate','missing','wrongyear','units','missingweekday','negative','nan','allzero'])
def test_unqualified_profile_refuses(fault):
    r = rows()
    if fault=='duplicate': r[-1]['hour_start']=0
    elif fault=='missing': r.pop()
    elif fault=='wrongyear': r[0]['year']=2022
    elif fault=='units': r[0]['units']='vehicles/hour'
    elif fault=='missingweekday': r[0]['weekday_indices'].pop('Sunday')
    elif fault=='negative': r[0]['weekday_indices']['Monday']=-1
    elif fault=='nan': r[0]['weekday_indices']['Monday']=float('nan')
    else:
        for row in r: row['weekday_indices']={d:0. for d in DAYS}
    with pytest.raises(ValueError): national_hour_weights(r,'All motor vehicles')


def test_night_stress_redistributes_without_creating_traffic():
    anchor = profiles()
    double = profiles({**BASE_CASE,'night_multiplier':2})
    a = anchor['cars_and_taxis']['period_fractions']
    b = double['cars_and_taxis']['period_fractions']
    assert b['night']==pytest.approx(2*a['night']/(1+a['night']))
    result = allocate_periods(traffic(cars_and_taxis=1234.),double)
    assert sum(p['assumed_daily_vehicles_in_period'] for p in result['cars_and_taxis'].values())==pytest.approx(1234)
    assert sum(p['assumed_average_vehicles_per_hour']*len(PERIOD_HOURS[k]) for k,p in result['cars_and_taxis'].items())==pytest.approx(1234)


def test_proxy_mapping_is_explicit_and_uniform_has_duration_fractions():
    assert profiles()['buses_and_coaches']['proxy_for_missing_vehicle_profile']
    assert profiles()['two_wheeled_motor_vehicles']['proxy_for_missing_vehicle_profile']
    assert not profiles()['LGVs']['proxy_for_missing_vehicle_profile']
    assert profiles({**BASE_CASE,'timing':'uniform'})['cars_and_taxis']['period_fractions']==pytest.approx({'day':.5,'evening':1/6,'night':1/3})


@pytest.mark.parametrize('field,cat',[('cars_and_taxis','1'),('LGVs','1'),('buses_and_coaches','2'),
    ('two_wheeled_motor_vehicles','4b'),('HGVs_2_rigid_axle','2'),('HGVs_3_rigid_axle','3'),('HGVs_6_articulated_axle','3')])
def test_vehicle_crosswalk(field,cat):
    assert model_category(field,BASE_CASE)==cat


def test_reference_vehicle_power_sums_energy_and_motorcycles_are_propulsion_only():
    c = coefficients()
    assert vehicle_power(c,'1',70,'Reference road surface')==pytest.approx([1.1e9]*8)
    assert vehicle_power(c,'4b',70,'Reference road surface')==pytest.approx([1e8]*8)
    # At half reference speed, rolling falls 9.0309 dB; propulsion falls 3.5 dB.
    expected = 1e9*.5**3 + 10**((80-3.5)/10)
    assert vehicle_power(c,'1',35,'Reference road surface')==pytest.approx([expected]*8)


def test_surface_correction_changes_rolling_and_absorbed_propulsion_separately():
    c = coefficients()
    expected = (1e9+1e8)*10**(-3/10)
    assert vehicle_power(c,'1',70,'porous')==pytest.approx([expected]*8)
    c['surfaces']['porous']['categories']['1']['alpha']=[3.]*8
    assert vehicle_power(c,'1',70,'porous')==pytest.approx([1e9*10**.3+1e8]*8)


@pytest.mark.parametrize('speed',[None,0,19.9,130.1,float('nan'),float('inf'),True])
def test_speed_does_not_silently_clamp_or_extrapolate(speed):
    with pytest.raises(ValueError): vehicle_power(coefficients(),'1',speed,'Reference road surface')


def test_surface_support_refusal_and_missing_vs_zero():
    with pytest.raises(ValueError): vehicle_power(coefficients(),'1',40,'porous')
    unknown = source_spectrum(traffic(cars_and_taxis=None),profiles(),BASE_CASE,coefficients())
    assert unknown['status']=='withheld' and unknown['period_spectra'] is None
    zero = source_spectrum(traffic(),profiles(),BASE_CASE,coefficients())
    assert all(p['zero_power'] and p['relative_power_per_m']==[0.]*8 and p['sound_power_db_re_1e_minus_12_W_per_m']==[None]*8 for p in zero['period_spectra'].values())


def test_flow_density_units_doubling_and_den_penalties():
    case = {**BASE_CASE,'timing':'uniform'}
    one = source_spectrum(traffic(cars_and_taxis=2400),profiles(case),case,coefficients())
    two = source_spectrum(traffic(cars_and_taxis=4800),profiles(case),case,coefficients())
    day = one['period_spectra']['day']['relative_power_per_m'][0]
    assert day==pytest.approx(1.1e9*100/(1000*70))
    assert two['period_spectra']['day']['sound_power_db_re_1e_minus_12_W_per_m'][0]-one['period_spectra']['day']['sound_power_db_re_1e_minus_12_W_per_m'][0]==pytest.approx(10*math.log10(2))
    assert one['period_spectra']['den_penalised_source']['relative_power_per_m'][0]==pytest.approx(day*(12+4*10**.5+8*10)/24)
    assert one['receiver_pressure_db'] is None


def test_original_components_are_preserved_and_stream_splitting_is_invariant():
    a = dict(vehicle_categories=traffic(cars_and_taxis=200),flow=201)
    before = deepcopy(a)
    assert aggregate_components([a])['cars_and_taxis']==200  # original flow residual stays unrepaired
    b = dict(vehicle_categories=traffic(cars_and_taxis=100),flow=100.5)
    assert aggregate_components([b,b])==aggregate_components([a])
    b['vehicle_categories']['LGVs']=None
    assert aggregate_components([a,b])['LGVs'] is None
    assert a==before


def test_case_identities_unique_and_invalid_case_is_explicit():
    cases = assumption_cases()
    assert len(cases)==len({c['case_id'] for c in cases})==15
    assert cases[0]['case_id']=='anchor'
    assert cases[-1]['surface']=='Worked surface' and cases[-1]['speed_kmh']==40


def test_survey_shape_comparison_does_not_expand_or_validate_night():
    p = dict(count_point_id='1',survey_days=[dict(count_date='2021-01-01',weekday='Friday',
        complete_published_07_19_support=True,annual_input_identity_compatible=True)],
        raw_rows=[dict(count_date='2021-01-01',hour_start=h,flow=10) for h in range(7,19)])
    before = deepcopy(p)
    result = survey_shape_check(p,candidates())[0]
    assert result['uniform_total_variation_distance']==0
    assert 0<result['national_total_variation_distance']<1
    assert not result['annual_night_profile_validated']
    assert p==before


def test_official_2021_canary_matches_independent_energy_calculation():
    c = json.loads((Path(__file__).parent/'fixtures/road_emission_2021_coefficients.json').read_bytes())
    # F-1, category 1, 1 kHz at 70 km/h: AR=100.1, AP=84.7.
    expected = 10**10.01+10**8.47
    assert vehicle_power(c,'1',70,'Reference road surface')[4]==pytest.approx(expected,rel=1e-12)
    case = {**BASE_CASE,'timing':'uniform'}
    result = source_spectrum(traffic(cars_and_taxis=2400),profiles(case),case,c)
    expected_line = expected*100/70000
    assert result['period_spectra']['day']['relative_power_per_m'][4]==pytest.approx(expected_line,rel=1e-12)
    assert result['period_spectra']['day']['sound_power_db_re_1e_minus_12_W_per_m'][4]==pytest.approx(10*math.log10(expected_line),abs=1e-10)
    # F-4, Thin layer A, category 1: alpha=-3 at 1 kHz, beta=-2.9.
    assert vehicle_power(c,'1',70,'Thin layer A')[4]==pytest.approx(expected*10**(-.3),rel=1e-12)
