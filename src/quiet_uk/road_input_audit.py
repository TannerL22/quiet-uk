"""Input diagnostics for a bounded road model; never predict noise or fill gaps."""
from collections import Counter, defaultdict
import csv
import math
from pathlib import Path

import numpy as np
from rasterio.windows import Window
from shapely.geometry import box

VEHICLE_FIELDS = ('two_wheeled_motor_vehicles', 'cars_and_taxis', 'buses_and_coaches',
                  'LGVs', 'HGVs_2_rigid_axle', 'HGVs_3_rigid_axle',
                  'HGVs_4_or_more_rigid_axle', 'HGVs_3_or_4_articulated_axle',
                  'HGVs_5_articulated_axle', 'HGVs_6_articulated_axle')


def load_exact_traffic(path, year):
    """Retain exact-year official categories/quality, rejecting unusable flows.

    AADF is an annual daily average, not an hourly or night-time observation.
    Missing category values remain None. Duplicate count-point/year rows reject
    the input instead of being silently overwritten by the matcher.
    """
    rows, rejected, seen = [], Counter(), set()
    exact_rows = 0
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        columns = reader.fieldnames or []
        required = {'count_point_id', 'year', 'all_motor_vehicles', 'all_HGVs',
                    'easting', 'northing', 'estimation_method'}
        if not required.issubset(columns):
            raise ValueError('Missing required traffic columns')
        for raw in reader:
            if raw['year'].strip() != str(year):
                continue
            exact_rows += 1
            identity = raw['count_point_id'].strip()
            if not identity or identity in seen:
                raise ValueError('Missing or duplicate exact-year count-point identity')
            seen.add(identity)
            try:
                flow, hgv, x, y = (float(raw[name]) for name in
                                   ('all_motor_vehicles', 'all_HGVs', 'easting', 'northing'))
                if not all(math.isfinite(value) for value in (flow, hgv, x, y)):
                    raise ValueError
                if flow <= 0 or not 0 <= hgv <= flow:
                    raise ValueError
            except (ValueError, TypeError):
                rejected['unusable_flow_or_coordinate'] += 1
                continue
            categories = {}
            for name in VEHICLE_FIELDS:
                try:
                    value = float(raw.get(name, ''))
                    categories[name] = value if math.isfinite(value) and value >= 0 else None
                except (ValueError, TypeError):
                    categories[name] = None
            rows.append({'year': year, 'count_point_id': identity, 'flow': flow,
                         'hgv_flow': hgv, 'hgv_share': hgv / flow,
                         'easting': x, 'northing': y, 'vehicle_categories': categories,
                         **{name: raw.get(name, '') for name in
                            ('road_name', 'road_category', 'road_type', 'local_authority_name',
                             'estimation_method', 'estimation_method_detailed')}})
    if not exact_rows or not rows:
        raise ValueError(f'No usable exact-year traffic for {year}; refusing year fallback')
    return rows, {'year': year, 'exact_year_rows': exact_rows, 'usable_rows': len(rows),
                  'rejected_rows': dict(rejected), 'columns': columns}


def point_rows_within(rows, bounds):
    w, s, e, n = bounds
    return [row for row in rows if w <= row['easting'] < e and s < row['northing'] <= n]


def clipped_attribution(sources, bounds):
    """Count links and actual line length inside the requested box, by evidence.

    A direct match can use either Counted or Estimated official AADF. Neither
    means full-year continuous traffic measurement. Splitting a line changes the
    link count, but must not change the length-weighted evidence coverage.
    """
    area = box(*bounds)
    groups = defaultdict(lambda: {'links': 0, 'length_m': 0.0})
    total_length, total_links = 0.0, 0
    for source in sources:
        length = float(source['geometry'].intersection(area).length)
        if length <= 0:
            continue
        assignment = source['traffic_assignment_source']
        quality = source['traffic_source'] if assignment == 'direct' else source['imputation_method']
        key = (source['road_class'], assignment, quality)
        groups[key]['links'] += 1
        groups[key]['length_m'] += length
        total_links += 1
        total_length += length
    direct_length = sum(value['length_m'] for key, value in groups.items() if key[1] == 'direct')
    direct_links = sum(value['links'] for key, value in groups.items() if key[1] == 'direct')
    return {'links': total_links, 'length_m': total_length,
            'direct_links': direct_links, 'direct_link_percent': 100 * direct_links / total_links if total_links else None,
            'direct_length_m': direct_length,
            'direct_length_percent': 100 * direct_length / total_length if total_length else None,
            'groups': [{'road_class': key[0], 'assignment': key[1], 'quality_or_imputation': key[2], **value}
                       for key, value in sorted(groups.items())]}


def window_for_centres(raster, bounds):
    """Select native pixel centres with west <= x < east, south < y <= north.

    No resampling or rounding a half-cell offset into an invented aligned grid.
    """
    transform = raster.transform
    if transform.b or transform.d or transform.a <= 0 or transform.e >= 0:
        raise ValueError('Expected a north-up native raster')
    w, s, e, n = bounds
    if not np.isfinite(bounds).all() or not w < e or not s < n:
        raise ValueError('Invalid study bounds')
    left = math.ceil((w - transform.c) / transform.a - .5 - 1e-9)
    right = math.ceil((e - transform.c) / transform.a - .5 - 1e-9)
    top = math.ceil((transform.f - n) / -transform.e - .5 - 1e-9)
    bottom = math.ceil((transform.f - s) / -transform.e - .5 - 1e-9)
    left, right = max(0, left), min(raster.width, right)
    top, bottom = max(0, top), min(raster.height, bottom)
    return Window(left, top, max(0, right - left), max(0, bottom - top))


def terrain_inventory(raster, bounds):
    """Zero/negative ground elevation is valid; masks/nonfinite heights are not."""
    window = window_for_centres(raster, bounds)
    data = raster.read(1, window=window, masked=True)
    finite = ~np.ma.getmaskarray(data) & np.isfinite(data.data)
    valid = data.data[finite]
    expected_area = (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
    pixel_area = abs(raster.transform.a * raster.transform.e)
    return {'sampled_cells': int(data.size), 'valid_cells': int(finite.sum()),
            'masked_or_nonfinite_cells': int((~finite).sum()),
            'requested_area_m2': expected_area,
            'valid_area_percent': 100 * finite.sum() * pixel_area / expected_area,
            'elevation_min_m': float(valid.min()) if valid.size else None,
            'elevation_max_m': float(valid.max()) if valid.size else None}
