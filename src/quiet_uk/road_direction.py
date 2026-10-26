"""Source-conserving, provisional road carriageway and direction handling.

Published directional estimates are not observations of legal one-way operation.
Geometry vertex order is not traffic direction. No flow or missing direction is
invented; special roads stay withheld without their own flow/structure evidence.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import math
import re

from shapely.geometry import LineString, Point
from shapely.ops import unary_union
from shapely.strtree import STRtree

from .road_input_audit import VEHICLE_FIELDS
from .road_temporal import number, sum_preserving_missing

AXES = {'N': (0., 1.), 'S': (0., -1.), 'E': (1., 0.), 'W': (-1., 0.)}
POLICY = {'version': 1, 'annual_reconciliation_tolerance_vehicles_per_day': 1.,
          'osm_maximum_offset_m': 10., 'osm_minimum_line_coverage_fraction': .95,
          'osm_probe_count': 7, 'osm_maximum_heading_difference_degrees': 25.,
          'local_dual_count_distance_m': 75., 'local_dual_endpoint_clearance_m': 20.,
          'maximum_cardinal_heading_difference_degrees': 35.,
          'dual_minimum_separation_m': 3., 'dual_maximum_separation_m': 80.,
          'parallel_overlap_fraction': .5, 'parallel_neighbourhood_m': 60.,
          'validation': 'fixed engineering/refusal rules; not calibrated probabilities',
          'flow_transfer': 'pre-existing link association; constant flow along that link remains an assumption'}


def normalise_ref(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def partition_aadf(point, records, year=2021):
    """Retain original rounded streams and audit, never force them to the total."""
    records = deepcopy(records)
    identity = str(point['count_point_id'])
    labels = [row['direction_of_travel'] for row in records]
    if len(labels) != len(set(labels)) or any(label not in (*AXES, 'C') for label in labels):
        raise ValueError('Duplicate or unqualified published direction')
    if point['year'] != year or any(row['year'] != year or str(row['count_point_id']) != identity for row in records):
        raise ValueError('Directional point/year mismatch')
    streams = []
    for row in records:
        flow, hgv = number(row.get('all_motor_vehicles')), number(row.get('all_hgvs'))
        if flow is None or hgv is None or hgv > flow:
            raise ValueError('Invalid directional flow')
        categories = {name: number(row.get(name.lower())) for name in VEHICLE_FIELDS}
        streams.append({'direction': row['direction_of_travel'], 'flow': flow,
                        'hgv_flow': hgv, 'vehicle_categories': categories,
                        'estimation_method': row.get('estimation_method'),
                        'estimation_method_detailed': row.get('estimation_method_detailed'),
                        'source_record_id': row.get('id'), 'units': 'vehicles/day; annual daily estimate'})
    fields = {'flow': 'flow', 'hgv_flow': 'hgv_flow', **{name: name for name in VEHICLE_FIELDS}}
    residuals = {}
    for field in fields:
        values = [s[field] if field in ('flow', 'hgv_flow') else s['vehicle_categories'][field] for s in streams]
        total = sum_preserving_missing(values)
        original = point[field] if field in ('flow', 'hgv_flow') else point['vehicle_categories'][field]
        residuals[field] = total - original if total is not None and original is not None else None
    identity_ok = bool(records) and all(
        number(r.get('easting')) == point['easting'] and number(r.get('northing')) == point['northing']
        and r.get('road_name') == point['road_name'] and r.get('road_type') == point['road_type'] for r in records)
    quality_ok = bool(records) and all(
        r.get('estimation_method') == point['estimation_method']
        and r.get('estimation_method_detailed') == point['estimation_method_detailed'] for r in records)
    reconciles = bool(streams) and all(abs(v) <= POLICY['annual_reconciliation_tolerance_vehicles_per_day']
                                     for v in residuals.values() if v is not None)
    labels_set = set(labels)
    if not streams:
        status = 'missing_directional_source'
    elif not identity_ok or not quality_ok:
        status = 'source_identity_or_quality_mismatch'
    elif not reconciles:
        status = 'source_snapshot_totals_disagree'
    elif labels_set in ({'N', 'S'}, {'E', 'W'}):
        status = 'paired_published_directions'
    elif labels_set == {'C'}:
        status = 'combined_published_record'
    elif len(labels_set) == 1 and labels_set.issubset(AXES):
        status = 'single_published_direction_not_proof_of_oneway'
    else:
        status = 'unsupported_direction_set'
    return {'count_point_id': identity, 'reference_year': year, 'status': status,
            'streams': sorted(streams, key=lambda s: s['direction']), 'original_records': records,
            'reference_all_direction_aadf': deepcopy(point), 'residuals': residuals,
            'source_identity_compatible': identity_ok, 'source_quality_compatible': quality_ok,
            'no_residual_redistribution': True, 'opposite_direction_assumed_zero': False,
            'annual_day_evening_night_flows': None, 'observed_speed_kmh': None}


def tangent(geometry, position, half_window=10.):
    start, end = max(0., position-half_window), min(geometry.length, position+half_window)
    a, b = geometry.interpolate(start), geometry.interpolate(end)
    dx, dy = b.x-a.x, b.y-a.y
    norm = math.hypot(dx, dy)
    return (dx/norm, dy/norm) if norm else None


def relative_direction(geometry, location, label, *, require_local=True):
    """Encode a published cardinal label relative to digitisation at its point."""
    distance = geometry.distance(location)
    position = geometry.project(location)
    clearance = min(position, geometry.length-position)
    vector = tangent(geometry, position)
    result = {'forward_sign': None, 'point_distance_m': float(distance),
              'projected_position_m': float(position), 'endpoint_clearance_m': float(clearance),
              'cardinal_projection': None, 'reason': None}
    if label not in AXES:
        result['reason'] = 'no_cardinal_direction_in_combined_record'
    elif require_local and distance > POLICY['local_dual_count_distance_m']:
        result['reason'] = 'count_point_outside_local_direction_support'
    elif require_local and clearance < POLICY['local_dual_endpoint_clearance_m']:
        result['reason'] = 'count_projection_near_link_endpoint'
    elif vector is None:
        result['reason'] = 'degenerate_tangent'
    else:
        dot = sum(a*b for a,b in zip(vector, AXES[label]))
        result['cardinal_projection'] = dot
        if abs(dot) < math.cos(math.radians(POLICY['maximum_cardinal_heading_difference_degrees'])):
            result['reason'] = 'cardinal_axis_ambiguous'
        else:
            result['forward_sign'] = 1 if dot > 0 else -1
    return result


def osm_oneway_sign(tags):
    """Explicit vehicular oneway only; no defaults or conditional/class exceptions."""
    if any(k.startswith('oneway:') for k in tags) or tags.get('junction') == 'roundabout':
        return None
    value = str(tags.get('oneway', '')).strip().lower()
    return 1 if value in ('yes', 'true', '1') else -1 if value == '-1' else None


class OnewayMatcher:
    def __init__(self, ways):
        self.ways = sorted(ways, key=lambda w: w['way_id'])
        self.tree = STRtree([w['geometry'] for w in self.ways])

    def match(self, link):
        geometry, ref = link['geometry'], normalise_ref(link.get('road_number'))
        result = {'status': 'unavailable', 'forward_sign': None, 'way_ids': [],
                  'coverage_fraction': 0., 'reason': None, 'source': 'historical OSM explicit one-way tags'}
        if not self.ways or not ref:
            result['reason'] = 'missing_oneway_source_or_road_ref'
            return result
        candidates = []
        for idx in self.tree.query(geometry, predicate='dwithin', distance=POLICY['osm_maximum_offset_m']):
            way = self.ways[int(idx)]
            refs = {normalise_ref(r) for r in way['tags'].get('ref', '').split(';')}
            if ref in refs and way['tags'].get('highway') in ('motorway', 'trunk', 'primary', 'secondary', 'tertiary'):
                candidates.append(way)
        if not candidates:
            result['reason'] = 'no_same_ref_oneway_geometry'
            return result
        buffer = POLICY['osm_maximum_offset_m']
        covered = unary_union([w['geometry'].buffer(buffer) for w in candidates])
        result['coverage_fraction'] = geometry.intersection(covered).length / geometry.length
        if result['coverage_fraction'] < POLICY['osm_minimum_line_coverage_fraction']:
            result['reason'] = 'incomplete_oneway_geometry_support'
            return result
        signs, used = set(), set()
        for i in range(POLICY['osm_probe_count']):
            position = geometry.length * (.05 + .9*i/(POLICY['osm_probe_count']-1))
            location, os_vector = geometry.interpolate(position), tangent(geometry, position)
            local = [(location.distance(w['geometry']), w) for w in candidates
                     if location.distance(w['geometry']) <= buffer]
            if not local or os_vector is None:
                result['reason'] = 'oneway_probe_missing'
                return result
            best_distance = min(d for d,_ in local)
            probe_signs = set()
            for distance, way in local:
                if distance > best_distance + 2.:
                    continue
                sign = osm_oneway_sign(way['tags'])
                vector = tangent(way['geometry'], way['geometry'].project(location))
                if sign is None or vector is None:
                    result['reason'] = 'oneway_tag_unqualified'
                    return result
                dot = sum(a*b for a,b in zip(os_vector, vector))
                if abs(dot) < math.cos(math.radians(POLICY['osm_maximum_heading_difference_degrees'])):
                    result['reason'] = 'oneway_geometry_heading_disagrees'
                    return result
                probe_signs.add(sign * (1 if dot > 0 else -1)); used.add(way['way_id'])
            if len(probe_signs) != 1:
                result['reason'] = 'conflicting_nearby_oneway_evidence'
                return result
            signs.update(probe_signs)
        if len(signs) != 1:
            result['reason'] = 'oneway_changes_along_os_link'
            return result
        result.update(status='provisional_historical_oneway_match', forward_sign=signs.pop(),
                      way_ids=sorted(used))
        return result


def parallel_overlap(left, right):
    """Detect concurrent nearby representations, excluding ordinary continuations."""
    a, b = left['geometry'], right['geometry']
    if a.distance(b) > POLICY['parallel_neighbourhood_m']:
        return False
    va, vb = tangent(a, a.length/2), tangent(b, b.length/2)
    if va is None or vb is None or abs(sum(x*y for x,y in zip(va,vb))) < math.cos(math.radians(20)):
        return False
    radius = POLICY['parallel_neighbourhood_m']
    # A proximity buffer alone also overlaps short end-to-end continuations.
    # Require actual longitudinal overlap on the aligned local axis as well.
    orientation = 1 if sum(x*y for x,y in zip(va,vb)) >= 0 else -1
    axis = (va[0]+orientation*vb[0], va[1]+orientation*vb[1])
    ranges = [sorted(x*axis[0]+y*axis[1] for x,y in line.coords) for line in (a,b)]
    overlap = min(r[-1] for r in ranges) - max(r[0] for r in ranges)
    if overlap <= 0 or any(r[-1] == r[0] or overlap/(r[-1]-r[0]) < POLICY['parallel_overlap_fraction'] for r in ranges):
        return False
    # Requiring overlap in BOTH links prevents a tiny continuation/junction stub
    # from condemning an otherwise long physical link.
    return (a.intersection(b.buffer(radius)).length / a.length >= POLICY['parallel_overlap_fraction']
            and b.intersection(a.buffer(radius)).length / b.length >= POLICY['parallel_overlap_fraction'])


def allocate_carriageways(links, associations, partitions, ways=()):
    """One primary representation per accepted budget; full-network refusal ledger."""
    by_link = {str(link['link_id']): link for link in links}
    if len(by_link) != len(links):
        raise ValueError('Duplicate road identity')
    by_association = {str(a['link_id']): a for a in associations}
    if len(by_association) != len(associations) or not set(by_association).issubset(by_link):
        raise ValueError('Duplicate or nonnetwork association')
    matcher, decisions, groups, donor_links = OnewayMatcher(ways), {}, [], defaultdict(list)
    for identity, link in sorted(by_link.items()):
        geometry = link['geometry']
        if geometry.is_empty or not geometry.is_valid or geometry.geom_type != 'LineString' or geometry.length <= 0:
            raise ValueError('Invalid physical road line')
        association = by_association.get(identity)
        donor = str(association['count_point_id']) if association else None
        decision = {'link_id': identity, 'count_point_id': donor, 'status': 'withheld', 'reason': None,
                    'form_of_way': link.get('form_of_way'), 'road_structure': link.get('road_structure'),
                    'group_id': None, 'primary_flow': None, 'direction_components': [],
                    'oneway_evidence': None, 'association': deepcopy(association)}
        decisions[identity] = decision
        if donor is None:
            decision['reason'] = 'no_retained_count_association'
            continue
        if donor not in partitions:
            raise ValueError('Missing donor partition')
        donor_links[donor].append(identity)
        partition = partitions[donor]
        if link.get('fictitious') is not False or not geometry.is_simple or geometry.is_ring or link.get('loop'):
            decision['reason'] = 'fictitious_loop_or_invalid_alignment'
        elif link.get('road_structure'):
            decision['reason'] = 'structure_requires_separate_emission_handling'
        elif link.get('form_of_way') not in ('Single Carriageway', 'Collapsed Dual Carriageway', 'Dual Carriageway'):
            decision['reason'] = 'special_carriageway_requires_own_flow'
        elif partition['status'] not in ('paired_published_directions', 'combined_published_record',
                                          'single_published_direction_not_proof_of_oneway'):
            decision['reason'] = partition['status']
        elif association.get('kind') == 'baseline_direct' and (
                not normalise_ref(link.get('road_number'))
                or normalise_ref(link.get('road_number')) != normalise_ref(partition['reference_all_direction_aadf']['road_name'])):
            decision['reason'] = 'road_number_not_exactly_compatible'
        else:
            point = partition['reference_all_direction_aadf']
            location = Point(point['easting'], point['northing'])
            decision['direction_components'] = [{**deepcopy(s), 'geometry_alignment': relative_direction(geometry, location, s['direction'])}
                                                for s in partition['streams']]
            if link['form_of_way'] == 'Dual Carriageway':
                decision['oneway_evidence'] = matcher.match(link)
                decision['reason'] = 'dual_allocation_pending_group_check'
            elif partition['status'] == 'single_published_direction_not_proof_of_oneway':
                decision['reason'] = 'unpaired_direction_is_not_oneway_authority'
            else:
                if link['form_of_way'] == 'Single Carriageway':
                    evidence = matcher.match(link)
                    decision['oneway_evidence'] = evidence
                    if evidence['forward_sign'] is not None and partition['status'] == 'paired_published_directions':
                        decision['reason'] = 'paired_annual_counts_conflict_with_historical_oneway'
                        continue
                decision.update(status='centreline_candidate', reason=None)

    # Check the physical network too: an unassociated or differently associated
    # concurrent representation cannot disappear merely because it has no donor.
    ordered_links = sorted(by_link.values(),key=lambda l:l['link_id'])
    tree = STRtree([l['geometry'] for l in ordered_links])
    concurrent = defaultdict(set)
    centre_forms = ('Single Carriageway','Collapsed Dual Carriageway','Dual Carriageway')
    for identity, decision in decisions.items():
        if decision['status'] != 'centreline_candidate' and decision['reason'] != 'dual_allocation_pending_group_check':
            continue
        link = by_link[identity]
        ref = normalise_ref(link.get('road_number'))
        if not ref:
            continue
        for idx in tree.query(link['geometry'],predicate='dwithin',distance=POLICY['parallel_neighbourhood_m']):
            other = ordered_links[int(idx)]
            if (other['link_id'] != identity and other.get('form_of_way') in centre_forms
                    and normalise_ref(other.get('road_number')) == ref and parallel_overlap(link,other)):
                concurrent[identity].add(other['link_id'])
        if decision['status']=='centreline_candidate' and concurrent[identity]:
            decision.update(status='withheld',reason='concurrent_network_representation_ambiguous')

    # No all-direction count is assigned to two concurrent nearby centreline
    # representations, or to a centreline alongside separated carriageways.
    for donor, identities in donor_links.items():
        for i, left_id in enumerate(identities):
            for right_id in identities[i+1:]:
                left, right = by_link[left_id], by_link[right_id]
                if not parallel_overlap(left, right):
                    continue
                if left.get('form_of_way') == 'Dual Carriageway' and right.get('form_of_way') == 'Dual Carriageway':
                    continue
                for identity in (left_id, right_id):
                    if decisions[identity]['status'] == 'centreline_candidate':
                        decisions[identity].update(status='withheld', reason='concurrent_carriageway_representations_ambiguous')

    for identity, decision in sorted(decisions.items()):
        if decision['status'] != 'centreline_candidate':
            continue
        donor, partition = decision['count_point_id'], partitions[decision['count_point_id']]
        group_id = 'centreline:' + identity
        flow = sum_preserving_missing(s['flow'] for s in partition['streams'])
        representation = {'link_id': identity, 'flow': flow, 'components': deepcopy(decision['direction_components'])}
        groups.append({'group_id': group_id, 'count_point_id': donor, 'kind': 'one_unsplit_centreline',
                       'status': 'provisional', 'representations': [representation],
                       'reference_total_for_audit_only': partition['reference_all_direction_aadf']['flow'],
                       'published_stream_total': flow, 'rounding_residual': partition['residuals']['flow'],
                       'consume': 'components once; reference total is validation metadata, never an extra source'})
        decision.update(status='provisional_centreline_budget', group_id=group_id, primary_flow=flow)

    for donor, identities in sorted(donor_links.items()):
        duals = [i for i in identities if decisions[i]['reason'] == 'dual_allocation_pending_group_check']
        if not duals:
            continue
        partition = partitions[donor]
        selected = []
        for identity in duals:
            decision = decisions[identity]
            if partition['status'] != 'paired_published_directions':
                decision['reason'] = 'dual_requires_complete_published_direction_pair'
                continue
            evidence = decision['oneway_evidence']
            if evidence['forward_sign'] is None:
                decision['reason'] = evidence['reason']
                continue
            matching = [s for s in decision['direction_components']
                        if s['geometry_alignment']['forward_sign'] == evidence['forward_sign']]
            if len(matching) != 1:
                decision['reason'] = 'dual_count_cardinal_or_local_geometry_ambiguous'
                continue
            selected.append((identity, matching[0]))
        if len(selected) != 2 or len({s['direction'] for _,s in selected}) != 2:
            for identity,_ in selected:
                decisions[identity]['reason'] = 'dual_pair_incomplete_or_multiple_local_members'
            continue
        (left_id, left_stream), (right_id, right_stream) = selected
        left, right = by_link[left_id], by_link[right_id]
        point = partition['reference_all_direction_aadf']
        location = Point(point['easting'], point['northing'])
        positions = [l['geometry'].interpolate(l['geometry'].project(location)) for l in (left,right)]
        separation = positions[0].distance(positions[1])
        mixed = any(parallel_overlap(by_link[i], member) for i in identities
                    if by_link[i].get('form_of_way') != 'Dual Carriageway'
                    for member in (left,right))
        mixed = mixed or bool((concurrent[left_id] | concurrent[right_id]) - {left_id,right_id})
        if (not POLICY['dual_minimum_separation_m'] <= separation <= POLICY['dual_maximum_separation_m']
                or not parallel_overlap(left, right) or mixed):
            for identity,_ in selected:
                decisions[identity]['reason'] = 'dual_membership_or_mixed_representation_ambiguous'
            continue
        group_id = 'dual:' + donor + ':' + ':'.join(sorted((left_id,right_id)))
        representations = [{'link_id': i, 'flow': s['flow'], 'components': [deepcopy(s)]} for i,s in selected]
        total = sum(r['flow'] for r in representations)
        if total != sum(s['flow'] for s in partition['streams']):
            raise ValueError('A dual budget lost or duplicated a published stream')
        groups.append({'group_id': group_id, 'count_point_id': donor, 'kind': 'historical_oneway_supported_dual_pair',
                       'status': 'provisional', 'representations': representations,
                       'reference_total_for_audit_only': point['flow'], 'published_stream_total': total,
                       'rounding_residual': partition['residuals']['flow'],
                       'local_separation_m': separation, 'consume': 'one published stream per physical carriageway; no extra whole-road source'})
        for representation in representations:
            decisions[representation['link_id']].update(status='provisional_dual_direction_allocation', reason=None,
                                                       group_id=group_id, primary_flow=representation['flow'])
    return {'policy': deepcopy(POLICY), 'groups': sorted(groups,key=lambda g:g['group_id']),
            'decisions': sorted(decisions.values(),key=lambda d:d['link_id']),
            'status_counts': dict(Counter(d['status'] for d in decisions.values())),
            'withheld_reasons': dict(Counter(d['reason'] for d in decisions.values() if d['status']=='withheld'))}
