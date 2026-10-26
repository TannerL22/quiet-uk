"""Freeze original EU multiple-edge equations and bounded public fixtures."""
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
    parents = {n:ROOT/'artifacts'/n for n in ('diffraction_method_sources_v1', 'terrain_boundary_checks_v1', 'terrain_boundary_review_v1')}
    for p in parents.values(): helper.verified(p)
    hashes = {n:file_hash(p/'inventory.json') for n, p in parents.items()}
    output.mkdir(parents=True)
    source = parents['diffraction_method_sources_v1']
    for name in ('NoiseModelling-LICENSE.txt', 'NoiseModelling-validation.rst', 'NoiseModelling-CnossosTests.java'):
        shutil.copyfile(source/name, output/name)
    shutil.copytree(source/'documentation', output/'documentation')
    receipts, total = [], 0
    requests_ = [(test+'_Direct.json', RAW+'noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/'+test+'_Direct.json', None)
                 for test in ('TC10', 'TC12', 'TC15')]
    requests_ += [('eu-2021-amendment.pdf', 'https://publications.europa.eu/resource/oj/JOL_2021_269_R_0004.ENG', {'Accept':'application/pdf'}),
                  ('eu-2021-metadata.rdf', 'https://publications.europa.eu/resource/celex/32021L1226', {'Accept':'application/rdf+xml'})]
    for name, url, headers in requests_:
        receipt = dict(file=name, requested_url=url, accept=headers, started_utc=datetime.now(timezone.utc).isoformat())
        with requests.get(url, headers=headers, stream=True, timeout=(15, 45)) as response:
            response.raise_for_status(); chunks = []; size = 0
            for chunk in response.iter_content(65536):
                size += len(chunk); total += len(chunk)
                if size > 12*1024*1024 or total > 16*1024*1024:
                    raise ValueError('Source request byte budget exceeded')
                chunks.append(chunk)
            body = b''.join(chunks)
            if name.endswith('.pdf') and not body.startswith(b'%PDF-'):
                raise ValueError('Original PDF required, access HTML excluded')
            if name.endswith('.json'): json.loads(body)
            (output/name).write_bytes(body)
            receipt.update(response_url=response.url, status=response.status_code, content_type=response.headers.get('Content-Type'),
                           retained_bytes=size, sha256=file_hash(output/name), completed_utc=datetime.now(timezone.utc).isoformat())
        receipts.append(receipt); (output/'receipts.json').write_bytes(json_bytes(receipts))
        print(name+': retained original', flush=True)
    manifest = dict(schema_version=1, parent_inventory_sha256=hashes, commit=COMMIT, received_bytes=total,
        request_count=len(receipts), acoustic_scope='Blocked homogeneous direct horizontal boundary terms, thick roofs and multiple ridges; image chains unchanged. No lateral/clear multi-edge/weather/reflection/annual levels.',
        coefficient_transcription='C_double_prime=1 for e<=0.3 m; otherwise (1+(5*lambda/e)^2)/(1/3+(5*lambda/e)^2), lambda=340/f. e is total length between first and last controlling edges.',
        original_equation_locator='2021/1226 Annex (9)(d), Eq 2.5.23 and Fig 2.5.d, PDF page 6 / OJ L269/70; visually inspected original.',
        external_fixture_scope='Original public TC10 cubic building, TC12 polygonal object, TC15 multiple buildings, DIRECT homogeneous boundary only. Engine not run.',
        external_licence='NoiseModelling originals GPLv3 preserved; no GPL implementation ported into production.',
        acquisition_code_sha256=file_hash(Path(__file__)), real_area_noise_validated=False)
    (output/'manifest.json').write_bytes(json_bytes(manifest)); shutil.copyfile(Path(__file__), output/Path(__file__).name)
    for n, p in parents.items():
        helper.verified(p); assert file_hash(p/'inventory.json') == hashes[n]
    print(json.dumps(dict(generation_id=helper.seal(output, 'multiple-edge-sources-'), received_bytes=total)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT/'artifacts/multiple_edge_sources_v1'))
    args = parser.parse_args(); run(args.output)
