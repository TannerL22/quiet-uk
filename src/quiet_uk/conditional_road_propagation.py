"""Offline, conditional DIRECT road components; never complete transport noise.

Native evidence is retained. Terrain simplification and finite-cell quadrature
are declared numerical models, not field uncertainty or certified convergence.
Reflections, lateral paths and transmission are explicitly outside this scope.
"""
from collections import Counter
import math

import numpy as np
from shapely import from_wkt
from shapely.geometry import MultiPoint

from .conditional_scene_domain import execution_policy
from .favourable_propagation import atmospheric_absorption, direct_attenuation, EXACT_CENTRES
from .physical_scene_assumptions import materialize_profile, feature_top
from .propagation_canary import BANDS
from .propagation_geometry_cache import geometry_cache
from .road_emission import model_category, vehicle_power
from .road_input_audit import VEHICLE_FIELDS
from .road_operating_inputs import absorption_air_parameters

A_WEIGHT_DB=np.array([-26.2,-16.1,-8.6,-3.2,0.,1.2,1.,-1.1])
PERIODS=('day','evening','night')
REFERENCE_AIR=dict(temperature_c=20.,relative_humidity_percent=70.,pressure_kpa=101.325,uniform_air=True)
KERNEL_KEYS=('points','source','receiver','source_g','ground_segments','obstacles','opaque','infinite_lateral_extent','scene_kind')


def simplify_vertical(points,tolerance_m=.1):
    """Polyline ordinate error <= declared tolerance at every original knot.

    Endpoints remain exact. Between knots both originals and candidates are
    linear, so their difference attains its extremes at those knots. This bound
    applies to profile geometry, not diffraction or noise prediction accuracy.
    """
    if not math.isfinite(tolerance_m) or not 0<=tolerance_m<=.25:raise ValueError('Bounded explicit terrain tolerance required')
    array=np.asarray(points,dtype=float)
    if array.ndim!=2 or array.shape[1]!=2 or len(array)<2 or not np.isfinite(array).all() or np.any(np.diff(array[:,0])<=0):raise ValueError('Complete increasing native terrain required')
    if tolerance_m==0:return [list(p) for p in points],0.
    keep={0,len(points)-1};pending=[(0,len(points)-1)]
    while pending:
        a,b=pending.pop()
        if b-a<2:continue
        xs=array[a+1:b,0];expected=array[a,1]+(array[b,1]-array[a,1])*(xs-array[a,0])/(array[b,0]-array[a,0])
        errors=np.abs(array[a+1:b,1]-expected);i=int(np.argmax(errors))
        if errors[i]>tolerance_m:
            middle=a+1+i;keep.add(middle);pending.extend(((a,middle),(middle,b)))
    selected=array[sorted(keep)];error=float(np.max(np.abs(array[:,1]-np.interp(array[:,0],selected[:,0],selected[:,1]))))
    if error>tolerance_m+1e-9:raise ValueError('Terrain simplification bound violated')
    return selected.tolist(),error


def build_air_tables(weather,calendar,*,maximum_distance_m=10000.,maximum_interpolation_db=.002):
    """Exact traffic-weighted hourly transmission at knots, bounded secant reuse.

    For k=ln(10)*alpha/10000 and step h, secant relative error is bounded by
    exp(kh)*(kh)^2/8. Positive weighted sums inherit that bound. This numerical
    tolerance does not describe weather representativeness or field accuracy.
    """
    if not weather or not math.isfinite(maximum_distance_m) or maximum_distance_m<=0 or not math.isfinite(maximum_interpolation_db) or not 0<maximum_interpolation_db<=.01:raise ValueError('Positive bounded air table required')
    alphas=np.array([[atmospheric_absorption(EXACT_CENTRES[f],**absorption_air_parameters(w,accept_uniform_station_transfer=True))['alpha_db_per_km'] for f in BANDS] for w in weather],dtype=float)
    if not np.isfinite(alphas).all():raise ValueError('Complete declared candidate air required')
    groups=sorted(calendar['groups']);tables={};bounds=[]
    for band,f in enumerate(BANDS):
        kmax=float(alphas[:,band].max())*math.log(10)/10000
        step=5.
        def bound(h):return 10*math.log10(1+math.exp(kmax*h)*(kmax*h)**2/8)
        while bound(step)>maximum_interpolation_db:step/=2
        grid=np.linspace(0,maximum_distance_m,math.ceil(maximum_distance_m/step)+1)
        for period in PERIODS:
            indices=np.array([i for i,w in enumerate(weather) if w['period']==period],dtype=int)
            weights=np.array([calendar['groups'][g]['slot_weights'] for g in groups])[:,indices]
            if not len(indices) or not np.isfinite(weights).all() or np.any(weights<0) or np.any(weights.sum(axis=1)<=0):raise ValueError('Missing positive traffic/weather period support')
            weights=weights/weights.sum(axis=1,keepdims=True)
            result=np.empty((len(groups),len(grid)))
            for start in range(0,len(grid),128):
                ds=grid[start:start+128]
                exponent=-math.log(10)/10000*alphas[indices,band,None]*ds[None,:]
                result[:,start:start+len(ds)]=weights@np.exp(exponent)
            if np.any(result<=0):raise ValueError('Positive hourly transmission underflowed')
            tables[(period,f)]=dict(distance_m=grid,groups=groups,transmission=result)
        bounds.append(dict(frequency_hz=f,step_m=float(grid[1]-grid[0]),max_alpha_db_per_km=float(alphas[:,band].max()),max_interpolation_db=bound(float(grid[1]-grid[0]))))
    return dict(tables=tables,alpha_db_per_km=alphas,interpolation_bounds=bounds,
                maximum_distance_m=maximum_distance_m,maximum_interpolation_db=maximum_interpolation_db)


def source_joint_power(road,coefficients,calendar,air_tables,distance_m,surface='Reference road surface'):
    if not 0<=distance_m<=air_tables['maximum_distance_m']:return None
    result=np.zeros((3,8))
    for field in VEHICLE_FIELDS:
        count=road['annual_categories'][field]
        if count is None or count<0:return None
        if count==0:continue
        speed=road['speed']['choices'][field]['candidate_kmh']
        category=model_category(field,dict(bus_category='2',two_wheeler_category='4b'))
        try:single=vehicle_power(coefficients,category,speed,surface)
        except ValueError:return None
        group=calendar['vehicle_profiles'][field]['profile_group']
        for pi,period in enumerate(PERIODS):
            q=road['conditional_baseline']['period_traffic'][field][period]['assumed_average_vehicles_per_hour']
            for bi,f in enumerate(BANDS):
                table=air_tables['tables'][(period,f)];row=table['groups'].index(group)
                transmission=np.interp(distance_m,table['distance_m'],table['transmission'][row])
                result[pi,bi]+=single[bi]*q/(1000*speed)*transmission
    return result


def direct_transfer(candidate,case,*,terrain_tolerance_m=.1,retain_kernel_scene=False):
    """H/F geometry plus divergence; hourly air is applied with source weighting."""
    if candidate['geometry_blockers']:return dict(status='withheld',reasons=candidate['geometry_blockers'])
    original=candidate['candidate_scene'];scene={k:original[k] for k in KERNEL_KEYS}
    if scene['obstacles'] is not None:
        scene['obstacles']=sorted(scene['obstacles'],key=lambda r:(r['vertices'][0][0],r['vertices'][-1][0]))
    scene['conditional_input_policy']=execution_policy(case)
    scene['points'],error=simplify_vertical(scene['points'],terrain_tolerance_m)
    values=np.empty((2,8));reasons=[]
    with geometry_cache() as cache:
        for i,f in enumerate(BANDS):
            try:r=direct_attenuation(scene,f,REFERENCE_AIR,absorption_frequency_policy='exact_base10')
            except (ValueError,OverflowError,ZeroDivisionError) as e:
                reasons.append('unsupported_cross_section:'+str(e));continue
            if r['status']!='conditional_research':
                reasons.extend(x.get('reason','unknown_branch') for x in (r.get('homogeneous',{}),r.get('favourable',{})) if x.get('status')=='withheld')
                if not reasons:reasons.append(r.get('reason','unknown_transfer'))
                continue
            for j,c in enumerate(('homogeneous','favourable')):
                attenuation=r[c+'_attenuation_db']-r['atmospheric_attenuation_db']
                values[j,i]=10**(-attenuation/10)
    common=dict(input_scene_kind='conditional_real_input',scope='direct_only_reflections_omitted',
                native_terrain_points=len(original['points']),candidate_terrain_points=len(scene['points']),
                maximum_vertical_reconstruction_error_m=error,cache_hits=cache['hits'],cache_misses=cache['misses'],
                physical_policy_id=scene['conditional_input_policy'].physical_policy_id,physical_case_id=case['case_id'])
    if retain_kernel_scene:common['candidate_kernel_scene']={k:v for k,v in scene.items() if k!='conditional_input_policy'}
    if reasons:return dict(status='withheld',reasons=sorted(set(reasons)),**common)
    if not np.isfinite(values).all() or np.any(values<=0):return dict(status='withheld',reasons=['positive_transfer_numerical_underflow'],**common)
    return dict(status='conditional_research',relative_transfer=values,direct_distance_m=math.dist(scene['source'],scene['receiver']),**common)


def receiver_metrics(energy):
    """Three periods x eight unweighted band energies; energy A weighting/Lden."""
    e=np.asarray(energy,dtype=float)
    if e.shape!=(3,8) or not np.isfinite(e).all() or np.any(e<0):raise ValueError('Explicit finite nonnegative period bands required')
    periods=e@np.power(10.,A_WEIGHT_DB/10)
    den=(12*periods[0]+4*10**.5*periods[1]+8*10*periods[2])/24
    return {p:10*math.log10(v) if v>0 else None for p,v in zip((*PERIODS,'den'),(*periods,den))}


def road_guard(source,builder,features,case,source_policy,receiver):
    """Keep positive source/unknown-height support from disappearing between nodes."""
    if source_policy['status']!='conditional_at_grade_reference':return ['source_surface_or_deck_unresolved']
    geometry=from_wkt(source['geometry_wkt_bng'])
    for i in builder.building_tree.query(geometry,predicate='intersects'):
        if geometry.intersection(builder.buildings[int(i)]['geometry']).length>0:return ['positive_source_length_intersects_generalised_building']
    fan=MultiPoint([receiver,*[p for part in ([geometry] if geometry.geom_type=='LineString' else geometry.geoms) for p in part.coords]]).convex_hull
    for i in builder.building_tree.query(fan,predicate='intersects'):
        feature=builder.buildings[int(i)]
        if feature_top(features[feature['feature_id']],case)[0] is None:
            return ['unresolved_building_height_in_source_receiver_fan']
    return []


def integrate_source_cells(source,receiver,evaluate,*,subdivisions=(1,2)):
    """Composite midpoint over every retained cell, with a spacing comparison.

    Every original turn/length is preserved. An unknown positive cell withholds
    the whole-road component. Known evaluated support is retained separately.
    Two spacings are a diagnostic, not proof that every path transition is split.
    """
    if not subdivisions or any(type(n) is not int or n<1 for n in subdivisions) or any(a>=b for a,b in zip(subdivisions,subdivisions[1:])):raise ValueError('Increasing positive integer subdivisions required')
    history=[];records=[];reasons=Counter();seen=set();length=0.
    for cell in source['source_cells']:
        if cell['segment_id'] in seen:raise ValueError('Duplicate physical source cell')
        if not math.isfinite(cell['length_m']) or cell['length_m']<=0:raise ValueError('Positive finite physical cell required')
        seen.add(cell['segment_id']);length+=cell['length_m']
        if not math.isclose(math.dist(cell['start_xy_bng'],cell['end_xy_bng']),cell['length_m'],abs_tol=1e-8):raise ValueError('Source length/geometry mismatch')
    if not math.isclose(length,source['prepared_length_m'],abs_tol=1e-7):raise ValueError('Incomplete retained source length')
    for subdivisions_n in subdivisions:
        sums=np.zeros((2,3,8));unknown_length=0.;known_length=0.;current=[]
        for cell in source['source_cells']:
            for j in range(subdivisions_n):
                fraction=(j+.5)/subdivisions_n
                xy=[a+(b-a)*fraction for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
                width=cell['length_m']/subdivisions_n;r=evaluate(xy)
                summary={k:(v.tolist() if isinstance(v,np.ndarray) else v) for k,v in r.items()}
                current.append(dict(source_cell_id=cell['segment_id'],subdivision=j,subdivisions=subdivisions_n,
                                    xy_bng=xy,weight_length_m=width,**summary))
                if r['status']=='withheld':
                    unknown_length+=width;reasons.update(r['reasons'])
                else:
                    energy=np.asarray(r['relative_energy_per_m'],dtype=float)
                    if r['status']!='conditional_research' or energy.shape!=(2,3,8) or not np.isfinite(energy).all() or np.any(energy<0):raise ValueError('Complete finite nonnegative direct H/F energy required')
                    known_length+=width;sums+=energy*width
        if not math.isclose(known_length+unknown_length,length,abs_tol=1e-7):raise ValueError('Source quadrature lost support')
        history.append(dict(subdivisions=subdivisions_n,evaluations=len(current),known_source_length_m=known_length,
            unknown_source_length_m=unknown_length,known_evaluated_relative_energy=sums.tolist(),
            whole_road_relative_energy=sums.tolist() if unknown_length==0 else None))
        records.extend(current)
    fine=history[-1];whole=fine['whole_road_relative_energy']
    differences=None
    if whole is not None and history[0]['whole_road_relative_energy'] is not None:
        coarse=np.asarray(history[0]['whole_road_relative_energy']);fine_array=np.asarray(whole)
        def change(a,b):return a-b if a is not None and b is not None else 0. if a is None and b is None else None
        differences={c:{p:change(receiver_metrics(fine_array[i])[p],receiver_metrics(coarse[i])[p]) for p in (*PERIODS,'den')}
                     for i,c in enumerate(('homogeneous','favourable'))}
    return dict(status='conditional_discretised_direct_component' if whole is not None else 'withheld_incomplete_positive_contributions',
        whole_road_relative_energy=whole,known_evaluated_relative_energy=fine['known_evaluated_relative_energy'],
        quadrature_history=history,spacing_change_db=differences,refinement_convergence_proven=False,
        source_length_m=length,reason_counts=dict(reasons),evaluations=records,
        scope='direct_only_reflections_omitted',complete_physical_path_support=False,real_area_validated=False,
        full_road_all_paths_noise_db=None,all_inclusive_noise_db=None)
