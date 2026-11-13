"""England overview: averaged zoomed-out tiles and hexagons summarise the native cells exactly."""
import io
import json
import math
import sqlite3
from urllib.error import HTTPError
from urllib.request import urlopen
import threading
import warnings

from matplotlib.path import Path as Polygon
import numpy as np
from PIL import Image
import pytest

from quiet_uk import england_map as m
from quiet_uk import england_overview as ov
from quiet_uk.evidence_semantics import zero_rule
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.map_presentation import THEMES, recolour
from quiet_uk.source_pilot import BREAKS
from test_england_map import acquisition, built, palette_class  # noqa: F401  (module fixtures)

ROOT = m.ea.REPOSITORY
warnings.filterwarnings('ignore', message='Best transformation is not available')


@pytest.fixture(scope='module')
def overview(built):
    output = built.parent/'england_overview_v1'
    ov.build(built, output, workers=0, log=lambda *a, **k: None)
    return output


def test_energy_average_classes():
    energy = np.array([[10**6 + 10**4, 10**4.1, 0, 0, 10**5.25]], dtype='float64')
    land = np.array([[2, 4, 0, 0, 1]])
    nodata = np.array([[0, 0, 3, 0, 9]])
    classes = ov.classify_average(energy, land, nodata, 40)
    # 60 and 40 dB average to 57.03 dB (55-60 band); 41 dB with three cells under the cutoff averages to 35 dB; 52.5 dB is in the 50-55 band.
    assert classes.tolist() == [[m.FIRST_BIN + 4, m.BELOW, m.NODATA, m.OUTSIDE, m.FIRST_BIN + 3]]
    # Without a cutoff rule (aircraft), a low average is a reported band, never "below the cutoff".
    assert ov.classify_average(np.array([[10**3.5]]), np.array([[1]]), np.array([[0]]), None)[0, 0] == m.FIRST_BIN


def test_value_codes_follow_the_display_contract():
    rng = np.random.default_rng(1)
    values = rng.choice([0, 0, 39.99, 40.0, 52.37, 75.5, -96], size=(40, 40)).astype('float32')
    valid = values != -96
    for rule in (zero_rule('road', 'Lden'), None):
        codes, classes = ov.encode_values(values, valid, rule), m.classify(values, valid, rule)
        assert ((codes == ov.NODATA_CODE) == (classes == m.NODATA)).all()
        assert ((codes == ov.BELOW_CODE) == (classes == m.BELOW)).all()
        reported = codes >= ov.VALUE0
        assert np.allclose((codes[reported] - ov.VALUE0) / 100, values[reported], atol=.005)


def averaged_class(england, layer, z, tx, ty, row, col):
    """Independent reading of one overview pixel from point lookups of its zoom-14 samples."""
    step = 1 << (m.MAX_ZOOM - z)
    energy, land, nodata = 0.0, 0, 0
    for dy in range(step):
        for dx in range(step):
            x = -m.HALF_WORLD + ((tx * m.TILE_PX + col) * step + dx + .5) * m.PIXEL_M
            y = m.HALF_WORLD - ((ty * m.TILE_PX + row) * step + dy + .5) * m.PIXEL_M
            lon, lat = m.to_lonlat(x, y)
            o = next(o for o in england.lookup(float(lon), float(lat))['observations'] if f'{o["source"]}-{o["metric"]}' == layer)
            if o['value_db'] is not None:
                energy += 10 ** (o['value_db'] / 10); land += 1
            elif o['status'] == 'verified_below_cutoff':
                land += 1
            elif o['status'] != 'outside_extract':
                nodata += 1
    if land == 0:
        return m.NODATA if nodata else m.OUTSIDE
    level = 10 * math.log10(energy / land) if energy else -math.inf
    limit = ov.cutoff(layer)
    if limit is not None and level < limit:
        return m.BELOW
    return m.FIRST_BIN + int(np.searchsorted(BREAKS, level, side='right'))


def test_overview_pixels_are_energy_averages_of_the_native_cells(built, overview):
    england = m.EnglandMap(built)
    db = sqlite3.connect(overview/'tiles.sqlite3')
    rng = np.random.default_rng(7)
    seen = set()
    try:
        for z, picks in ((13, 25), (12, 12), (11, 6), (9, 2)):
            rows = db.execute('SELECT i.layer, i.x, i.y, d.png FROM tile_index i JOIN tile_data d ON d.id = i.id WHERE z = ?', (z,)).fetchall()
            assert rows, z
            for layer, tx, ty, data in rows:
                if layer.endswith('Lday'):
                    continue
                indices = np.asarray(Image.open(io.BytesIO(data)))
                drawn = np.argwhere(indices > 0)
                for row, col in drawn[rng.choice(len(drawn), size=min(len(drawn), picks), replace=False)]:
                    expected = averaged_class(england, layer, z, tx, ty, row, col)
                    assert palette_class(int(indices[row, col])) == expected, (layer, z, tx, ty, row, col)
                    seen.add(expected)
    finally:
        db.close()
        england.close()
    assert {m.BELOW} <= seen and max(seen) >= m.FIRST_BIN + 2


def test_low_zoom_tiles_exist_down_to_zoom_five(overview):
    db = sqlite3.connect(overview/'tiles.sqlite3')
    zooms = {z for (z,) in db.execute('SELECT DISTINCT z FROM tile_index')}
    db.close()
    assert zooms == set(range(ov.MIN_ZOOM, ov.MAX_ZOOM + 1))


def test_hexagons_count_every_native_cell_once(built, overview):
    england = m.EnglandMap(built)
    hexes = json.loads((overview/'hexes.json').read_text('utf-8'))
    try:
        for layer in ('road-Lden', 'rail-Lnight'):
            # Brute force: every core cell of each square, placed by point-in-polygon on the hexagon outline.
            rows = hexes['layers'][layer]
            outlines = [Polygon(np.array(ov.hex_corners(h['q'], h['r']))) for h in hexes['hexes']]
            expected = np.zeros((len(outlines), 3))
            for tile in ('E525005N175005', 'E535005N175005'):
                record = england.records[(tile, layer)]
                codes = ov._square_core(record, england.acquisition)
                w, s = (int(v) for v in tile[1:].split('N'))
                r_, c_ = np.nonzero(codes)
                east, north = w + 5 + 10 * c_, s + 9995 - 10 * r_
                points = np.c_[east, north]
                owner = np.full(len(points), -1)
                for i, outline in enumerate(outlines):
                    owner[(owner < 0) & outline.contains_points(points)] = i
                assert (owner >= 0).all()
                vals = codes[r_, c_]
                for i in range(len(outlines)):
                    mine = vals[owner == i]
                    expected[i] += [(mine >= ov.VALUE0).sum(), (mine == ov.BELOW_CODE).sum(), (mine == ov.NODATA_CODE).sum()]
            got = np.array([[0, 0, 0] if row is None else row[:3] for row in rows])
            assert (got == expected).all(), layer
            assert any(row and row[1] for row in rows) or layer.startswith('rail')
    finally:
        england.close()
    features = ov.hex_features(hexes, 'road-Lden')['features']
    assert features and all(0 <= f['properties']['quiet'] <= 1 and 0 <= f['properties']['loud'] <= 1 for f in features)
    assert all(f['properties']['quiet'] is None for f in ov.hex_features(hexes, 'aircraft-Lden')['features'])


def test_overview_verifies_and_detects_tampering(built, overview, tmp_path):
    report = ov.verify(overview, built, reproduce_blocks=1, workers=0, log=lambda *a, **k: None)
    assert report['verified'] and report['tiles'] > 0
    england = m.EnglandMap(built)
    try:
        copy = tmp_path/'copy'
        import shutil
        shutil.copytree(overview, copy)
        db = sqlite3.connect(copy/'tiles.sqlite3')
        first, data = db.execute('SELECT id, png FROM tile_data ORDER BY id LIMIT 1').fetchone()
        db.execute('UPDATE tile_data SET png = ? WHERE id = ?', (data + b'\x00', first))
        db.commit(); db.close()
        bad = ov.EnglandOverview(copy, england)
        key = bad.db.execute('SELECT layer, z, x, y FROM tile_index WHERE id = (SELECT MIN(id) FROM tile_data)').fetchone()
        with pytest.raises(ov.OverviewIntegrityError):
            bad.tile(*key)
        bad.close()
        (copy/'hexes.json').write_text('{}', encoding='utf-8')
        with pytest.raises(ov.OverviewIntegrityError):
            ov.EnglandOverview(copy, england)
    finally:
        england.close()


def test_server_serves_averaged_levels_and_hexagons(built, overview):
    server = ExplorerServer(0, None, ROOT/'explorer', england=built, england_overview=overview)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_address[1]}'
    try:
        info = json.loads(urlopen(base+'/api/england').read())
        assert info['overview']['release_id'] == server.england_overview.release_id
        release, ov_release = info['release_id'], info['overview']['release_id']
        db = sqlite3.connect(overview/'tiles.sqlite3')
        layer, z, x, y, png = db.execute('SELECT i.layer, i.z, i.x, i.y, d.png FROM tile_index i JOIN tile_data d ON d.id = i.id WHERE z = 12 LIMIT 1').fetchone()
        db.close()
        for theme in THEMES:
            got = urlopen(f'{base}/england-display/{release}/{ov_release}/{layer}/{z}/{x}/{y}.png?theme={theme}').read()
            assert got == recolour(png, theme, layer)
        # A stale overview release is refused rather than mixed in.
        with pytest.raises(HTTPError) as error:
            urlopen(f'{base}/england-display/{release}/england-overview-{"0" * 20}/{layer}/{z}/{x}/{y}.png')
        assert error.value.code == 409
        native = m.EnglandMap(built)
        try:
            k = native.db.execute('SELECT layer, z, x, y FROM tile_index WHERE z = 14 LIMIT 1').fetchone()
            assert urlopen(f'{base}/england-display/{release}/{ov_release}/{k[0]}/14/{k[2]}/{k[3]}.png?theme=night').read() == recolour(native.tile(*k), 'night', k[0])
            k = native.db.execute('SELECT layer, z, x, y FROM tile_index WHERE z = 12 LIMIT 1').fetchone()
            assert urlopen(f'{base}/england-display/{release}/native/{k[0]}/12/{k[2]}/{k[3]}.png?theme=atlas').read() == recolour(native.tile(*k), 'atlas', k[0])
        finally:
            native.close()
        hexes = json.loads(urlopen(f'{base}/api/england/hexes?layer=road-Lden&release={release}').read())
        assert hexes['type'] == 'FeatureCollection' and hexes['features'] and hexes['who_2018_db'] == 53
        with pytest.raises(HTTPError) as error:
            urlopen(f'{base}/api/england/hexes?layer=road-Lnoon&release={release}')
        assert error.value.code == 400
    finally:
        server.shutdown(); server.server_close()


def oklab_lightness(colour):
    """OKLab L of a #rrggbb colour (Ottosson 2020)."""
    linear = [c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4
              for c in (int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5))]
    lms = [sum(w * c for w, c in zip(row, linear)) ** (1 / 3) for row in (
        (.4122214708, .5363325363, .0514459929), (.2119034982, .6806995451, .1073969566), (.0883024619, .2817188376, .6299787005))]
    return .2104542553 * lms[0] + .7936177850 * lms[1] - .0040720613 * lms[2]


def test_looks_step_steadily_from_quiet_to_loud():
    from quiet_uk.map_presentation import BAND_RANGES, LAYERS, WHO_2018, colours, guide_bands
    for name, rising in (('night', True), ('atlas', False), ('light', False), ('dark', True)):
        steps = [oklab_lightness(c) for c in colours(name)['bands']]
        assert all((b > a) if rising else (b < a) for a, b in zip(steps, steps[1:])), name
    for name, rising in (('night', True), ('atlas', False), ('guide', False)):  # Quiet-area lens: more quiet, more contrast.
        steps = [oklab_lightness(c) for c in THEMES[name]['lens']]
        assert len(steps) == 5 and all((b > a) if rising else (b < a) for a, b in zip(steps, steps[1:])), name
    for layer in LAYERS:
        source, metric = layer.split('-')
        limit, bands = WHO_2018[source].get(metric), guide_bands(layer)
        over = [c for c, (low, _) in zip(bands, BAND_RANGES) if limit is None or (low is not None and low >= limit)]
        under = [c for c, (_, high) in zip(bands, BAND_RANGES) if limit is not None and high is not None and high <= limit]
        steps = [oklab_lightness(c) for c in over]
        assert over and all(b < a for a, b in zip(steps, steps[1:])), layer  # Over the guideline: darker as it gets louder.
        assert all(c in THEMES['guide']['cool'] for c in under), layer       # Under it: only the calm teal steps.
        assert len(under) + len(over) + (limit is not None and limit not in BREAKS) == len(BAND_RANGES), layer
