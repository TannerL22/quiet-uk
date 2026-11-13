"""Place names for the England map's search box: a local index of OS Open Names.

Suggestions as you type come from this index on the computer, so nothing typed leaves it.
(OpenStreetMap's Nominatim forbids search-as-you-type; the page still sends a search to it
when Enter is pressed and this index has nothing.) A name only moves the map. Noise values
always come from the 10 m cell that is then checked, so names are context, never evidence.

The index is built once from Ordnance Survey's CSV download, pinned below by size and hash,
by `scripts/126_build_place_names.py`. OS Open Names lists about 3 million names in Great
Britain: settlements, named and numbered roads, postcodes, stations, schools, hospitals and
natural features, each with one point. It has no house numbers.

Positions are converted from the British National Grid with the England map's own pinned
transformation, so a name lands where the map draws it.
"""
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import unicodedata
import zipfile

import numpy as np

from .digests import file_hash, json_bytes

SOURCE = {
    'name': 'OS Open Names',
    'publisher': 'Ordnance Survey',
    'version': '2026-10',
    'product': 'https://www.ordnancesurvey.co.uk/products/os-open-names',
    'url': 'https://api.os.uk/downloads/v1/products/OpenNames/downloads?area=GB&format=CSV&redirect',
    'file': 'opname_csv_gb.zip',
    'bytes': 103312081,
    'md5': 'bba5aa44ba375d39de60584099e335cd',  # As published by the OS Downloads API.
    'sha256': '81ee5ba2c96d8fe5ddbf83fb4f9928e8740563618c6c5eb9376280b399a74532',
    'retrieved': '2026-11-07',
    'crs': 'EPSG:27700',
    'licence': 'Open Government Licence v3.0',
    'attribution': 'Contains OS data © Crown copyright and database right 2026.',
}
DEFAULT_SOURCE = Path('artifacts') / 'place_names' / 'os_open_names_2026_10' / SOURCE['file']
DEFAULT_INDEX = Path('artifacts') / 'place_names_v1'
HEADER = ('ID', 'NAMES_URI', 'NAME1', 'NAME1_LANG', 'NAME2', 'NAME2_LANG', 'TYPE', 'LOCAL_TYPE', 'GEOMETRY_X',
          'GEOMETRY_Y', 'MOST_DETAIL_VIEW_RES', 'LEAST_DETAIL_VIEW_RES', 'MBR_XMIN', 'MBR_YMIN', 'MBR_XMAX', 'MBR_YMAX',
          'POSTCODE_DISTRICT', 'POSTCODE_DISTRICT_URI', 'POPULATED_PLACE', 'POPULATED_PLACE_URI', 'POPULATED_PLACE_TYPE',
          'DISTRICT_BOROUGH', 'DISTRICT_BOROUGH_URI', 'DISTRICT_BOROUGH_TYPE', 'COUNTY_UNITARY', 'COUNTY_UNITARY_URI',
          'COUNTY_UNITARY_TYPE', 'REGION', 'REGION_URI', 'COUNTRY', 'COUNTRY_URI', 'RELATED_SPATIAL_OBJECT',
          'SAME_AS_DBPEDIA', 'SAME_AS_GEONAMES')
COLUMN = {name: i for i, name in enumerate(HEADER)}

# How each kind of name is ranked (lower first), worded and shown. Towns, villages and areas are
# places to look around ("area": the page moves there without choosing a spot); a road, postcode or
# building is a point to check.
SETTLEMENTS = {'City': (0, 11.5, True), 'Town': (1, 12.5, True), 'Village': (2, 14, True),
               'Suburban Area': (2, 14, True), 'Hamlet': (3, 15, False), 'Other Settlement': (3, 15, False)}
ROADS = ('Named Road', 'Section Of Named Road', 'Numbered Road', 'Section Of Numbered Road')
TIER_ROAD, TIER_OTHER, TIER_NATURAL, TIER_DISTRICT, TIER_POSTCODE = 4, 5, 6, 8, 9
NATURAL = ('hydrography', 'landform', 'landcover')
WORDS = {
    'Suburban Area': 'Area', 'Other Settlement': 'Settlement', 'Railway Station': 'Railway station',
    'Bus Station': 'Bus station', 'Coach Station': 'Coach station', 'Bus Station,Coach Station': 'Bus station',
    'Port Consisting of Docks and Nautical Berthing': 'Port', 'Passenger Ferry Terminal': 'Ferry terminal',
    'Vehicular Ferry Terminal': 'Ferry terminal', 'Vehicular Rail Terminal': 'Rail terminal',
    'Road User Services': 'Services', 'Medical Care Accommodation': 'Care home', 'Higher or University Education': 'University',
    'Further Education': 'College', 'Woodland Or Forest': 'Wood', 'Hill Or Mountain': 'Hill',
    'Hill Or Mountain Ranges': 'Hills', 'Inland Water': 'Lake or reservoir', 'Tidal Water': 'Tidal water',
    'Urban Greenspace': 'Park', 'Other Landcover': 'Land', 'Cirque Or Hollow': 'Hollow', 'Cliff Or Slope': 'Cliff',
    'Coastal Headland': 'Headland', 'Other Coastal Landform': 'Coast', 'Other Landform': 'Landform',
    'Spot Height': 'Height', 'Group Of Islands': 'Islands',
}
SCHOOLS = ('Primary Education', 'Secondary Education', 'Special Needs Education', 'Non State Primary Education',
           'Non State Secondary Education')
ABBREVIATIONS = {
    'rd': ('road',), 'st': ('street', 'saint'), 'saint': ('st',), 'ave': ('avenue',), 'av': ('avenue',), 'ln': ('lane',),
    'cl': ('close',), 'cres': ('crescent',), 'cresc': ('crescent',), 'dr': ('drive',), 'gdns': ('gardens',),
    'gr': ('grove',), 'gro': ('grove',), 'pk': ('park',), 'pl': ('place',), 'sq': ('square',), 'ter': ('terrace',),
    'terr': ('terrace',), 'ct': ('court',), 'mt': ('mount',), 'gt': ('great',), 'lt': ('little',), 'hts': ('heights',),
    'mws': ('mews',), 'wk': ('walk',),
}
SUGGESTIONS = 8
CANDIDATES = 1000  # Matches read per query, in rank order, before the closest are chosen.
SAME_NAME = 3  # At most this many suggestions share one name (so "Oxford" isn't eight Oxford Roads).
# Suggestion order: how well the name fits what was typed (it starts with all of it; with the first
# word; has it later; only the place it is in matches), plus the kind's tier, plus a cost for each
# typed word found in neither the name nor the place it is in (only its county, say).
FIT_COST, MISS_COST = (0, 1, 2, 8), 2
# Kinds of feature ranked like settlements: people search for them by name.
TYPE_TIERS = {'Airport': 1, 'Railway Station': 3}
NEAR_POSTCODE_M, NEAR_SETTLEMENT_M = 400, 3000
POSTCODE_START = re.compile(r'[A-Z]{1,2}[0-9][A-Z0-9]?(?:[0-9][A-Z]{0,2})?')
OUTWARD = re.compile(r'[A-Z]{1,2}[0-9][A-Z0-9]?')
INWARD_START = re.compile(r'[0-9][A-Z]{0,2}')
HOUSE_NUMBER = re.compile(r'\d+[a-z]?|flat|unit|apartment')
_APOSTROPHES = re.compile(r"['’`]")
_WORD = re.compile(r'[^\W_]+')


class PlaceNamesIntegrityError(ValueError):
    pass


def words(text):
    """Lower-case words without accents or apostrophes: how names are indexed and queries read."""
    text = _APOSTROPHES.sub('', text.casefold()).replace('&', ' and ')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c))
    return _WORD.findall(text)


def postcode_key(text):
    return re.sub(r'\s+', '', text).upper()


def describe_row(row):
    """Ranking tier, kind, plain word, detail line, zoom and area flag for one OS Open Names row."""
    local, group = row[COLUMN['LOCAL_TYPE']], row[COLUMN['TYPE']]
    name = row[COLUMN['NAME1']]
    place, district, county = (row[COLUMN[k]] for k in ('POPULATED_PLACE', 'DISTRICT_BOROUGH', 'COUNTY_UNITARY'))
    where = next((v for v in (place, district, county) if v and v != name), '')
    if local == 'Postcode':
        return TIER_POSTCODE, 'postcode', 'Postcode', where, 16.5, False
    if local in SETTLEMENTS:
        tier, zoom, area = SETTLEMENTS[local]
        return tier, 'settlement', WORDS.get(local, local), next((v for v in (county, district) if v and v != name), ''), zoom, area
    if local in ROADS:
        detail = ', '.join(v for v in (where, row[COLUMN['POSTCODE_DISTRICT']]) if v)
        return TIER_ROAD, 'road', 'Road', detail, 16, False
    word = (WORDS.get(local) or ('School' if any(s in local.split(',') for s in SCHOOLS) else None)
            or ('College' if 'Further Education' in local else None) or local.split(',')[0].capitalize())
    tier = TYPE_TIERS.get(local, TIER_NATURAL if group in NATURAL else TIER_OTHER)
    return tier, 'feature', word, where, (13 if local == 'Airport' else 15), False


def build(source, output, transformer, transformation, *, pinned=SOURCE, log=print):
    """Build the index from the OS Open Names CSV download (checked against its pin)."""
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    if source.stat().st_size != pinned['bytes'] or file_hash(source) != pinned['sha256']:
        raise PlaceNamesIntegrityError('The OS Open Names download differs from the pinned file')
    staging = output.with_name(output.name + '.partial')
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    work = sqlite3.connect(staging / 'work.sqlite3')
    work.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF;'
                       'CREATE TABLE raw (tier INTEGER, kind TEXT, type TEXT, word TEXT, name TEXT, detail TEXT, '
                       'search_name TEXT, context TEXT, postcode TEXT, zoom REAL, area INTEGER, place TEXT, '
                       'easting REAL, northing REAL, longitude REAL, latitude REAL, os_id TEXT)')
    rows_read = 0
    with zipfile.ZipFile(source) as archive:
        header = archive.read('Doc/OS_Open_Names_Header.csv').decode('utf-8-sig').strip().split(',')
        if tuple(header) != HEADER:
            raise PlaceNamesIntegrityError('Unexpected OS Open Names columns')
        files = sorted(n for n in archive.namelist() if n.startswith('Data/') and n.endswith('.csv'))
        for k, name in enumerate(files, 1):
            with archive.open(name) as stream:
                rows = list(csv.reader(io.TextIOWrapper(stream, 'utf-8-sig', newline='')))
            if any(len(r) != len(HEADER) for r in rows):
                raise PlaceNamesIntegrityError(f'Unexpected row length in {name}')
            if not rows:
                continue
            east = np.array([float(r[COLUMN['GEOMETRY_X']]) for r in rows])
            north = np.array([float(r[COLUMN['GEOMETRY_Y']]) for r in rows])
            lon, lat = transformer.transform(east, north, direction='INVERSE')
            if not (np.isfinite(lon).all() and np.isfinite(lat).all()):
                raise PlaceNamesIntegrityError(f'Positions in {name} could not be converted')
            records = []
            for r, e, n, lo, la in zip(rows, east, north, lon, lat):
                tier, kind, word, detail, zoom, area = describe_row(r)
                name1, name2 = r[COLUMN['NAME1']], r[COLUMN['NAME2']]
                context = (' '.join(words(' '.join(r[COLUMN[c]] for c in ('POPULATED_PLACE', 'DISTRICT_BOROUGH',
                                                                           'COUNTY_UNITARY', 'POSTCODE_DISTRICT'))))
                           if kind != 'postcode' else '')
                records.append((tier, kind, r[COLUMN['LOCAL_TYPE']], word, name1 + (f' / {name2}' if name2 else ''), detail,
                                ' '.join(words(f'{name1} {name2}')), context,
                                postcode_key(name1) if kind == 'postcode' else None, zoom, int(area),
                                r[COLUMN['POPULATED_PLACE']] or r[COLUMN['DISTRICT_BOROUGH']] or r[COLUMN['COUNTY_UNITARY']],
                                float(e), float(n), round(float(lo), 7), round(float(la), 7), r[COLUMN['ID']]))
            work.executemany('INSERT INTO raw VALUES (' + ','.join('?' * 17) + ')', records)
            rows_read += len(rows)
            if k % 100 == 0 or k == len(files):
                work.commit()
                log(f'{k}/{len(files)} files, {rows_read:,} names', flush=True)

    # Postcode districts (OX1, M25 ...): the mean of their postcodes' points, named by their commonest place.
    districts = {}
    for outward, place, count, east, north in work.execute(
            "SELECT substr(name, 1, instr(name, ' ') - 1), place, COUNT(*), SUM(easting), SUM(northing) "
            "FROM raw WHERE kind = 'postcode' AND instr(name, ' ') > 0 GROUP BY 1, 2"):
        d = districts.setdefault(outward, {'count': 0, 'east': 0.0, 'north': 0.0, 'places': Counter()})
        d['count'] += count; d['east'] += east; d['north'] += north
        if place:
            d['places'][place] += count
    keys = sorted(districts)
    east = np.array([districts[k]['east'] / districts[k]['count'] for k in keys])
    north = np.array([districts[k]['north'] / districts[k]['count'] for k in keys])
    lon, lat = transformer.transform(east, north, direction='INVERSE') if keys else ([], [])
    work.executemany('INSERT INTO raw VALUES (' + ','.join('?' * 17) + ')', [
        (TIER_DISTRICT, 'postcode_district', 'Postcode district', 'Postcode district', k,
         districts[k]['places'].most_common(1)[0][0] if districts[k]['places'] else '', k.lower(), '', k, 13, 0, '',
         float(e), float(n), round(float(lo), 7), round(float(la), 7), 'district:' + k)
        for k, e, n, lo, la in zip(keys, east, north, lon, lat)])
    work.commit()
    log(f'{len(keys):,} postcode districts; ordering the index', flush=True)

    # Row ids follow rank, so a search reads the most important matches first and can stop early.
    work.execute('CREATE TABLE ordered AS SELECT ROW_NUMBER() OVER (ORDER BY tier, length(name), name, detail, os_id) AS id, * FROM raw')
    work.execute('DROP TABLE raw')
    work.execute('ATTACH DATABASE ? AS out', (str(staging / 'places.sqlite3'),))
    work.executescript("""
        CREATE TABLE out.places (id INTEGER PRIMARY KEY, name TEXT NOT NULL, detail TEXT NOT NULL, kind TEXT NOT NULL,
            type TEXT NOT NULL, word TEXT NOT NULL, tier INTEGER NOT NULL, zoom REAL NOT NULL, area INTEGER NOT NULL,
            longitude REAL NOT NULL, latitude REAL NOT NULL, easting REAL NOT NULL, northing REAL NOT NULL, os_id TEXT NOT NULL);
        INSERT INTO out.places SELECT id, name, detail, kind, type, word, tier, zoom, area, longitude, latitude,
            easting, northing, os_id FROM ordered ORDER BY id;
        CREATE VIRTUAL TABLE out.search USING fts5(name, context, content='', columnsize=0, prefix='1 2 3',
            tokenize='unicode61 remove_diacritics 2');
        INSERT INTO out.search (rowid, name, context) SELECT id, search_name, context FROM ordered
            WHERE kind NOT IN ('postcode', 'postcode_district') ORDER BY id;
        INSERT INTO out.search (search) VALUES ('optimize');
        CREATE TABLE out.postcodes (key TEXT PRIMARY KEY, id INTEGER NOT NULL) WITHOUT ROWID;
        INSERT OR IGNORE INTO out.postcodes SELECT postcode, id FROM ordered WHERE kind = 'postcode' ORDER BY postcode, id;
        CREATE TABLE out.districts (key TEXT PRIMARY KEY, id INTEGER NOT NULL) WITHOUT ROWID;
        INSERT INTO out.districts SELECT postcode, id FROM ordered WHERE kind = 'postcode_district' ORDER BY postcode;
        CREATE VIRTUAL TABLE out.near_postcode USING rtree(id, min_lon, max_lon, min_lat, max_lat);
        INSERT INTO out.near_postcode SELECT id, longitude, longitude, latitude, latitude FROM ordered WHERE kind = 'postcode';
        CREATE VIRTUAL TABLE out.near_settlement USING rtree(id, min_lon, max_lon, min_lat, max_lat);
        INSERT INTO out.near_settlement SELECT id, longitude, longitude, latitude, latitude FROM ordered WHERE kind = 'settlement';
    """)
    work.commit()
    by_kind = dict(work.execute('SELECT kind, COUNT(*) FROM ordered GROUP BY kind ORDER BY kind'))
    by_type = dict(work.execute('SELECT type, COUNT(*) FROM ordered GROUP BY type ORDER BY type'))
    postcodes = work.execute('SELECT COUNT(*) FROM out.postcodes').fetchone()[0]
    work.execute('DETACH DATABASE out')
    work.close()
    (staging / 'work.sqlite3').unlink()
    index = staging / 'places.sqlite3'
    root = Path(__file__).resolve().parents[2]
    construction = {path.relative_to(root).as_posix(): file_hash(path) for path in
                    (Path(__file__).resolve(), root / 'scripts' / '126_build_place_names.py') if path.exists()}
    manifest = {
        'schema': 'quiet-uk-place-names', 'schema_version': 1,
        'title': 'Quiet UK · place names for search (OS Open Names)',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'source': pinned,
        'index': {'file': index.name, 'bytes': index.stat().st_size, 'sha256': file_hash(index)},
        'rows': rows_read, 'by_kind': by_kind, 'by_type': by_type, 'postcodes_indexed': postcodes,
        'duplicate_postcodes': by_kind.get('postcode', 0) - postcodes,
        'transformation': {k: transformation[k] for k in ('description', 'definition', 'grids') if k in transformation},
        'construction': construction,
        'use': 'Finding places only. A name moves the map; noise values always come from the 10 m cell then checked.',
        'positions': 'One OS Open Names point per name, converted from British National Grid with the transformation above. '
                     'Postcode districts are the mean of their postcode points.',
    }
    manifest['release_id'] = 'place-names-' + hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    (staging / 'manifest.json').write_bytes(json_bytes(manifest))
    staging.replace(output)
    return manifest


def read_manifest(root):
    manifest = json.loads((Path(root) / 'manifest.json').read_text('utf-8'))
    base = {k: v for k, v in manifest.items() if k != 'release_id'}
    if (manifest.get('schema') != 'quiet-uk-place-names' or manifest.get('schema_version') != 1
            or manifest.get('release_id') != 'place-names-' + hashlib.sha256(json_bytes(base)).hexdigest()[:20]):
        raise PlaceNamesIntegrityError('Place-name index manifest checksum/schema mismatch')
    return manifest


def _distance_m(lon1, lat1, lon2, lat2):
    """Ground distance, accurate to well under 1% over a few kilometres."""
    dx = (lon2 - lon1) * 111_320 * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, (lat2 - lat1) * 110_540)


class PlaceNames:
    """A verified, read-only place-name index."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.manifest = read_manifest(self.root)
        self.release_id = self.manifest['release_id']
        path = self.root / self.manifest['index']['file']
        if path.stat().st_size != self.manifest['index']['bytes'] or file_hash(path) != self.manifest['index']['sha256']:
            raise PlaceNamesIntegrityError('The place-name index differs from its manifest')
        self.lock = threading.Lock()
        self.db = sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, check_same_thread=False)
        self.db.row_factory = sqlite3.Row

    def close(self):
        self.db.close()

    def public(self):
        s = self.manifest['source']
        return {'release_id': self.release_id, 'name': s['name'], 'publisher': s['publisher'], 'version': s['version'],
                'licence': s['licence'], 'attribution': s['attribution'], 'product': s['product'], 'sha256': s['sha256'],
                'rows': self.manifest['rows'], 'by_kind': self.manifest['by_kind'], 'use': self.manifest['use'],
                'positions': self.manifest['positions']}

    def _rows(self, ids):
        if not ids:
            return {}
        with self.lock:
            found = self.db.execute(f'SELECT * FROM places WHERE id IN ({",".join("?" * len(ids))})', list(ids)).fetchall()
        return {row['id']: row for row in found}

    def _match(self, expression, limit):
        with self.lock:
            return [r[0] for r in self.db.execute('SELECT rowid FROM search WHERE search MATCH ? ORDER BY rowid LIMIT ?',
                                                  (expression, limit))]

    def _postcodes(self, query):
        key = postcode_key(query)
        if not POSTCODE_START.fullmatch(key):
            return []
        # A typed space ends the outward code: "OX1 3" is the start of OX1 3xx, not the OX13 district.
        # Full postcodes are stored without the space, and the inward code is always three characters.
        parts = query.upper().split()
        outward = (parts[0] if len(parts) == 2 and OUTWARD.fullmatch(parts[0]) and INWARD_START.fullmatch(parts[1])
                   else None)
        ids = []
        with self.lock:
            if outward:
                ids += [r[0] for r in self.db.execute(
                    'SELECT id FROM postcodes WHERE key >= ? AND key < ? AND length(key) = ? ORDER BY key LIMIT ?',
                    (key, key + '~', len(outward) + 3, SUGGESTIONS))]
            elif OUTWARD.fullmatch(key):
                exact = self.db.execute('SELECT id FROM districts WHERE key = ?', (key,)).fetchall()
                ids += [r[0] for r in exact] + [r[0] for r in self.db.execute(
                    'SELECT id FROM districts WHERE key > ? AND key < ? ORDER BY length(key), key LIMIT 4', (key, key + '~'))]
            if not outward and (len(key) > 4 or not ids):
                ids += [r[0] for r in self.db.execute('SELECT id FROM postcodes WHERE key >= ? AND key < ? ORDER BY key LIMIT ?',
                                                      (key, key + '~', SUGGESTIONS))]
        rows = self._rows(ids)
        return [rows[i] for i in ids if i in rows]

    def suggest(self, query, near=None, limit=SUGGESTIONS):
        """Up to `limit` places for what has been typed so far, best first. `near` is (lon, lat) of the
        map view, used to put closer places first among equally good matches."""
        query = query.strip()
        if not 2 <= len(query) <= 100:
            raise ValueError('Type between 2 and 100 characters')
        postcodes = self._postcodes(query)
        typed = words(query)
        # House numbers aren't in OS Open Names; "10 Downing Street" finds the street.
        while len(typed) > 1 and HOUSE_NUMBER.fullmatch(typed[0]):
            typed.pop(0)
        named = self._named(typed, near) if typed else []
        ordered, seen = [], set()
        for row in postcodes + named:
            label = (row['name'].casefold(), row['detail'].casefold(), row['kind'])
            if label not in seen:
                seen.add(label); ordered.append(row)
        # Variety: other names appear at most SAME_NAME times, unless nothing else is left. The best
        # match's own name is not limited ("Downing Street" lists every Downing Street).
        top = ordered[0]['name'].casefold() if ordered else None
        chosen, names = [], Counter()
        for row in ordered:
            key = row['name'].casefold()
            if key == top or row['kind'] == 'postcode' or names[key] < SAME_NAME:
                chosen.append(row); names[key] += 1
        chosen += [row for row in ordered if row not in chosen]
        return {'release_id': self.release_id, 'query_words': typed,
                'places': [self._public_row(row) for row in chosen[:limit]]}

    def _named(self, typed, near):
        last = len(typed) - 1

        def quoted(word):
            return '"' + word.replace('"', '""') + '"'

        terms = []
        for i, word in enumerate(typed):
            options = [quoted(word) + ('*' if i == last else '')] + [quoted(w) for w in ABBREVIATIONS.get(word, ())]
            terms.append('(' + ' OR '.join(options) + ')')
        # Names that start with what was typed, then any name or place matching every word.
        start = self._match('name : ^ ' + quoted(' '.join(typed)) + ' *', CANDIDATES)
        anywhere = self._match(' AND '.join(terms), CANDIDATES)
        rows = self._rows(list(dict.fromkeys(start + anywhere)))
        first = {typed[0], *ABBREVIATIONS.get(typed[0], ())}

        def found(word, among):
            return any(n.startswith(w) for n in among for w in (word, *ABBREVIATIONS.get(word, ())))

        def rank(row):
            name = words(row['name'])
            if name[:last] == typed[:last] and len(name) > last and name[last].startswith(typed[last]):
                fit = 0  # The name starts with everything typed.
            elif name and any(name[0].startswith(w) for w in first):
                fit = 1
            elif any(n.startswith(w) for n in name for w in first):
                fit = 2
            else:
                fit = 3  # Matched only through the place it is in.
            # A word not in the name should name the place it's in ("Oxford, OX1"), not only its county.
            rest = [w for w in typed if not found(w, name)]
            detail = words(row['detail']) if rest else ()
            misses = sum(1 for w in rest if not found(w, detail))
            distance = _distance_m(near[0], near[1], row['longitude'], row['latitude']) if near else 0
            tier = TYPE_TIERS.get(row['type'], row['tier'])
            return fit, FIT_COST[fit] + tier + MISS_COST * misses, round(distance, -2), len(row['name']), row['id']
        ranked = sorted(((rank(row), row) for row in rows.values()), key=lambda item: item[0][1:])
        # One word that only matches the place something is in ("ox" for every road in Oxfordshire)
        # is noise unless nothing else matches.
        if len(typed) == 1 and any(key[0] < 3 for key, _ in ranked):
            ranked = [item for item in ranked if item[0][0] < 3]
        return [row for _, row in ranked]

    @staticmethod
    def _public_row(row):
        return {'id': row['id'], 'name': row['name'], 'detail': row['detail'], 'kind': row['kind'], 'type': row['type'],
                'word': row['word'], 'longitude': row['longitude'], 'latitude': row['latitude'], 'zoom': row['zoom'],
                'area': bool(row['area'])}

    def near(self, lon, lat):
        """A short name for a clicked spot: the nearest postcode point within 400 m, else the nearest
        settlement within 3 km, else none."""
        if not (math.isfinite(lon) and math.isfinite(lat) and -9 <= lon <= 3 and 49 <= lat <= 61):
            raise ValueError('Supply a longitude and latitude in Great Britain')
        for table, radius in (('near_postcode', NEAR_POSTCODE_M), ('near_settlement', NEAR_SETTLEMENT_M)):
            dlat, dlon = radius / 110_540, radius / (111_320 * math.cos(math.radians(lat)))
            with self.lock:
                ids = [r[0] for r in self.db.execute(
                    f'SELECT id FROM {table} WHERE min_lon >= ? AND max_lon <= ? AND min_lat >= ? AND max_lat <= ?',
                    (lon - dlon, lon + dlon, lat - dlat, lat + dlat))]
            best = None
            for row in self._rows(ids).values():
                d = _distance_m(lon, lat, row['longitude'], row['latitude'])
                if d <= radius and (best is None or (d, row['id']) < best[0]):
                    best = ((d, row['id']), row)
            if best:
                (distance, _), row = best
                label = f'Near {row["name"]}, {row["detail"]}' if row['kind'] == 'postcode' and row['detail'] else f'Near {row["name"]}'
                return {'release_id': self.release_id, 'label': label, 'distance_m': round(distance), **self._public_row(row),
                        'use': 'context only'}
        return {'release_id': self.release_id, 'label': None, 'use': 'context only'}
