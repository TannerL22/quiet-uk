"""Recheck three largest settled discrepancies with four midpoint nodes per leaf.

Uses the unchanged geographic evaluator from runner 92, replacing only its
quadrature callback. This is a finer numerical check, not independent acoustics.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
import gzip
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
from quiet_uk.conditional_road_propagation import receiver_metrics

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
refiner=module('refined_geographic_runner',ROOT/'scripts/92_refine_conditional_roads.py')
BANK={}

def read(path):return json.loads(Path(path).read_bytes())

def finer_rule(source,evaluate,*,partition,controls,seed_evaluations):
    key=(source['link_id'],seed_evaluations[0]['physical_case_id']);original=BANK[key]
    cells={c['segment_id']:c for c in source['source_cells']};energy=np.zeros((2,3,8));nodes=[];reasons=Counter()
    for li,leaf in enumerate(original['leaves']):
        cell=cells[leaf['source_cell_id']];left,right=leaf['left_fraction'],leaf['right_fraction'];length=leaf['length_m']
        for j in range(4):
            t=left+(right-left)*(j+.5)/4;xy=[a+(b-a)*t for a,b in zip(cell['start_xy_bng'],cell['end_xy_bng'])]
            node=dict(evaluate(xy));node.update(node_id=len(nodes),source_cell_id=leaf['source_cell_id'],leaf_index=li,fraction=t,xy_bng=xy,quadrature_j=j,quadrature_n=4,weight_length_m=length/4)
            nodes.append(node)
            if node['status']=='withheld':reasons.update(node['reasons']);node['relative_energy_per_m']=None
            else:
                value=np.asarray(node['relative_energy_per_m']);assert value.shape==(2,3,8) and np.isfinite(value).all() and np.all(value>=0)
                energy+=value*length/4;node['relative_energy_per_m']=value.tolist()
    changes=None
    if not reasons:
        previous=np.asarray(original['whole_road_relative_energy'])
        changes={s:{p:None if m is None or receiver_metrics(previous[i])[p] is None else m-receiver_metrics(previous[i])[p] for p,m in receiver_metrics(energy[i]).items()} for i,s in enumerate(('homogeneous','favourable'))}
    maximum=None if changes is None else max((abs(v) for state in changes.values() for v in state.values() if v is not None),default=0.)
    return dict(status='finer_spacing_check_passed' if maximum is not None and maximum<=.1 else 'finer_spacing_check_withheld_or_changed',
        evaluation_count=len(nodes),evaluations=nodes,known_evaluated_relative_energy=energy.tolist(),whole_road_relative_energy=None if reasons else energy.tolist(),
        unknown_reason_counts=dict(reasons),change_from_adaptive_db=changes,maximum_absolute_change_db=maximum,criterion_db=.1,
        prior_worst_spacing_change_db=original['prior_worst_spacing_change_db'],
        refinement_convergence_proven=False,rigorous_noise_error_bound_db=None,real_area_validated=False,all_inclusive_noise_db=None)

def initialise(output):
    refiner.initialise(output);protocol=read(Path(output)/'protocol.json')
    main=ROOT/'artifacts'/protocol['input_directory'];rows=read(main/'refined_roads.json')['roads']
    BANK.update({(r['link_id'],r['physical_case_id']):r for r in rows})
    refiner.integrate_adaptive=finer_rule
    refiner.geometry_breakpoints=lambda *args,**kwargs:None

def compute(job):return refiner.compute(job)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve();start=time.perf_counter()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    refiner.prior.helper.verified(inputs);input_hash=file_hash(inputs/'inventory.json')
    code_paths=[Path(__file__),ROOT/'scripts/92_refine_conditional_roads.py',ROOT/'scripts/89_integrate_conditional_roads.py',ROOT/'scripts/57_acquire_ground_cover_sources.py',ROOT/'scripts/81_build_real_road_scenes.py',*(ROOT/'src/quiet_uk').glob('*.py')]
    code_hashes={p:file_hash(p) for p in code_paths}
    for path in (inputs/'code').glob('*.py'):assert file_hash(path)==file_hash(ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name)
    eligible=sorted([r for r in read(inputs/'refined_roads.json')['roads'] if r['diagnostic_tolerance_met']],key=lambda r:(-r['prior_worst_spacing_change_db'],r['link_id'],r['physical_case_id']))[:3]
    if len(eligible)!=3:raise ValueError('Three settled cases required for fixed confirmation protocol')
    sources={r['link_id']:r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    output.mkdir(parents=True)
    for name in ('air_tables.npz','air_tables.json'):shutil.copyfile(inputs/name,output/name)
    parent_seeds=read(inputs/'seed_evaluations.json');selected_keys=[r['link_id']+'|'+r['physical_case_id'] for r in eligible]
    (output/'seed_evaluations.json').write_bytes(json_bytes({k:parent_seeds[k] for k in selected_keys}))
    protocol=dict(input_directory=inputs.name,input_inventory_sha256=input_hash,controls={},
        method='Four equally weighted interior midpoints on every final adaptive leaf; half the accepted midpoint spacing.',
        selected_identities=[dict(link_id=r['link_id'],physical_case_id=r['physical_case_id']) for r in eligible],selection='Three largest prior spacing discrepancies among cases that met the primary adaptive diagnostic.',
        criterion_db=.1,geometry_and_kernel_unchanged=True,independent_acoustics=False,field_validation=False,rigorous_noise_error_bound_db=None,all_inclusive_noise_db=None)
    (output/'protocol.json').write_bytes(json_bytes(protocol));rows=[]
    with (output/'node_evaluations.jsonl.gz').open('wb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as stream:
            with ProcessPoolExecutor(max_workers=2,initializer=initialise,initargs=(str(output),)) as pool:
                futures=[pool.submit(compute,(sources[r['link_id']],r['physical_case_id'])) for r in eligible]
                for future in futures:
                    row=future.result()
                    for node in row.pop('evaluations'):stream.write((json.dumps(dict(node,link_id=row['link_id'],physical_case_id=row['physical_case_id']),allow_nan=False,separators=(',',':'))+'\n').encode())
                    rows.append(row);stream.flush();print(f'{row["link_id"]}: {row["status"]}; maximum change {row["maximum_absolute_change_db"]}; {row["evaluation_count"]} nodes',flush=True)
    (output/'road_checks.json').write_bytes(json_bytes(dict(roads=rows)))
    (output/'report.json').write_bytes(json_bytes(dict(checked_cases=len(rows),passed_cases=sum(r['status']=='finer_spacing_check_passed' for r in rows),
        evaluations=sum(r['evaluation_count'] for r in rows),maximum_known_change_db=max((r['maximum_absolute_change_db'] for r in rows if r['maximum_absolute_change_db'] is not None),default=None),
        elapsed_seconds=time.perf_counter()-start,refinement_convergence_proven=False,field_validation=False,all_inclusive_noise_db=None)))
    if any(file_hash(p)!=h for p,h in code_hashes.items()):raise ValueError('Executed code changed')
    code=output/'code';code.mkdir()
    for path in code_paths:shutil.copyfile(path,code/path.name)
    (output/'provenance.json').write_bytes(json_bytes(dict(input_inventory_sha256=input_hash,code_sha256={p.name:file_hash(p) for p in code.glob('*.py')},licences='Preserve all source-specific parent attribution and licences.')))
    refiner.prior.helper.verified(inputs);assert file_hash(inputs/'inventory.json')==input_hash
    generation=refiner.prior.helper.seal(output,'adaptive-spacing-check-');print(json.dumps(dict(generation_id=generation,report=read(output/'report.json'))),flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--inputs',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();run(args.inputs,args.output)
