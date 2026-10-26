"""Bounded homogeneous horizontal diffraction over opaque cross sections.

Own derivation of Annex II 2.5.21/23/30-32, including 2021/1226.
Ground is separate from obstacle roofs. Only blocked paths with unchanged
controlling image chains are qualified here; no lateral or annual levels.
"""
import math
from quiet_uk.propagation_geometry_cache import cached_geometry

from quiet_uk.propagation_canary import (
    BANDS, SOUND_SPEED, corrected_ground, finite_number, ground_factor,
    ground_homogeneous, positive,
)
from quiet_uk.terrain_boundary import ground_average, side_ground, single_edge_boundary, withheld
from quiet_uk.terrain_profile import linear_segments, mean_plane, plane_frame, upper_path

METHOD = 'EU-Annex-II-2021-homogeneous-blocked-multiple-horizontal-edges'
MAX_OBSTACLES = 256
MAX_VERTICES = 1024


def multiple_coefficient(frequency_hz, inter_edge_path_m):
    """2021 amended 2.5.23; e is a path length, not horizontal width."""
    f, e = positive(frequency_hz), finite_number(inter_edge_path_m)
    if e < 0:
        raise ValueError('Inter-edge length cannot be negative')
    if e <= .3:
        return 1.
    # Equivalent scaled form avoids squaring an unbounded wavelength/e ratio.
    ratio = e*f/(5*SOUND_SPEED)
    inverse = (1/ratio)**2 if ratio > 1 else None
    return ((1+inverse)/(1/3+inverse) if inverse is not None
            else (1+ratio*ratio)/(1+ratio*ratio/3))


@cached_geometry
def chain_geometry(source, receiver, edges):
    """Lengths of an already selected blocked convex chain, in metres."""
    path = [list(source)]+[list(p) for p in edges]+[list(receiver)]
    length = math.fsum(math.dist(a, b) for a, b in zip(path, path[1:]))
    direct = positive(math.dist(source, receiver))
    e = math.fsum(math.dist(a, b) for a, b in zip(edges, edges[1:]))
    return dict(path=path, direct_distance_m=direct, path_distance_m=length,
                delta_m=max(0., math.fsum((length, -direct))), inter_edge_path_m=e)


@cached_geometry
def convex_chain(candidates, source, receiver):
    """Clockwise upper envelope, retaining physical endpoint coordinates."""
    hull = []
    for p in [list(source)]+[list(p) for p in candidates]+[list(receiver)]:
        while len(hull) >= 2:
            a, b = hull[-2:]
            cross = (b[0]-a[0])*(p[1]-b[1])-(b[1]-a[1])*(p[0]-b[0])
            if cross < 0:
                break
            hull.pop()
        hull.append(p)
    return hull


@cached_geometry
def terrain_slice(segments, start, end):
    parts = []
    for a, b, u, v in segments:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            parts.append((lo, hi, u+(v-u)*(lo-a)/(b-a), u+(v-u)*(hi-a)/(b-a)))
    return parts


def diffraction(frequency_hz, delta_m, inter_edge_path_m):
    coefficient = multiple_coefficient(frequency_hz, inter_edge_path_m)
    q = 40*coefficient*finite_number(delta_m)*positive(frequency_hz)/SOUND_SPEED
    return 0. if q < -2 else max(0., 10*math.log10(3+q))


def multiple_edge_boundary(points, source, receiver, frequency_hz, ground_segments, source_g,
                           *, obstacles=None, opaque=None, infinite_lateral_extent=None,
                           conditions='homogeneous', scene_kind='unqualified'):
    """A complete declared 2D synthetic/reference cross section is required.

    obstacles is an explicit list of roof polylines, each with one or more
    vertices. One vertex is an ideal thin screen; multiple vertices describe
    finite width along propagation. Terrain supplies other ridge candidates.
    Opacity and lateral extrusion apply to the entire supplied cross section.
    Finite transverse extent, transmission and clear multiple-edge selection
    remain outside this bounded calculation. None never means an empty scene.
    """
    segments = linear_segments(points)
    if conditions != 'homogeneous':
        return withheld('unsupported_propagation_conditions')
    if scene_kind not in ('synthetic', 'published_reference'):
        return withheld('real_area_scene_not_qualified')
    plane = mean_plane(segments)
    if plane['status'] == 'withheld':
        return withheld('unknown_terrain_support')
    if source is None or receiver is None or source[1] is None or receiver[1] is None:
        return withheld('unknown_endpoint_elevation')
    upper_path(points, source, receiver)
    source, receiver = [list(finite_number(v) for v in p) for p in (source, receiver)]
    f = positive(frequency_hz)
    if f not in BANDS:
        return withheld('unsupported_nominal_frequency')
    g = ground_average(ground_segments, points[0][0], points[-1][0])
    if g is None or source_g is None:
        return withheld('unknown_acoustic_ground')
    source_g = ground_factor(source_g)
    if obstacles is None or opaque is None or infinite_lateral_extent is None:
        return withheld('unknown_obstacle_geometry_or_properties')
    if not isinstance(opaque, bool) or not isinstance(infinite_lateral_extent, bool):
        raise ValueError('Cross-section properties must be explicit booleans')
    if not opaque or not infinite_lateral_extent:
        return withheld('unsupported_transmission_or_finite_lateral_extent')
    if not isinstance(obstacles, (list, tuple)) or len(obstacles) > MAX_OBSTACLES:
        raise ValueError('A bounded list of obstacles is required')
    vertices, widths, previous = [], [], None
    for obstacle in obstacles:
        roof = obstacle.get('vertices')
        if roof is None or any(z is None for _, z in roof):
            return withheld('unknown_obstacle_geometry_or_properties')
        if not roof:
            raise ValueError('An obstacle needs at least one vertex')
        roof = [list(finite_number(v) for v in p) for p in roof]
        if any(b[0] <= a[0] for a, b in zip(roof, roof[1:])):
            raise ValueError('Roof chainages must strictly increase')
        if not points[0][0] < roof[0][0] <= roof[-1][0] < points[-1][0]:
            raise ValueError('Obstacles must be strictly inside the profile')
        if previous is not None and roof[0][0] <= previous:
            raise ValueError('Obstacles must be ordered and nonoverlapping')
        previous = roof[-1][0]
        # Check every ground/roof break, not only roof endpoint elevations.
        for index, (a, b) in enumerate(zip(roof, roof[1:])):
            for lo, hi, u, v in terrain_slice(segments, a[0], b[0]):
                for x, z in ((lo, u), (hi, v)):
                    top = a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])
                    if top < z-1e-10:
                        raise ValueError('Obstacle roof cannot lie beneath terrain')
        if len(roof) == 1:
            x, z = roof[0]
            local = next(u+(v-u)*(x-a)/(b-a) for a, b, u, v in segments if a <= x <= b)
            if z < local:
                raise ValueError('Obstacle top cannot lie beneath terrain')
        vertices.extend(roof); widths.append(roof[-1][0]-roof[0][0])
        if len(vertices) > MAX_VERTICES:
            raise ValueError('Obstacle vertex budget exceeded')
    elevations = dict(points[1:-1])
    for x, z in vertices:
        elevations[x] = max(z, elevations.get(x, z))
    candidates = sorted([x, z] for x, z in elevations.items())
    path = convex_chain(candidates, source, receiver)
    edges = path[1:-1]
    if not edges:
        if not obstacles and plane['rms_residual_m'] <= 1e-10:
            return single_edge_boundary(points, source, receiver, f, ground_segments, source_g,
                                        edge=None, scene_kind=scene_kind)
        return withheld('clear_path_multiple_geometry_requires_selection')
    main_geometry = chain_geometry(source, receiver, edges)
    first, last = edges[0], edges[-1]
    ps = mean_plane(terrain_slice(segments, points[0][0], first[0]))
    pr = mean_plane(terrain_slice(segments, last[0], points[-1][0]))
    sf, rf = plane_frame(ps, source, first), plane_frame(pr, last, receiver)
    sm, rm = sf['source']['mirror_point'], rf['receiver']['mirror_point']
    sb, rb = sf['source']['negative_equivalent_height_clipped'], rf['receiver']['negative_equivalent_height_clipped']
    # Qualifications are checked against all supplied terrain/roof candidates.
    # The present subset does not resolve changes of edges in image paths.
    if candidates and not sm[0] < candidates[0][0] <= candidates[-1][0] < rm[0]:
        return withheld('unsupported_mirror_point_order')
    source_chain = convex_chain(candidates, sm, receiver)
    receiver_chain = convex_chain(candidates, source, rm)
    if ((not sb and source_chain[1:-1] != edges) or (not rb and receiver_chain[1:-1] != edges)):
        return withheld('mirror_changes_controlling_chain', controlling_edges=edges,
                        source_image_edges=source_chain[1:-1], receiver_image_edges=receiver_chain[1:-1])
    source_geometry = main_geometry if sb else chain_geometry(sm, receiver, edges)
    receiver_geometry = main_geometry if rb else chain_geometry(source, rm, edges)
    gs = side_ground(ground_segments, points[0][0], first[0])
    gr = side_ground(ground_segments, last[0], points[-1][0])
    sg = ground_homogeneous(f, sf['projected_distance_m'], sf['source']['equivalent_height_m'],
                           sf['receiver']['equivalent_height_m'], gs, source_g)
    rg = ground_homogeneous(f, rf['projected_distance_m'], rf['source']['equivalent_height_m'],
                           rf['receiver']['equivalent_height_m'], gr, source_g, receiver_side=True)
    main = diffraction(f, main_geometry['delta_m'], main_geometry['inter_edge_path_m'])
    si = main if sb else diffraction(f, source_geometry['delta_m'], source_geometry['inter_edge_path_m'])
    ri = main if rb else diffraction(f, receiver_geometry['delta_m'], receiver_geometry['inter_edge_path_m'])

    def coupling(ground, image, below):
        if below:
            return ground
        amplitude = 1+math.expm1(-ground*math.log(10)/20)*math.exp((main-image)*math.log(10)/20)
        return None if amplitude <= 0 else -20*math.log10(amplitude)

    sc, rc = coupling(sg, si, sb), coupling(rg, ri, rb)
    if sc is None or rc is None:
        return withheld('nonpositive_ground_diffraction_coupling')
    return dict(status='conditional_research', branch='diffraction_including_ground', method=METHOD,
        controlling_edges=edges, controlling_edge_count=len(edges), obstacle_widths_m=widths,
        main_geometry=main_geometry, source_image_geometry=source_geometry, receiver_image_geometry=receiver_geometry,
        multiple_diffraction_coefficient=multiple_coefficient(f, main_geometry['inter_edge_path_m']),
        source_plane=ps, receiver_plane=pr, source_frame=sf, receiver_frame=rf, source_mirror=sm, receiver_mirror=rm,
        g_path=g, source_g=source_g, source_side_g=gs, receiver_side_g=gr,
        source_side_corrected_g=corrected_ground(gs, source_g, sf['projected_distance_m'],
            sf['source']['equivalent_height_m'], sf['receiver']['equivalent_height_m']),
        main_diffraction_uncapped_db=main, main_diffraction_db=min(main, 25.), source_image_diffraction_db=si,
        receiver_image_diffraction_db=ri, source_ground_db=sg, receiver_ground_db=rg,
        source_ground_coupling_db=sc, receiver_ground_coupling_db=rc,
        source_below_mean_plane_rule=sb, receiver_below_mean_plane_rule=rb,
        separate_ground_db=0., boundary_db=min(main, 25.)+sc+rc,
        annual_noise_prediction=None, regulatory_implementation_qualified=False)
