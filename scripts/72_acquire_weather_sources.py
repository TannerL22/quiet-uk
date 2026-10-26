"""Retain bounded weather method originals and pinned public reference cuts."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash,json_bytes
spec = importlib.util.spec_from_file_location('byte_helper',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
COMMIT = '1a0158958d3288752daef3a3a60b5f8bc1afacef'
RAW = 'https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/'+COMMIT+'/'


def run(output,research):
    output,research = Path(output).resolve(),Path(research).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts') or not research.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use fresh workspace output and retained workspace originals')
    parents = {n:ROOT/'artifacts'/n for n in ('sightline_sources_v1','sightline_checks_v1','sightline_review_v1')}
    for p in parents.values(): helper.verified(p)
    hashes = {n:file_hash(p/'inventory.json') for n,p in parents.items()}
    originals = [json.loads((research/'receipts.json').read_bytes())[0],
        json.loads((research/'eu-2015-annex.pdf.receipt.json').read_bytes()),json.loads((research/'eu-2018-receipt.json').read_bytes())]
    for r in originals:
        assert file_hash(research/r['file'])==r['sha256']
        assert (research/r['file']).read_bytes().startswith(b'%PDF')
    output.mkdir(parents=True)
    source = parents['sightline_sources_v1']
    for p in source.iterdir():
        if p.name not in ('inventory.json','manifest.json','69_acquire_sightline_sources.py'):
            if p.is_dir(): shutil.copytree(p,output/p.name)
            else: shutil.copyfile(p,output/p.name)
    receipts = []
    for r in originals:
        shutil.copyfile(research/r['file'],output/r['file']);receipts.append(r)
    for name in ('eu-2015-metadata.rdf','eu-2018-expression.rdf','eu-2018-manifestation.rdf','air-conversion-documentation.json'):
        shutil.copyfile(research/name,output/name)
    total = 0
    for test in ('TC01','TC02','TC03','TC04'):
        name = test+'_Direct.json'
        url = RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/'+name
        with requests.get(url,stream=True,timeout=(15,40)) as r:
            r.raise_for_status();chunks=[];size=0
            for chunk in r.iter_content(65536):
                size+=len(chunk);total+=len(chunk)
                if size>1024*1024 or total>4*1024*1024: raise ValueError('Request budget exceeded')
                chunks.append(chunk)
            body=b''.join(chunks);json.loads(body);(output/name).write_bytes(body)
            receipts.append(dict(file=name,url=url,response_url=r.url,status=r.status_code,bytes=size,
                sha256=file_hash(output/name),retrieved_utc=datetime.now(timezone.utc).isoformat()))
        (output/'weather_receipts.json').write_bytes(json_bytes(receipts));print(name+': retained',flush=True)
    manifest = dict(schema_version=1,commit=COMMIT,parent_inventory_sha256=hashes,request_count=4,received_bytes=total,
        original_method='Original 2015 Annex (complete 823-page journal issue), 2018 ground correction, retained 2021 amendment; original 12-page ISO 9613-1:1993 SIST preview. Limited preview is not full ISO qualification.',
        absorption_conversion='RH percent to molar percent through saturation pressure documented by the original python-acoustics project; no implementation code copied.',
        favourable_scope='DIRECT upper minor circular arcs, explicit roof/G support, pure image paths on selected physical chain. No reflection/lateral/full-scene/annual release.',
        frequency_policy='Ground/diffraction nominal octave frequencies; ISO absorption explicit exact base10 centres or explicit nominal policy.',
        licences='Original NoiseModelling GPLv3 fixtures/expected tests unchanged. ISO preview copyrighted, retained locally for review. Own mathematical implementation.',
        external_engine_executed=False,full_current_method_qualified=False,acquisition_code_sha256=file_hash(Path(__file__)))
    (output/'manifest.json').write_bytes(json_bytes(manifest));shutil.copyfile(Path(__file__),output/Path(__file__).name)
    for n,p in parents.items(): helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output,'weather-sources-'),received_bytes=total)),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',default=str(ROOT/'artifacts/weather_sources_v1'))
    p.add_argument('--research',default=str(ROOT/'artifacts/weather_research_work'))
    a=p.parse_args();run(a.output,a.research)
