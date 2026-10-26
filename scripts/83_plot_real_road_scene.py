"""Plot one retained geographic scene; never plot a calculated noise estimate."""
import argparse
import csv
import gzip
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'artifacts/real_scene_mpl_cache'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from shapely import from_wkt
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('frozen_inventory',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)

def run(inputs,output):
    inputs,output=Path(inputs).resolve(),Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Use fresh workspace output')
    helper.verified(inputs);digest=file_hash(inputs/'inventory.json');chosen=None
    with gzip.open(inputs/'prepared_paths.jsonl.gz','rt',encoding='utf-8') as stream:
        for i,line in enumerate(stream):
            row=json.loads(line)
            if chosen is None:chosen=row
            if 800<=row['scene']['direct']['length_m']<=1200:chosen=row;break
            if i>=99:break
    scene=chosen['scene'];origin=np.array(scene['receiver_xy_bng']);source=np.array(scene['source_xy_bng'])
    ids={c['feature_id'] for c in scene['direct']['crossings'] if c['kind']=='building'}
    polygons=[]
    with (ROOT/'artifacts/physical_scene_inputs_v2/building_heights_bng.csv').open(encoding='utf-8',newline='') as f:
        for row in csv.DictReader(f):
            if 'os:'+row['source_id']+':'+row['source_record_number'] in ids:polygons.append(from_wkt(row['geometry_wkt_bng']))
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    fig,(map_ax,profile_ax)=plt.subplots(1,2,figsize=(12,5))
    for geometry in polygons:
        pieces=[geometry] if geometry.geom_type=='Polygon' else geometry.geoms
        for polygon in pieces:
            points=np.asarray(polygon.exterior.coords)-origin;map_ax.fill(points[:,0],points[:,1],color='#bec5cc',alpha=.65)
    points=np.array([source,origin])-origin;map_ax.plot(points[:,0],points[:,1],color='#216c83',linewidth=2,label='Direct geographic path')
    for i,reflection in enumerate(scene['reflections']):
        xy=np.array([source,reflection['plan']['reflection_xy'],origin])-origin
        map_ax.plot(xy[:,0],xy[:,1],'--',color='#a56b12',alpha=.85,label='Facade plan candidate' if i==0 else None)
        map_ax.scatter(*xy[1],color='#a56b12',s=25)
    map_ax.scatter(*(source-origin),color='#216c83',s=45,label='Closest road source point')
    map_ax.scatter(0,0,color='#9c3856',marker='x',s=60,label='Existing core-centre receiver')
    map_ax.set(title='Road-to-location geometry',xlabel='Easting relative to receiver (m)',ylabel='Northing relative to receiver (m)');map_ax.set_aspect('equal',adjustable='datalim');map_ax.legend(fontsize=8)
    profile=scene['direct'];terrain=np.asarray(profile['kernel_scene']['points'],dtype=float)
    profile_ax.plot(terrain[:,0],terrain[:,1],color='#216c83',linewidth=1.3,label='Native terrain ordinates / declared reconstruction')
    labelled=False
    for c in profile['crossings']:
        if c['kind']=='building':
            for a,b in c['intervals_m']:
                profile_ax.axvspan(a,b,color='#bec5cc',alpha=.35,label='Footprint crossing; roof height unknown' if not labelled else None);labelled=True
    s,r=profile['kernel_scene']['source'],profile['kernel_scene']['receiver'];profile_ax.scatter([s[0],r[0]],[s[1],r[1]],color='#9c3856',marker='x',s=45,label='Declared at-grade source / receiver heights')
    profile_ax.set(title='Terrain evidence and unknown roof spans',xlabel='Direct path chainage (m)',ylabel='Elevation above ODN (m)');profile_ax.legend(fontsize=7,loc='best')
    fig.text(.04,.015,'Geographic readiness snapshot only. Generalised footprints/facades and declared heights remain unqualified; no noise level is computed.',fontsize=9)
    fig.tight_layout(rect=(0,.045,1,1));output.mkdir(parents=True);fig.savefig(output/'real_road_scene.png',dpi=170);plt.close(fig)
    (output/'receipt.json').write_bytes(json_bytes(dict(input_inventory_sha256=digest,link_id=chosen['link_id'],selection='First retained path within 800-1200m among first 100 ordered roads; otherwise first road. Illustration only.',real_area_noise_prediction=None,plot_code_sha256=file_hash(Path(__file__)))))
    shutil.copyfile(Path(__file__),output/Path(__file__).name);helper.verified(inputs);assert file_hash(inputs/'inventory.json')==digest
    print(dict(generation_id=helper.seal(output,'real-road-scene-figure-'),link_id=chosen['link_id']),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--inputs',default=str(ROOT/'artifacts/real_road_scenes_v2'));p.add_argument('--output',default=str(ROOT/'artifacts/real_road_scene_figure_v1'))
    a=p.parse_args();run(a.inputs,a.output)
