"""Single-wall reflection on a selected horizontal-edge/terrain path.

Own Annex equations. Earlier clear-wall and boundary kernels stay frozen.
The wall lies on a leg between adjacent diffraction endpoints. All favourable
physical legs retain the radius of the whole unfolded source-receiver path.
Qualified real scenes, inclined/multiple/lateral/rail-body paths are excluded.
"""
import math

from quiet_uk.conditional_reflection_domain import reflection_scene_supported
from quiet_uk.propagation_canary import BANDS,finite_number,positive
from quiet_uk.favourable_propagation import EXACT_CENTRES,arc_length,arc_tangents,atmospheric_absorption,favourable_boundary,ray_radius
from quiet_uk.sightline_boundary import sightline_boundary
from quiet_uk.reflection_path import absent,held,mix_conditions,retrodiffraction,vector,energy_value

METHOD='EU-Annex-II-single-wall-screened-physical-leg-research'


def reflecting_leg(source,receiver,boundary,wall_x):
    edges=boundary.get('controlling_edges',[]) if boundary['branch']=='diffraction_including_ground' else []
    nodes=[list(source),*edges,list(receiver)]
    tolerance=64*math.ulp(max(1.,*(abs(v) for p in nodes for v in p),abs(wall_x)))
    if any(abs(wall_x-p[0])<=tolerance for p in nodes):return None
    return next(((a,b) for a,b in zip(nodes,nodes[1:]) if a[0]<wall_x<b[0]),None)


def leg_ray_height(a,b,x,radius=None):
    a,b=vector(a,2),vector(b,2);x=finite_number(x)
    if not a[0]<x<b[0]:raise ValueError('Reflection must be inside a physical leg')
    if radius is None:return a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
    radius=positive(radius);arc_tangents(a,b,radius)
    dx,dz=b[0]-a[0],b[1]-a[1];d=math.hypot(dx,dz);offset=math.sqrt(radius*radius-d*d/4)
    cx,cz=dx/2+dz/d*offset,dz/2-dx/d*offset
    return a[1]+cz+math.sqrt(radius*radius-(x-a[0]-cx)**2)


def leg_retrodiffraction(a,b,top,radius=None):
    lengths=[math.dist(a,top),math.dist(top,b)];direct=math.dist(a,b)
    if radius is not None:lengths=[arc_length(v,radius) for v in lengths];direct=arc_length(direct,radius)
    excess=math.fsum([*lengths,-direct])
    if excess < -1e-8:raise ValueError('Wall top is below the admissible reflecting ray')
    return -max(0.,excess)


def screened_wall_attenuation(scene,wall,frequency_hz,air,*,probability_favourable=None,absorption_frequency_policy=None,conditional_reflection_policy=None):
    """Conditional H/F reflection; no invented geometry, absorption or weather.

    The supplied unfolded profile includes both legs, complete roof G/opacity
    support and a known single planar reflector. Apparent wall dimensions are
    explicitly supplied for the selected incident leg in each condition.
    """
    f=positive(frequency_hz)
    common=dict(method=METHOD,nominal_frequency_hz=f,probability_favourable=probability_favourable,
        annual_noise_prediction=None,regulatory_implementation_qualified=False)
    if probability_favourable is not None:
        p=finite_number(probability_favourable)
        if not 0<=p<=1:raise ValueError('Favourable probability must lie in [0,1]')
    reason=None
    if f not in BANDS:reason='unsupported_nominal_frequency'
    elif not reflection_scene_supported(scene,wall,conditional_reflection_policy):reason='real_area_scene_not_qualified'
    elif scene.get('conditions') not in (None,'homogeneous','favourable'):reason='unsupported_propagation_conditions'
    elif wall is None or wall.get('unfolded_geometry_confirmed') is not True:reason='unknown_unfolded_reflection_geometry'
    elif any(wall.get(k) is None for k in ('unfolded_x_m','base_z_m','top_z_m','slope_from_vertical_deg')):reason='unknown_wall_geometry'
    elif any(scene.get(k) is None or any(v is None for v in scene[k]) for k in ('source','receiver')):reason='unknown_endpoint_elevation'
    if reason:return dict(**held(reason),homogeneous=held(reason),favourable=held(reason),**common)
    config={k:v for k,v in scene.items() if k not in ('case_id','scope','unknown_ground','conditions')}
    s,r=[vector(config[k],2) for k in ('source','receiver')]
    x,base,top,slope=[finite_number(wall[k]) for k in ('unfolded_x_m','base_z_m','top_z_m','slope_from_vertical_deg')]
    if top<=base or not 0<=slope<=90:raise ValueError('Invalid wall height or slope')
    if not s[0]<x<r[0]:raise ValueError('Wall must be inside unfolded source-receiver span')
    alphas=wall.get('absorption_by_band')
    if alphas is not None:
        if len(alphas)!=8:raise ValueError('Eight explicit absorption bands required')
        if any(a is not None and not 0<=finite_number(a)<=1 for a in alphas):raise ValueError('Absorption must lie in [0,1]')
    alpha=None if alphas is None else alphas[BANDS.index(f)]
    radius=ray_radius(s,r);distance=math.dist(s,r)

    def branch(condition):
        if slope>=15:return absent('wall_slope_outside_reflection_domain')
        if slope!=0:return held('inclined_wall_not_implemented')
        boundary=(sightline_boundary if condition=='homogeneous' else favourable_boundary)(frequency_hz=f,**config)
        details=dict(conditions=condition,boundary=boundary)
        if boundary['status']=='withheld':return held('incomplete_unfolded_boundary_inputs',**details)
        leg=reflecting_leg(s,r,boundary,x)
        if leg is None:return held('coincident_reflection_and_diffraction_or_unsupported_order',**details)
        a,b=leg;ray_radius_m=radius if condition=='favourable' else None
        try:hit=leg_ray_height(a,b,x,ray_radius_m)
        except ValueError:return held('unsupported_curved_reflection_geometry',**details)
        details.update(reflecting_leg=[a,b],hit_height_m=hit,ray_radius_m=ray_radius_m)
        tolerance=64*math.ulp(max(1.,abs(hit),abs(base),abs(top)))
        if hit<base-tolerance or hit>top+tolerance:return absent('ray_passes_outside_wall_height',**details)
        for key in ('incident_view_height_m','incident_view_width_m'):
            value=(wall.get(key) or {}).get(condition)
            if value is None:return held('unknown_incident_view_wall_dimensions',**details)
            value=finite_number(value)
            if value<0:raise ValueError('Apparent dimensions must be nonnegative')
            if value<.5:return absent('incident_view_wall_dimension_below_minimum',**details)
        if alpha is None:return held('unknown_wall_absorption',**details)
        if alpha==1:return absent('perfectly_absorbing_wall_zero_energy_extension',**details)
        if absorption_frequency_policy not in ('nominal','exact_base10'):return held('unknown_absorption_frequency_policy',**details)
        absorption=atmospheric_absorption(EXACT_CENTRES[f] if absorption_frequency_policy=='exact_base10' else f,**(air or {}))
        details['absorption']=absorption
        if absorption['status']=='withheld':return held('unknown_or_unsupported_absorption_air',**details)
        try:delta=leg_retrodiffraction(a,b,[x,top],ray_radius_m)
        except ValueError:return held('unsupported_retrodiffraction_geometry',**details)
        retro=retrodiffraction(f,delta);power=10*math.log1p(-alpha)/math.log(10)
        divergence=20*math.log10(distance)+11;atmosphere=absorption['alpha_db_per_km']*distance/1000
        attenuation=divergence+atmosphere+boundary['boundary_db']-power+retro
        return dict(status='conditional_research',attenuation_db=attenuation,energy_ratio=energy_value(attenuation),
            reflected_distance_m=distance,divergence_db=divergence,atmospheric_attenuation_db=atmosphere,
            absorption_loss_db=-power,reflection_power_correction_db=power,retrodiffraction_db=retro,
            retrodiffraction_delta_prime_m=delta,**details)
    h,fav=[branch(c) for c in ('homogeneous','favourable')];mixed=mix_conditions(h,fav,probability_favourable)
    status=mixed['status']
    if probability_favourable is None and all(b['status']!='withheld' for b in (h,fav)):status='conditional_research'
    return dict(status=status,homogeneous=h,favourable=fav,meteorological_mix=mixed,attenuation_db=mixed['attenuation_db'],
        absorption_frequency_policy=absorption_frequency_policy,**common)
