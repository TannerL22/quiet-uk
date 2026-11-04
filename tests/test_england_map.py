"""England map: display pixels and point lookups read the same owning native cell."""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sqlite3
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import warnings

import numpy as np
from PIL import Image
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.transform import from_origin

from quiet_uk import england_acquisition as ea
from quiet_uk import england_map as m
from quiet_uk.evidence_semantics import CONTRACT_V2, PROVIDER_REPLY, interpret, zero_rule
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.source_pilot import BREAKS

ROOT = Path(__file__).parents[1]
WEST_TILE, EAST_TILE = 'E525005N175005', 'E535005N175005'
BOUNDARY = 535005
TOP = 180005
HALO_VALUE = 99.0  # Only the west tile's one-cell halo holds this; the east tile owns those cells.

warnings.filterwarnings('ignore', message='Best transformation is not available')


def geotiff(values, west, north, nodata):
    with MemoryFile() as memory:
        with memory.open(driver='GTiff', width=values.shape[1], height=values.shape[0], count=1, dtype=values.dtype,
                         crs='EPSG:27700', transform=from_origin(west, north, 10, 10), nodata=nodata) as ds:
            ds.write(values, 1)
        return memory.read()


def road(rows, cols, shift):
    values = (40 + (np.add.outer(np.arange(rows), np.arange(cols) + shift) % 35) + .37).astype('float32')
    values[5:15, 5:15] = 0      # Calculated below the cutoff.
    values[20:25, 30:40] = -96  # Provider nodata.
    values[30, 30] = 40.0       # Exactly at the inclusive cutoff: a reported value.
    return values


def rail(rows, cols, shift):
    values = np.zeros((rows, cols), dtype='float32')
    values[:, (np.arange(cols) + shift) % 17 == 0] = 61.25
    return values


@pytest.fixture(scope='module')
def acquisition(tmp_path_factory):
    root = tmp_path_factory.mktemp('england')/'england_acquisition_v1'
    rasters = {  # tile: (west, cols, column shift); 60 rows ending at TOP.
        WEST_TILE: (534005, 101, 0),
        EAST_TILE: (534995, 101, 99),
    }
    records = []
    for tile, (west, cols, shift) in rasters.items():
        for source in ('road', 'rail', 'aircraft'):
            for metric in ('Lden', 'Lday', 'Lnight'):
                record = {'tile': tile, 'source': source, 'metric': metric}
                if source == 'aircraft' and tile == WEST_TILE:
                    records.append({**record, 'request_bounds': None, 'status': 'outside_provider_rectangle'})
                    continue
                if source == 'aircraft':
                    values = np.full((60, cols), 3.4e38)
                    values[40:50, 50:60] = 57.5
                    data = geotiff(values, west, TOP, 3.4e38)
                else:
                    values = (road if source == 'road' else rail)(60, cols, shift)
                    if tile == WEST_TILE:
                        values[:, -1] = HALO_VALUE
                    data = geotiff(values, west, TOP, -96)
                path = f'archive/{tile}/{source}-{metric}.tif.zst'
                ea.write_archive(root/path, data)
                bounds = [west, TOP - 600, west + cols*10, TOP]
                records.append({**record, 'request_bounds': bounds, 'status': 'accepted', 'path': path,
                                'http_record': f'attempts/{tile}-{source}-{metric}.json',
                                'sha256': hashlib.sha256(data).hexdigest(), 'qa': {'bounds': bounds}, 'archive': 'zstd'})
    for record in records:
        target = root/ea.record_name(record['tile'], record['source'], record['metric'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(record), encoding='utf-8')
    plan = {'tiles': [{'id': WEST_TILE}, {'id': EAST_TILE}, {'id': 'E545005N175005'}]}
    (root/'plan.json').write_text(json.dumps(plan), encoding='utf-8')
    period = {'start': '2021-01-01', 'end': '2021-12-31'}
    products = {s: {'metadata_id': s+'-id', 'reference_period': None if s == 'aircraft' else period,
                    'reference_period_status': 'unspecified_by_provider' if s == 'aircraft' else 'provider_declared'}
                for s in ('road', 'rail', 'aircraft')}
    (root/'products.json').write_text(json.dumps(products), encoding='utf-8')
    return root


@pytest.fixture(scope='module')
def built(acquisition):
    output = acquisition.parent/'england_map_v1'
    m.build(acquisition, output, workers=0, allow_incomplete=True, log=lambda *a, **k: None)
    return output


def lonlat(england, east, north):
    return england.transformer.transform(east, north, direction='INVERSE')


def expected_class(observation):
    if observation['status'] == 'outside_extract':
        return m.OUTSIDE
    if observation['status'] == 'unreported_unknown':
        return m.NODATA
    if observation['status'] == 'verified_below_cutoff':
        return m.BELOW
    return m.FIRST_BIN + int(np.searchsorted(BREAKS, observation['value_db'], side='right'))


def palette_class(index):
    return m.OUTSIDE if index == 0 else m.NODATA if index in (1, 2) else index - 1


def test_defra_zero_rule_bounds_road_and_rail_zeros_only():
    road_zero = interpret(0, inside=True, domain=True, verified_zero_rule=zero_rule('road', 'Lden'))
    assert road_zero['status'] == 'verified_below_cutoff' and road_zero['value_db'] is None
    assert road_zero['bounds'] == {'lower_db': None, 'upper_db': 40, 'lower_inclusive': False, 'upper_inclusive': False}
    assert road_zero['display_label'] == 'Under 40 dB(A)'
    night = interpret(0, inside=True, domain=True, verified_zero_rule=zero_rule('rail', 'Lnight'))
    assert night['bounds']['upper_db'] == 35
    assert zero_rule('aircraft', 'Lden') is None
    assert interpret(0, inside=True, verified_zero_rule=None)['status'] == 'unreported_unknown'
    # Inclusive cutoff: an exact 40.00 is published and stays a value.
    assert interpret(40.0, inside=True, verified_zero_rule=zero_rule('road', 'Lden'))['value_db'] == 40.0
    assert interpret(-96, inside=True, masked=True, verified_zero_rule=zero_rule('road', 'Lden'))['status'] == 'unreported_unknown'
    assert CONTRACT_V2['version'] == 2 and CONTRACT_V2['provider_reply'] is PROVIDER_REPLY
    retained = ROOT/PROVIDER_REPLY['retained_text']
    if retained.exists():  # Kept outside Git on the owner's machine.
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == PROVIDER_REPLY['retained_text_sha256']


def test_classes_follow_the_contract():
    values = np.array([[0, 39.99, 40, 69.99, 70, 3.4e38]], dtype='float64')
    valid = np.array([[True, True, True, True, True, False]])
    assert m.classify(values, valid, zero_rule('road', 'Lden')).tolist() == [[m.BELOW, m.FIRST_BIN, m.FIRST_BIN+1, m.FIRST_BIN+6, m.FIRST_BIN+7, m.NODATA]]
    assert m.classify(values, valid, None)[0, 0] == m.NODATA
    with pytest.raises(m.MapIntegrityError, match='encoding'):
        m.classify(np.array([[-1.0]]), np.array([[True]]), None)
    image = Image.open(io.BytesIO(m.encode(np.array([[m.OUTSIDE, m.NODATA, m.BELOW, m.FIRST_BIN]], dtype='uint8'))))
    # Pixel (0, 1) falls on a dense stripe of the no-data hatch.
    assert image.mode == 'P' and np.asarray(image).tolist() == [[0, 2, 3, 4]]


def test_transformation_grid_is_pinned_by_fingerprint(tmp_path, monkeypatch):
    info = m.best_transformer()
    for grid in info['grids']:  # OSTN15 where installed: the file found is the one PROJ used.
        assert m.file_hash(m.grid_path(grid['name'])) == grid['sha256']
    m.pinned_transformer(info)
    grid = tmp_path / 'test_grid.tif'
    grid.write_bytes(b'grid')
    real = m.grid_path
    monkeypatch.setattr(m, 'grid_path', lambda name: (grid if grid.is_file() else None) if name == grid.name else real(name))
    pinned = {**info, 'grids': [*info['grids'], {'name': grid.name, 'sha256': hashlib.sha256(b'grid').hexdigest()}]}
    m.pinned_transformer(pinned)
    grid.write_bytes(b'changed')
    with pytest.raises(m.MapIntegrityError, match='grid test_grid.tif'):
        m.pinned_transformer(pinned)
    grid.unlink()
    with pytest.raises(m.MapIntegrityError, match='missing'):
        m.pinned_transformer(pinned)


def test_unsealed_acquisition_needs_an_explicit_preview(acquisition, tmp_path):
    with pytest.raises(m.MapIntegrityError, match='not sealed'):
        m.build(acquisition, tmp_path/'map', workers=0)


def test_every_display_pixel_shows_the_owning_native_cell(built):
    england = m.EnglandMap(built)
    db = sqlite3.connect(built/'tiles.sqlite3')
    rng = np.random.default_rng(4)
    checked = set()
    try:
        for z in (14, 13, 11, 8):
            step = 1 << (m.MAX_ZOOM - z)
            rows = db.execute('SELECT i.layer, i.x, i.y, d.png FROM tile_index i JOIN tile_data d ON d.id = i.id WHERE z = ?', (z,)).fetchall()
            assert rows, z
            for layer, tx, ty, data in rows:
                indices = np.asarray(Image.open(io.BytesIO(data)))
                drawn = np.argwhere(indices > 0)
                picks = drawn[rng.choice(len(drawn), size=min(len(drawn), 40), replace=False)]
                blank = np.argwhere(indices == 0)
                picks = np.r_[picks, blank[rng.choice(len(blank), size=min(len(blank), 10), replace=False)]]
                for row, col in picks:
                    # The pixel shows the zoom-14 pixel at its centre (offset step // 2).
                    x = -m.HALF_WORLD + ((tx * m.TILE_PX + col) * step + step // 2 + .5) * m.PIXEL_M
                    y = m.HALF_WORLD - ((ty * m.TILE_PX + row) * step + step // 2 + .5) * m.PIXEL_M
                    lon, lat = m.to_lonlat(x, y)
                    observation = next(o for o in england.lookup(float(lon), float(lat))['observations']
                                       if f'{o["source"]}-{o["metric"]}' == layer)
                    assert palette_class(int(indices[row, col])) == expected_class(observation), (layer, z, tx, ty, row, col)
                    assert observation['raw_value'] != HALO_VALUE
                    checked.add(expected_class(observation))
    finally:
        db.close()
        england.close()
    assert {m.OUTSIDE, m.NODATA, m.BELOW} <= checked and max(checked) >= m.FIRST_BIN


def test_point_lookup_values_bounds_and_tile_ownership(built):
    england = m.EnglandMap(built)
    try:
        def at(east, north):
            result = england.lookup(*lonlat(england, east, north))
            return result, {f'{o["source"]}-{o["metric"]}': o for o in result['observations']}
        # Cell row 2, column 40 of the west raster.
        result, layers = at(534005 + 405, TOP - 25)
        assert result['tile'] == WEST_TILE
        assert layers['road-Lden']['value_db'] == round(40 + (2 + 40) % 35 + .37, 2)
        assert layers['road-Lden']['record']['sha256'] and layers['road-Lden']['cell']['type'] == 'Polygon'
        assert layers['aircraft-Lden']['status'] == 'outside_extract'
        _, layers = at(534005 + 75, TOP - 75)  # Zero block.
        assert layers['road-Lden']['display_label'] == 'Under 40 dB(A)'
        assert layers['road-Lnight']['bounds']['upper_db'] == 35
        _, layers = at(534005 + 355, TOP - 225)  # -96 block.
        assert layers['road-Lden']['status'] == 'unreported_unknown' and layers['road-Lden']['value_db'] is None
        _, layers = at(534005 + 305, TOP - 305)
        assert layers['road-Lday']['value_db'] == 40.0
        # Two metres either side of the tile boundary: each tile owns its own side, never the halo.
        west, west_layers = at(BOUNDARY - 2, TOP - 105)
        east, east_layers = at(BOUNDARY + 2, TOP - 105)
        assert (west['tile'], east['tile']) == (WEST_TILE, EAST_TILE)
        assert east_layers['road-Lden']['raw_value'] != HALO_VALUE
        assert east_layers['aircraft-Lden']['status'] == 'unreported_unknown'
        _, layers = at(534995 + 555, TOP - 455)
        assert layers['aircraft-Lden']['value_db'] == 57.5
        outside = england.lookup(-1.0, 54.0)
        assert outside['tile'] is None and {o['status'] for o in outside['observations']} == {'outside_extract'}
    finally:
        england.close()


def test_close_up_cell_images_match_point_lookups(built):
    england = m.EnglandMap(built)
    try:
        lon0, lat0 = lonlat(england, BOUNDARY - 300, TOP - 300)
        lon1, lat1 = lonlat(england, BOUNDARY + 300, TOP)
        windows = england.cell_windows('road-Lden', lon0, lat0, lon1, lat1)['windows']
        assert {w['id'] for w in windows} == {'road-Lden-529-174', 'road-Lden-530-174'}
        image = np.asarray(Image.open(io.BytesIO(england.cell_image('road-Lden', 529, 174))))
        assert image.shape == (100, 100)
        for row, col in [(2, 40), (7, 7), (22, 35), (59, 99), (60, 50)]:
            east, north = 534005 + col * 10 + 5, TOP - row * 10 - 5
            observation = next(o for o in england.lookup(*lonlat(england, east, north))['observations']
                               if o['source'] == 'road' and o['metric'] == 'Lden')
            assert palette_class(int(image[row, col])) == expected_class(observation)
        assert england.cell_image('road-Lden', 600, 174) is None
        with pytest.raises(ValueError, match='close-up'):
            england.cell_windows('road-Lden', -2, 51, -1, 52)
    finally:
        england.close()


def test_verify_reproduces_and_tampering_is_detected(built, tmp_path):
    report = m.verify(built, log=lambda *a, **k: None)
    assert report['tiles_checked'] > 0 and report['reproduced_blocks']
    copy = tmp_path/'england_map_v1'
    shutil.copytree(built, copy)
    shutil.copytree(built.parent/'england_acquisition_v1', tmp_path/'england_acquisition_v1')
    db = sqlite3.connect(copy/'tiles.sqlite3')
    identifier, data = db.execute('SELECT id, png FROM tile_data LIMIT 1').fetchone()
    db.execute('UPDATE tile_data SET png = ? WHERE id = ?', (data[:-1] + b'\x00', identifier))
    db.commit()
    england = m.EnglandMap(copy)
    key = england.db.execute('SELECT layer, z, x, y FROM tile_index WHERE id = ?', (identifier,)).fetchone()
    with pytest.raises(m.MapIntegrityError, match='tile changed'):
        england.tile(*key)
    england.close()
    db.execute('UPDATE tile_index SET sha256 = ? WHERE id = ?', (hashlib.sha256(data[:-1] + b'\x00').hexdigest(), identifier))
    db.commit()
    db.close()
    with pytest.raises(m.MapIntegrityError, match='index changed'):
        m.EnglandMap(copy)
    shutil.copyfile(built/'tiles.sqlite3', copy/'tiles.sqlite3')
    archive = tmp_path/'england_acquisition_v1/archive'/WEST_TILE/'road-Lden.tif.zst'
    original = archive.read_bytes()
    archive.write_bytes(ea.zstd.compress(ea.read_archive(archive)[:-1] + b'\x01'))
    england = m.EnglandMap(copy)
    with pytest.raises(ValueError, match='Archived response changed'):
        england.lookup(*lonlat(england, 534405, TOP - 25))
    england.close()
    archive.write_bytes(original)
    code = copy/'construction/src/quiet_uk/england_map.py'
    source = code.read_bytes()
    code.write_bytes(source + b'\n# changed\n')
    with pytest.raises(m.MapIntegrityError, match='construction'):
        m.EnglandMap(copy)
    code.write_bytes(source)
    records = json.loads((copy/'records.json').read_text('utf-8'))
    records[0]['sha256'] = '0' * 64
    (copy/'records.json').write_text(json.dumps(records), encoding='utf-8')
    with pytest.raises(m.MapIntegrityError, match='record list'):
        m.EnglandMap(copy)
    manifest = json.loads((copy/'manifest.json').read_text('utf-8'))
    manifest['display']['analytical_use'] = True
    (copy/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(m.MapIntegrityError, match='manifest'):
        m.EnglandMap(copy)


def test_server_routes_tiles_cells_and_point_evidence(built):
    server = ExplorerServer(0, None, ROOT/'explorer', england=built)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        app = json.load(urlopen(base+'/api/app'))
        release = app['england_release']
        assert app['default_view'] == 'england' and app['interface_version'] == 5 and release.startswith('england-map-')
        assert b'england.js' in urlopen(base+'/').read() and b'england.js' in urlopen(base+'/england').read()
        assert json.load(urlopen(base+'/api/england'))['release_id'] == release
        england = server.england
        layer, z, x, y = england.db.execute('SELECT layer, z, x, y FROM tile_index WHERE z = 14 LIMIT 1').fetchone()
        tile = urlopen(f'{base}/england-tiles/{release}/{layer}/{z}/{x}/{y}.png')
        assert tile.status == 200 and tile.read() == england.tile(layer, z, x, y)
        assert urlopen(f'{base}/england-tiles/{release}/{layer}/14/1/1.png').status == 204
        with pytest.raises(HTTPError) as caught:
            urlopen(f'{base}/england-tiles/england-map-{"0"*20}/{layer}/{z}/{x}/{y}.png')
        assert caught.value.code == 404
        lon, lat = lonlat(england, 534405, TOP - 25)
        result = json.load(urlopen(f'{base}/api/england/location?lon={lon}&lat={lat}&release={release}'))
        assert result['tile'] == WEST_TILE and result['evidence_contract_version'] == 2
        with pytest.raises(HTTPError) as caught:
            urlopen(f'{base}/api/england/location?lon={lon}&lat={lat}&release=england-map-{"0"*20}')
        assert caught.value.code == 409
        download = urlopen(f'{base}/downloads/england-location.json?lon={lon}&lat={lat}')
        assert 'attachment' in download.headers['Content-Disposition']
        bbox = ','.join(map(str, (lon - .001, lat - .001, lon + .001, lat + .001)))
        cells = json.load(urlopen(f'{base}/api/england/cells?layer=road-Lden&bbox={bbox}&release={release}'))
        assert cells['windows'] and urlopen(base + cells['windows'][0]['url']).status == 200
        with pytest.raises(HTTPError) as caught:
            urlopen(Request(base+'/api/england', headers={'Origin': 'http://example.com'}))
        assert caught.value.code == 403
    finally:
        server.shutdown()
        server.server_close()
