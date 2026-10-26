from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

import numpy as np
import pytest

from quiet_uk.favourable_propagation import atmospheric_absorption
from quiet_uk.road_emission import BASE_CASE, source_spectrum
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_operating_inputs import (absorption_air_parameters, air_candidate, choose_speeds,
    hourly_weather, joint_calendar_profiles, operating_spectrum, parse_isd_record,
    period_for_hour, relative_humidity, wind_sector_diagnostics)
from quiet_uk.road_temporal import DAYS


RECORD = ('0204037720999992021010100004+51483-000450FM-12+002599999V020'
          '3601N001010012019N005000199+00051-00051100931'
          'ADDAA106000091MA1999999100621REMSYN003MA1')


def replace_at(line, a, b, text): return line[:a]+text+line[b:]


def report(stamp='202101010000', number=1, pressure='100621'):
    line = replace_at(RECORD, 15, 27, stamp)
    line = line.replace('100621REM', pressure+'REM')
    return parse_isd_record(line, number)


def national():
    def curve(group):
        return [dict(year=2021, hour_start=h, vehicle_group=group,
            weekday_indices={d:1.+h+(20 if d=='Friday' else 0) for d in DAYS},
            units='published index, not vehicles/hour') for h in range(24)]
    return dict(timing=dict(tra0307=dict(rows=curve('All motor vehicles')),
        tra0308=dict(rows=sum([curve(g) for g in ('Cars and Taxis','Light Commercial Vehicles','Heavy Goods Vehicles')],[]))))


@pytest.fixture(scope='module')
def weather(): return hourly_weather([])


@pytest.fixture(scope='module')
def calendar(weather): return joint_calendar_profiles(national(), weather)


@pytest.fixture(scope='module')
def coefficients():
    return json.loads((Path(__file__).parent/'fixtures/road_emission_2021_coefficients.json').read_bytes())


def traffic(**changes): return dict(dict.fromkeys(VEHICLE_FIELDS,0.),**changes)


def freeflow():
    keys=['Motorcycles','Cars','Light Commercial Vehicles','Rigid Heavy Goods Vehicles 2 axles',
          'Rigid Heavy Goods Vehicles 3 or more axles','Articulated Heavy Goods Vehicles 3 or 4 axles',
          'Articulated Heavy Goods Vehicles 5 or more axles']
    return [dict(year=2021, road_type='Motorways', published_vehicle_values={
        k+' [notes]':dict(original_cell='60',speed_kmh_if_same_vehicle_and_road_type=96.56064) for k in keys})]


def test_original_record_units_and_station_pressure_kind():
    r=report()
    assert r['temperature_c']['value']==.5 and r['dewpoint_c']['value']==-.5
    assert r['station_pressure_kpa']['value']==100.62
    assert r['sea_level_pressure_kpa']['value']==100.93
    assert r['wind_speed_m_s']['value']==1 and r['wind_from_degrees']['value']==360
    assert r['observation_location']==dict(latitude=51.483,longitude=-.45,elevation_m=25)
    assert r['report_type']=='FM-12'


@pytest.mark.parametrize('qc',['0','2','3','4','6','7','9','A'])
def test_nonaccepted_temperature_quality_is_preserved_as_unknown(qc):
    r=parse_isd_record(replace_at(RECORD,92,93,qc),1)
    assert r['temperature_c']['value'] is None
    assert r['temperature_c']['original_cell']=='+0005' and r['temperature_c']['quality_code']==qc
    assert r['derived_relative_humidity_percent'] is None


def test_missing_pressure_and_remark_token_never_use_sea_level_pressure():
    r=report(pressure='999999')
    assert r['station_pressure_kpa']['value'] is None
    assert r['sea_level_pressure_kpa']['value']==100.93
    assert air_candidate(r)['pressure_kpa']==101.325
    assert air_candidate(r)['field_basis']['pressure_kpa']=='explicit_standard_pressure_reference'
    assert parse_isd_record(RECORD.replace('MA1999999100621',''),1)['station_pressure_kpa']['value'] is None


def test_missing_sentinel_is_not_a_temperature_and_zero_is_valid():
    assert parse_isd_record(replace_at(RECORD,87,92,'+9999'),1)['temperature_c']['value'] is None
    assert parse_isd_record(replace_at(RECORD,87,92,'+0000'),1)['temperature_c']['value']==0


def test_wrong_year_station_type_and_duplicate_pressure_are_rejected():
    for line in [replace_at(RECORD,15,19,'2020'),replace_at(RECORD,4,10,'123456'),
                 replace_at(RECORD,41,46,'FM-99'),RECORD.replace('REM','MA1999999100621REM'),RECORD[:50]]:
        with pytest.raises(ValueError): parse_isd_record(line,1)


def test_humidity_numeric_reference_and_no_supersaturation_clipping():
    assert relative_humidity(20,10)==pytest.approx(52.5116545042,abs=1e-8)
    assert relative_humidity(0,0)==100
    for a,b in [(20,21),(None,10),(True,0),(-21,-22),(math.nan,0)]:
        assert relative_humidity(a,b) is None


def test_air_api_requires_explicit_assumption_acceptance():
    row=dict(candidate_absorption_air=air_candidate(report()))
    assert absorption_air_parameters(row) is None
    air=absorption_air_parameters(row,accept_uniform_station_transfer=True)
    assert set(air)=={'temperature_c','relative_humidity_percent','pressure_kpa','uniform_air'}
    assert atmospheric_absorption(1000,**air)['status']=='conditional_research'
    with pytest.raises(ValueError): absorption_air_parameters(row,accept_uniform_station_transfer='yes')


def test_fixed_gap_air_is_labelled_and_not_an_observation():
    row=hourly_weather([])[0]
    assert row['chosen_observation'] is None
    assert row['candidate_absorption_air']['status']=='assumed_gap_air'
    assert row['candidate_absorption_air']['temperature_c']==10


def test_hour_selection_keeps_joint_tuple_then_nearest_report():
    missing=report('202101010000',1,pressure='999999')
    complete=report('202101010020',2)
    rows=hourly_weather([missing,complete])
    assert rows[0]['original_reports_in_slot']==2
    assert rows[0]['chosen_observation']['record_number']==2
    assert rows[0]['chosen_observation']['observed_utc'].endswith('00:20:00+00:00')
    # Equal complete reports do not receive two hours of weight.
    exact=report('202101010000',3)
    assert hourly_weather([complete,exact])[0]['chosen_observation']['record_number']==3


def test_end_of_year_and_half_hour_mapping_do_not_create_extra_hours():
    rows=hourly_weather([report('202112312350')])
    assert len(rows)==8760 and all(r['chosen_observation'] is None for r in rows)
    rows=hourly_weather([report('202101010030')])
    assert rows[0]['chosen_observation'] is None and rows[1]['chosen_observation'] is not None


def test_elapsed_calendar_retains_dst_gap_fold_and_noise_period_boundaries(weather):
    assert len(weather)==8760
    spring=[r['local_time'] for r in weather if r['local_time'].startswith('2021-03-28T')]
    autumn=[r for r in weather if r['local_time'].startswith('2021-10-31T01:')]
    assert len(spring)==23 and not any('T01:' in s for s in spring)
    assert len(autumn)==2 and [r['local_fold'] for r in autumn]==[0,1]
    assert [period_for_hour(h) for h in (6,7,18,19,22,23)]==['night','day','day','evening','evening','night']
    assert sum(r['period']=='night' for r in weather)==2920


def test_joint_traffic_weights_conserve_aadf_and_weekday_volume(calendar,weather):
    group=calendar['groups']['All motor vehicles']
    w=group['slot_weights']
    assert math.fsum(w)==pytest.approx(1,abs=1e-14)
    assert math.fsum(1234*365*x for x in w)==pytest.approx(1234*365)
    friday=next(i for i,r in enumerate(weather) if r['local_time'].startswith('2021-01-01T07:'))
    monday=next(i for i,r in enumerate(weather) if r['local_time'].startswith('2021-01-04T07:'))
    assert w[friday]/w[monday]==pytest.approx(28/8)
    assert calendar['vehicle_profiles']['buses_and_coaches']['proxy_for_missing_vehicle_profile']
    assert not calendar['vehicle_profiles']['LGVs']['proxy_for_missing_vehicle_profile']


def test_night_stress_renormalises_the_whole_year(calendar,weather):
    doubled=joint_calendar_profiles(national(),weather,2)['groups']['All motor vehicles']
    old=calendar['groups']['All motor vehicles']['period_fractions'];new=doubled['period_fractions']
    assert new['night']/new['day']==pytest.approx(2*old['night']/old['day'])
    assert sum(new.values())==pytest.approx(1)


def test_calendar_refuses_deleted_duplicated_and_wrong_year_inputs(weather):
    with pytest.raises(ValueError): joint_calendar_profiles(national(),weather[:-1])
    damaged=deepcopy(weather);damaged[1]=damaged[0]
    with pytest.raises(ValueError): joint_calendar_profiles(national(),damaged)
    bad=national();bad['timing']['tra0307']['rows'][0]['year']=2020
    with pytest.raises(ValueError): joint_calendar_profiles(bad,weather)


def test_motorway_vehicle_speeds_are_conditional_and_bus_gap_is_explicit():
    result=choose_speeds(dict(road_number='M4',form_of_way='Collapsed Dual Carriageway'),'M4',freeflow())
    assert result['motorway_identity_candidate']
    assert result['choices']['cars_and_taxis']['candidate_kmh']==96.56064
    assert result['choices']['buses_and_coaches']['candidate_kmh']==70
    assert result['choices']['buses_and_coaches']['basis']=='explicit_motorway_missing_vehicle_reference'
    assert result['choices']['cars_and_taxis']['observed_local_kmh'] is None


@pytest.mark.parametrize('ref,donor,form',[('M4','M25','Collapsed Dual Carriageway'),('M4','M4','Slip Road'),
    ('M4','M4','Roundabout'),('A4','A4','Collapsed Dual Carriageway'),('','U','Single Carriageway')])
def test_unestablished_road_types_never_receive_motorway_speed(ref,donor,form):
    result=choose_speeds(dict(road_number=ref,form_of_way=form),donor,freeflow())
    assert not result['motorway_identity_candidate']
    assert all(v['candidate_kmh']==40 for v in result['choices'].values())


def test_national_speed_missing_stays_an_explicit_reference():
    rows=freeflow();rows[0]['published_vehicle_values']['Cars [notes]']=dict(original_cell='[x]',speed_kmh_if_same_vehicle_and_road_type=None)
    r=choose_speeds(dict(road_number='M4',form_of_way='Dual Carriageway'),'M4',rows)
    assert r['choices']['cars_and_taxis']['original_national_cell']=='[x]'
    assert r['choices']['cars_and_taxis']['candidate_kmh']==70


def test_per_class_speed_kernel_matches_frozen_kernel_for_common_speed(calendar,coefficients):
    categories=traffic(cars_and_taxis=1234,LGVs=25,HGVs_2_rigid_axle=300,buses_and_coaches=40)
    profiles=calendar['vehicle_profiles'];speeds=dict.fromkeys(VEHICLE_FIELDS,70.)
    result=operating_spectrum(categories,profiles,speeds,coefficients)
    reference=source_spectrum(categories,profiles,BASE_CASE,coefficients)
    for p in ('day','evening','night','den_penalised_source'):
        np.testing.assert_allclose(result['period_spectra'][p]['relative_power_per_m'],reference['period_spectra'][p]['relative_power_per_m'],rtol=1e-14)


def test_source_is_linear_in_flow_and_q_over_v_is_used(calendar,coefficients):
    speeds=dict.fromkeys(VEHICLE_FIELDS,40.);profiles=calendar['vehicle_profiles']
    a=operating_spectrum(traffic(cars_and_taxis=100),profiles,speeds,coefficients)
    b=operating_spectrum(traffic(cars_and_taxis=200),profiles,speeds,coefficients)
    np.testing.assert_allclose(np.array(b['period_spectra']['day']['relative_power_per_m']),2*np.array(a['period_spectra']['day']['relative_power_per_m']))
    c=coefficients['categories']['1'];v=40.;band=4
    single=10**((c['AR'][band]+c['BR'][band]*math.log10(v/70))/10)+10**((c['AP'][band]+c['BP'][band]*(v-70)/70)/10)
    q=100*profiles['cars_and_taxis']['period_fractions']['day']/12
    assert a['period_spectra']['day']['relative_power_per_m'][band]==pytest.approx(single*q/(1000*v))


def test_missing_category_and_unsupported_speed_withhold_but_zero_stays_zero(calendar,coefficients):
    speeds=dict.fromkeys(VEHICLE_FIELDS,40.);profiles=calendar['vehicle_profiles']
    zero=operating_spectrum(traffic(),profiles,speeds,coefficients)
    assert zero['period_spectra']['day']['zero_power']
    assert all(v is None for v in zero['period_spectra']['day']['sound_power_db_re_1e_minus_12_W_per_m'])
    assert operating_spectrum(traffic(LGVs=None),profiles,speeds,coefficients)['status']=='withheld'
    speeds['cars_and_taxis']=19
    assert operating_spectrum(traffic(),profiles,speeds,coefficients)['status']=='withheld'


def test_wind_from_to_conversion_and_sector_missingness_are_separate_from_acoustics():
    obs=report();obs['wind_from_degrees']['value']=270.;obs['wind_speed_m_s']['value']=2.
    rows=[dict(period='day',chosen_observation=obs),dict(period='day',chosen_observation=None)]
    east=wind_sector_diagnostics(rows,90)['day'];west=wind_sector_diagnostics(rows,270)['day']
    assert east['downwind_sector_hours']==1 and west.get('downwind_sector_hours',0)==0
    assert east['full_period_membership_lower']==.5 and east['full_period_membership_upper']==1
    assert east['probability_favourable'] is None
    assert wind_sector_diagnostics(rows,0)['day']['full_period_membership_upper']==.5


def test_wind_calm_and_variable_direction_are_not_fabricated():
    calm=report();calm['wind_speed_m_s']['value']=0.;calm['wind_from_degrees']['value']=None
    variable=report();variable['wind_from_degrees']['value']=None
    result=wind_sector_diagnostics([dict(period='night',chosen_observation=o) for o in (calm,variable)],0)['night']
    assert result['below_speed_threshold_hours']==1 and result['unknown_hours']==1
    assert result['probability_favourable'] is None
