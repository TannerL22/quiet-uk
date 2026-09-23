"""Scientific boundary and HTTP regressions for the exploratory map product."""
import json
import math
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen, Request

import numpy as np
import pytest
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform

from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import Explorer, publish_display, colourize, file_hash, load_release, xyz_bounds
from quiet_uk.explorer_server import ExplorerServer, PlaceSearch
from test_catalogue import _build, BASE_EASTING, BASE_NORTHING


@pytest.fixture
def product(tmp_path):
    paths, _ = _build(tmp_path)
    dest = tmp_path/'display'
    publish_display(tmp_path/'catalogue', paths[0], paths[3], dest)
    return Explorer(dest, tmp_path/'catalogue', paths[0], paths[3]), paths


def test_display_preserves_qualification_and_land(product):
    explorer, _ = product
    with rasterio.open(explorer.root/'energy.tif') as ds:
        e = ds.read(1)
    with rasterio.open(explorer.root/'quality.tif') as ds:
        q = ds.read(1)
    assert q[0].tolist() == [1, 1, 1, 1, 2, 2, 0, 1]
    assert np.all(e[:, 4:6] == 0)  # road-source rejection cannot appear quiet
    assert e[0, 6] == 0  # sea is not zero-dB land
    assert math.isclose(10*math.log10(e[0, 0]), 60, abs_tol=1e-5)
    # Airport rejection does not suppress a qualified road/rail bound.
    assert math.isclose(10*math.log10(e[0, 2]), 61, abs_tol=1e-5)


def test_energy_overview_is_not_arithmetic_db_mean(product):
    explorer, _ = product
    with rasterio.open(explorer.root/'energy.tif') as ds:
        value = float(ds.read(1, out_shape=(1, 2), resampling=Resampling.average)[0, 0])
    expected = (10**6 + 10**6.1)/2
    assert math.isclose(value, expected, rel_tol=1e-6)
    assert not math.isclose(10*math.log10(value), 60.5, abs_tol=.001)


def test_lookup_export_is_exact_and_linked(product):
    explorer, _ = product
    lon, lat = transform('EPSG:27700','EPSG:4326',[BASE_EASTING+50],[BASE_NORTHING+150])
    value = explorer.lookup(lon[0], lat[0])
    assert value['bands']['road_rail_upper_db']['value'] == 60
    assert value['dataset']['release_id'] == explorer.manifest['release_id']
    assert len(value['dataset']['source_tile_sha256']) == 64
    assert value['dataset']['historical_acquisition_linkage'] == 'unresolved'
    assert value['cell_geojson']['geometry']['type'] == 'Polygon'
    assert value['reference_years']['road']['value'] is None
    aircraft = next(x for x in value['observations'] if x['field'] == 'airport_reported_lower_db')
    assert aircraft['value'] == value['bands']['airport_reported_lower_db']['value']
    assert aircraft['statistic'] == 'reported_lower_bound'
    assert aircraft['reference_period'] is None
    assert value['capabilities']['overall_exposure']['status'] == 'unavailable'
    assert value['capabilities']['temporal_metrics']['event_count']['status'] == 'unavailable'


def test_lookup_withheld_values_not_exported(product):
    explorer, _ = product
    lon, lat = transform('EPSG:27700','EPSG:4326',[BASE_EASTING+450],[BASE_NORTHING+150])
    value = explorer.lookup(lon[0],lat[0])
    assert value['bands']['road_rail_upper_db']['value'] is None
    assert value['bands']['road_rail_upper_db']['qualification'] == 'withheld_due_to_source_quality'


def test_release_checksum_tampering_fails(product):
    explorer, _ = product
    with (explorer.root/'energy.tif').open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(CatalogueIntegrityError, match='checksum'):
        load_release(explorer.root, explorer.catalogue_dir)


def test_source_tile_change_fails_location(product):
    explorer, paths = product
    with (paths[0]/'r0000c0000.tif').open('ab') as stream:
        stream.write(b'changed')
    lon, lat = transform('EPSG:27700','EPSG:4326',[BASE_EASTING+50],[BASE_NORTHING+150])
    with pytest.raises(CatalogueIntegrityError):
        explorer.lookup(lon[0],lat[0])


def test_publication_refuses_existing_and_does_not_change_inputs(product):
    explorer, paths = product
    old = file_hash(paths[0]/'r0000c0000.tif')
    with pytest.raises(FileExistsError):
        publish_display(explorer.catalogue_dir, paths[0], paths[3], explorer.root)
    assert file_hash(paths[0]/'r0000c0000.tif') == old


def test_unknown_is_not_a_quiet_colour():
    rgba = colourize(np.array([[20000., 20000., 0., 0.]]), np.array([[1, 2, 0, 1]]))
    assert rgba[0,0,3] > 0
    assert rgba[0,1,:3].tolist() == [158,158,158]
    assert rgba[0,2,3] == 0
    assert rgba[0,3,:3].tolist() == [158,158,158]


def test_aircraft_keeps_unreported_withheld_coverage_and_sea_distinct(product):
    explorer, _ = product
    with rasterio.open(explorer.root/'aircraft_energy.tif') as ds:
        energy = ds.read(1)
        assert ds.nodata == -1
    with rasterio.open(explorer.root/'aircraft_quality.tif') as ds:
        quality = ds.read(1)
    assert quality[0].tolist() == [1, 1, 2, 2, 1, 1, 0, 3]
    assert energy[1,1] == 0  # accepted, but unreported, not -1 missing
    assert energy[0,2] == -1  # airport rejected
    assert energy[0,7] == -1  # outside declared airport domain
    assert energy[0,4] > 0  # road rejection does not hide accepted aircraft
    rgba = colourize(energy, quality, mode='aircraft')
    assert rgba[1,1,:3].tolist() == [214,207,187]
    assert rgba[0,2,:3].tolist() == [158,158,158]
    assert rgba[0,6,3] == 0
    overlay = colourize(energy, quality, mode='aircraft_presence')
    assert overlay[0,0,:3].tolist() == [100,47,119]
    assert overlay[0,0,3] > 0
    assert overlay[1,1,3] == overlay[0,7,3] == 0


def test_aircraft_overview_keeps_zero_lower_energy_in_denominator(product):
    explorer, _ = product
    with rasterio.open(explorer.root/'aircraft_energy.tif') as ds:
        full = ds.read(1)
        overview = ds.read(1, out_shape=(1,4), resampling=Resampling.average)
    assert overview[0,0] == pytest.approx(full[:2,:2].sum()/4)
    assert overview[0,0] < full[0,0]  # unreported fourth cell is retained


def test_aircraft_file_integrity_is_checked(product):
    explorer, _ = product
    with (explorer.root/'aircraft_energy.tif').open('ab') as stream: stream.write(b'changed')
    with pytest.raises(CatalogueIntegrityError, match='checksum'):
        load_release(explorer.root, explorer.catalogue_dir)


@pytest.mark.parametrize('coords', [(float('nan'),53),(-2,float('inf')),(10,53),(-2,0)])
def test_invalid_coordinates(product, coords):
    with pytest.raises(ValueError):
        product[0].lookup(*coords)


@pytest.mark.parametrize('tile', [(4,0,0),(17,0,0),(5,-1,0),(5,0,32)])
def test_invalid_tile_indices(tile):
    with pytest.raises(ValueError):
        xyz_bounds(*tile)


def test_rendered_xyz_is_png(product):
    lon, lat = -.5,51.5
    z = 12
    x=int((lon+180)/360*2**z)
    y=int((1-math.asinh(math.tan(math.radians(lat)))/math.pi)/2*2**z)
    assert product[0].tile(z,x,y).startswith(b'\x89PNG\r\n\x1a\n')


def test_local_http_contract(product):
    explorer, _ = product
    class Search:
        def search(self, query): return [{'name':'Example', 'latitude':53,'longitude':-2}]
    server = ExplorerServer(0, explorer, Path(__file__).parents[1]/'explorer', Search())
    thread=threading.Thread(target=server.serve_forever, daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        assert json.load(urlopen(base+'/api/dataset'))['release_id'] == explorer.manifest['release_id']
        with urlopen(base+'/downloads/dataset.json') as response:
            assert 'attachment;' in response.headers['Content-Disposition']
            assert json.load(response)['release_id'] == explorer.manifest['release_id']
        lon,lat = transform('EPSG:27700','EPSG:4326',[BASE_EASTING+50],[BASE_NORTHING+150])
        with urlopen(base+f'/downloads/location.json?lon={lon[0]}&lat={lat[0]}') as response:
            assert 'attachment;' in response.headers['Content-Disposition']
            assert json.load(response)['bands']['road_rail_upper_db']['value']==60
        assert b'quiet' in urlopen(base+'/').read()
        assert json.load(urlopen(base+'/api/search?q=Example'))['places'][0]['name']=='Example'
        for mode in ('road_rail', 'aircraft', 'aircraft_presence'):
            assert urlopen(base+f'/tiles/{explorer.manifest["release_id"]}/{mode}/5/15/10.png').read().startswith(b'\x89PNG')
        for path, status in [('/api/location?lon=nan&lat=53',400),('/api/location?lon=-2&lon=-3&lat=53',400),('/../config.json',404),('/api/search?q=x&q=y',400),('/tiles/wrong/5/15/10.png',404)]:
            with pytest.raises(HTTPError) as error: urlopen(base+path)
            assert error.value.code==status
        for headers in [{'Origin':'https://example.com'},{'Host':'example.com'}]:
            with pytest.raises(HTTPError) as error: urlopen(Request(base+'/api/dataset',headers=headers))
            assert error.value.code==403
    finally:
        server.shutdown();server.server_close();thread.join(5)


def test_search_is_cached_not_autocomplete(monkeypatch):
    calls=[]
    class Response:
        def raise_for_status(self): pass
        def json(self): return [{'display_name':'York','lon':'-1.08','lat':'53.96'}]
    def get(url,**kwargs): calls.append(kwargs);return Response()
    monkeypatch.setattr('quiet_uk.explorer_server.requests.get',get)
    search=PlaceSearch()
    assert search.search('York') == search.search('york')
    assert len(calls)==1
    assert calls[0]['params']['countrycodes']=='gb'
    assert 'QuietUK' in calls[0]['headers']['User-Agent']
