"""Explicit physical-input scenarios over retained real geographic evidence.

These are conditional input candidates, not synthetic scenes, qualified acoustic
obstacles, noise estimates, confidence intervals or guaranteed noise bounds.
The existing propagation kernels deliberately reject their scene kind.
"""
from bisect import bisect_left, bisect_right
import math

import numpy as np

from .ground_cover import CLASSES
from .road_emission import model_category, vehicle_power
from .road_input_audit import VEHICLE_FIELDS
from .road_temporal import number

POLICY_ID = 'physical-scene-assumptions-2026-10-11-v1'


def assumption_cases():
    baseline = dict(ground_mode='conditional_cover', ambiguous_ground_g=.5,
                    roof_statistic='p50', missing_building_height_m=None,
                    barrier_mode='omit_unverified', barrier_reference_height_m=None,
                    source_deck_offset_m=0., source_g=0., roof_g=0.)
    variants = [('baseline', {})]
    variants += [(f'ground_uniform_{str(g).replace(".", "_")}',
                  dict(ground_mode='uniform_recognised', ambiguous_ground_g=g))
                 for g in (0., .3, .7, 1.)]
    variants += [(f'roof_{p}', dict(roof_statistic=p)) for p in ('p10', 'p90')]
    variants += [(f'missing_buildings_{h}m', dict(missing_building_height_m=float(h)))
                 for h in (3, 6, 12)]
    variants += [(f'walls_{h}m', dict(barrier_mode='solid_walls', barrier_reference_height_m=float(h)))
                 for h in (1, 3, 5)]
    variants += [('solid_fences_and_walls_3m', dict(barrier_mode='solid_fences_and_walls',
                                                 barrier_reference_height_m=3.)),
                 ('combined_reference', dict(missing_building_height_m=6.,
                                             barrier_mode='solid_walls', barrier_reference_height_m=3.))]
    return [dict(baseline, **changes, case_id=name) for name, changes in variants]


def absolute_surface_summary(terrain, surface, terrain_mask, surface_mask,
                             dtm_codes, dsm_codes, interior, footprint):
    """Summarise original paired DSM ordinates without changing prior acceptance.

    Native absolute p10/p50/p90 are sample spreads, not acoustic roof/eaves
    measurements or confidence limits. A footprint-wide terrain reference makes
    hypothetical missing-height prisms independent of receiver/path selection.
    """
    arrays = (terrain, surface, terrain_mask, surface_mask, dtm_codes, dsm_codes, interior, footprint)
    if any(a.shape != terrain.shape for a in arrays):
        raise ValueError('Native array dimensions differ')
    terrain_valid = np.isfinite(terrain) & (terrain_mask != 0)
    surface_valid = np.isfinite(surface) & (surface_mask != 0)
    paired = (interior & terrain_valid & surface_valid & (dtm_codes == dsm_codes)
              & (dtm_codes > 0) & (dtm_codes != 65535))
    values = surface[paired].astype(float)
    base = terrain[footprint & terrain_valid].astype(float)
    return dict(paired_pixels=int(paired.sum()),
                surface_odn_m={p: float(v) for p, v in zip(('p10', 'p50', 'p90'), np.percentile(values, [10, 50, 90]))} if len(values) else None,
                difference_agl_m={p: float(v) for p, v in zip(('p10', 'p50', 'p90'), np.percentile(surface[paired].astype(float)-terrain[paired].astype(float), [10, 50, 90]))} if len(values) else None,
                footprint_native_terrain_pixels=len(base),
                reference_base_odn_m=float(base.max()) if len(base) else None)


def ground_candidates(samples, length_m, case):
    """Keep full native sampling support; missing classes stay unknown in all cases."""
    if number(length_m) is None or length_m <= 0 or not samples:
        raise ValueError('Positive full-length ground support required')
    runs, lengths, cursor = [], {}, 0.
    for a, b, row, col, code, state in samples:
        if not math.isclose(a, cursor, abs_tol=1e-7) or not a < b <= length_m+1e-7:
            raise ValueError('Ground intervals have missing, overlapping or invalid support')
        known = state == 'reported_class' and code in CLASSES
        if not known: g, basis = None, 'missing_or_unsupported_native_class'
        elif case['ground_mode'] == 'uniform_recognised':
            g, basis = case['ambiguous_ground_g'], 'uniform_ground_stress_case'
        elif case['ground_mode'] != 'conditional_cover':
            raise ValueError('Unknown ground policy')
        elif code in (30, 40): g, basis = 1., 'assumed_porous_grass_or_crop_substrate'
        elif code in (50, 80): g, basis = 0., 'assumed_sealed_built_ground_or_water'
        else: g, basis = case['ambiguous_ground_g'], 'unobserved_substrate_reference'
        if g is not None and (number(g) is None or not 0 <= g <= 1):
            raise ValueError('Invalid candidate ground factor')
        lengths[basis] = lengths.get(basis, 0.)+b-a
        if runs and runs[-1][2] == g: runs[-1][1] = b
        else: runs.append([a, b, g])
        cursor = b
    if not math.isclose(cursor, length_m, abs_tol=1e-7):
        raise ValueError('Ground intervals omit path length')
    missing = lengths.get('missing_or_unsupported_native_class', 0.)
    return dict(segments=runs, support_length_m=length_m, basis_lengths_m=lengths,
                unknown_length_m=missing, observed_g=None,
                full_length_candidate_mean_g=None if missing else math.fsum((b-a)*g for a,b,g in runs)/length_m)


def feature_top(feature, case):
    if feature['kind'] == 'building':
        if feature['original_height_status'] == 'provisional_surface_height':
            summary = feature['native_summary']['surface_odn_m']
            return (summary[case['roof_statistic']], 'paired_native_dsm_flat_top_assumption') if summary else (None, 'missing_paired_surface')
        height = case['missing_building_height_m']
        base = feature['native_summary']['reference_base_odn_m']
        return (base+height, 'hypothetical_missing_building_prism') if height is not None and base is not None else (None, 'unresolved_building_height')
    mode, category = case['barrier_mode'], feature['category']
    included = category == 'generic_wall_candidate' and mode in ('solid_walls', 'solid_fences_and_walls')
    included |= category == 'generic_fence_candidate' and mode == 'solid_fences_and_walls'
    if mode not in ('omit_unverified', 'solid_walls', 'solid_fences_and_walls'):
        raise ValueError('Unknown barrier policy')
    if not included:
        return None, 'explicitly_omitted_unverified_screen_effect'
    height, base = feature['tagged_height_m'], feature['reference_base_odn_m']
    if height is None: height = case['barrier_reference_height_m']
    if base is None or number(height) is None or height <= 0:
        return None, 'missing_screen_reference_geometry'
    return base+height, 'unverified_tag_height_assumed_solid' if feature['tagged_height_m'] is not None else 'hypothetical_solid_screen_reference'


def materialize_profile(profile, features, case, *, source_policy, endpoint_ids=None,
                        reflecting_feature_ids=(), reflection_x_m=None):
    """Return a separate candidate scene; never edit observed or qualified inputs."""
    # Native point ordinates are read-only shared evidence. Only candidate fields
    # are copied/replaced; copying the full native path fifteen times is costly.
    scene = dict(profile['kernel_scene'])
    scene['source'] = list(scene['source']); scene['receiver'] = list(scene['receiver'])
    scene.update(scene_kind='conditional_real_input', assumption_policy_id=POLICY_ID,
                 assumption_case_id=case['case_id'], opaque=True, infinite_lateral_extent=True,
                 source_g=case['source_g'], obstacles=[])
    ground = ground_candidates(profile['ground_native_samples'], profile['length_m'], case)
    scene['ground_segments'] = ground['segments']
    blockers = []
    if ground['unknown_length_m']: blockers.append('missing_native_ground_support')
    if source_policy['status'] != 'conditional_at_grade_reference':
        blockers.append('source_surface_or_deck_unresolved')
        scene['source'][1] = None
    elif scene['source'][1] is not None:
        scene['source'][1] += case['source_deck_offset_m']
    if scene['source'][1] is None or scene['receiver'][1] is None:
        blockers.append('missing_endpoint_elevation')
    endpoint_ids = endpoint_ids or {}
    if any(endpoint_ids.get(k) for k in ('source', 'receiver')):
        blockers.append('endpoint_intersects_generalised_building')
    points = scene['points']; xs = [p[0] for p in points]
    complete_terrain = all(p[1] is not None for p in points)
    ys = [p[1] for p in points] if complete_terrain else None
    xs_array = np.asarray(xs); ys_array = np.asarray(ys) if complete_terrain else None
    if not complete_terrain: blockers.append('missing_native_terrain')
    tops, omissions, unresolved, roof_extents = [], [], [], []
    for c in profile['crossings']:
        identity = c['feature_id']
        f = features[identity]; top, basis = feature_top(f, case)
        if c['kind'] != f['kind']: raise ValueError('Crossing identity/type mismatch')
        if basis == 'explicitly_omitted_unverified_screen_effect':
            omissions.append(identity); continue
        intervals = c['intervals_m']
        contacts = c['contacts_m']
        # The terminal hit on the reflecting facade belongs to the wall model.
        contacts = [x for x in contacts if not (identity in reflecting_feature_ids and
                    (math.isclose(x, 0., abs_tol=1e-7) or math.isclose(x, profile['length_m'], abs_tol=1e-7)
                     or (reflection_x_m is not None and math.isclose(x, reflection_x_m, abs_tol=1e-7))))]
        if not intervals and not contacts: continue
        if top is None:
            unresolved.append(identity); continue
        if f['kind'] == 'barrier' and intervals:
            blockers.append('collinear_barrier_extent_unresolved')
        for a, b in intervals:
            if not 0 < a < b < profile['length_m']:
                blockers.append('obstacle_overlaps_endpoint')
            vertices = [[a, top], [b, top]]
            scene['obstacles'].append(dict(vertices=vertices, roof_g=case['roof_g'], feature_id=identity))
            roof_extents.append((a,b,identity))
            inner = points[bisect_left(xs, a):bisect_right(xs, b)]
            if complete_terrain:
                zs = [p[1] for p in inner]+[float(np.interp(a, xs_array, ys_array)), float(np.interp(b, xs_array, ys_array))]
                if zs and top < max(zs)-1e-7: blockers.append('candidate_top_below_reconstructed_terrain')
            tops.extend((x, top, identity, basis) for x in (a,b))
        for x in contacts:
            if not 0 < x < profile['length_m']:
                blockers.append('obstacle_touches_endpoint')
            scene['obstacles'].append(dict(vertices=[[x,top]], feature_id=identity))
            if complete_terrain and top < np.interp(x, xs_array, ys_array)-1e-7:
                blockers.append('candidate_top_below_reconstructed_terrain')
            tops.append((x,top,identity,basis))
    if unresolved: blockers.append('missing_building_or_screen_height')
    extents = sorted(roof_extents)
    maximum_end = -math.inf
    for a,b,_ in extents:
        if a < maximum_end-1e-7: blockers.append('overlapping_obstacle_roofs')
        maximum_end = max(maximum_end,b)
    source, receiver = scene['source'], scene['receiver']
    def clearance(x,z):
        return z-(source[1]+(receiver[1]-source[1])*x/profile['length_m'])
    if source[1] is not None and receiver[1] is not None:
        building_hits = [(identity,clearance(x,z)) for x,z,identity,_ in tops if clearance(x,z) > 0]
        terrain_clearances = [clearance(x,z) for x,z in points if z is not None]
        obstacle_max = max((clearance(x,z) for x,z,_,_ in tops), default=None)
    else: building_hits, terrain_clearances, obstacle_max = [], [], None
    return dict(status='withheld_geometry_candidate' if blockers else 'conditional_geometry_candidate',
                candidate_scene=scene, candidate_ground=ground,
                geometry_blockers=sorted(set(blockers)), unresolved_feature_ids=sorted(set(unresolved)),
                omitted_screen_feature_ids=sorted(set(omissions)),
                geometry_diagnostic=dict(candidate_obstacles=len(scene['obstacles']),
                    obstacles_above_endpoint_sightline=len(set(i for i,_ in building_hits)),
                    maximum_obstacle_clearance_m=obstacle_max,
                    maximum_terrain_clearance_m=max(terrain_clearances, default=None)),
                interpretation='Endpoint sightline geometry only; no diffraction, attenuation, uncertainty bound or receiver noise',
                qualified_ground_g=None, qualified_obstacle_top_odn_m=None, receiver_noise_db=None)


def pavement_spectrum(road, coefficients, surface):
    """Apply hypothetical F-4 rows at the existing per-class speeds and flows.

    Unsupported combinations stay held. Flat, steady, dry, 20 C reference;
    source air-temperature, slope and junction corrections remain unapplied.
    """
    baseline = road['conditional_baseline']
    periods = baseline['period_traffic']
    if baseline['period_spectra'] is None:
        return dict(status='withheld', surface=surface, reason='missing_operating_inputs', period_spectra=None)
    power = {p: np.zeros(len(coefficients['frequencies_hz'])) for p in ('day','evening','night')}
    for field in VEHICLE_FIELDS:
        count = number(road['annual_categories'][field])
        if count is None or count < 0:
            return dict(status='withheld', surface=surface, reason='missing_or_negative_original_category', period_spectra=None)
        if count == 0: continue
        speed = road['speed']['choices'][field]['candidate_kmh']
        category = model_category(field, dict(bus_category='2', two_wheeler_category='4b'))
        try: single = vehicle_power(coefficients, category, speed, surface)
        except ValueError:
            return dict(status='withheld', surface=surface, reason='published_surface_speed_domain_unsupported', original_vehicle_field=field, candidate_speed_kmh=speed, period_spectra=None)
        for period in power:
            power[period] += single*periods[field][period]['assumed_average_vehicles_per_hour']/(1000*speed)
    power['den_penalised_source'] = (12*power['day']+4*10**.5*power['evening']+8*10*power['night'])/24
    spectra = {p: dict(relative_power_per_m=v.tolist(), sound_power_db_re_1e_minus_12_W_per_m=[10*math.log10(x) if x > 0 else None for x in v]) for p,v in power.items()}
    return dict(status='conditional_research_source_emission', surface=surface, period_spectra=spectra,
                actual_pavement=None, source_temperature_c=20., actual_source_temperature_c=None,
                applied_gradient_and_junction_correction_db=0., observed_gradient_and_junction_correction_db=None,
                receiver_noise_db=None)
