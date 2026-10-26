import math

import numpy as np
import pytest

from quiet_uk.terrain_profile import (equivalent_point, linear_segments, mean_plane,
    native_intervals, planar_ground_check, plane_frame, split_planes, upper_path)


def test_irregular_sampling_does_not_weight_extra_vertices_as_extra_ground():
    profile=[(0,0),(50,10),(100,0)]
    dense=[(0,0),(1,.2),(2,.4),(3,.6),(50,10),(100,0)]
    a=mean_plane(linear_segments(profile));b=mean_plane(linear_segments(dense))
    assert a['slope']==pytest.approx(0,abs=1e-15)
    assert a['intercept']==pytest.approx(5)
    assert b['intercept']==pytest.approx(a['intercept'])
    assert b['slope']==pytest.approx(a['slope'],abs=1e-15)
    assert np.polyfit(*np.array(dense).T,1)[1] != pytest.approx(5)


@pytest.mark.parametrize('slope',[-.4,-.1,0,.1,.4])
def test_exact_sloping_ground_and_orthogonal_heights(slope):
    points=[(0,30),(17,30+17*slope),(100,30+100*slope)]
    p=mean_plane(linear_segments(points))
    assert p['slope']==pytest.approx(slope,abs=1e-15)
    assert p['intercept']==pytest.approx(30)
    assert p['rms_residual_m']<1e-13
    f=plane_frame(p,[0,30+.05],[100,30+100*slope+4])
    assert f['source']['equivalent_height_m']==pytest.approx(.05/math.hypot(1,slope))
    assert f['receiver']['equivalent_height_m']==pytest.approx(4/math.hypot(1,slope))
    assert f['projected_distance_m']==pytest.approx((100+slope*(100*slope+3.95))/math.hypot(1,slope))
    assert f['direct_distance_m']==pytest.approx(math.hypot(100,100*slope+3.95))


def test_horizontal_and_vertical_translation_preserves_normalised_fit():
    points=[(0,0),(10,4),(60,-1),(100,2)]
    baseline=mean_plane(linear_segments(points))
    shifted=mean_plane(linear_segments([(x+1e9,z+1e6) for x,z in points]))
    assert shifted['slope']==pytest.approx(baseline['slope'],abs=1e-12)
    assert shifted['height_at_origin_m']-1e6==pytest.approx(baseline['height_at_origin_m'],abs=1e-9)
    assert shifted['rms_residual_m']==pytest.approx(baseline['rms_residual_m'],abs=1e-9)


def test_constant_native_intervals_have_length_weights():
    p=mean_plane([(0,25,0,0),(25,100,10,10)])
    assert p['slope']==pytest.approx(.1125)
    assert p['height_at_origin_m']==pytest.approx(1.875)


def test_negative_equivalent_height_clips_only_derived_geometry():
    p=mean_plane(linear_segments([(0,0),(50,10),(100,0)]))
    point=equivalent_point(0,1,p)
    assert point['signed_height_m']==pytest.approx(-4)
    assert point['original']==[0,1]
    assert point['equivalent_height_m']==0
    assert point['equivalent_point']==point['mirror_point']==point['projection']
    assert point['negative_equivalent_height_clipped']


def test_missing_interval_is_not_dropped_and_zero_elevation_is_valid():
    p=mean_plane([(0,25,0,0),(25,50,None,None),(50,100,3,3)])
    assert p['status']=='withheld' and p['missing_length_m']==25
    assert p['slope'] is None
    assert mean_plane([(0,100,0,0)])['height_at_origin_m']==0
    assert mean_plane([(0,100,-3,-3)])['height_at_origin_m']==-3


@pytest.mark.parametrize('points',[[],[(0,0)],[(0,0),(0,1)],[(0,0),(-1,1)],[(0,0),(math.inf,1)],[(False,0),(1,1)]])
def test_malformed_profiles_are_refused(points):
    with pytest.raises(ValueError):linear_segments(points)


@pytest.mark.parametrize('segments',[[],[(1,0,0,0)],[(0,1,0,0),(2,3,0,0)],[(0,2,0,0),(1,3,0,0)],[(0,1,math.nan,0)]])
def test_invalid_interval_support_is_refused(segments):
    with pytest.raises(ValueError):mean_plane(segments)


def test_ridge_upper_path_and_separate_side_planes():
    pts=[(0,0),(50,10),(100,0)]
    r=upper_path(pts,[0,1],[100,1])
    assert r['edge_count']==1 and r['direct_ray_blocked']
    assert r['path']==[[0,1],[50,10],[100,1]]
    assert r['path_difference_m']==pytest.approx(2*math.hypot(50,9)-100)
    assert r['noise_attenuation_db'] is None
    sides=split_planes(pts,50,[0,1],[100,1])
    assert sides['source_plane']['slope']==pytest.approx(.2)
    assert sides['receiver_plane']['slope']==pytest.approx(-.2)
    assert sides['source_frame']['receiver']['equivalent_height_m']==pytest.approx(0,abs=1e-13)
    assert sides['noise_attenuation_db'] is None


def test_multiple_ridges_keep_upper_path_and_discard_lower_dip():
    r=upper_path([(0,0),(25,10),(50,1),(75,10),(100,0)],[0,1],[100,1])
    assert r['path']==[[0,1],[25,10],[75,10],[100,1]]
    assert r['edge_count']==2


def test_tangent_path_is_recorded_separately_from_blocked():
    r=upper_path([(0,0),(50,1),(100,0)],[0,1],[100,1])
    assert not r['direct_ray_blocked'] and r['touches_terrain']
    assert r['path_difference_m']==0


def test_clear_nonplanar_ground_still_withholds_acoustic_branch():
    pts=[(0,0),(50,-10),(100,0)]
    assert upper_path(pts,[0,1],[100,4])['edge_count']==0
    r=planar_ground_check(pts,[0,1],[100,4],1000,1)
    assert r['status']=='withheld'
    assert r['reason']=='nonplanar_terrain_requires_qualified_diffraction_selector'


@pytest.mark.parametrize('f',[63,125,250,500,1000,2000,4000])
def test_explicit_planar_hard_ground_has_one_boundary_term(f):
    r=planar_ground_check([(0,0),(100,20)],[0,.05],[100,24],f,0)
    assert r['boundary_db']==-3
    assert r['annual_noise_prediction'] is None
    assert not r['regulatory_implementation_qualified']


def test_unknowns_and_frequency_scope_do_not_become_numeric_ground_results():
    pts=[(0,0),(100,0)]
    for p,s,r,g in [(pts,[0,.05],[100,4],None), ([(0,None),(100,0)],[0,.05],[100,4],0),(pts,None,[100,4],0)]:
        assert planar_ground_check(p,s,r,1000,g)['boundary_db'] is None
    assert planar_ground_check(pts,[0,.05],[100,4],8000,0)['boundary_db'] is None


def test_endpoints_beneath_local_terrain_are_rejected():
    with pytest.raises(ValueError):upper_path([(0,2),(100,3)],[0,1],[100,4])


@pytest.mark.parametrize('start,end,expected', [((.5,3.5),(3.5,.5),[(0,0),(1,1)]),
    ((2,3.5),(2,.5),[(0,1),(1,1)]),((.5,4),(3.5,4),[(0,0),(0,1)]),
    ((4,.5),(4,3.5),[(None,None),(None,None)])])
def test_native_cell_traversal_ownership_and_corner_crossings(start,end,expected):
    parts=native_intervals(start,end,(0,0,4,4))
    assert [(r['row'],r['column']) for r in parts]==expected
    assert sum(r['represented_length_m'] for r in parts)==pytest.approx(math.dist(start,end))
    assert all(r['represented_length_m']>0 for r in parts)


def test_native_outside_support_remains_an_interval():
    parts=native_intervals((-1,3),(3,3),(0,0,4,4))
    assert [r['column'] for r in parts]==[None,0,1]
    assert [r['represented_length_m'] for r in parts]==[1,2,1]


def test_native_budget_and_empty_path_are_refused():
    with pytest.raises(ValueError):native_intervals((0,0),(0,0),(0,0,4,4))
    with pytest.raises(ValueError):native_intervals((0,1),(100000,1),(0,0,100000,4))
