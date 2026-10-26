"""Bounded single vertical-wall research paths, not real-area noise levels.

Own image-source and EU Annex II equations 2.5.35..38. The 2021 amendment
requires a curved favourable hit and incident-view dimensions. Supplied view
dimensions are explicit qualifications, never inferred from footprint lengths.
Reflection/diffraction interactions, inclined walls and multiple reflections
remain outside this increment. A perfectly absorbing wall is an explicit zero
energy extension of the Annex's alpha<1 domain, avoiding infinity in JSON.
"""
import math

from quiet_uk.propagation_canary import BANDS, finite_number, positive
from quiet_uk.favourable_propagation import (
    EXACT_CENTRES, arc_tangents, atmospheric_absorption, curved_geometry,
    favourable_boundary, ray_radius,
)
from quiet_uk.sightline_boundary import sightline_boundary

METHOD = 'EU-Annex-II-2018-2021-single-vertical-wall-clear-path-research'
CONDITIONS = ('homogeneous', 'favourable')


def vector(point, size):
    if len(point) != size: raise ValueError('Unexpected coordinate dimension')
    return [finite_number(v) for v in point]


def absent(reason, **details):
    return dict(status='absent', reason=reason, attenuation_db=None, energy_ratio=0., **details)


def held(reason, **details):
    return dict(status='withheld', reason=reason, attenuation_db=None, energy_ratio=None, **details)


def specular_plan(source_xy, receiver_xy, wall_start_xy, wall_end_xy):
    """Horizontal image source, finite segment hit, and unfolded path distance.

    Known plan misses are absent; unknown geometry is withheld. Wall endpoint
    order does not define a preferred reflective face. Elevation is separate.
    """
    if any(p is None or any(v is None for v in p) for p in (source_xy, receiver_xy, wall_start_xy, wall_end_xy)):
        return held('unknown_plan_geometry')
    s,r,a,b = [vector(p,2) for p in (source_xy,receiver_xy,wall_start_xy,wall_end_xy)]
    v = [b[i]-a[i] for i in range(2)]; length = positive(math.hypot(*v))
    unit = [q/length for q in v]; normal = [-unit[1],unit[0]]
    sides = [sum((p[i]-a[i])*normal[i] for i in range(2)) for p in (s,r)]
    tolerance = 64*math.ulp(max(1.,*(abs(q) for p in (s,r,a,b) for q in p)))
    if any(abs(q)<=tolerance for q in sides) or sides[0]*sides[1] <= 0:
        return absent('endpoints_not_on_same_open_wall_side')
    image = [s[i]-2*sides[0]*normal[i] for i in range(2)]
    t = -sum((image[i]-a[i])*normal[i] for i in range(2))/sum((r[i]-image[i])*normal[i] for i in range(2))
    hit = [image[i]+t*(r[i]-image[i]) for i in range(2)]
    u = sum((hit[i]-a[i])*unit[i] for i in range(2))/length
    if not -tolerance/length <= u <= 1+tolerance/length:
        return absent('specular_hit_outside_wall_segment')
    if not 0 <= u <= 1:
        u = min(1.,max(0.,u));hit = [a[i]+u*v[i] for i in range(2)]
    return dict(status='conditional_research', image_source_xy=image, reflection_xy=hit, wall_fraction=u,
        reflection_distance_from_source_m=math.dist(s,hit), unfolded_horizontal_distance_m=math.dist(image,r))


def upper_ray_height(source, receiver, x_m, conditions):
    """Straight H ray or upper minor circular F ray at unfolded abscissa."""
    s,r = vector(source,2),vector(receiver,2); x = finite_number(x_m)
    if r[0] <= s[0] or not s[0] < x < r[0]: raise ValueError('Wall must be between unfolded endpoints')
    fraction = (x-s[0])/(r[0]-s[0])
    if conditions == 'homogeneous':
        return dict(height_m=s[1]+fraction*(r[1]-s[1]), radius_m=None)
    if conditions != 'favourable': raise ValueError('Unknown propagation conditions')
    radius = ray_radius(s,r); arc_tangents(s,r,radius)
    dx,dz = r[0]-s[0],r[1]-s[1]; distance = math.hypot(dx,dz)
    offset = math.sqrt(radius*radius-distance*distance/4)
    cx,cz = dx/2+dz/distance*offset,dz/2-dx/distance*offset
    height = s[1]+cz+math.sqrt(radius*radius-(x-s[0]-cx)**2)
    return dict(height_m=height, radius_m=radius)


def retrodiffraction(frequency_hz, delta_prime_m):
    """2.5.37, Ch=1; no multiple-edge coefficient or 25 dB barrier cap."""
    f,delta = positive(frequency_hz),finite_number(delta_prime_m)
    if delta > 1e-8: raise ValueError('Retrodiffraction requires an admissible wall hit')
    scaled = 40*min(0.,delta)/(340/f)
    return 0. if scaled < -2 else 10*math.log10(3+scaled)


def energy_value(attenuation):
    exponent = -attenuation/10
    return 10**exponent if exponent < 308 else None


def combine_paths_attenuation(branches):
    """Add equal-source-power incoherent path energies for ONE weather state.

    This is a conditional transfer attenuation, not a total noise level. An
    absent path contributes zero; a path with unknown inputs prevents a total.
    """
    branches = list(branches)
    if any(b['status']=='withheld' for b in branches): return held('unknown_path_contribution')
    if any(b['status'] not in ('conditional_research','absent') for b in branches):
        raise ValueError('Unexpected path status')
    values = [finite_number(b['attenuation_db']) for b in branches if b['status']=='conditional_research']
    if not values: return absent('all_paths_absent')
    base = min(values)
    attenuation = base-10*math.log10(math.fsum(10**(-(v-base)/10) for v in values))
    return dict(status='conditional_research', attenuation_db=attenuation, energy_ratio=energy_value(attenuation))


def mix_conditions(homogeneous, favourable, probability):
    if probability is None: return held('unknown_favourable_probability')
    p = finite_number(probability)
    if not 0 <= p <= 1: raise ValueError('Favourable probability must lie in [0,1]')
    pairs = [(b,w) for b,w in ((homogeneous,1-p),(favourable,p)) if w > 0]
    if any(b['status']=='withheld' for b,w in pairs): return held('unknown_weather_path_contribution')
    active = [(b,w) for b,w in pairs if b['status']=='conditional_research']
    if not active: return absent('all_weighted_paths_absent')
    base = min(b['attenuation_db'] for b,w in active)
    attenuation = base-10*math.log10(math.fsum(w*10**(-(b['attenuation_db']-base)/10) for b,w in active))
    return dict(status='conditional_research', attenuation_db=attenuation, energy_ratio=energy_value(attenuation))


def single_wall_attenuation(scene, wall, frequency_hz, air, *, probability_favourable=None, absorption_frequency_policy=None):
    """H/F reflected transfer for a supplied complete unfolded clear path.

    The unfolded profile follows both physical legs cumulatively; it is not a
    straight source-receiver terrain sample. Wall base/top and height inputs use
    the same vertical datum as terrain/endpoints. No hidden wall material,
    apparent dimensions, air, centre policy or weather probability defaults.
    """
    f = positive(frequency_hz)
    common = dict(method=METHOD, nominal_frequency_hz=f, probability_favourable=probability_favourable,
        annual_noise_prediction=None, regulatory_implementation_qualified=False)
    if probability_favourable is not None:
        p = finite_number(probability_favourable)
        if not 0 <= p <= 1: raise ValueError('Favourable probability must lie in [0,1]')
    config = {k:v for k,v in scene.items() if k not in ('case_id','scope','unknown_ground','conditions')}
    reason = None
    if f not in BANDS: reason = 'unsupported_nominal_frequency'
    elif scene.get('scene_kind') not in ('synthetic','published_reference'): reason = 'real_area_scene_not_qualified'
    elif scene.get('conditions') not in (None,*CONDITIONS): reason = 'unsupported_propagation_conditions'
    elif wall is None or wall.get('unfolded_geometry_confirmed') is not True: reason = 'unknown_unfolded_reflection_geometry'
    elif any(wall.get(k) is None for k in ('unfolded_x_m','base_z_m','top_z_m','slope_from_vertical_deg')):
        reason = 'unknown_wall_geometry'
    elif scene.get('obstacles') != []: reason = 'reflection_diffraction_interaction_not_implemented'
    elif any(scene.get(k) is None for k in ('source','receiver')) or any(v is None for k in ('source','receiver') for v in scene[k]):
        reason = 'unknown_endpoint_elevation'
    if reason:
        return dict(**held(reason), homogeneous=held(reason), favourable=held(reason), **common)
    s,r = [vector(config[k],2) for k in ('source','receiver')]
    x,base,top,slope = [finite_number(wall[k]) for k in ('unfolded_x_m','base_z_m','top_z_m','slope_from_vertical_deg')]
    if top <= base or not 0 <= slope <= 90: raise ValueError('Invalid wall height or slope')
    if not s[0] < x < r[0]: raise ValueError('Wall must be between unfolded endpoints')
    alphas = wall.get('absorption_by_band')
    if alphas is not None:
        if len(alphas) != len(BANDS): raise ValueError('Eight explicit wall absorption bands required')
        for a in alphas:
            if a is not None and not 0 <= finite_number(a) <= 1: raise ValueError('Wall absorption must lie in [0,1]')
    alpha = None if alphas is None else alphas[BANDS.index(f)]

    def branch(conditions):
        if slope >= 15: return absent('wall_slope_outside_reflection_domain')
        if slope != 0: return held('inclined_wall_not_implemented')
        try: hit = upper_ray_height(s,r,x,conditions)
        except ValueError: return held('unsupported_curved_reflection_geometry')
        details = dict(conditions=conditions, hit_height_m=hit['height_m'], ray_radius_m=hit['radius_m'])
        if not base <= hit['height_m'] <= top:
            return absent('ray_passes_outside_wall_height', **details)
        for key in ('incident_view_height_m','incident_view_width_m'):
            value = (wall.get(key) or {}).get(conditions)
            if value is None: return held('unknown_incident_view_wall_dimensions', **details)
            value = finite_number(value)
            if value < 0: raise ValueError('Incident-view dimensions must be nonnegative')
            if value < .5: return absent('incident_view_wall_dimension_below_minimum', **details)
        if alpha is None: return held('unknown_wall_absorption', **details)
        if alpha == 1: return absent('perfectly_absorbing_wall_zero_energy_extension', **details)
        boundary = (sightline_boundary if conditions=='homogeneous' else favourable_boundary)(frequency_hz=f, **config)
        details['boundary'] = boundary
        if boundary['status']=='withheld': return held('incomplete_unfolded_boundary_inputs', **details)
        if boundary['branch']=='diffraction_including_ground':
            return held('reflection_diffraction_interaction_not_implemented', **details)
        if absorption_frequency_policy not in ('nominal','exact_base10'):
            return held('unknown_absorption_frequency_policy', **details)
        absorption = atmospheric_absorption(EXACT_CENTRES[f] if absorption_frequency_policy=='exact_base10' else f, **(air or {}))
        details['absorption'] = absorption
        if absorption['status']=='withheld': return held('unknown_or_unsupported_absorption_air', **details)
        edge = [x,top]
        try:
            if conditions=='homogeneous': excess = math.fsum([math.dist(s,edge),math.dist(edge,r),-math.dist(s,r)])
            else: excess = curved_geometry(s,r,[edge])['delta_m']
        except ValueError: return held('unsupported_retrodiffraction_geometry', **details)
        if excess < -1e-8: return held('unsupported_retrodiffraction_geometry', **details)
        delta = -max(0.,excess); retro = retrodiffraction(f,delta)
        power = 10*math.log1p(-alpha)/math.log(10)
        distance = math.dist(s,r); divergence = 20*math.log10(distance)+11
        atmosphere = absorption['alpha_db_per_km']*distance/1000
        attenuation = divergence+atmosphere+boundary['boundary_db']-power+retro
        return dict(status='conditional_research', attenuation_db=attenuation, energy_ratio=energy_value(attenuation),
            reflected_distance_m=distance, divergence_db=divergence, atmospheric_attenuation_db=atmosphere,
            absorption_loss_db=-power, reflection_power_correction_db=power, retrodiffraction_db=retro,
            retrodiffraction_delta_prime_m=delta, **details)

    h,fav = [branch(c) for c in CONDITIONS]
    mixed = mix_conditions(h,fav,probability_favourable)
    # Zero-probability states do not require a transfer value. Keep conditional
    # branches available when only the mixing probability is unknown.
    status = mixed['status']
    if probability_favourable is None and all(b['status']!='withheld' for b in (h,fav)):
        status = 'conditional_research'
    return dict(status=status, homogeneous=h, favourable=fav, meteorological_mix=mixed,
        attenuation_db=mixed['attenuation_db'], absorption_frequency_policy=absorption_frequency_policy, **common)
