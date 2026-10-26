"""Qualify temporal traffic support without manufacturing annual hourly flows."""
from collections import defaultdict
from datetime import date
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from quiet_uk.road_input_audit import VEHICLE_FIELDS

DAYS = ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday')
NS = {'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
      'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'}
API_FIELDS = {name: name.lower() for name in VEHICLE_FIELDS}


def number(value):
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
        return parsed if math.isfinite(parsed) and parsed >= 0 else None
    except (TypeError, ValueError):
        return None


def sum_preserving_missing(values):
    values = list(values)
    return sum(values) if values and all(value is not None for value in values) else None


def ods_sheets(path):
    """Read bounded source tables, refusing oversized XML or populated repeats.

    Trailing empty repeated cells/rows are omitted. Interior cell positions and
    published text, including missing/suppressed codes, are retained.
    """
    with zipfile.ZipFile(Path(path)) as archive:
        item = archive.getinfo('content.xml')
        if item.file_size > 20 * 1024 * 1024:
            raise ValueError('ODS XML budget exceeded')
        root = ET.fromstring(archive.read(item))
    sheets = {}
    for table in root.findall('.//table:table', NS):
        name = table.get('{' + NS['table'] + '}name')
        if not name or name in sheets:
            raise ValueError('Missing or duplicate sheet identity')
        rows = []
        for row in table.findall('table:table-row', NS):
            cells, pending_empty = [], 0
            for cell in row:
                if cell.tag not in ('{' + NS['table'] + '}table-cell',
                                    '{' + NS['table'] + '}covered-table-cell'):
                    continue
                text = ' '.join(''.join(p.itertext()) for p in cell.findall('.//text:p', NS))
                repeat = int(cell.get('{' + NS['table'] + '}number-columns-repeated', '1'))
                if repeat < 1 or (text and repeat > 100):
                    raise ValueError('Populated cell repeat exceeds table budget')
                if not text:
                    pending_empty += repeat
                    continue
                if len(cells) + pending_empty + repeat > 100:
                    raise ValueError('Interior columns exceed table budget')
                cells.extend([''] * pending_empty)
                cells.extend([text] * repeat)
                pending_empty = 0
            if not cells:
                continue
            repeat = int(row.get('{' + NS['table'] + '}number-rows-repeated', '1'))
            if repeat != 1 or len(rows) >= 10000:
                raise ValueError('Populated row repeat or table size is unqualified')
            rows.append(cells)
        sheets[name] = rows
    return sheets


def exact_year_indices(sheets, sheet_name, year=2021):
    rows = sheets[sheet_name]
    headers = [row for row in rows if row and row[0] == 'Year']
    if len(headers) != 1 or not set(DAYS).issubset(headers[0]):
        raise ValueError('Unexpected national profile schema')
    header = headers[0]
    selected = [dict(zip(header, row)) for row in rows if row and row[0] == str(year)]
    if not selected:
        raise ValueError('No exact-year national profile; refusing year fallback')
    grouped = defaultdict(set)
    result = []
    for row in selected:
        time = row['Time of Day']
        match = re.fullmatch(r'(\d{2}):00 to (\d{2}):00', time)
        if not match or int(match[2]) != (int(match[1]) + 1) % 24 or int(match[1]) > 23:
            raise ValueError('Unexpected hourly interval')
        hour = int(match[1])
        vehicle = row.get('Vehicle', 'All motor vehicles')
        if hour in grouped[vehicle]:
            raise ValueError('Duplicate national profile interval')
        grouped[vehicle].add(hour)
        indices = {day: number(row[day]) for day in DAYS}
        if any(value is None for value in indices.values()) or row['Units'] != 'Index number':
            raise ValueError('Unqualified index value or units')
        result.append({'year': year, 'hour_start': hour, 'published_interval': time,
                       'vehicle_group': vehicle, 'weekday_indices': indices,
                       'notes': row['Notes'], 'units': 'published index, not vehicles/hour',
                       'scope': 'Great Britain, all road classes combined',
                       'local_transfer_status': 'assumption_requires_evaluation'})
    if any(hours != set(range(24)) for hours in grouped.values()):
        raise ValueError('Incomplete 24-hour national profile')
    return sorted(result, key=lambda row: (row['vehicle_group'], row['hour_start']))


def exact_year_speed_rows(sheets, year=2021):
    name = f'SPE0102_({year})'
    if name not in sheets:
        raise ValueError('No exact-year speed sheet; refusing year fallback')
    rows = sheets[name]
    headers = [row for row in rows if row and row[0] == 'Road type']
    if len(headers) != 1:
        raise ValueError('Unexpected speed schema')
    header = headers[0]
    result = []
    for values in rows:
        row = dict(zip(header, values))
        if row.get('Units') != 'Average free flow speed (miles per hour)':
            continue
        result.append({'year': year, 'road_type': row['Road type'], 'notes': row['Notes'],
                       'units': 'mph', 'published_vehicle_values': {key: row[key] for key in header[3:]},
                       'scope': 'GB sample of sites selected for free-flow conditions',
                       'local_transfer_status': 'not_a_local_observed_speed'})
    if not result:
        raise ValueError('No exact-year average free-flow speeds')
    return result


def audit_temporal_records(points, raw_rows, directional_rows, year=2021):
    """Preserve survey-day/direction evidence, keeping annual periods unavailable."""
    by_point = {str(point['count_point_id']): point for point in points}
    if len(by_point) != len(points) or any(point.get('year') != year for point in points):
        raise ValueError('Duplicate point or wrong-year annual input')
    raw, directional = defaultdict(list), defaultdict(list)
    seen_raw, seen_directional = set(), set()
    for row in raw_rows:
        identity = str(row['count_point_id'])
        if identity not in by_point or row['year'] != year:
            raise ValueError('Raw point/year outside qualified selection')
        observed_date = date.fromisoformat(row['count_date'])
        hour = row['hour']
        if observed_date.year != year or isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour < 24:
            raise ValueError('Invalid count date or hour')
        direction = row['direction_of_travel']
        if direction not in ('N', 'S', 'E', 'W', 'C'):
            raise ValueError('Unqualified raw direction')
        key = (identity, row['count_date'], direction, hour)
        if key in seen_raw:
            raise ValueError('Duplicate raw count interval')
        seen_raw.add(key)
        flow, hgv = number(row.get('all_motor_vehicles')), number(row.get('all_hgvs'))
        if flow is None or hgv is None or not 0 <= hgv <= flow or flow != int(flow) or hgv != int(hgv):
            raise ValueError('Unqualified raw count total')
        categories = {name: number(row.get(api_name)) for name, api_name in API_FIELDS.items()}
        if all(v is not None for v in categories.values()) and sum(categories.values()) != flow:
            raise ValueError('Raw vehicle categories differ from total')
        if all(categories[name] is not None for name in VEHICLE_FIELDS[4:]) and sum(categories[name] for name in VEHICLE_FIELDS[4:]) != hgv:
            raise ValueError('Raw HGV categories differ from total')
        point = by_point[identity]
        coordinate_match = (number(row.get('easting')) == point['easting']
                            and number(row.get('northing')) == point['northing'])
        identity_match = (row.get('road_type') == point['road_type']
                          and row.get('road_name') == point['road_name'])
        raw[identity].append({'count_point_id': identity, 'count_date': row['count_date'],
                              'weekday': DAYS[observed_date.weekday()], 'hour_start': hour,
                              'direction': direction, 'flow': flow, 'hgv_flow': hgv,
                              'vehicle_categories': categories,
                              'coordinate_match_to_annual_input': coordinate_match,
                              'road_identity_match_to_annual_input': identity_match,
                              'units': 'vehicles counted in this published hour on this survey date'})
    for row in directional_rows:
        identity = str(row['count_point_id'])
        if identity not in by_point or row['year'] != year:
            raise ValueError('Directional point/year outside qualified selection')
        direction = row['direction_of_travel']
        if direction not in ('N', 'S', 'E', 'W', 'C') or (identity, direction) in seen_directional:
            raise ValueError('Unqualified or duplicate annual direction')
        seen_directional.add((identity, direction))
        directional[identity].append(row)
    result = []
    for identity, point in sorted(by_point.items()):
        records = sorted(raw[identity], key=lambda row: (row['count_date'], row['direction'], row['hour_start']))
        days = defaultdict(list)
        for row in records:
            days[row['count_date']].append(row)
        surveys = []
        for count_date, group in sorted(days.items()):
            labels = sorted({row['direction'] for row in group})
            paired = set(labels) in ({'N', 'S'}, {'E', 'W'})
            coverage = {label: sorted(row['hour_start'] for row in group if row['direction'] == label)
                        for label in labels}
            complete = (paired or labels == ['C']) and all(hours == list(range(7, 19)) for hours in coverage.values())
            compatible = all(row['coordinate_match_to_annual_input'] and row['road_identity_match_to_annual_input'] for row in group)
            surveys.append({'count_date': count_date, 'weekday': group[0]['weekday'],
                            'directions': labels, 'hours_by_direction': coverage,
                            'complete_published_07_19_support': complete,
                            'annual_input_identity_compatible': compatible,
                            'observed_total_07_19': sum(row['flow'] for row in group) if complete and compatible else None,
                            'observed_hgv_07_19': sum(row['hgv_flow'] for row in group) if complete and compatible else None,
                            'vehicle_totals_07_19': {name: sum_preserving_missing(row['vehicle_categories'][name] for row in group)
                                                     if complete and compatible else None for name in VEHICLE_FIELDS},
                            'observed_other_hours': sorted({row['hour_start'] for row in group} - set(range(7, 19))),
                            'interpretation': 'survey-day count only; no annual/seasonal/night expansion'})
        dirs = directional[identity]
        direction_identity_compatible = all(number(row.get('easting')) == point['easting']
            and number(row.get('northing')) == point['northing']
            and row.get('road_type') == point['road_type'] and row.get('road_name') == point['road_name']
            for row in dirs) if dirs else False
        direction_quality_compatible = all(row.get('estimation_method') == point['estimation_method']
            and row.get('estimation_method_detailed') == point['estimation_method_detailed']
            for row in dirs) if dirs else False
        labels = {row['direction_of_travel'] for row in dirs}
        paired = labels in ({'N', 'S'}, {'E', 'W'})
        combined = labels == {'C'}
        flow_sum = sum_preserving_missing(number(row.get('all_motor_vehicles')) for row in dirs)
        hgv_sum = sum_preserving_missing(number(row.get('all_hgvs')) for row in dirs)
        residuals = {'all_motor_vehicles': None if flow_sum is None else flow_sum - point['flow'],
                     'all_hgvs': None if hgv_sum is None else hgv_sum - point['hgv_flow'],
                     **{name: None if point['vehicle_categories'][name] is None else
                        (None if (total := sum_preserving_missing(number(row.get(api_name)) for row in dirs)) is None
                         else total - point['vehicle_categories'][name])
                        for name, api_name in API_FIELDS.items()}}
        result.append({'count_point_id': identity, 'aadf_quality': point['estimation_method'],
                       'aadf_method_detailed': point['estimation_method_detailed'],
                       'raw_rows': records, 'survey_days': surveys,
                       'directional_aadf': {'records': dirs, 'paired_directions': paired,
                                            'combined_only': combined,
                                            'incomplete_or_unqualified_direction_set': not (paired or combined),
                                            'annual_input_identity_compatible': direction_identity_compatible,
                                            'annual_input_quality_flags_compatible': direction_quality_compatible,
                                            'residuals_against_frozen_all_direction_aadf': residuals,
                                            'max_absolute_residual': max((abs(v) for v in residuals.values() if v is not None), default=None),
                                            'interpretation': 'annual directional estimate; no carriageway allocation or temporal flow implied'},
                       'annual_day_evening_night_flows': None, 'observed_speed_kmh': None,
                       'night_observation_status': 'not_established_by_published_07_19_survey_counts'})
    return result
