"""Distance-weighted terrain geometry; limited synthetic plane-ground checks.

JRC2012 VI-3/4 and orthogonal-height construction. Original raster intervals
are piecewise constant, synthetic polylines piecewise linear. Neither native
cell steps nor a mean-plane fit establishes real diffracting edges or shielding.
"""
import math
from quiet_uk.propagation_geometry_cache import cached_geometry

from quiet_uk.propagation_canary import finite_number, positive, ground_homogeneous

MAX_SEGMENTS = 20000


@cached_geometry
def linear_segments(points):
    if len(points) < 2 or len(points) > MAX_SEGMENTS+1:
        raise ValueError('A bounded terrain profile needs at least two points')
    points = [(finite_number(x), None if z is None else finite_number(z)) for x,z in points]
    if any(b[0] <= a[0] for a,b in zip(points,points[1:])):
        raise ValueError('Profile chainages must strictly increase')
    return [(a[0], b[0], a[1], b[1]) for a,b in zip(points,points[1:])]


@cached_geometry
def mean_plane(segments):
    """Exact continuous least-squares line, weighting horizontal distance.

    Normalised, centred coordinates avoid raw x-cubed cancellation. Segment
    endpoint heights may agree (native constant cells) or differ (polylines).
    Missing support withholds the entire fit; no interval is silently omitted.
    """
    if not segments or len(segments) > MAX_SEGMENTS:
        raise ValueError('Nonempty bounded terrain segments required')
    parts = []
    for a,b,u,v in segments:
        a,b = finite_number(a), finite_number(b)
        if b <= a or (parts and a!=parts[-1][1]):
            raise ValueError('Segments must be contiguous and have positive length')
        parts.append((a,b,None if u is None else finite_number(u),None if v is None else finite_number(v)))
    missing = math.fsum(b-a for a,b,u,v in parts if u is None or v is None)
    length = parts[-1][1]-parts[0][0]
    if missing:
        return dict(status='withheld',reason='missing_terrain_support',missing_length_m=missing,profile_length_m=length,
                    slope=None,intercept=None,height_at_origin_m=None)
    origin = parts[0][0]
    centre = origin+length/2
    reference = parts[0][2]
    integrals, moments = [], []
    for a,b,u,v in parts:
        h=(b-a)/length
        t=(a-centre)/length
        u,v=u-reference,v-reference
        integrals.append(h*(u+v)/2)
        moments.append(h*(t*(u+v)/2+h*(u/2+(v-u)/3)))
    average = math.fsum(integrals)
    slope = 12*math.fsum(moments)/length
    at_origin = reference+average-slope*length/2
    error = []
    for a,b,u,v in parts:
        r0=u-(at_origin+slope*(a-origin));r1=v-(at_origin+slope*(b-origin))
        error.append((b-a)*(r0*r0+r0*r1+r1*r1)/3)
    return dict(status='conditional_geometry',origin_x_m=origin,height_at_origin_m=at_origin,
                slope=slope,intercept=at_origin-slope*origin,profile_length_m=length,
                missing_length_m=0.,rms_residual_m=math.sqrt(max(0.,math.fsum(error)/length)))


def equivalent_point(x,z,plane):
    x,z=finite_number(x),finite_number(z)
    if plane['status']=='withheld': raise ValueError('Mean plane is unknown')
    a=plane['slope'];norm=math.hypot(1,a)
    height=(z-(plane['height_at_origin_m']+a*(x-plane['origin_x_m'])))/norm
    foot=(x+a*height/norm,z-height/norm)
    clipped=max(0.,height)
    equivalent=(foot[0]-a*clipped/norm,foot[1]+clipped/norm)
    mirror=(foot[0]+a*clipped/norm,foot[1]-clipped/norm)
    return dict(original=[x,z],signed_height_m=height,equivalent_height_m=clipped,
                projection=list(foot),equivalent_point=list(equivalent),mirror_point=list(mirror),
                negative_equivalent_height_clipped=height<0)


@cached_geometry
def plane_frame(plane, source, receiver):
    if plane['status']=='withheld':return dict(status='withheld',reason='missing_terrain_support',projected_distance_m=None)
    if source is None or receiver is None or source[1] is None or receiver[1] is None:
        return dict(status='withheld',reason='unknown_endpoint_elevation',projected_distance_m=None)
    s=equivalent_point(*source,plane);r=equivalent_point(*receiver,plane)
    a=plane['slope']
    dp=(r['original'][0]-s['original'][0]+a*(r['original'][1]-s['original'][1]))/math.hypot(1,a)
    if dp<=0:raise ValueError('Projected source/receiver order is nonpositive')
    return dict(status='conditional_geometry',source=s,receiver=r,projected_distance_m=dp,
                direct_distance_m=math.dist(source,receiver))


@cached_geometry
def upper_path(points,source,receiver):
    """Upper convex polyline above supplied terrain and endpoints; geometry only."""
    linear_segments(points)
    if (any(z is None for _,z in points) or source is None or receiver is None
            or source[1] is None or receiver[1] is None):
        return dict(status='withheld',reason='unknown_terrain_or_endpoints',path=None,path_difference_m=None)
    s=tuple(finite_number(v) for v in source);r=tuple(finite_number(v) for v in receiver)
    if s[0]!=points[0][0] or r[0]!=points[-1][0] or s[1]<points[0][1] or r[1]<points[-1][1]:
        raise ValueError('Endpoints must be at profile ends and not beneath local terrain')
    hull=[]
    for p in [s]+[tuple(p) for p in points[1:-1]]+[r]:
        while len(hull)>=2:
            a,b=hull[-2:]
            cross=(b[0]-a[0])*(p[1]-b[1])-(b[1]-a[1])*(p[0]-b[0])
            if cross<0:break
            hull.pop()
        hull.append(p)
    direct=math.dist(s,r)
    length=math.fsum(math.dist(a,b) for a,b in zip(hull,hull[1:]))
    clearances=[s[1]+(r[1]-s[1])*(x-s[0])/(r[0]-s[0])-z for x,z in points]
    return dict(status='conditional_geometry',path=[list(p) for p in hull],edge_count=len(hull)-2,
                direct_distance_m=direct,path_distance_m=length,path_difference_m=max(0.,length-direct),
                minimum_vertical_clearance_m=min(clearances),direct_ray_blocked=min(clearances)<0,
                touches_terrain=min(clearances)==0,noise_attenuation_db=None,
                meaning='Upper profile geometry is not a qualified diffraction, clear-path Rayleigh or finite-width terrain solver.')


def split_planes(points,edge_x,source,receiver):
    segments=linear_segments(points)
    x=finite_number(edge_x)
    if not points[0][0]<x<points[-1][0]:raise ValueError('Split must be strictly inside the path')
    left,right=[],[]
    for a,b,u,v in segments:
        if u is None or v is None:
            return dict(status='withheld',reason='missing_terrain_support',source_plane=None,receiver_plane=None)
        def segment(lo,hi):
            return (lo,hi,u+(v-u)*(lo-a)/(b-a),u+(v-u)*(hi-a)/(b-a))
        if a<x:left.append(segment(a,min(x,b)))
        if b>x:right.append(segment(max(a,x),b))
    edge_z=left[-1][3]
    source_plane,receiver_plane=mean_plane(left),mean_plane(right)
    edge=[x,edge_z]
    source_frame=plane_frame(source_plane,source,edge)
    receiver_frame=plane_frame(receiver_plane,edge,receiver)
    return dict(status='conditional_geometry',edge=edge,source_plane=source_plane,receiver_plane=receiver_plane,
                source_frame=source_frame,receiver_frame=receiver_frame,noise_attenuation_db=None)


def planar_ground_check(points,source,receiver,frequency_hz,g):
    """Research ground-only result for an explicitly synthetic planar scene.

    Nonplanar terrain needs a qualified edge/Rayleigh and boundary calculation;
    it is not silently flattened. A geometric clear ray alone is insufficient.
    """
    plane=mean_plane(linear_segments(points))
    if plane['status']=='withheld':return dict(status='withheld',reason='unknown_terrain',boundary_db=None)
    if source is None or receiver is None or source[1] is None or receiver[1] is None:
        return dict(status='withheld',reason='unknown_endpoint_elevation',boundary_db=None)
    upper_path(points,source,receiver)  # Reject physical endpoints beneath local terrain.
    if g is None:return dict(status='withheld',reason='unknown_ground',boundary_db=None)
    if plane['rms_residual_m']>1e-10:
        return dict(status='withheld',reason='nonplanar_terrain_requires_qualified_diffraction_selector',boundary_db=None)
    f=positive(frequency_hz)
    if not 63<=f<=4000:
        return dict(status='withheld',reason='outside_checked_JRC2012_frequency_scope',boundary_db=None)
    frame=plane_frame(plane,source,receiver)
    value=ground_homogeneous(f,frame['projected_distance_m'],frame['source']['equivalent_height_m'],
                            frame['receiver']['equivalent_height_m'],g)
    return dict(status='conditional_synthetic_research',mean_plane=plane,frame=frame,boundary_db=value,
                regulatory_implementation_qualified=False,annual_noise_prediction=None)


def native_intervals(start,end,bounds,resolution=2.):
    """Exact intersections of a straight BNG path with native cell boundaries.

    Returns original containing-cell ownership and represented path length.
    Raster heights/masks are looked up separately; no interpolation or smoothing.
    """
    x0,y0=(finite_number(v) for v in start);x1,y1=(finite_number(v) for v in end)
    w,s,e,n=(finite_number(v) for v in bounds);res=positive(resolution)
    if w>=e or s>=n:raise ValueError('Invalid native extent')
    width=(e-w)/res;height=(n-s)/res
    if not width.is_integer() or not height.is_integer():raise ValueError('Nonintegral native grid')
    length=positive(math.hypot(x1-x0,y1-y0))
    cuts=[0.,1.]
    for v0,v1,origin,count in ((x0,x1,w,int(width)),(y0,y1,s,int(height))):
        if v0==v1:continue
        # Include outer grid boundaries so outside support retains its own interval.
        lo=max(0,math.ceil((min(v0,v1)-origin)/res));hi=min(count,math.floor((max(v0,v1)-origin)/res))
        if hi-lo+1>MAX_SEGMENTS:raise ValueError('Native crossing budget exceeded')
        cuts.extend(t for k in range(lo,hi+1) if 0<(t:=(origin+k*res-v0)/(v1-v0))<1)
    cuts=sorted(set(cuts))
    if len(cuts)>MAX_SEGMENTS+1:raise ValueError('Native interval budget exceeded')
    result=[]
    for a,b in zip(cuts,cuts[1:]):
        if b==a:continue
        x=x0+(a+b)/2*(x1-x0);y=y0+(a+b)/2*(y1-y0)
        inside=w<=x<e and s<y<=n
        result.append(dict(start_m=a*length,end_m=b*length,represented_length_m=(b-a)*length,
            easting=x,northing=y,row=math.floor((n-y)/res) if inside else None,
            column=math.floor((x-w)/res) if inside else None))
    return result
