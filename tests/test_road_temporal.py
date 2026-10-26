from copy import deepcopy
import zipfile

import pytest

from quiet_uk.road_input_audit import VEHICLE_FIELDS
from quiet_uk.road_temporal import (
    DAYS, audit_temporal_records, exact_year_indices, exact_year_speed_rows,
    number, ods_sheets, sum_preserving_missing,
)


def annual():
    return {'count_point_id': 'p1', 'year': 2021, 'flow': 1000., 'hgv_flow': 50.,
            'easting': 506371., 'northing': 178304., 'road_type': 'Minor', 'road_name': 'C',
            'estimation_method': 'Counted', 'estimation_method_detailed': 'Manual count',
            'vehicle_categories': {name: (900. if name == 'cars_and_taxis' else
                                          50. if name in ('LGVs', 'HGVs_2_rigid_axle') else 0.)
                                   for name in VEHICLE_FIELDS}}


def raw(hour=7, direction='N', **changes):
    return {'id': hour, 'count_point_id': 'p1', 'year': 2021, 'count_date': '2021-09-14',
            'hour': hour, 'direction_of_travel': direction, 'all_motor_vehicles': 100,
            'all_hgvs': 5, 'easting': 506371, 'northing': 178304,
            'road_type': 'Minor', 'road_name': 'C',
            **{name.lower(): (90 if name == 'cars_and_taxis' else
                             5 if name in ('LGVs', 'HGVs_2_rigid_axle') else 0)
               for name in VEHICLE_FIELDS}, **changes}


def direction(label='N', **changes):
    p = annual()
    return {'count_point_id': 'p1', 'year': 2021, 'direction_of_travel': label,
            'all_motor_vehicles': 500, 'all_hgvs': 25,
            'easting': p['easting'], 'northing': p['northing'],
            'road_name': p['road_name'], 'road_type': p['road_type'],
            'estimation_method': p['estimation_method'],
            'estimation_method_detailed': p['estimation_method_detailed'],
            **{name.lower(): value / 2 for name, value in p['vehicle_categories'].items()},
            **changes}


def survey():
    return [raw(hour, direction) for hour in range(7, 19) for direction in ('N', 'S')]


def audit(rows=None, directions=None):
    return audit_temporal_records([annual()], survey() if rows is None else rows,
                                  [direction(), direction('S')] if directions is None else directions)[0]


def test_survey_day_remains_observed_support_and_never_becomes_annual_night_flow():
    rows = survey()
    before = deepcopy(rows)
    result = audit(rows)
    assert result['survey_days'][0]['observed_total_07_19'] == 2400
    assert result['survey_days'][0]['weekday'] == 'Tuesday'
    assert result['annual_day_evening_night_flows'] is None
    assert result['observed_speed_kmh'] is None
    assert result['directional_aadf']['max_absolute_residual'] == 0
    assert result['directional_aadf']['annual_input_quality_flags_compatible']
    assert rows == before


def test_missing_category_does_not_become_zero_and_zero_stays_valid():
    rows = survey()
    rows[0]['lgvs'] = None
    result = audit(rows)
    assert result['raw_rows'][0]['vehicle_categories']['LGVs'] is None
    assert result['survey_days'][0]['vehicle_totals_07_19']['LGVs'] is None
    assert result['survey_days'][0]['vehicle_totals_07_19']['two_wheeled_motor_vehicles'] == 0
    assert result['survey_days'][0]['observed_total_07_19'] == 2400


def test_missing_hour_and_unpaired_raw_direction_abstain_without_zero_filling():
    result = audit(survey()[1:])
    assert result['survey_days'][0]['observed_total_07_19'] is None
    assert not result['survey_days'][0]['complete_published_07_19_support']
    assert len(result['raw_rows']) == 23
    assert audit([raw(hour) for hour in range(7, 19)])['survey_days'][0]['observed_total_07_19'] is None


def test_no_count_day_remains_unavailable_even_with_annual_directional_values():
    result = audit([])
    assert result['survey_days'] == [] and result['raw_rows'] == []
    assert result['annual_day_evening_night_flows'] is None


def test_duplicate_interval_and_directional_row_reject_instead_of_double_counting():
    with pytest.raises(ValueError, match='Duplicate raw'):
        audit(survey() + [raw()])
    with pytest.raises(ValueError, match='duplicate annual'):
        audit(directions=[direction(), direction()])


@pytest.mark.parametrize('change', [{'year': 2020}, {'count_point_id': 'other'},
                                   {'count_date': '2020-09-14'}, {'hour': True},
                                   {'all_motor_vehicles': -1}, {'all_hgvs': 101},
                                   {'all_motor_vehicles': 100.5},
                                   {'cars_and_taxis': 91}])
def test_unqualified_raw_records_cannot_gain_temporal_support(change):
    with pytest.raises(ValueError):
        audit([raw(**change)])


def test_coordinate_or_road_identity_mismatch_withholds_support_but_preserves_records():
    result = audit([raw(hour, d, easting=506372) for hour in range(7, 19) for d in ('N', 'S')])
    assert result['survey_days'][0]['observed_total_07_19'] is None
    assert not result['survey_days'][0]['annual_input_identity_compatible']
    assert len(result['raw_rows']) == 24


def test_directional_rounding_residuals_and_quality_changes_stay_explicit():
    result = audit(directions=[direction(all_motor_vehicles=501), direction('S', estimation_method='Estimated')])
    assert result['directional_aadf']['residuals_against_frozen_all_direction_aadf']['all_motor_vehicles'] == 1
    assert not result['directional_aadf']['annual_input_quality_flags_compatible']
    assert result['annual_day_evening_night_flows'] is None


def test_combined_and_single_direction_are_distinct_and_never_split_equally():
    combined = audit(directions=[direction('C', all_motor_vehicles=1000)])
    single = audit(directions=[direction('W')])
    assert combined['directional_aadf']['combined_only']
    assert single['directional_aadf']['incomplete_or_unqualified_direction_set']
    assert len(single['directional_aadf']['records']) == 1


@pytest.mark.parametrize('change', [{'year': 2020}, {'count_point_id': 'other'}])
def test_directional_year_or_point_fallback_is_refused(change):
    with pytest.raises(ValueError):
        audit(directions=[direction(**change)])


def profile(year=2021):
    header = ['Year', 'Time of Day', 'Notes', 'Units', *DAYS]
    return {'TRA0307': [header, *[[str(year), f'{hour:02d}:00 to {(hour+1)%24:02d}:00',
                                 '[note 3]', 'Index number', *['100'] * 7] for hour in range(24)]]}


def test_indices_are_preserved_as_indices_without_local_hourly_flow_assignment():
    result = exact_year_indices(profile(), 'TRA0307')
    assert len(result) == 24 and result[-1]['hour_start'] == 23
    assert result[0]['weekday_indices']['Monday'] == 100
    assert result[0]['units'] == 'published index, not vehicles/hour'


def test_missing_year_hour_or_duplicate_hour_refuses_national_profile():
    with pytest.raises(ValueError, match='exact-year'):
        exact_year_indices(profile(2025), 'TRA0307')
    incomplete = profile()
    incomplete['TRA0307'].pop()
    with pytest.raises(ValueError, match='Incomplete'):
        exact_year_indices(incomplete, 'TRA0307')
    duplicate = profile()
    duplicate['TRA0307'].append(duplicate['TRA0307'][-1])
    with pytest.raises(ValueError, match='Duplicate'):
        exact_year_indices(duplicate, 'TRA0307')


def test_speed_sheet_is_exact_year_and_preserves_unavailable_codes():
    sheets = {'SPE0102_(2021)': [['Road type', 'Notes', 'Units', 'Cars', 'Buses'],
                               ['Motorways', '', 'Average free flow speed (miles per hour)', '69', '[x]']]}
    row = exact_year_speed_rows(sheets)[0]
    assert row['published_vehicle_values']['Buses'] == '[x]'
    assert row['local_transfer_status'] == 'not_a_local_observed_speed'
    with pytest.raises(ValueError, match='exact-year'):
        exact_year_speed_rows({'SPE0102_(2025)': sheets['SPE0102_(2021)']})


def write_ods(path, row):
    xml = ('<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
           'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
           'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0">'
           '<table:table table:name="data"><table:table-row>' + row +
           '</table:table-row></table:table></office:document-content>')
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr('content.xml', xml)


def test_ods_empty_repeats_keep_interior_positions_and_skip_large_trailing_blank_area(tmp_path):
    path = tmp_path / 'source.ods'
    write_ods(path, '<table:table-cell><text:p>A</text:p></table:table-cell>'
              '<table:table-cell table:number-columns-repeated="2"/>'
              '<table:table-cell><text:p>0</text:p></table:table-cell>'
              '<table:table-cell table:number-columns-repeated="1048576"/>')
    assert ods_sheets(path)['data'] == [['A', '', '', '0']]


def test_ods_oversized_interior_columns_cannot_shift_schema_silently(tmp_path):
    path = tmp_path / 'source.ods'
    write_ods(path, '<table:table-cell table:number-columns-repeated="101"/>'
              '<table:table-cell><text:p>A</text:p></table:table-cell>')
    with pytest.raises(ValueError, match='Interior columns'):
        ods_sheets(path)


def test_missing_and_nonfinite_values_are_not_zero_measurements():
    assert number('0') == 0
    for value in ('', '[x]', '[low]', None, True, float('nan'), -1):
        assert number(value) is None
    assert sum_preserving_missing([0, 2]) == 2
    assert sum_preserving_missing([0, None]) is None

