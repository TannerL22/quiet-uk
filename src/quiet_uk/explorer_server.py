"""Loopback-only explorer HTTP surface; no arbitrary file or proxy routes."""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

import requests

from .explorer import json_bytes
from .catalogue import CatalogueIntegrityError, CatalogueError


class PlaceSearch:
    """Explicit submitted searches only; shared throttle and bounded cache."""
    def __init__(self, endpoint='https://nominatim.openstreetmap.org/search'):
        self.endpoint = endpoint
        self.lock = threading.Lock()
        self.last_request = 0.0
        self.cache = OrderedDict()

    def search(self, query):
        query = query.strip()
        if not 2 <= len(query) <= 160:
            raise ValueError('Enter between 2 and 160 characters')
        with self.lock:
            key = query.casefold()
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            time.sleep(max(0, 1.05 - (time.monotonic()-self.last_request)))
            self.last_request = time.monotonic()
            response = requests.get(self.endpoint, params={'q': query, 'format': 'jsonv2', 'countrycodes': 'gb', 'limit': 5},
                                    headers={'User-Agent': 'QuietUK-LocalExplorer/0.3 (https://github.com/TannerL22/quiet-uk)'}, timeout=(5, 12))
            response.raise_for_status()
            places = [{'name': p['display_name'], 'longitude': float(p['lon']), 'latitude': float(p['lat'])} for p in response.json()]
            self.cache[key] = places
            while len(self.cache) > 128:
                self.cache.popitem(last=False)
            return places


class ExplorerHandler(BaseHTTPRequestHandler):
    def _send(self, payload, mime, status=200, cache='no-store', filename=None):
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Cache-Control', cache)
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _json(self, value, status=200):
        self._send(json_bytes(value), 'application/json; charset=utf-8', status)

    def do_GET(self):
        host = self.headers.get('Host', '')
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if host not in allowed or self.headers.get('Origin', f'http://{host}') != f'http://{host}':
            self._json({'error': 'Local requests only'}, 403)
            return
        url = urlsplit(self.path)
        params = parse_qs(url.query)
        explorer = self.server.explorer
        try:
            if url.path.startswith(('/api/pilot', '/downloads/pilot', '/pilot-images/')):
                pilot = self.server.pilot
                if pilot is None:
                    return self._json({'error': 'The source pilot has not been built on this machine.'}, 404)
                if url.path == '/api/pilot':
                    return self._json(pilot.manifest)
                if url.path == '/api/pilot/locate':
                    if set(params) != {'lon', 'lat'} or any(len(v) != 1 for v in params.values()):
                        raise ValueError('Supply one longitude and latitude')
                    return self._json({'sites': pilot.locate(float(params['lon'][0]), float(params['lat'][0]))})
                if url.path == '/downloads/pilot.zip':
                    return self._send(pilot.bundle(), 'application/zip', filename=pilot.manifest['release_id']+'.zip')
                if url.path in ('/api/pilot/location', '/downloads/pilot-location.json'):
                    if set(params) != {'site', 'lon', 'lat'} or any(len(v) != 1 for v in params.values()):
                        raise ValueError('Supply one pilot site, longitude and latitude')
                    result = pilot.lookup(params['site'][0], float(params['lon'][0]), float(params['lat'][0]))
                    if url.path.startswith('/downloads/'):
                        return self._send(json_bytes(result), 'application/json; charset=utf-8', filename=pilot.manifest['release_id']+'-location.json')
                    return self._json(result)
                asset = re.fullmatch(r'/pilot-images/(pilot-[a-f0-9]{20})/([a-z]+-(?:road|rail|aircraft)-(?:Lden|Lday|Lnight))\.png', url.path)
                if asset and asset[1] == pilot.manifest['release_id'] and asset[2] in pilot.records:
                    return self._send(pilot.verified_path(pilot.records[asset[2]]['display']['path']).read_bytes(), 'image/png', cache='public, max-age=3600')
                return self._json({'error': 'Unknown pilot resource'}, 404)
            if url.path == '/api/dataset':
                return self._json(explorer.manifest)
            if url.path == '/downloads/dataset.json':
                return self._send(json_bytes(explorer.manifest), 'application/json; charset=utf-8', filename=explorer.manifest['release_id']+'-dataset.json')
            if url.path in ('/api/location', '/downloads/location.json'):
                if set(params) != {'lon', 'lat'} or any(len(v) != 1 for v in params.values()):
                    raise ValueError('Supply one longitude and latitude')
                result = explorer.lookup(float(params['lon'][0]), float(params['lat'][0]))
                if url.path.startswith('/downloads/'):
                    return self._send(json_bytes(result), 'application/json; charset=utf-8', filename=explorer.manifest['release_id']+'-location.json')
                return self._json(result)
            if url.path == '/api/search':
                if set(params) != {'q'} or len(params['q']) != 1:
                    raise ValueError('Supply one place query')
                return self._json({'places': self.server.search.search(params['q'][0])})
            tile = re.fullmatch(r'/tiles/([a-z0-9-]+)/(road_rail|aircraft|aircraft_presence)/(\d{1,2})/(\d{1,6})/(\d{1,6})\.png', url.path)
            if tile:
                if tile[1] != explorer.manifest['release_id']:
                    return self._json({'error': 'Unknown dataset generation'}, 404)
                return self._send(explorer.tile(*map(int, tile.groups()[2:]), mode=tile[2]), 'image/png', cache='public, max-age=3600')
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/pilot': ('pilot.html', 'text/html; charset=utf-8'),
                      '/pilot.js': ('pilot.js', 'text/javascript'), '/pilot.css': ('pilot.css', 'text/css'),
                      '/app.js': ('app.js', 'text/javascript'), '/styles.css': ('styles.css', 'text/css'),
                      '/vendor/maplibre-gl.js': ('vendor/maplibre-gl.js', 'text/javascript'),
                      '/vendor/maplibre-gl.css': ('vendor/maplibre-gl.css', 'text/css')}
            if url.path in assets:
                name, mime = assets[url.path]
                return self._send((self.server.asset_root/name).read_bytes(), mime)
            return self._json({'error': 'Not found'}, 404)
        except (ValueError, KeyError) as exc:
            return self._json({'error': str(exc)}, 400)
        except requests.RequestException:
            return self._json({'error': 'Place search is unavailable. Try an example area or latitude, longitude.'}, 503)
        except (CatalogueError, CatalogueIntegrityError, OSError):
            return self._json({'error': 'Dataset integrity or read check failed. No value was returned.'}, 503)

    def log_message(self, fmt, *args):
        # Avoid logging user searches or coordinates.
        if len(args) > 1 and str(args[1]) not in ('200', '304'):
            print('Explorer HTTP status:', args[1], flush=True)


class ExplorerServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, explorer, asset_root, search=None, pilot=None):
        self.explorer, self.asset_root = explorer, Path(asset_root)
        self.search = search or PlaceSearch()
        self.pilot = pilot
        super().__init__(('127.0.0.1', port), ExplorerHandler)

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address
