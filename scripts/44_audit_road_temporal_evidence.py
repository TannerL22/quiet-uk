"""Audit acquired temporal road inputs offline; export no annual flow allocation."""
import argparse
from collections import Counter
import csv
import json
from pathlib import Path
import platform
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.road_temporal import audit_temporal_records, exact_year_indices, exact_year_speed_rows, ods_sheets
from quiet_uk.road_input_audit import load_exact_traffic

SOURCE = ROOT / 'artifacts/road_temporal_sources_v1'
AUDIT = ROOT / 'artifacts/road_input_audit_v1'
LINKAGE = ROOT / 'artifacts/minor_road_linkage_v3'
DOCUMENTATION = ROOT / 'artifacts/road_temporal_documentation_v1'
DISCOVERY = ROOT / 'artifacts/road_temporal_webtris_probe_v1'
INPUTS = (SOURCE, AUDIT, LINKAGE, DOCUMENTATION, DISCOVERY)


def verified_inventory(path):
    inventory = json.loads((path / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        target = (path / name).resolve()
        if not target.is_relative_to(path.resolve()) or file_hash(target) != digest:
            raise ValueError('Input inventory mismatch')
    return inventory


def run(output):
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(ROOT / 'data'):
        raise ValueError('Use a new output outside frozen data')
    identities = {path.name: verified_inventory(path) for path in INPUTS}
    manifest = json.loads((SOURCE / 'manifest.json').read_bytes())
    parent = json.loads((AUDIT / 'report.json').read_bytes())
    if manifest['parent_audit_id'] != identities[AUDIT.name]['audit_id']:
        raise ValueError('Source parent differs from frozen study')
    source_csv = 'data/raw/phase2b_road/aadf/dft_traffic_counts_aadf.csv'
    if file_hash(ROOT / source_csv) != parent['source_and_code_sha256'][source_csv]:
        raise ValueError('Frozen annual traffic changed')
    points, _ = load_exact_traffic(ROOT / source_csv, 2021)
    selected = set(manifest['selected_count_point_ids'])
    points = [row for row in points if row['count_point_id'] in selected]
    if len(points) != len(selected):
        raise ValueError('Missing frozen annual point')
    payloads = {}
    for endpoint in ('raw-counts', 'average-annual-daily-flow-by-direction'):
        payloads[endpoint] = [row for path in sorted(SOURCE.glob(endpoint + '-page-*.json'))
                             for row in json.loads(path.read_bytes())['data']]
        if len(payloads[endpoint]) != manifest['api_results'][endpoint]['rows']:
            raise ValueError('Incomplete acquired endpoint')
    code_names = ('scripts/44_audit_road_temporal_evidence.py', 'src/quiet_uk/road_temporal.py',
                  'src/quiet_uk/road_input_audit.py', 'src/quiet_uk/explorer.py')
    code_hashes = {name: file_hash(ROOT / name) for name in code_names}
    decisions = audit_temporal_records(points, payloads['raw-counts'],
                                      payloads['average-annual-daily-flow-by-direction'])
    indices = {}
    for name in ('tra0307', 'tra0308'):
        sheets = ods_sheets(SOURCE / (name + '.ods'))
        indices[name] = {'rows': exact_year_indices(sheets, name.upper()),
                         'published_notes': sheets['Notes'], 'cover': sheets['Cover_sheet'],
                         'source_scope': sheets['Table_of_contents'],
                         'local_application': None}
    speeds = ods_sheets(SOURCE / 'spe0102.ods')
    national = {'reference_year': 2021, 'timing': indices,
                'free_flow_speeds': {'rows': exact_year_speed_rows(speeds),
                                    'published_notes': speeds['Notes'],
                                    'local_application': None}}
    linked = json.loads((LINKAGE / 'provisional_traffic_inputs.json').read_bytes())['associations']
    accepted = {row['count_point_id']: row['link_id'] for row in linked}
    for row in decisions:
        row['provisional_minor_link_id'] = accepted.get(row['count_point_id'])
    def has_complete_survey(row):
        if row is None:
            return False
        return any(day['complete_published_07_19_support'] and day['annual_input_identity_compatible']
                   for day in row['survey_days'])
    summary = {
        'audited_points': len(decisions), 'raw_hour_direction_records': len(payloads['raw-counts']),
        'points_with_raw_counts': sum(bool(row['raw_rows']) for row in decisions),
        'points_with_complete_identity_compatible_07_19_counts': sum(has_complete_survey(row) for row in decisions),
        'accepted_minor_points': len(accepted),
        'accepted_minor_points_with_complete_07_19_counts': sum(has_complete_survey(row) for row in decisions if row['count_point_id'] in accepted),
        'survey_days_per_observed_point': dict(Counter(len(row['survey_days']) for row in decisions if row['survey_days'])),
        'annual_directional_records': len(payloads['average-annual-daily-flow-by-direction']),
        'paired_annual_direction_points': sum(row['directional_aadf']['paired_directions'] for row in decisions),
        'combined_only_annual_direction_points': sum(row['directional_aadf']['combined_only'] for row in decisions),
        'incomplete_annual_direction_points': sum(row['directional_aadf']['incomplete_or_unqualified_direction_set'] for row in decisions),
        'directional_points_with_frozen_identity_mismatch': sum(not row['directional_aadf']['annual_input_identity_compatible'] for row in decisions),
        'directional_points_with_frozen_quality_flag_mismatch': sum(not row['directional_aadf']['annual_input_quality_flags_compatible'] for row in decisions),
        'points_with_directional_residual_above_one_vehicle_per_day': sum((row['directional_aadf']['max_absolute_residual'] or 0) > 1 for row in decisions),
        'national_2021_hour_weekday_indices': sum(len(data['rows']) * 7 for data in indices.values()),
        'national_2021_free_flow_road_types': len(national['free_flow_speeds']['rows']),
        'qualified_local_annual_day_evening_night_flows': 0, 'qualified_local_observed_speeds': 0,
    }
    by_point = {row['count_point_id']: row for row in decisions}
    point_for_link = {link: point for point, link in accepted.items()}
    with (AUDIT / 'road_link_evidence.csv').open(encoding='utf-8', newline='') as stream:
        baseline = list(csv.DictReader(stream))
    outside_selection = sorted({row['matched_dft_id'] for row in baseline
                                if row['traffic_assignment_source'] == 'direct'
                                and row['matched_dft_id'] not in by_point})
    summary['direct_matcher_donor_ids_outside_local_temporal_selection'] = outside_selection
    coverage = {}
    for area, length_field in (('core', 'core_length_m'), ('source_halo', 'halo_length_m')):
        road_length = sum(float(row[length_field]) for row in baseline)
        direct = sum(float(row[length_field]) for row in baseline if row['traffic_assignment_source'] == 'direct')
        raw_supported = sum(float(row[length_field]) for row in baseline if row['traffic_assignment_source'] == 'direct'
                            and has_complete_survey(by_point.get(row['matched_dft_id'])))
        provisional = sum(float(row[length_field]) for row in baseline if row['link_id'] in point_for_link
                          and has_complete_survey(by_point[point_for_link[row['link_id']]]))
        coverage[area] = {'total_road_length_m': road_length, 'existing_direct_length_m': direct,
                          'direct_length_with_temporal_donor_outside_selection_m': sum(float(row[length_field]) for row in baseline
                              if row['traffic_assignment_source'] == 'direct' and row['matched_dft_id'] not in by_point),
                          'direct_links_with_complete_count_day_support_m': raw_supported,
                          'provisional_minor_links_with_complete_count_day_support_m': provisional,
                          'count_day_supported_association_percent': 100 * (raw_supported + provisional) / road_length,
                          'interpretation': 'Survey-day evidence at attributed points; does not validate flows throughout links or annual noise periods.'}
    candidates = json.loads((DISCOVERY / 'local-site-candidates.json').read_bytes())
    year_quality = json.loads((DISCOVERY / 'core-quality-2021.json').read_bytes())
    webtris_rows = [row for name in ('core-year-first-records.json', 'site19675-2021-12-01-first-page.json')
                   for row in json.loads((DISCOVERY / name).read_bytes())['Rows']]
    report = {
        'schema_version': 1, 'status': 'temporal_input_qualification_no_noise_predictions',
        'reference_year': 2021, 'study': parent['study'], 'summary': summary, 'coverage': coverage,
        'source_id': identities[SOURCE.name]['source_id'],
        'parent_audit_id': identities[AUDIT.name]['audit_id'],
        'parent_linkage_id': identities[LINKAGE.name]['generation_id'],
        'runtime': {'python': sys.version, 'platform': platform.platform()},
        'discovery': {'webtris_metadata_entries_in_source_halo': len(candidates),
                      'webtris_metadata_entries_in_core': sum(row['in_core'] for row in candidates),
                      'core_2021_provider_aggregate_quality_field': year_quality['data_quality'],
                      'retrieved_paged_sample_rows': len(webtris_rows),
                      'sample_rows_with_populated_total_volume': sum(row['Total Volume'] != '' for row in webtris_rows),
                      'sample_rows_with_populated_average_speed': sum(row['Avg mph'] != '' for row in webtris_rows),
                      'interpretation': 'Bounded discovery only: entries are not independent sensors; pages are incomplete and blank fields are unavailable, never zero. No full-year local speed/flow input is qualified.'},
        'source_csv_sha256': parent['source_and_code_sha256'][source_csv],
        'input_inventory_sha256': {str(path.relative_to(ROOT)): file_hash(path / 'inventory.json')
                                  for path in INPUTS},
        'code_sha256': code_hashes, 'point_decisions': decisions,
        'licence': manifest['licence'],
        'interpretation': [
            'Raw records cover observed survey dates and published hour labels, not annual average hourly flow.',
            'The 07-19 count window is not automatically a named acoustic day period; period/time-zone definitions require qualification.',
            'National GB all-road weekday indices are retained unchanged. No normalization or transfer to local vehicle classes occurs.',
            'Cars/taxis, LCV and HGV timing groups do not supply separate motorcycle, bus/coach or HGV-axle temporal profiles.',
            'National free-flow speeds are sample statistics, not observed speeds or limits at these local roads.',
            'Exact-2021 historical tables are current published revisions; their cover/notes and checksums are retained.',
            'C annual direction means an unsplit combined record for this audit; unpaired directions and source-snapshot discrepancies remain explicit.',
            'No AADF minus survey-day total is interpreted as night flow; seasonal/date expansion prevents that subtraction.',
            'Local annual period flows and observed speeds stay null. No model, prediction or production UI change occurs.',
        ]}
    for name, digest in code_hashes.items():
        if file_hash(ROOT / name) != digest:
            raise ValueError('Audit code changed during run')
    for path in INPUTS:
        if verified_inventory(path) != identities[path.name]:
            raise ValueError('Input generation changed during run')
    if file_hash(ROOT / source_csv) != report['source_csv_sha256']:
        raise ValueError('Annual source changed during run')
    output.mkdir(parents=True)
    (output / 'report.json').write_bytes(json_bytes(report))
    (output / 'national_2021_assumption_candidates.json').write_bytes(json_bytes({
        **national, 'licence': manifest['licence'],
        'status': 'official_national_evidence_not_applied_to_local_roads'}))
    (output / 'local_survey_day_inputs.json').write_bytes(json_bytes({
        'reference_year': 2021, 'source_id': report['source_id'], 'licence': manifest['licence'],
        'points': decisions, 'status': 'survey_day_and_directional_evidence_not_annual_period_flows'}))
    files = {path.name: file_hash(path) for path in sorted(output.iterdir())}
    identity = 'road-temporal-audit-' + files['report.json'][:20]
    (output / 'inventory.json').write_bytes(json_bytes({'audit_id': identity, 'files': files}))
    print(json.dumps({'audit_id': identity, 'summary': summary, 'coverage': coverage, 'output': str(output)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/road_temporal_audit_v2')
    run(parser.parse_args().output)
