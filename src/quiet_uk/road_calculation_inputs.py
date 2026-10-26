"""Per-process inputs shared by the conditional road calculations.

Formerly scripts/89_integrate_conditional_roads.py:initialise, which scripts
loaded by file path. STATE is filled once per worker process.
"""
import json
import os
from pathlib import Path
import time

import numpy as np

from quiet_uk.conditional_road_propagation import PERIODS
from quiet_uk.propagation_canary import BANDS
from quiet_uk.real_scene_inputs import load_builder

ROOT = Path(__file__).resolve().parents[2]
PARENTS = ('physical_scene_inputs_v2', 'ground_cover_inputs_v3', 'road_direction_inputs_v2',
    'road_emission_assumptions_v2', 'real_road_scenes_v2', 'road_operating_inputs_v2',
    'physical_scene_assumptions_v2', 'physical_scene_assumption_review_v1')
RECEIVER = [507000., 179000.]
STATE = {}


def read(path):return json.loads(Path(path).read_bytes())


def initialise(output):
    p=ROOT/'artifacts';start=time.perf_counter()
    STATE.update(builder=load_builder(p/'physical_scene_inputs_v2',p/'ground_cover_inputs_v3',maximum_reflections=0),
        features=read(p/'physical_scene_assumptions_v2/physical_features.json')['features'],
        policies=read(p/'physical_scene_assumptions_v2/source_physical_inputs.json')['roads'],
        roads={r['link_id']:r for r in read(p/'road_operating_inputs_v2/road_operating_inputs.json')['roads']},
        calendar=read(p/'road_operating_inputs_v2/traffic_weather_calendars.json')['calendars']['national_vehicle'],
        coefficients=read(p/'road_emission_assumptions_v2/published_2021_coefficients.json'))
    air=read(Path(output)/'air_tables.json');data=np.load(Path(output)/'air_tables.npz',allow_pickle=False)
    STATE['air']=dict(maximum_distance_m=air['maximum_distance_m'],tables={
        (period,f):dict(distance_m=data[f'distance_{f}'],groups=air['groups'],transmission=data[f'{period}_{f}']) for period in PERIODS for f in BANDS})
    print(f'Worker {os.getpid()} ready in {time.perf_counter()-start:.1f}s',flush=True)
