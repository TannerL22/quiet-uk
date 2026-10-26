"""Freeze method originals and public single-edge benchmark inputs."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes

spec = importlib.util.spec_from_file_location('sources', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
COMMIT = '1a0158958d3288752daef3a3a60b5f8bc1afacef'
RAW = 'https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/'+COMMIT+'/'


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents = {n: ROOT/'artifacts'/n for n in ('terrain_method_sources_v1', 'terrain_profile_checks_v2',
        'terrain_profile_review_v1', 'ground_barrier_method_review_v1')}
    for p in parents.values(): helper.verified(p)
    hashes = {n: file_hash(p/'inventory.json') for n, p in parents.items()}
    output.mkdir(parents=True)
    for name in ('NoiseModelling-LICENSE.txt', 'NoiseModelling-validation.rst', 'NoiseModelling-CnossosTests.java', 'TC05_Direct.json', 'TC06_Direct.json'):
        shutil.copyfile(parents['terrain_method_sources_v1']/name, output/name)
    documentation = output/'documentation'; documentation.mkdir()
    shutil.copyfile(parents['terrain_profile_checks_v2']/'method-review/eu-consolidated-2020.json', documentation/'eu-consolidated-2020.json')
    shutil.copyfile(parents['ground_barrier_method_review_v1']/'documentation/eu-2021-amendment.json', documentation/'eu-2021-amendment.json')
    receipts = []; total = 0
    requests_ = [
        ('TC07_Direct.json', RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/TC07_Direct.json', None, True),
        ('eu-annex-2021.pdf', 'https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/', {'uri':'CELEX:02002L0049-20210729'}, False),
        ('eu-amendment-2021.pdf', 'https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/', {'uri':'CELEX:32021L1226'}, False),
    ]
    for name, url, params, required in requests_:
        receipt = dict(file=name, source_url=url, params=params, started_utc=datetime.now(timezone.utc).isoformat())
        try:
            with requests.get(url, params=params, stream=True, timeout=(15, 45)) as response:
                receipt.update(status=response.status_code, response_url=response.url, content_type=response.headers.get('Content-Type'))
                response.raise_for_status()
                chunks = []; size = 0
                for chunk in response.iter_content(65536):
                    size += len(chunk); total += len(chunk)
                    if size>12*1024*1024 or total>24*1024*1024:
                        raise ValueError('Method-source request budget exceeded')
                    chunks.append(chunk)
                body = b''.join(chunks)
                if name.endswith('.pdf') and not body.startswith(b'%PDF-'):
                    raise ValueError('Response is not an original PDF; excluded as method evidence')
                if name.endswith('.json'): json.loads(body)
                (output/name).write_bytes(body)
                receipt.update(state='retained_original', bytes=size, sha256=file_hash(output/name))
        except (requests.RequestException, ValueError) as error:
            receipt.update(state='unavailable', error=str(error))
            if required: raise
        finally:
            receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
            receipts.append(receipt)
            (output/'receipts.json').write_bytes(json_bytes(receipts))
        print(name+': '+receipt['state'], flush=True)
    manifest = dict(schema_version=1, parent_inventory_sha256=hashes, commit=COMMIT,
        method='EU Annex II homogeneous single horizontal-edge subset, Ch=1; 2021 Rayleigh and below-mean-plane rules. No favourable/weather/multiple-edge/lateral/reflection chain.',
        original_method_urls=['https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02002L0049-20210729',
            'https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj/eng'],
        independent_fixture_scope='Public TC05, TC06, TC07 homogeneous boundary expectations and intermediates; external engine not executed.',
        external_licence='NoiseModelling originals GPLv3, preserved under their licence and not ported into production code.',
        received_bytes=total, request_count=len(receipts), scientific_real_area_validation=False,
        acquisition_code_sha256=file_hash(Path(__file__)))
    (output/'manifest.json').write_bytes(json_bytes(manifest))
    shutil.copyfile(Path(__file__), output/Path(__file__).name)
    for n, p in parents.items():
        helper.verified(p); assert file_hash(p/'inventory.json')==hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output, 'diffraction-method-sources-'), receipts=receipts)), flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'artifacts/diffraction_method_sources_v1'))
    args = parser.parse_args(); run(args.output)
