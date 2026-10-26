"""Connect explicit facade hypotheses, native profiles and road power per metre.

The selected finite path sum is available separately from complete road noise.
Each path uses its own distance for traffic-weighted hourly air transmission.
No catalogue/search cap, missing path or unresolved geometry becomes zero sound.
"""
import math

import numpy as np

from .conditional_reflection_domain import SCOPE, reflection_execution_policy
from .conditional_scene_domain import execution_policy
from .conditional_road_propagation import (KERNEL_KEYS, REFERENCE_AIR, direct_transfer,
                                          integrate_source_cells, simplify_vertical)
from .physical_scene_assumptions import feature_top, materialize_profile
from .propagation_canary import BANDS
from .propagation_geometry_cache import geometry_cache
from .reflection_path import specular_plan
from .screened_reflection import screened_wall_attenuation

STATES = ('homogeneous', 'favourable')


def wall_key(plan_input):
    return tuple(sorted(tuple(plan_input[k]) for k in ('wall_start_xy', 'wall_end_xy')))


def same_wall_plane(first, second):
    a,b=first; c,d=second
    u=np.asarray(b,dtype=float)-a; v=np.asarray(d,dtype=float)-c
    lu,lv=float(np.linalg.norm(u)),float(np.linalg.norm(v))
    if min(lu,lv)<=0:raise ValueError('Positive facade length required')
    tolerance=64*math.ulp(max(1.,*(abs(x) for p in (a,b,c,d) for x in p)))
    normal=np.array([-u[1],u[0]])/lu
    return abs(float(normal@v))/lv<=64*math.ulp(1.) and abs(float(normal@(np.asarray(c)-a)))<=tolerance


def facade_inputs(ref, features, physical_case, reflection_case):
    """Attach wall hypotheses; reusable without repeating the terrain overlay."""
    reflection_execution_policy(physical_case, reflection_case)
    x=ref['wall']['unfolded_x_m'];profile=ref['profile']
    identities=ref.get('coincident_feature_ids',[ref['feature_id']])
    tops=[feature_top(features[i],physical_case)[0] for i in identities]
    reasons=[]
    if any(c['feature_id'] in identities and any(a-1e-7<=x<=b+1e-7 for a,b in c['intervals_m'])
           for c in profile['crossings']):
        reasons.append('reflector_hit_adjacent_to_own_footprint_interior')
    if any(features[i]['kind']!='building' for i in identities):reasons.append('unsupported_nonbuilding_reflector')
    if not tops or any(t is None for t in tops):reasons.append('missing_reflector_height')
    elif len(set(tops))!=1:reasons.append('coincident_reflector_top_ambiguity')
    top=tops[0] if tops else None;base=ref['wall']['base_z_m']
    if base is None or top is None or not math.isfinite(base) or not math.isfinite(top):
        reasons.append('missing_reflector_vertical_geometry')
    elif top<=base:reasons.append('reflector_top_not_above_local_terrain')
    elif top-base<reflection_case['incident_view_height_m']:
        reasons.append('physical_wall_height_below_assumed_view_height')
    pi=ref['plan_input']
    if math.dist(pi['wall_start_xy'],pi['wall_end_xy'])<reflection_case['incident_view_width_m']:
        reasons.append('physical_facade_width_below_assumed_view_width')
    wall=dict(unfolded_x_m=x,base_z_m=base,top_z_m=top,
        slope_from_vertical_deg=reflection_case['slope_from_vertical_deg'],
        absorption_by_band=list(reflection_case['absorption_by_band']),
        incident_view_height_m={s:reflection_case['incident_view_height_m'] for s in STATES},
        incident_view_width_m={s:reflection_case['incident_view_width_m'] for s in STATES},
        unfolded_geometry_confirmed=True)
    return dict(wall=wall,geometry_blockers=sorted(set(reasons)),plan_input=pi,
                physical_wall_key=wall_key(pi),reflecting_feature_ids=identities)


def materialize_reflection(ref, prepared, features, physical_case, source_policy, reflection_case):
    """Retain observed evidence and make a separate, explicitly assumed facade."""
    reflection_execution_policy(physical_case, reflection_case)
    plan_input = ref['plan_input']
    plan = specular_plan(**plan_input)
    if plan['status'] != 'conditional_research':
        raise ValueError('Prepared reflection must have a finite specular plan')
    for key, expected in (('source_xy', prepared['source_xy_bng']),
                          ('receiver_xy', prepared['receiver_xy_bng'])):
        if math.dist(plan_input[key], expected) > 1e-8:
            raise ValueError('Reflection plan/world endpoint mismatch')
    profile = ref['profile']; x = ref['wall']['unfolded_x_m']
    if (abs(profile['length_m']-plan['unfolded_horizontal_distance_m']) > .002
            or abs(x-plan['reflection_distance_from_source_m']) > .002):
        raise ValueError('Reflection unfolding/finite plan mismatch')
    if any(a is None or b is None or abs(a-b)>1e-8 for a,b in
           ((profile['kernel_scene'][key][1],prepared['direct']['kernel_scene'][key][1]) for key in ('source','receiver'))):
        raise ValueError('Reflected/direct endpoint elevations disagree')
    facade=facade_inputs(ref,features,physical_case,reflection_case)
    identities=facade['reflecting_feature_ids']
    candidate = materialize_profile(profile, features, physical_case, source_policy=source_policy,
        endpoint_ids=prepared['endpoint_footprint_ids'], reflecting_feature_ids=identities,
        reflection_x_m=x)
    reasons=candidate['geometry_blockers']+facade['geometry_blockers']
    return dict(candidate,**{k:v for k,v in facade.items() if k!='geometry_blockers'},
                geometry_blockers=sorted(set(reasons)))


def reflected_transfer(candidate, physical_case, reflection_case, *, terrain_tolerance_m=.1):
    policy = reflection_execution_policy(physical_case, reflection_case)
    common = dict(scope=SCOPE, physical_case_id=physical_case['case_id'],
                  reflection_case_id=reflection_case['case_id'])
    if candidate['geometry_blockers']:
        return dict(status='withheld', reasons=candidate['geometry_blockers'], **common)
    original = candidate['candidate_scene']
    scene = {k: original[k] for k in KERNEL_KEYS}
    scene['obstacles'] = sorted(scene['obstacles'], key=lambda o: (o['vertices'][0][0], o['vertices'][-1][0]))
    scene['conditional_input_policy'] = execution_policy(physical_case)
    scene['points'], error = simplify_vertical(scene['points'], terrain_tolerance_m)
    transfers = np.zeros((2, 8)); reasons = []; branches = []
    with geometry_cache():
        for bi, f in enumerate(BANDS):
            try:
                result = screened_wall_attenuation(scene, candidate['wall'], f, REFERENCE_AIR,
                    absorption_frequency_policy='exact_base10', conditional_reflection_policy=policy)
            except (ValueError, OverflowError, ZeroDivisionError) as exc:
                reasons.append('unsupported_reflected_cross_section:'+str(exc)); continue
            record = dict(frequency_hz=f, branches={})
            for si, state in enumerate(STATES):
                b = result[state]
                record['branches'][state] = {k: b[k] for k in ('status', 'reason', 'attenuation_db',
                    'hit_height_m', 'retrodiffraction_db', 'atmospheric_attenuation_db') if k in b}
                if b['status'] == 'absent':
                    continue
                if b['status'] == 'withheld':
                    reasons.append(b['reason']); continue
                transfer = 10**(-(b['attenuation_db']-b['atmospheric_attenuation_db'])/10)
                if not math.isfinite(transfer) or transfer <= 0:
                    reasons.append('positive_reflection_transfer_numerical_underflow')
                else:
                    transfers[si, bi] = transfer
            branches.append(record)
    common.update(branches=branches, reflected_distance_m=math.dist(scene['source'], scene['receiver']),
        candidate_kernel_scene={k: v for k, v in scene.items() if k != 'conditional_input_policy'},
        maximum_vertical_reconstruction_error_m=error)
    if reasons:
        return dict(status='withheld', reasons=sorted(set(reasons)), **common)
    return dict(status='conditional_research', relative_transfer=transfers, **common)


def combine_path_density(paths, power_at_distance):
    """Add source x path energies once, before any road-length weighting."""
    if sum(p['path_id'] == 'direct' for p in paths) != 1:
        raise ValueError('Exactly one direct path is required')
    keys = set(); identities = set(); total = np.zeros((2, 3, 8)); records = []; reasons = []
    for path in paths:
        identity = path['path_id']
        if identity in identities:
            raise ValueError('Duplicate physical path identity')
        identities.add(identity)
        if identity != 'direct':
            key = wall_key(path['plan_input'])
            if key in keys or any(same_wall_plane(key, old) for old in keys):
                raise ValueError('Duplicate reflecting facade path')
            keys.add(key)
        transfer = path['transfer']
        if transfer['status'] == 'withheld':
            reasons.extend(transfer['reasons'])
            records.append(dict(path_id=identity, status='withheld', reasons=transfer['reasons']))
            continue
        values = np.asarray(transfer['relative_transfer'], dtype=float)
        distance = transfer['direct_distance_m'] if identity == 'direct' else transfer['reflected_distance_m']
        if (values.shape != (2, 8) or not np.isfinite(values).all() or np.any(values < 0)
                or not math.isfinite(distance) or distance <= 0):
            raise ValueError('Invalid path transfer or distance')
        power = np.zeros((3, 8)) if not np.any(values) else power_at_distance(distance)
        if power is None:
            reasons.append('missing_positive_source_or_path_air_support')
            records.append(dict(path_id=identity, status='withheld', distance_m=distance))
            continue
        power = np.asarray(power, dtype=float)
        if power.shape != (3, 8) or not np.isfinite(power).all() or np.any(power < 0):
            raise ValueError('Invalid joint source/air power')
        density = values[:, None, :]*power[None, :, :]
        if not np.isfinite(density).all():
            raise ValueError('Nonfinite path contribution')
        total += density
        records.append(dict(path_id=identity, status='conditional_research', distance_m=distance,
                            relative_transfer=values.tolist(), joint_source_air_power_per_m=power.tolist(),
                            relative_energy_per_m=density.tolist()))
    return dict(status='withheld' if reasons else 'conditional_research', reasons=sorted(set(reasons)),
        relative_energy_per_m=None if reasons else total, known_selected_energy_per_m=total,
        paths=records, scope=SCOPE, complete_physical_path_support=False,
        full_road_all_paths_noise_db=None, all_inclusive_noise_db=None, field_validation=False)


def road_point_energy(prepared, features, physical_case, source_policy, reflection_case, power_at_distance):
    reflection_execution_policy(physical_case, reflection_case)
    candidate = materialize_profile(prepared['direct'], features, physical_case,
        source_policy=source_policy, endpoint_ids=prepared['endpoint_footprint_ids'])
    direct = direct_transfer(candidate, physical_case)
    paths = [dict(path_id='direct', transfer=direct)]; evidence = []
    for ref in prepared['reflections']:
        materialized = materialize_reflection(ref, prepared, features, physical_case, source_policy, reflection_case)
        result = reflected_transfer(materialized, physical_case, reflection_case)
        paths.append(dict(path_id=ref['facet_id'], plan_input=ref['plan_input'], transfer=result))
        evidence.append(dict(facet_id=ref['facet_id'], candidate=materialized, transfer=result))
    result = combine_path_density(paths, power_at_distance)
    search = prepared['reflection_search']
    if search.get('reason') == 'reflection_search_budget_exceeded':
        result.update(status='withheld', relative_energy_per_m=None,
                      reasons=sorted(set(result['reasons']+['reflection_search_budget_exceeded'])))
    return dict(result, reflection_search=search, reflected_evidence=evidence)


def integrate_selected_road_paths(source, receiver, evaluate, *, subdivisions=(1, 2)):
    """Use the existing length-conserving rule without claiming complete sound."""
    result = integrate_source_cells(source, receiver, evaluate, subdivisions=subdivisions)
    selected = result.pop('whole_road_relative_energy')
    result.update(status='conditional_selected_path_road_component' if selected is not None else 'withheld_incomplete_selected_paths',
        selected_paths_whole_line_relative_energy=selected, scope=SCOPE,
        whole_road_relative_energy=None, full_road_all_paths_noise_db=None,
        all_inclusive_noise_db=None, field_validation=False)
    return result
