"""Retain original method evidence and bounded pinned single-wall cut profiles."""
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
spec = importlib.util.spec_from_file_location('byte_helper', ROOT/'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
COMMIT = '1a0158958d3288752daef3a3a60b5f8bc1afacef'
RAW = 'https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/'+COMMIT+'/'
FILES = ('TC16_Reflection.json', 'TC17_Direct.json', 'TC17_Reflection.json', 'TC26_Direct.json', 'TC26_Reflection.json')


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents = {n: ROOT/'artifacts'/n for n in ('weather_sources_v1', 'weather_checks_v2', 'weather_review_v2')}
    for p in parents.values(): helper.verified(p)
    hashes = {n: file_hash(p/'inventory.json') for n,p in parents.items()}
    output.mkdir(parents=True)
    source = parents['weather_sources_v1']
    for name in ('eu-2015-annex.pdf', 'eu-2018-correction.pdf', 'eu-2021-amendment.pdf',
                 'NoiseModelling-CnossosTests.java', 'TC16_Direct.json', 'LICENSE', 'weather_receipts.json'):
        if (source/name).exists(): shutil.copyfile(source/name, output/name)
    # Preserve the exact licence filename and prior acquisition manifest.
    for p in source.iterdir():
        if 'licen' in p.name.lower() and p.is_file() and not (output/p.name).exists():
            shutil.copyfile(p, output/p.name)
    shutil.copyfile(source/'manifest.json', output/'parent_source_manifest.json')
    receipts, total = [], 0
    for name in FILES:
        url = RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/'+name
        with requests.get(url, stream=True, timeout=(15,40)) as r:
            r.raise_for_status(); chunks=[]; size=0
            for chunk in r.iter_content(65536):
                size += len(chunk); total += len(chunk)
                if size > 1024*1024 or total > 5*1024*1024: raise ValueError('Request budget exceeded')
                chunks.append(chunk)
            body = b''.join(chunks); json.loads(body); (output/name).write_bytes(body)
            receipts.append(dict(file=name, url=url, response_url=r.url, status=r.status_code, bytes=size,
                                 sha256=file_hash(output/name), retrieved_utc=datetime.now(timezone.utc).isoformat()))
        (output/'reflection_receipts.json').write_bytes(json_bytes(receipts))
        print(name+': retained', flush=True)
    manifest = dict(schema_version=1, commit=COMMIT, parent_inventory_sha256=hashes,
        request_count=len(FILES), received_bytes=total,
        method_pages='2015 original PDF pages41-43;2018 correction;2021 original PDF page9 / OJ L269/73.',
        scope='Single vertical-wall image path audit. TC16 and TC26 controlled clear reflected paths; TC17 retained for screened-path audit, not claimed as completed.',
        discovery=dict(query='site:eur-lex.europa.eu 2021 1226 "retro-diffraction" "reflection"',
            firecrawl_search_id='01a1285a-a051-7616-86d2-f6735b926392',
            result_url='https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:32021L1226',
            result_content='Search excerpts only; method equations read and visually reviewed in byte-retained original PDFs.'),
        licences='NoiseModelling original GPLv3 fixtures/expected tests unchanged; own mathematical implementation, no external implementation port.',
        external_engine_executed=False, full_current_method_qualified=False,
        acquisition_code_sha256=file_hash(Path(__file__)))
    (output/'manifest.json').write_bytes(json_bytes(manifest))
    shutil.copyfile(Path(__file__), output/Path(__file__).name)
    for n,p in parents.items(): helper.verified(p); assert file_hash(p/'inventory.json') == hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output, 'reflection-sources-'), received_bytes=total)), flush=True)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output', default=str(ROOT/'artifacts/reflection_sources_v1'))
    a = p.parse_args(); run(a.output)
