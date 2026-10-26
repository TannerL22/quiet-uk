"""Independent circular visibility paths, quadrature ground and decimal air.

Imports prior independent references only, never the evaluated weather modules
or input builders. Enumerates admissible curved paths instead of a hull stack.
"""
import argparse
from collections import Counter
from decimal import Decimal,localcontext
import hashlib
import importlib.util
import itertools
import math
from pathlib import Path
import shutil

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('sightline_reference',ROOT/'scripts/71_verify_sightline_checks.py')
prior=importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
geometry,ground=prior.geometry,prior.ground_reference
read,file_hash,json_bytes,close=prior.read,prior.file_hash,prior.json_bytes,prior.close
BANDS=(63,125,250,500,1000,2000,4000,8000)
TESTS=('TC01','TC02','TC03','TC04','TC05','TC06','TC07','TC10','TC11','TC12','TC13','TC14','TC15','TC16','TC21','TC23')
FIELDS={'main_diffraction_uncapped_db':'expectedDeltaDiffSR','source_ground_db':'expectedAGroundSO',
    'receiver_ground_db':'expectedAGroundOR','source_image_diffraction_db':'expectedDeltaDiffSPrimeR','receiver_image_diffraction_db':'expectedDeltaDiffSRPrime',
    'source_ground_coupling_db':'expectedDeltaGroundSO','receiver_ground_coupling_db':'expectedDeltaGroundOR'}


def arc(chord,radius):
    if chord>2*radius: raise ValueError('diameter')
    # Integrate the circular line element along its chord-centred coordinate.
    return geometry.quad(lambda u:1/math.sqrt(1-(u/radius)**2),-chord/2,chord/2,epsabs=1e-10)[0]


def leg(a,b,radius):
    a,b=np.array(a),np.array(b);v=b-a;length=float(np.linalg.norm(v));unit=v/length
    if length>=2*radius or v[0]<=0: raise ValueError('diameter/order')
    midpoint=(a+b)/2;normal=np.array([-unit[1],unit[0]])
    center=midpoint-normal*math.sqrt(radius**2-length**2/4)
    # Tangents follow directly from the radii, without chord-angle +/- asin.
    ta=np.array([-(a-center)[1],(a-center)[0]])
    tb=np.array([-(b-center)[1],(b-center)[0]])
    ta,tb=-ta,-tb
    if ta[0]<=0 or tb[0]<=0: raise ValueError('not a graph')
    return center,math.atan2(ta[1],ta[0]),math.atan2(tb[1],tb[0])


def envelope(candidates,s,r,radius):
    nodes=[list(s),*candidates,list(r)];n=len(nodes)
    assert n<=16,'Independent exhaustive research budget exceeded'
    # All required obstacle support must admit a monotonic adjacent minor arc.
    # This is the declared bounded geometry domain, not an impossibility claim
    # about other types of ray or arbitrary obstacle heights.
    for a,b in zip(nodes,nodes[1:]): leg(a,b,radius)
    valid=[]
    for mask in range(1<<(n-2)):
        indexes=[0]+[i for i in range(1,n-1) if mask>>(i-1)&1]+[n-1]
        length=0.;arrival=None;allowed=True
        for ia,ib in zip(indexes,indexes[1:]):
            a,b=nodes[ia],nodes[ib]
            try: center,departure,end=leg(a,b,radius)
            except ValueError: allowed=False;break
            if arrival is not None and arrival-departure<=1e-12: allowed=False;break
            for p in nodes[ia+1:ib]:
                y=center[1]+math.sqrt(max(0.,radius**2-(p[0]-center[0])**2))
                if p[1]>y+1e-9: allowed=False;break
            if not allowed: break
            arrival=end;length+=arc(float(np.linalg.norm(np.array(b)-a)),radius)
        if allowed: valid.append((length,indexes))
    if not valid: raise ValueError('no bounded circular path')
    _,indexes=min(valid)
    return [nodes[i] for i in indexes][1:-1]


def curved(s,r,edges):
    s,r=np.array(s,dtype=float),np.array(r,dtype=float)
    distance=float(np.linalg.norm(r-s));radius=max(1000.,8*distance)
    path=[s,*[np.array(p,dtype=float) for p in edges],r]
    lengths=[arc(float(np.linalg.norm(b-a)),radius) for a,b in zip(path,path[1:])]
    base=arc(distance,radius);delta=sum(lengths)-base
    outside=len(edges)==1 and not min(s[0],r[0])<edges[0][0]<max(s[0],r[0])
    if len(edges)==1 and not outside:
        e=np.array(edges[0]);cross=float(np.linalg.det(np.column_stack((r-s,e-s))))
        if cross<=0:
            a=s+(r-s)*(e[0]-s[0])/(r[0]-s[0])
            delta=2*(arc(float(np.linalg.norm(a-s)),radius)+arc(float(np.linalg.norm(r-a)),radius))-sum(lengths)-base
    return dict(path=[list(p) for p in path],radius_m=radius,direct_distance_m=distance,direct_arc_m=base,
        curved_segment_lengths_m=lengths,path_distance_m=sum(lengths),delta_m=delta,inter_edge_path_m=sum(lengths[1:-1]),single_edge_outside_endpoint_span=outside)


def favourable_ground(f,frame,g,sg,receiver=False):
    d=frame['projected_distance_m'];s=frame['source']['equivalent_height_m'];r=frame['receiver']['equivalent_height_m']
    ratio=min(1.,d/(30*(s+r))) if s+r else 1.
    gm=g if receiver else ratio*g+(1-ratio)*sg
    floor=-3*(1-gm)*(3-2*min(1.,30*(s+r)/d))
    result=dict(g_w=g,g_m=gm,lower_bound_db=floor)
    if g==0: return dict(attenuation_db=floor,modified_source_height_m=None,modified_receiver_height_m=None,**result)
    if s+r==0: return dict(attenuation_db=None,reason='zero_height_sum_for_favourable_ground',**result)
    heights=np.array([s,r]);modified=heights+1e-4*(d*heights/(s+r))**2+.006*d/(s+r)
    k=2*math.pi*f/340
    w=.0185*f**2.5*g**2.6/(f**1.5*g**2.6+1300*f**.75*g**1.3+1160000)
    cf=d*(1+3*w*d*math.exp(-math.sqrt(w*d)))/(1+w*d)
    terms=modified**2-math.sqrt(2*cf/k)*modified+cf/k
    value=max(-10*math.log10(4*k*k/d**2*float(np.prod(terms))),floor)
    return dict(attenuation_db=value,modified_source_height_m=float(modified[0]),modified_receiver_height_m=float(modified[1]),w=w,cf_m=cf,**result)


def independent(scene,f,g=None):
    if scene.get('conditions','favourable')!='favourable': return dict(reason='unsupported_propagation_conditions')
    if scene['scene_kind'] not in ('synthetic','published_reference'): return dict(reason='real_area_scene_not_qualified')
    points,s,r,roofs=scene['points'],scene['source'],scene['receiver'],scene['obstacles']
    if any(z is None for _,z in points): return dict(reason='unknown_terrain_support')
    if s is None or r is None or s[1] is None or r[1] is None: return dict(reason='unknown_endpoint_elevation')
    gs=scene.get('ground_segments',[[points[0][0],points[-1][0],g]]);sg=scene['source_g']
    if any(v is None for _,_,v in gs) or sg is None: return dict(reason='unknown_acoustic_ground')
    if scene['opaque'] is None or scene['infinite_lateral_extent'] is None: return dict(reason='unknown_obstacle_geometry_or_properties')
    if not scene['opaque'] or not scene['infinite_lateral_extent']: return dict(reason='unsupported_transmission_or_finite_lateral_extent')
    if roofs is None or any(o['vertices'] is None or any(z is None for _,z in o['vertices']) or
        (len(o['vertices'])>1 and o.get('roof_g') is None) for o in roofs): return dict(reason='unknown_obstacle_geometry_or_roof_ground')
    surface,gs=prior.numerical_surface(points,gs,roofs)
    candidates=prior.numerical_candidates(points,roofs)
    common=dict(surface_segments=surface,surface_ground_segments=gs,physical_edge_candidates=candidates,
        common_plane=geometry.numerical_plane(surface,adaptive_check=True),source_g=sg,g_path=ground.ground_mean(gs,points[0][0],points[-1][0]))
    try: edges=envelope(candidates,s,r,max(1000.,8*float(np.linalg.norm(np.array(r)-s))))
    except ValueError: return dict(reason='unsupported_circular_envelope')
    blocked=bool(edges)

    def ground_only(branch,**details):
        frame=geometry.independent_frame(common['common_plane'],s,r)
        if frame['projected_distance_m']<=0: return dict(reason='nonpositive_common_projection')
        ag=favourable_ground(f,frame,common['g_path'],sg)
        if ag['attenuation_db'] is None: return dict(reason=ag['reason'])
        return dict(branch=branch,boundary_db=ag['attenuation_db'],common_frame=frame,ground_result=ag,**common,**details)

    if not candidates: return ground_only('ground_only_no_edges')
    if not blocked: edges=[max(candidates,key=lambda p:curved(s,r,[p])['delta_m'])]
    first,last=edges[0],edges[-1]
    ps,pr=[geometry.numerical_plane(prior.sliced_surface(surface,a,b),adaptive_check=True) for a,b in ((points[0][0],first[0]),(last[0],points[-1][0]))]
    sf,rf=geometry.independent_frame(ps,s,first),geometry.independent_frame(pr,last,r)
    if sf['projected_distance_m']<=0 or rf['projected_distance_m']<=0: return dict(reason='unsupported_curved_path_or_side_projection')
    sm,rm=sf['source']['mirror_point'],rf['receiver']['mirror_point']
    main=curved(s,r,edges);mirror=curved(sm,rm,[first]) if not blocked else None
    if blocked: selection=dict(use_diffraction=True,blocked=True)
    else:
        delta,dm=main['delta_m'],mirror['delta_m'];wave=340/f
        first_test,second_test=delta>-wave/20,delta+dm>wave/4
        selection=dict(use_diffraction=first_test and second_test,blocked=False,wavelength_m=wave,first_condition=first_test,
            rayleigh_condition=second_test,first_margin_m=delta+wave/20,rayleigh_margin_m=delta+dm-wave/4)
    details=dict(controlling_edges=edges,controlling_edge_count=len(edges),direct_arc_blocked=blocked,main_geometry=main,
        selection=selection,rayleigh_mirror_geometry=mirror,source_plane=ps,receiver_plane=pr,source_frame=sf,receiver_frame=rf,source_mirror=sm,receiver_mirror=rm)
    if not selection['use_diffraction']: return ground_only('ground_only_clear',**details)
    sb,rb=sf['source']['signed_height_m']<0,rf['receiver']['signed_height_m']<0
    si_path=main if sb else curved(sm,r,edges);ri_path=main if rb else curved(s,rm,edges)
    gso,gor=ground.ground_mean(gs,points[0][0],first[0]),ground.ground_mean(gs,last[0],points[-1][0])
    so,orr=favourable_ground(f,sf,gso,sg),favourable_ground(f,rf,gor,sg,True)
    if so['attenuation_db'] is None or orr['attenuation_db'] is None: return dict(reason='zero_height_sum_for_favourable_ground')

    def coefficient(e): return (1+(5*340/f/e)**2)/(1/3+(5*340/f/e)**2) if e>.3 else 1.
    def pure(p):
        q=3+40*coefficient(p['inter_edge_path_m'])*p['delta_m']/(340/f)
        return 10*math.log10(q) if q>1 else 0.
    main_db,si,ri=pure(main),pure(si_path),pure(ri_path)
    def coupling(ag,image,below):
        if below: return ag
        weight=10**((main_db-image)/20)
        amplitude=(1-weight)+weight*10**(-ag/20)
        return -20*math.log10(amplitude) if amplitude>0 else None
    sc,rc=coupling(so['attenuation_db'],si,sb),coupling(orr['attenuation_db'],ri,rb)
    if sc is None or rc is None: return dict(reason='nonpositive_ground_diffraction_coupling')
    return dict(branch='diffraction_including_ground',boundary_db=min(main_db,25)+sc+rc,
        main_diffraction_uncapped_db=main_db,main_diffraction_db=min(main_db,25),source_image_diffraction_db=si,receiver_image_diffraction_db=ri,
        source_ground_db=so['attenuation_db'],receiver_ground_db=orr['attenuation_db'],source_ground_coupling_db=sc,receiver_ground_coupling_db=rc,
        source_ground_result=so,receiver_ground_result=orr,source_image_geometry=si_path,receiver_image_geometry=ri_path,
        source_side_g=gso,receiver_side_g=gor,multiple_diffraction_coefficient=coefficient(main['inter_edge_path_m']),
        source_below_mean_plane_rule=sb,receiver_below_mean_plane_rule=rb,separate_ground_db=0.,**common,**details)


def absorption(f,air):
    if any(air.get(k) is None for k in ('temperature_c','relative_humidity_percent','pressure_kpa','uniform_air')): return dict(reason='unknown_absorption_air')
    if not air['uniform_air']: return dict(reason='inhomogeneous_absorption_air')
    t,rh,p=air['temperature_c'],air['relative_humidity_percent'],air['pressure_kpa']
    if not (50<=f<=10000 and -20<=t<=50 and 10<=rh<=100 and 50<=p<=120): return dict(reason='outside_bounded_absorption_domain')
    with localcontext() as context:
        context.prec=50
        D=lambda v:Decimal(str(v))
        power=lambda x,y:(x.ln()*D(y)).exp()
        temperature=D(t)+D('273.15');ratio=temperature/D('293.15');pr=D(p)/D('101.325')
        h=D(rh)/pr*power(D(10),D('4.6151')-D('6.8346')*power(D('273.16')/temperature,D('1.261')))
        fo=pr*(D(24)+D(40400)*h*(D('.02')+h)/(D('.391')+h))
        fn=pr*power(ratio,D('-.5'))*(D(9)+D(280)*h*(-D('4.17')*(power(ratio,-D(1)/D(3))-D(1))).exp())
        square=D(f)**2
        classical=D('1.84e-11')/pr*ratio.sqrt()
        oxygen=D('.01275')*(-D('2239.1')/temperature).exp()*fo/(fo**2+square)
        nitrogen=D('.1068')*(-D('3352')/temperature).exp()*fn/(fn**2+square)
        alpha=D(8686)*square*(classical+power(ratio,D('-2.5'))*(oxygen+nitrogen))
    return dict(alpha_db_per_km=float(alpha),water_vapour_molar_percent=float(h),relaxation_oxygen_hz=float(fo),relaxation_nitrogen_hz=float(fn),
                standard_estimated_accuracy_percent=10 if .05<=h<=5 else 20)


def nested(a,b,errors):
    if isinstance(b,dict):
        for k,v in b.items():
            if k.endswith('_plane'): geometry.compare_plane(a[k],v)
            elif k.endswith('_frame'):
                frame=dict(v)
                for end in ('source','receiver'):
                    nested(a[k][end]['original'],v[end]['original'],errors)
                    frame[end]=dict(v[end],original=a[k][end]['original'])
                geometry.compare_frame(a[k],frame)
            else: nested(a[k],v,errors)
    elif isinstance(b,(list,tuple)):
        assert len(a)==len(b)
        for x,y in zip(a,b): nested(x,y,errors)
    elif isinstance(b,(str,bool)) or b is None: assert a==b,(a,b)
    else: close(a,b);errors.append(abs(a-b))


def compare(result,expected,counts,errors,db_errors):
    assert result['annual_noise_prediction'] is None and not result['regulatory_implementation_qualified']
    if 'reason' in expected:
        assert result['status']=='withheld' and result['reason']==expected['reason'],(result,expected)
        assert result['boundary_db'] is None;counts['withheld_boundary_results']+=1;return
    assert result['status']=='conditional_research'
    nested(result,expected,errors);counts['numeric_boundary_results']+=1
    db_errors.extend(abs(result[k]-v) for k,v in expected.items() if k.endswith('_db') and isinstance(v,(int,float)))
    if expected.get('direct_arc_blocked'): counts['blocked_curved_paths']+=1
    if expected['branch']=='diffraction_including_ground':
        counts['main_capped_results']+=int(expected['main_diffraction_uncapped_db']>25)


def combined(result,scene,f,air,probability,counts,errors,db_errors):
    fav=independent(scene,f)
    compare(result['favourable'],fav,counts,errors,db_errors)
    hom_scene=dict(scene);hom_scene.pop('conditions',None)
    hom=prior.independent(hom_scene,f)
    prior.compare(result['homogeneous'],hom,Counter(),[])
    actual_f=1000*10**(.3*(BANDS.index(f)-4)) if result['absorption_frequency_policy']=='exact_base10' else f
    atm=absorption(actual_f,air)
    if 'reason' in atm:
        assert result['status']=='withheld' and result['attenuation_db'] is None and result['absorption']['reason']==atm['reason']
        counts['withheld_absorption_guards']+=1;return
    assert result['status']=='conditional_research'
    nested(result['absorption'],atm,errors)
    distance=float(np.linalg.norm(np.array(scene['receiver'])-scene['source']))
    loss=atm['alpha_db_per_km']*distance/1000;divergence=20*math.log10(distance)+11
    h,ff=divergence+loss+hom['boundary_db'],divergence+loss+fav['boundary_db']
    if probability is None: mixed=None
    else: mixed=-10*math.log10((1-probability)*10**(-h/10)+probability*10**(-ff/10))
    nested(result,dict(direct_distance_m=distance,atmospheric_attenuation_db=loss,divergence_db=divergence,
        homogeneous_attenuation_db=h,favourable_attenuation_db=ff,attenuation_db=mixed,probability_favourable=probability),errors)
    db_errors.extend(abs(result[k]-v) for k,v in dict(atmospheric_attenuation_db=loss,divergence_db=divergence,
        homogeneous_attenuation_db=h,favourable_attenuation_db=ff,attenuation_db=mixed).items() if v is not None)
    assert result['annual_noise_prediction'] is None
    counts['numeric_paired_attenuation_results']+=1


def figure(output,benchmarks,grid):
    plt=geometry.plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes=plt.subplots(1,2,figsize=(12.5,5.2))
    for test in ('TC07','TC21','TC23'):
        rows=[b for b in benchmarks if b['case_id']==test]
        axes[0].plot(BANDS,[b['result']['homogeneous']['boundary_db'] for b in rows],'--',label=test+' homogeneous')
        axes[0].plot(BANDS,[b['result']['favourable']['boundary_db'] for b in rows],'-o',label=test+' favourable')
    axes[0].set_title('Same declared cross section, different propagation',loc='left')
    axes[0].set_ylabel('Boundary attenuation (dB)');axes[0].legend(fontsize=8)
    for t in (0,10,20):
        rows=[r for r in grid if r['air']['temperature_c']==t and r['air']['relative_humidity_percent']==70 and r['air']['pressure_kpa']==101.325 and r['policy']=='exact_base10']
        axes[1].plot(BANDS,[r['result']['alpha_db_per_km'] for r in rows],'o-',label=str(t)+' °C')
    axes[1].set_yscale('log');axes[1].set_title('Absorption with explicit temperature and air',loc='left')
    axes[1].set_ylabel('Atmospheric absorption (dB/km)');axes[1].legend(title='70% RH, 101.325 kPa',fontsize=8)
    for ax in axes:
        ax.set_xscale('log');ax.set_xticks(BANDS,[str(f) if f<1000 else str(f//1000)+'k' for f in BANDS]);ax.set_xlabel('Nominal octave band (Hz)')
    fig.text(.06,.025,'Conditional method checks, not measured or annual levels. Exact base10 centres used for absorption.\nEU Annex II / 2018 / 2021; ISO 9613-1 preview; original public NoiseModelling DIRECT expectations.',fontsize=9)
    fig.tight_layout(rect=[0,.09,1,1]);fig.savefig(output/'weather_checks.png',dpi=170);plt.close(fig)


def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    input_sha=geometry.check_generation(inputs);report,protocol=read(inputs/'report.json'),read(inputs/'protocol.json')
    parents={n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    for n,p in parents.items(): assert geometry.check_generation(p)==report['parent_inventory_sha256'][n]
    sources=next(p for p in parents.values() if (p/'TC01_Direct.json').exists());manifest=read(sources/'manifest.json')
    for n,h in manifest['parent_inventory_sha256'].items(): assert geometry.check_generation(ROOT/'artifacts'/n)==h
    for n,h in report['code_sha256'].items(): assert file_hash(ROOT/n)==file_hash(inputs/'code'/n)==h
    old=read(parents['sightline_checks_v1']/'report.json')
    for n,h in old['code_sha256'].items(): assert file_hash(ROOT/n)==h
    scripts=['74_verify_weather_checks.py','71_verify_sightline_checks.py','68_verify_multiple_edge_checks.py','65_verify_terrain_boundary_checks.py','62_verify_terrain_profile_checks.py']
    script_sha={n:file_hash(ROOT/'scripts'/n) for n in scripts}
    old_review=read(parents['sightline_review_v1']/'verification.json')
    assert all(script_sha[n]==h for n,h in old_review['code_sha256'].items())
    cases={c['case_id']:c for c in protocol['synthetic_scenes']};assert len(cases)==51
    results=read(inputs/'synthetic_results.json')['results'];counts,errors,db_errors,alpha_errors=Counter(),[],[],[]
    keys={(c['case_id'],g,f) for c in cases.values() for g in ([None] if c.get('unknown_ground') or 'ground_segments' in c else protocol['ground_cases']) for f in BANDS}
    assert len(keys)==len(results)==1584 and {(r['case_id'],r['ground_case_g'],r['frequency_hz']) for r in results}==keys
    for r in results: compare(r,independent(cases[r['case_id']],r['frequency_hz'],r['ground_case_g']),counts,errors,db_errors)
    scenes={c['case_id']:prior.reference_fixture(sources,c) for c in protocol['reference_scenes']};assert set(scenes)==set(TESTS)
    benchmarks=read(inputs/'reference_results.json')['benchmarks'];assert len(benchmarks)==128
    assert {(b['case_id'],b['frequency_hz']) for b in benchmarks}=={(t,f) for t in TESTS for f in BANDS}
    text=(sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8');public_keys=set();public_errors=[]
    for b in benchmarks:
        test,f=b['case_id'],b['frequency_hz'];r=b['result'];combined(r,scenes[test],f,protocol['reference_air'],.5,counts,errors,db_errors)
        fields={'boundary_db':'expectedABoundaryF'}
        if r['favourable']['branch']=='diffraction_including_ground' and test!='TC15': fields.update({k:v+'F' for k,v in FIELDS.items()})
        expected_fields={('favourable',k,v) for k,v in fields.items()}|{('absorption','alpha_db_per_km','expectedAlphaAtm'),('combined','atmospheric_attenuation_db','expectedAAtm'),('combined','divergence_db','expectedADiv')}
        assert {(c['component'],c['field'],c['original_variable']) for c in b['comparisons']}==expected_fields
        assert len(b['comparisons'])==len(expected_fields)
        for c in b['comparisons']:
            actual=r[c['component']][c['field']] if c['component']!='combined' else r[c['field']]
            expected=ground.original_array(text,test,c['original_variable'])[BANDS.index(f)]
            tol=(.05 if test in ('TC12','TC14') else .03 if test=='TC13' else .02) if c['component']=='favourable' else .02
            assert c['expected']==expected and c['tolerance']==tol
            close(c['actual'],actual);close(c['error_db'],actual-expected);assert abs(actual-expected)<=tol
            public_keys.add((test,f,c['component'],c['field']));public_errors.append(abs(actual-expected))
        assert not b['external_engine_executed'] and b['source_commit']==manifest['commit']
    assert len(public_keys)==911;counts['public_scalar_comparisons']=len(public_keys)
    atm=read(inputs/'atmospheric_results.json');grid=atm['results']
    grid_keys={(t,rh,p,policy,f) for t,rh,p,policy,f in itertools.product([-20.,0.,10.,20.,50.],[10.,50.,70.,100.],[80.,101.325,120.],['nominal','exact_base10'],BANDS)}
    assert len(grid)==len(grid_keys)==960
    assert {(r['air']['temperature_c'],r['air']['relative_humidity_percent'],r['air']['pressure_kpa'],r['policy'],r['nominal_frequency_hz']) for r in grid}==grid_keys
    for r in grid:
        f=1000*10**(.3*(BANDS.index(r['nominal_frequency_hz'])-4)) if r['policy']=='exact_base10' else r['nominal_frequency_hz']
        expected=absorption(f,r['air']);assert r['result']['status']=='conditional_research';nested(r['result'],expected,errors);counts['independent_decimal_absorption_cases']+=1
        alpha_errors.append(abs(r['result']['alpha_db_per_km']-expected['alpha_db_per_km']))
    assert len(atm['guards'])==8
    for r in atm['guards']: combined(r['result'],scenes['TC01'],1000,r['air'],.5,counts,errors,db_errors)
    assert len(atm['meteorology_guards'])==6
    for r in atm['meteorology_guards']: combined(r['result'],scenes['TC01'],1000,protocol['reference_air'],r['probability'],counts,errors,db_errors)
    readiness=read(inputs/'real_path_readiness.json')['probes'];old_readiness=read(parents['sightline_checks_v1']/'real_path_readiness.json')['probes']
    assert len(readiness)==100 and {p['probe_id'] for p in readiness}=={p['probe_id'] for p in old_readiness}
    for p in readiness:
        old=next(q for q in old_readiness if q['probe_id']==p['probe_id'])
        for k,v in old.items(): assert p[k]==v
        assert p['qualified_absorption_air'] is None and p['qualified_directional_favourable_probability'] is None
    assert not report['eligible_for_absolute_noise_release'] and not report['full_current_method_qualified'] and not report['real_area_noise_validated']
    summary=report['summary'];assert summary['states']==dict(Counter(r['status'] for r in results))
    assert summary['branches']==dict(Counter(r['branch'] for r in results if r['status']=='conditional_research'))
    assert summary['reference_scalar_comparisons']==911 and summary['atmospheric_grid_cases']==960
    output.mkdir(parents=True);figure(output,benchmarks,grid)
    receipt=dict(status='passed_independent_weather_checks',input_inventory_sha256=input_sha,parent_inventory_sha256=report['parent_inventory_sha256'],
        source_parent_inventory_sha256=manifest['parent_inventory_sha256'],counts=dict(counts),maximum_numeric_difference_mixed_units=max(errors),
        maximum_independent_db_difference=max(db_errors),maximum_independent_alpha_difference_db_per_km=max(alpha_errors),
        methods=['Exhaustive circular visibility paths and downward tangent constraints','QUADPACK arc line integrals','Gauss/QUADPACK composite plane fits and vector mirrors',
            'Expanded favourable ground and energy/amplitude weighting','50-digit decimal ISO absorption','Independent original DIRECT expectation parsing'],
        code_sha256=script_sha,full_current_method_qualified=False,external_engine_executed=False,ISO_certification=False,real_area_noise_validated=False)
    (output/'verification.json').write_bytes(json_bytes(receipt))
    for n in scripts: shutil.copyfile(ROOT/'scripts'/n,output/n)
    assert geometry.check_generation(inputs)==input_sha
    for n,p in parents.items(): assert geometry.check_generation(p)==report['parent_inventory_sha256'][n]
    for n,h in report['code_sha256'].items(): assert file_hash(ROOT/n)==h
    assert all(file_hash(ROOT/'scripts'/n)==h for n,h in script_sha.items())
    files={p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity='weather-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity,files=files)))
    print(dict(generation_id=identity,counts=dict(counts),maximum_independent_db_difference=max(db_errors),maximum_independent_alpha_difference_db_per_km=max(alpha_errors)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs',default=str(ROOT/'artifacts/weather_checks_v2'))
    p.add_argument('--output',default=str(ROOT/'artifacts/weather_review_v2'))
    a=p.parse_args();run(a.inputs,a.output)
