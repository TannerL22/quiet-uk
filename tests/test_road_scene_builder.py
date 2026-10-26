"""Native evidence, finite facade geometry and the geographic line adapter."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from shapely.geometry import LineString,Polygon

from quiet_uk.road_scene_builder import NativeTerrain,RoadSceneBuilder,segment_road
from quiet_uk.road_line_integration import integrate_road_line
from quiet_uk.favourable_propagation import atmospheric_absorption

def classes(xy):return [dict(row=0,column=0,code=50,state='reported_class') for _ in xy]
def terrain(*,synthetic=False):return NativeTerrain(np.zeros((100,100)),np.ones((100,100)),(-100,-100,100,100),synthetic=synthetic)
def building(id,geometry):return dict(feature_id=id,geometry=geometry,acoustic_top_elevation_odn_m=None,provisional_height_agl_m=8.,roof_g=None)

def test_zero_and_below_datum_native_elevations_remain_valid():
    t=NativeTerrain(np.array([[0.,-2.],[1.,2.]]),np.ones((2,2)),(0,0,4,4))
    assert t.sample([1,3])['terrain_odn_m']==0
    assert t.sample([3,3])['terrain_odn_m']==-2
    assert t.sample([4,3])['state']=='outside'
    assert t.sample([1,0])['state']=='outside'

def test_missing_native_intervals_are_retained_in_full_length_denominator():
    values=np.zeros((2,2));mask=np.ones((2,2));mask[0,1]=0
    t=NativeTerrain(values,mask,(0,0,4,4));intervals=t.intervals([.5,3],[5.5,3])
    assert sum(b-a for a,b,*_ in intervals)==pytest.approx(5.)
    assert sum(b-a for a,b,_,_,z in intervals if z is None)==pytest.approx(3.5)
    assert t.sample([3,3])['terrain_odn_m'] is None

@pytest.mark.parametrize('coordinates',[[(0,0),(3,0),(3,4)],[(3,4),(3,0),(0,0)],[(0,0),(0,0),(3,0),(3,4)]])
def test_road_cells_preserve_all_turns_endpoints_and_horizontal_power(coordinates):
    line=LineString(coordinates);cells=segment_road('road',line,maximum_length_m=2)
    assert math.fsum(c['length_m'] for c in cells)==pytest.approx(7.)
    assert len({c['segment_id'] for c in cells})==len(cells)
    assert cells[0]['start_xy_bng']==list(coordinates[0]) and cells[-1]['end_xy_bng']==list(coordinates[-1])
    assert any(c['end_xy_bng']==[3,0] for c in cells)
    assert all(c['length_m']<=2+1e-12 and c['relative_power_per_m'] is None for c in cells)
    assert all(c['path_breakpoints'] is None for c in cells)

def test_native_profile_is_not_promoted_to_a_qualified_real_scene():
    builder=RoadSceneBuilder(terrain(),classes,[],[])
    result=builder.prepare_point([0,0],[20,0]);profile=result['direct']
    assert profile['kernel_scene']['scene_kind']=='unqualified'
    assert profile['kernel_scene']['source_g'] is None
    assert all(g is None for _,_,g in profile['kernel_scene']['ground_segments'])
    assert profile['qualified_source_elevation_odn_m'] is None
    assert result['status']=='withheld' and result['receiver_noise_db'] is None
    assert result['reflection_search']['support_complete'] is False

def test_native_real_raster_cannot_be_relabelled_as_a_synthetic_example():
    with pytest.raises(ValueError,match='relabel'):RoadSceneBuilder(terrain(),classes,[],[],controls={'ground_g':0})

def test_footprint_hole_keeps_separate_roof_intervals():
    polygon=Polygon([(0,-3),(10,-3),(10,3),(0,3)],holes=[[(3,-1),(7,-1),(7,1),(3,1)]])
    builder=RoadSceneBuilder(terrain(),classes,[building('courtyard',polygon)],[])
    result=builder.prepare_point([-2,0],[12,0],reflections=False)
    crossing=result['direct']['crossings'][0]
    assert sorted(crossing['intervals_m'])==[[2.,5.],[9.,12.]]
    obstacles=result['direct']['kernel_scene']['obstacles']
    assert len(obstacles)==2 and all(o['roof_g'] is None for o in obstacles)
    assert all(z is None for o in obstacles for _,z in o['vertices'])

def test_barrier_intersection_preserves_unknown_height_and_datum():
    barrier=dict(feature_id='fence',geometry=LineString([(5,-10),(5,10)]),acoustic_top_elevation_odn_m=None)
    result=RoadSceneBuilder(terrain(),classes,[],[barrier]).prepare_point([0,0],[10,0],reflections=False)
    assert result['direct']['crossings'][0]['contacts_m']==[5.]
    assert result['direct']['kernel_scene']['obstacles']==[dict(vertices=[[5.,None]])]

def test_source_and_receiver_in_buildings_are_flagged_without_relocation():
    polygon=Polygon([(-2,-2),(2,-2),(2,2),(-2,2)])
    result=RoadSceneBuilder(terrain(),classes,[building('inside',polygon)],[]).prepare_point([0,0],[1,0],reflections=False)
    assert result['source_xy_bng']==[0.,0.] and result['receiver_xy_bng']==[1.,0.]
    assert 'source_intersects_generalised_building' in result['blockers']
    assert 'receiver_intersects_generalised_building' in result['blockers']

def test_finite_reflection_plan_unfolds_two_physical_legs_with_conserved_lengths():
    wall=building('wall',Polygon([(-2,-10),(0,-10),(0,10),(-2,10)]))
    builder=RoadSceneBuilder(terrain(),classes,[wall],[],maximum_reflections=1)
    result=builder.prepare_point([2,0],[2,8]);reflection=result['reflections'][0]
    assert reflection['plan']['reflection_xy']==pytest.approx([0.,4.])
    assert reflection['profile']['length_m']==pytest.approx(math.sqrt(80))
    assert reflection['wall']['unfolded_x_m']==pytest.approx(math.sqrt(20))
    for name in ('terrain_native_intervals','ground_native_samples'):
        assert sum(b-a for a,b,*_ in reflection['profile'][name])==pytest.approx(math.sqrt(80))
    assert reflection['wall']['top_z_m'] is None
    assert reflection['wall']['incident_view_height_m'] is None
    assert result['reflection_search']['unprepared_candidates']>0

def test_reflection_candidate_budgets_are_explicit_and_never_imply_absence():
    wall=building('wall',Polygon([(-2,-10),(0,-10),(0,10),(-2,10)]))
    builder=RoadSceneBuilder(terrain(),classes,[wall],[],maximum_wall_candidates=0)
    result=builder.prepare_point([2,0],[2,8])
    assert result['reflection_search']['reason']=='reflection_search_budget_exceeded'
    assert result['reflection_search']['eligible_plan_candidates'] is None
    assert result['reflection_search']['support_complete'] is False

def test_coincident_facades_keep_both_identities_without_duplicating_a_path():
    polygon=Polygon([(-2,-10),(0,-10),(0,10),(-2,10)])
    builder=RoadSceneBuilder(terrain(),classes,[building('first',polygon),building('second',polygon)],[],maximum_reflections=1)
    result=builder.prepare_point([2,0],[2,8]);r=result['reflections'][0]
    assert r['geometry_identity_ambiguous']
    assert r['coincident_feature_ids']==['first','second'] and len(r['coincident_facet_ids'])==2

def test_known_absence_of_reflectors_cannot_override_supplied_facades():
    with pytest.raises(ValueError,match='conflicts'):RoadSceneBuilder(terrain(synthetic=True),classes,
        [building('wall',Polygon([(-2,-10),(0,-10),(0,10),(-2,10)]))],[],controls={'known_no_reflectors':True})

def test_reversal_preserves_direct_native_value_length_distribution():
    builder=RoadSceneBuilder(terrain(),classes,[],[])
    a=builder.prepare_point([-5,0],[12,9],reflections=False)['direct']
    b=builder.prepare_point([12,9],[-5,0],reflections=False)['direct']
    def lengths(profile):
        result={}
        for start,end,row,col,z in profile['terrain_native_intervals']:result[(row,col,z)]=result.get((row,col,z),0.)+end-start
        return result
    for key,value in lengths(a).items():assert value==pytest.approx(lengths(b)[key],abs=1e-10)

def test_unknown_ground_class_sampler_cannot_omit_path_intervals():
    builder=RoadSceneBuilder(terrain(),lambda xy:[],[],[])
    with pytest.raises(ValueError,match='omitted'):builder.prepare_point([0,0],[10,0])

def test_synthetic_geographic_builder_connects_through_full_road_line_quadrature():
    builder=RoadSceneBuilder(terrain(synthetic=True),classes,[],[],controls=dict(ground_g=0.,opaque=True,infinite_lateral_extent=True,known_no_reflectors=True))
    air=dict(temperature_c=10.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)
    def transfer(s,r,f):return builder.transfer_at_source(s,r,f,air=air,probability_favourable=.5,absorption_frequency_policy='exact_base10')
    road=LineString([(-10,0),(0,0),(10,0)]);cells=segment_road('road',road,maximum_length_m=5.)
    segments=[dict(segment_id=c['segment_id'],start=[*c['start_xy_bng'],.05],end=[*c['end_xy_bng'],.05],length_basis='horizontal',relative_power_per_m=[1.]*8,breakpoints=[]) for c in cells]
    result=integrate_road_line(segments,[0.,20.,4.],1000,transfer,scene_kind='synthetic',metre_coordinates_confirmed=True,path_support_complete=True,tolerance_db=.001)
    alpha=atmospheric_absorption(1000.,**air)['alpha_db_per_km']
    reference=quad(lambda x:10**(-1.1+.3)*10**(-alpha*math.sqrt(x*x+20**2+3.95**2)/10000)/(x*x+20**2+3.95**2),-10.,10.,epsabs=1e-12)[0]
    assert result['source_relative_power']==20
    assert abs(10*math.log10(result['branches']['mixed']['relative_energy']/reference))<=.001
    assert result['annual_noise_prediction'] is None

def test_world_elevations_must_agree_with_declared_geographic_reconstruction():
    builder=RoadSceneBuilder(terrain(synthetic=True),classes,[],[],controls=dict(ground_g=0.,opaque=True,infinite_lateral_extent=True,known_no_reflectors=True))
    with pytest.raises(ValueError,match='elevation'):builder.transfer_at_source([0.,0.,100.],[20.,0.,4.],1000)

@pytest.mark.parametrize('invalid',[0.,-1.,True,'2',math.nan,math.inf])
def test_invalid_source_cell_spacing_is_rejected(invalid):
    with pytest.raises(ValueError):segment_road('road',LineString([(0,0),(1,0)]),maximum_length_m=invalid)
