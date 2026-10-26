"""Prepare geographic road paths without promoting provisional acoustic inputs.

Native raster values and vector intersections are evidence. Straight lines between
sampled terrain ordinates, an at-grade source and receiver height are declared
reconstructions. Finite facade plans are candidates, not qualified reflections.
Actual datasets always produce unqualified kernel scenes and no real noise level.
Controlled synthetic inputs can exercise the existing propagation/line interface.
"""
import math

import numpy as np
from shapely import distance as geometry_distance
from shapely.geometry import LineString, Point, box
from shapely.strtree import STRtree

from quiet_uk.propagation_canary import finite_number, positive
from quiet_uk.reflection_path import specular_plan, vector
from quiet_uk.terrain_profile import native_intervals
from quiet_uk.road_line_integration import point_path_transfer


def parts(geometry, kind):
    if geometry.is_empty:return []
    if geometry.geom_type==kind:return [geometry]
    if hasattr(geometry,'geoms'):return [p for g in geometry.geoms for p in parts(g,kind)]
    return []


class NativeTerrain:
    """Containing cells and exact straight-ray grid intervals; no raster resampling."""
    def __init__(self, values, mask, bounds, resolution=2., *, synthetic=False):
        self.values,self.mask=np.asarray(values),np.asarray(mask)
        self.bounds=tuple(finite_number(v) for v in bounds);self.resolution=positive(resolution)
        w,s,e,n=self.bounds
        if w>=e or s>=n or (e-w)/resolution!=self.values.shape[1] or (n-s)/resolution!=self.values.shape[0] or self.mask.shape!=self.values.shape:
            raise ValueError('Native terrain grid does not match its extent')
        self.synthetic=synthetic is True

    def sample(self, xy):
        x,y=vector(xy,2);w,s,e,n=self.bounds
        if not w<=x<e or not s<y<=n:return dict(row=None,column=None,terrain_odn_m=None,state='outside')
        row,col=math.floor((n-y)/self.resolution),math.floor((x-w)/self.resolution)
        value=float(self.values[row,col]);known=bool(self.mask[row,col]) and math.isfinite(value)
        return dict(row=row,column=col,terrain_odn_m=value if known else None,state='reported' if known else 'nodata')

    def intervals(self, a, b):
        result=[]
        for cell in native_intervals(a,b,self.bounds,self.resolution):
            sample=self.sample([cell['easting'],cell['northing']])
            result.append([cell['start_m'],cell['end_m'],sample['row'],sample['column'],sample['terrain_odn_m']])
        return result


def segment_road(link_id, geometry, *, maximum_length_m=25.):
    """Keep every polyline turn and subdivide without moving geometry or power."""
    maximum_length_m=positive(maximum_length_m)
    if not link_id or geometry.geom_type not in ('LineString','MultiLineString') or not geometry.is_valid:
        raise ValueError('An identified valid road polyline is required')
    cells=[];chainage=0.
    for part_index,line in enumerate(parts(geometry,'LineString')):
        for edge,(a,b) in enumerate(zip(line.coords,line.coords[1:])):
            if len(a)!=2 or len(b)!=2:raise ValueError('Road geometry must be planar BNG')
            length=math.dist(a,b)
            if length==0:continue
            n=math.ceil(length/maximum_length_m)
            for j in range(n):
                start=[a[k]+(b[k]-a[k])*j/n for k in range(2)]
                end=[a[k]+(b[k]-a[k])*(j+1)/n for k in range(2)]
                cells.append(dict(segment_id=f'{link_id}:{part_index}:{edge}:{j}',start_xy_bng=start,end_xy_bng=end,
                    start_chainage_m=chainage+j*length/n,end_chainage_m=chainage+(j+1)*length/n,
                    length_m=math.dist(start,end),length_basis='horizontal',qualified_source_elevation_odn_m=None,
                    relative_power_per_m=None,path_breakpoints=None))
            chainage+=length
    if not cells:raise ValueError('Road has no positive source length')
    if not math.isclose(math.fsum(c['length_m'] for c in cells),geometry.length,rel_tol=1e-10,abs_tol=1e-8):
        raise ValueError('Road subdivision changed source length')
    return cells


class RoadSceneBuilder:
    """Spatially indexed terrain, footprint, barrier and one-bounce facade plans.

    ground_sampler accepts an N×2 array of BNG points and returns native-cell
    records (row,column,code,state). Ground sampling is midpoint diagnostic
    support, not exact geographic class boundary extraction. Reflection search
    is bounded by extra path length and candidate/output budgets, with omissions
    explicit. Catalogue absence and a finite halo never establish acoustic absence.
    """
    def __init__(self, terrain, ground_sampler, buildings, barriers, *,
                 source_height_agl_m=.05, receiver_height_agl_m=4.,
                 ground_step_m=2., reflection_detour_m=100.,
                 maximum_reflections=2, maximum_wall_candidates=20000,
                 controls=None):
        self.terrain,self.ground_sampler=terrain,ground_sampler
        self.buildings,self.barriers=list(buildings),list(barriers)
        self.source_height=positive(source_height_agl_m);self.receiver_height=positive(receiver_height_agl_m)
        self.ground_step=positive(ground_step_m);self.detour=positive(reflection_detour_m)
        if any(isinstance(v,bool) or not isinstance(v,int) or v<0 for v in (maximum_reflections,maximum_wall_candidates)):
            raise ValueError('Reflection budgets must be nonnegative integers')
        self.maximum_reflections,self.maximum_wall_candidates=maximum_reflections,maximum_wall_candidates
        self.controls=controls or {}
        if self.controls and not terrain.synthetic:raise ValueError('Controlled physics cannot relabel native real evidence as synthetic')
        for records in (self.buildings,self.barriers):
            ids=[r['feature_id'] for r in records]
            if len(set(ids))!=len(ids):raise ValueError('Duplicate obstacle identity')
            if any(r['geometry'].is_empty or not r['geometry'].is_valid for r in records):raise ValueError('Invalid obstacle geometry')
        self.building_tree=STRtree([r['geometry'] for r in self.buildings]);self.barrier_tree=STRtree([r['geometry'] for r in self.barriers])
        self.walls=[]
        for record in self.buildings:
            for part_index,polygon in enumerate(parts(record['geometry'],'Polygon')):
                # Generalised exterior outlines are possible facades, not verified wall planes.
                for edge,(a,b) in enumerate(zip(polygon.exterior.coords, list(polygon.exterior.coords)[1:])):
                    if math.dist(a,b)>0:self.walls.append(dict(record=record,facet_id=f"{record['feature_id']}:{part_index}:{edge}",start=list(a),end=list(b),geometry=LineString([a,b])))
        self.wall_tree=STRtree([w['geometry'] for w in self.walls])
        if self.controls.get('known_no_reflectors') and self.walls:raise ValueError('Known-no-reflectors assertion conflicts with supplied facades')

    def _crossings(self,line,tree,records,kind):
        result=[]
        for i in sorted(tree.query(line,predicate='intersects'),key=lambda i:records[int(i)]['feature_id']):
            record=records[int(i)];intersection=line.intersection(record['geometry'])
            intervals=[]
            for cut in parts(intersection,'LineString'):
                xs=[float(line.project(Point(p))) for p in cut.coords];intervals.append([min(xs),max(xs)])
            contacts=[float(line.project(p)) for p in parts(intersection,'Point')]
            result.append(dict(feature_id=record['feature_id'],kind=kind,intervals_m=intervals,contacts_m=contacts,
                acoustic_top_elevation_odn_m=record.get('acoustic_top_elevation_odn_m'),
                provisional_height_agl_m=record.get('provisional_height_agl_m'),
                opacity=record.get('opacity'),roof_g=record.get('roof_g'),historical_compatibility=record.get('historical_compatibility','unestablished')))
        return result

    def _leg(self,a,b):
        line=LineString([a,b]);length=positive(line.length)
        terrain=self.terrain.intervals(a,b)
        count=math.ceil(length/self.ground_step);width=length/count
        xy=np.asarray([[a[k]+(b[k]-a[k])*(j+.5)/count for k in range(2)] for j in range(count)])
        classes=self.ground_sampler(xy)
        if len(classes)!=count:raise ValueError('Ground sampler omitted support')
        ground=[]
        for j,cell in enumerate(classes):
            key=[cell.get('row'),cell.get('column'),cell.get('code'),cell['state']]
            if ground and ground[-1][2:]==key:ground[-1][1]=(j+1)*width
            else:ground.append([j*width,(j+1)*width,*key])
        start,end=self.terrain.sample(a),self.terrain.sample(b)
        # This is a declared sampled-ordinate reconstruction, not physical raster steps.
        points=[[0.,start['terrain_odn_m']],*[[(x+y)/2,z] for x,y,_,_,z in terrain],[length,end['terrain_odn_m']]]
        return dict(world_start_xy_bng=list(a),world_end_xy_bng=list(b),length_m=length,terrain_native_intervals=terrain,
            ground_native_samples=ground,terrain_points=points,source_terrain=start,receiver_terrain=end,
            footprint_crossings=self._crossings(line,self.building_tree,self.buildings,'building'),
            barrier_crossings=self._crossings(line,self.barrier_tree,self.barriers,'barrier'),
            endpoint_footprint_ids={key:sorted(self.buildings[int(i)]['feature_id'] for i in self.building_tree.query(Point(p),predicate='intersects')) for key,p in (('source',a),('receiver',b))})

    def _unfold(self,legs):
        points=[];terrain=[];ground=[];crossings=[];offset=0.
        for i,leg in enumerate(legs):
            points.extend([[x+offset,z] for x,z in leg['terrain_points']][1 if i else 0:])
            terrain.extend([[a+offset,b+offset,*data] for a,b,*data in leg['terrain_native_intervals']])
            ground.extend([[a+offset,b+offset,*data] for a,b,*data in leg['ground_native_samples']])
            for crossing in leg['footprint_crossings']+leg['barrier_crossings']:
                crossings.append(dict(crossing,physical_leg=i,intervals_m=[[a+offset,b+offset] for a,b in crossing['intervals_m']],contacts_m=[x+offset for x in crossing['contacts_m']]))
            offset+=leg['length_m']
        g=self.controls.get('ground_g');source=legs[0]['source_terrain']['terrain_odn_m'];receiver=legs[-1]['receiver_terrain']['terrain_odn_m']
        obstacles=[]
        for crossing in crossings:
            top=crossing['acoustic_top_elevation_odn_m']
            if crossing['kind']=='building':
                for a,b in crossing['intervals_m']:
                    if b>a:obstacles.append(dict(vertices=[[a,top],[b,top]],roof_g=crossing['roof_g']))
            else:
                for x in crossing['contacts_m']:obstacles.append(dict(vertices=[[x,top]]))
                # A collinear barrier overlap is an unresolved extent, not a dropped screen.
                for a,b in crossing['intervals_m']:obstacles.append(dict(vertices=[[a,top],[b,top]],roof_g=None))
        source_z=None if source is None else source+self.source_height
        receiver_z=None if receiver is None else receiver+self.receiver_height
        scene=dict(points=points,source=[0.,source_z],receiver=[offset,receiver_z],
            source_g=g,ground_segments=[[a,b,g] for a,b,*_ in ground],obstacles=obstacles,
            opaque=self.controls.get('opaque'),infinite_lateral_extent=self.controls.get('infinite_lateral_extent'),
            scene_kind='synthetic' if self.controls else 'unqualified')
        return dict(length_m=offset,terrain_native_intervals=terrain,ground_native_samples=ground,crossings=crossings,kernel_scene=scene,
            geometry_status='prepared_native_evidence',profile_reconstruction='Piecewise linear between containing-cell terrain midpoint ordinates and endpoint cells; reconstruction/edge qualification unestablished for real terrain.',
            ground_sampling='Full-length diagnostic midpoint intervals <= declared step; conditional native categorical support, not exact class-boundary geometry or observed G.',
            qualified_source_elevation_odn_m=None,qualified_receiver_elevation_odn_m=None,observed_ground_g=None,noise_prediction=None)

    def reflection_candidates(self,source,receiver):
        s,r=np.asarray(source),np.asarray(receiver);length=positive(float(np.linalg.norm(r-s)))
        center=(s+r)/2;u=(r-s)/length;a=(length+self.detour)/2;b=math.sqrt(max(0.,a*a-length*length/4))
        radii=[math.hypot(a*u[k],b*u[1-k]) for k in range(2)]
        query=box(center[0]-radii[0],center[1]-radii[1],center[0]+radii[0],center[1]+radii[1])
        ids=np.asarray(self.wall_tree.query(query),dtype=int);count=len(ids)
        # Cheap conservative tests before the expensive finite mirror solve.
        # Sum of separate point-to-segment minima is a lower bound on any hit.
        if count:
            geometries=self.wall_tree.geometries[ids]
            lower=geometry_distance(Point(s),geometries)+geometry_distance(Point(r),geometries)
            starts=np.asarray([self.walls[int(i)]['start'] for i in ids]);ends=np.asarray([self.walls[int(i)]['end'] for i in ids])
            delta=ends-starts;norm=np.linalg.norm(delta,axis=1);normal=np.column_stack((-delta[:,1],delta[:,0]))/norm[:,None]
            ss=np.sum((s-starts)*normal,axis=1);rs=np.sum((r-starts)*normal,axis=1)
            tol=64*math.ulp(max(1.,*(abs(v) for v in (*source,*receiver)),float(np.max(np.abs(starts))),float(np.max(np.abs(ends)))))
            ids=ids[(lower<=length+self.detour+tol)&(np.abs(ss)>tol)&(np.abs(rs)>tol)&(ss*rs>0)]
        bounded_count=len(ids)
        if bounded_count>self.maximum_wall_candidates:return dict(candidates=[],queried_facets=count,bounded_plan_candidate_facets=bounded_count,
            eligible_plan_candidates=None,unprepared_candidates=None,reason='reflection_search_budget_exceeded',support_complete=False)
        result=[];clusters={}
        for i in sorted(ids,key=lambda i:self.walls[int(i)]['facet_id']):
            w=self.walls[int(i)];key=tuple(sorted((tuple(w['start']),tuple(w['end']))))
            clusters.setdefault(key,[]).append(w)
        for cluster in clusters.values():
            w=cluster[0]
            plan_input=dict(source_xy=list(source),receiver_xy=list(receiver),wall_start_xy=w['start'],wall_end_xy=w['end'])
            plan=specular_plan(**plan_input)
            if plan['status']!='conditional_research' or plan['unfolded_horizontal_distance_m']>length+self.detour:continue
            result.append(dict(facet_id=w['facet_id'],feature_id=w['record']['feature_id'],plan_input=plan_input,plan=plan,
                coincident_facet_ids=[v['facet_id'] for v in cluster],coincident_feature_ids=sorted(set(v['record']['feature_id'] for v in cluster)),
                geometry_identity_ambiguous=len(cluster)>1,
                acoustic_top_elevation_odn_m=w['record'].get('acoustic_top_elevation_odn_m') if all(v['record'].get('acoustic_top_elevation_odn_m')==w['record'].get('acoustic_top_elevation_odn_m') for v in cluster) else None,
                absorption_by_band=w['record'].get('absorption_by_band') if all(v['record'].get('absorption_by_band')==w['record'].get('absorption_by_band') for v in cluster) else None))
        result.sort(key=lambda c:(c['plan']['unfolded_horizontal_distance_m'],c['facet_id']))
        return dict(candidates=result[:self.maximum_reflections],queried_facets=count,bounded_plan_candidate_facets=bounded_count,eligible_plan_candidates=len(result),
            unprepared_candidates=max(0,len(result)-self.maximum_reflections),reason='bounded_generalised_facade_candidates_only',support_complete=False)

    def prepare_point(self,source_xy,receiver_xy,*,reflections=True):
        source_xy,receiver_xy=vector(source_xy,2),vector(receiver_xy,2)
        direct_leg=self._leg(source_xy,receiver_xy);direct=self._unfold([direct_leg])
        search=self.reflection_candidates(source_xy,receiver_xy) if reflections else dict(candidates=[],support_complete=False,reason='reflection_search_not_requested')
        prepared=[]
        for candidate in search['candidates']:
            hit=candidate['plan']['reflection_xy'];first=self._leg(source_xy,hit);second=self._leg(hit,receiver_xy)
            profile=self._unfold([first,second]);base=self.terrain.sample(hit)['terrain_odn_m']
            wall=dict(unfolded_x_m=first['length_m'],base_z_m=base,top_z_m=candidate['acoustic_top_elevation_odn_m'],
                slope_from_vertical_deg=None,absorption_by_band=candidate['absorption_by_band'],
                incident_view_height_m=None,incident_view_width_m=None,unfolded_geometry_confirmed=None)
            prepared.append(dict(candidate,profile=profile,wall=wall,qualification='Finite plan hit only; wall height/base, inclination, view dimensions, opacity, absorption and historical facade geometry remain unqualified.'))
        blockers=['unqualified_terrain_reconstruction','unknown_source_road_surface_or_deck','receiver_height_and_outdoor_location_assumed',
            'unknown_observed_ground_g','incomplete_acoustic_obstacle_inventory','unqualified_historical_geometry','unknown_absorption_air_and_directional_weather',
            'incomplete_reflection_support'] if not self.controls else []
        if any(z is None for _,_,_,_,z in direct['terrain_native_intervals']):blockers.append('missing_native_terrain')
        if direct_leg['endpoint_footprint_ids']['receiver']:blockers.append('receiver_intersects_generalised_building')
        if direct_leg['endpoint_footprint_ids']['source']:blockers.append('source_intersects_generalised_building')
        return dict(source_xy_bng=source_xy,receiver_xy_bng=receiver_xy,direct=direct,reflections=prepared,
            reflection_search={k:v for k,v in search.items() if k!='candidates'},endpoint_footprint_ids=direct_leg['endpoint_footprint_ids'],
            assumed_source_height_agl_m=self.source_height,assumed_receiver_height_agl_m=self.receiver_height,
            status='withheld' if blockers else 'controlled_geometry_prepared',blockers=blockers,receiver_noise_db=None,annual_noise_prediction=None)

    def transfer_at_source(self,source_xyz,receiver_xyz,frequency_hz,*,air=None,probability_favourable=None,absorption_frequency_policy=None):
        """Callback for the line integrator; actual incomplete support stays held."""
        prepared=self.prepare_point(source_xyz[:2],receiver_xyz[:2],reflections=not self.controls.get('known_no_reflectors',False))
        for expected,actual in zip((source_xyz[2],receiver_xyz[2]),(prepared['direct']['kernel_scene']['source'][1],prepared['direct']['kernel_scene']['receiver'][1])):
            if actual is not None and not math.isclose(expected,actual,abs_tol=1e-8):raise ValueError('World endpoint elevation disagrees with the declared reconstruction')
        refs=[dict(scene=c['profile']['kernel_scene'],wall=c['wall'],plan_input=c['plan_input']) for c in prepared['reflections']]
        return point_path_transfer(source_xyz,receiver_xyz,frequency_hz,direct_scene=prepared['direct']['kernel_scene'],reflections=refs,
            reflection_support_complete=bool(self.controls.get('known_no_reflectors')),air=air,
            probability_favourable=probability_favourable,absorption_frequency_policy=absorption_frequency_policy)
