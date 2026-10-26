"""Retain clear-ray and changing-image reference originals with method lineage."""
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
spec = importlib.util.spec_from_file_location('sources',ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
COMMIT = '1a0158958d3288752daef3a3a60b5f8bc1afacef'
RAW = 'https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/'+COMMIT+'/'


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents = {n:ROOT/'artifacts'/n for n in ('multiple_edge_sources_v1','multiple_edge_checks_v1','multiple_edge_review_v1','diffraction_method_sources_v1')}
    for p in parents.values(): helper.verified(p)
    hashes = {n:file_hash(p/'inventory.json') for n,p in parents.items()}
    source = parents['multiple_edge_sources_v1']
    output.mkdir(parents=True)
    for name in ('NoiseModelling-CnossosTests.java','NoiseModelling-LICENSE.txt','NoiseModelling-validation.rst',
                 'TC10_Direct.json','TC12_Direct.json','TC15_Direct.json','eu-2021-amendment.pdf','eu-2021-metadata.rdf'):
        shutil.copyfile(source/name,output/name)
    for test in ('TC05','TC06','TC07'):
        shutil.copyfile(parents['diffraction_method_sources_v1']/(test+'_Direct.json'),output/(test+'_Direct.json'))
    shutil.copytree(source/'documentation',output/'documentation')
    receipts,total = [],0
    for test in ('TC11','TC13','TC14','TC16','TC21','TC23'):
        name = test+'_Direct.json'
        url = RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/'+name
        with requests.get(url,stream=True,timeout=(15,45)) as response:
            response.raise_for_status(); chunks = [];size = 0
            for chunk in response.iter_content(65536):
                size+=len(chunk);total+=len(chunk)
                if size>1024*1024 or total>6*1024*1024: raise ValueError('Reference request budget exceeded')
                chunks.append(chunk)
            body = b''.join(chunks);json.loads(body);(output/name).write_bytes(body)
            receipts.append(dict(file=name,requested_url=url,response_url=response.url,status=response.status_code,
                content_type=response.headers.get('Content-Type'),retained_bytes=size,sha256=file_hash(output/name),retrieved_utc=datetime.now(timezone.utc).isoformat()))
        (output/'receipts.json').write_bytes(json_bytes(receipts));print(name+': retained original',flush=True)
    manifest = dict(schema_version=1,commit=COMMIT,parent_inventory_sha256=hashes,request_count=len(receipts),received_bytes=total,
        method_scope='Homogeneous physical-edge selection, strict 2021 clear-ray/Rayleigh tests, fixed-chain image diffraction and roof surface/G support. No lateral/weather/reflection/annual/real-area release.',
        method_evidence='Retained original 2020 Annex II plus original 2021/1226 PDF and metadata. No new consolidated-method qualification claimed.',
        interpretation='Select physical edges; retain them for image diffraction. A virtual hull is diagnostic only; each image uses its own direct-distance baseline. Positive returning-leg excess if the virtual endpoint projects before a fixed edge.',
        reference_limits='TC11/14 negative equivalent diffraction-edge heights in original geometry tables use a different convention. Annex II null-height rule is retained; those positive height entries are excluded from conformance claims. Boundary/ground arrays can still agree where the ground floor dominates.',
        licence='Original NoiseModelling fixtures/test expectations GPLv3 retained unchanged; no GPL implementation ported. External engine not executed.',
        acquisition_code_sha256=file_hash(Path(__file__)),external_engine_executed=False,real_area_noise_validated=False)
    (output/'manifest.json').write_bytes(json_bytes(manifest));shutil.copyfile(Path(__file__),output/Path(__file__).name)
    for n,p in parents.items():
        helper.verified(p);assert file_hash(p/'inventory.json')==hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output,'sightline-sources-'),received_bytes=total)),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'artifacts/sightline_sources_v1'))
    args = parser.parse_args();run(args.output)
