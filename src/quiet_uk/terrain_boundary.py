"""Bounded homogeneous single-horizontal-edge CNOSSOS research calculation.

Annex II 2.5.15–18, 21–22, 30–32 with the 2021 Rayleigh/below-plane rules.
Ch=1; no 2012 low-edge modifier. Numeric outputs require an explicitly supplied
synthetic/reference scene. Native terrain steps do not qualify such a scene.
"""
import math
from fractions import Fraction

from quiet_uk.propagation_canary import (
    BANDS, SOUND_SPEED, corrected_ground, finite_number, ground_factor,
    ground_homogeneous, positive,
)
from quiet_uk.terrain_profile import linear_segments, mean_plane, plane_frame, split_planes, upper_path

METHOD = 'EU-Annex-II-homogeneous-single-horizontal-edge-with-2021-amendment'


def withheld(reason, **diagnostics):
    return dict(status='withheld', reason=reason, boundary_db=None,
                annual_noise_prediction=None, regulatory_implementation_qualified=False, **diagnostics)


def path_difference(source, receiver, edge):
    """Signed straight-ray excess, measured against each pair's own direct ray."""
    source, receiver, edge = [tuple(finite_number(v) for v in p) for p in (source, receiver, edge)]
    direct = math.dist(source, receiver)
    positive(direct)
    orientation = ((receiver[0]-source[0])*(edge[1]-source[1])
                   -(receiver[1]-source[1])*(edge[0]-source[0]))
    excess = max(0., math.fsum((math.dist(source, edge), math.dist(edge, receiver), -direct)))
    return dict(delta_m=excess if orientation>=0 else -excess, blocked=orientation>0,
                tangent=orientation==0, direct_distance_m=direct)


def rayleigh_selection(frequency_hz, delta_m, mirror_delta_m, blocked=False):
    f, delta, mirror = positive(frequency_hz), finite_number(delta_m), finite_number(mirror_delta_m)
    if not isinstance(blocked, bool):
        raise ValueError('Blocked flag must be explicit')
    wavelength = SOUND_SPEED/f
    first = delta > -wavelength/20
    second = delta > wavelength/4-mirror
    return dict(use_diffraction=blocked or (first and second), blocked=blocked,
        wavelength_m=wavelength, first_condition=first, rayleigh_condition=second,
        first_margin_m=delta+wavelength/20, rayleigh_margin_m=delta+mirror-wavelength/4)


def pure_horizontal_diffraction(frequency_hz, delta_m):
    """Uncapped single-edge 2.5.21, with Ch=C''=1 (Annex II 2.5.22)."""
    q = 40*finite_number(delta_m)*positive(frequency_hz)/SOUND_SPEED
    return 0. if q < -2 else max(0., 10*math.log10(3+q))


def _ground_parts(segments):
    parts = []
    previous = None
    for a, b, g in segments:
        a, b = finite_number(a), finite_number(b)
        if b<=a or (previous is not None and a!=previous):
            raise ValueError('Ground intervals must be contiguous and increasing')
        parts.append((a, b, None if g is None else ground_factor(g)))
        previous = b
    return parts


def _derived_ground_average(value, parts, start, end):
    """Preserve ordinary results; resolve a rounded boundary with exact inputs.

    Validated contiguous support and factors in [0, 1] define a convex mean.
    Subtracting interval endpoints can nevertheless round its float quotient
    above 1. Exact rational fallback avoids a tolerance or input-factor clamp.
    """
    if 0 <= value <= 1:
        return value
    left, right = Fraction(start), Fraction(end)
    weights = [max(Fraction(0), min(Fraction(b), right)-max(Fraction(a), left))
               for a, b, _ in parts]
    if sum(weights, Fraction(0)) != right-left:
        raise ValueError('Ground intervals must cover the averaging range')
    exact = sum((weight*Fraction(g) for weight, (_, _, g) in zip(weights, parts)), Fraction(0))/(right-left)
    return ground_factor(float(exact))


def ground_average(segments, start, end):
    """Horizontal path-length weighting; unknown support withholds the average."""
    if not segments:
        return None
    parts = _ground_parts(segments)
    start, end = finite_number(start), finite_number(end)
    length = positive(end-start)
    if parts[0][0]!=start or parts[-1][1]!=end:
        raise ValueError('Ground intervals must cover the entire supplied profile')
    if any(g is None for _, _, g in parts):
        return None
    value = math.fsum((b-a)*g for a, b, g in parts)/length
    return _derived_ground_average(value, parts, start, end)


def side_ground(segments, start, end):
    """Average a fully supported side range without relaxing supplied factors."""
    if not segments:
        return None
    parts = _ground_parts(segments)
    start, end = finite_number(start), finite_number(end)
    length = positive(end-start)
    if start < parts[0][0] or end > parts[-1][1]:
        raise ValueError('Ground intervals must cover the averaging range')
    if any(g is None for _, _, g in parts):
        return None
    value = math.fsum(max(0., min(b, end)-max(a, start))*g for a, b, g in parts)/length
    return _derived_ground_average(value, parts, start, end)


def terrain_corners(points):
    """Convex-down vertices; redundant collinear sampling is not a new edge."""
    corners = []
    for before, point, after in zip(points, points[1:], points[2:]):
        left = (point[1]-before[1])/(point[0]-before[0])
        right = (after[1]-point[1])/(after[0]-point[0])
        tolerance = 64*math.ulp(max(1., abs(left), abs(right)))
        if left-right>tolerance:
            corners.append(point)
    return corners


def single_edge_boundary(points, source, receiver, frequency_hz, ground_segments, source_g,
                         *, edge=None, conditions='homogeneous', scene_kind='unqualified',
                         edge_width_m=None, opaque=None, infinite_lateral_extent=None):
    """Boundary attenuation only, under fully stated synthetic/reference assumptions.

    Terrain describes ground, excluding a raised screen's top. A supplied edge
    may be a ridge corner or raised ideal screen. Two fitted side planes define
    separate mirror points. Multiple controlling edges, unsupported widths,
    weather, transmission and real-area qualification remain withheld.
    """
    segments = linear_segments(points)
    if conditions!='homogeneous':
        return withheld('unsupported_propagation_conditions')
    if scene_kind not in ('synthetic', 'published_reference'):
        return withheld('real_area_scene_not_qualified')
    plane = mean_plane(segments)
    if plane['status']=='withheld':
        return withheld('unknown_terrain_support')
    if source is None or receiver is None or source[1] is None or receiver[1] is None:
        return withheld('unknown_endpoint_elevation')
    upper_path(points, source, receiver)  # Also validates physical endpoint elevations/order.
    source, receiver = [list(finite_number(v) for v in p) for p in (source, receiver)]
    f = positive(frequency_hz)
    if f not in BANDS:
        return withheld('unsupported_nominal_frequency')
    g = ground_average(ground_segments, points[0][0], points[-1][0])
    if g is None or source_g is None:
        return withheld('unknown_acoustic_ground')
    source_g = ground_factor(source_g)
    common = plane_frame(plane, source, receiver)

    def ground_only(branch, **geometry):
        value = ground_homogeneous(f, common['projected_distance_m'],
            common['source']['equivalent_height_m'], common['receiver']['equivalent_height_m'], g, source_g)
        return dict(status='conditional_research', branch=branch, boundary_db=value, common_plane=plane,
            common_frame=common, g_path=g, source_g=source_g, separate_ground_db=None,
            annual_noise_prediction=None, regulatory_implementation_qualified=False, method=METHOD, **geometry)

    if edge is None:
        if plane['rms_residual_m']>1e-10:
            return withheld('nonplanar_terrain_without_declared_controlling_edge')
        return ground_only('ground_only_planar')
    if edge[1] is None:
        return withheld('unknown_edge_elevation')
    if edge_width_m is None or opaque is None or infinite_lateral_extent is None:
        return withheld('unknown_edge_properties')
    if not isinstance(opaque, bool) or not isinstance(infinite_lateral_extent, bool):
        raise ValueError('Edge properties must be explicit booleans')
    if finite_number(edge_width_m)!=0 or not opaque or not infinite_lateral_extent:
        return withheld('unsupported_edge_width_transmission_or_lateral_extent')
    edge = list(finite_number(v) for v in edge)
    x, z = edge
    if not points[0][0]<x<points[-1][0]:
        raise ValueError('Edge must be strictly inside the profile')
    sides = split_planes(points, x, source, receiver)
    if z<sides['edge'][1]:
        raise ValueError('Supplied edge cannot be beneath local terrain')
    # Retain terrain fits while using the actual supplied edge height in frames.
    source_frame = plane_frame(sides['source_plane'], source, edge)
    receiver_frame = plane_frame(sides['receiver_plane'], edge, receiver)
    with_edge = [p for p in points if p[0]!=x]+[edge]
    with_edge.sort(key=lambda p: p[0])
    path = upper_path(with_edge, source, receiver)
    if path['edge_count']>1 or (path['edge_count']==1 and path['path'][1]!=edge):
        return withheld('multiple_or_other_controlling_edges', upper_path=path)
    main_path = path_difference(source, receiver, edge)
    # For a clear ray the prescribed edge must be the largest signed excess.
    other_corners = [p for p in terrain_corners(points) if p[0]!=x]
    if not main_path['blocked'] and any(path_difference(source, receiver, p)['delta_m']>main_path['delta_m']+1e-10 for p in other_corners):
        return withheld('another_edge_requires_selection')
    source_mirror = source_frame['source']['mirror_point']
    receiver_mirror = receiver_frame['receiver']['mirror_point']
    if not source_mirror[0]<x<receiver_mirror[0]:
        return withheld('unsupported_mirror_point_order')
    mirror_path = path_difference(source_mirror, receiver_mirror, edge)
    selection = rayleigh_selection(f, main_path['delta_m'], mirror_path['delta_m'], main_path['blocked'])
    geometry = dict(edge=edge, delta_m=main_path['delta_m'], mirror_delta_m=mirror_path['delta_m'],
        selection=selection, source_plane=sides['source_plane'], receiver_plane=sides['receiver_plane'],
        source_frame=source_frame, receiver_frame=receiver_frame, source_mirror=source_mirror,
        receiver_mirror=receiver_mirror, upper_path=path)
    if not selection['use_diffraction']:
        return ground_only('ground_only_clear_edge', **geometry)
    gs = side_ground(ground_segments, points[0][0], x)
    gr = side_ground(ground_segments, x, points[-1][0])
    source_ground = ground_homogeneous(f, source_frame['projected_distance_m'],
        source_frame['source']['equivalent_height_m'], source_frame['receiver']['equivalent_height_m'], gs, source_g)
    receiver_ground = ground_homogeneous(f, receiver_frame['projected_distance_m'],
        receiver_frame['source']['equivalent_height_m'], receiver_frame['receiver']['equivalent_height_m'], gr, source_g, receiver_side=True)
    main = pure_horizontal_diffraction(f, main_path['delta_m'])
    ds = path_difference(source_mirror, receiver, edge)['delta_m']
    dr = path_difference(source, receiver_mirror, edge)['delta_m']
    source_below = source_frame['source']['negative_equivalent_height_clipped']
    receiver_below = receiver_frame['receiver']['negative_equivalent_height_clipped']
    source_image = main if source_below else pure_horizontal_diffraction(f, ds)
    receiver_image = main if receiver_below else pure_horizontal_diffraction(f, dr)

    def coupling(ground, image, below):
        if below:
            return ground
        argument = 1+math.expm1(-ground*math.log(10)/20)*math.exp(-(image-main)*math.log(10)/20)
        return None if argument<=0 else -20*math.log10(argument)

    source_coupling = coupling(source_ground, source_image, source_below)
    receiver_coupling = coupling(receiver_ground, receiver_image, receiver_below)
    if source_coupling is None or receiver_coupling is None:
        return withheld('nonpositive_ground_diffraction_coupling', **geometry)
    return dict(status='conditional_research', branch='diffraction_including_ground', method=METHOD, **geometry,
        common_plane=plane, common_frame=common, g_path=g, source_g=source_g, source_side_g=gs, receiver_side_g=gr,
        source_side_corrected_g=corrected_ground(gs, source_g, source_frame['projected_distance_m'],
            source_frame['source']['equivalent_height_m'], source_frame['receiver']['equivalent_height_m']),
        main_diffraction_uncapped_db=main, main_diffraction_db=min(main, 25.), source_image_diffraction_db=source_image,
        receiver_image_diffraction_db=receiver_image, source_image_delta_m=ds, receiver_image_delta_m=dr,
        source_ground_db=source_ground, receiver_ground_db=receiver_ground,
        source_ground_coupling_db=source_coupling, receiver_ground_coupling_db=receiver_coupling,
        source_below_mean_plane_rule=source_below, receiver_below_mean_plane_rule=receiver_below,
        separate_ground_db=0., boundary_db=min(main, 25.)+source_coupling+receiver_coupling,
        annual_noise_prediction=None, regulatory_implementation_qualified=False)
