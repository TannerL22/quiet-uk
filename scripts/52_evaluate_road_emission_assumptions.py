"""Freeze and evaluate explicit road-source cases against immutable inputs.

Conditional source power only. No tuning to native noise rasters, propagation,
receptor exposure, gap filling, local annual timing/speed qualification or release.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.road_emission import (BASE_CASE, PERIOD_HOURS, aggregate_components,
    assumption_cases, case_hour_weights, source_spectrum, survey_shape_check)
from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_temporal import number


def read_json(path):
    return json.loads(Path(path).read_bytes())


def verify_inventory(path):
    path = Path(path).resolve()
    if not path.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use workspace artifact parents')
    inv = read_json(path/'inventory.json')
    for name,digest in inv['files'].items():
        p = (path/name).resolve()
        if not p.is_relative_to(path) or file_hash(p)!=digest:
            raise ValueError('Parent integrity failed: '+str(p))
    return inv


def describe(values):
    return {'n':len(values),**{k:float(v) for k,v in zip(('min','p10','median','p90','max'),np.percentile(values,[0,10,50,90,100]))}} if values else {'n':0}


def conditional_free_flow_candidates(candidates):
    rows = []
    for row in candidates['free_flow_speeds']['rows']:
        rows.append({**row,'published_vehicle_values':{
            key:{'original_cell':cell,'speed_kmh_if_same_vehicle_and_road_type':None if number(cell) is None else number(cell)*1.609344}
            for key,cell in row['published_vehicle_values'].items()},
            'local_application':None,'status':'conditional_national_free_flow_candidate_not_assigned_to_a_local_link'})
    return dict(rows=rows,published_notes=candidates['free_flow_speeds']['published_notes'],mph_to_kmh=1.609344)


def run(output,sources):
    output, sources = Path(output).resolve(),Path(sources).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace output directory')
    parents = {n:ROOT/'artifacts'/n for n in ('road_direction_inputs_v2','road_direction_review_v1',
        'road_temporal_audit_v2','road_temporal_sources_v1')}
    parents[sources.name] = sources
    invs = {n:verify_inventory(p) for n,p in parents.items()}
    parent_hashes = {n:file_hash(p/'inventory.json') for n,p in parents.items()}
    review = read_json(parents['road_direction_review_v1']/'verification.json')
    if review['input_inventory_sha256']!=parent_hashes['road_direction_inputs_v2']:
        raise ValueError('Carriageway review is not for this input generation')
    input_report = read_json(parents['road_direction_inputs_v2']/'report.json')
    budgets = read_json(parents['road_direction_inputs_v2']/'carriageway_budgets.json')['groups']
    partitions = {p['count_point_id']:p for p in read_json(parents['road_direction_inputs_v2']/'donor_partitions.json')['points']}
    candidates = read_json(parents['road_temporal_audit_v2']/'national_2021_assumption_candidates.json')
    local = read_json(parents['road_temporal_audit_v2']/'local_survey_day_inputs.json')['points']
    coefficients = read_json(sources/'published_2021_coefficients.json')
    if candidates['reference_year']!=2021 or not 0<len(budgets)<=2000:
        raise ValueError('Expected bounded exact-2021 input')
    code_names = ['scripts/52_evaluate_road_emission_assumptions.py','src/quiet_uk/road_emission.py',
                  'src/quiet_uk/road_temporal.py','src/quiet_uk/road_input_audit.py','src/quiet_uk/explorer.py']
    code_hashes = {n:file_hash(ROOT/n) for n in code_names}
    cases = assumption_cases()
    case_profiles = {c['case_id']:{f:case_hour_weights(candidates,f,c) for f in VEHICLE_FIELDS} for c in cases}
    protocol = dict(schema_version=1,status='frozen_conditional_research_protocol',reference_year=2021,
        runtime=dict(python=sys.version,implementation=platform.python_implementation(),platform=platform.platform(),
                     numpy=np.__version__,matplotlib=matplotlib.__version__,plot_backend=matplotlib.get_backend()),
        method='JRC72550 equations III-1, III-3/4, III-5, III-11, III-19/20 with published 2021/1226 F-1/F-4 coefficients',
        method_complete_regulatory_annex_qualification=False,
        source_band_extension='Uses the eight 2021 coefficient bands (63 Hz to 8 kHz) in the checked source equations; a complete current regulatory implementation is not claimed.',
        comparison_anchor='national_vehicle timing; common 70 km/h; reference surface; buses category 2; two-wheelers category 4b. Numerical comparison only, not a selected best estimate.',
        cases=cases,period_hours={p:list(h) for p,h in PERIOD_HOURS.items()},
        clock_convention='Europe/London nominal civil-hour annual strategic periods: 12/4/8 hours; not an elapsed-time monitoring series or a DST reconstruction.',
        calendar_weighting='2021 occurrence counts over all 168 weekday/hour index cells; no independent daily normalisation.',
        proxy_profiles={'two_wheeled_motor_vehicles':'All motor vehicles','buses_and_coaches':'All motor vehicles',
                        'HGV axle subclasses':'Heavy Goods Vehicles'},
        vehicle_crosswalk={'cars_and_taxis':'1','LGVs':'1','HGVs_2_rigid_axle':'2 (rear twin tyres assumed, not observed)',
                           'HGVs_3_plus_and_articulated':'3','buses_and_coaches':'2 vs 3; axle configuration unknown',
                           'two_wheeled_motor_vehicles':'all 4b vs all 4a; engine size unknown; all-4a case assumes 40 km/h'},
        source_conditions={'flat_road':True,'steady_speed':True,'dry_surface':True,'air_temperature_c':20,
                           'studded_tyre_fraction':0,'source_height_above_road_m':.05,
                           'status':'reference assumptions; not measured local conditions'},
        surface_rule='F-4 alpha+beta*log10(v/70) on rolling, min(alpha,0) on propulsion; no second pavement absorption correction later.',
        surface_classification='All three codes are hypothetical comparison cases; actual local pavement and its acoustic condition remain null.',
        missing_rule='Missing original vehicle category withholds all source spectra. Zero power stays linear zero; logarithmic zero is null with an explicit zero flag.',
        rounding_rule='Use original directional category estimates once; retain their residual against published all-vehicle total; no balancing or extra whole-road emission.',
        scope_rule='Only previously qualified provisional carriageway budgets. All other physical links remain excluded; no traffic propagation to neighbouring roads.',
        sensitivity_interpretation='Fixed stress/comparison cases; extrema are not calibrated probabilities or guaranteed error bounds.',
        qualified_local_annual_period_flows=None,observed_local_speed_kmh=None,observed_local_pavement_type=None,
        receptor_noise_predictions_created=False,eligible_for_absolute_noise_release=False,
        parent_inventory_sha256=parent_hashes,code_sha256=code_hashes,
        source_licences={**input_report['source_licences'],'method_reference':{
            'attribution':'European Union; JRC72550 (2012), DOI 10.2788/31776; Directive 2021/1226 Appendix F',
            'urls':['https://publications.jrc.ec.europa.eu/repository/handle/JRC72550','https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj/eng']}})

    # Keep 722 spatial groups, but evaluate identical donor components once. No
    # network flow sum and no pretence that repeated matched links are samples.
    donor_inputs, group_rows, seen_links = {}, [], set()
    for g in budgets:
        donor = g['count_point_id']
        components = [c for r in g['representations'] for c in r['components']]
        categories = aggregate_components(components)
        expected = aggregate_components(partitions[donor]['streams'])
        if categories!=expected or sum(c['flow'] for c in components)!=g['published_stream_total']:
            raise ValueError('Budget streams differ from frozen donor')
        if donor in donor_inputs and donor_inputs[donor]['annual_categories']!=categories:
            raise ValueError('Nonidentical donor representations need separate evaluation identities')
        donor_inputs[donor] = dict(count_point_id=donor,annual_categories=categories,
                                  original_published_stream_total=g['published_stream_total'],
                                  category_sum_residual=None if any(v is None for v in categories.values()) else sum(categories.values())-g['published_stream_total'],
                                  estimation_method=partitions[donor]['reference_all_direction_aadf']['estimation_method'])
        for r in g['representations']:
            if r['link_id'] in seen_links: raise ValueError('Multiple source budgets for one physical link')
            seen_links.add(r['link_id'])
            group_rows.append(dict(group_id=g['group_id'],link_id=r['link_id'],evaluation_donor_id=donor,
                status='provisional_geometry_association_conditional_emission',original_primary_flow=r['flow']))
    evaluations, summaries = [], []
    by_case = defaultdict(dict)
    for case in cases:
        for donor,inputs in sorted(donor_inputs.items()):
            result = source_spectrum(inputs['annual_categories'],case_profiles[case['case_id']],case,coefficients)
            evaluation = dict(evaluation_id=case['case_id']+':'+donor,case_id=case['case_id'],**inputs,**result)
            evaluations.append(evaluation)
            by_case[case['case_id']][donor] = evaluation
    anchor = by_case['anchor']
    periods = [*PERIOD_HOURS,'den_penalised_source']
    for case in cases:
        summary = dict(case_id=case['case_id'],status_counts=dict(Counter(v['status'] for v in by_case[case['case_id']].values())),
                       withheld_reasons=dict(Counter(v['reason'] for v in by_case[case['case_id']].values() if v['status']=='withheld')),
                       source_delta_db_by_period_and_band={})
        for p in periods:
            summary['source_delta_db_by_period_and_band'][p] = {}
            for j,freq in enumerate(coefficients['frequencies_hz']):
                values = []
                for donor,e in by_case[case['case_id']].items():
                    if e['status']=='withheld' or anchor[donor]['status']=='withheld': continue
                    now = e['period_spectra'][p]['sound_power_db_re_1e_minus_12_W_per_m'][j]
                    ref = anchor[donor]['period_spectra'][p]['sound_power_db_re_1e_minus_12_W_per_m'][j]
                    if now is not None and ref is not None: values.append(now-ref)
                summary['source_delta_db_by_period_and_band'][p][str(freq)] = describe(values)
        summaries.append(summary)
    surveys = [r for p in local for r in survey_shape_check(p,candidates)]
    if len(surveys)>100: raise ValueError('Unbounded daytime diagnostic')
    survey_summary = dict(survey_days=len(surveys),
        national_shape_total_variation=describe([r['national_total_variation_distance'] for r in surveys]),
        uniform_shape_total_variation=describe([r['uniform_total_variation_distance'] for r in surveys]),
        national_better_than_uniform_count=sum(r['national_total_variation_distance']<r['uniform_total_variation_distance'] for r in surveys),
        inference='single survey-day 07-19 shape check; does not validate annual or night profiles')
    report = dict(protocol_status=protocol['status'],source_generations={n:v.get('generation_id',v.get('audit_id')) for n,v in invs.items()},
        input_summary=input_report['summary'],coverage=input_report['coverage'],provisional_spatial_groups=len(budgets),
        unique_evaluated_donors=len(donor_inputs),scenario_cases=len(cases),donor_case_evaluations=len(evaluations),
        evaluation_status_counts=dict(Counter(e['status'] for e in evaluations)),
        donor_category_sum_residuals=dict(Counter(v['category_sum_residual'] for v in donor_inputs.values())),
        sensitivity=summaries,daytime_profile_diagnostic=survey_summary,
        eligible_for_absolute_noise_release=False,receptor_noise_predictions_created=False,
        study=input_report['study'],runtime=protocol['runtime'],parent_inventory_sha256=parent_hashes,code_sha256=code_hashes,
        limitations=[protocol[k] for k in ('comparison_anchor','clock_convention','surface_classification','scope_rule','sensitivity_interpretation','source_band_extension')]+
            ['Source sound power per metre is not sound pressure at a receiver. Frequency bands are unweighted, not dB(A).',
             'Terrain, buildings, ground, barriers, meteorology, diffraction and reflections are not propagated in this increment.',
             'Junction/gradient/temperature/studded-tyre corrections are not inferred from local evidence; reference source conditions are assumptions.',
             'National 2021 traffic was affected by COVID-19 and historic revisions. Surveys do not observe evenings, nights, seasons or continuous annual traffic.',
             'The 2015/consolidated legal annex was not retrieved completely. This checked research subset is not regulatory CNOSSOS compliance.'])
    output.mkdir(parents=True)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    (output/'case_period_profiles.json').write_bytes(json_bytes(case_profiles))
    (output/'donor_source_scenarios.json').write_bytes(json_bytes(dict(evaluations=evaluations)))
    (output/'group_evaluation_index.json').write_bytes(json_bytes(dict(groups=group_rows)))
    (output/'daytime_profile_diagnostics.json').write_bytes(json_bytes(dict(diagnostics=surveys,summary=survey_summary)))
    (output/'conditional_national_free_flow_candidates.json').write_bytes(json_bytes(conditional_free_flow_candidates(candidates)))
    (output/'report.json').write_bytes(json_bytes(report))
    shutil.copyfile(sources/'published_2021_coefficients.json',output/'published_2021_coefficients.json')
    for name in code_names:
        dest = output/'code'/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(ROOT/name,dest)
    with (output/'source_sensitivity_1000hz.csv').open('w',encoding='utf-8',newline='') as f:
        writer = csv.DictWriter(f,fieldnames=['case_id','period','n','min','p10','median','p90','max'])
        writer.writeheader()
        for s in summaries:
            for p in periods: writer.writerow(dict(case_id=s['case_id'],period=p,**s['source_delta_db_by_period_and_band'][p]['1000']))
    plot_summaries = [s for s in summaries if s['case_id'] not in ('anchor','invalid_worked_at_40')]
    labels = [s['case_id'].replace('_',' ') for s in plot_summaries]
    fig,axes = plt.subplots(1,2,figsize=(13,7),constrained_layout=True)
    for ax,p,title in ((axes[0],'night','Night source'),(axes[1],'den_penalised_source','Day/evening/night penalised source')):
        stats = [s['source_delta_db_by_period_and_band'][p]['1000'] for s in plot_summaries]
        y = np.arange(len(stats))
        for i,s in enumerate(stats):
            if not s['n']: continue
            ax.plot([s['min'],s['max']],[i,i],color='#a5c8c8',linewidth=3)
            ax.plot([s['p10'],s['p90']],[i,i],color='#178581',linewidth=6)
            ax.scatter(s['median'],i,c='#233744',s=26,zorder=3)
        ax.axvline(0,color='#777777',linewidth=.7)
        ax.set_yticks(y,labels); ax.invert_yaxis(); ax.grid(axis='x',alpha=.2)
        ax.set_title(title); ax.set_xlabel('Conditional 1 kHz source-power change from anchor (dB)')
    fig.suptitle('Road-emission assumption sensitivity | source power only, no receiver noise\n'
        f'{len(donor_inputs)} unique traffic donors; marker: median, thick bar: p10–p90, pale bar: min–max',fontsize=12)
    fig.savefig(output/'source_assumption_sensitivity.png',dpi=160)
    plt.close(fig)
    for n,h in code_hashes.items():
        if file_hash(ROOT/n)!=h: raise ValueError('Code changed during evaluation')
    for n,h in parent_hashes.items():
        verify_inventory(parents[n])
        if file_hash(parents[n]/'inventory.json')!=h: raise ValueError('Parent changed during evaluation')
    files = {p.relative_to(output).as_posix():file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output/'inventory.json').write_bytes(json_bytes(dict(
        generation_id='road-emission-assumptions-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20],files=files,
        receptor_noise_predictions_created=False)))
    print(json.dumps({k:report[k] for k in ('provisional_spatial_groups','unique_evaluated_donors','scenario_cases',
        'donor_case_evaluations','evaluation_status_counts','donor_category_sum_residuals','daytime_profile_diagnostic')}),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'artifacts/road_emission_assumptions_v2'))
    parser.add_argument('--sources',default=str(ROOT/'artifacts/road_emission_sources_v1'))
    args = parser.parse_args()
    run(args.output,args.sources)
