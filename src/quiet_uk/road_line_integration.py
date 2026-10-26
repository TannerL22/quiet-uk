"""Bounded numerical integration of incoherent road power per metre.

Composite midpoint quadrature and refinement are numerical choices, not an
Annex-prescribed segmentation rule. Supplied path/emission discontinuities
must be breakpoints. Convergence is a numerical diagnostic, not field accuracy.
This adapter consumes qualified synthetic/reference profiles; it is not a GIS
path finder and does not release real-area or annual noise predictions.
"""
import math

from quiet_uk.propagation_canary import BANDS, finite_number, positive
from quiet_uk.reflection_path import vector, held, absent, combine_paths_attenuation, mix_conditions, specular_plan
from quiet_uk.favourable_propagation import direct_attenuation
from quiet_uk.screened_reflection import screened_wall_attenuation

METHOD = 'incoherent-road-line-composite-midpoint-research'
STATES = ('homogeneous', 'favourable', 'mixed')


def point_path_transfer(source, receiver, frequency_hz, *, direct_scene,
                        reflections, reflection_support_complete, air,
                        probability_favourable=None, absorption_frequency_policy=None):
    """Sum a declared complete direct/reflected path set before weather mixing.

    World coordinates are local metres. Reflected profiles must be unfolded
    from the supplied world endpoints and one finite planar wall. Apparent wall
    dimensions, ground and obstacles remain explicit path input qualifications.
    """
    source, receiver = vector(source, 3), vector(receiver, 3)
    unknown = held('incomplete_reflection_path_support')
    if reflection_support_complete is not True or reflections is None:
        return dict(homogeneous=unknown, favourable=unknown, probability_favourable=probability_favourable)
    if direct_scene is None or any(direct_scene.get(k) is None or any(v is None for v in direct_scene[k]) for k in ('source','receiver')):
        unknown=held('incomplete_direct_path')
        return dict(homogeneous=unknown,favourable=unknown,probability_favourable=probability_favourable)
    distance = math.dist(source[:2], receiver[:2])
    if (not math.isclose(direct_scene['source'][0], 0., abs_tol=1e-9)
            or not math.isclose(direct_scene['receiver'][0], distance, abs_tol=1e-8)
            or not math.isclose(direct_scene['source'][1], source[2], abs_tol=1e-8)
            or not math.isclose(direct_scene['receiver'][1], receiver[2], abs_tol=1e-8)):
        raise ValueError('Direct profile does not match the world endpoints')
    direct = direct_attenuation(direct_scene, frequency_hz, air,
        probability_favourable=probability_favourable,
        absorption_frequency_policy=absorption_frequency_policy)
    paths = {c: [dict(status='conditional_research', attenuation_db=direct[c+'_attenuation_db'])
        if direct.get(c+'_attenuation_db') is not None else held('incomplete_direct_path')]
        for c in STATES[:2]}
    wall_keys = set()
    for ref in reflections:
        plan_input = ref.get('plan_input')
        if plan_input is None:
            for c in paths: paths[c].append(held('unknown_reflection_plan'))
            continue
        plan = specular_plan(**plan_input)
        if plan['status'] == 'withheld':
            for c in paths: paths[c].append(plan)
            continue
        if any(math.dist(plan_input[k], p[:2]) > 1e-8 for k,p in
               (('source_xy', source), ('receiver_xy', receiver))):
            raise ValueError('Reflection plan does not match the world endpoints')
        wall_key = tuple(sorted(tuple(plan_input[k]) for k in ('wall_start_xy','wall_end_xy')))
        if wall_key in wall_keys: raise ValueError('Duplicate reflecting wall path')
        wall_keys.add(wall_key)
        if plan['status'] != 'conditional_research':
            for c in paths: paths[c].append(plan)
            continue
        scene, wall = ref.get('scene'), ref.get('wall')
        if (scene is None or wall is None or wall.get('unfolded_x_m') is None
                or any(scene.get(k) is None or any(v is None for v in scene[k]) for k in ('source','receiver'))):
            for c in paths: paths[c].append(held('unknown_unfolded_reflection_geometry'))
            continue
        if (abs(scene['source'][0]) > 1e-8
                or abs(scene['receiver'][0]-plan['unfolded_horizontal_distance_m']) > .002
                or abs(wall['unfolded_x_m']-plan['reflection_distance_from_source_m']) > .002
                or abs(scene['source'][1]-source[2]) > 1e-8
                or abs(scene['receiver'][1]-receiver[2]) > 1e-8):
            raise ValueError('Unfolded reflection profile does not match its finite-wall plan')
        reflected = screened_wall_attenuation(scene, wall, frequency_hz, air,
            probability_favourable=probability_favourable,
            absorption_frequency_policy=absorption_frequency_policy)
        for c in paths: paths[c].append(reflected[c])
    combined = {c: combine_paths_attenuation(paths[c]) for c in paths}
    return dict(**combined, probability_favourable=probability_favourable,
                meteorological_mix=mix_conditions(combined['homogeneous'], combined['favourable'], probability_favourable))


def _point(a, b, t):
    return [x+(y-x)*t for x,y in zip(a,b)]


def _nearest(a, b, r):
    d = [y-x for x,y in zip(a,b)]
    t = max(0., min(1., math.fsum((q-x)*v for q,x,v in zip(r,a,d))/math.fsum(v*v for v in d)))
    return math.dist(_point(a,b,t), r)


def _overlap(a, b, c, d):
    """Exclude duplicate/overlapping collinear physical source intervals."""
    v = [y-x for x,y in zip(a,b)]; size = math.dist(a,b)
    ts = [math.fsum((y-x)*u for y,x,u in zip(p,a,v))/(size*size) for p in (c,d)]
    tolerance = 64*math.ulp(max(1., *(abs(x) for p in (a,b,c,d) for x in p)))
    return (all(math.dist(p, _point(a,b,t)) <= tolerance for p,t in zip((c,d),ts))
            and (min(1.,max(ts))-max(0.,min(ts)))*size > tolerance)


def integrate_road_line(segments, receiver, frequency_hz, transfer, *,
                        scene_kind=None, metre_coordinates_confirmed=None,
                        path_support_complete=None, tolerance_db=.005,
                        distance_ratio=.5, max_refinements=12, max_evaluations=20000):
    """Integrate one octave, multiplying source power per metre by cell length.

    Each segment declares segment_id, start/end XYZ, length_basis ('horizontal'
    or '3d'), eight relative_power_per_m bands and breakpoints (fractions 0..1).
    The transfer callback returns H/F path attenuation and a supplied favourable
    probability at each source point. Unknown positive contributions propagate.
    The receiver must be off the finite source line. Zero power is known zero.
    """
    f = positive(frequency_hz); receiver = vector(receiver,3)
    tolerance_db, distance_ratio = positive(tolerance_db), positive(distance_ratio)
    if distance_ratio > 1: raise ValueError('Distance ratio must not exceed one')
    if any(isinstance(v,bool) or not isinstance(v,int) or v < 1 for v in (max_refinements,max_evaluations)):
        raise ValueError('Resource budgets must be positive integers')
    common = dict(method=METHOD, frequency_hz=f, annual_noise_prediction=None,
        regulatory_implementation_qualified=False, refinement_tolerance_db=tolerance_db,
        numerical_error_is_field_uncertainty=False)
    def withheld(reason, **details):
        return dict(status='withheld', reason=reason, receiver_level_db=None, **common, **details)
    if f not in BANDS: return withheld('unsupported_nominal_frequency')
    if scene_kind not in ('synthetic','published_reference'): return withheld('real_area_scene_not_qualified')
    if metre_coordinates_confirmed is not True: return withheld('unknown_coordinate_units')
    if path_support_complete is not True: return withheld('incomplete_path_or_breakpoint_support')
    if segments is None: return withheld('unknown_source_segments')
    parsed = []; ids = set()
    for record in segments:
        if record.get('segment_id') is None: return withheld('unknown_segment_identity')
        if record['segment_id'] in ids: raise ValueError('Duplicate source segment identity')
        ids.add(record['segment_id'])
        if any(record.get(k) is None or any(v is None for v in record[k]) for k in ('start','end')):
            return withheld('unknown_source_geometry')
        a,b = vector(record['start'],3),vector(record['end'],3)
        if math.dist(a,b)==0: raise ValueError('Zero-length source segment')
        basis = record.get('length_basis')
        if basis not in ('horizontal','3d'): return withheld('unknown_source_length_basis')
        length = math.dist(a[:2],b[:2]) if basis=='horizontal' else math.dist(a,b)
        if length==0: raise ValueError('Zero source length in declared basis')
        powers = record.get('relative_power_per_m')
        if powers is None: return withheld('unknown_source_power')
        if len(powers)!=8: raise ValueError('Eight explicit emission bands required')
        for power in powers:
            if power is not None and finite_number(power)<0: raise ValueError('Source power must be nonnegative')
        power = powers[BANDS.index(f)]
        if power is None: return withheld('unknown_source_power_band')
        breaks = record.get('breakpoints')
        if breaks is None: return withheld('unknown_path_or_emission_breakpoints')
        breaks = [0., *[finite_number(t) for t in breaks], 1.]
        if any(not 0<=t<=1 for t in breaks): raise ValueError('Breakpoints must lie in [0,1]')
        breaks = sorted(set(breaks))
        for previous in parsed:
            if _overlap(a,b,previous['start'],previous['end']): raise ValueError('Overlapping physical road source intervals')
        parsed.append(dict(start=a,end=b,length=length,power=power,breakpoints=breaks))
    source_power = math.fsum(p['power']*p['length'] for p in parsed)
    if not math.isfinite(source_power): raise ValueError('Nonfinite integrated source power')
    base_cells = []
    for seg in parsed:
        if seg['power']==0: continue
        for left,right in zip(seg['breakpoints'],seg['breakpoints'][1:]):
            nearest = _nearest(_point(seg['start'],seg['end'],left), _point(seg['start'],seg['end'],right),receiver)
            if nearest==0: return withheld('receiver_on_source_line')
            n = max(1, math.ceil((right-left)*seg['length']/(distance_ratio*nearest)))
            if n > max_evaluations: return withheld('quadrature_budget_exceeded',evaluations=0)
            base_cells.extend((seg,left+(right-left)*i/n,left+(right-left)*(i+1)/n) for i in range(n))
    history = []; evaluations = 0; previous = None; stable = 0
    for refinement in range(max_refinements+1):
        pieces = 2**refinement; required = len(base_cells)*pieces
        if evaluations+required > max_evaluations:
            return withheld('quadrature_budget_exceeded',evaluations=evaluations,history=history,source_relative_power=source_power)
        values = {c: [] for c in STATES}; unknown = {c: False for c in STATES}
        for seg,left,right in base_cells:
            for j in range(pieces):
                t = left+(right-left)*(j+.5)/pieces
                result = transfer(_point(seg['start'],seg['end'],t),receiver,f); evaluations += 1
                weight = seg['power']*seg['length']*(right-left)/pieces
                energy = {}
                for c in STATES[:2]:
                    branch = result[c]
                    if branch['status']=='absent': energy[c]=0.
                    elif branch['status']=='withheld': energy[c]=None
                    elif branch['status']=='conditional_research':
                        attenuation = finite_number(branch['attenuation_db'])
                        energy[c]=10**(-attenuation/10)
                        if not math.isfinite(energy[c]): raise ValueError('Nonfinite transfer energy')
                    else: raise ValueError('Unsupported transfer state')
                    if energy[c] is None: unknown[c]=True
                    else: values[c].append(weight*energy[c])
                p = result.get('probability_favourable')
                if p is None: unknown['mixed']=True
                else:
                    p = finite_number(p)
                    if not 0<=p<=1: raise ValueError('Favourable probability must lie in [0,1]')
                    terms = [(1-p,energy['homogeneous']), (p,energy['favourable'])]
                    if any(w>0 and e is None for w,e in terms): unknown['mixed']=True
                    else: values['mixed'].append(weight*math.fsum(w*e for w,e in terms if w>0))
        totals = {c: None if unknown[c] else math.fsum(values[c]) for c in STATES}
        changes = {c: None if previous is None or totals[c] is None or previous[c] is None
            else (0. if totals[c]==previous[c]==0 else
                  abs(10*math.log10(totals[c]/previous[c])) if min(totals[c],previous[c])>0 else math.inf) for c in STATES}
        history.append(dict(refinement=refinement,source_points=required,relative_energy=totals,
                            change_db={c: v if v is None or math.isfinite(v) else None for c,v in changes.items()}))
        known = [c for c in STATES if totals[c] is not None]
        if not known:
            return withheld('incomplete_positive_transfer_contribution',evaluations=evaluations,history=history,source_relative_power=source_power)
        settled = previous is not None and bool(known) and all(changes[c] is not None and changes[c]<=tolerance_db for c in known)
        stable = stable+1 if settled else 0
        if refinement>=3 and stable>=2:
            branches = {c: dict(status='withheld' if totals[c] is None else ('absent' if totals[c]==0 else 'conditional_research'),
                relative_energy=totals[c],receiver_level_db=None if totals[c] is None or totals[c]==0 else 10*math.log10(totals[c])) for c in STATES}
            return dict(status=branches['mixed']['status'], receiver_level_db=branches['mixed']['receiver_level_db'],
                reason='incomplete_positive_transfer_or_weather' if totals['mixed'] is None else None,
                branches=branches,source_relative_power=source_power,evaluations=evaluations,history=history,
                converged=True,**common)
        previous = totals
    return withheld('quadrature_not_converged',evaluations=evaluations,history=history,source_relative_power=source_power)
