"""The place-name index behind the England map's search box (OS Open Names, see notes/ENGLAND_MAP.md)."""
import csv
import hashlib
import io
import json
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen
import warnings
import zipfile

from pyproj import Transformer
import pytest

from quiet_uk import place_names as pn
from quiet_uk.explorer_server import ExplorerServer

warnings.filterwarnings('ignore', message='Best transformation is not available')
OXFORD = (-1.25, 51.75)


def row(i, name, kind, local, east, north, district='', place='', borough='', county='', name2=''):
    r = [''] * len(pn.HEADER)
    r[pn.COLUMN['ID']], r[pn.COLUMN['NAME1']], r[pn.COLUMN['NAME2']] = f'osgb{i}', name, name2
    r[pn.COLUMN['TYPE']], r[pn.COLUMN['LOCAL_TYPE']] = kind, local
    r[pn.COLUMN['GEOMETRY_X']], r[pn.COLUMN['GEOMETRY_Y']] = str(east), str(north)
    r[pn.COLUMN['POSTCODE_DISTRICT']], r[pn.COLUMN['POPULATED_PLACE']] = district, place
    r[pn.COLUMN['DISTRICT_BOROUGH']], r[pn.COLUMN['COUNTY_UNITARY']] = borough, county
    return r


ROWS = [
    row(1, 'Oxford', 'populatedPlace', 'City', 451300, 206300, 'OX1', borough='Oxford', county='Oxfordshire'),
    row(2, 'High Street', 'transportNetwork', 'Named Road', 451500, 206200, 'OX1', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(3, 'London Road', 'transportNetwork', 'Named Road', 454000, 206500, 'OX3', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(4, "St John's Road", 'transportNetwork', 'Named Road', 451000, 207000, 'OX2', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(5, 'OX1 3BD', 'other', 'Postcode', 451450, 206350, '', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(6, 'OX1 4AA', 'other', 'Postcode', 451300, 205900, '', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(7, 'Abingdon', 'populatedPlace', 'Town', 449700, 197100, 'OX14', county='Oxfordshire'),
    row(8, 'Oxford', 'transportNetwork', 'Railway Station', 450400, 206400, 'OX1', 'Oxford', 'Oxford', 'Oxfordshire'),
    row(9, 'High Street', 'transportNetwork', 'Named Road', 530000, 181000, 'EC1', 'London', 'Islington'),
    row(10, 'Caerdydd', 'populatedPlace', 'City', 318000, 176000, 'CF10', county='Cardiff', name2='Cardiff'),
    row(11, 'OX13 5AB', 'other', 'Postcode', 437000, 199000, '', 'Kingston Bagpuize', 'Vale of White Horse', 'Oxfordshire'),
]


def download(path, rows=ROWS, header=pn.HEADER):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        archive.writestr('Doc/OS_Open_Names_Header.csv', '﻿' + ','.join(header) + '\r\n')
        text = io.StringIO()
        csv.writer(text).writerows(rows)
        archive.writestr('Data/SP50.csv', text.getvalue())
    path.write_bytes(buffer.getvalue())
    return path, {**pn.SOURCE, 'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def build(source, output, pinned):
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:27700', always_xy=True)
    return pn.build(source, output, transformer, {'description': 'test', 'definition': transformer.definition},
                    pinned=pinned, log=lambda *a, **k: None)


@pytest.fixture(scope='module')
def index(tmp_path_factory):
    root = tmp_path_factory.mktemp('places')
    source, pinned = download(root/'names.zip')
    build(source, root/'place_names_v1', pinned)
    return root/'place_names_v1'


@pytest.fixture(scope='module')
def names(index):
    places = pn.PlaceNames(index)
    yield places
    places.close()


def found(names, query, near=OXFORD):
    return [(p['name'], p['detail'], p['kind']) for p in names.suggest(query, near=near)['places']]


def test_build_checks_the_pinned_download_and_writes_a_verified_manifest(index, tmp_path):
    manifest = pn.read_manifest(index)
    assert manifest['release_id'].startswith('place-names-') and manifest['rows'] == len(ROWS)
    assert manifest['by_kind'] == {'feature': 1, 'postcode': 3, 'postcode_district': 2, 'road': 4, 'settlement': 3}
    assert manifest['postcodes_indexed'] == 3 and manifest['duplicate_postcodes'] == 0
    data = (index/'places.sqlite3').read_bytes()
    assert manifest['index'] == {'file': 'places.sqlite3', 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    assert 'src/quiet_uk/place_names.py' in manifest['construction']
    source, pinned = download(tmp_path/'names.zip')
    with pytest.raises(pn.PlaceNamesIntegrityError):
        build(source, tmp_path/'changed', {**pinned, 'sha256': '0' * 64})
    with pytest.raises(FileExistsError):
        build(source, index, pinned)
    other, pinned = download(tmp_path/'other.zip', header=pn.HEADER[:-1] + ('SOMETHING_ELSE',))
    with pytest.raises(pn.PlaceNamesIntegrityError):
        build(other, tmp_path/'columns', pinned)
    assert not (tmp_path/'changed').exists() and not (tmp_path/'columns').exists()


def test_a_changed_index_or_manifest_is_refused(index, tmp_path):
    copy = tmp_path/'copy'
    copy.mkdir()
    for name in ('manifest.json', 'places.sqlite3'):
        (copy/name).write_bytes((index/name).read_bytes())
    data = bytearray((copy/'places.sqlite3').read_bytes())
    data[-1] ^= 1
    (copy/'places.sqlite3').write_bytes(bytes(data))
    with pytest.raises(pn.PlaceNamesIntegrityError):
        pn.PlaceNames(copy)
    manifest = json.loads((index/'manifest.json').read_text('utf-8'))
    manifest['rows'] += 1
    (copy/'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(pn.PlaceNamesIntegrityError):
        pn.read_manifest(copy)


def test_words_ignore_case_accents_and_apostrophes():
    assert pn.words("St John's  Road") == ['st', 'johns', 'road']
    assert pn.words('Bishop’s Stortford & Ware') == ['bishops', 'stortford', 'and', 'ware']
    assert pn.words('Ystradgynlais Caffé') == ['ystradgynlais', 'caffe']


def test_suggestions_rank_whole_names_kinds_and_nearness(names):
    assert found(names, 'ox')[0] == ('Oxford', 'Oxfordshire', 'settlement')  # A city before its station.
    assert found(names, 'oxford')[:2] == [('Oxford', 'Oxfordshire', 'settlement'), ('Oxford', 'Oxfordshire', 'feature')]
    assert found(names, 'high st')[0] == ('High Street', 'Oxford, OX1', 'road')        # Abbreviation; the nearer one first.
    assert found(names, 'high st', near=(-0.1, 51.52))[0] == ('High Street', 'London, EC1', 'road')
    assert found(names, 'high street ox') == [('High Street', 'Oxford, OX1', 'road')]   # Words that match the place it is in.
    assert found(names, '10 high street')[0][0] == 'High Street'                        # House numbers aren't in the list.
    assert found(names, 'st johns') == found(names, "st john's rd") == [("St John's Road", 'Oxford, OX2', 'road')]
    assert found(names, 'london rd') == [('London Road', 'Oxford, OX3', 'road')]
    assert found(names, 'caerd') == found(names, 'cardiff') == [('Caerdydd / Cardiff', 'Cardiff', 'settlement')]
    assert found(names, 'zzzz') == []
    result = names.suggest('oxford')
    assert result['release_id'] == names.release_id and result['query_words'] == ['oxford']
    first = result['places'][0]
    assert first['area'] and first['zoom'] == 11.5 and first['word'] == 'City'
    assert abs(first['longitude'] - -1.2577) < .01 and abs(first['latitude'] - 51.7517) < .01
    for bad in ('o', ' x ', 'x' * 101):
        with pytest.raises(ValueError):
            names.suggest(bad)


def test_postcodes_come_first_and_districts_before_full_postcodes(names):
    places = names.suggest('OX1')['places']
    assert (places[0]['name'], places[0]['kind'], places[0]['detail']) == ('OX1', 'postcode_district', 'Oxford')
    assert ('OX13', 'Kingston Bagpuize', 'postcode_district') in found(names, 'OX1')  # Districts that start the same.
    # A typed space ends the outward code: "OX1 3" is OX1 3xx, never the OX13 district or OX13 5AB.
    assert found(names, 'ox1 3') == found(names, 'OX1  3B') == [('OX1 3BD', 'Oxford', 'postcode')]
    assert found(names, 'ox13')[0] == ('OX13', 'Kingston Bagpuize', 'postcode_district')
    assert found(names, 'ox13 5') == [('OX13 5AB', 'Kingston Bagpuize', 'postcode')]
    assert found(names, 'ox13bd') == [('OX1 3BD', 'Oxford', 'postcode')]
    full = names.suggest('OX1 3BD')['places'][0]
    assert full['zoom'] == 16.5 and not full['area']


def test_a_clicked_spot_is_named_by_the_nearest_postcode_then_settlement(names):
    transformer = Transformer.from_crs('EPSG:4326', 'EPSG:27700', always_xy=True)
    lon, lat = transformer.transform(451450 + 150, 206350, direction='INVERSE')
    near = names.near(lon, lat)
    assert near['label'] == 'Near OX1 3BD, Oxford' and abs(near['distance_m'] - 150) <= 2 and near['use'] == 'context only'
    lon, lat = transformer.transform(449700, 197100 + 2500, direction='INVERSE')  # 2.5 km from Abingdon, no postcode near.
    assert names.near(lon, lat)['label'] == 'Near Abingdon'
    assert names.near(-2.5, 53.5)['label'] is None
    with pytest.raises(ValueError):
        names.near(10, 51)


def test_server_suggests_names_and_says_when_the_list_is_missing(index):
    server = ExplorerServer(0, None, pn.Path(__file__).parents[1]/'explorer', place_names=index)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        assert server.place_names_ready.wait(30)
        app = json.load(urlopen(base+'/api/app'))
        assert app['place_names_release'] == pn.read_manifest(index)['release_id'] and app['place_names_problem'] is None
        result = json.load(urlopen(base+'/api/places?' + urlencode({'q': 'high st', 'near': '-0.1,51.52'})))
        assert result['places'][0]['detail'] == 'London, EC1'
        near = json.load(urlopen(base+'/api/places/near?lon=-1.2566&lat=51.7525'))
        assert near['label'].startswith('Near OX1 3BD')
        for query in ('q=o', 'q=ox&near=1', 'q=ox&near=a,b', 'q=ox&extra=1', 'lon=1'):
            path = '/api/places/near?' if query.startswith('lon') else '/api/places?'
            with pytest.raises(HTTPError) as caught:
                urlopen(base + path + query)
            assert caught.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
    server = ExplorerServer(0, None, pn.Path(__file__).parents[1]/'explorer')
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with pytest.raises(HTTPError) as caught:
            urlopen(f'http://127.0.0.1:{server.server_port}/api/places?q=oxford')
        assert caught.value.code == 404 and json.load(caught.value)['code'] == 'places_unavailable'
    finally:
        server.shutdown()
        server.server_close()
