from copy import deepcopy
import random

import pytest
from shapely.geometry import LineString

from quiet_uk.minor_road_linkage import link_minor_counts


def road(identity='one', coordinates=((-100, 0), (100, 0)), **changes):
    return {'link_id': identity, 'geometry': LineString(coordinates), 'road_class': 'minor',
            'form_of_way': 'Single Carriageway', 'road_structure': '', 'fictitious': False, **changes}


def point(identity='p1', x=0., y=2., **changes):
    return {'count_point_id': identity, 'year': 2021, 'road_type': 'Minor', 'road_name': 'U',
            'flow': 1000., 'hgv_flow': 50., 'easting': x, 'northing': y,
            'estimation_method': 'Counted', 'estimation_method_detailed': 'Manual count',
            'vehicle_categories': {'cars_and_taxis': 900., 'LGVs': None, 'two_wheeled_motor_vehicles': 0.},
            **changes}


def decision(points, roads, **kwargs):
    return link_minor_counts(points, roads, **kwargs)['decisions'][0]


def test_unique_association_retains_quality_categories_and_does_not_mutate_inputs():
    counts = [point(estimation_method='Estimated', estimation_method_detailed='Prior year estimate')]
    before = deepcopy(counts)
    result = decision(counts, [road()])
    assert result['status'] == 'accepted_provisional' and result['accepted_link_id'] == 'one'
    assert result['distance_m'] == 2 and result['endpoint_clearance_m'] == 100
    assert result['traffic'] == before[0]
    assert result['traffic']['vehicle_categories']['LGVs'] is None
    assert result['traffic']['vehicle_categories']['two_wheeled_motor_vehicles'] == 0
    result['traffic']['vehicle_categories']['cars_and_taxis'] = 123
    assert counts == before


def test_incompatible_competing_road_still_rejects_ambiguity():
    roads = [road(), road('major', ((-100, 8), (100, 8)), road_class='a_road')]
    result = decision([point()], roads)
    assert result['reason'] == 'competing_road_ambiguous'
    assert result['competing_road_margin_m'] == 4
    assert result['accepted_link_id'] is None


def test_nearer_major_road_is_not_skipped_to_force_a_minor_match():
    roads = [road(), road('major', ((-100, 2), (100, 2)), road_class='a_road')]
    result = decision([point()], roads)
    assert result['reason'] == 'nearest_road_class_incompatible'


def test_endpoint_and_continuation_links_do_not_spread_a_count():
    roads = [road('one', ((-100, 0), (0, 0))), road('two', ((0, 0), (100, 0)))]
    result = decision([point(x=-5)], roads)
    assert result['reason'] == 'near_link_endpoint_or_junction'
    assert result['accepted_link_id'] is None


@pytest.mark.parametrize('form', ['Dual Carriageway', 'Collapsed Dual Carriageway', 'Roundabout', 'Slip Road', None])
def test_unqualified_carriageway_forms_do_not_receive_all_direction_flow(form):
    result = decision([point()], [road(form_of_way=form)])
    assert result['reason'] == 'carriageway_or_structure_unqualified'


@pytest.mark.parametrize('structure', ['Road In Tunnel', 'Bridge', None])
def test_unknown_and_special_road_structures_reject_association(structure):
    result = decision([point()], [road(road_structure=structure)])
    assert result['reason'] == 'carriageway_or_structure_unqualified'


def test_fictitious_network_geometry_is_never_associated():
    result = decision([point()], [road(fictitious=True)])
    assert result['reason'] == 'no_road_within_distance_limit'


def test_existing_direct_flow_is_protected_and_unknown_occupied_id_rejects():
    result = decision([point()], [road()], occupied_link_ids={'one'})
    assert result['reason'] == 'existing_direct_evidence_protected'
    with pytest.raises(ValueError, match='Occupied'):
        link_minor_counts([point()], [road()], occupied_link_ids={'missing'})


def test_two_counts_on_one_link_are_both_withheld_instead_of_averaged():
    result = link_minor_counts([point('p1', x=-40), point('p2', x=40, flow=2000.)], [road()])
    assert result['summary']['accepted_provisional'] == 0
    assert {d['reason'] for d in result['decisions']} == {'multiple_count_points_on_one_link'}
    assert all(d['accepted_link_id'] is None for d in result['decisions'])


def test_exact_policy_boundaries_are_inclusive_and_outside_distance_is_withheld():
    roads = [road('one', ((0, 0), (100, 0))), road('other', ((0, 35), (100, 35)))]
    result = decision([point(x=20, y=10)], roads)
    assert result['status'] == 'accepted_provisional'
    assert result['endpoint_clearance_m'] == 20 and result['competing_road_margin_m'] == 15
    assert decision([point(y=10.01)], [road()])['reason'] == 'no_road_within_distance_limit'


@pytest.mark.parametrize('change', [{'year': 2025}, {'flow': float('nan')}, {'hgv_flow': 1001.},
                                   {'easting': None}, {'flow': True}])
def test_wrong_year_or_unusable_traffic_cannot_be_associated(change):
    assert decision([point(**change)], [road()])['reason'] == 'invalid_or_wrong_year_traffic'


def test_unqualified_quality_flag_and_unsupported_identifier_are_withheld():
    assert decision([point(estimation_method='Unknown')], [road()])['reason'] == 'unqualified_traffic_quality_flag'
    assert decision([point(road_name='A4')], [road()])['reason'] == 'unsupported_minor_road_identity'


def test_input_order_and_tied_geometry_do_not_change_decisions():
    roads = [road('two', ((-100, 4), (100, 4))), road(),
             road('far', ((-100, 100), (100, 100)))]
    points = [point('p2', x=40), point('p1', x=-40)]
    expected = link_minor_counts(points, roads)
    for seed in range(5):
        random.Random(seed).shuffle(roads)
        random.Random(seed + 10).shuffle(points)
        assert link_minor_counts(points, roads) == expected


def test_duplicate_identities_cannot_silently_overwrite_evidence():
    with pytest.raises(ValueError, match='count-point'):
        link_minor_counts([point(), point()], [road()])
    with pytest.raises(ValueError, match='road-link'):
        link_minor_counts([point()], [road(), road()])


def test_extract_boundary_cannot_manufacture_an_unambiguous_association():
    result = decision([point()], [road()], network_bounds=(-20., -100., 100., 100.))
    assert result['reason'] == 'insufficient_network_margin'
    assert result['network_margin_m'] == 20 and result['accepted_link_id'] is None
    edge = decision([point()], [road()], network_bounds=(-25., -100., 100., 100.))
    assert edge['status'] == 'accepted_provisional' and edge['network_margin_m'] == 25


@pytest.mark.parametrize('bounds', [(5, 0, 0, 5), (0, 5, 5, 0), (0, 0, float('inf'), 5)])
def test_bad_network_bounds_reject_the_input(bounds):
    with pytest.raises(ValueError, match='network bounds'):
        link_minor_counts([point()], [road()], network_bounds=bounds)
