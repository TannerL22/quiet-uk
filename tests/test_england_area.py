"""Area summaries on the England map, checked against answers worked out cell by cell.

The expected grid is rebuilt from the test rasters' own definitions (test_england_map: road, rail),
independently of the code under test, and tied to the map with point lookups.
"""
import csv
import io
import json
import math
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
import warnings

import numpy as np
import pytest
from scipy.spatial import cKDTree

from quiet_uk import england_area as area
from quiet_uk import england_map as m
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.map_presentation import WHO_2018
from quiet_uk.source_pilot import BREAKS
from test_england_map import acquisition, built, lonlat, rail, road, BOUNDARY, ROOT, TOP  # noqa: F401  (module fixtures)

warnings.filterwarnings('ignore', message='Best transformation is not available')
NO_DATA, BELOW, VALUE = area.NO_DATA, area.BELOW, area.VALUE
# Both test rasters are 60 rows by 101 columns; the west one's last column is a halo the east one owns.
RASTERS = ((534005, 0, 534005, BOUNDARY), (534995, 99, BOUNDARY, 536005))  # west edge, column shift, owned from, to


@pytest.fixture(scope='module')
def england(built):
    loaded = m.EnglandMap(built)
    yield loaded
    loaded.close()


def expected_grid(source, xs, ys):
    """Status and value of the cells centred at eastings xs and northings ys, from the fixture definitions."""
    status = np.full((ys.size, xs.size), NO_DATA, dtype='uint8')
    values = np.zeros(status.shape)
    rows = (TOP - ys - 5) // 10
    for west, shift, lo, hi in RASTERS:
        raster = (road if source == 'road' else rail)(60, 101, shift).astype('float64')
        rr = np.flatnonzero((rows >= 0) & (rows < 60))
        cc = np.flatnonzero((xs >= lo) & (xs < hi))
        v = raster[np.ix_(rows[rr], (xs[cc] - west - 5) // 10)]
        status[np.ix_(rr, cc)] = np.where(v == -96, NO_DATA, np.where(v == 0, BELOW, VALUE))
        values[np.ix_(rr, cc)] = np.where(v > 0, v, 0)
    return status, values


def box_around(england, south_west, north_east):
    """A longitude/latitude box from two National Grid corners."""
    (w, s), (e, n) = lonlat(england, *south_west), lonlat(england, *north_east)
    return float(w), float(s), float(e), float(n)


def worked_out(england, layer, box, level=None):
    """Everything the summary should say about the box, cell by cell."""
    source, metric = layer.split('-')
    xs = np.arange(533005, 537005, 10) + 5
    ys = TOP - 5 - 10 * np.arange(-100, 160)  # Cell centres, north first.
    status, values = expected_grid(source, xs, ys)
    X, Y = np.meshgrid(xs, ys)
    lon, lat = england.transformer.transform(X, Y, direction='INVERSE')
    w, s, e, n = box
    inside = (lon > w) & (lon < e) & (lat > s) & (lat < n)
    assert not (inside[0].any() or inside[-1].any() or inside[:, 0].any() or inside[:, -1].any())  # The grid is big enough.
    cutoff = 35 if metric == 'Lnight' else 40
    who = WHO_2018[source].get(metric)
    level = level if level is not None else who if who is not None else area.DEFAULT_LEVEL_DB
    vals = values[inside & (status == VALUE)]
    n_below = int((inside & (status == BELOW)).sum())
    return dict(xs=xs, ys=ys, status=status, values=values, inside=inside, vals=vals, n_below=n_below,
                n_calc=vals.size + n_below, cutoff=cutoff, who=who, level=level)


def clearances(w, candidates):
    """Distance from each candidate cell to the nearest cell at or over the level, or with no published value
    (an exact nearest-neighbour search, not the distance transform the code uses)."""
    blocked = (w['status'] == NO_DATA) | ((w['status'] == VALUE) & (w['values'] >= w['level']))
    cy, cx = np.nonzero(candidates)
    d, _ = cKDTree(10.0 * np.argwhere(blocked)).query(10.0 * np.c_[cy, cx])
    return cy, cx, d, blocked


def cell_index(w, spot):
    return int(np.flatnonzero(w['ys'] == spot['northing'])[0]), int(np.flatnonzero(w['xs'] == spot['easting'])[0])


def check_against_cells(england, layer, box, level=None):
    result = area.summarise(england, layer, *box, level=level)
    w = worked_out(england, layer, box, level)
    vals, n_below, n_calc = w['vals'], w['n_below'], w['n_calc']
    inside = w['inside']
    assert result['cells'] == {'inside': int(inside.sum()), 'value': int(vals.size), 'below_cutoff': n_below,
                               'no_data': int((inside & (w['status'] == NO_DATA)).sum())}
    assert result['area_km2'] == round(inside.sum() / 1e4, 4)
    bands = np.bincount(np.searchsorted(BREAKS, vals, side='right'), minlength=len(BREAKS) + 1)
    assert [b['cells'] for b in result['bands']] == bands.tolist()
    assert [b['low_db'] for b in result['bands']] == [None, *BREAKS] and [b['high_db'] for b in result['bands']] == [*BREAKS, None]
    assert result['below_cutoff'] == {'cells': n_below, 'share': round(n_below / n_calc, 6)}
    assert sum(b['share'] for b in result['bands']) + result['below_cutoff']['share'] == pytest.approx(1, abs=1e-5)
    if w['who'] is not None:
        assert result['who_2018']['cells_at_or_over'] == int((vals >= w['who']).sum())
    assert result['level_db'] == w['level'] and result['at_or_over_level']['cells'] == int((vals >= w['level']).sum())
    rank = (n_calc + 1) // 2
    median = {'under_db': w['cutoff'], 'value_db': None} if rank <= n_below else {'under_db': None, 'value_db': round(float(np.sort(vals)[rank - n_below - 1]), 2)}
    assert result['median_db'] == median
    energy = np.power(10, vals.astype('float32').astype('float64') / 10).sum()
    lower = round(10 * math.log10(energy / n_calc), 2) if vals.size else None
    upper = 10 * math.log10((energy + n_below * 10 ** (w['cutoff'] / 10)) / n_calc)
    assert result['energy_average_db']['lower_db'] == lower and result['energy_average_db']['upper_db'] == pytest.approx(upper, abs=.006)
    if vals.size:
        loud = result['loudest']
        top = float(vals.max())
        assert loud['value_db'] == round(top, 2) and loud['tied_cells'] == int((inside & (w['values'] == top)).sum())
        r, c = cell_index(w, loud)
        assert inside[r, c] and w['values'][r, c] == top
    # The quietest place: as far as any candidate gets from what could be loud.
    candidates = inside & (w['status'] == BELOW)
    kind = 'below_cutoff'
    if not candidates.any():
        candidates, kind = inside & (w['status'] == VALUE) & (w['values'] == vals.min()), 'lowest_value'
    cy, cx, d, blocked = clearances(w, candidates)
    q = result['quietest']
    assert q['kind'] == kind and q['clearance'] == 'exact' and q['clearance_m'] == round(float(d.max()), 1)
    assert q['tied_cells'] == int((np.round(d, 6) == round(float(d.max()), 6)).sum())
    r, c = cell_index(w, q)
    assert candidates[r, c] and min(d[(cy == r) & (cx == c)]) == pytest.approx(d.max())
    nr, nc = cell_index(w, q['nearest'])
    assert blocked[nr, nc] and math.hypot(10 * (nr - r), 10 * (nc - c)) == pytest.approx(d.max())
    assert q['limited_by'] == ('no_published_value' if w['status'][nr, nc] == NO_DATA else 'at_or_over_level')
    return result, w


def test_counts_bands_median_and_average_match_every_cell(england):
    # Across the boundary between two Defra squares, over the zero block and the -96 block.
    box = box_around(england, (534020, TOP - 590), (535400, TOP - 10))
    result, w = check_against_cells(england, 'road-Lden', box)
    assert result['cells']['below_cutoff'] and result['cells']['no_data'] and result['cells']['value']
    assert result['loudest']['value_db'] <= 74.37  # Never the west square's halo.
    assert {r['tile'] for r in result['records']} == {'E525005N175005', 'E535005N175005'}
    assert all(r['sha256'] for r in result['records'] if r['status'] == 'accepted')
    # The worked-out grid is the map's own: spot checks with a click's lookup.
    rng = np.random.default_rng(4)
    ry, rx = np.nonzero(w['inside'])
    for k in rng.choice(ry.size, 120, replace=False):
        y, x = ry[k], rx[k]
        obs = {f'{o["source"]}-{o["metric"]}': o for o in england.lookup(*lonlat(england, w['xs'][x], w['ys'][y]))['observations']}['road-Lden']
        state = VALUE if obs['value_db'] is not None else BELOW if obs['status'] == 'verified_below_cutoff' else NO_DATA
        assert state == w['status'][y, x]
        if state == VALUE:
            assert obs['value_db'] == round(w['values'][y, x], 2)
    for name in ('road-Lnight', 'road-Lday'):
        check_against_cells(england, name, box)
    check_against_cells(england, 'road-Lden', box, level=70)


def test_quietest_place_against_rail_lines(england):
    box = box_around(england, (534100, TOP - 500), (535900, TOP - 100))
    result, _ = check_against_cells(england, 'rail-Lnight', box)
    assert result['quietest']['limited_by'] == 'at_or_over_level' and result['level_db'] == 44
    assert 0 < result['quietest']['clearance_m'] <= 90  # Rail lines are 170 m apart.
    # With the level above the trains' 61.25 dB, only the edges of the data limit it.
    loose, _ = check_against_cells(england, 'rail-Lnight', box, level=65)
    assert loose['quietest']['limited_by'] == 'no_published_value' and loose['quietest']['clearance_m'] > 200
    assert check_against_cells(england, 'rail-Lday', box)[0]['who_2018'] is None


def test_lowest_value_stands_in_when_nothing_is_under_the_cutoff(england):
    box = box_around(england, (534605, TOP - 500), (534905, TOP - 300))
    result, w = check_against_cells(england, 'road-Lden', box)
    assert result['quietest']['kind'] == 'lowest_value' and result['quietest']['value_db'] == round(float(w['vals'].min()), 2)
    assert result['cells']['below_cutoff'] == 0 and result['median_db']['value_db'] is not None


def test_a_distance_past_the_search_area_is_a_lower_bound(england, monkeypatch):
    monkeypatch.setattr(area, 'MARGIN_M', 0)
    box = box_around(england, (534075, TOP - 135), (534135, TOP - 65))  # Inside the zero block.
    result = area.summarise(england, 'road-Lden', *box)
    q = result['quietest']
    assert result['cells']['below_cutoff'] == result['cells']['inside'] and result['search_area']['no_data_cells'] == 0
    assert (q['clearance'], q['limited_by'], q['nearest']) == ('at_least', 'search_edge', None) and q['clearance_m'] >= 10


def test_an_area_with_no_published_values_is_not_called_quiet(england):
    result = area.summarise(england, 'road-Lden', -1.0, 54.0, -0.99, 54.01)
    assert result['cells']['no_data'] == result['cells']['inside'] > 0
    assert result['below_cutoff']['share'] is None and all(b['share'] is None for b in result['bands'])
    assert (result['median_db'], result['energy_average_db'], result['loudest'], result['quietest']) == (None, None, None, None)


def test_limits_and_unsupported_layers(england):
    w, s = -0.1, 51.5
    with pytest.raises(ValueError, match='road and rail'):
        area.summarise(england, 'aircraft-Lden', w, s, w + .01, s + .01)
    with pytest.raises(KeyError):
        area.summarise(england, 'water-Lden', w, s, w + .01, s + .01)
    for box in ((w, s, w + .15, s + .1),    # About 115 km².
                (w, s, w + .3, s + .01),    # 21 km across.
                (w, s, w - .01, s + .01),   # West of east is backwards.
                (-10, s, -9.99, s + .01),   # Off the map.
                (w, s, w + 1e-6, s + 1e-6)):  # Too small to hold a cell centre.
        with pytest.raises(ValueError):
            area.summarise(england, 'road-Lden', *box)
    for level in (30, 81, float('nan')):
        with pytest.raises(ValueError):
            area.summarise(england, 'road-Lden', w, s, w + .01, s + .01, level=level)
    summaries = area.AreaSummaries(england)
    first = summaries.summary('road-Lnight', w, s, w + .01, s + .01)
    assert summaries.summary('road-Lnight', w, s, w + .01, s + .01) is first and first['level_db'] == 45


def test_csv_spells_out_the_summary(england):
    box = box_around(england, (534020, TOP - 590), (535400, TOP - 10))
    result = area.summarise(england, 'road-Lden', *box)
    rows = list(csv.reader(io.StringIO(area.area_csv(result).decode('utf-8'))))
    assert rows[0] == ['field', 'value']
    fields = {k: v for k, v in rows[1:] if k != 'record'}
    assert int(fields['cells_inside']) == result['cells']['inside'] and fields['release_id'] == result['release_id']
    assert sum(int(v) for k, v in fields.items() if k.startswith('band_') and k.endswith('_cells')) == result['cells']['value']
    assert float(fields['quietest_clearance_m']) == result['quietest']['clearance_m'] and fields['quietest_limited_by'] == result['quietest']['limited_by']
    assert sum(1 for k, _ in rows if k == 'record') == len(result['records'])


def test_server_summarises_areas_and_downloads_them(built):
    server = ExplorerServer(0, None, ROOT/'explorer', england=built)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        manifest = json.load(urlopen(base+'/api/england'))
        release = manifest['release_id']
        assert manifest['area']['max_km2'] == area.MAX_AREA_KM2 and manifest['area']['method'] == area.METHOD
        assert manifest['places'] is None
        box = box_around(server.england, (534020, TOP - 590), (535400, TOP - 10))
        query = {'layer': 'road-Lden', 'bbox': ','.join(f'{v:.6f}' for v in box), 'release': release}
        result = json.load(urlopen(base+'/api/england/area?'+urlencode(query)))
        expected = area.summarise(server.england, 'road-Lden', *(round(v, 6) for v in box))
        assert result == json.loads(json.dumps(expected))
        assert json.load(urlopen(base+'/api/england/area?'+urlencode({**query, 'level': 70})))['level_db'] == 70
        download = urlopen(base+'/downloads/england-area.json?'+urlencode(query))
        assert download.headers['Content-Disposition'] == f'attachment; filename="{release}-area.json"'
        assert json.load(download)['cells'] == result['cells']
        table = urlopen(base+'/downloads/england-area.csv?'+urlencode(query))
        assert table.headers['Content-Type'].startswith('text/csv') and b'cells_inside' in table.read()
        with pytest.raises(HTTPError) as caught:
            urlopen(base+'/api/england/area?'+urlencode({**query, 'release': 'england-map-' + '0' * 20}))
        assert caught.value.code == 409
        for bad in ({**query, 'layer': 'aircraft-Lden'}, {**query, 'bbox': '1,2,3'}, {'layer': 'road-Lden', 'release': release},
                    {**query, 'level': 20}, {**query, 'extra': 1}):
            with pytest.raises(HTTPError) as caught:
                urlopen(base+'/api/england/area?'+urlencode(bad))
            assert caught.value.code == 400
        with pytest.raises(HTTPError) as caught:
            urlopen(base+'/api/england/area?'+urlencode({**query, 'layer': 'aircraft-Lden'}))
        assert 'road and rail' in json.load(caught.value)['error']
    finally:
        server.shutdown()
        server.server_close()
