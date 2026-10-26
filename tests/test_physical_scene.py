from copy import deepcopy
from types import SimpleNamespace

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.transform import from_origin
from shapely.geometry import box, mapping

from quiet_uk.physical_scene import (
    AMBIGUOUS_SURVEY, assert_native_grid, building_surface_summary,
    paired_survey_mask, qualify_surveys, surface_difference, survey_grid,
)


def arrays():
    terrain = np.full((10, 10), -2., dtype='float32')
    surface = terrain + 8
    masks = np.full((10, 10), 255, dtype='uint8')
    codes = np.ones((10, 10), dtype='uint16')
    return terrain, surface, masks, masks.copy(), codes, codes.copy()


def summary(values=None, geometry=None, **kwargs):
    return building_surface_summary(box(0, 0, 20, 20) if geometry is None else geometry,
                                    *(arrays() if values is None else values),
                                    from_origin(0, 20, 2, 2), **kwargs)


def document(geometry=None, **properties):
    return {'crs': {'properties': {'name': 'EPSG:27700'}}, 'features': [
        {'geometry': mapping(box(0, 0, 20, 20) if geometry is None else geometry),
         'properties': {'polygon_id': 'P_1', 'sd_flown': 1607040000000,
                        'ed_flown': 1613865600000, 'resolution': 1,
                        'comp_year': '2022', 'lidar_rtn': 'Last Return', 'year': 2021,
                        **properties}}]}


def test_negative_terrain_and_zero_remain_observations():
    terrain = np.array([[-2., 0., np.nan]], dtype='float32')
    surface = np.array([[6., 0., 8.]], dtype='float32')
    difference, valid = surface_difference(terrain, surface, np.ones((1, 3)), np.ones((1, 3)))
    assert valid.tolist() == [[True, True, False]]
    assert difference[0, :2].tolist() == [8., 0.]
    assert np.isnan(difference[0, 2])


def test_missing_elevation_and_negative_difference_never_become_zero():
    difference, valid = surface_difference(np.array([[2., 10.]]), np.array([[1., 15.]]),
                                           np.ones((1, 2)), np.array([[1, 0]]))
    assert difference[0, 0] == -1
    assert np.isnan(difference[0, 1])
    assert valid.tolist() == [[True, False]]


def test_dimension_mismatch_is_rejected():
    with pytest.raises(ValueError, match='dimensions'):
        surface_difference(np.zeros((2, 2)), np.zeros((3, 3)), np.ones((2, 2)), np.ones((2, 2)))


@pytest.mark.parametrize('change', [dict(transform=from_origin(1, 20, 2, 2)),
                                  dict(crs=CRS.from_epsg(4326)), dict(shape=(9, 10)),
                                  dict(count=2), dict(bounds=(0, 0, 18, 20))])
def test_shifted_resampled_wrong_crs_or_wrong_shape_grids_fail(change):
    raster = SimpleNamespace(crs=CRS.from_epsg(27700), count=1, shape=(10, 10),
                             bounds=(0, 0, 20, 20), transform=from_origin(0, 20, 2, 2))
    assert_native_grid(raster, (0, 0, 20, 20))
    raster.__dict__.update(change)
    with pytest.raises(ValueError, match='grid mismatch'):
        assert_native_grid(raster, (0, 0, 20, 20))


def test_actual_survey_dates_are_retained_not_replaced_by_year():
    source = document()
    before = deepcopy(source)
    rows, _ = qualify_surveys(source, source)
    assert rows[0]['survey_start'] == '2020-12-04'
    assert rows[0]['survey_end'] == '2021-02-21'
    assert rows[0]['provider_year_field'] == 2021
    assert rows[0]['whole_index_geometry_equal']
    assert source == before


def test_different_survey_date_cannot_pair_even_with_same_id():
    rows, layers = qualify_surveys(document(), document(sd_flown=1607126400000))
    grids = [survey_grid(layer, (10, 10), from_origin(0, 20, 2, 2)) for layer in layers]
    assert len(rows) == 2
    assert not paired_survey_mask(*grids).any()


def test_geometry_difference_only_pairs_shared_unique_pixel_support():
    rows, layers = qualify_surveys(document(), document(box(0, 0, 10, 20)))
    grids = [survey_grid(layer, (10, 10), from_origin(0, 20, 2, 2)) for layer in layers]
    assert not rows[0]['whole_index_geometry_equal']
    assert paired_survey_mask(*grids).sum() == 50


def test_overlapping_index_polygons_are_ambiguous_in_either_order():
    layers = [(box(0, 0, 20, 20), 1), (box(0, 0, 10, 20), 2)]
    first = survey_grid(layers, (10, 10), from_origin(0, 20, 2, 2))
    reverse = survey_grid(layers[::-1], (10, 10), from_origin(0, 20, 2, 2))
    assert np.array_equal(first, reverse)
    assert np.count_nonzero(first == AMBIGUOUS_SURVEY) == 50
    assert paired_survey_mask(first, first).sum() == 50


def test_provider_rule_infers_newer_equal_resolution_survey_independent_of_order():
    source = document()
    source['features'] += document(sd_flown=1615420800000, ed_flown=1615420800000,
                                   polygon_id='P_2')['features']
    records, layers = qualify_surveys(source, source)
    grids = [survey_grid(layers[0], (10, 10), from_origin(0, 20, 2, 2), records),
             survey_grid(layers[0][::-1], (10, 10), from_origin(0, 20, 2, 2), records)]
    newer_code = next(row['code'] for row in records if row['polygon_id'] == 'P_2')
    assert np.array_equal(*grids)
    assert np.all(grids[0] == newer_code)


@pytest.mark.parametrize('change', [dict(resolution=2), dict(sd_flown=1610000000000),
                                  dict(sd_flown=1607040000000, ed_flown=1613865600000)])
def test_different_resolutions_overlapping_dates_or_ties_remain_unresolved(change):
    source = document()
    source['features'] += document(**{'sd_flown': 1615420800000, 'ed_flown': 1615420800000,
                                     'polygon_id': 'P_2', **change})['features']
    records, layers = qualify_surveys(source, source)
    grid = survey_grid(layers[0], (10, 10), from_origin(0, 20, 2, 2), records)
    assert np.all(grid == AMBIGUOUS_SURVEY)


def test_unique_later_survey_can_resolve_older_ambiguous_overlaps():
    source = document()
    source['features'] += document(polygon_id='P_2')['features']
    source['features'] += document(polygon_id='P_3', sd_flown=1615420800000,
                                   ed_flown=1615420800000)['features']
    records, layers = qualify_surveys(source, source)
    grid = survey_grid(layers[0], (10, 10), from_origin(0, 20, 2, 2), records)
    newest = next(row['code'] for row in records if row['polygon_id'] == 'P_3')
    assert np.all(grid == newest)


@pytest.mark.parametrize('mode', ['duplicate', 'missing_date', 'wrong_crs', 'truncated'])
def test_bad_survey_indices_fail(mode):
    source = document()
    if mode == 'duplicate':
        source['features'] *= 2
    elif mode == 'missing_date':
        del source['features'][0]['properties']['sd_flown']
    elif mode == 'wrong_crs':
        source['crs']['properties']['name'] = 'EPSG:4326'
    else:
        source['exceededTransferLimit'] = True
    with pytest.raises(ValueError):
        qualify_surveys(source, document())


def test_surface_height_is_provisional_and_has_no_invented_uncertainty():
    result = summary()
    assert result['provisional_height_agl_m'] == 8
    assert result['status'] == 'provisional_surface_height'
    assert result['acoustic_obstacle_height_m'] is None
    assert result['historical_footprint_compatibility'] == 'unestablished'
    assert result['interior_pixels'] == 64


def test_tall_surfaces_outside_footprint_do_not_affect_height():
    values = arrays()
    values[1][:, 5:] = 40
    result = summary(values, geometry=box(0, 0, 10, 20))
    assert result['provisional_height_agl_m'] == 8


def test_missing_cells_withhold_candidate_rather_than_fill():
    values = arrays()
    values[2][3:6, 3:6] = 0
    result = summary(values)
    assert result['withheld_reason'] == 'incomplete_elevation_support'
    assert result['provisional_height_agl_m'] is None
    assert result['sample_median_m'] == 8


@pytest.mark.parametrize('code', [0, AMBIGUOUS_SURVEY, 2])
def test_absent_overlapping_or_mismatched_survey_support_withholds(code):
    values = arrays()
    values[5][:] = code
    result = summary(values)
    assert result['withheld_reason'] == 'incomplete_paired_survey_support'
    assert result['provisional_height_agl_m'] is None


def test_multiple_dates_in_one_building_are_withheld():
    values = arrays()
    values[4][:, 5:] = 2
    values[5][:, 5:] = 2
    result = summary(values)
    assert result['withheld_reason'] == 'mixed_survey_dates'
    assert result['survey_codes'] == [1, 2]


def test_low_surface_might_be_missing_building_and_does_not_receive_typical_height():
    values = arrays()
    values[1][:] = values[0]
    result = summary(values)
    assert result['sample_median_m'] == 0
    assert result['withheld_reason'] == 'no_clear_elevated_surface'
    assert result['provisional_height_agl_m'] is None


def test_negative_differences_withhold_candidate_and_keep_diagnostics():
    values = arrays()
    values[1][3:6, 3:6] = values[0][3:6, 3:6] - 1
    result = summary(values)
    assert result['withheld_reason'] == 'negative_surface_differences'
    assert result['sample_min_m'] == -1
    assert result['provisional_height_agl_m'] is None


def test_small_and_extract_edge_footprints_are_withheld():
    assert summary(geometry=box(0, 0, 3, 3))['withheld_reason'] == 'no_buffered_interior'
    assert summary(geometry=box(0, 0, 5, 5))['withheld_reason'] == 'too_few_interior_pixels'
    result = summary(fully_inside=False)
    assert result['withheld_reason'] == 'footprint_crosses_extract_boundary'
    assert result['provisional_height_agl_m'] is None
