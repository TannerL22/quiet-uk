"""Independently check selected physical chains and composite surface support.

No imports of evaluated propagation modules or the builder. Uses the sealed
independent quadrature/ground references, GEOS envelopes, NumPy interpolation
and vector distances, expanded coefficient and amplitude-sum expressions.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from shapely.geometry import LineString,Point

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prior_reference',ROOT/'scripts/68_verify_multiple_edge_checks.py')
prior = importlib.util.module_from_spec(spec);spec.loader.exec_module(prior)
ground_reference = prior.prior
geometry = prior.geometry
file_hash,json_bytes,read,close = geometry.file_hash,geometry.json_bytes,geometry.read,geometry.close
BANDS = (63,125,250,500,1000,2000,4000,8000)
TESTS = ('TC05','TC06','TC07','TC10','TC11','TC12','TC13','TC14','TC15','TC16','TC21','TC23')


def numerical_surface(points,ground,roofs):
    cuts = sorted(set(np.array(points)[:,0])|{v for a,b,_ in ground for v in (a,b)}
                  |{x for o in roofs for x,_ in o['vertices']})
    surface,gs = [],[]
    terrain_x,terrain_z = np.array(points).T
    for a,b in zip(cuts,cuts[1:]):
        probe = Point((a+b)/2,0)
        roof = next((o for o in roofs if len(o['vertices'])>1 and
            LineString([(o['vertices'][0][0],0),(o['vertices'][-1][0],0)]).distance(probe)<1e-12),None)
        # The point must be inside the closed roof domain; a tiny distance from
        # an endpoint alone does not establish positive-length support.
        if roof is not None and not roof['vertices'][0][0]<=a<b<=roof['vertices'][-1][0]: roof=None
        if roof is None:
            u,v = np.interp([a,b],terrain_x,terrain_z)
            g = next(g for lo,hi,g in ground if lo<=probe.x<hi)
        else:
            x,z = np.array(roof['vertices']).T
            u,v = np.interp([a,b],x,z);g = roof['roof_g']
        surface.append([float(a),float(b),float(u),float(v)]);gs.append([float(a),float(b),g])
    return surface,gs


def numerical_candidates(points,roofs):
    def corners(vertices):
        return [list(b) for a,b,c in zip(vertices,vertices[1:],vertices[2:])
            if math.atan2(b[1]-a[1],b[0]-a[0])-math.atan2(c[1]-b[1],c[0]-b[0])>1e-12]
    values = [p for p in corners(points) if not any(len(o['vertices'])>1 and
        o['vertices'][0][0]<=p[0]<=o['vertices'][-1][0] for o in roofs)]
    for o in roofs:
        vertices = o['vertices']
        values.extend(vertices if len(vertices)==1 else [vertices[0],*corners(vertices),vertices[-1]])
    xs = sorted({x for x,_ in values})
    return [[x,max(z for xx,z in values if xx==x)] for x in xs]


def numerical_selected_chain(s,r,edges):
    result = prior.numerical_chain(s,r,edges)
    outside = len(edges)==1 and not min(s[0],r[0])<edges[0][0]<max(s[0],r[0])
    if len(edges)==1 and not outside:
        orientation = float(np.linalg.det(np.column_stack((np.array(r)-s,np.array(edges[0])-s))))
        result['delta_m'] *= 1 if orientation>=0 else -1
    result['single_edge_outside_endpoint_span'] = outside
    return result


def sliced_surface(surface,start,end):
    result = []
    for a,b,u,v in surface:
        lo,hi = max(a,start),min(b,end)
        if hi>lo:
            z = np.interp([lo,hi],[a,b],[u,v])
            result.append([lo,hi,float(z[0]),float(z[1])])
    return result


def independent(scene,f,ground=None):
    if scene.get('conditions','homogeneous')!='homogeneous': return dict(reason='unsupported_propagation_conditions')
    if scene['scene_kind'] not in ('synthetic','published_reference'): return dict(reason='real_area_scene_not_qualified')
    points,s,r,roofs = scene['points'],scene['source'],scene['receiver'],scene['obstacles']
    if any(z is None for _,z in points): return dict(reason='unknown_terrain_support')
    if s is None or r is None or s[1] is None or r[1] is None: return dict(reason='unknown_endpoint_elevation')
    gs = scene.get('ground_segments',[(points[0][0],points[-1][0],ground)])
    source_g = scene['source_g']
    if any(g is None for _,_,g in gs) or source_g is None: return dict(reason='unknown_acoustic_ground')
    if scene['opaque'] is None or scene['infinite_lateral_extent'] is None: return dict(reason='unknown_obstacle_geometry_or_properties')
    if not scene['opaque'] or not scene['infinite_lateral_extent']: return dict(reason='unsupported_transmission_or_finite_lateral_extent')
    if roofs is None or any(o['vertices'] is None or any(z is None for _,z in o['vertices']) or
        (len(o['vertices'])>1 and o.get('roof_g') is None) for o in roofs):
        return dict(reason='unknown_obstacle_geometry_or_roof_ground')
    surface,surface_g = numerical_surface(points,gs,roofs)
    candidates = numerical_candidates(points,roofs)
    edges = prior.geos_chain(candidates,s,r)[1:-1]
    blocked = bool(edges)
    plane = geometry.numerical_plane(surface,adaptive_check=True)
    g = ground_reference.ground_mean(surface_g,points[0][0],points[-1][0])
    result = dict(surface_segments=surface,surface_ground_segments=surface_g,physical_edge_candidates=candidates,
                  common_plane=plane,g_path=g,source_g=source_g,obstacle_widths_m=[o['vertices'][-1][0]-o['vertices'][0][0] for o in roofs])

    def ground_only(branch):
        frame = geometry.independent_frame(plane,s,r)
        if frame['projected_distance_m']<=0: return dict(reason='nonpositive_common_projection')
        return dict(**result,branch=branch,common_frame=frame,boundary_db=ground_reference.reference_ground(f,frame,g,source_g))

    if not candidates: return ground_only('ground_only_no_edges')
    if not blocked:
        differences = np.array([numerical_selected_chain(s,r,[p])['delta_m'] for p in candidates])
        edges = [candidates[int(np.argmax(differences))]]
    first,last = edges[0],edges[-1]
    ps,pr = [geometry.numerical_plane(sliced_surface(surface,a,b),adaptive_check=True)
        for a,b in ((points[0][0],first[0]),(last[0],points[-1][0]))]
    sf,rf = geometry.independent_frame(ps,s,first),geometry.independent_frame(pr,last,r)
    if sf['projected_distance_m']<=0 or rf['projected_distance_m']<=0: return dict(reason='nonpositive_side_projection')
    sm,rm = sf['source']['mirror_point'],rf['receiver']['mirror_point']
    main_geometry = numerical_selected_chain(s,r,edges)
    if blocked: selection = dict(use_diffraction=True,blocked=True)
    else:
        delta = main_geometry['delta_m'];mirror = numerical_selected_chain(sm,rm,[first])['delta_m']
        # Rayleigh's signed single-edge expression uses the three named points
        # only, even when the virtual endpoint projects before D.
        mirror = ground_reference.numerical_delta(sm,rm,first)[0]
        wave = 340/f;first_test,second_test = delta>-wave/20,delta+mirror>wave/4
        selection = dict(use_diffraction=bool(first_test and second_test),blocked=False,wavelength_m=wave,
            first_condition=bool(first_test),rayleigh_condition=bool(second_test),first_margin_m=delta+wave/20,rayleigh_margin_m=delta+mirror-wave/4)
    result.update(controlling_edges=edges,controlling_edge_count=len(edges),direct_ray_blocked=blocked,
        main_geometry=main_geometry,selection=selection,source_plane=ps,receiver_plane=pr,source_frame=sf,receiver_frame=rf,
        source_mirror=sm,receiver_mirror=rm,rayleigh_mirror_edge=first if not blocked else None)
    if not selection['use_diffraction']: return ground_only('ground_only_clear')
    sb,rb = sf['source']['signed_height_m']<0,rf['receiver']['signed_height_m']<0
    sg_path = main_geometry if sb else numerical_selected_chain(sm,r,edges)
    rg_path = main_geometry if rb else numerical_selected_chain(s,rm,edges)
    source_hull = prior.geos_chain([p for p in candidates if sm[0]<p[0]<r[0]],sm,r)[1:-1]
    receiver_hull = prior.geos_chain([p for p in candidates if s[0]<p[0]<rm[0]],s,rm)[1:-1]
    source_factor = ground_reference.ground_mean(surface_g,points[0][0],first[0])
    receiver_factor = ground_reference.ground_mean(surface_g,last[0],points[-1][0])
    sg = ground_reference.reference_ground(f,sf,source_factor,source_g)
    rg = ground_reference.reference_ground(f,rf,receiver_factor,source_g,receiver_side=True)
    wave = 340/f

    def coefficient(e):
        return (1+(5*wave/e)**2)/(1/3+(5*wave/e)**2) if e>.3 else 1.

    def pure(p):
        argument = 3+40*coefficient(p['inter_edge_path_m'])*p['delta_m']/wave
        return max(0.,10*math.log10(argument)) if argument>=1 else 0.

    main = pure(main_geometry);si,ri = main if sb else pure(sg_path),main if rb else pure(rg_path)

    def coupling(ag,image,below):
        if below: return ag
        weight = 10**((main-image)/20)
        amplitude = (1-weight)+weight*10**(-ag/20)
        return -20*math.log10(amplitude) if amplitude>0 else None

    sc,rc = coupling(sg,si,sb),coupling(rg,ri,rb)
    if sc is None or rc is None: return dict(reason='nonpositive_ground_diffraction_coupling')
    heights = sf['source']['equivalent_height_m']+sf['receiver']['equivalent_height_m']
    fraction = min(1.,sf['projected_distance_m']/(30*heights)) if heights else 1.
    result.update(branch='diffraction_including_ground',source_image_geometry=sg_path,receiver_image_geometry=rg_path,
        source_virtual_hull_edges=source_hull,receiver_virtual_hull_edges=receiver_hull,
        source_virtual_hull_changes_edges=source_hull!=edges,receiver_virtual_hull_changes_edges=receiver_hull!=edges,
        multiple_diffraction_coefficient=coefficient(main_geometry['inter_edge_path_m']),source_side_g=source_factor,receiver_side_g=receiver_factor,
        source_side_corrected_g=fraction*source_factor+(1-fraction)*source_g,main_diffraction_uncapped_db=main,main_diffraction_db=min(main,25.),
        source_image_diffraction_db=si,receiver_image_diffraction_db=ri,source_ground_db=sg,receiver_ground_db=rg,
        source_ground_coupling_db=sc,receiver_ground_coupling_db=rc,source_below_mean_plane_rule=sb,receiver_below_mean_plane_rule=rb,
        separate_ground_db=0.,boundary_db=min(main,25.)+sc+rc)
    return result


def compare(actual,expected,counts,errors):
    if 'reason' in expected:
        assert actual['status']=='withheld' and actual['reason']==expected['reason'],(actual,expected)
        assert actual['boundary_db'] is None and actual['annual_noise_prediction'] is None
        counts['withheld_results_checked']+=1;return
    assert actual['status']=='conditional_research' and actual['branch']==expected['branch']
    assert actual['annual_noise_prediction'] is None and not actual['regulatory_implementation_qualified']

    def nested(a,b):
        if isinstance(b,dict):
            for k,v in b.items(): nested(a[k],v)
        elif isinstance(b,(tuple,list)):
            assert len(a)==len(b)
            for x,y in zip(a,b): nested(x,y)
        elif b is None or isinstance(b,(str,bool)): assert a==b,(a,b)
        else: close(a,b)

    for key,value in expected.items():
        if key.endswith('_plane'): geometry.compare_plane(actual[key],value)
        elif key.endswith('_frame'):
            frame = dict(value)
            for end in ('source','receiver'):
                nested(actual[key][end]['original'],value[end]['original'])
                frame[end] = dict(value[end],original=actual[key][end]['original'])
            geometry.compare_frame(actual[key],frame)
        else:
            nested(actual[key],value)
            if key.endswith('_db'): errors.append(abs(actual[key]-value))
    counts['numeric_results_checked']+=1
    counts['clear_numeric_results_checked']+=int(not expected.get('direct_ray_blocked',False))
    if expected['branch']=='diffraction_including_ground':
        counts['changed_virtual_hull_cases_checked']+=int(expected['source_virtual_hull_changes_edges'] or expected['receiver_virtual_hull_changes_edges'])
        counts['returning_image_leg_cases_checked']+=int(expected['source_image_geometry']['single_edge_outside_endpoint_span'] or expected['receiver_image_geometry']['single_edge_outside_endpoint_span'])
        counts['main_cap_cases_checked']+=int(expected['main_diffraction_uncapped_db']>25)
        counts['source_below_plane_cases_checked']+=int(expected['source_below_mean_plane_rule'])
        counts['receiver_below_plane_cases_checked']+=int(expected['receiver_below_mean_plane_rule'])


def reference_fixture(sources,scene):
    raw = read(sources/(scene['case_id']+'_Direct.json'))['cutPoints']
    s,r = raw[0]['coordinate'],raw[-1]['coordinate']
    line = LineString([(s['x'],s['y']),(r['x'],r['y'])]);xs = []
    for c in raw:
        p = Point(c['coordinate']['x'],c['coordinate']['y']);assert line.distance(p)<1e-9;xs.append(line.project(p))
    support,intervals,obstacles = {},[],[];enter = None
    for i,(x,c) in enumerate(zip(xs,raw)):
        if x in support: assert support[x]==c['zGround']
        support[x] = c['zGround']
        if i+1<len(raw) and xs[i+1]>x: intervals.append([x,xs[i+1],c['groundCoefficient']])
        if c['type']=='Wall':
            kind = c.get('intersectionType')
            if kind=='BUILDING_ENTER': assert enter is None;enter = [x,c['coordinate']['z']]
            elif kind=='BUILDING_EXIT':
                assert enter is not None;obstacles.append(dict(vertices=[enter,[x,c['coordinate']['z']]],roof_g=0.));enter = None
            else: obstacles.append(dict(vertices=[[x,c['coordinate']['z']]]))
    assert enter is None
    independent_scene = dict(scene,points=[[x,z] for x,z in sorted(support.items())],source=[0.,s['z']],receiver=[line.length,r['z']],
                             ground_segments=intervals,obstacles=obstacles,source_g=raw[0]['groundCoefficient'])
    for key in ('points','ground_segments'):
        assert len(scene[key])==len(independent_scene[key])
        for a,b in zip(scene[key],independent_scene[key]):
            for x,y in zip(a,b): close(x,y)
    assert len(scene['obstacles'])==len(obstacles)
    for a,b in zip(scene['obstacles'],obstacles):
        assert a.get('roof_g')==b.get('roof_g') and len(a['vertices'])==len(b['vertices'])
        for p,q in zip(a['vertices'],b['vertices']):
            for x,y in zip(p,q): close(x,y)
    for key in ('source','receiver'):
        for x,y in zip(scene[key],independent_scene[key]): close(x,y)
    assert scene['source_g']==independent_scene['source_g']
    return independent_scene


def figure(output,results,benchmarks,scene):
    plt = geometry.plt
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,axes = plt.subplots(2,2,figsize=(13,8.5))
    rows = [r for r in results if r['case_id']=='near-clear-roof' and r['ground_case_g']==.3]
    record = rows[0];ax = axes[0,0]
    for a,b,u,v in record['surface_segments']: ax.plot([a,b],[u,v],color='#7c6d57',lw=2)
    ax.fill_between([30,70],0,1.6,color='#e2dbcf',label='Declared roof')
    ax.plot([0,100],[1,4],'--',color='#9c3856',label='Clear direct ray')
    ax.scatter(*np.array(record['controlling_edges']).T,color='#216c83',s=55,label='Selected physical edge D')
    ax.set_title('Clear roof: select a real corner, not a sample point',loc='left')
    ax.set_xlabel('Horizontal chainage (m)');ax.set_ylabel('Elevation (m)');ax.set_ylim(0,5);ax.legend(fontsize=8,loc='upper left')
    ax = axes[0,1]
    ax.plot(BANDS,[r['boundary_db'] for r in rows],color='#216c83',lw=1.5)
    for branch,marker,label in [('ground_only_clear','o','Ground only'),('diffraction_including_ground','s','Diffraction including ground')]:
        chosen = [r for r in rows if r['branch']==branch]
        ax.scatter([r['frequency_hz'] for r in chosen],[r['boundary_db'] for r in chosen],marker=marker,s=50,label=label)
    ax.set_title('Same clear scene: the branch depends on frequency',loc='left');ax.set_ylabel('Boundary attenuation (dB)');ax.legend(fontsize=8,loc='upper left')
    ax = axes[1,0]
    for test in ('TC11','TC14','TC16','TC21'):
        records = [b for b in benchmarks if b['case_id']==test]
        ax.plot(BANDS,[b['result']['boundary_db'] for b in records],'o-',label=test)
    ax.set_title('Public DIRECT homogeneous boundary terms',loc='left');ax.set_ylabel('Boundary attenuation (dB)');ax.legend(fontsize=8,loc='upper left')
    for ax in (axes[0,1],axes[1,0]):
        ax.set_xscale('log');ax.set_xticks(BANDS,[str(f) if f<1000 else str(f//1000)+'k' for f in BANDS]);ax.set_xlabel('Nominal octave centre (Hz)')
    ax = axes[1,1]
    r = next(b['result'] for b in benchmarks if b['case_id']=='TC21')
    surface = r['surface_segments']
    for a,b,u,v in surface: ax.plot([a,b],[u,v],color='#7c6d57',lw=1.4)
    fixed = r['receiver_image_geometry']['path']
    wrong_hull = [scene['source']]+r['receiver_virtual_hull_edges']+[r['receiver_mirror']]
    ax.plot(*np.array(fixed).T,'o-',color='#216c83',label='Selected physical chain used for image term')
    ax.plot(*np.array(wrong_hull).T,'x--',color='#9c3856',label='Virtual convex hull (diagnostic only)')
    ax.set_xlim(135,196);ax.set_ylim(-15,16)
    ax.set_title('TC21: rebuilding the virtual hull adds different edges',loc='left')
    ax.set_xlabel('Horizontal chainage (m)');ax.set_ylabel('Elevation (m)');ax.legend(fontsize=7,loc='lower left')
    fig.text(.04,.025,'Conditional homogeneous cross-section checks; these are not measured or annual noise levels.\nEU Annex II / 2021 amendment; original public NoiseModelling fixtures retained under GPLv3.',fontsize=10)
    fig.tight_layout(rect=[0,.085,1,1]);fig.savefig(output/'sightline_checks.png',dpi=170);plt.close(fig)


def run(inputs,output):
    inputs,output = Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'): raise ValueError('Use a fresh workspace generation')
    script_names = ['71_verify_sightline_checks.py','68_verify_multiple_edge_checks.py','65_verify_terrain_boundary_checks.py','62_verify_terrain_profile_checks.py']
    script_sha = {n:file_hash(ROOT/'scripts'/n) for n in script_names}
    input_sha = geometry.check_generation(inputs)
    report,protocol = read(inputs/'report.json'),read(inputs/'protocol.json')
    parents = {n:ROOT/'artifacts'/n for n in report['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n,p in parents.items())
    sources = next(p for p in parents.values() if (p/'TC21_Direct.json').exists());manifest = read(sources/'manifest.json')
    source_parents = {n:ROOT/'artifacts'/n for n in manifest['parent_inventory_sha256']}
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n,p in source_parents.items())
    old = read(parents['multiple_edge_review_v1']/'verification.json')
    for n,h in old['code_sha256'].items(): assert script_sha[n]==h
    for n,h in report['code_sha256'].items(): assert file_hash(ROOT/n)==file_hash(inputs/'code'/n)==h
    cases = {c['case_id']:c for c in protocol['synthetic_scenes']}
    assert len(cases)==45 and tuple(protocol['bands_hz'])==BANDS
    expected_keys = {(c['case_id'],g,f) for c in cases.values()
        for g in ([None] if c.get('unknown_ground') or 'ground_segments' in c else protocol['ground_cases']) for f in BANDS}
    results = read(inputs/'synthetic_results.json')['results']
    assert len(results)==len(expected_keys)==1392
    assert {(r['case_id'],r['ground_case_g'],r['frequency_hz']) for r in results}==expected_keys
    counts,errors = Counter(),[]
    for r in results: compare(r,independent(cases[r['case_id']],r['frequency_hz'],r['ground_case_g']),counts,errors)
    benchmarks = read(inputs/'reference_results.json')['benchmarks']
    scenes = {c['case_id']:reference_fixture(sources,c) for c in protocol['reference_scenes']}
    assert set(scenes)==set(TESTS) and len(benchmarks)==96
    assert {(b['case_id'],b['frequency_hz']) for b in benchmarks}=={(test,f) for test in TESTS for f in BANDS}
    fields = {'main_diffraction_uncapped_db':'expectedDeltaDiffSR','source_ground_db':'expectedAGroundSO',
        'receiver_ground_db':'expectedAGroundOR','source_image_diffraction_db':'expectedDeltaDiffSPrimeR','receiver_image_diffraction_db':'expectedDeltaDiffSRPrime',
        'source_ground_coupling_db':'expectedDeltaGroundSO','receiver_ground_coupling_db':'expectedDeltaGroundOR'}
    text = (sources/'NoiseModelling-CnossosTests.java').read_text(encoding='utf-8');keys = set();max_public_error = 0.
    for b in benchmarks:
        test,f = b['case_id'],b['frequency_hz'];expected = independent(scenes[test],f)
        compare(b['result'],expected,counts,errors)
        assert not b['external_engine_executed'] and b['source_commit']==manifest['commit']
        expected_fields = {'boundary_db'}|(set(fields) if expected['branch']=='diffraction_including_ground' else set())
        assert len(b['comparisons'])==len(expected_fields) and {c['field'] for c in b['comparisons']}==expected_fields
        for c in b['comparisons']:
            variable = 'expectedABoundaryH' if c['field']=='boundary_db' else fields[c['field']]+('' if test=='TC06' else 'H')
            reference = ground_reference.original_array(text,test,variable)[BANDS.index(f)]
            tolerance = .05 if test in ('TC12','TC14') else .02
            assert c['expected']==reference and c['original_variable']==variable and c['tolerance_db']==tolerance
            close(c['actual'],b['result'][c['field']]);close(c['error_db'],c['actual']-reference)
            assert abs(expected[c['field']]-reference)<=tolerance
            keys.add((test,f,c['field']));max_public_error = max(max_public_error,abs(c['error_db']))
    assert len(keys)==614
    counts['public_scalar_comparisons_checked']=len(keys)
    assert any(b['result'].get('receiver_virtual_hull_changes_edges') for b in benchmarks if b['case_id']=='TC21')
    for b in benchmarks:
        if b['case_id'] in ('TC11','TC14'):
            assert b['result']['receiver_frame']['source']['signed_height_m']<0
            assert b['result']['receiver_frame']['source']['equivalent_height_m']==0
        if b['case_id']=='TC14': assert b['result']['receiver_image_geometry']['single_edge_outside_endpoint_span']
    readiness = read(inputs/'real_path_readiness.json')['probes']
    native = {p['probe_id']:p for p in read(parents['terrain_profile_checks_v2']/'native_terrain_profiles.json')['probes']}
    assert len(readiness)==100 and {p['probe_id'] for p in readiness}==set(native)
    for p in readiness:
        assert p['status']=='withheld' and p['boundary_db'] is None and p['noise_prediction'] is None and p['qualified_obstacle_cross_section'] is None
        for k in ('qualified_source_elevation_odn_m','qualified_ground_g'): assert p[k]==native[p['probe_id']][k] is None
        counts['real_paths_verified_withheld']+=1
    summary = report['summary']
    for key,actual in [('states',dict(Counter(r['status'] for r in results))),('branches',dict(Counter(r['branch'] for r in results if r['status']=='conditional_research'))),
        ('withheld_reasons',dict(Counter(r['reason'] for r in results if r['status']=='withheld')))]: assert summary[key]==actual
    assert summary['synthetic_scenes']==45 and summary['synthetic_results']==1392 and summary['reference_frequency_cases']==96
    assert summary['reference_scalar_comparisons']==614 and summary['real_paths_withheld']==100
    close(summary['maximum_reference_error_db'],max_public_error)
    assert not report['eligible_for_absolute_noise_release'] and not report['full_current_method_qualified'] and not report['external_engine_executed']
    output.mkdir(parents=True);figure(output,results,benchmarks,scenes['TC21'])
    receipt = dict(schema_version=1,status='passed_independent_sightline_checks',input_inventory_sha256=input_sha,
        parent_inventory_sha256=report['parent_inventory_sha256'],source_parent_inventory_sha256=manifest['parent_inventory_sha256'],counts=dict(counts),
        maximum_independent_db_difference=max(errors),maximum_public_error_db=max_public_error,public_tolerances_db=[.02,.05],
        methods=['GEOS world projection / physical and diagnostic virtual hulls','NumPy surface interpolation, oriented distances and chain lengths',
            'Gauss / QUADPACK fits and vector frames','Expanded ground / multiple coefficient and amplitude-sum coupling','Independent original DIRECT array extraction'],
        excluded_reference_geometry='TC11/14 positive heights for diffraction points below the mean plane; primary null-height rule retained.',
        full_current_method_qualified=False,external_engine_executed=False,ISO_certification=False,real_area_noise_validated=False,
        code_sha256=script_sha,runtime=dict(python=sys.version,numpy=np.__version__,scipy=geometry.scipy.__version__,shapely=geometry.shapely.__version__))
    (output/'verification.json').write_bytes(json_bytes(receipt))
    for n in script_names: shutil.copyfile(ROOT/'scripts'/n,output/n)
    assert all(file_hash(ROOT/'scripts'/n)==h for n,h in script_sha.items())
    assert geometry.check_generation(inputs)==input_sha
    assert all(geometry.check_generation(p)==report['parent_inventory_sha256'][n] for n,p in parents.items())
    assert all(geometry.check_generation(p)==manifest['parent_inventory_sha256'][n] for n,p in source_parents.items())
    assert all(file_hash(ROOT/n)==h for n,h in report['code_sha256'].items())
    files = {p.relative_to(output).as_posix():file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
    identity = 'sightline-review-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20]
    (output/'inventory.json').write_bytes(json_bytes(dict(generation_id=identity,files=files)))
    print(dict(generation_id=identity,counts=dict(counts),maximum_independent_db_difference=max(errors),maximum_public_error_db=max_public_error),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs',default=str(ROOT/'artifacts/sightline_checks_v1'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/sightline_review_v1'))
    args = parser.parse_args();run(args.inputs,args.output)
