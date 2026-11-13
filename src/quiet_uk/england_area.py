"""Area summaries for the England map: what a box drawn on the map holds, cell by cell.

A box drawn on the page is a rectangle of longitude and latitude. Every native 10 m cell
whose centre lies inside it counts once. Cells are read from the same verified archives as
a click (`EnglandMap.cache`), under the same zero rule (evidence contract v2). Nothing is
resampled or interpolated, and no display tile is used.

For one road or rail layer the summary gives:

- cells by status, and the calculated cells (with a value, or under the cutoff) by colour
  band. Shares are of calculated cells; no-data cells are counted separately, never as quiet;
- the share at or over the WHO 2018 guideline, and at or over the chosen level;
- the energy average 10*log10(mean(10^(L/10))) as a range: cells under the cutoff count as
  silence for the lower end and as the cutoff for the upper end;
- the loudest cell;
- the quietest place: the under-cutoff cell furthest from any cell at or over the chosen
  level, or with no published value (which could hide noise). Distances are between cell
  centres. Cells up to MARGIN_M beyond the box count too, and a distance that would reach
  past that search area is a lower bound ("at least").

Aircraft is not offered: it has no zero rule and is mapped only around airports. When a
combined transport product is installed it can be summarised the same way.
"""
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
import csv
import io
import math
import threading

import numpy as np
from scipy import ndimage
import shapely

from . import england_acquisition as ea
from .england_map import CELL_M, LAYERS, MapIntegrityError, owner, tile_for
from .evidence_semantics import CONTRACT_V2, zero_rule
from .map_presentation import WHO_2018
from .source_pilot import BREAKS

MAX_AREA_KM2 = 100
MAX_SIDE_M = 20_000
MARGIN_M = 2_000
DEFAULT_LEVEL_DB = 55  # Used where the WHO gives no guideline (daytime).
MAX_LEVEL_DB = 80
EDGE_POINTS = 65  # Per side of the box: its curved edges on the National Grid, to well under a millimetre.
NO_DATA, BELOW, VALUE = 0, 1, 2
CACHED = 16
METHOD = {
    'cells': 'Native 10 m cells whose centre is inside the box (tested on the British National Grid against the '
             'box with its edges converted by the map\'s pinned transformation). Each cell is read from the Defra '
             'square that owns it, as a click is.',
    'shares': 'Shares are of calculated cells: cells with a published value or under the reporting cutoff. Cells '
              'with no published value are counted separately and are not treated as quiet.',
    'median': 'The level half the calculated cells are at or under (the ceil(n/2)-th lowest); "under the cutoff" '
              'when at least half are under it.',
    'energy_average': '10*log10(mean(10^(L/10))) over calculated cells. Cells under the cutoff count as silence for '
                      'the lower end and as the cutoff for the upper end, so the true average lies between them. '
                      'The loudest cells dominate it.',
    'loudest': 'The cell with the highest published value; ties are counted.',
    'quietest': 'Among cells under the cutoff, the one furthest from any cell at or over the chosen level or with no '
                'published value (which could hide noise). Distances are between cell centres, by an exact '
                'Euclidean distance transform on the 10 m grid. Cells up to 2 km beyond the box are included; a '
                'distance that would reach past that is a lower bound. If no cell is under the cutoff, the cell '
                'with the lowest published value is used instead.',
    'analytical_use': 'Modelled annual transport noise for one source only. Not measured sound, and not other '
                      'sources such as people, industry or nature.',
}


def _edge(a, b):
    f = np.linspace(0, 1, EDGE_POINTS)
    return a + (b - a) * f


def box_on_grid(transformer, west, south, east, north):
    """The box's outline on the National Grid, densified so that straight segments follow its curved edges."""
    lons = np.r_[_edge(west, east), np.full(EDGE_POINTS, east), _edge(east, west), np.full(EDGE_POINTS, west)]
    lats = np.r_[np.full(EDGE_POINTS, south), _edge(south, north), np.full(EDGE_POINTS, north), _edge(north, south)]
    e, n = transformer.transform(lons, lats)
    if not (np.isfinite(e).all() and np.isfinite(n).all()):
        raise ValueError('Draw the box over England')
    return np.asarray(e), np.asarray(n)


def _length(e, n):
    return float(np.hypot(np.diff(e), np.diff(n)).sum())


class AreaSummaries:
    """Summaries for boxes on one England map, with a small cache of recent answers."""

    def __init__(self, england):
        self.england = england
        self.cache, self.lock = OrderedDict(), threading.Lock()
        self.slots = threading.BoundedSemaphore(2)  # Each summary holds a few tens of MB while it runs.

    def summary(self, layer, west, south, east, north, level=None):
        key = (layer, *(round(float(v), 6) for v in (west, south, east, north)), None if level is None else float(level))
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
        if not self.slots.acquire(timeout=30):
            raise TimeoutError('Area summaries are busy')
        try:
            result = summarise(self.england, layer, *key[1:5], level=key[5])
        finally:
            self.slots.release()
        with self.lock:
            self.cache[key] = result
            while len(self.cache) > CACHED:
                self.cache.popitem(last=False)
        return result


def summarise(england, layer, west, south, east, north, level=None):
    if layer not in LAYERS:
        raise KeyError('Unknown map layer')
    source, metric = layer.split('-')
    rule = zero_rule(source, metric)
    if rule is None:
        raise ValueError('Area summaries are for road and rail noise')
    box = [float(v) for v in (west, south, east, north)]
    west, south, east, north = box
    if not (np.isfinite(box).all() and west < east and south < north and -9 <= west and east <= 3
            and 49 <= south and north <= 56.5):
        raise ValueError('Draw the box over England')
    cutoff, who = rule['cutoff_db'], WHO_2018[source].get(metric)
    level = float(level) if level is not None else float(who if who is not None else DEFAULT_LEVEL_DB)
    if not (math.isfinite(level) and cutoff <= level <= MAX_LEVEL_DB):
        raise ValueError(f'Choose a level from {cutoff} to {MAX_LEVEL_DB} dB')
    transformer = england.transformer
    e, n = box_on_grid(transformer, west, south, east, north)
    outline = shapely.Polygon(zip(e, n))
    q = EDGE_POINTS
    sides = [_length(e[k*q:(k+1)*q], n[k*q:(k+1)*q]) for k in range(4)]
    if outline.area > MAX_AREA_KM2 * 1e6 or max(sides) > MAX_SIDE_M:
        raise ValueError(f'Choose an area up to {MAX_AREA_KM2} km², no more than {MAX_SIDE_M // 1000} km across')

    # The grid: native cells covering the box and a margin round it, rows from the north as in the rasters.
    l0, l1 = ea.LATTICE
    gx0 = l0 + CELL_M * math.floor((e.min() - MARGIN_M - l0) / CELL_M)
    gx1 = l0 + CELL_M * math.ceil((e.max() + MARGIN_M - l0) / CELL_M)
    gy0 = l1 + CELL_M * math.floor((n.min() - MARGIN_M - l1) / CELL_M)
    gy1 = l1 + CELL_M * math.ceil((n.max() + MARGIN_M - l1) / CELL_M)
    rows, cols = (gy1 - gy0) // CELL_M, (gx1 - gx0) // CELL_M
    values = np.zeros((rows, cols), dtype='float32')
    status = np.full((rows, cols), NO_DATA, dtype='uint8')

    # Each cell centre belongs to one Defra 10 km square (england_map.owner), so read each square's part.
    i0, j0 = (int(v) for v in owner(np.float64(gx0 + CELL_M / 2), np.float64(gy0 + CELL_M / 2)))
    i1, j1 = (int(v) for v in owner(np.float64(gx1 - CELL_M / 2), np.float64(gy1 - CELL_M / 2)))
    squares = [(i, j, england.records.get((tile_for(i, j), layer))) for i in range(i0, i1 + 1) for j in range(j0, j1 + 1)]
    accepted = [record for _, _, record in squares if record is not None and record['status'] == 'accepted']
    with ThreadPoolExecutor(max(1, min(4, len(accepted)))) as pool:
        rasters = dict(zip((r['path'] for r in accepted), pool.map(england.cache.get, accepted)))
    used = []
    for i, j, record in squares:
        if record is None:
            continue
        used.append({k: record.get(k) for k in ('tile', 'status', 'sha256', 'path')})
        if record['status'] != 'accepted':
            continue
        raster, valid, (rw, rt) = rasters[record['path']]
        west_edge, north_edge = l0 + i * ea.TILE_M, l1 + (j + 1) * ea.TILE_M
        c0, c1 = max(0, (west_edge - gx0) // CELL_M), min(cols, (west_edge + ea.TILE_M - gx0) // CELL_M)
        r0, r1 = max(0, (gy1 - north_edge) // CELL_M), min(rows, (gy1 - north_edge + ea.TILE_M) // CELL_M)
        dc, dr = (gx0 - rw) / CELL_M, (rt - gy1) / CELL_M
        if dc != int(dc) or dr != int(dr):
            raise MapIntegrityError('Raster is not on the 10 m lattice: ' + record['path'])
        dc, dr = int(dc), int(dr)
        # Only the part of this square the raster covers (it normally covers all of it).
        c0, c1 = max(c0, -dc), min(c1, raster.shape[1] - dc)
        r0, r1 = max(r0, -dr), min(r1, raster.shape[0] - dr)
        if c0 >= c1 or r0 >= r1:
            continue
        v = raster[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        ok = valid[r0 + dr:r1 + dr, c0 + dc:c1 + dc]
        if (ok & ~((v >= 0) & (v <= 160))).any():
            raise MapIntegrityError('Unrecognised unmasked noise encoding')
        part = np.full(v.shape, NO_DATA, dtype='uint8')
        part[ok & (v > 0)] = VALUE
        part[ok & (v == 0)] = BELOW
        status[r0:r1, c0:c1] = part
        values[r0:r1, c0:c1] = np.where(part == VALUE, v, 0)

    # Cells whose centre is in the box: tested only over the box's own rows and columns.
    centre_x = gx0 + CELL_M * np.arange(cols) + CELL_M / 2
    centre_y = gy1 - CELL_M * np.arange(rows) - CELL_M / 2
    cs = np.flatnonzero((centre_x > e.min()) & (centre_x < e.max()))
    rs = np.flatnonzero((centre_y > n.min()) & (centre_y < n.max()))
    inside = np.zeros((rows, cols), dtype=bool)
    if cs.size and rs.size:
        shapely.prepare(outline)
        xx, yy = np.meshgrid(centre_x[cs], centre_y[rs])
        inside[np.ix_(rs, cs)] = shapely.contains_xy(outline, xx, yy)
    count = int(inside.sum())
    if count == 0:
        raise ValueError('The box is too small to hold a 10 m cell; draw a larger one')

    here = status[inside]
    level_values = values[inside & (status == VALUE)]
    n_value, n_below = int(level_values.size), int((here == BELOW).sum())
    n_calc = n_value + n_below
    bands = np.bincount(np.searchsorted(BREAKS, level_values, side='right'), minlength=len(BREAKS) + 1)

    def share(k):
        return round(k / n_calc, 6) if n_calc else None
    energy = float(np.power(10.0, level_values.astype('float64') / 10).sum())
    # The typical cell: half the calculated cells are at or under it. Under the cutoff if they are.
    median = None
    if n_calc:
        rank = (n_calc + 1) // 2
        median = ({'under_db': cutoff, 'value_db': None} if rank <= n_below else
                  {'under_db': None, 'value_db': round(float(np.partition(level_values, rank - n_below - 1)[rank - n_below - 1]), 2)})
    average = None
    if n_calc:
        upper = 10 * math.log10((energy + n_below * 10 ** (cutoff / 10)) / n_calc)
        average = {'lower_db': round(10 * math.log10(energy / n_calc), 2) if energy > 0 else None,
                   'upper_db': round(upper, 2), 'below_cutoff_counted_as': [None, cutoff]}

    def place(r, c):
        """A cell of the grid, described as a click would find it."""
        x, y = gx0 + CELL_M * c + CELL_M / 2, gy1 - CELL_M * r - CELL_M / 2
        lon, lat = transformer.transform(x, y, direction='INVERSE')
        corners = [(x - 5 + dx * CELL_M, y + 5 - dy * CELL_M) for dx, dy in ((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))]
        lons, lats = transformer.transform(*zip(*corners), direction='INVERSE')
        tile = tile_for(*owner(np.float64(x), np.float64(y)))
        record = england.records.get((tile, layer))
        state = int(status[r, c])
        return {'longitude': round(float(lon), 7), 'latitude': round(float(lat), 7), 'easting': x, 'northing': y,
                'tile': tile, 'record_sha256': record['sha256'] if record else None,
                'status': ('value', 'below_cutoff', 'no_data')[(VALUE, BELOW, NO_DATA).index(state)],
                'value_db': round(float(values[r, c]), 2) if state == VALUE else None,
                'upper_db': cutoff if state == BELOW else None,
                'cell': {'type': 'Polygon', 'coordinates': [[[round(a, 7), round(b, 7)] for a, b in zip(lons, lats)]]}}

    loudest = None
    if n_value:
        top = float(level_values.max())
        hits = np.argwhere(inside & (status == VALUE) & (values == top))
        loudest = {**place(*hits[0]), 'tied_cells': int(len(hits))}

    quietest = None
    blocked = (status == NO_DATA) | ((status == VALUE) & (values >= level))
    candidates = inside & (status == BELOW)
    kind = 'below_cutoff'
    if not candidates.any() and n_value:
        candidates, kind = inside & (status == VALUE) & (values == level_values.min()), 'lowest_value'
    if candidates.any():
        rr, cc = np.nonzero(candidates)
        # A blocking cell could lie just past the grid: no further than the nearest unseen cell centre.
        edge = CELL_M * np.minimum.reduce([rr + 1, rows - rr, cc + 1, cols - cc]).astype('float64')
        if blocked.any():
            distance, nearest = ndimage.distance_transform_edt(~blocked, sampling=CELL_M, return_indices=True)
            d = distance[rr, cc]
        else:
            d, nearest = np.full(rr.shape, np.inf), None
        clearance = np.minimum(d, edge)
        best = np.lexsort((np.arange(rr.size), -np.where(np.isfinite(d), d, 1e12), -clearance))[0]
        r, c = int(rr[best]), int(cc[best])
        exact = bool(d[best] <= edge[best])
        limit = None
        if exact:
            nr, nc = int(nearest[0, r, c]), int(nearest[1, r, c])
            limit = place(nr, nc)
        quietest = {**place(r, c), 'kind': kind, 'clearance_m': round(float(clearance[best]), 1),
                    'clearance': 'exact' if exact else 'at_least',
                    'limited_by': ('no_published_value' if limit['status'] == 'no_data' else 'at_or_over_level') if exact else 'search_edge',
                    'nearest': limit, 'tied_cells': int((clearance == clearance[best]).sum())}

    who_cells = int((level_values >= who).sum()) if who is not None else None
    level_cells = int((level_values >= level).sum())
    return {
        'release_id': england.release_id, 'layer': layer, 'source': source, 'metric': metric, 'units': 'dB(A)',
        'box': {'west': west, 'south': south, 'east': east, 'north': north},
        'area_km2': round(count * CELL_M * CELL_M / 1e6, 4),
        'cells': {'inside': count, 'value': n_value, 'below_cutoff': n_below, 'no_data': int((here == NO_DATA).sum())},
        'cutoff_db': cutoff,
        'below_cutoff': {'cells': n_below, 'share': share(n_below)},
        'bands': [{'low_db': BREAKS[k - 1] if k else None, 'high_db': BREAKS[k] if k < len(BREAKS) else None,
                   'cells': int(bands[k]), 'share': share(int(bands[k]))} for k in range(len(BREAKS) + 1)],
        'who_2018': None if who is None else {'limit_db': who, 'cells_at_or_over': who_cells, 'share': share(who_cells)},
        'level_db': level, 'at_or_over_level': {'cells': level_cells, 'share': share(level_cells)},
        'median_db': median, 'energy_average_db': average,
        'loudest': loudest, 'quietest': quietest,
        'search_area': {'margin_m': MARGIN_M, 'cells': rows * cols, 'no_data_cells': int((status == NO_DATA).sum())},
        'records': used,
        'evidence_contract_version': CONTRACT_V2['version'], 'zero_rule': rule,
        'missing_value_policy': CONTRACT_V2['missing_value_policy'],
        'transformation': england.manifest['transformation']['description'],
        'uncertainty': england.manifest['uncertainty'], 'method': METHOD,
    }


def area_csv(result):
    """The summary as a two-column table, with the bands and the two places spelled out."""
    out = io.StringIO()
    w = csv.writer(out, lineterminator='\n')
    w.writerow(['field', 'value'])
    b = result['box']
    for field, value in [('release_id', result['release_id']), ('layer', result['layer']), ('units', result['units']),
                         ('box_west_south_east_north', f"{b['west']},{b['south']},{b['east']},{b['north']}"),
                         ('area_km2', result['area_km2']),
                         *((f'cells_{k}', v) for k, v in result['cells'].items()),
                         ('cutoff_db', result['cutoff_db']), ('below_cutoff_share', result['below_cutoff']['share'])]:
        w.writerow([field, value])
    for band in result['bands']:
        name = f"band_{'' if band['low_db'] is None else band['low_db']}_{'' if band['high_db'] is None else band['high_db']}_db"
        w.writerow([name + '_cells', band['cells']]); w.writerow([name + '_share', band['share']])
    if result['who_2018']:
        w.writerow(['who_2018_limit_db', result['who_2018']['limit_db']])
        w.writerow(['who_2018_share_at_or_over', result['who_2018']['share']])
    w.writerow(['level_db', result['level_db']]); w.writerow(['level_share_at_or_over', result['at_or_over_level']['share']])
    median = result['median_db'] or {}
    w.writerow(['median_db', median.get('value_db')]); w.writerow(['median_under_db', median.get('under_db')])
    average = result['energy_average_db'] or {}
    w.writerow(['energy_average_lower_db', average.get('lower_db')]); w.writerow(['energy_average_upper_db', average.get('upper_db')])
    for name in ('loudest', 'quietest'):
        spot = result[name] or {}
        for key in ('longitude', 'latitude', 'easting', 'northing', 'tile', 'status', 'value_db', 'upper_db',
                    'clearance_m', 'clearance', 'limited_by', 'tied_cells'):
            if key in spot:
                w.writerow([f'{name}_{key}', spot[key]])
    for record in result['records']:
        w.writerow(['record', f"{record['tile']} {record['status']} {record['sha256']} {record['path']}"])
    return out.getvalue().encode('utf-8')
