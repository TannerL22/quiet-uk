"""Freeze bounded primary method documentation and published 2021 coefficients."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes

PDF_URL = 'https://publications.jrc.ec.europa.eu/repository/bitstream/JRC72550/cnossos-eu%20jrc%20reference%20report_final_on%20line%20version_10%20august%202012.pdf'


def published_coefficients(markdown):
    """Only the complete replacement F-1 and selected F-4 rows, no fitted values."""
    block = markdown.split('Table F-1 is replaced by the following:',1)[1].split('Table F-4 is replaced by the following:',1)[0]
    categories, current = {}, None
    for line in block.splitlines():
        cells = [s.strip().strip('‘’') for s in line.strip().split('|')[1:-1]]
        if len(cells)==9 and cells[0] in ('AR','BR','AP','BP'):
            cells.insert(0,'')  # merged category cell in the official table
        if len(cells)!=10 or cells[1] not in ('AR','BR','AP','BP'):
            continue
        if cells[0]: current = cells[0]
        if current=='5':
            continue  # open category has no published coefficients
        values = [float(s.replace(',','.')) for s in cells[2:]]
        if current not in ('1','2','3','4a','4b') or cells[1] in categories.setdefault(current,{}):
            raise ValueError('Unexpected/duplicate F-1 row')
        categories[current][cells[1]] = values
    if set(categories)!= {'1','2','3','4a','4b'} or any(set(c)!= {'AR','BR','AP','BP'} for c in categories.values()):
        raise ValueError('Incomplete replacement F-1')
    block = markdown.split('Table F-4 is replaced by the following:',1)[1].split('(20)',1)[0]
    surfaces, current = {}, None
    for line in block.splitlines():
        cells = [s.strip().strip('‘’') for s in line.strip().split('|')[1:-1]]
        if not cells:
            continue
        try: float(cells[-1].replace(',','.'))
        except ValueError: continue
        if len(cells)==13:
            current = cells[0]
            if current not in ('Reference road surface','Thin layer A','Worked surface'):
                continue
            surfaces[current] = {'minimum_speed_kmh':None if cells[1]=='--' else float(cells[1]),
                                 'maximum_speed_kmh':None if cells[2]=='--' else float(cells[2]),'categories':{}}
            cat, values = cells[3], cells[4:]
        elif len(cells)>=10:
            cat, values = cells[0],cells[1:10]
        else:
            continue
        if current not in surfaces:
            continue
        values = [float(s.replace(',','.')) for s in values]
        for identity in ('4a','4b') if cat=='4a/4b' else (cat,):
            if identity in surfaces[current]['categories']:
                raise ValueError('Duplicate surface row')
            surfaces[current]['categories'][identity] = {'alpha':values[:8],'beta':values[8]}
    if set(surfaces)!= {'Reference road surface','Thin layer A','Worked surface'} or any(set(s['categories'])!=set(categories) for s in surfaces.values()):
        raise ValueError('Incomplete selected replacement F-4')
    return dict(frequencies_hz=[63,125,250,500,1000,2000,4000,8000],reference_speed_kmh=70,
                coefficient_source='2021/1226 Appendix F replacements F-1 and F-4',categories=categories,surfaces=surfaces)


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Use fresh workspace sources')
    docs = ROOT/'artifacts/road_emission_documentation_v1'
    amendment = json.loads((docs/'eu-2021-amendment.json').read_bytes())
    coefficients = published_coefficients(amendment['markdown'])
    output.mkdir(parents=True)
    shutil.copytree(docs,output/'documentation')
    receipts = []
    with requests.get(PDF_URL,stream=True,timeout=(15,60),headers={
            'User-Agent':'Quiet-UK-research/0.2 (bounded road-emission source qualification)'}) as response:
        receipt = dict(url=PDF_URL,final_url=response.url,status_code=response.status_code,
                       retrieved_at_utc=datetime.now(timezone.utc).isoformat(),content_type=response.headers.get('Content-Type'))
        response.raise_for_status()
        total = 0
        with (output/'jrc-cnossos-2012.pdf').open('wb') as stream:
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total>8*1024*1024: raise ValueError('PDF exceeds 8 MiB budget')
                stream.write(chunk)
        if not (output/'jrc-cnossos-2012.pdf').read_bytes().startswith(b'%PDF-'):
            raise ValueError('Method response is not PDF')
        receipt['bytes'] = total
        receipts.append(receipt)
    (output/'published_2021_coefficients.json').write_bytes(json_bytes(coefficients))
    (output/'receipts.json').write_bytes(json_bytes(receipts))
    shutil.copyfile(Path(__file__),output/Path(__file__).name)
    (output/'manifest.json').write_bytes(json_bytes(dict(
        reference_method='JRC72550 (2012) methodological reference; 2021/1226 F-1 and F-4 source coefficients',
        regulatory_compliance_claim=False,request_count=1,byte_budget=8*1024*1024,
        source_documentation_limit='Some 2015/consolidated EUR-Lex pages yielded navigation or a robot challenge, retained explicitly; not qualified as complete annex text.',
        original_publication_url='https://publications.jrc.ec.europa.eu/repository/handle/JRC72550',
        coefficient_url='https://eur-lex.europa.eu/eli/dir_del/2021/1226/oj/eng',
        coefficient_document_sha256=file_hash(output/'documentation/eu-2021-amendment.json'),
        attributions=['European Union, JRC72550, 2012; DOI 10.2788/31776',
                      'European Union, Commission Delegated Directive (EU) 2021/1226, Appendix F',
                      'Department for Transport national 2021 tables, Open Government Licence v3.0'],
        acquisition_code_sha256=file_hash(Path(__file__)))))
    files = {p.relative_to(output).as_posix():file_hash(p) for p in output.rglob('*') if p.is_file()}
    (output/'inventory.json').write_bytes(json_bytes(dict(
        generation_id='road-emission-sources-'+hashlib.sha256(json_bytes(files)).hexdigest()[:20],files=files)))
    print(json.dumps({'pdf_bytes':total,'categories':list(coefficients['categories']),'surfaces':list(coefficients['surfaces'])}),flush=True)


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'artifacts/road_emission_sources_v1'))
    args = parser.parse_args()
    run(args.output)
