from copy import deepcopy

import pytest
from shapely.geometry import LineString, Point

from quiet_uk.road_direction import (allocate_carriageways, osm_oneway_sign,
    OnewayMatcher, parallel_overlap, partition_aadf, relative_direction)
from quiet_uk.road_input_audit import VEHICLE_FIELDS


def source(labels=('N', 'S'), flows=(60, 40), *, total=None):
    point = dict(count_point_id='1', year=2021, easting=0., northing=100.,
                 road_name='A1', road_type='Major', flow=sum(flows) if total is None else total,
                 hgv_flow=10., vehicle_categories={f: 2. for f in VEHICLE_FIELDS},
                 estimation_method='Counted', estimation_method_detailed='Manual count')
    rows = [dict(id=i, count_point_id=1, year=2021, direction_of_travel=label,
                 easting=0, northing=100, road_name='A1', road_type='Major',
                 all_motor_vehicles=flow, all_hgvs=10./len(labels),
                 estimation_method='Counted', estimation_method_detailed='Manual count',
                 **{f.lower(): 2./len(labels) for f in VEHICLE_FIELDS})
            for i, (label, flow) in enumerate(zip(labels, flows))]
    return point, rows


def road(identity='a', x=0, *, form='Single Carriageway', y0=0, y1=200, reverse=False, **kwargs):
    coords = [(x, y0), (x, y1)]
    return dict(link_id=identity, geometry=LineString(coords[::-1] if reverse else coords),
                road_number='A1', form_of_way=form, fictitious=False, loop=False,
                road_structure='', **kwargs)


def way(identity, x, sign='yes', *, y0=0, y1=200, reverse=False, tags=None):
    coords = [(x, y0), (x, y1)]
    return dict(way_id=identity, geometry=LineString(coords[::-1] if reverse else coords),
                tags={'ref': 'A1', 'highway': 'primary', 'oneway': sign, **(tags or {})})


def allocate(links, ways=(), *, labels=('N', 'S'), flows=(60, 40)):
    point, rows = source(labels, flows)
    return allocate_carriageways(links, [dict(link_id=l['link_id'], count_point_id='1', kind='baseline_direct')
                                       for l in links], {'1': partition_aadf(point, rows)}, ways)


@pytest.mark.parametrize('labels,flows,status', [
    (('N','S'),(60,40),'paired_published_directions'),
    (('E','W'),(60,40),'paired_published_directions'),
    (('C',),(100,),'combined_published_record'),
    (('W',),(100,),'single_published_direction_not_proof_of_oneway'),
    (('N','E'),(60,40),'unsupported_direction_set'),
    ((),(),'missing_directional_source')])
def test_published_direction_states(labels, flows, status):
    point, rows = source(labels, flows)
    original = deepcopy(rows)
    result = partition_aadf(point, rows)
    assert result['status'] == status
    assert rows == original == result['original_records']
    assert result['opposite_direction_assumed_zero'] is False
    assert result['annual_day_evening_night_flows'] is None


def test_rounding_missing_and_zero_are_preserved_without_redistribution():
    point, rows = source(flows=(0,99), total=100)
    rows[0]['all_hgvs'], rows[1]['all_hgvs'] = 0, 10
    for f in VEHICLE_FIELDS:
        rows[0][f.lower()], rows[1][f.lower()] = 0, 2
    rows[0]['cars_and_taxis'] = None
    result = partition_aadf(point, rows)
    assert result['status'] == 'paired_published_directions'
    assert result['residuals']['flow'] == -1
    assert result['residuals']['cars_and_taxis'] is None
    assert [s['flow'] for s in result['streams']] == [0,99]
    assert result['streams'][0]['vehicle_categories']['cars_and_taxis'] is None
    point['flow'] = 103
    assert partition_aadf(point, rows)['status'] == 'source_snapshot_totals_disagree'


@pytest.mark.parametrize('field,value', [('easting',1),('road_name','A2'),
    ('road_type','Minor'),('estimation_method','Estimated'),('estimation_method_detailed','Elsewhere')])
def test_source_identity_and_quality_must_agree(field, value):
    point, rows = source()
    rows[0][field] = value
    assert partition_aadf(point, rows)['status'] == 'source_identity_or_quality_mismatch'


@pytest.mark.parametrize('change', ['duplicate','wrong_year','wrong_id','negative','hgv_exceeds'])
def test_malformed_source_fails(change):
    point, rows = source()
    if change == 'duplicate': rows[1]['direction_of_travel'] = 'N'
    elif change == 'wrong_year': rows[0]['year'] = 2020
    elif change == 'wrong_id': rows[0]['count_point_id'] = 2
    elif change == 'negative': rows[0]['all_motor_vehicles'] = -1
    else: rows[0]['all_hgvs'] = 1000
    with pytest.raises(ValueError): partition_aadf(point, rows)


def test_geometry_order_is_not_traffic_direction():
    assert relative_direction(road()['geometry'], Point(0,100), 'N')['forward_sign'] == 1
    assert relative_direction(road(reverse=True)['geometry'], Point(0,100), 'N')['forward_sign'] == -1
    assert relative_direction(road()['geometry'], Point(0,100), 'E')['forward_sign'] is None
    assert relative_direction(road()['geometry'], Point(0,1), 'N')['forward_sign'] is None
    assert relative_direction(road()['geometry'], Point(100,100), 'N')['forward_sign'] is None


@pytest.mark.parametrize('tags,sign', [({'oneway':'yes'},1),({'oneway':'-1'},-1),
    ({'oneway':'no'},None),({'highway':'motorway'},None),({'oneway':'reversible'},None),
    ({'oneway':'yes','oneway:conditional':'no @ (night)'},None),
    ({'oneway':'yes','oneway:bicycle':'no'},None),({'oneway':'yes','junction':'roundabout'},None)])
def test_oneway_requires_explicit_unqualified_vehicle_evidence(tags, sign):
    assert osm_oneway_sign(tags) == sign


def test_osm_split_ways_cover_one_link_and_reverse_consistently():
    ways = [way(1,0,y1=100),way(2,0,y0=100)]
    match = OnewayMatcher(ways).match(road())
    assert match['forward_sign'] == 1 and match['way_ids'] == [1,2]
    assert OnewayMatcher(ways).match(road(reverse=True))['forward_sign'] == -1
    assert OnewayMatcher([way(1,0,sign='-1',reverse=True)]).match(road())['forward_sign'] == 1


@pytest.mark.parametrize('ways', [[way(1,0,y1=60)], [way(1,11)],
    [way(1,0,tags={'ref':'A10'})], [way(1,0),way(2,0,sign='-1')],
    [way(1,0,y1=100),way(2,0,sign='-1',y0=100)], [way(1,0,sign='no')]])
def test_incomplete_or_conflicting_oneway_support_is_withheld(ways):
    assert OnewayMatcher(ways).match(road())['forward_sign'] is None


def test_short_continuations_are_not_parallel_duplicates():
    assert not parallel_overlap(road(y1=40),road('b',y0=40,y1=80))
    assert parallel_overlap(road(y1=40),road('b',x=10,y1=40,reverse=True))
    result = allocate([road(y1=40),road('b',y0=40,y1=80)])
    assert len(result['groups']) == 2  # same flow continues; never summed as a network total


@pytest.mark.parametrize('form', ['Single Carriageway','Collapsed Dual Carriageway'])
def test_unsplit_centreline_consumes_original_streams_once(form):
    result = allocate([road(form=form)])
    group = result['groups'][0]
    assert group['published_stream_total'] == 100
    assert len(group['representations']) == 1
    assert [s['flow'] for s in group['representations'][0]['components']] == [60,40]


def test_combined_record_is_not_split_and_unpaired_is_not_zero_filled():
    combined = allocate([road()], labels=('C',),flows=(100,))['groups'][0]
    assert len(combined['representations'][0]['components']) == 1
    assert combined['representations'][0]['components'][0]['direction'] == 'C'
    assert allocate([road()],labels=('W',),flows=(100,))['groups'] == []


@pytest.mark.parametrize('change', [dict(form='Slip Road'),dict(form='Roundabout'),
    dict(form='Shared Use'),dict(road_structure='Road In Tunnel'),dict(loop=True),dict(fictitious=True)])
def test_special_roads_do_not_inherit_whole_road_flow(change):
    link = road()
    for key,value in change.items(): link['form_of_way' if key=='form' else key] = value
    result = allocate([link])
    assert not result['groups'] and result['decisions'][0]['primary_flow'] is None


def test_dual_allocation_conserves_each_published_stream_under_reversal_and_order():
    links = [road('n',x=-10,form='Dual Carriageway'), road('s',x=10,form='Dual Carriageway')]
    ways = [way(1,-10),way(2,10,sign='-1')]
    for reverse in (False, True):
        trial = deepcopy(links[::-1] if reverse else links)
        if reverse:
            for l in trial: l['geometry'] = LineString(list(l['geometry'].coords)[::-1])
        group = allocate(trial,ways)['groups'][0]
        assert group['published_stream_total'] == 100
        assert {r['link_id']:r['flow'] for r in group['representations']} == {'n':60,'s':40}
        assert sum(s['hgv_flow'] for r in group['representations'] for s in r['components']) == 10
        for f in VEHICLE_FIELDS:
            assert sum(s['vehicle_categories'][f] for r in group['representations'] for s in r['components']) == 2


def test_dual_without_authority_or_with_multiple_local_members_abstains():
    links = [road('n',x=-10,form='Dual Carriageway'),road('s',x=10,form='Dual Carriageway')]
    assert not allocate(links)['groups']
    assert not allocate(links[:1],[way(1,-10)])['groups']
    extra = road('third',x=-30,form='Dual Carriageway')
    assert not allocate(links+[extra],[way(1,-10),way(2,10,sign='-1'),way(3,-30)])['groups']
    assert not allocate(links,[way(1,-10),way(2,10,sign='-1')],labels=('C',),flows=(100,))['groups']


def test_concurrent_mixed_centreline_and_dual_are_not_double_consumed():
    links = [road('n',x=-10,form='Dual Carriageway'),road('s',x=10,form='Dual Carriageway'),
             road('centre',form='Collapsed Dual Carriageway')]
    assert not allocate(links,[way(1,-10),way(2,10,sign='-1')])['groups']
    assert not allocate([road(),road('b',x=10)])['groups']


def test_single_with_opposed_annual_counts_conflicts_with_oneway():
    assert not allocate([road()],[way(1,0)])['groups']


def test_unknown_association_and_duplicate_identity():
    result = allocate_carriageways([road()],[],{})
    assert result['decisions'][0]['primary_flow'] is None
    with pytest.raises(ValueError): allocate([road(),road()])


def test_unassociated_or_different_donor_representation_cannot_evade_guard():
    point, rows = source()
    p = partition_aadf(point,rows)
    links = [road(),road('other',x=10)]
    a = [dict(link_id='a',count_point_id='1',kind='baseline_direct')]
    assert not allocate_carriageways(links,a,{'1':p})['groups']
    point2, rows2 = deepcopy(point), deepcopy(rows)
    point2['count_point_id'] = '2'
    for r in rows2: r['count_point_id'] = 2
    a.append(dict(link_id='other',count_point_id='2',kind='baseline_direct'))
    assert not allocate_carriageways(links,a,{'1':p,'2':partition_aadf(point2,rows2)})['groups']


def test_unqualified_third_dual_member_blocks_otherwise_qualified_pair():
    point, rows = source()
    links = [road('n',x=-10,form='Dual Carriageway'),road('s',x=10,form='Dual Carriageway'),
             road('unknown',x=30,form='Dual Carriageway')]
    associations = [dict(link_id=i,count_point_id='1',kind='baseline_direct') for i in ('n','s')]
    assert not allocate_carriageways(links,associations,{'1':partition_aadf(point,rows)},
                                     [way(1,-10),way(2,10,sign='-1')])['groups']
