"""Retain originals for screened reflections and source-line integration."""
import argparse
from datetime import datetime,timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec=importlib.util.spec_from_file_location('byte_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
COMMIT='1a0158958d3288752daef3a3a60b5f8bc1afacef'
RAW='https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/'+COMMIT+'/'


def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):raise ValueError('Fresh workspace output required')
    parents={n:ROOT/'artifacts'/n for n in ('reflection_sources_v1','reflection_checks_v3','reflection_review_v4')}
    for p in parents.values():helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    output.mkdir(parents=True)
    for p in parents['reflection_sources_v1'].iterdir():
        if p.is_file() and p.name not in ('inventory.json','manifest.json'):shutil.copyfile(p,output/p.name)
    shutil.copyfile(parents['reflection_sources_v1']/'manifest.json',output/'preceding_reflection_manifest.json')
    receipts=[];total=0
    for name in ('TC18_Direct.json','TC18_Reflection.json','TC27_Direct.json','TC27_Reflection.json'):
        url=RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/'+name
        with requests.get(url,stream=True,timeout=(15,40)) as r:
            r.raise_for_status();size=0;chunks=[]
            for chunk in r.iter_content(65536):
                size+=len(chunk);total+=len(chunk)
                if size>1024*1024 or total>4*1024*1024:raise ValueError('Bounded request budget exceeded')
                chunks.append(chunk)
            body=b''.join(chunks);json.loads(body);(output/name).write_bytes(body)
            receipts.append(dict(file=name,url=url,response_url=r.url,status=r.status_code,bytes=size,sha256=file_hash(output/name),retrieved_utc=datetime.now(timezone.utc).isoformat()))
        (output/'acquisition_receipts.json').write_bytes(json_bytes(receipts));print(name+': retained',flush=True)
    manifest=dict(schema_version=1,commit=COMMIT,parent_inventory_sha256=hashes,request_count=4,received_bytes=total,
        method='Original EU2015 Annex PDF29-32/41-43,2018 correction,2021 amendment PDF7-9; no external engine or GPL implementation port.',
        discovery=dict(firecrawl_search_id='01a12a32-62c1-72ac-b19f-078936b0e0b0',query='CNOSSOS EU Annex II line source segmentation equivalent point sources segment length integration retrodiffraction diffraction reflections',
            content='Search excerpts only; calculations derive from retained original Annex and amendment, not secondary search snippets.'),
        licences='Original NoiseModelling GPLv3 fixtures and expected tests unchanged; own mathematical implementation.',
        external_engine_executed=False,full_current_method_qualified=False,acquisition_code_sha256=file_hash(Path(__file__)))
    (output/'manifest.json').write_bytes(json_bytes(manifest));shutil.copyfile(Path(__file__),output/Path(__file__).name)
    for n,p in parents.items():helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(dict(generation_id=helper.seal(output,'screened-line-sources-'),received_bytes=total),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',default=str(ROOT/'artifacts/screened_line_sources_v1'))
    a=p.parse_args();run(a.output)
