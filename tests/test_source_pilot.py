"""Research-pilot regressions: missingness, grid phase, provenance and serving."""
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import urlopen, Request
import zipfile

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds
from rasterio.warp import transform
import requests

from quiet_uk import source_pilot as p
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer_server import ExplorerServer

GRID = b'<Coverage><RectifiedGrid srsName="EPSG:27700"><origin><pos>82650 657600</pos></origin><offsetVector>10 0</offsetVector><offsetVector>0 -10</offsetVector></RectifiedGrid></Coverage>'
META = '<h3>Period</h3><ul><li>From: 01 January 2021</li><li>To: 31 December 2021</li></ul></div>'


@pytest.fixture
def pilot(tmp_path, monkeypatch):
    def acquire(root):
        sites = {'heathrow': p.SITES['heathrow']}
        products, records = {}, []
        grid = p.native_grid(GRID)
        bounds = p.site_bounds(sites['heathrow'], grid)
        for source in p.PROVIDERS:
            meta = root/f'{source}.html'
            meta.write_text(META if source != 'aircraft' else '<h3>Period</h3><p>N/A</p></div>')
            product = {'metadata_id': p.PROVIDERS[source]['id'], 'metadata_file': meta.name,
                       'reference_period': p.provider_period(meta.read_text()),
                       'reference_period_status': 'unspecified_by_provider' if source == 'aircraft' else 'provider_declared',
                       'endpoint': 'https://example.org/wcs', 'requests_by_metric': {}}
            products[source] = product
            for metric in p.METRICS:
                identifier = f'{source}-{metric}'
                desc = root/f'{identifier}.xml'; desc.write_bytes(GRID)
                product['requests_by_metric'][metric] = {'version': '1.0.0', 'coverage_id': identifier, 'description_file': desc.name}
                path = root/f'{identifier}.tif'
                data = np.full((200, 200), 60., dtype='float32')
                data[0, :4] = [0, -96, 35 if metric == 'Lnight' else 40, 65]
                with rasterio.open(path, 'w', driver='GTiff', count=1, width=200, height=200,
                                   dtype='float32', crs='EPSG:27700', nodata=-96, transform=from_bounds(*bounds, 200, 200)) as ds:
                    ds.write(data, 1)
                request = requests.Request('GET', product['endpoint'], params=p.coverage_params(product, metric, bounds)).prepare()
                http = root/(path.name+'.http.json')
                http.write_bytes(p.json_bytes({'request_url': request.url, 'status': 200, 'sha256': p.file_hash(path)}))
                records.append({'id': 'heathrow-'+identifier, 'site': 'heathrow', 'source': source, 'metric': metric,
                                'path': path.name, 'http_record': http.name, 'sha256': p.file_hash(path),
                                'native_grid': grid, 'qa': p.inspect_raster(path, bounds, grid, metric)})
        return products, sites, records
    monkeypatch.setattr(p, 'acquire', acquire)
    p.publish(tmp_path)
    return p.SourcePilot(tmp_path)


def coords(pilot, row=0, col=0):
    with rasterio.open(pilot.root/pilot.manifest['records'][0]['path']) as ds:
        x, y = ds.xy(row, col)
    lon, lat = transform('EPSG:27700', 'EPSG:4326', [x], [y])
    return lon[0], lat[0]


def test_nodata_and_zero_are_never_zero_db_or_quiet_bounds(pilot):
    for col, raw in [(0, 0), (1, -96)]:
        observations = pilot.lookup('heathrow', *coords(pilot, col=col))['observations']
        assert all(o['value_db'] is None and o['status'] == 'unreported' and o['raw_value'] == raw for o in observations)
    qa = pilot.manifest['records'][0]['qa']
    assert qa['reported_cells'] == 39998
    assert qa['zero_sentinel_cells'] == qa['nodata_cells'] == 1


def test_night_retains_values_below_day_cutoff_and_periods(pilot):
    obs = pilot.lookup('heathrow', *coords(pilot, col=2))['observations']
    assert all(o['value_db'] == (35 if o['metric'] == 'Lnight' else 40) for o in obs)
    assert all(o['reference_period'] is None for o in obs if o['source'] == 'aircraft')
    assert all(o['reference_period']['start'] == '2021-01-01' for o in obs if o['source'] != 'aircraft')
    assert all(o['cell']['type'] == 'Polygon' for o in obs)
    assert len({o['source_sha256'] for o in obs}) >= 2


def test_outside_is_distinct_from_unreported(pilot):
    obs = pilot.lookup('heathrow', -2, 53)['observations']
    assert all(o['status'] == 'outside_pilot' and o['raw_value'] is None and o['cell'] is None for o in obs)


@pytest.mark.parametrize('point', [(float('nan'),51),(-1,float('inf')),(181,51),(-1,91)])
def test_invalid_coordinates_fail(pilot, point):
    with pytest.raises(ValueError): pilot.lookup('heathrow', *point)


def test_offline_reproduction_and_bundle(pilot):
    result = pilot.verify(reproduce=True)
    assert result['verified_records'] == 9 and result['display_reproduced']
    with zipfile.ZipFile(io.BytesIO(pilot.bundle())) as archive:
        assert 'src/quiet_uk/source_pilot.py' in archive.namelist()
        assert json.loads(archive.read('manifest.json'))['release_id'] == pilot.manifest['release_id']
    with pytest.raises(FileExistsError): p.publish(pilot.root)


def test_post_load_tampering_blocks_point_values(pilot):
    with (pilot.root/pilot.manifest['records'][0]['path']).open('ab') as stream: stream.write(b'changed')
    with pytest.raises(CatalogueIntegrityError, match='checksum'): pilot.lookup('heathrow', *coords(pilot))


def test_manifest_tampering_fails(pilot):
    manifest = pilot.manifest
    manifest['receiver_height_m'] = 1
    (pilot.root/'manifest.json').write_bytes(p.json_bytes(manifest))
    with pytest.raises(CatalogueIntegrityError, match='manifest'): p.SourcePilot(pilot.root)


def test_grid_phase_and_extent_are_checked(pilot):
    r = pilot.manifest['records'][0]
    bounds = r['qa']['bounds']
    with pytest.raises(ValueError, match='extent'):
        p.inspect_raster(pilot.root/r['path'], [v+5 for v in bounds], r['native_grid'], r['metric'])
    grid = {**r['native_grid'], 'center_origin': [82655,657600]}
    with pytest.raises(ValueError, match='shifted'):
        p.inspect_raster(pilot.root/r['path'], bounds, grid, r['metric'])


def test_unsupported_subthreshold_positive_value_rejected(pilot):
    r = pilot.manifest['records'][0]
    with rasterio.open(pilot.root/r['path'], 'r+') as ds:
        data = ds.read(1); data[0,0] = 12; ds.write(data,1)
    with pytest.raises(ValueError, match='noise value'):
        p.inspect_raster(pilot.root/r['path'], r['qa']['bounds'], r['native_grid'], 'Lden')


def test_reference_year_is_not_inferred_from_creation_date():
    assert p.provider_period('<h3>Creation</h3>29 September 2025<h3>Period</h3><p>N/A</p></div>') is None
    assert p.provider_period(META)['end'] == '2021-12-31'
    with pytest.raises(ValueError): p.provider_period('<h3>Creation</h3>2021')


def test_coverage_selection_excludes_major_and_rejects_ambiguity():
    name = 'id:Road_Noise_Lnight_England_Round_4_All'
    assert p.select_coverage([name, name.replace('All','Major')], 'road', 'Lnight') == name
    with pytest.raises(ValueError): p.select_coverage([name, 'other:'+name], 'road', 'Lnight')
    with pytest.raises(ValueError): p.select_coverage([name], 'rail', 'Lnight')


def test_capture_preserves_http_evidence_and_checks_resume(tmp_path, monkeypatch):
    calls = []
    def get(url, params, **kwargs):
        calls.append(url)
        response = requests.Response(); response.status_code = 200; response._content = b'original'
        response.request = requests.Request('GET',url,params=params).prepare(); response.url = response.request.url
        response.headers = {'Content-Type':'image/tiff', 'Set-Cookie':'not retained'}
        return response
    monkeypatch.setattr(p.requests, 'get', get)
    path = p.capture(tmp_path, 'sample.tif', 'https://example.org', [('subset','E(1,2)'),('subset','N(3,4)')])
    p.capture(tmp_path, 'sample.tif', 'https://example.org', [('subset','E(1,2)'),('subset','N(3,4)')])
    assert len(calls) == 1
    evidence = json.loads((tmp_path/'sample.tif.http.json').read_text())
    assert evidence['request_url'].count('subset=') == 2 and 'Set-Cookie' not in evidence['response_headers']
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='changed'): p.capture(tmp_path, 'sample.tif', 'https://example.org')


def test_pilot_http_routes_and_integrity(pilot):
    server = ExplorerServer(0, None, Path(__file__).parents[1]/'explorer', pilot=pilot)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    lon,lat = coords(pilot, col=2)
    try:
        assert b'id="inspect-centre"' in urlopen(base+'/pilot').read()
        assert json.load(urlopen(base+'/api/pilot'))['release_id'] == pilot.manifest['release_id']
        assert json.load(urlopen(base+f'/api/pilot/locate?lon={lon}&lat={lat}'))['sites'] == ['heathrow']
        assert json.load(urlopen(base+'/api/pilot/locate?lon=-2&lat=53'))['sites'] == []
        with urlopen(base+f'/downloads/pilot-location.json?site=heathrow&lon={lon}&lat={lat}') as response:
            assert 'attachment;' in response.headers['Content-Disposition']
            assert len(json.load(response)['observations']) == 9
        r = pilot.manifest['records'][0]
        image = f'/pilot-images/{pilot.manifest["release_id"]}/{r["id"]}.png'
        assert urlopen(base+image).read().startswith(b'\x89PNG')
        for path, code in [('/api/pilot/location?site=heathrow&lon=nan&lat=51',400),
                           ('/api/pilot/location?site=heathrow&site=didcot&lon=0&lat=51',400),
                           ('/pilot-images/../../config.json',404),('/api/pilot/unknown',404)]:
            with pytest.raises(HTTPError) as exc: urlopen(base+path)
            assert exc.value.code == code
        with pytest.raises(HTTPError) as exc: urlopen(Request(base+'/api/pilot',headers={'Origin':'https://example.org'}))
        assert exc.value.code == 403
        (pilot.root/r['display']['path']).write_bytes(b'changed')
        with pytest.raises(HTTPError) as exc: urlopen(base+image)
        assert exc.value.code == 503
    finally:
        server.shutdown(); server.server_close(); thread.join(5)


def test_regional_recipe_is_bounded_before_network(tmp_path, monkeypatch):
    monkeypatch.setattr(p, 'inventory', lambda root: pytest.fail('Invalid recipe must not contact provider'))
    for size in (0, -2000, 10020, 2001, 2000.5, True):
        with pytest.raises(ValueError):
            p.acquire(tmp_path, {'heathrow': {**p.SITES['heathrow'], 'size_m': size}})
    plan = p.acquisition_plan({k:{**s,'size_m':10000} for k,s in p.SITES.items()})
    assert plan['source_indicator_cells'] == 36_000_000
    assert plan['coverage_requests'] == 36 and plan['area_km2_sum'] == 400
    with pytest.raises(ValueError): p.acquire(tmp_path, {})
    with pytest.raises(ValueError): p.acquire(tmp_path, {'../escape': p.SITES['heathrow']})


def test_changed_recipe_cannot_resume_into_same_directory(tmp_path, monkeypatch):
    sites = {'heathrow': {**p.SITES['heathrow'],'size_m':2000}}
    (tmp_path/'acquisition_plan.json').write_bytes(p.json_bytes(p.acquisition_plan(sites)))
    monkeypatch.setattr(p, 'inventory', lambda root: pytest.fail('Must reject changed recipe before networking'))
    with pytest.raises(ValueError, match='recipe changed'):
        p.acquire(tmp_path, {'heathrow': {**p.SITES['heathrow'],'size_m':10000}})


def test_enlarged_bounds_preserve_original_grid_and_native_dimensions():
    grid = p.native_grid(GRID)
    small = p.site_bounds(p.SITES['heathrow'],grid)
    large = p.site_bounds({**p.SITES['heathrow'],'size_m':10000},grid)
    assert np.array(large)-small == pytest.approx([-4000,-4000,4000,4000])
    product = {'requests_by_metric':{'Lnight':{'coverage_id':'night','version':'1.0.0'}}}
    params = dict(p.coverage_params(product,'Lnight',large))
    assert params['width'] == params['height'] == '1000'
    assert p.grid_shape(large) == (1000,1000)
    with pytest.raises(ValueError): p.grid_shape([0,0,10005,10000])


def hydration(data):
    return '<script id="__NEXT_DATA__" type="application/json">'+json.dumps({'props':{'pageProps':{'dataset':data}}})+'</script>'


def test_client_rendered_metadata_period_is_identity_linked():
    data = {'entryType':'dataSet','id':p.PROVIDERS['road']['id'],
            'createdAt':1702036764000,'temporalExtent':{'begin':'2021-01-01','end':'2021-12-31'}}
    assert p.provider_period(hydration(data),data['id']) == {'start':'2021-01-01','end':'2021-12-31'}
    with pytest.raises(ValueError,match='identity'): p.provider_period(hydration(data),p.PROVIDERS['rail']['id'])
    data.pop('temporalExtent')
    assert p.provider_period(hydration(data)) is None
    with pytest.raises(ValueError,match='Conflicting'): p.provider_period(hydration(data)+META)


@pytest.mark.parametrize('extent', [{'begin':'2021-01-01'}, {'begin':'2022-01-01','end':'2021-12-31'}, {'begin':'unknown','end':'2021-12-31'}])
def test_malformed_provider_period_rejected(extent):
    with pytest.raises(ValueError):
        p.provider_period(hydration({'id':p.PROVIDERS['road']['id'],'entryType':'dataSet','temporalExtent':extent}))


def test_locate_uses_native_bounds_and_returns_no_coverage_for_poles(pilot):
    assert pilot.locate(*coords(pilot)) == ['heathrow']
    assert pilot.locate(-2,53) == []
    assert pilot.locate(0,90) == []
    assert all(o['status']=='outside_pilot' for o in pilot.lookup('heathrow',0,90)['observations'])
    with pytest.raises(ValueError): pilot.locate(float('nan'),51)


@pytest.mark.parametrize('change', ['none','value','phase','extent','period'])
def test_overlap_gate_checks_values_grid_and_period(tmp_path, change):
    small = np.array([[0,40,41],[-96,65,66],[70,71,72]],dtype='float32')
    large = np.full((9,9),55,dtype='float32'); large[3:6,3:6] = small
    if change == 'value': large[4,4] += .5
    paths = []
    for name, data, bounds in [('small',small,[30,30,60,60]), ('large',large,[5,0,95,90] if change=='phase' else [0,0,90,90])]:
        if change == 'extent' and name == 'large': bounds = [50,50,140,140]
        path = tmp_path/(name+'.tif'); paths.append(path)
        with rasterio.open(path,'w',driver='GTiff',count=1,width=data.shape[1],height=data.shape[0],dtype='float32',crs='EPSG:27700',nodata=-96,transform=from_bounds(*bounds,data.shape[1],data.shape[0])) as ds: ds.write(data,1)
    def dataset(path, year):
        record = {'id':'heathrow-road-Lden','source':'road','metric':'Lden','path':path.name}
        manifest = {'units':'dB(A)','receiver_height_m':4,'spatial_resolution_m':10,'release_id':path.stem,
                    'records':[record],'products':{'road':{'metadata_id':'road','reference_period':year,'requests_by_metric':{'Lden':{'coverage_id':'road-Lden'}}}}}
        return SimpleNamespace(manifest=manifest,records={record['id']:record},verified_path=lambda name:tmp_path/name)
    reference, candidate = dataset(paths[0],2021),dataset(paths[1],2022 if change=='period' else 2021)
    if change == 'none':
        assert p.compare_reference(candidate,reference)['compared_cells'] == 9
    else:
        with pytest.raises(CatalogueIntegrityError): p.compare_reference(candidate,reference)
