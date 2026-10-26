"""Freeze terrain geometry canaries, external benchmarks and native diagnostics."""
import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import platform
import shutil
import sys

import numpy as np
import rasterio
from shapely import from_wkt

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.propagation_canary import BANDS
from quiet_uk.terrain_profile import (linear_segments,mean_plane,plane_frame,upper_path,
                                     split_planes,planar_ground_check,native_intervals)
spec=importlib.util.spec_from_file_location('sources',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


def read(path):return json.loads(Path(path).read_bytes())


def case(id_,points,source=None,receiver=None,*,unknown_source=False,unknown_ground=False):
    source=[points[0][0],points[0][1]+.05] if source is None and not unknown_source else source
    receiver=[points[-1][0],points[-1][1]+4] if receiver is None else receiver
    return dict(case_id=id_,points=points,source=source,receiver=receiver,unknown_ground=unknown_ground)


def protocol():
    cases=[case('plane-slope-'+str(a),[(0,30),(17,30+17*a),(100,30+100*a)]) for a in (-.5,-.2,0,.2,.5)]
    cases.extend([
        case('large-vertical-datum',[(0,10000),(100,10000)]),
        case('symmetric-ridge',[(0,0),(50,10),(100,0)]),
        case('offcentre-ridge',[(0,0),(20,0),(40,8),(70,0),(100,0)]),
        case('narrow-ridge',[(0,0),(48,0),(50,10),(52,0),(100,0)]),
        case('two-ridges',[(0,0),(25,8),(50,-3),(75,8),(100,0)]),
        case('valley-clear-ray',[(0,0),(50,-10),(100,0)]),
        case('tangent-terrain',[(0,0),(50,2.025),(100,0)]),
        case('missing-terrain',[(0,0),(50,None),(100,0)]),
        case('missing-source-elevation',[(0,0),(100,0)],unknown_source=True),
        case('missing-ground',[(0,0),(100,0)],unknown_ground=True),
    ])
    return dict(schema_version=1,study='Synthetic geometry plus unchanged native terrain diagnostics; no real-area acoustic prediction.',
        method='JRC2012 VI-3/4 continuous mean plane and VI.4.3 orthogonal geometry. Later 2020 consolidated annex confirms negative-height clipping. Full 2021 boundary chain unqualified.',
        cases=cases,bands_hz=list(BANDS),ground_cases=[0.,.3,.7,1.],
        frequency_scope='63–4000Hz for the checked JRC2012 ground subset; 8000Hz withheld as a scope test.',
        planar_numerical_residual_tolerance_m=1e-10,
        refusal='Any nonlinear terrain, unknown ground/support/endpoint or out-of-scope frequency withholds the ground-only acoustic result. Clear geometry does not establish Rayleigh clearance.',
        native_interpretation='Exact cell lengths/values under a declared piecewise-constant diagnostic representation. Native pixel steps are not physical diffraction edges.')


def run(sources,output):
    sources,output=Path(sources).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use a fresh workspace generation')
    names=('ground_barrier_audit_v1','physical_scene_inputs_v2','ground_cover_inputs_v3','ground_cover_review_v3','synthetic_propagation_v2')
    parents={n:ROOT/'artifacts'/n for n in names};parents[sources.name]=sources
    for p in parents.values():helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    source_manifest=read(sources/'manifest.json')
    output.mkdir(parents=True)
    shutil.copytree(ROOT/'artifacts/terrain_pdf_review_v1',output/'method-review')
    proto=protocol();(output/'protocol.json').write_bytes(json_bytes(proto))
    geometry_cases,results=[],[]
    for c in proto['cases']:
        p=mean_plane(linear_segments(c['points']))
        frame=plane_frame(p,c['source'],c['receiver'])
        path=upper_path(c['points'],c['source'],c['receiver'])
        sides=[]
        if path['status']!='withheld':
            sides=[split_planes(c['points'],edge[0],c['source'],c['receiver']) for edge in path['path'][1:-1]]
        geometry_cases.append(dict(**c,mean_plane=p,common_frame=frame,upper_path=path,split_planes=sides,
                                   qualified_noise_prediction=None))
        for g in ([None] if c['unknown_ground'] else proto['ground_cases']):
            for f in proto['bands_hz']:
                results.append(dict(case_id=c['case_id'],g=g,frequency_hz=f,
                    **planar_ground_check(c['points'],c['source'],c['receiver'],f,g)))
    (output/'synthetic_geometry.json').write_bytes(json_bytes(dict(cases=geometry_cases)))
    (output/'synthetic_ground_results.json').write_bytes(json_bytes(dict(results=results)))
    # Independent external expectations are published in the pinned upstream tests.
    benchmark=[]
    for name,expected in [('TC05_Direct',[.05,-2.83,3.83,6.16,194.59]),('TC06_Direct',[.05,-2.83,3.83,3.66,194.45])]:
        cuts=read(sources/(name+'.json'))['cutPoints']
        s=cuts[0]['coordinate'];r=cuts[-1]['coordinate']
        points=[(math.hypot(c['coordinate']['x']-s['x'],c['coordinate']['y']-s['y']),c['zGround']) for c in cuts]
        a=[0.,s['z']];b=[points[-1][0],r['z']]
        plane=mean_plane(linear_segments(points));frame=plane_frame(plane,a,b)
        def entry(id_,plane,frame,expected):
            actual=[plane['slope'],plane['intercept'],frame['source']['equivalent_height_m'],frame['receiver']['equivalent_height_m'],frame['projected_distance_m']]
            error=[x-y for x,y in zip(actual,expected)]
            if any(abs(e)>.0051 for e in error):raise ValueError('Rounded external benchmark mismatch')
            return dict(case_id=id_,points=points,source=a,receiver=b,expected_rounded=expected,actual=actual,
                error=error,tolerance=.0051,expected_fields=['slope','intercept','zs','zr','dp'],
                source_commit=source_manifest['commit'],fixture_file=name+'.json',external_engine_executed=False)
        benchmark.append(entry(name,plane,frame,expected))
        if name=='TC06_Direct':
            edge=next(x for x,z in points if z==10)
            sides=split_planes(points,edge,a,b)
            for suffix,side,frame,expected in [('source-side',sides['source_plane'],sides['source_frame'],[.05,-2.33,3.33,3.95,179.06]),
                ('receiver-side',sides['receiver_plane'],sides['receiver_frame'],[0.,10.,0.,1.5,15.33])]:
                e=entry(name+'-'+suffix,side,frame,expected);e['split_x_m']=edge;benchmark.append(e)
    (output/'external_mean_plane_benchmarks.json').write_bytes(json_bytes(dict(benchmarks=benchmark,
        scope='Original TC05/TC06 public rounded expectations for five geometry fields. Not full ISO certification or execution of NoiseModelling.',
        external_licence=source_manifest['external_licence'])))
    # Show why extra uneven vertices must not contribute extra statistical weight.
    irregular=[(0,0),(1,.2),(2,.4),(3,.6),(50,10),(100,0)]
    demonstration=dict(points=irregular,continuous_mean_plane=mean_plane(linear_segments(irregular)),
                       unweighted_point_ols=np.polyfit(*np.array(irregular).T,1).tolist())
    (output/'sampling_bias_demonstration.json').write_bytes(json_bytes(demonstration))
    physical=parents['physical_scene_inputs_v2'];audit=parents['ground_barrier_audit_v1']
    bounds=read(audit/'report.json')['study']['source_halo_bounds']
    probes=read(audit/'path_geometry_audit.json')['probes']
    native=[];counts=Counter()
    with rasterio.open(physical/'terrain_odn_2m.tif') as dataset:
        if dataset.crs.to_epsg()!=27700 or dataset.res!=(2.,2.) or tuple(dataset.bounds)!=tuple(bounds):
            raise ValueError('Unexpected native terrain grid')
        values=dataset.read(1);mask=dataset.read_masks(1)
        for p in probes:
            line=from_wkt(p['geometry_wkt_bng'])
            if len(line.coords)!=2:raise ValueError('Expected existing straight geometry probes')
            intervals=native_intervals(line.coords[0],line.coords[-1],bounds)
            segments=[]
            for interval in intervals:
                row,col=interval['row'],interval['column']
                value=float(values[row,col]) if row is not None and mask[row,col] and math.isfinite(float(values[row,col])) else None
                interval['terrain_odn_m']=value
                interval['valid_original_terrain']=value is not None
                segments.append((interval['start_m'],interval['end_m'],value,value))
            plane=mean_plane(segments)
            st=p['source_terrain']['terrain_odn_m'];rt=p['receptor_terrain']['terrain_odn_m']
            frame=plane_frame(plane,[0,st+.05] if st is not None else None,[line.length,rt+4] if rt is not None else None)
            counts['native_intervals']+=len(intervals)
            counts['missing_intervals']+=sum(i['terrain_odn_m'] is None for i in intervals)
            if frame['status']!='withheld':
                counts['conditional_source_height_clips']+=int(frame['source']['negative_equivalent_height_clipped'])
                counts['conditional_receiver_height_clips']+=int(frame['receiver']['negative_equivalent_height_clipped'])
            native.append(dict(probe_id=p['probe_id'],geometry_wkt_bng=p['geometry_wkt_bng'],length_m=line.length,
                intervals=intervals,cell_constant_mean_plane=plane,conditional_at_grade_frame=frame,
                basis='Diagnostic containing-cell terrain, with assumed at-grade source +0.05m and receiver +4m. Actual road deck/receptor qualification missing.',
                qualified_source_elevation_odn_m=None,qualified_ground_g=None,noise_prediction=None,
                status='native_terrain_geometry_diagnostic_only'))
    (output/'native_terrain_profiles.json').write_bytes(json_bytes(dict(probes=native)))
    summary=dict(synthetic_geometry_cases=len(geometry_cases),synthetic_ground_results=len(results),
        ground_result_states=dict(Counter(r['status'] for r in results)),
        withheld_reasons=dict(Counter(r['reason'] for r in results if r['status']=='withheld')),
        external_geometry_benchmarks=len(benchmark),external_geometry_scalar_checks=sum(len(b['actual']) for b in benchmark),
        maximum_rounded_benchmark_error=max(abs(e) for b in benchmark for e in b['error']),
        native_geometry_probes=len(native),native_counts=dict(counts),real_area_noise_predictions=False)
    code_names=['scripts/57_acquire_ground_cover_sources.py','scripts/60_acquire_terrain_method_sources.py',
                'scripts/61_run_terrain_profile_checks.py','src/quiet_uk/terrain_profile.py','src/quiet_uk/propagation_canary.py','src/quiet_uk/explorer.py']
    report=dict(schema_version=1,status='bounded_terrain_geometry_and_synthetic_planar_ground_canary',summary=summary,
        parent_inventory_sha256=hashes,checked_primary_pdf_pages=[77,85],
        method_limits=proto['refusal'],native_interpretation=proto['native_interpretation'],
        licence_and_attribution=dict(physical=read(physical/'report.json')['licenses'],physical_credits=read(physical/'report.json')['attributions'],
            benchmark=source_manifest['external_licence'],method='EU/JRC72550 DOI10.2788/31776; EU consolidated annex dated2020, not full current compliance.'),
        eligible_for_absolute_noise_release=False,
        runtime=dict(python=sys.version,platform=platform.platform(),numpy=np.__version__,rasterio=rasterio.__version__),
        code_sha256={n:file_hash(ROOT/n) for n in code_names})
    (output/'report.json').write_bytes(json_bytes(report))
    for n in code_names:
        dest=output/'code'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/n,dest)
    for n,p in parents.items():
        helper.verified(p)
        if file_hash(p/'inventory.json')!=hashes[n]:raise ValueError('Parent changed during canary')
    if any(file_hash(ROOT/n)!=h for n,h in report['code_sha256'].items()):raise ValueError('Code changed during canary')
    print(json.dumps(dict(generation_id=helper.seal(output,'terrain-profile-checks-'),summary=summary)),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources',default=str(ROOT/'artifacts/terrain_method_sources_v1'))
    parser.add_argument('--output',default=str(ROOT/'artifacts/terrain_profile_checks_v2'))
    args=parser.parse_args();run(args.sources,args.output)
