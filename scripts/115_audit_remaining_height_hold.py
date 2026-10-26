"""Diagnose the two remaining roof/terrain conflicts without altering inputs.

Compare the declared, unsimplified midpoint terrain reconstruction with the
native containing-cell ordinates and the existing global DSM roof percentiles.
No alternative terrain or roof assumption is promoted to an accepted road.
"""
import argparse
from bisect import bisect_right
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np
from shapely.geometry import Point, box

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('height_native_pipeline',ROOT/'scripts/109_integrate_budget_held_roads.py')
pipeline=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pipeline;spec.loader.exec_module(pipeline)
from quiet_uk.physical_scene_assumptions import materialize_profile,feature_top,assumption_cases
from quiet_uk.conditional_road_propagation import direct_transfer
read,file_hash,json_bytes=pipeline.read,pipeline.file_hash,pipeline.json_bytes
RECEIPT=ROOT/'artifacts/budget_road_validation_v2.json'
LINK='F4E6B65F-E0DF-412B-939B-7B666CCE8273'


def ordinate(points,x):
    xs=[p[0] for p in points]
    if not xs[0]<=x<=xs[-1]:raise ValueError('Point outside complete terrain profile')
    i=min(max(0,bisect_right(xs,x)-1),len(points)-2)
    a,u=points[i];b,v=points[i+1]
    return u+(v-u)*(x-a)/(b-a)


def interval_diagnostic(points,native,a,b,top):
    probes=[a,b,*[x for x,z in points if a<x<b]]
    maximum=max(ordinate(points,x) for x in probes)
    cells=[dict(start_m=max(a,l),end_m=min(b,r),row=row,column=col,terrain_odn_m=z)
           for l,r,row,col,z in native if max(a,l)<min(b,r)]
    assert cells and all(c['terrain_odn_m'] is not None for c in cells)
    assert math.isclose(math.fsum(c['end_m']-c['start_m'] for c in cells),b-a,abs_tol=1e-8)
    buried=0.
    for (l,u),(r,v) in zip(points,points[1:]):
        lo,hi=max(l,a),min(r,b)
        if lo>=hi:continue
        first,last=ordinate(points,lo)-top,ordinate(points,hi)-top
        if first>0 and last>0:buried+=hi-lo
        elif first>0 or last>0:
            cross=lo+(hi-lo)*(-first)/(last-first)
            buried+=cross-lo if first>0 else hi-cross
    return dict(interval_m=[a,b],width_m=b-a,roof_top_odn_m=top,
        reconstructed_terrain_max_odn_m=maximum,roof_below_reconstructed_terrain_m=max(0.,maximum-top),
        native_containing_cell_max_odn_m=max(c['terrain_odn_m'] for c in cells),
        native_containing_cell_roof_clearance_m=top-max(c['terrain_odn_m'] for c in cells),
        positive_buried_roof_extent_m=buried,native_cells=cells)


def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace audit required')
    receipt=read(RECEIPT)
    for name,sha in receipt['code_sha256'].items():assert file_hash(ROOT/name)==sha
    pipeline.verified_model()
    names=['corrected_road_observations_v2','corrected_road_observation_review_v1',*pipeline.runner.prior.PARENTS]
    parents={n:ROOT/'artifacts'/n for n in names}
    for p in parents.values():pipeline.helper.verified(p)
    parent_hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    paths=[Path(__file__),*pipeline.code_paths()]
    hashes={p:file_hash(p) for p in paths}
    cache=read(parents['corrected_road_observations_v2']/'seed_evaluations.json')
    old=[n for n in cache[LINK+'|combined_reference'] if n['status']=='withheld']
    assert len(old)==2 and all(n['reasons']==['candidate_top_below_reconstructed_terrain'] for n in old)
    pipeline.runner.initialise(ROOT/'artifacts/budget_road_observations_v1')
    state=pipeline.runner.STATE;builder=state['builder']
    case=next(c for c in assumption_cases() if c['case_id']=='combined_reference')
    geometries={r['feature_id']:r['geometry'] for r in builder.buildings}
    output.mkdir(parents=True);records=[]
    with gzip.open(output/'native_profiles.jsonl.gz','wt',encoding='utf-8',compresslevel=1) as stream:
        for index,prior in enumerate(old):
            prepared=builder.prepare_point(prior['xy_bng'],pipeline.runner.prior.RECEIVER,reflections=False)
            profile=prepared['direct'];native_hash=hashlib.sha256(json_bytes(profile)).hexdigest()
            assert native_hash==prior['native_profile_sha256']
            candidate=materialize_profile(profile,state['features'],case,
                source_policy=state['policies'][LINK]['physical_source'],endpoint_ids=prepared['endpoint_footprint_ids'])
            assert candidate['geometry_blockers']==['candidate_top_below_reconstructed_terrain']
            result=direct_transfer(candidate,case)
            assert result['status']=='withheld' and result['reasons']==candidate['geometry_blockers']
            # The blocker is calculated on this complete midpoint profile before
            # the subsequent 0.1 m simplifier is ever called.
            points=profile['kernel_scene']['points'];native=profile['terrain_native_intervals']
            rebuilt=[[0.,builder.terrain.sample(prior['xy_bng'])['terrain_odn_m']],
                *[[(a+b)/2,z] for a,b,row,col,z in native],
                [profile['length_m'],builder.terrain.sample(pipeline.runner.prior.RECEIVER)['terrain_odn_m']]]
            assert points==rebuilt
            offending=[]
            for crossing in profile['crossings']:
                feature=state['features'][crossing['feature_id']];top,basis=feature_top(feature,case)
                if top is None:continue
                for a,b in crossing['intervals_m']:
                    diagnostic=interval_diagnostic(points,native,a,b,top)
                    if diagnostic['roof_below_reconstructed_terrain_m']<=1e-7:continue
                    assert feature['kind']=='building' and basis=='paired_native_dsm_flat_top_assumption'
                    geometry=geometries[crossing['feature_id']]
                    west,south,east,north=builder.terrain.bounds;resolution=builder.terrain.resolution
                    for cell in diagnostic['native_cells']:
                        row,col=cell['row'],cell['column']
                        assert float(builder.terrain.values[row,col])==cell['terrain_odn_m']
                        cx,cy=west+(col+.5)*resolution,north-(row+.5)*resolution
                        cell['cell_centre_bng']=[cx,cy]
                        cell['cell_centre_inside_generalised_footprint']=geometry.covers(Point(cx,cy))
                        pixel=box(west+col*resolution,north-(row+1)*resolution,
                                  west+(col+1)*resolution,north-row*resolution)
                        cell['footprint_overlap_area_m2']=geometry.intersection(pixel).area
                    summary=feature['native_summary']
                    percentiles=summary['surface_odn_m']
                    diagnostic.update(feature_id=crossing['feature_id'],feature_kind=feature['kind'],
                        flat_roof_basis=basis,feature=feature,feature_geometry_wkt_bng=geometry.wkt,
                        deficit_in_float_ulps=diagnostic['roof_below_reconstructed_terrain_m']/math.ulp(top),
                        roof_percentile_clearances_m={p:z-diagnostic['reconstructed_terrain_max_odn_m'] for p,z in percentiles.items()})
                    scalar_max=diagnostic['reconstructed_terrain_max_odn_m']
                    xs=[p[0] for p in points];zs=[p[1] for p in points]
                    probes=[a,b,*[x for x,z in points if a<x<b]]
                    assert math.isclose(scalar_max,max(float(np.interp(x,xs,zs)) for x in probes),abs_tol=1e-12)
                    offending.append(diagnostic)
            assert len(offending)==1
            variant_geometry=[]
            for variant in assumption_cases():
                if variant['case_id'] not in ('baseline','roof_p10','roof_p90','combined_reference'):continue
                materialized=materialize_profile(profile,state['features'],variant,
                    source_policy=state['policies'][LINK]['physical_source'],endpoint_ids=prepared['endpoint_footprint_ids'])
                variant_geometry.append(dict(case_id=variant['case_id'],geometry_blockers=materialized['geometry_blockers'],
                    interpretation='Existing declared input scenario; geometric support only, no replacement road value.'))
            row=dict(record_id=index,xy_bng=prior['xy_bng'],source_cell_id=prior['source_cell_id'],fraction=prior['fraction'],
                native_profile_sha256=native_hash,original_cache_record_id=prior['cache_record_id'],
                original_reasons=prior['reasons'],replayed_reasons=result['reasons'],
                unsimplified_terrain_knots=len(points),rejection_precedes_terrain_simplification=True,
                offending_features=offending,declared_scenario_geometry=variant_geometry)
            records.append(row)
            stream.write(json.dumps(dict(record_id=index,profile=profile,candidate=candidate),allow_nan=False,separators=(',',':'))+'\n')
    (output/'diagnostics.json').write_bytes(json_bytes(dict(points=records)))
    (output/'protocol.json').write_bytes(json_bytes(dict(kind='remaining_roof_terrain_height_audit_v1',
        parent_inventory_sha256=parent_hashes,prior_validation_sha256=file_hash(RECEIPT),
        link_id=LINK,physical_case=case,selection='Both retained native height-failure witnesses on the only remaining physically held adaptive road.',
        scalar_cross_check='Complete native midpoint reconstruction, linear clipping at footprint crossings, native raster cell ordinates and unchanged paired DSM summary.',
        changes_to_inputs_or_equations=False,road_ledger_updated=False,all_inclusive_noise_db=None,map_updated=False)))
    deficits=[r['offending_features'][0]['roof_below_reconstructed_terrain_m'] for r in records]
    (output/'report.json').write_bytes(json_bytes(dict(status='completed_native_height_consistency_audit',
        witness_points=2,offending_features=len({r['offending_features'][0]['feature_id'] for r in records}),
        roof_below_unsimplified_reconstructed_terrain_m=deficits,
        native_cell_roof_clearances_m=[r['offending_features'][0]['native_containing_cell_roof_clearance_m'] for r in records],
        classification='Conditional flat DSM-median roof and sampled-midpoint terrain conflict at generalised footprint edge; not floating-point rounding or the subsequent vertical simplifier.',
        decision='Keep this road withheld. Native raster steps and higher DSM percentiles are not qualified replacements for the declared terrain/roof geometry.',
        next_evidence='Matched roof-edge/ground geometry or a separately justified and validated terrain/roof union method; no arbitrary roof raising, footprint trimming or relaxed clearance tolerance.',
        field_validation=False,road_ledger_updated=False,all_inclusive_noise_db=None,map_updated=False)))
    assert all(file_hash(p)==sha for p,sha in hashes.items())
    for name,p in parents.items():pipeline.helper.verified(p);assert file_hash(p/'inventory.json')==parent_hashes[name]
    (output/'code').mkdir()
    for p in hashes:shutil.copyfile(p,output/'code'/p.name)
    generation=pipeline.helper.seal(output,'native-height-consistency-audit-')
    print(json.dumps(dict(generation_id=generation,report=read(output/'report.json'))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True)
    run(parser.parse_args().output)
