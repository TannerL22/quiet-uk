"""Pin primary method and independent mean-plane benchmark originals."""
import argparse
from datetime import datetime, timezone
import hashlib
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
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
COMMIT = '1a0158958d3288752daef3a3a60b5f8bc1afacef'
REPO = 'https://raw.githubusercontent.com/Universite-Gustave-Eiffel/NoiseModelling/' + COMMIT + '/'
TEST_PATH = 'noisemodelling-propagation/src/test/java/org/noise_planet/noisemodelling/propagation/AttenuationComputeOutputCnossosTest.java'
GEOMETRY_PATH = 'noisemodelling-pathfinder/src/main/java/org/noise_planet/noisemodelling/pathfinder/utils/geometry/JTSUtility.java'


def run(output):
    output=Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parents={n:ROOT/'artifacts'/n for n in ('road_emission_sources_v1','ground_barrier_method_review_v1','ground_cover_inputs_v3','ground_cover_review_v3')}
    for p in parents.values(): helper.verified(p)
    hashes={n:file_hash(p/'inventory.json') for n,p in parents.items()}
    output.mkdir(parents=True)
    discovery=ROOT/'artifacts/terrain_method_discovery_v1/documentation'
    shutil.copytree(discovery,output/'documentation')
    receipts=[]
    total=0
    for name,path in [('NoiseModelling-LICENSE.txt','LICENSE'),('NoiseModelling-validation.rst','Docs/Validation.rst'),
                      ('NoiseModelling-JTSUtility.java',GEOMETRY_PATH),('NoiseModelling-CnossosTests.java',TEST_PATH),
                      ('TC05_Direct.json','noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/TC05_Direct.json'),
                      ('TC06_Direct.json','noisemodelling-pathfinder/src/main/resources/org/noise_planet/noisemodelling/pathfinder/test_cases/TC06_Direct.json')]:
        started=datetime.now(timezone.utc).isoformat()
        with requests.get(REPO+path,stream=True,timeout=(15,45)) as response:
            response.raise_for_status()
            sha=hashlib.sha256();size=0
            with (output/name).open('wb') as stream:
                for chunk in response.iter_content(65536):
                    size+=len(chunk);total+=len(chunk)
                    if size>2*1024*1024 or total>6*1024*1024:raise ValueError('Bounded method-source budget exceeded')
                    stream.write(chunk);sha.update(chunk)
            receipts.append(dict(file=name,source_url=REPO+path,status=response.status_code,bytes=size,sha256=sha.hexdigest(),
                started_utc=started,completed_utc=datetime.now(timezone.utc).isoformat(),
                etag=response.headers.get('ETag'),last_modified=response.headers.get('Last-Modified')))
        (output/'receipts.json').write_bytes(json_bytes(receipts))
    publication=parents['road_emission_sources_v1']/'jrc-cnossos-2012.pdf'
    manifest=dict(schema_version=1,commit=COMMIT,repository='https://github.com/Universite-Gustave-Eiffel/NoiseModelling',
        fixture_cases=['TC05_Direct','TC06_Direct'], fixture_status='Original public rounded mean-plane expectations; external engine not executed.',
        external_licence='NoiseModelling source snapshots GPL v3; retained unchanged under their own licence, not added as a production dependency.',
        original_test_path=TEST_PATH,original_geometry_path=GEOMETRY_PATH,
        jrc_pdf_path=publication.relative_to(ROOT).as_posix(),jrc_pdf_sha256=file_hash(publication),
        checked_method='JRC2012 VI-3/4 (p77) continuous distance-weighted least squares; orthogonal heights (p84-85), plus 2021 selector as a later separate diffraction gate.',
        parent_inventory_sha256=hashes,download_bytes=total,request_count=len(receipts),
        acoustic_method_compliance_qualified=False,scientific_field_validation=False)
    shutil.copyfile(Path(__file__),output/Path(__file__).name)
    manifest['acquisition_code_sha256']=file_hash(Path(__file__))
    (output/'manifest.json').write_bytes(json_bytes(manifest))
    for n,p in parents.items():
        helper.verified(p)
        if file_hash(p/'inventory.json')!=hashes[n]:raise ValueError('Parent changed during acquisition')
    print(json.dumps(dict(generation_id=helper.seal(output,'terrain-method-sources-'),bytes=total,requests=len(receipts))),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'artifacts/terrain_method_sources_v1'))
    run(parser.parse_args().output)
