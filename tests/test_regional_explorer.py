"""Regional delivery must not depend on an installed national catalogue."""
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import zipfile
from urllib.error import HTTPError
from urllib.request import urlopen

import numpy as np
import pytest

from quiet_uk.explorer import colourize
from quiet_uk.explorer_server import ExplorerServer
from test_source_pilot import pilot, coords

ROOT = Path(__file__).parents[1]


def test_standalone_app_routes_and_evidence(pilot):
    server = ExplorerServer(0, None, ROOT/'explorer', pilot=pilot)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        app = json.load(urlopen(base+'/api/app'))
        assert app['regional_release'] == pilot.manifest['release_id']
        assert app['overview_release'] is None and app['default_view'] == 'regional'
        assert b'id="coverage-note"' in urlopen(base+'/').read()
        assert b'id="inspect-centre"' in urlopen(base+'/pilot').read()
        for path in ('/api/dataset', '/api/location?lon=0&lat=52', '/downloads/dataset.json', '/tiles/x/road_rail/5/1/1.png', '/overview'):
            with pytest.raises(HTTPError) as caught:
                urlopen(base+path)
            assert caught.value.code == 404
            assert b'overview' in caught.value.read().lower()
        lon, lat = coords(pilot, col=4)
        result = json.load(urlopen(base+f'/api/pilot/location?site=heathrow&lon={lon}&lat={lat}'))
        assert len(result['observations']) == 9
        assert json.load(urlopen(base+'/api/pilot/locate?lon=-3&lat=55'))['sites'] == []
        # Same meaningful point evidence remains available as a download.
        assert json.load(urlopen(base+f'/downloads/pilot-location.json?site=heathrow&lon={lon}&lat={lat}')) == result
    finally:
        server.shutdown(); server.server_close(); worker.join(5)


@pytest.mark.parametrize('regional_only', [True, False])
def test_cli_starts_without_national_files(pilot, tmp_path, monkeypatch, regional_only):
    # Exercise the delivered ZIP layout, not references to the source release.
    extracted = tmp_path/'extracted-bundle'
    with zipfile.ZipFile(io.BytesIO(pilot.bundle())) as bundle:
        bundle.extractall(extracted)
    spec = importlib.util.spec_from_file_location('serve_explorer', ROOT/'scripts/29_serve_explorer.py')
    command = importlib.util.module_from_spec(spec); spec.loader.exec_module(command)
    monkeypatch.setattr(command, 'ROOT', tmp_path)
    def forbidden(*a, **kw):
        pytest.fail('Regional startup accessed or built historical data')
    monkeypatch.setattr(command, 'Explorer', forbidden)
    monkeypatch.setattr(command, 'publish_display', forbidden)
    calls = []
    class Server:
        server_port = 8766
        def __init__(self, port, explorer, assets, search, pilot):
            assert explorer is None
            calls.append(pilot.manifest['release_id'])
        def serve_forever(self): pass
        def server_close(self): pass
    monkeypatch.setattr(command, 'ExplorerServer', Server)
    monkeypatch.setattr(sys, 'argv', ['serve', '--pilot', str(extracted), *(['--regional-only'] if regional_only else [])])
    command.main()
    assert calls == [pilot.manifest['release_id']]
    assert not (tmp_path/'artifacts/explorer_display_v4').exists()


@pytest.mark.parametrize('mode', ['road_rail', 'aircraft_presence'])
def test_hatching_matches_one_continuous_canvas_across_tiles(mode):
    energy = np.full((512, 512), 1e5)
    quality = np.ones((512, 512), dtype='uint8')
    quality[:128] = 2
    expected = colourize(energy, quality, mode, pixel_origin=(7*256, 9*256))
    actual = np.empty_like(expected)
    for row in range(2):
        for col in range(2):
            area = np.s_[row*256:(row+1)*256, col*256:(col+1)*256]
            actual[area] = colourize(energy[area], quality[area], mode, pixel_origin=((7+col)*256, (9+row)*256))
    assert np.array_equal(actual, expected)
