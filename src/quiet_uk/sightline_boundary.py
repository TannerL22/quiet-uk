"""Homogeneous cross-section research boundary with clear-ray selection.

Annex II / 2021: select physical edges first. Image diffraction uses that
selected chain; the Rayleigh mirror test uses only the selected clear edge.
Buildings replace positive-length surface support with their declared roofs
and acoustic G. Original terrain and all input heights remain unchanged.
"""
import math
from quiet_uk.propagation_geometry_cache import cached_geometry
from quiet_uk.conditional_scene_domain import scene_supported

from quiet_uk.propagation_canary import BANDS, corrected_ground, finite_number, ground_factor, ground_homogeneous, positive
from quiet_uk.terrain_profile import linear_segments, mean_plane, plane_frame, upper_path
from quiet_uk.terrain_boundary import ground_average, path_difference, rayleigh_selection, side_ground, terrain_corners, withheld
from quiet_uk.multiple_edge_boundary import (
    MAX_OBSTACLES, MAX_VERTICES, chain_geometry, convex_chain, diffraction, multiple_coefficient, terrain_slice,
)

METHOD = 'EU-Annex-II-2021-homogeneous-physical-edge-selection-and-composite-surface'


@cached_geometry
def validated_roofs(segments, obstacles):
    """Validate complete cross sections; return None for genuinely unknown input."""
    if obstacles is None:
        return None
    if not isinstance(obstacles, (list, tuple)) or len(obstacles)>MAX_OBSTACLES:
        raise ValueError('A bounded list of obstacles is required')
    roofs, previous, count = [], None, 0
    for record in obstacles:
        if not isinstance(record, dict):
            raise ValueError('Each obstacle must declare vertices and roof_g')
        raw = record.get('vertices')
        if raw is None:
            return None
        if not isinstance(raw, (list, tuple)) or not raw:
            raise ValueError('An obstacle needs a nonempty vertex list')
        count += len(raw)
        if count>MAX_VERTICES:
            raise ValueError('Obstacle vertex budget exceeded')
        if any(not isinstance(p, (list, tuple)) or len(p)!=2 for p in raw):
            raise ValueError('Vertices must have exactly two coordinates')
        if any(p[1] is None for p in raw):
            return None
        vertices = [[finite_number(v) for v in p] for p in raw]
        if any(b[0]<=a[0] for a,b in zip(vertices, vertices[1:])):
            raise ValueError('Roof chainages must strictly increase')
        if not segments[0][0]<vertices[0][0]<=vertices[-1][0]<segments[-1][1]:
            raise ValueError('Obstacles must be strictly inside the profile')
        if previous is not None and vertices[0][0]<=previous:
            raise ValueError('Obstacles must be ordered and nonoverlapping')
        previous = vertices[-1][0]
        for x,z in vertices:
            a,b,u,v = next(p for p in segments if p[0]<=x<=p[1])
            if z<u+(v-u)*(x-a)/(b-a)-1e-10:
                raise ValueError('Obstacle top cannot lie beneath terrain')
        for a,b in zip(vertices, vertices[1:]):
            for lo,hi,u,v in terrain_slice(segments, a[0], b[0]):
                for x,z in ((lo,u),(hi,v)):
                    if a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0])<z-1e-10:
                        raise ValueError('Obstacle roof cannot hide terrain above it')
        # A thin ideal screen represents no positive-length roof support.
        g = record.get('roof_g')
        if len(vertices)>1 and g is None:
            return None
        roofs.append(dict(vertices=vertices, roof_g=None if g is None else ground_factor(g)))
    return roofs


@cached_geometry
def composite_surface(segments, ground_segments, roofs):
    """Exact positive-length surface/G support; vertical roof walls have no area."""
    cuts = sorted({x for a,b,_,_ in segments for x in (a,b)}
                  |{x for a,b,_ in ground_segments for x in (a,b)}
                  |{x for roof in roofs for x,_ in roof['vertices']})
    surface, ground = [], []
    ti, gi, oi = 0, 0, 0
    wide = [r for r in roofs if len(r['vertices'])>1]
    for a,b in zip(cuts, cuts[1:]):
        middle = a+(b-a)/2
        while segments[ti][1]<=a: ti+=1
        while ground_segments[gi][1]<=a: gi+=1
        while oi<len(wide) and wide[oi]['vertices'][-1][0]<=a: oi+=1
        roof = wide[oi] if oi<len(wide) and wide[oi]['vertices'][0][0]<=a and b<=wide[oi]['vertices'][-1][0] else None
        if roof:
            lo,hi = next((lo,hi) for lo,hi in zip(roof['vertices'],roof['vertices'][1:]) if lo[0]<=middle<=hi[0])
            u = lo[1]+(hi[1]-lo[1])*(a-lo[0])/(hi[0]-lo[0])
            v = lo[1]+(hi[1]-lo[1])*(b-lo[0])/(hi[0]-lo[0])
            g = roof['roof_g']
        else:
            lo,hi,u0,v0 = segments[ti]
            u,v = [u0+(v0-u0)*(x-lo)/(hi-lo) for x in (a,b)]
            g = ground_segments[gi][2]
        surface.append((a,b,u,v)); ground.append((a,b,g))
    return surface, ground


@cached_geometry
def physical_edge_candidates(points, roofs):
    """True terrain corners and roof edges; no fictitious edge at a G split."""
    wide = [r for r in roofs if len(r['vertices'])>1]
    candidates = [p for p in terrain_corners(points)
                  if not any(r['vertices'][0][0]<=p[0]<=r['vertices'][-1][0] for r in wide)]
    for roof in roofs:
        vertices = roof['vertices']
        candidates.extend(vertices if len(vertices)==1 else [vertices[0], *terrain_corners(vertices), vertices[-1]])
    elevations = {}
    for x,z in candidates:
        elevations[x] = max(z,elevations.get(x,z))
    return sorted([x,z] for x,z in elevations.items())


@cached_geometry
def selected_chain_geometry(source, receiver, edges):
    """Each image pair has its own direct baseline, with physical edges fixed."""
    geometry = chain_geometry(source, receiver, edges)
    outside = len(edges)==1 and not min(source[0],receiver[0])<edges[0][0]<max(source[0],receiver[0])
    # The negative single-edge expression describes an edge below the direct
    # ray *between* endpoints. A virtual endpoint can project before the fixed
    # physical edge (TC14); the forced returning leg retains positive excess.
    if len(edges)==1 and not outside:
        geometry['delta_m'] = path_difference(source, receiver, edges[0])['delta_m']
    geometry['single_edge_outside_endpoint_span'] = outside
    return geometry


def sightline_boundary(points, source, receiver, frequency_hz, ground_segments, source_g,
                       *, obstacles=None, opaque=None, infinite_lateral_extent=None,
                       conditions='homogeneous', scene_kind='unqualified', conditional_input_policy=None):
    """Conditional octave-band boundary only; explicit synthetic/reference domain.

    Declared roofs require acoustic roof_g independently of underlying ground.
    Opacity and lateral extrusion are explicit assumptions, never inferred
    from DSM/land cover. Weather, lateral paths, transmission, reflections,
    source power, annual levels and real-area qualification remain outside.
    """
    segments = linear_segments(points)
    if conditions!='homogeneous': return withheld('unsupported_propagation_conditions')
    if not scene_supported(scene_kind,conditional_input_policy): return withheld('real_area_scene_not_qualified')
    if mean_plane(segments)['status']=='withheld': return withheld('unknown_terrain_support')
    if source is None or receiver is None or source[1] is None or receiver[1] is None:
        return withheld('unknown_endpoint_elevation')
    upper_path(points,source,receiver)
    source,receiver = [[finite_number(v) for v in p] for p in (source,receiver)]
    f = positive(frequency_hz)
    if f not in BANDS: return withheld('unsupported_nominal_frequency')
    if ground_average(ground_segments,points[0][0],points[-1][0]) is None or source_g is None:
        return withheld('unknown_acoustic_ground')
    source_g = ground_factor(source_g)
    if opaque is None or infinite_lateral_extent is None: return withheld('unknown_obstacle_geometry_or_properties')
    if not isinstance(opaque,bool) or not isinstance(infinite_lateral_extent,bool):
        raise ValueError('Cross-section properties must be explicit booleans')
    if not opaque or not infinite_lateral_extent: return withheld('unsupported_transmission_or_finite_lateral_extent')
    roofs = validated_roofs(segments,obstacles)
    if roofs is None: return withheld('unknown_obstacle_geometry_or_roof_ground')
    surface,ground = composite_surface(segments,ground_segments,roofs)
    candidates = physical_edge_candidates(points,roofs)
    main_edges = convex_chain(candidates,source,receiver)[1:-1]
    blocked = bool(main_edges)
    plane = mean_plane(surface)
    g = side_ground(ground,points[0][0],points[-1][0])
    common = dict(method=METHOD,surface_segments=surface,surface_ground_segments=ground,physical_edge_candidates=candidates,
                  common_plane=plane,g_path=g,source_g=source_g,obstacle_widths_m=[r['vertices'][-1][0]-r['vertices'][0][0] for r in roofs])

    def ground_only(branch,**diagnostics):
        try: frame = plane_frame(plane,source,receiver)
        except ValueError: return withheld('nonpositive_common_projection',**common,**diagnostics)
        value = ground_homogeneous(f,frame['projected_distance_m'],frame['source']['equivalent_height_m'],
                                  frame['receiver']['equivalent_height_m'],g,source_g)
        return dict(status='conditional_research',branch=branch,boundary_db=value,common_frame=frame,
                    separate_ground_db=None,annual_noise_prediction=None,regulatory_implementation_qualified=False,**common,**diagnostics)

    if not candidates: return ground_only('ground_only_no_edges')
    # Only true physical corners enter the clear-ray search. Redundant sampling
    # along a roof, a planar slope or a G boundary is not a new diffraction edge.
    if not blocked:
        deltas = [(path_difference(source,receiver,p)['delta_m'],p) for p in candidates]
        main_edges = [max(deltas,key=lambda pair:pair[0])[1]]
    first,last = main_edges[0],main_edges[-1]
    ps = mean_plane(terrain_slice(surface,points[0][0],first[0]))
    pr = mean_plane(terrain_slice(surface,last[0],points[-1][0]))
    try: sf,rf = plane_frame(ps,source,first),plane_frame(pr,last,receiver)
    except ValueError: return withheld('nonpositive_side_projection',**common)
    sm,rm = sf['source']['mirror_point'],rf['receiver']['mirror_point']
    main_geometry = selected_chain_geometry(source,receiver,main_edges)
    selection = dict(use_diffraction=True,blocked=True) if blocked else rayleigh_selection(
        f,main_geometry['delta_m'],path_difference(sm,rm,first)['delta_m'],False)
    geometry = dict(controlling_edges=main_edges,controlling_edge_count=len(main_edges),direct_ray_blocked=blocked,
        main_geometry=main_geometry,selection=selection,source_plane=ps,receiver_plane=pr,source_frame=sf,receiver_frame=rf,
        source_mirror=sm,receiver_mirror=rm,rayleigh_mirror_edge=first if not blocked else None)
    if not selection['use_diffraction']:
        return ground_only('ground_only_clear',**geometry)
    sb,rb = sf['source']['negative_equivalent_height_clipped'],rf['receiver']['negative_equivalent_height_clipped']
    # Pure image diffraction is evaluated on the physical path's chosen edges.
    # A convex hull around a virtual endpoint may add entirely different edges;
    # that hull is recorded as a diagnostic, not substituted into the equation.
    sg_path = main_geometry if sb else selected_chain_geometry(sm,receiver,main_edges)
    rg_path = main_geometry if rb else selected_chain_geometry(source,rm,main_edges)
    source_hull = convex_chain([p for p in candidates if sm[0]<p[0]<receiver[0]],sm,receiver)[1:-1]
    receiver_hull = convex_chain([p for p in candidates if source[0]<p[0]<rm[0]],source,rm)[1:-1]
    gs,gr = side_ground(ground,points[0][0],first[0]),side_ground(ground,last[0],points[-1][0])
    sg = ground_homogeneous(f,sf['projected_distance_m'],sf['source']['equivalent_height_m'],sf['receiver']['equivalent_height_m'],gs,source_g)
    rg = ground_homogeneous(f,rf['projected_distance_m'],rf['source']['equivalent_height_m'],rf['receiver']['equivalent_height_m'],gr,source_g,receiver_side=True)
    main = diffraction(f,main_geometry['delta_m'],main_geometry['inter_edge_path_m'])
    si = main if sb else diffraction(f,sg_path['delta_m'],sg_path['inter_edge_path_m'])
    ri = main if rb else diffraction(f,rg_path['delta_m'],rg_path['inter_edge_path_m'])

    def coupling(ag,image,below):
        if below: return ag
        amplitude = 1+math.expm1(-ag*math.log(10)/20)*math.exp((main-image)*math.log(10)/20)
        return None if amplitude<=0 else -20*math.log10(amplitude)

    sc,rc = coupling(sg,si,sb),coupling(rg,ri,rb)
    if sc is None or rc is None: return withheld('nonpositive_ground_diffraction_coupling',**common,**geometry)
    return dict(status='conditional_research',branch='diffraction_including_ground',**common,**geometry,
        source_image_geometry=sg_path,receiver_image_geometry=rg_path,
        source_virtual_hull_edges=source_hull,receiver_virtual_hull_edges=receiver_hull,
        source_virtual_hull_changes_edges=source_hull!=main_edges,receiver_virtual_hull_changes_edges=receiver_hull!=main_edges,
        multiple_diffraction_coefficient=multiple_coefficient(f,main_geometry['inter_edge_path_m']),
        source_side_g=gs,receiver_side_g=gr,source_side_corrected_g=corrected_ground(gs,source_g,sf['projected_distance_m'],
            sf['source']['equivalent_height_m'],sf['receiver']['equivalent_height_m']),
        main_diffraction_uncapped_db=main,main_diffraction_db=min(main,25.),source_image_diffraction_db=si,
        receiver_image_diffraction_db=ri,source_ground_db=sg,receiver_ground_db=rg,source_ground_coupling_db=sc,receiver_ground_coupling_db=rc,
        source_below_mean_plane_rule=sb,receiver_below_mean_plane_rule=rb,separate_ground_db=0.,
        boundary_db=min(main,25.)+sc+rc,annual_noise_prediction=None,regulatory_implementation_qualified=False)
