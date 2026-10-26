"""Independently check operating inputs against original records and equations.

Never imports the evaluated operating-input module/runner or source/air kernels.
"""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timedelta, timezone
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('inventory_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


def read(path): return json.loads(Path(path).read_bytes())


def close(a,b,tol=2e-12):
    assert math.isclose(a,b,rel_tol=tol,abs_tol=tol),(a,b)


def original_air(line):
    def value(a,b,q,missing,scale,lo,hi):
        n=int(line[a:b]);v=n/scale
        return v if n!=missing and line[q] in '15' and lo<=v<=hi else None
    t=value(87,92,92,9999,10,-93.2,61.8);d=value(93,98,98,9999,10,-98.2,36.8)
    optional=line[105:]
    stop=min([optional.find(tag) for tag in ('REM','EQD') if tag in optional]+[len(optional)])
    optional=optional[:stop];pos=optional.find('MA1');p=None
    if pos>=0:
        n=int(optional[pos+9:pos+14]);q=optional[pos+14]
        if n!=99999 and q in '15' and 4500<=n<=10900:p=n/100
    rh=None
    if t is not None and d is not None and -20<=t<=50 and -80<=d<=t:
        e=6.112*math.exp(17.67*d/(243.5+d));es=6.112*math.exp(17.67*t/(243.5+t));rh=100*e/es
    full=t is not None and -20<=t<=50 and rh is not None and 10<=rh<=100 and p is not None and 50<=p<=120
    return t,d,p,rh,full


def scalar_alpha(f,t,rh,p):
    # ISO pure-tone formula; saturation ratio and relaxation expressed independently.
    T=t+273.15;Tr=T/293.15;Pr=p/101.325
    h=rh*10**(4.6151-6.8346*(273.16/T)**1.261)/Pr
    o=Pr*(24+40400*h*(.02+h)/(.391+h))
    n=Pr/math.sqrt(Tr)*(9+280*h*math.exp(-4.17*(Tr**(-1/3)-1)))
    classical=1.84e-11/Pr*math.sqrt(Tr)
    oxygen=.01275*math.exp(-2239.1/T)*o/(o*o+f*f)
    nitrogen=.1068*math.exp(-3352/T)*n/(n*n+f*f)
    return 8686*f*f*(classical+Tr**(-2.5)*(oxygen+nitrogen))


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace generation required')
    helper.verified(inputs);report=read(inputs/'report.json');parents={n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==report['parent_inventory_sha256'][n]
    hashes={'input_inventory_sha256':file_hash(inputs/'inventory.json')};counts=Counter()
    evidence=next(p for n,p in parents.items() if n.startswith('road_operating_evidence'))
    original=gzip.decompress((evidence/'heathrow-2021.gz').read_bytes()).decode('ascii').splitlines()
    air=[original_air(line) for line in original]
    candidates=defaultdict(list)
    for i,line in enumerate(original):
        assert line[4:15]=='03772099999' and line[15:19]=='2021'
        stamp=datetime.strptime(line[15:27],'%Y%m%d%H%M').replace(tzinfo=timezone.utc)
        slot=(stamp+timedelta(minutes=30)).replace(minute=0,second=0,microsecond=0)
        if slot.year==2021:candidates[slot].append((not air[i][4],abs((stamp-slot).total_seconds()),line[41:46]!='FM-12',i))
    with (inputs/'London.tzif').open('rb') as stream: london=ZoneInfo.from_file(stream,key='Europe/London')
    assert file_hash(inputs/'London.tzif')==report['London_tzif_sha256']
    weather=read(inputs/'hourly_weather.json')['rows'];assert len(weather)==8760
    start=datetime(2021,1,1,tzinfo=timezone.utc)
    fielddefs={'temperature_c':(87,92,92,9999,10,-93.2,61.8),'dewpoint_c':(93,98,98,9999,10,-98.2,36.8),
               'wind_speed_m_s':(65,69,69,9999,10,0,90),'wind_from_degrees':(60,63,63,999,1,1,360),
               'sea_level_pressure_kpa':(99,104,104,99999,100,86,110)}
    local_times=[]
    for i,row in enumerate(weather):
        slot=start+timedelta(hours=i);local=slot.astimezone(london);local_times.append(local)
        assert row['slot_utc']==slot.isoformat() and row['local_time']==local.isoformat() and row['local_fold']==local.fold
        period='day' if 7<=local.hour<19 else 'evening' if 19<=local.hour<23 else 'night'
        assert row['period']==period and row['original_reports_in_slot']==len(candidates[slot])
        chosen=row['chosen_observation'];expected=min(candidates[slot])[3] if candidates[slot] else None
        if expected is None:
            assert chosen is None;values=(None,None,None,None,False)
        else:
            assert chosen['record_number']==expected+1
            line=original[expected];values=air[expected]
            assert chosen['report_type']==line[41:46]
            assert chosen['observed_utc']==datetime.strptime(line[15:27],'%Y%m%d%H%M').replace(tzinfo=timezone.utc).isoformat()
            assert chosen['observation_location']==dict(latitude=int(line[28:34])/1000,longitude=int(line[34:41])/1000,elevation_m=int(line[46:51]))
            for field,(a,b,q,missing,scale,lo,hi) in fielddefs.items():
                cell=chosen[field];n=int(line[a:b]);v=n/scale
                val=v if n!=missing and line[q] in '15' and lo<=v<=hi else None
                if field=='wind_from_degrees' and line[64] in 'CV9':val=None
                assert cell['original_cell']==line[a:b] and cell['quality_code']==line[q] and cell['value']==val
            assert chosen['station_pressure_kpa']['value']==values[2]
            if values[3] is None:assert chosen['derived_relative_humidity_percent'] is None
            else:close(chosen['derived_relative_humidity_percent'],values[3])
            counts['selected_original_observations_checked']+=1
        t,d,p,rh,full=values;candidate=row['candidate_absorption_air']
        expected_t=t if t is not None and -20<=t<=50 else 10.
        expected_rh=rh if rh is not None and 10<=rh<=100 else 70.
        expected_p=p if p is not None and 50<=p<=120 else 101.325
        close(candidate['temperature_c'],expected_t);close(candidate['relative_humidity_percent'],expected_rh);close(candidate['pressure_kpa'],expected_p)
        assert candidate['uniform_air'] is True
        assert (candidate['status']=='station_derived_uniform_path_assumption')==full
        if chosen is None:
            assert candidate['status']=='assumed_gap_air'
            assert set(candidate['field_basis'].values())=={'explicit_gap_reference'}
        else:
            assert candidate['field_basis']==dict(
                temperature_c='station_transfer' if t is not None and -20<=t<=50 else 'explicit_gap_reference',
                relative_humidity_percent='derived_station_transfer' if rh is not None and 10<=rh<=100 else 'explicit_gap_reference',
                pressure_kpa='station_transfer' if p is not None and 50<=p<=120 else 'explicit_standard_pressure_reference')
        counts['elapsed_weather_hours_checked']+=1
    # Rebuild national weekday/civil-hour weights from the immutable national tables.
    national=read(parents['road_temporal_audit_v2']/'national_2021_assumption_candidates.json')
    calendars=read(inputs/'traffic_weather_calendars.json')['calendars'];fractions={}
    for identity,multiplier in (('national_vehicle',1.),('night_half',.5),('night_double',2.)):
        fractions[identity]={}
        for group,record in calendars[identity]['groups'].items():
            table=national['timing']['tra0307' if group=='All motor vehicles' else 'tra0308']['rows']
            lookup={r['hour_start']:r for r in table if r['vehicle_group']==group};assert len(lookup)==24
            raw=[lookup[t.hour]['weekday_indices'][t.strftime('%A')]*(multiplier if row['period']=='night' else 1.) for t,row in zip(local_times,weather)]
            normalizer=math.fsum(raw);weights=[v/normalizer for v in raw]
            assert len(record['slot_weights'])==8760
            for actual,expected in zip(record['slot_weights'],weights):close(actual,expected,tol=1e-14)
            close(math.fsum(weights),1.)
            shares={p:math.fsum(w for w,row in zip(weights,weather) if row['period']==p) for p in ('day','evening','night')}
            for p,v in shares.items():close(record['period_fractions'][p],v)
            fractions[identity][group]=shares;counts['annual_slot_weights_checked']+=len(weights)
    scene=read(parents['real_road_scenes_v2']/'source_lines.json');source_by_id={r['link_id']:r for r in scene['source_roads']}
    with (parents['road_direction_inputs_v2']/'road_representation_ledger_bng.csv').open(encoding='utf-8',newline='') as f:ledger={r['link_id']:r for r in csv.DictReader(f)}
    partitions={r['count_point_id']:r for r in read(parents['road_direction_inputs_v2']/'donor_partitions.json')['points']}
    # Newly acquired one-day counts retain their original categories and identity.
    api=[r for path in sorted(evidence.glob('raw-counts-page-*.json')) for r in read(path)['data']]
    extra=read(inputs/'additional_donor_surveys.json')['points'];extra_by_id={p['count_point_id']:p for p in extra}
    assert set(extra_by_id)==set(read(evidence/'manifest.json')['selected_additional_donors'])
    assert sum(len(p['raw_rows']) for p in extra)==len(api)==72
    for original_count in api:
        identity=str(original_count['count_point_id']);point=partitions[identity]['reference_all_direction_aadf']
        row=next(r for r in extra_by_id[identity]['raw_rows'] if (r['count_date'],r['direction'],r['hour_start'])==
                 (original_count['count_date'],original_count['direction_of_travel'],original_count['hour']))
        assert row['flow']==original_count['all_motor_vehicles'] and row['hgv_flow']==original_count['all_hgvs']
        assert row['vehicle_categories']=={f:original_count[f.lower()] for f in point['vehicle_categories']}
        assert row['coordinate_match_to_annual_input']==(original_count['easting']==point['easting'] and original_count['northing']==point['northing'])
        assert row['road_identity_match_to_annual_input']==(original_count['road_name']==point['road_name'] and original_count['road_type']==point['road_type'])
        counts['new_original_survey_records_checked']+=1
    surveys={p['count_point_id']:p for p in read(parents['road_temporal_audit_v2']/'local_survey_day_inputs.json')['points']+extra}
    fields=tuple(source_by_id[next(iter(source_by_id))]['traffic_budget']['representations'][0]['components'][0]['vehicle_categories'])
    crosswalk={'two_wheeled_motor_vehicles':'Motorcycles','cars_and_taxis':'Cars','LGVs':'Light Commercial Vehicles',
       'HGVs_2_rigid_axle':'Rigid Heavy Goods Vehicles 2 axles','HGVs_3_rigid_axle':'Rigid Heavy Goods Vehicles 3 or more axles',
       'HGVs_4_or_more_rigid_axle':'Rigid Heavy Goods Vehicles 3 or more axles','HGVs_3_or_4_articulated_axle':'Articulated Heavy Goods Vehicles 3 or 4 axles',
       'HGVs_5_articulated_axle':'Articulated Heavy Goods Vehicles 5 or more axles','HGVs_6_articulated_axle':'Articulated Heavy Goods Vehicles 5 or more axles'}
    speed_table=next(r for r in national['free_flow_speeds']['rows'] if r['road_type']=='Motorways')
    coefficients=read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    records=read(inputs/'road_operating_inputs.json')['roads'];assert len(records)==722 and {r['link_id'] for r in records}==set(source_by_id)
    for record in records:
        link=record['link_id'];source=source_by_id[link];road=ledger[link];donor=partitions[record['count_point_id']]
        components=next(r for r in source['traffic_budget']['representations'] if r['link_id']==link)['components']
        assert record['original_traffic_components']==components
        annual={f:math.fsum(c['vehicle_categories'][f] for c in components) for f in fields};assert record['annual_categories']==annual
        assert record['source_cell_ids']==[c['segment_id'] for c in source['source_cells']]
        assert record['original_source_emission_id']==source['source_emission_id']
        motorway=bool(re.fullmatch('M[0-9]+',road['road_number'])) and road['road_number']==donor['reference_all_direction_aadf']['road_name'] and road['form_of_way'] in ('Collapsed Dual Carriageway','Dual Carriageway')
        assert record['speed']['motorway_identity_candidate']==motorway
        base_speeds={}
        for field,choice in record['speed']['choices'].items():
            if motorway and field in crosswalk:
                cell=next(v for k,v in speed_table['published_vehicle_values'].items() if k.split(' [')[0]==crosswalk[field])
                expected=float(cell)*1.609344 if cell.replace('.','',1).isdigit() else 70.
            else:expected=70. if motorway else 40.
            close(choice['candidate_kmh'],expected);assert choice['observed_local_kmh'] is None;base_speeds[field]=expected
            expected_basis=('2021_national_motorway_free_flow_transfer' if motorway and field in crosswalk
                            else 'explicit_motorway_missing_vehicle_reference' if motorway else 'explicit_unknown_road_speed_reference')
            assert choice['basis']==expected_basis
            counts['vehicle_speed_assignments_checked']+=1
        assert record['observed_local_period_flows'] is None and record['observed_local_speed_kmh'] is None and record['receiver_noise_db'] is None
        assert record['observed_survey_day_support']==any(s['complete_published_07_19_support'] and s['annual_input_identity_compatible'] for s in surveys[record['count_point_id']]['survey_days'])
        assert record['release_status']=='withheld_unqualified_physical_scene_and_incomplete_network'
        assert record['conditional_baseline']['source_temperature_c']==20. and record['conditional_baseline']['actual_source_temperature_c'] is None
        assert record['qualified_favourable_probability'] is None and record['favourable_probability_cases']==[0.,.5,1.]
        assert len(record['conditional_cases'])==7
        for case in record['conditional_cases']:
            identity=case['case_id'];speeds=case['candidate_speeds_kmh'];calendar=case['calendar_id']
            expected_speeds=dict.fromkeys(fields,float(identity.rsplit('_',1)[1])) if identity.startswith('common_speed_') else base_speeds
            assert speeds==expected_speeds
            spectrum={p:[0.]*8 for p in ('day','evening','night')}
            for field in fields:
                category='4b' if field=='two_wheeled_motor_vehicles' else '1' if field in ('cars_and_taxis','LGVs') else '2' if field in ('buses_and_coaches','HGVs_2_rigid_axle') else '3'
                group='Cars and Taxis' if field=='cars_and_taxis' else 'Light Commercial Vehicles' if field=='LGVs' else 'Heavy Goods Vehicles' if field.startswith('HGVs_') else 'All motor vehicles'
                share=fractions[calendar][group];close(sum(annual[field]*v for v in share.values()),annual[field])
                if identity=='baseline':
                    for period,duration in (('day',12),('evening',4),('night',8)):
                        traffic=record['conditional_baseline']['period_traffic'][field][period]
                        close(traffic['assumed_daily_vehicles_in_period'],annual[field]*share[period])
                        close(traffic['assumed_average_vehicles_per_hour'],annual[field]*share[period]/duration)
                        counts['baseline_period_vehicle_flows_checked']+=1
                v=speeds[field];c=coefficients['categories'][category]
                for band in range(8):
                    propulsion=10**((c['AP'][band]+c['BP'][band]*(v-70)/70)/10)
                    rolling=0. if category=='4b' else 10**((c['AR'][band]+c['BR'][band]*math.log10(v/70))/10)
                    for period,duration in (('day',12),('evening',4),('night',8)):
                        spectrum[period][band]+=(rolling+propulsion)*annual[field]*share[period]/duration/(1000*v)
            spectrum['den_penalised_source']=[(12*spectrum['day'][k]+4*10**.5*spectrum['evening'][k]+80*spectrum['night'][k])/24 for k in range(8)]
            for period,values in spectrum.items():
                actual=case['period_spectra'][period]
                for band,power in enumerate(values):
                    close(actual['relative_power_per_m'][band],power)
                    if power>0:close(actual['sound_power_db_re_1e_minus_12_W_per_m'][band],10*math.log10(power))
                    else:assert actual['sound_power_db_re_1e_minus_12_W_per_m'][band] is None
                    counts['scalar_source_band_values_checked']+=1
            if identity=='baseline':assert case['period_spectra']==record['conditional_baseline']['period_spectra']
            counts['conditional_source_cases_checked']+=1
        counts['road_assignments_checked']+=1
    # Wind membership is checked with a vector dot product, not a wrap-angle helper.
    sectors=read(inputs/'station_wind_sectors.json')['bearings']
    for bearing,periods in sectors.items():
        angle=math.radians(float(bearing));counts_by_period={p:Counter() for p in ('day','evening','night')}
        for row in weather:
            c=counts_by_period[row['period']];c['expected_hours']+=1;o=row['chosen_observation']
            speed=o['wind_speed_m_s']['value'] if o else None;direction=o['wind_from_degrees']['value'] if o else None
            if speed is None:c['unknown_hours']+=1
            elif speed<.5:c['below_speed_threshold_hours']+=1
            elif direction is None:c['unknown_hours']+=1
            else:
                c['known_direction_hours']+=1;a=math.radians(direction+180)
                if math.sin(a)*math.sin(angle)+math.cos(a)*math.cos(angle)>=.5-1e-12:c['downwind_sector_hours']+=1
        for period,c in counts_by_period.items():
            actual=periods[period]
            for k in ('expected_hours','unknown_hours','below_speed_threshold_hours','known_direction_hours','downwind_sector_hours'):assert actual.get(k,0)==c[k]
            close(actual['full_period_membership_lower'],c['downwind_sector_hours']/c['expected_hours'])
            close(actual['full_period_membership_upper'],(c['downwind_sector_hours']+c['unknown_hours'])/c['expected_hours'])
            assert actual['probability_favourable'] is None;counts['station_wind_sector_periods_checked']+=1
    for case in read(inputs/'air_absorption_diagnostics.json')['cases']:
        k=coefficients['frequencies_hz'].index(case['nominal_label_hz']);frequency=1000*10**(.3*(k-4));close(case['absorption_frequency_hz'],frequency)
        terms=[];weights=[]
        for i,row in enumerate(weather):
            if row['period']!=case['period']:continue
            a=row['candidate_absorption_air'];alpha=scalar_alpha(frequency,a['temperature_c'],a['relative_humidity_percent'],a['pressure_kpa'])
            w=calendars['national_vehicle']['groups']['All motor vehicles']['slot_weights'][i]
            terms.append(w*10**(-alpha/10));weights.append(w);counts['independent_hourly_absorption_coefficients_checked']+=1
        close(case['traffic_weighted_air_only_attenuation_db'],-10*math.log10(math.fsum(terms)/math.fsum(weights)))
        assert case['receiver_noise_db'] is None;counts['joint_energy_absorption_averages_checked']+=1
    assert file_hash(inputs/'road_input_inventory_bng.csv')==file_hash(parents['real_road_scenes_v2']/'road_input_inventory_bng.csv')
    assert read(inputs/'road_operating_inputs.json')['combined_road_noise_db'] is None
    assert not report['eligible_for_absolute_noise_release']
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==report['parent_inventory_sha256'][n]
    helper.verified(inputs);assert file_hash(inputs/'inventory.json')==hashes['input_inventory_sha256']
    output.mkdir(parents=True);shutil.copyfile(__file__,output/Path(__file__).name)
    result=dict(status='passed_independent_operating_input_checks',counts=dict(counts),**hashes,
        verification_code_sha256=file_hash(__file__),original_budgets_preserved=True,
        all_receiver_levels_withheld=True,scenario_spreads_are_statistical_intervals=False,
        full_current_method_qualified=False,real_area_noise_validated=False)
    (output/'verification.json').write_bytes(json_bytes(result))
    print(dict(generation_id=helper.seal(output,'road-operating-review-'),**result),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',type=Path,default=ROOT/'artifacts/road_operating_inputs_v2')
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts/road_operating_review_v2')
    args=parser.parse_args();run(args.inputs,args.output)
