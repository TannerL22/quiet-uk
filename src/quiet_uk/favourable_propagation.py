"""Conditional amended CNOSSOS favourable DIRECT cross-section research.

Own equations from Annex II, 2018 correction, 2021 curved envelope and ISO
9613-1 pure-tone absorption. No real scene, annual level or certification.
"""
import math
from quiet_uk.propagation_geometry_cache import cached_geometry
from quiet_uk.conditional_scene_domain import scene_supported

from quiet_uk.propagation_canary import BANDS, corrected_ground, finite_number, ground_factor, nonnegative, positive
from quiet_uk.terrain_profile import linear_segments, mean_plane, plane_frame, upper_path
from quiet_uk.terrain_boundary import ground_average, path_difference, rayleigh_selection, side_ground, withheld
from quiet_uk.multiple_edge_boundary import convex_chain, diffraction, multiple_coefficient, terrain_slice
from quiet_uk.sightline_boundary import composite_surface, physical_edge_candidates, sightline_boundary, validated_roofs

METHOD = 'EU-Annex-II-2018-2021-favourable-circular-envelope-with-explicit-air'
EXACT_CENTRES = dict(zip(BANDS, (1000*10**(.3*i) for i in range(-4,4))))
ENVELOPE_ALGORITHM = 'straight-upper-support-hull-then-circular-envelope-v2'


def arc_length(chord_m, radius_m):
    chord, radius = nonnegative(chord_m), positive(radius_m)
    if chord > 2*radius:
        raise ValueError('Chord exceeds circle diameter')
    return 2*radius*math.asin(chord/(2*radius))


def ray_radius(source, receiver):
    return max(1000., 8*positive(math.dist(source, receiver)))


def arc_tangents(a, b, radius):
    """Upper minor arc: departure and arrival angles in the vertical plane."""
    length = positive(math.dist(a,b))
    if b[0] <= a[0] or length >= 2*radius:
        raise ValueError('Unsupported circular envelope geometry')
    angle = math.atan2(b[1]-a[1],b[0]-a[0])
    half = math.asin(length/(2*radius))
    if not -math.pi/2 < angle-half <= angle+half < math.pi/2:
        raise ValueError('Circular envelope must be a single-valued height path')
    return angle+half, angle-half


@cached_geometry
def circular_envelope(candidates, source, receiver, radius):
    """Shortest downward-deflecting combination of upper minor arcs.

    A chord hull is insufficient: a corner below an enclosing arc is removed
    even when it lies above the straight chord. Physical endpoints stay fixed.

    Every admissible graph of upper arcs with downward junctions is concave.
    It therefore lies above the straight upper hull of the supplied points.
    Corners strictly below that hull cannot control the circular envelope.
    Remove them before testing arcs: adjacent raw terrain/roof corners can
    have an unsupported minor arc even though the enclosing path clears both.
    This filters path candidates only; terrain, roofs and ground support used
    for side planes and attenuation remain unchanged.
    """
    nodes=[[finite_number(v) for v in p] for p in [source,*candidates,receiver]]
    if any(len(p)!=2 for p in nodes) or any(b[0]<=a[0] for a,b in zip(nodes,nodes[1:])):
        raise ValueError('Circular support must have strictly increasing two-dimensional points')
    radius=positive(radius)
    chain = []
    for p in convex_chain(nodes[1:-1],nodes[0],nodes[-1]):
        while len(chain)>1:
            incoming = arc_tangents(chain[-2],chain[-1],radius)[1]
            outgoing = arc_tangents(chain[-1],p,radius)[0]
            if incoming-outgoing > 1e-12:
                break
            chain.pop()
        if chain:
            arc_tangents(chain[-1],p,radius)
        chain.append(p)
    return chain


@cached_geometry
def curved_geometry(source, receiver, edges):
    """2.5.25..28 on the selected physical chain; image pairs own their radius."""
    radius = ray_radius(source,receiver)
    path = [list(source)]+[list(p) for p in edges]+[list(receiver)]
    lengths = [arc_length(math.dist(a,b),radius) for a,b in zip(path,path[1:])]
    baseline = arc_length(math.dist(source,receiver),radius)
    delta = math.fsum([*lengths,-baseline])
    outside = len(edges)==1 and not min(source[0],receiver[0])<edges[0][0]<max(source[0],receiver[0])
    if len(edges)==1 and not outside and not path_difference(source,receiver,edges[0])['blocked']:
        # 2.5.27: A is the vertical projection of the edge onto the straight ray.
        x = edges[0][0]
        a = [x,source[1]+(receiver[1]-source[1])*(x-source[0])/(receiver[0]-source[0])]
        delta = math.fsum([2*arc_length(math.dist(source,a),radius),2*arc_length(math.dist(a,receiver),radius),
                           -sum(lengths),-baseline])
    return dict(path=path,radius_m=radius,direct_distance_m=math.dist(source,receiver),direct_arc_m=baseline,
                curved_segment_lengths_m=lengths,path_distance_m=math.fsum(lengths),delta_m=delta,
                inter_edge_path_m=math.fsum(lengths[1:-1]),single_edge_outside_endpoint_span=outside)


def favourable_ground(frequency_hz, dp, zs, zr, g_path, source_g, *, receiver_side=False):
    """2.5.15..20: Gw=Gpath; Gm=Gprime (source) or Gpath (receiver)."""
    f,dp,zs,zr = positive(frequency_hz),positive(dp),nonnegative(zs),nonnegative(zr)
    g,source_g = ground_factor(g_path),ground_factor(source_g)
    gm = g if receiver_side else corrected_ground(g,source_g,dp,zs,zr)
    height = zs+zr
    floor = -3*(1-gm)*(1+2*max(0.,1-30*height/dp))
    common = dict(g_w=g,g_m=gm,lower_bound_db=floor)
    if g==0:
        return dict(attenuation_db=floor,modified_source_height_m=None,modified_receiver_height_m=None,**common)
    # Positive G and two null heights make the turbulence correction singular.
    if height==0:
        return dict(attenuation_db=None,reason='zero_height_sum_for_favourable_ground',**common)
    turbulence = .006*dp/height
    s = zs+.0001*(zs/height)**2*dp**2+turbulence
    r = zr+.0001*(zr/height)**2*dp**2+turbulence
    k = 2*math.pi*f/340
    w = .0185*f**2.5*g**2.6/(f**1.5*g**2.6+1300*f**.75*g**1.3+1.16e6)
    cf = dp*(1+3*w*dp*math.exp(-math.sqrt(w*dp)))/(1+w*dp)
    b = cf/(2*k)
    power = 4*k*k/dp**2*((s-math.sqrt(b))**2+b)*((r-math.sqrt(b))**2+b)
    return dict(attenuation_db=max(-10*math.log10(power),floor),modified_source_height_m=s,
                modified_receiver_height_m=r,w=w,cf_m=cf,**common)


def favourable_boundary(points,source,receiver,frequency_hz,ground_segments,source_g,
                        *,obstacles=None,opaque=None,infinite_lateral_extent=None,scene_kind='unqualified',conditions='favourable',conditional_input_policy=None):
    segments = linear_segments(points)
    if conditions!='favourable': return withheld('unsupported_propagation_conditions')
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
    if not isinstance(opaque,bool) or not isinstance(infinite_lateral_extent,bool): raise ValueError('Explicit booleans required')
    if not opaque or not infinite_lateral_extent: return withheld('unsupported_transmission_or_finite_lateral_extent')
    roofs = validated_roofs(segments,obstacles)
    if roofs is None: return withheld('unknown_obstacle_geometry_or_roof_ground')
    surface,ground = composite_surface(segments,ground_segments,roofs)
    candidates = physical_edge_candidates(points,roofs)
    common = dict(method=METHOD,conditions='favourable',surface_segments=surface,surface_ground_segments=ground,
                  circular_envelope_algorithm=ENVELOPE_ALGORITHM,
                  physical_edge_candidates=candidates,common_plane=mean_plane(surface),source_g=source_g,
                  g_path=side_ground(ground,points[0][0],points[-1][0]))
    try:
        edges = circular_envelope(candidates,source,receiver,ray_radius(source,receiver))[1:-1]
    except ValueError: return withheld('unsupported_circular_envelope',**common)
    blocked = bool(edges)

    def ground_only(branch,**geometry):
        try: frame = plane_frame(common['common_plane'],source,receiver)
        except ValueError: return withheld('nonpositive_common_projection',**common,**geometry)
        ground_result = favourable_ground(f,frame['projected_distance_m'],frame['source']['equivalent_height_m'],
            frame['receiver']['equivalent_height_m'],common['g_path'],source_g)
        if ground_result['attenuation_db'] is None: return withheld(ground_result['reason'],**common,**geometry)
        return dict(status='conditional_research',branch=branch,boundary_db=ground_result['attenuation_db'],common_frame=frame,
            ground_result=ground_result,annual_noise_prediction=None,regulatory_implementation_qualified=False,**common,**geometry)

    if not candidates: return ground_only('ground_only_no_edges')
    try:
        if not blocked:
            edges = [max(candidates,key=lambda p:curved_geometry(source,receiver,[p])['delta_m'])]
        first,last = edges[0],edges[-1]
        ps,pr = mean_plane(terrain_slice(surface,points[0][0],first[0])),mean_plane(terrain_slice(surface,last[0],points[-1][0]))
        sf,rf = plane_frame(ps,source,first),plane_frame(pr,last,receiver)
        sm,rm = sf['source']['mirror_point'],rf['receiver']['mirror_point']
        main_geometry = curved_geometry(source,receiver,edges)
        # The strict amended clear-path test uses the named S*,D,R* only.
        mirror_geometry = curved_geometry(sm,rm,[first]) if not blocked else None
    except ValueError: return withheld('unsupported_curved_path_or_side_projection',**common)
    selection = dict(use_diffraction=True,blocked=True) if blocked else rayleigh_selection(f,main_geometry['delta_m'],mirror_geometry['delta_m'])
    geometry = dict(controlling_edges=edges,controlling_edge_count=len(edges),direct_arc_blocked=blocked,main_geometry=main_geometry,
        selection=selection,rayleigh_mirror_geometry=mirror_geometry,source_plane=ps,receiver_plane=pr,source_frame=sf,receiver_frame=rf,
        source_mirror=sm,receiver_mirror=rm)
    if not selection['use_diffraction']: return ground_only('ground_only_clear',**geometry)
    sb,rb = sf['source']['negative_equivalent_height_clipped'],rf['receiver']['negative_equivalent_height_clipped']
    try:
        si_path = main_geometry if sb else curved_geometry(sm,receiver,edges)
        ri_path = main_geometry if rb else curved_geometry(source,rm,edges)
    except ValueError: return withheld('unsupported_image_circle_diameter',**common,**geometry)
    gs,gr = side_ground(ground,points[0][0],first[0]),side_ground(ground,last[0],points[-1][0])
    sg = favourable_ground(f,sf['projected_distance_m'],sf['source']['equivalent_height_m'],sf['receiver']['equivalent_height_m'],gs,source_g)
    rg = favourable_ground(f,rf['projected_distance_m'],rf['source']['equivalent_height_m'],rf['receiver']['equivalent_height_m'],gr,source_g,receiver_side=True)
    if sg['attenuation_db'] is None or rg['attenuation_db'] is None: return withheld('zero_height_sum_for_favourable_ground',**common,**geometry)
    main = diffraction(f,main_geometry['delta_m'],main_geometry['inter_edge_path_m'])
    si,ri = [diffraction(f,p['delta_m'],p['inter_edge_path_m']) for p in (si_path,ri_path)]

    def coupling(ag,image,below):
        if below: return ag
        a = 1+math.expm1(-ag*math.log(10)/20)*math.exp((main-image)*math.log(10)/20)
        return None if a<=0 else -20*math.log10(a)

    sc,rc = coupling(sg['attenuation_db'],si,sb),coupling(rg['attenuation_db'],ri,rb)
    if sc is None or rc is None: return withheld('nonpositive_ground_diffraction_coupling',**common,**geometry)
    return dict(status='conditional_research',branch='diffraction_including_ground',boundary_db=min(main,25)+sc+rc,
        main_diffraction_uncapped_db=main,main_diffraction_db=min(main,25),source_image_diffraction_db=si,receiver_image_diffraction_db=ri,
        source_ground_db=sg['attenuation_db'],receiver_ground_db=rg['attenuation_db'],source_ground_coupling_db=sc,receiver_ground_coupling_db=rc,
        source_ground_result=sg,receiver_ground_result=rg,source_image_geometry=si_path,receiver_image_geometry=ri_path,
        source_side_g=gs,receiver_side_g=gr,multiple_diffraction_coefficient=multiple_coefficient(f,main_geometry['inter_edge_path_m']),
        source_below_mean_plane_rule=sb,receiver_below_mean_plane_rule=rb,separate_ground_db=0.,
        annual_noise_prediction=None,regulatory_implementation_qualified=False,**common,**geometry)


def atmospheric_absorption(frequency_hz, *,temperature_c=None,relative_humidity_percent=None,pressure_kpa=None,uniform_air=None):
    """ISO pure-tone equations; RH and water-vapour concentration are percentages.

    Bounded research domain: -20..50 C, 10..100 % RH, 50..120 kPa, 50..10000 Hz.
    No frequency conversion/default pressure or weather is hidden in this API.
    """
    if any(v is None for v in (temperature_c,relative_humidity_percent,pressure_kpa,uniform_air)):
        return dict(status='withheld',reason='unknown_absorption_air',alpha_db_per_km=None)
    if not isinstance(uniform_air,bool): raise ValueError('Uniform air must be an explicit boolean')
    if not uniform_air: return dict(status='withheld',reason='inhomogeneous_absorption_air',alpha_db_per_km=None)
    f,t,rh,p = [finite_number(v) for v in (frequency_hz,temperature_c,relative_humidity_percent,pressure_kpa)]
    if not (50<=f<=10000 and -20<=t<=50 and 10<=rh<=100 and 50<=p<=120):
        return dict(status='withheld',reason='outside_bounded_absorption_domain',alpha_db_per_km=None)
    kelvin = t+273.15
    ratio,pressure = kelvin/293.15,p/101.325
    saturation = 101.325*10**(-6.8346*(273.16/kelvin)**1.261+4.6151)
    h = rh*saturation/p
    fo = pressure*(24+40400*h*(.02+h)/(.391+h))
    fn = pressure*ratio**-.5*(9+280*h*math.exp(-4.17*(ratio**(-1/3)-1)))
    alpha = 8.686*f*f*(1.84e-11/pressure*math.sqrt(ratio)+ratio**-2.5*(
        .01275*math.exp(-2239.1/kelvin)/(fo+f*f/fo)+.1068*math.exp(-3352/kelvin)/(fn+f*f/fn)))
    return dict(status='conditional_research',frequency_hz=f,temperature_c=t,relative_humidity_percent=rh,pressure_kpa=p,
        water_vapour_molar_percent=h,relaxation_oxygen_hz=fo,relaxation_nitrogen_hz=fn,alpha_db_per_km=1000*alpha,
        standard_estimated_accuracy_percent=10 if .05<=h<=5 else 20,regulatory_implementation_qualified=False)


def weather_weighted_attenuation(homogeneous_db,favourable_db,probability_favourable):
    """Energy-weight two conditional attenuation terms; no annual inference."""
    if any(v is None for v in (homogeneous_db,favourable_db,probability_favourable)): return None
    h,f,p = [finite_number(v) for v in (homogeneous_db,favourable_db,probability_favourable)]
    if not 0<=p<=1: raise ValueError('Favourable probability must lie in [0,1]')
    if p==0: return h
    if p==1: return f
    base = min(h,f)
    return base-10*math.log10((1-p)*10**(-(h-base)/10)+p*10**(-(f-base)/10))


def direct_attenuation(scene,frequency_hz,air,*,probability_favourable=None,absorption_frequency_policy=None):
    """Conditional H/F DIRECT attenuation, with all weather choices supplied.

    Source powers and additional paths are absent, so this is never a level.
    H/F use nominal acoustic bands; ISO absorption uses a declared exact/nominal
    centre policy and the physical 3D direct distance (not a roof detour).
    """
    config = {k:v for k,v in scene.items() if k not in ('case_id','scope','unknown_ground','conditions')}
    if scene.get('conditions') not in (None,'homogeneous','favourable'):
        return dict(status='withheld',reason='unsupported_propagation_conditions',attenuation_db=None,annual_noise_prediction=None)
    if absorption_frequency_policy not in ('exact_base10','nominal'):
        return dict(status='withheld',reason='unknown_absorption_frequency_policy',attenuation_db=None,annual_noise_prediction=None)
    f = positive(frequency_hz)
    if f not in BANDS: return dict(status='withheld',reason='unsupported_nominal_frequency',attenuation_db=None,annual_noise_prediction=None)
    h = sightline_boundary(frequency_hz=f,**config)
    fav = favourable_boundary(frequency_hz=f,**config)
    iso_f = EXACT_CENTRES[f] if absorption_frequency_policy=='exact_base10' else f
    atm = atmospheric_absorption(iso_f,**(air or {}))
    result = dict(method=METHOD,homogeneous=h,favourable=fav,absorption=atm,absorption_frequency_policy=absorption_frequency_policy,
        nominal_frequency_hz=f,probability_favourable=probability_favourable,annual_noise_prediction=None,regulatory_implementation_qualified=False)
    if any(r['status']=='withheld' for r in (h,fav,atm)):
        return dict(status='withheld',reason='incomplete_conditional_path_or_air',attenuation_db=None,**result)
    distance = math.dist(config['source'],config['receiver'])
    loss = atm['alpha_db_per_km']*distance/1000
    divergence = 20*math.log10(distance)+11
    ha,fa = divergence+loss+h['boundary_db'],divergence+loss+fav['boundary_db']
    mixed = weather_weighted_attenuation(ha,fa,probability_favourable)
    return dict(status='conditional_research',direct_distance_m=distance,atmospheric_attenuation_db=loss,divergence_db=divergence,
        homogeneous_attenuation_db=ha,favourable_attenuation_db=fa,attenuation_db=mixed,
        meteorological_mix_status='withheld_unknown_probability' if mixed is None else 'conditional_supplied_probability',**result)
