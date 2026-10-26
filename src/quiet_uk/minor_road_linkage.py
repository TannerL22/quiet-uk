"""Conservative point-to-link associations for historical minor-road AADF.

An accepted geometric association is provisional input evidence, not a certified
historic link flow or an acoustic observation. It never spreads counts to other
links, infers time-of-day flows, or modifies a noise grid.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math

from shapely.geometry import Point
from shapely.strtree import STRtree

POLICY = {
    'version': 2,
    'maximum_point_distance_m': 10.0,
    'minimum_competing_road_margin_m': 15.0,
    'minimum_endpoint_clearance_m': 20.0,
    'competition_search_radius_m': 25.0,
    'permitted_form_of_way': 'Single Carriageway',
    'permitted_road_class': 'minor',
    'flow_support': 'one associated OS link; constant flow along that link is a future modelling assumption',
    'validation': 'fixed engineering abstention rules, not calibrated match probabilities',
}


def _finite_number(value):
    if isinstance(value, bool):
        return False
    return isinstance(value, (int, float)) and math.isfinite(value)


def link_minor_counts(points, links, *, occupied_link_ids=(), reference_year=2021, network_bounds=None):
    """Return deterministic accepted/rejected decisions with retained raw evidence.

    Every nearby physical road participates in ambiguity checks, even when its
    class or carriageway form is not eligible for acceptance. Competing counts
    on one link reject all of those associations. Existing direct evidence is
    protected. Caller supplies projected EPSG:27700 metre coordinates and a
    complete nearby network. Cropped inputs must supply network_bounds so the
    whole ambiguity-search neighbourhood is required to lie inside the extract.
    """
    identities = [str(point['count_point_id']) for point in points]
    if len(identities) != len(set(identities)) or any(not identity for identity in identities):
        raise ValueError('Missing or duplicate count-point identity')
    link_ids = [str(link['link_id']) for link in links]
    if len(link_ids) != len(set(link_ids)) or any(not identity for identity in link_ids):
        raise ValueError('Missing or duplicate road-link identity')
    occupied = set(occupied_link_ids)
    if not occupied.issubset(set(link_ids)):
        raise ValueError('Occupied link identities must belong to the input network')
    if network_bounds is not None:
        if (len(network_bounds) != 4 or not all(_finite_number(v) for v in network_bounds)
                or not network_bounds[0] < network_bounds[2] or not network_bounds[1] < network_bounds[3]):
            raise ValueError('Invalid network bounds')
    physical = []
    for link in sorted(links, key=lambda item: str(item['link_id'])):
        geometry = link['geometry']
        if geometry.geom_type != 'LineString' or geometry.is_empty or not geometry.is_valid or geometry.length <= 0:
            raise ValueError('Expected valid physical line geometry')
        if not link.get('fictitious', False):
            physical.append(link)
    tree = STRtree([link['geometry'] for link in physical])
    radius = POLICY['competition_search_radius_m']
    decisions = []
    for point in sorted(points, key=lambda item: str(item['count_point_id'])):
        decision = {'count_point_id': str(point['count_point_id']), 'status': 'rejected',
                    'reason': None, 'accepted_link_id': None, 'proposed_link_id': None,
                    'distance_m': None, 'endpoint_clearance_m': None,
                    'runner_up_distance_m': None, 'competing_road_margin_m': None,
                    'network_margin_m': None,
                    'candidate_count': 0, 'candidates': [], 'traffic': deepcopy(point)}
        values = [point.get(name) for name in ('flow', 'hgv_flow', 'easting', 'northing')]
        if (point.get('year') != reference_year or not all(_finite_number(v) for v in values)
                or values[0] <= 0 or not 0 <= values[1] <= values[0]):
            decision['reason'] = 'invalid_or_wrong_year_traffic'
        elif point.get('road_type') != 'Minor' or (point.get('road_name') or '').strip().upper() not in ('C', 'U'):
            decision['reason'] = 'unsupported_minor_road_identity'
        elif point.get('estimation_method') not in ('Counted', 'Estimated'):
            decision['reason'] = 'unqualified_traffic_quality_flag'
        else:
            if network_bounds is not None:
                w, s, e, n = network_bounds
                margin = min(point['easting'] - w, e - point['easting'], point['northing'] - s, n - point['northing'])
                decision['network_margin_m'] = float(margin)
                if margin < radius:
                    decision['reason'] = 'insufficient_network_margin'
                    decisions.append(decision)
                    continue
            location = Point(point['easting'], point['northing'])
            candidates = []
            for index in tree.query(location, predicate='dwithin', distance=radius):
                link = physical[int(index)]
                distance = float(location.distance(link['geometry']))
                candidates.append((distance, str(link['link_id']), link))
            candidates.sort(key=lambda item: (item[0], item[1]))
            decision['candidate_count'] = len(candidates)
            decision['candidates'] = [{'link_id': identity, 'distance_m': distance,
                                       'road_class': link.get('road_class'),
                                       'form_of_way': link.get('form_of_way'),
                                       'road_structure': link.get('road_structure')}
                                      for distance, identity, link in candidates]
            if not candidates or candidates[0][0] > POLICY['maximum_point_distance_m']:
                decision['reason'] = 'no_road_within_distance_limit'
            else:
                distance, identity, link = candidates[0]
                along = float(link['geometry'].project(location))
                clearance = min(along, float(link['geometry'].length) - along)
                decision.update(proposed_link_id=identity, distance_m=distance,
                                endpoint_clearance_m=clearance)
                if len(candidates) > 1:
                    decision['runner_up_distance_m'] = candidates[1][0]
                    decision['competing_road_margin_m'] = candidates[1][0] - distance
                if link.get('road_class') != POLICY['permitted_road_class']:
                    decision['reason'] = 'nearest_road_class_incompatible'
                elif (link.get('fictitious') is not False
                      or link.get('form_of_way') != POLICY['permitted_form_of_way']
                      or link.get('road_structure') != '' or link['geometry'].is_ring
                      or not link['geometry'].is_simple):
                    decision['reason'] = 'carriageway_or_structure_unqualified'
                elif identity in occupied:
                    decision['reason'] = 'existing_direct_evidence_protected'
                elif clearance < POLICY['minimum_endpoint_clearance_m']:
                    decision['reason'] = 'near_link_endpoint_or_junction'
                elif (decision['competing_road_margin_m'] is not None
                      and decision['competing_road_margin_m'] < POLICY['minimum_competing_road_margin_m']):
                    decision['reason'] = 'competing_road_ambiguous'
                else:
                    decision.update(status='accepted_provisional', reason='unique_local_geometric_support',
                                    accepted_link_id=identity)
        decisions.append(decision)
    by_link = defaultdict(list)
    for decision in decisions:
        if decision['status'] == 'accepted_provisional':
            by_link[decision['accepted_link_id']].append(decision)
    for group in by_link.values():
        if len(group) > 1:
            for decision in group:
                decision.update(status='rejected', reason='multiple_count_points_on_one_link', accepted_link_id=None)
    return {'policy': deepcopy(POLICY), 'reference_year': reference_year,
            'summary': {'points': len(decisions),
                        'accepted_provisional': sum(d['status'] == 'accepted_provisional' for d in decisions),
                        'rejection_reasons': dict(sorted(Counter(d['reason'] for d in decisions
                                                                if d['status'] == 'rejected').items()))},
            'decisions': decisions}
