"""Acquire native EA DTM/DSM and regional OS building footprints, bounded GETs.

Original responses are immutable after inventory.json is written. Interrupted
downloads may resume using the receipt ledger; incomplete files are not inputs.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import threading
from urllib.parse import urlparse

import requests
import rasterio
from rasterio.transform import from_origin

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes

HALO = (501000, 173000, 513000, 185000)
CORE = (506000, 178000, 508000, 180000)
PRODUCTS = {
    'dtm': ('https://environment.data.gov.uk/spatialdata/lidar-composite-digital-terrain-model-dtm-2m/wcs',
            '09ea3b37-df3a-4e8b-ac69-fb0842227b04__Lidar_Composite_Elevation_DTM_2m'),
    'dsm': ('https://environment.data.gov.uk/spatialdata/lidar-composite-digital-surface-model-last-return-dsm-2m/wcs',
            'f083c5dc-504f-4428-9811-a1b2519fa279__Lidar_Composite_Elevation_LZ_DSM_2m'),
}
MAX_REQUESTS = 100
MAX_TOTAL = 1024 * 1024 * 1024


def run(output, discovery):
    output, discovery = Path(output).resolve(), Path(discovery).resolve()
    if not output.is_relative_to(ROOT / 'artifacts') or not discovery.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use workspace artifact directories')
    if (output / 'inventory.json').exists():
        raise ValueError('Completed source generations are immutable')
    output.mkdir(parents=True, exist_ok=True)
    parent = ROOT / 'artifacts/road_input_audit_v1'
    parent_inventory = json.loads((parent / 'inventory.json').read_bytes())
    for name, digest in parent_inventory['files'].items():
        if file_hash(parent / name) != digest:
            raise ValueError('Parent audit checksum mismatch')
    catalog = json.loads((discovery / 'os-downloads-unfiltered.json').read_bytes())
    matches = [r for r in catalog if r['area'] == 'TQ' and 'Shapefile' in r['format']]
    if len(matches) != 1 or matches[0]['fileName'] != 'opmplc_essh_tq.zip':
        raise ValueError('Unexpected regional building source')
    os_record = matches[0]
    if not 0 < os_record['size'] < 300 * 1024 * 1024:
        raise ValueError('Unexpected OS download size')
    metadata = output / 'metadata'
    metadata.mkdir(exist_ok=True)
    discovery_files = {}
    for path in sorted(discovery.iterdir()):
        if path.is_file() and path.suffix != '.tif':
            target = metadata / path.name
            digest = file_hash(path)
            if target.exists() and file_hash(target) != digest:
                raise ValueError('Discovery snapshot changed during resume')
            if not target.exists():
                shutil.copyfile(path, target)
            discovery_files[path.name] = digest
    state_path = output / 'acquisition-state.json'
    state = json.loads(state_path.read_bytes()) if state_path.exists() else {'receipts': [], 'failures': []}
    lock = threading.Lock()

    def save_state():
        temp = state_path.with_suffix('.tmp')
        temp.write_bytes(json_bytes(state))
        temp.replace(state_path)

    def fetch(name, url, params=None, limit=12 * 1024 * 1024, expected_size=None, md5=None):
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with lock:
            previous = [r for r in state['receipts'] if r['file'] == name]
            if previous:
                if len(previous) != 1 or not path.exists() or file_hash(path) != previous[0]['sha256']:
                    raise ValueError('Resume checksum mismatch')
                return path
            if path.exists():
                raise ValueError('Unreceipted completed source file')
            if len(state['receipts']) + len(state['failures']) >= MAX_REQUESTS:
                raise ValueError('Request budget exhausted')
        started = datetime.now(timezone.utc).isoformat()
        sha, md, size = hashlib.sha256(), hashlib.md5(), 0
        part = path.with_suffix(path.suffix + '.part')
        try:
            with requests.get(url, params=params, stream=True, timeout=(15, 90)) as response:
                response.raise_for_status()
                if any(urlparse(r.url).scheme != 'https' for r in [*response.history, response]):
                    raise ValueError('Non-HTTPS download redirect')
                with part.open('wb') as stream:
                    for chunk in response.iter_content(1024 * 1024):
                        size += len(chunk)
                        with lock:
                            total = sum(r['response_bytes'] for r in state['receipts'])
                        if size > limit or total + size > MAX_TOTAL:
                            raise ValueError('Byte budget exceeded')
                        stream.write(chunk)
                        sha.update(chunk)
                        md.update(chunk)
                if expected_size is not None and size != expected_size:
                    raise ValueError('Provider size mismatch')
                # OS catalogue MD5 is occasionally rendered without leading zeros.
                if md5 is not None and md.hexdigest() != md5.lower().zfill(32):
                    raise ValueError('Provider MD5 mismatch')
                receipt = {'file': name, 'method': 'GET', 'url': response.request.url,
                           'response_url': response.url, 'status': response.status_code,
                           'started_utc': started, 'completed_utc': datetime.now(timezone.utc).isoformat(),
                           'response_bytes': size, 'sha256': sha.hexdigest(), 'md5': md.hexdigest(),
                           'headers': {key: response.headers.get(key) for key in
                                       ('Content-Type', 'Content-Length', 'Last-Modified', 'ETag')},
                           'redirects': [{'url': r.url, 'status': r.status_code,
                                          'location': r.headers.get('Location')} for r in response.history]}
                with lock:
                    part.replace(path)
                    state['receipts'].append(receipt)
                    save_state()
                return path
        except Exception as error:
            with lock:
                state['failures'].append({'file': name, 'started_utc': started,
                                          'url': url, 'error': str(error), 'partial_bytes': size})
                save_state()
            raise

    tasks = []
    for kind, (url, coverage) in PRODUCTS.items():
        for south in range(HALO[1], HALO[3], 2000):
            for west in range(HALO[0], HALO[2], 2000):
                bounds = (west, south, west + 2000, south + 2000)
                name = f'{kind}/{kind}_{west}_{south}_2m.tif'
                params = [('service', 'WCS'), ('version', '2.0.1'), ('request', 'GetCoverage'),
                          ('coverageId', coverage), ('subset', f'E({west},{west+2000})'),
                          ('subset', f'N({south},{south+2000})'), ('format', 'image/tiff')]
                tasks.append((name, url, params, bounds))

    def tile(task):
        name, url, params, bounds = task
        path = fetch(name, url, params)
        with rasterio.open(path) as raster:
            if (raster.crs != rasterio.crs.CRS.from_epsg(27700) or raster.count != 1
                    or raster.shape != (1000, 1000) or tuple(raster.bounds) != bounds
                    or raster.transform != from_origin(bounds[0], bounds[3], 2, 2)
                    or raster.dtypes != ('float32',) or raster.nodata is None):
                raise ValueError(f'Native grid mismatch: {name}')
        return name

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(tile, task) for task in tasks]
        for index, future in enumerate(as_completed(futures), 1):
            name = future.result()
            if index % 8 == 0 or index == len(tasks):
                print(f'Native tiles checked: {index}/{len(tasks)} ({name})', flush=True)
    archive = fetch('buildings/opmplc_essh_tq.zip', os_record['url'],
                    limit=300 * 1024 * 1024, expected_size=os_record['size'], md5=os_record['md5'])
    print(f'OS regional source verified: {archive.stat().st_size} bytes', flush=True)
    # Recheck discovery and parent snapshots before publishing the completion marker.
    for name, digest in discovery_files.items():
        if file_hash(discovery / name) != digest:
            raise ValueError('Discovery snapshot changed')
    for name, digest in parent_inventory['files'].items():
        if file_hash(parent / name) != digest:
            raise ValueError('Parent audit changed')
    source = {'schema_version': 1, 'core_bounds_bng': CORE, 'halo_bounds_bng': HALO,
              'resolution_m': 2, 'resampling': None, 'products': PRODUCTS,
              'os_record': os_record, 'os_product_version': json.loads((metadata / 'os-product.json').read_bytes())['version'],
              'parent_inventory_sha256': file_hash(parent / 'inventory.json'),
              'discovery_files': discovery_files, 'acquisition_code_sha256': file_hash(Path(__file__)),
              'source_dates': 'EA 2022 composite; local survey dates are in the retained survey indices',
              'vertical_datum': 'metres above Ordnance Datum Newlyn; OSTN15 per EA metadata',
              'licenses': {'EA': 'Open Government Licence v3.0', 'OS': 'Open Government Licence v3.0'},
              'attributions': ['© Environment Agency copyright and/or database right 2022. All rights reserved.',
                               'Contains OS data © Crown copyright and database right 2026.'],
              'request_count': len(state['receipts']), 'download_bytes': sum(r['response_bytes'] for r in state['receipts'])}
    (output / 'source-manifest.json').write_bytes(json_bytes(source))
    files = {str(p.relative_to(output)).replace('\\', '/'): file_hash(p)
             for p in sorted(output.rglob('*')) if p.is_file() and not p.name.endswith('.part')}
    inventory = {'generation_id': 'physical-scene-sources-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20],
                 'files': files}
    (output / 'inventory.json').write_bytes(json_bytes(inventory))
    print(json.dumps({'generation_id': inventory['generation_id'], 'bytes': source['download_bytes'],
                      'requests': source['request_count']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/physical_scene_sources_v1'))
    parser.add_argument('--discovery', default=str(ROOT / 'artifacts/physical_scene_discovery_v1'))
    args = parser.parse_args()
    run(args.output, args.discovery)
