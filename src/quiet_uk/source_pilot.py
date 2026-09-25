"""Small, source-linked WCS acquisition; originals are immutable evidence.

This pilot deliberately retains provider grids and missing values. It neither
infers a quiet-end value nor adds sources with unverified temporal compatibility.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import copy
import shutil
from html import unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import platform
import io
import re
import xml.etree.ElementTree as ET
import zipfile

import numpy as np
import rasterio
from rasterio.warp import transform, calculate_default_transform, reproject
from rasterio.enums import Resampling
import requests

from .explorer import file_hash, json_bytes, rgba_png
from .catalogue import CatalogueIntegrityError
from .evidence_semantics import CONTRACT, interpret, record_evidence
from .locking import ResourceLock

PROVIDERS = {
    'road': {'id': '562c9d56-7c2d-4d42-83bb-578d6e97a517',
             'service': 'road-noise-all-metrics-england-round-4', 'version': '1.0.0'},
    'rail': {'id': '3fb3c2d7-292c-4e0a-bd5b-d8e4e1fe2947',
             'service': 'noise-data', 'version': '1.0.0'},
    'aircraft': {'id': 'dac9cba4-abe7-43bd-b8e9-8a83da52edd8',
                 'service': 'airport-noise-all-metrics-england-round-4', 'version': '2.0.1'},
}
METRICS = {
    'Lden': 'Annual day-evening-night indicator; evening +5 dB, night +10 dB.',
    'Lday': 'Annual average daytime sound energy, 07:00–19:00.',
    'Lnight': 'Annual average night-time sound energy, 23:00–07:00.',
}
SITES = {
    'heathrow': {'name': 'Heathrow · airport', 'center': [-0.447622, 51.464852]},
    'didcot': {'name': 'Didcot · railway', 'center': [-1.2425, 51.6110]},
    'oxford': {'name': 'Oxford · city', 'center': [-1.2577, 51.7520]},
    'chilterns': {'name': 'Chilterns · countryside', 'center': [-0.969, 51.641]},
}
COLOURS = ['#b1dddd', '#258b92', '#bfd0a2', '#e0d498', '#deb16e', '#cb855f', '#ab605d', '#77465b']
BREAKS = [40, 45, 50, 55, 60, 65, 70]
MAX_SITE_SIZE_M = 10000
MAX_PILOT_CELLS = 36_000_000  # 4 x 10 km squares x 9 indicators; 144 MB float32.


def acquisition_plan(sites):
    """Validate a bounded recipe before opening a provider connection."""
    if not sites or len(sites) > 4:
        raise ValueError('Choose between one and four pilot areas')
    geographic_cells = 0
    for key, site in sites.items():
        if not re.fullmatch('[a-z]+', key):
            raise ValueError('Area identifiers must contain lowercase letters only')
        size = site.get('size_m', 2000)
        if isinstance(size, bool) or not isinstance(size, int) or not 20 <= size <= MAX_SITE_SIZE_M or size % 20:
            raise ValueError('Area size must be a multiple of 20 m, at most 10000 m')
        lon, lat = site['center']
        if not np.isfinite([lon, lat]).all() or not (-8 <= lon <= 3 and 49 <= lat <= 56):
            raise ValueError('Pilot centers must be finite coordinates within the England bounding region')
        geographic_cells += (size//10)**2
    cells = geographic_cells * len(PROVIDERS) * len(METRICS)
    if cells > MAX_PILOT_CELLS:
        raise ValueError('Pilot exceeds the bounded acquisition budget')
    return {'sites': sites, 'sources': list(PROVIDERS), 'metrics': list(METRICS),
            'coverage_requests': len(sites)*len(PROVIDERS)*len(METRICS),
            'source_indicator_cells': cells, 'uncompressed_float32_bytes': cells*4,
            'area_km2_sum': geographic_cells*100/1_000_000,
            'note': 'Area sums may overlap for custom recipes. Transfer size differs from raw array size.'}


def capture(root, name, url, params=None, required=True):
    """Save exact response bytes and the prepared request, including failures.

    No retry replaces a previous response. Resume reuses only hash-verified HTTP
    successes. A failed attempt remains on disk and requires a new destination.
    """
    root = Path(root)
    path, record_path = root/name, root/(name+'.http.json')
    if record_path.exists():
        record = json.loads(record_path.read_text('utf-8'))
        prepared = requests.Request('GET', url, params=params).prepare().url
        if record['request_url'] != prepared or file_hash(path) != record['sha256']:
            raise ValueError('Acquisition evidence changed: '+name)
        if required and record['status'] != 200:
            raise ValueError('Previous failed response retained: '+name)
        return path
    if path.exists():
        raise ValueError('Incomplete acquisition evidence: '+name)
    path.parent.mkdir(parents=True, exist_ok=True)
    response = requests.get(url, params=params, timeout=(15, 180),
                            headers={'User-Agent': 'QuietUK-SourcePilot/1.0'})
    path.write_bytes(response.content)
    record = {'request_url': response.request.url, 'response_url': response.url,
              'retrieved_at_utc': datetime.now(timezone.utc).isoformat(),
              'status': response.status_code, 'sha256': file_hash(path),
              'bytes': len(response.content),
              'response_headers': {k: v for k, v in response.headers.items()
                                   if k.lower() in ('content-type', 'etag', 'last-modified', 'date')},
              'redirects': [{'url': r.url, 'status': r.status_code} for r in response.history]}
    record_path.write_bytes(json_bytes(record))
    if required:
        response.raise_for_status()
    if required and response.status_code != 200:
        raise ValueError('Expected complete HTTP response: '+name)
    return path


def coverage_ids(xml):
    root = ET.fromstring(xml)
    return sorted({node.text.strip() for node in root.iter()
                   if node.tag.rsplit('}', 1)[-1] in ('name', 'CoverageId') and node.text})


def select_coverage(identifiers, source, metric):
    family = 'airport' if source == 'aircraft' else source
    candidates = []
    for identifier in identifiers:
        tokens = set(re.split(r'[_:\-]+', identifier.lower()))
        if {family, metric.lower(), 'all'} <= tokens and not tokens & {'major', 'octave'}:
            candidates.append(identifier)
    if len(candidates) != 1:
        raise ValueError(f'Expected one {source}/{metric} all-sources coverage: {candidates}')
    return candidates[0]


def inventory(root, *, capture_response=None):
    root = Path(root)
    capture = capture_response or globals()['capture']
    products = {}
    for source, provider in PROVIDERS.items():
        base = f'https://environment.data.gov.uk/spatialdata/{provider["service"]}/wcs'
        capture(root, f'evidence/{source}.html', f'https://environment.data.gov.uk/dataset/{provider["id"]}')
        metadata = capture(root, f'evidence/{source}.xml',
                           'https://environment.data.gov.uk/discover/defra/csw',
                           {'id': provider['id'], 'request': 'GetRecordById'}, required=False)
        caps = capture(root, f'evidence/{source}-capabilities.xml', base,
                       {'service': 'WCS', 'request': 'GetCapabilities', 'version': provider['version']})
        ids = coverage_ids(caps.read_bytes())
        selected = {metric: select_coverage(ids, source, metric) for metric in METRICS}
        requests_by_metric = {}
        for metric, identifier in selected.items():
            name = f'evidence/{source}-{metric}-description.xml'
            description = capture(root, name, base,
                    {'service': 'WCS', 'version': provider['version'], 'request': 'DescribeCoverage',
                     'coverage' if provider['version'] == '1.0.0' else 'coverageId': identifier})
            version = provider['version']
            fallback = None
            if ET.fromstring(description.read_bytes()).tag.rsplit('}', 1)[-1] == 'ServiceExceptionReport' and version == '1.0.0':
                # The live road Lnight 1.0 capabilities advertise a rejected wksp
                # prefix. Discover 2.0 identifiers independently; never rewrite it.
                alternate = capture(root, f'evidence/{source}-capabilities-2.xml', base,
                                    {'service': 'WCS', 'version': '2.0.1', 'request': 'GetCapabilities'})
                identifier = select_coverage(coverage_ids(alternate.read_bytes()), source, metric)
                version, fallback = '2.0.1', 'advertised_wcs10_identifier_rejected'
                name = f'evidence/{source}-{metric}-description-2.xml'
                description = capture(root, name, base, {'service': 'WCS', 'version': version,
                                      'request': 'DescribeCoverage', 'coverageId': identifier})
            native_grid(description.read_bytes())
            requests_by_metric[metric] = {'version': version, 'coverage_id': identifier,
                                         'description_file': name, 'fallback_reason': fallback}
        products[source] = {'endpoint': base, 'version': provider['version'], 'coverage_ids': selected,
                            'requests_by_metric': requests_by_metric,
                            'metadata_id': provider['id'], 'metadata_file': f'evidence/{source}.html'}
    return products


class _DatasetPayload(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.active, self.parts = False, []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = dict(attrs).get('id') == '__NEXT_DATA__'

    def handle_endtag(self, tag):
        if tag == 'script':
            self.active = False

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)


def provider_period(html, expected_id=None):
    """Read explicit temporal extent from rendered or hydrated provider metadata.

    Defra can serve a client-rendered page whose dataset is in __NEXT_DATA__.
    Neither format permits using a publication/creation date as an exposure year.
    """
    parser = _DatasetPayload()
    parser.feed(html)
    structured = False
    if parser.parts:
        data = json.loads(''.join(parser.parts))['props']['pageProps']['dataset']
        if data.get('entryType') != 'dataSet' or data.get('id') not in {p['id'] for p in PROVIDERS.values()} or (expected_id and data['id'] != expected_id):
            raise ValueError('Provider metadata dataset identity mismatch')
        extent = data.get('temporalExtent')
        structured = True
        period = None
        if extent:
            try:
                begin, end = (datetime.strptime(extent[k], '%Y-%m-%d').date().isoformat() for k in ('begin', 'end'))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError('Incomplete provider temporal extent') from exc
            if begin > end:
                raise ValueError('Reversed provider temporal extent')
            period = {'start': begin, 'end': end}
    match = re.search(r'>Period</h3>(.*?)</div>', html, re.S)
    if not match:
        if structured:
            return period
        raise ValueError('Provider metadata no longer exposes a Period field')
    text = unescape(re.sub('<[^>]+>', ' ', match[1]))
    if 'N/A' in text:
        rendered = None
    else:
        dates = re.findall(r'(?:From|To):\s*(\d{2} \w+ \d{4})', text)
        if len(dates) != 2:
            raise ValueError('Unrecognised provider reference period')
        rendered = dict(zip(('start', 'end'), (datetime.strptime(x, '%d %B %Y').date().isoformat() for x in dates)))
        if rendered['start'] > rendered['end']:
            raise ValueError('Reversed provider temporal extent')
    if structured and rendered != period:
        raise ValueError('Conflicting rendered and structured reference periods')
    return rendered


def native_grid(xml):
    root = ET.fromstring(xml)
    grids = [e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'RectifiedGrid']
    if len(grids) != 1:
        raise ValueError('Expected one rectified provider grid')
    grid = grids[0]
    def values(tag):
        return [list(map(float, e.text.split())) for e in grid.iter()
                if e.tag.rsplit('}', 1)[-1] == tag and e.text]
    origin, offsets = values('pos'), values('offsetVector')
    if len(origin) != 1 or offsets != [[10., 0.], [0., -10.]]:
        raise ValueError('Unsupported provider native grid')
    grid_crs = {e.attrib['srsName'] for e in grid.iter() if 'srsName' in e.attrib}
    if not grid_crs or not grid_crs <= {'EPSG:27700', 'http://www.opengis.net/def/crs/EPSG/0/27700'}:
        raise ValueError('Expected British National Grid')
    return {'center_origin': origin[0], 'offset_vectors': offsets, 'crs': 'EPSG:27700'}


def site_bounds(site, grid):
    acquisition_plan({'site': site})
    x, y = transform('EPSG:4326', 'EPSG:27700', [site['center'][0]], [site['center'][1]])
    ox, oy = grid['center_origin']
    cx, cy = ox + np.floor((x[0]-ox)/10)*10, oy + np.floor((y[0]-oy)/10)*10
    half = site.get('size_m', 2000)/2
    return [float(cx-half-5), float(cy-half-5), float(cx+half-5), float(cy+half-5)]


def grid_shape(bounds, *, max_cells=1000):
    if len(bounds) != 4 or not np.isfinite(bounds).all():
        raise ValueError('Expected four finite grid bounds')
    w, s, e, n = bounds
    dimensions = np.array([(n-s)/10, (e-w)/10])
    if np.any(dimensions <= 0) or np.any(dimensions > max_cells) or not np.allclose(dimensions, np.round(dimensions), rtol=0, atol=1e-7):
        raise ValueError(f'Grid bounds must describe at most {max_cells} native 10 m cells per axis')
    return tuple(int(round(x)) for x in dimensions)


def coverage_params(product, metric, bounds, *, max_cells=1000):
    w, s, e, n = bounds
    height, width = grid_shape(bounds, max_cells=max_cells)
    request = product['requests_by_metric'][metric]
    identifier, version = request['coverage_id'], request['version']
    common = [('service', 'WCS'), ('version', version), ('request', 'GetCoverage')]
    if version == '1.0.0':
        return common + [('coverage', identifier), ('bbox', ','.join(map(str, bounds))),
                         ('crs', 'EPSG:27700'), ('response_crs', 'EPSG:27700'),
                         ('width', str(width)), ('height', str(height)), ('format', 'GeoTIFF')]
    # Native cell-edge subsets, without a scaling extension or interpolation.
    return common + [('coverageId', identifier), ('format', 'image/tiff'),
                     ('subset', f'E({w},{e})'), ('subset', f'N({s},{n})')]


def inspect_raster(path, bounds, grid, metric, *, max_cells=1000):
    with rasterio.open(path) as ds:
        if ds.crs != rasterio.crs.CRS.from_epsg(27700) or ds.count != 1:
            raise ValueError('Expected a single-band EPSG:27700 raster')
        if ds.shape != grid_shape(bounds, max_cells=max_cells) or not np.allclose(ds.bounds, bounds, rtol=0, atol=1e-7):
            raise ValueError(f'Unexpected raster extent/shape: {ds.bounds} {ds.shape}; expected {bounds}')
        t = ds.transform
        if not np.allclose((t.a, t.b, t.d, t.e), (10, 0, 0, -10), rtol=0, atol=1e-7):
            raise ValueError('Returned grid is not native 10 m')
        origin = grid['center_origin']
        phase = [(t.c+5-origin[0])/10, (t.f-5-origin[1])/10]
        if not np.allclose(phase, np.round(phase), rtol=0, atol=1e-7):
            raise ValueError('Returned raster is shifted from provider grid')
        if ds.nodata is None or not np.isfinite(ds.nodata):
            raise ValueError('Explicit finite nodata required')
        data = ds.read(1, masked=True)
        present = data.compressed()
        cutoff = 35 if metric == 'Lnight' else 40
        if not np.all(np.isfinite(present)) or np.any((present < 0) | (present > 160) | ((present > 0) & (present < cutoff))):
            raise ValueError('Unrecognised noise value')
        # Provider uses zero in addition to TIFF nodata. Neither is a measured
        # zero dB nor proof that exposure is below a particular threshold.
        valid = present[present >= cutoff]
        return {'shape': list(ds.shape), 'bounds': list(ds.bounds), 'transform': list(t),
                'crs': ds.crs.to_string(), 'nodata': ds.nodata, 'dtype': ds.dtypes[0],
                'reported_cells': int(valid.size), 'unreported_cells': int(data.size-valid.size),
                'zero_sentinel_cells': int(np.count_nonzero(present == 0)),
                'nodata_cells': int(np.ma.getmaskarray(data).sum()),
                'reported_min_db': float(valid.min()) if valid.size else None,
                'reported_max_db': float(valid.max()) if valid.size else None}


def acquire(root, sites=None):
    root = Path(root)
    if (root/'manifest.json').exists():
        raise FileExistsError('Published pilot already exists; use a new destination')
    selected_sites = SITES if sites is None else sites
    plan = acquisition_plan(selected_sites)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root/'acquisition_plan.json'
    if plan_path.exists() and json.loads(plan_path.read_text('utf-8')) != plan:
        raise ValueError('Acquisition recipe changed; choose a new destination')
    if not plan_path.exists():
        plan_path.write_bytes(json_bytes(plan))
    products = inventory(root)
    records = []
    for source, product in products.items():
        product['reference_period'] = provider_period((root/product['metadata_file']).read_text('utf-8'), product['metadata_id'])
        product['reference_period_status'] = 'provider_declared' if product['reference_period'] else 'unspecified_by_provider'
        for metric in METRICS:
            grid = native_grid((root/product['requests_by_metric'][metric]['description_file']).read_bytes())
            for site_id, site in selected_sites.items():
                bounds = site_bounds(site, grid)
                suffix = '-native' if product['requests_by_metric'][metric]['version'] == '2.0.1' else ''
                name = f'raw/{site_id}-{source}-{metric}{suffix}.tif'
                path = capture(root, name, product['endpoint'], coverage_params(product, metric, bounds))
                qa = inspect_raster(path, bounds, grid, metric)
                records.append({'id': f'{site_id}-{source}-{metric}', 'site': site_id, 'source': source,
                                'metric': metric, 'path': name, 'sha256': file_hash(path),
                                'http_record': name+'.http.json', 'native_grid': grid, 'qa': qa})
                print(f'{site_id}/{source}/{metric}: {qa["reported_cells"]}/{np.prod(qa["shape"])} reported', flush=True)
    return products, selected_sites, records


def display_image(path, *, evidence_version=None):
    """A nearest-cell Web Mercator display; analytical files are never warped."""
    with rasterio.open(path) as ds:
        t, width, height = calculate_default_transform(ds.crs, 'EPSG:3857', ds.width, ds.height, *ds.bounds)
        # -9999 marks outside the rotated source rectangle; -96/zero inside
        # remain visibly unreported. Both differ from genuinely reported values.
        data = ds.read(1).astype('float64')
        data[ds.read_masks(1) == 0] = -1 if evidence_version else 0
        warped = np.full((height, width), -9999., dtype='float64')
        reproject(data, warped, src_transform=ds.transform, src_crs=ds.crs,
                  dst_transform=t, dst_crs='EPSG:3857', dst_nodata=-9999,
                  resampling=Resampling.nearest)
    rgba = np.zeros((*warped.shape, 4), dtype='uint8')
    present = warped > 0
    colours = np.array([[int(c[i:i+2], 16) for i in (1, 3, 5)] for c in COLOURS], dtype='uint8')
    rgba[present, :3] = colours[np.searchsorted(BREAKS, warped[present], side='right')]
    rgba[present, 3] = 230
    yy, xx = np.indices(warped.shape)
    unknown = warped == 0
    rgba[unknown, :3] = [192, 184, 165]
    rgba[unknown, 3] = np.where(((xx+yy) % 8)[unknown] < 2, 210, 130)
    if evidence_version:
        missing = warped == -1
        rgba[missing, :3] = [170, 181, 196]
        rgba[missing, 3] = np.where(((xx-yy) % 8)[missing] < 2, 210, 130)
    corners = [t*(0, 0), t*(width, 0), t*(width, height), t*(0, height)]
    lon, lat = transform('EPSG:3857', 'EPSG:4326', *zip(*corners))
    return rgba_png(rgba), list(map(list, zip(lon, lat)))


def publish(root, sites=None):
    with ResourceLock(root, 'source pilot'):
        return _publish(root, sites)


def _publish(root, sites=None):
    root = Path(root)
    if (root/'manifest.json').exists():
        raise FileExistsError('Refusing to overwrite a published pilot')
    products, sites, records = acquire(root) if sites is None else acquire(root, sites)
    (root/'display').mkdir(exist_ok=True)
    for record in records:
        png, corners = display_image(root/record['path'])
        name = f'display/{record["id"]}.png'
        (root/name).write_bytes(png)
        record['display'] = {'path': name, 'corners': corners, 'resampling': 'nearest',
                             'analytical_use': False}
        w, s, e, n = record['qa']['bounds']
        lon, lat = transform('EPSG:27700', 'EPSG:4326', [w, e, e, w, w], [n, n, s, s, n])
        record['footprint'] = {'type': 'Polygon', 'coordinates': [list(map(list, zip(lon, lat)))]}
    # Pin construction code and dependency versions alongside downloaded bytes.
    repo = Path(__file__).resolve().parents[2]
    for original in [*Path(__file__).parent.glob('*.py'), repo/'scripts/30_source_pilot.py', repo/'requirements-windows-py314-amd64.lock']:
        target = root/original.relative_to(repo)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(original.read_bytes())
    files = {p.relative_to(root).as_posix(): file_hash(p) for p in sorted(root.rglob('*'))
             if p.is_file() and p.name not in ('manifest.json', 'manifest.pending')}
    manifest = {'schema_version': 1, 'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'title': 'Quiet UK · 10 m source and time-of-day explorer',
                'status': 'exploratory_source_linked_pilot', 'products': products, 'sites': sites,
                'acquisition_plan': acquisition_plan(sites),
                'metrics': METRICS, 'records': records, 'files': files,
                'units': 'dB(A)', 'receiver_height_m': 4, 'spatial_resolution_m': 10,
                'reporting_cutoff_db': {'Lden': 40, 'Lday': 40, 'Lnight': 35},
                'aircraft_cutoff_note': 'These are minimum cutoffs; actual airport thresholds vary.',
                'missing_value_policy': 'TIFF nodata and zero are unreported, not silence. Below-cutoff and missing/domain gaps are not separable here.',
                'uncertainty': 'No cell-level uncertainty supplied. Grid spacing is not demonstrated accuracy.',
                'unavailable': ['compatible_all_source_total', 'background_LA90', 'event_maxima', 'event_counts', 'quiet_intervals'],
                'retained_nonproduct_rasters': [name for name in files if name.startswith('raw/') and name.endswith('.tif') and name not in {r['path'] for r in records}],
                'audit_note': 'Only records[] identifies accepted analytical rasters. Any failed requests or nonproduct rasters are retained as evidence, not mapped or used in analysis. The saved public dataset HTML supplies the reference period; optional CSW responses may be errors.',
                'licence': 'Open Government Licence v3.0',
                'licence_url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                'attribution': '© Department for Environment, Food & Rural Affairs copyright and/or database right 2023 (road/rail), 2024 (airport).',
                'display': {'colours': COLOURS, 'breaks_db': BREAKS, 'method': 'nearest native cell, reprojected for display only'},
                'runtime': {'python': platform.python_version(), 'rasterio': rasterio.__version__,
                            'gdal': rasterio.__gdal_version__, 'numpy': np.__version__, 'requests': requests.__version__}}
    manifest['release_id'] = 'pilot-'+hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    # Publication is the final step. A failed acquisition never gets a manifest.
    pending = root/'manifest.pending'
    pending.write_bytes(json_bytes(manifest))
    pending.replace(root/'manifest.json')
    return manifest


def publish_interpretation(parent_root, output):
    """Publish changed meanings/derivatives without acquiring or modifying originals."""
    parent = SourcePilot(parent_root)
    if parent.manifest['schema_version'] != 1:
        raise ValueError('This migration requires an original schema-1 source release')
    parent.verify()
    output = Path(output).resolve()
    if output == parent.root or output.is_relative_to(parent.root) or parent.root.is_relative_to(output):
        raise ValueError('Interpretation output must be separate from its parent')
    with ResourceLock(output, 'source interpretation'):
        if output.exists():
            raise FileExistsError('Use a new interpretation destination')
        output.mkdir(parents=True)
        for name in parent.manifest['files']:
            target = output/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(parent.verified_path(name), target)
        (output/'parent-manifest.json').write_bytes((parent.root/'manifest.json').read_bytes())
        manifest = copy.deepcopy(parent.manifest)
        manifest.pop('release_id')
        manifest.pop('reporting_cutoff_db', None)
        manifest.pop('aircraft_cutoff_note', None)
        manifest.update(schema_version=2, parent_release_id=parent.manifest['release_id'],
                        parent_manifest='parent-manifest.json', evidence_contract=copy.deepcopy(CONTRACT),
                        created_at_utc=datetime.now(timezone.utc).isoformat(),
                        missing_value_policy=CONTRACT['missing_value_policy'])
        (output/'display-evidence-v1').mkdir()
        for record in manifest['records']:
            record['evidence'] = record_evidence(record, manifest['products'][record['source']], manifest['files'])
            png, corners = display_image(output/record['path'], evidence_version=CONTRACT['version'])
            name = f'display-evidence-v1/{record["id"]}.png'
            (output/name).write_bytes(png)
            record['display'] = {'path': name, 'corners': corners, 'resampling': 'nearest', 'analytical_use': False}
        # Preserve the parent construction code; pin this interpretation separately.
        repo = Path(__file__).resolve().parents[2]
        for original in [*Path(__file__).parent.glob('*.py'), repo/'scripts/30_source_pilot.py', repo/'requirements-windows-py314-amd64.lock']:
            target = output/'interpretation'/original.relative_to(repo)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
        manifest['files'] = {p.relative_to(output).as_posix(): file_hash(p) for p in sorted(output.rglob('*')) if p.is_file()}
        manifest['display']['evidence_contract_version'] = CONTRACT['version']
        manifest['release_id'] = 'pilot-'+hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
        (output/'manifest.pending').write_bytes(json_bytes(manifest))
        (output/'manifest.pending').replace(output/'manifest.json')
    return manifest


class SourcePilot:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = json.loads((self.root/'manifest.json').read_text('utf-8'))
        base = {k: v for k, v in self.manifest.items() if k != 'release_id'}
        expected = 'pilot-'+hashlib.sha256(json_bytes(base)).hexdigest()[:20]
        if self.manifest.get('schema_version') not in (1, 2) or self.manifest['release_id'] != expected:
            raise CatalogueIntegrityError('Pilot manifest checksum/schema mismatch')
        if self.manifest['schema_version'] == 2 and self.manifest.get('evidence_contract') != CONTRACT:
            raise CatalogueIntegrityError('Unsupported evidence contract')
        self.records = {r['id']: r for r in self.manifest['records']}
        for name in self.manifest['files']:
            self.verified_path(name)
        if self.manifest['schema_version'] == 2:
            parent = json.loads(self.verified_path(self.manifest['parent_manifest']).read_text('utf-8'))
            base = {k: v for k, v in parent.items() if k != 'release_id'}
            if (parent['release_id'] != self.manifest['parent_release_id']
                    or parent['release_id'] != 'pilot-'+hashlib.sha256(json_bytes(base)).hexdigest()[:20]
                    or any(parent[key] != self.manifest[key] for key in ('products', 'sites', 'metrics', 'units', 'receiver_height_m', 'spatial_resolution_m'))
                    or any(self.manifest['files'].get(name) != digest for name, digest in parent['files'].items())):
                raise CatalogueIntegrityError('Interpretation parent mismatch')
            originals = {r['id']: r for r in parent['records']}
            if set(originals) != set(self.records):
                raise CatalogueIntegrityError('Interpretation changed analytical layers')
            for record in self.records.values():
                if ({k: v for k, v in record.items() if k not in ('display', 'evidence')}
                        != {k: v for k, v in originals[record['id']].items() if k not in ('display', 'evidence')}
                        or self.manifest['files'][record['path']] != record['sha256']
                        or record['evidence'] != record_evidence(record, self.manifest['products'][record['source']], self.manifest['files'])):
                    raise CatalogueIntegrityError('Interpretation changed original evidence')

    def verified_path(self, name):
        if name not in self.manifest['files']:
            raise CatalogueIntegrityError('Unknown pilot file')
        path = (self.root/name).resolve()
        if not path.is_relative_to(self.root) or file_hash(path) != self.manifest['files'][name]:
            raise CatalogueIntegrityError('Pilot file checksum/path mismatch: '+name)
        return path

    def locate(self, lon, lat):
        """Find coverage using native analytical bounds, not display PNG bounds."""
        if not np.isfinite([lon, lat]).all() or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError('Supply finite longitude/latitude in range')
        # Avoid undefined polar projections; outside England is outside this pilot.
        if not (-8 <= lon <= 3 and 49 <= lat <= 56):
            return []
        x, y = transform('EPSG:4326', 'EPSG:27700', [lon], [lat])
        if not np.isfinite([x[0], y[0]]).all():
            raise ValueError('Coordinates cannot be projected into the source grid')
        matches = set()
        for record in self.manifest['records']:
            w, s, e, n = record['qa']['bounds']
            if w <= x[0] < e and s < y[0] <= n:
                matches.add(record['site'])
        return [site for site in self.manifest['sites'] if site in matches]

    def open_raster(self, name):
        return rasterio.open(self.verified_path(name))

    def lookup(self, site, lon, lat):
        if site not in self.manifest['sites'] or not np.isfinite([lon, lat]).all() or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError('Choose a pilot site and finite longitude/latitude')
        projectable = -8 <= lon <= 3 and 49 <= lat <= 56
        x, y = transform('EPSG:4326', 'EPSG:27700', [lon], [lat]) if projectable else ([0], [0])
        if projectable and not np.isfinite([x[0], y[0]]).all():
            raise ValueError('Coordinates cannot be projected into the source grid')
        observations = []
        for record in self.manifest['records']:
            if record['site'] != site:
                continue
            product = self.manifest['products'][record['source']]
            with self.open_raster(record['path']) as ds:
                row, col = ds.index(x[0], y[0]) if projectable else (-1, -1)
                inside = 0 <= row < ds.height and 0 <= col < ds.width
                raw, value, cell = None, None, None
                status = 'outside_pilot'
                evidence = None
                if inside:
                    raw = float(ds.read(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0])
                    cutoff = self.manifest.get('reporting_cutoff_db', {}).get(record['metric'], 35 if record['metric'] == 'Lnight' else 40)
                    status = 'reported_model_value' if raw != ds.nodata and raw >= cutoff else 'unreported'
                    value = raw if status == 'reported_model_value' else None
                    pts = [ds.transform*(col+dx, row+dy) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
                    lons, lats = transform(ds.crs, 'EPSG:4326', *zip(*pts))
                    cell = {'type': 'Polygon', 'coordinates': [list(map(list, zip(lons, lats)))]}
                if self.manifest['schema_version'] == 2:
                    masked = inside and not bool(ds.read_masks(1, window=rasterio.windows.Window(col, row, 1, 1))[0, 0])
                    evidence = interpret(raw, inside=inside, masked=masked)
                    status, value = evidence['status'], evidence['value_db']
                observations.append({'record_id': record['id'], 'source': record['source'], 'metric': record['metric'],
                                     'value_db': value, 'raw_value': raw, 'status': status,
                                     'units': 'dB(A)', 'reference_period': product['reference_period'],
                                     'reference_period_status': product['reference_period_status'],
                                     'source_sha256': record['sha256'], 'http_record': record['http_record'],
                                     'coverage_id': product['requests_by_metric'][record['metric']]['coverage_id'],
                                     'cell': cell, 'row': row if inside else None, 'column': col if inside else None,
                                     **({**record['evidence'], **evidence} if evidence else {})})
        return {'release_id': self.manifest['release_id'], 'site': site, 'longitude': lon, 'latitude': lat,
                'observations': observations,
                **({'evidence_contract_version': CONTRACT['version'], 'parent_release_id': self.manifest['parent_release_id']} if self.manifest['schema_version'] == 2 else {}),
                'missing_value_policy': self.manifest['missing_value_policy'],
                'uncertainty': self.manifest['uncertainty'], 'unavailable': self.manifest['unavailable']}

    def verify(self, reproduce=False):
        """Offline QA against the saved provider responses, with optional PNG replay."""
        if 'acquisition_plan' in self.manifest:
            expected_plan = acquisition_plan(self.manifest['sites'])
            if self.manifest['acquisition_plan'] != expected_plan:
                raise CatalogueIntegrityError('Acquisition plan differs from published areas')
            if 'acquisition_plan.json' in self.manifest['files'] and json.loads(self.verified_path('acquisition_plan.json').read_text('utf-8')) != expected_plan:
                raise CatalogueIntegrityError('Saved acquisition recipe differs from manifest')
        for record in self.manifest['records']:
            source, metric = record['source'], record['metric']
            product = self.manifest['products'][source]
            grid = native_grid(self.verified_path(product['requests_by_metric'][metric]['description_file']).read_bytes())
            if grid != record['native_grid']:
                raise CatalogueIntegrityError('Native grid evidence mismatch')
            bounds = site_bounds(self.manifest['sites'][record['site']], grid)
            qa = inspect_raster(self.verified_path(record['path']), bounds, grid, metric)
            if qa != record['qa']:
                raise CatalogueIntegrityError('Pilot scientific QA mismatch')
            http = json.loads(self.verified_path(record['http_record']).read_text('utf-8'))
            request = requests.Request('GET', product['endpoint'], params=coverage_params(product, metric, bounds)).prepare()
            if http['request_url'] != request.url or http['status'] != 200 or http['sha256'] != record['sha256'] or record['sha256'] != self.manifest['files'][record['path']]:
                raise CatalogueIntegrityError('Pilot acquisition chain mismatch')
            if provider_period(self.verified_path(product['metadata_file']).read_text('utf-8'), product['metadata_id']) != product['reference_period']:
                raise CatalogueIntegrityError('Reference period evidence mismatch')
            if self.manifest['schema_version'] == 2:
                expected = record_evidence(record, product, self.manifest['files'])
                if record.get('evidence') != expected:
                    raise CatalogueIntegrityError('Evidence interpretation differs from recorded sources')
            if reproduce:
                png, corners = display_image(self.verified_path(record['path']), evidence_version=CONTRACT['version'] if self.manifest['schema_version'] == 2 else None)
                if png != self.verified_path(record['display']['path']).read_bytes() or corners != record['display']['corners']:
                    raise CatalogueIntegrityError('Display reproduction mismatch')
        return {'release_id': self.manifest['release_id'], 'verified_records': len(self.records),
                'verified_files': len(self.manifest['files']), 'display_reproduced': reproduce}

    def bundle(self):
        """Small in-memory convenience export; serving uses write_bundle instead."""
        output = io.BytesIO()
        self.write_bundle(output)
        return output.getvalue()

    def write_bundle(self, output):
        """Write a portable archive to a path or file, with bounded file buffers."""
        with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr('manifest.json', json_bytes(self.manifest))
            for name in self.manifest['files']:
                digest = hashlib.sha256()
                with self.verified_path(name).open('rb') as incoming, bundle.open(name, 'w', force_zip64=True) as outgoing:
                    while chunk := incoming.read(1024 * 1024):
                        digest.update(chunk)
                        outgoing.write(chunk)
                if digest.hexdigest() != self.manifest['files'][name]:
                    raise CatalogueIntegrityError('Evidence changed during export: '+name)
                self.verified_path(name)


def compare_reference(candidate, reference):
    """Require exact native-cell preservation over every reference raster.

    This checks acquisition consistency, not independent acoustic accuracy.
    No interpolation or tolerance hides a shifted grid or changed provider value.
    """
    for field in ('units', 'receiver_height_m', 'spatial_resolution_m'):
        if candidate.manifest[field] != reference.manifest[field]:
            raise CatalogueIntegrityError('Reference comparison semantics differ: '+field)
    results = []
    for original in reference.manifest['records']:
        r = candidate.records.get(original['id'])
        if not r:
            raise CatalogueIntegrityError('Reference layer missing: '+original['id'])
        source, metric = original['source'], original['metric']
        a, b = (obj.manifest['products'][source] for obj in (reference, candidate))
        if a['metadata_id'] != b['metadata_id'] or a['reference_period'] != b['reference_period'] or a['requests_by_metric'][metric]['coverage_id'] != b['requests_by_metric'][metric]['coverage_id']:
            raise CatalogueIntegrityError('Reference comparison source/period differs: '+original['id'])
        with rasterio.open(reference.verified_path(original['path'])) as small, rasterio.open(candidate.verified_path(r['path'])) as large:
            if small.crs != large.crs or small.res != large.res or small.nodata != large.nodata:
                raise CatalogueIntegrityError('Reference comparison grids/encoding differ')
            window = rasterio.windows.from_bounds(*small.bounds, transform=large.transform)
            values = [window.row_off, window.col_off, window.height, window.width]
            if not np.allclose(values, np.round(values), rtol=0, atol=1e-7):
                raise CatalogueIntegrityError('Reference grid phase differs')
            if window.row_off < 0 or window.col_off < 0 or window.row_off+window.height > large.height or window.col_off+window.width > large.width:
                raise CatalogueIntegrityError('Candidate does not contain reference extent')
            window = window.round_offsets().round_lengths()
            before, after = small.read(1), large.read(1, window=window)
            changed = int(np.count_nonzero(before != after))
            if changed or not np.array_equal(small.read_masks(1), large.read_masks(1, window=window)):
                raise CatalogueIntegrityError(f'Reference values/masks changed: {original["id"]} ({changed} values)')
            results.append({'record_id': r['id'], 'compared_cells': int(before.size), 'changed_cells': 0})
    return {'reference_release': reference.manifest['release_id'], 'candidate_release': candidate.manifest['release_id'],
            'compared_cells': sum(r['compared_cells'] for r in results), 'layers': results,
            'qualification': 'Exact cell consistency; not independent acoustic validation.'}
