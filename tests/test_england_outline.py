"""England boundary: no-data outside England is left undrawn; everything else is untouched.

A stand-in boundary splits the test map: England to the west of SPLIT, "Wales" to the east.
The line runs through the east raster's no-data block, so that block is part inside and
part outside England, while the west raster's no-data block is wholly inside.
"""
import hashlib
import io
import json
import re
import threading
from urllib.error import HTTPError
from urllib.request import urlopen
import warnings

import numpy as np
from PIL import Image
import pytest
import shapely

from quiet_uk import england_map as m
from quiet_uk import england_outline as o
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.map_presentation import recolour
from test_england_map import acquisition, built, lonlat, TOP  # noqa: F401  (module fixtures)

ROOT = m.ea.REPOSITORY
SPLIT = 535345  # Through the east raster's no-data block (eastings 535295-535395).
warnings.filterwarnings('ignore', message='Best transformation is not available')


def countries(england=(500000, 150000, 535345, 200000), crs='EPSG:27700', codes=(o.ENGLAND, o.WALES, o.SCOTLAND)):
    boxes = [england, (SPLIT, 150000, 560000, 200000), (300000, 700000, 310000, 710000)]
    features = [{'type': 'Feature', 'properties': {'CTRY25CD': code, 'CTRY25NM': code},
                 'geometry': shapely.geometry.mapping(shapely.box(*box))} for code, box in zip(codes, boxes)]
    return json.dumps({'type': 'FeatureCollection', 'crs': {'type': 'name', 'properties': {'name': crs}},
                       'features': features}).encode('utf-8')


def pinned(data):
    return {**o.SOURCE, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}


@pytest.fixture(scope='module')
def boundary(tmp_path_factory):
    path = tmp_path_factory.mktemp('boundary')/'countries.geojson'
    data = countries()
    path.write_bytes(data)
    return path, pinned(data)


@pytest.fixture(scope='module')
def outline(built, boundary):
    england = m.EnglandMap(built)
    yield england, o.EnglandOutline(boundary[0], england.transformer, source=boundary[1])
    england.close()


def indices(data):
    return np.asarray(Image.open(io.BytesIO(data)))


ENGLAND_BOX = (500000, 150000, SPLIT, 200000)


def in_england(east, north):
    west, south, split, top = ENGLAND_BOX
    return (east > west) & (east < split) & (north > south) & (north < top)


def exact_inside(england, z, x, y):
    return in_england(*england.transformer.transform(*o.tile_centres(z, x, y)))


def test_the_boundary_file_is_checked(boundary, built, tmp_path):
    england = m.EnglandMap(built)
    try:
        path, source = boundary
        with pytest.raises(o.OutlineIntegrityError, match='differs'):
            o.EnglandOutline(path, england.transformer)  # The real ONS pin: this file is not it.
        for data, problem in ((countries(crs='EPSG:4326'), 'British National Grid'),
                              (countries(codes=(o.ENGLAND, o.WALES, 'N92000002')), 'lacks')):
            other = tmp_path/'other.geojson'
            other.write_bytes(data)
            with pytest.raises(o.OutlineIntegrityError, match=problem):
                o.EnglandOutline(other, england.transformer, source=pinned(data))
    finally:
        england.close()


def test_tile_masks_agree_with_an_exact_test_at_every_pixel(outline):
    england, boundary = outline
    keys = england.db.execute("SELECT z, x, y FROM tile_index WHERE layer = 'road-Lden'").fetchall()
    assert {z for z, _, _ in keys} >= {10, 12, 14}
    split = 0
    for z, x, y in keys:
        mask, exact = boundary.tile_mask(z, x, y), exact_inside(england, z, x, y)
        if mask is o.INSIDE:
            assert exact.all()
        elif mask is o.OUTSIDE:
            assert not exact.any()
        else:
            split += 1
            # Pixel centres are transformed exactly on a lattice and interpolated between: a pixel can
            # only differ if its centre is within millimetres of the line.
            assert (mask != exact).sum() <= (1 if z < 13 else 0), (z, x, y)
    assert split, 'some tiles must straddle the boundary'


def test_only_no_data_outside_england_is_left_undrawn(outline):
    england, boundary = outline
    dropped = kept_inside = kept_values_outside = 0
    for layer, z, x, y in england.db.execute('SELECT layer, z, x, y FROM tile_index').fetchall():
        stored = england.tile(layer, z, x, y)
        served = boundary.apply(stored, boundary.tile_mask(z, x, y))
        before = indices(stored)
        after = indices(served) if served is not None else np.zeros_like(before)
        inside = exact_inside(england, z, x, y)
        no_data = np.isin(before, o.NO_DATA)
        # Inside England every pixel is as stored, no-data included; outside only no-data changes.
        assert np.array_equal(after[inside], before[inside])
        assert np.array_equal(after[~inside & ~no_data], before[~inside & ~no_data])
        assert not after[~inside & no_data].any()
        dropped += int((~inside & no_data).sum())
        kept_inside += int((inside & no_data).sum())
        kept_values_outside += int((~inside & (before > 2)).sum())
        if served is not None and served != stored:
            assert recolour(served, 'night', layer)  # Still a palette PNG the looks can recolour.
    assert dropped and kept_inside and kept_values_outside
    assert boundary.apply(None, o.OUTSIDE) is None


def test_native_cell_windows_are_masked_by_cell_centre(outline):
    england, boundary = outline
    lon, lat = lonlat(england, SPLIT, TOP - 225)
    windows = england.cell_windows('road-Lden', lon - .004, lat - .002, lon + .004, lat + .002)['windows']
    side = m.CELL_WINDOW * m.CELL_M
    checked = 0
    for window in windows:
        wx, wy = map(int, re.search(r'/(\d+)/(\d+)\.png$', window['url']).groups())
        stored = england.cell_image('road-Lden', wx, wy)
        served = boundary.apply(stored, boundary.window_mask(wx, wy))
        before = indices(stored)
        after = indices(served) if served is not None else np.zeros_like(before)
        centres = (np.arange(m.CELL_WINDOW) + .5) * m.CELL_M
        east, north = np.meshgrid(m.ea.LATTICE[0] + wx * side + centres, m.ea.LATTICE[1] + (wy + 1) * side - centres)
        inside = in_england(east, north)
        no_data = before == 1
        assert np.array_equal(after[inside | ~no_data], before[inside | ~no_data])
        assert not after[~inside & no_data].any()
        checked += int((~inside & no_data).sum())
    assert checked


def test_server_masks_drawn_tiles_but_not_the_evidence(built, boundary):
    path, source = boundary
    server = ExplorerServer(0, None, ROOT/'explorer', england=built, england_outline=path, outline_source=source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        england, boundary_ = server.england, server.england_outline
        app = json.load(urlopen(base + '/api/app'))
        assert app['interface_version'] == 9 and app['england_outline_sha256'] == source['sha256']
        assert json.load(urlopen(base + '/api/england'))['outline']['sha256'] == source['sha256']
        release, tag = england.release_id, source['sha256'][:12]
        for layer, z, x, y in england.db.execute("SELECT layer, z, x, y FROM tile_index WHERE layer IN ('road-Lden', 'rail-Lnight')"):
            stored = england.tile(layer, z, x, y)
            expected = boundary_.apply(stored, boundary_.tile_mask(z, x, y))
            response = urlopen(f'{base}/england-display/{release}/native/{layer}/{z}/{x}/{y}.png?theme=atlas&outline={tag}')
            assert (response.status, response.read()) == ((204, b'') if expected is None else (200, recolour(expected, 'atlas', layer)))
            # The research tile route still serves the stored tile unmasked.
            assert urlopen(f'{base}/england-tiles/{release}/{layer}/{z}/{x}/{y}.png?theme=atlas').read() == recolour(stored, 'atlas', layer)
        for wrong in ('none', '0' * 12):
            with pytest.raises(HTTPError) as caught:
                urlopen(f'{base}/england-display/{release}/native/{layer}/{z}/{x}/{y}.png?theme=atlas&outline={wrong}')
            assert caught.value.code == 409
        shapes = urlopen(f'{base}/api/england/outline?outline={tag}')
        assert shapes.headers['Cache-Control'] == 'no-store'
        features = {f['properties']['kind']: shapely.geometry.shape(f['geometry']) for f in json.load(shapes)['features']}
        border = shapely.get_coordinates(features['border'])
        border_east, _ = england.transformer.transform(border[:, 0], border[:, 1])
        assert len(border) >= 2 and np.abs(border_east - SPLIT).max() < .1  # The shared edge, to 6 decimal places of a degree.
        assert all(p.is_valid for p in shapely.get_parts(features['outside']))
        for east, name in ((SPLIT - 300, 'England'), (SPLIT + 300, 'Wales')):
            lon, lat = lonlat(england, east, TOP - 225)
            result = json.load(urlopen(f'{base}/api/england/location?lon={lon}&lat={lat}&release={release}'))
            assert result['country']['name'] == name
            assert result['observations'] == json.loads(json.dumps(england.lookup(lon, lat)['observations']))
    finally:
        server.shutdown()
        server.server_close()


def test_a_changed_boundary_file_is_not_used(built, boundary, tmp_path):
    path, source = boundary
    changed = tmp_path/'countries.geojson'
    changed.write_bytes(path.read_bytes() + b' ')
    server = ExplorerServer(0, None, ROOT/'explorer', england=built, england_outline=changed, outline_source=source)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        app = json.load(urlopen(base + '/api/app'))
        assert app['england_outline_sha256'] is None and 'differs' in app['england_outline_problem']
        england = server.england
        layer, z, x, y = england.db.execute("SELECT layer, z, x, y FROM tile_index WHERE layer = 'road-Lden' AND z = 14 LIMIT 1").fetchone()
        served = urlopen(f'{base}/england-display/{england.release_id}/native/{layer}/{z}/{x}/{y}.png?theme=night&outline=none').read()
        assert served == recolour(england.tile(layer, z, x, y), 'night', layer)  # Drawn as stored.
        with pytest.raises(HTTPError) as caught:
            urlopen(base + '/api/england/outline')
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_fonts_are_served_with_their_licences():
    fonts = ROOT/'explorer'/'fonts'
    css = (ROOT/'explorer'/'england.css').read_text('utf-8')
    named = set(re.findall(r'url\("fonts/([^"]+)"\)', css))
    assert named == {p.name for p in fonts.glob('*.woff2')} and len(named) == 6
    for family in ('geist', 'newsreader', 'figtree'):
        assert 'SIL OPEN FONT LICENSE Version 1.1' in (fonts/f'{family}-LICENSE.txt').read_text('utf-8')
    server = ExplorerServer(0, None, ROOT/'explorer')
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        for name in named:
            response = urlopen(f'{base}/fonts/{name}')
            body = response.read()
            assert response.headers['Content-Type'] == 'font/woff2' and body[:4] == b'wOF2' and body == (fonts/name).read_bytes()
        for path in ('/fonts/geist-LICENSE.txt', '/fonts/../england.css', '/fonts/other-latin-wght-normal.woff2'):
            with pytest.raises(HTTPError) as caught:
                urlopen(base + path)
            assert caught.value.code in (400, 404)
    finally:
        server.shutdown()
        server.server_close()
