import csv
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.io import MemoryFile
from rasterio.transform import from_origin
from shapely.geometry import LineString

from quiet_uk.road_input_audit import (clipped_attribution, load_exact_traffic, point_rows_within,
                                     terrain_inventory, window_for_centres)


def traffic_file(tmp_path, rows):
    path = tmp_path / 'traffic.csv'
    columns = ['count_point_id', 'year', 'all_motor_vehicles', 'all_HGVs', 'easting', 'northing',
               'estimation_method', 'cars_and_taxis', 'two_wheeled_motor_vehicles']
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def traffic_row(identity='1', year=2021, **fields):
    return dict(count_point_id=identity, year=year, all_motor_vehicles=100, all_HGVs=5,
                easting=500000, northing=180000, estimation_method='Estimated',
                cars_and_taxis='', two_wheeled_motor_vehicles=0, **fields)


def test_exact_year_preserves_quality_and_missing_category_without_fallback(tmp_path):
    path = traffic_file(tmp_path, [traffic_row(year=2025), traffic_row()])
    rows, inventory = load_exact_traffic(path, 2021)
    assert inventory['exact_year_rows'] == inventory['usable_rows'] == 1
    assert rows[0]['year'] == 2021 and rows[0]['estimation_method'] == 'Estimated'
    assert rows[0]['vehicle_categories']['cars_and_taxis'] is None
    assert rows[0]['vehicle_categories']['two_wheeled_motor_vehicles'] == 0
    with pytest.raises(ValueError, match='refusing year fallback'):
        load_exact_traffic(path, 2020)


def test_duplicate_exact_year_identity_cannot_silently_overwrite_flow(tmp_path):
    path = traffic_file(tmp_path, [traffic_row(), traffic_row()])
    with pytest.raises(ValueError, match='duplicate'):
        load_exact_traffic(path, 2021)


@pytest.mark.parametrize('change', [{'all_motor_vehicles': ''}, {'all_motor_vehicles': 0},
                                   {'all_HGVs': 101}, {'all_HGVs': -1}, {'easting': 'nan'}])
def test_unusable_traffic_is_inventory_evidence_not_a_fabricated_flow(tmp_path, change):
    invalid = traffic_row('2') | change
    rows, inventory = load_exact_traffic(traffic_file(tmp_path, [traffic_row(), invalid]), 2021)
    assert len(rows) == 1 and rows[0]['count_point_id'] == '1'
    assert inventory['exact_year_rows'] == 2
    assert inventory['rejected_rows'] == {'unusable_flow_or_coordinate': 1}


def source(line, direct, quality='counted'):
    return {'geometry': LineString(line), 'road_class': 'a_road',
            'traffic_assignment_source': 'direct' if direct else 'imputed',
            'traffic_source': quality if direct else 'imputed',
            'imputation_method': 'none' if direct else 'global_median_all_dft'}


def test_clipped_length_coverage_survives_segment_splitting_and_separates_estimated_aadf():
    direct = source([(-100, 10), (1000, 10)], True, 'estimated')
    unknown = source([(0, 20), (1000, 20)], False)
    first = clipped_attribution([direct, unknown], (0, 0, 1000, 100))
    split = [source([(x, 10), (x + 100, 10)], True, 'estimated') for x in range(0, 1000, 100)]
    second = clipped_attribution(split + [unknown], (0, 0, 1000, 100))
    assert first['direct_length_percent'] == second['direct_length_percent'] == 50
    assert first['direct_link_percent'] == 50
    assert second['direct_link_percent'] == pytest.approx(1000 / 11)
    assert first['length_m'] == second['length_m'] == 2000
    assert any(row['quality_or_imputation'] == 'estimated' for row in first['groups'])


def test_half_cell_native_centres_follow_lookup_ownership_without_resampling():
    raster = SimpleNamespace(transform=from_origin(5, 45, 10, 10), width=4, height=4)
    window = window_for_centres(raster, (10, 20, 30, 40))
    assert (window.col_off, window.row_off, window.width, window.height) == (0, 0, 2, 2)
    points = [{'easting': x, 'northing': y} for y in (10, 20, 30, 40) for x in (10, 20, 30, 40)]
    assert {(p['easting'], p['northing']) for p in point_rows_within(points, (10, 20, 30, 40))} == {
        (10, 30), (20, 30), (10, 40), (20, 40)}


def test_terrain_keeps_zero_and_negative_heights_and_counts_uncovered_halo():
    with MemoryFile() as memory:
        with memory.open(driver='GTiff', width=2, height=2, count=1, dtype='float32',
                         crs='EPSG:27700', transform=from_origin(0, 20, 10, 10), nodata=-9999) as raster:
            raster.write(np.array([[0, -2], [15, -9999]], dtype='float32'), 1)
            result = terrain_inventory(raster, (0, 0, 40, 20))
    assert result['sampled_cells'] == 4 and result['valid_cells'] == 3
    assert result['valid_area_percent'] == 37.5
    assert result['elevation_min_m'] == -2 and result['elevation_max_m'] == 15
