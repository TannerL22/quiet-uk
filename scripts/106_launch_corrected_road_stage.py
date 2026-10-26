"""Importable Windows spawn entry point for the corrected numerical stages.

Registers dynamically loaded evaluators consistently in parent and children.
No physics or numerical settings change. This launcher is included in snapshots.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
import importlib.util
import pickle
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('corrected_road_pipeline',ROOT/'scripts/101_rerun_corrected_roads.py')
pipeline=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pipeline;spec.loader.exec_module(pipeline)
sys.modules[pipeline.refiner.__name__]=pipeline.refiner
sys.modules[pipeline.checker.__name__]=pipeline.checker
original_code_paths=pipeline.code_paths
def code_paths():return [*original_code_paths(),Path(__file__)]
pipeline.code_paths=code_paths

def initialise_spawn_probe():
    pipeline.refiner.compute=lambda value:dict(spawn_probe=value)

def spawn_probe():
    pickle.dumps(pipeline.refiner.initialise)
    with ProcessPoolExecutor(max_workers=1,initializer=initialise_spawn_probe) as pool:
        assert list(pipeline.bounded(pool,[1,2,3]))==[dict(spawn_probe=value) for value in (1,2,3)]
    print('Windows spawn entry point and dynamic evaluator registration passed',flush=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage',choices=('integrate','finer','spawn-probe'),required=True);parser.add_argument('--inputs');parser.add_argument('--review');parser.add_argument('--output')
    args=parser.parse_args()
    if args.stage=='spawn-probe':spawn_probe()
    elif args.stage=='integrate':pipeline.integrate(args.inputs,args.review,args.output)
    else:pipeline.finer(args.inputs,args.output)
