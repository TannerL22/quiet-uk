"""Loopback-only explorer HTTP surface; no arbitrary file or proxy routes."""
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import os
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

import requests

from .explorer import json_bytes
from .catalogue import CatalogueIntegrityError, CatalogueError
from .comparison import compare_places, comparison_csv
from .serving_snapshot import RegionalSnapshot, COPY_CHUNK


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

    def _send_archive(self, path, filename):
        # Open before sending headers so read/open failures can still be a 503.
        with path.open('rb') as stream:
            self.send_response(200)
            self.send_header('Content-Type', 'application/zip')
            self.send_header('Content-Length', str(os.fstat(stream.fileno()).st_size))
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
            self.end_headers()
            try:
                while chunk := stream.read(COPY_CHUNK):
                    self.wfile.write(chunk)
            except OSError:
                # A disconnect or disk error after headers must terminate the
                # response, never append a JSON error to a partial ZIP.
                self.close_connection = True

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
            if url.path == '/api/app':
                return self._json({'app': 'quiet-uk', 'interface_version': 2,
                                   'default_view': 'regional' if self.server.pilot else 'overview',
                                   'regional_serving': 'private_snapshot_v1' if self.server.pilot else None,
                                   'regional_release': self.server.pilot.manifest['release_id'] if self.server.pilot else None,
                                   'overview_release': explorer.manifest['release_id'] if explorer else None})
            national = (url.path in ('/api/dataset', '/api/location', '/downloads/dataset.json', '/downloads/location.json', '/overview')
                        or url.path.startswith('/tiles/'))
            if national and explorer is None:
                if url.path == '/overview':
                    return self._send(b'<h1>Historical overview not installed</h1><p>The detailed regional map is available independently.</p><a href="/">Open Quiet UK</a>', 'text/html; charset=utf-8', 404)
                return self._json({'error': 'Historical overview not installed. Use the detailed regional map.', 'code': 'overview_unavailable'}, 404)
            if url.path.startswith(('/api/pilot', '/downloads/pilot', '/pilot-images/')):
                pilot = self.server.pilot
                if pilot is None:
                    return self._json({'error': 'The source pilot has not been built on this machine.'}, 404)
                if url.path == '/api/pilot':
                    return self._json(pilot.manifest)
                if url.path in ('/api/pilot/comparison', '/downloads/pilot-comparison.json', '/downloads/pilot-comparison.csv'):
                    if set(params) != {'places', 'release'} or any(len(v) != 1 for v in params.values()) or len(params['places'][0]) > 2500:
                        raise ValueError('Supply one bounded place list and release')
                    result = compare_places(pilot, json.loads(params['places'][0]), params['release'][0])
                    if url.path.endswith('.csv'):
                        return self._send(comparison_csv(result), 'text/csv; charset=utf-8', filename=pilot.manifest['release_id']+'-comparison.csv')
                    if url.path.endswith('.json'):
                        return self._send(json_bytes(result), 'application/json; charset=utf-8', filename=pilot.manifest['release_id']+'-comparison.json')
                    return self._json(result)
                if url.path == '/api/pilot/locate':
                    if set(params) != {'lon', 'lat'} or any(len(v) != 1 for v in params.values()):
                        raise ValueError('Supply one longitude and latitude')
                    return self._json({'sites': pilot.locate(float(params['lon'][0]), float(params['lat'][0]))})
                if url.path == '/downloads/pilot.zip':
                    if not self.server.download_slots.acquire(blocking=False):
                        return self._json({'error': 'Evidence downloads are busy. Try again shortly.'}, 503)
                    try:
                        return self._send_archive(pilot.bundle_path(), pilot.manifest['release_id']+'.zip')
                    finally:
                        self.server.download_slots.release()
                if url.path in ('/api/pilot/location', '/downloads/pilot-location.json'):
                    if set(params) != {'site', 'lon', 'lat'} or any(len(v) != 1 for v in params.values()):
                        raise ValueError('Supply one pilot site, longitude and latitude')
                    result = pilot.lookup(params['site'][0], float(params['lon'][0]), float(params['lat'][0]))
                    if url.path.startswith('/downloads/'):
                        return self._send(json_bytes(result), 'application/json; charset=utf-8', filename=pilot.manifest['release_id']+'-location.json')
                    return self._json(result)
                asset = re.fullmatch(r'/pilot-images/(pilot-[a-f0-9]{20})/([a-z0-9-]+-(?:road|rail|aircraft)-(?:Lden|Lday|Lnight))\.png', url.path)
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
            assets = {'/': ('pilot.html' if self.server.pilot else 'index.html', 'text/html; charset=utf-8'),
                      '/overview': ('index.html', 'text/html; charset=utf-8'),
                      '/pilot': ('pilot.html', 'text/html; charset=utf-8'),
                      '/pilot.js': ('pilot.js', 'text/javascript'), '/pilot.css': ('pilot.css', 'text/css'),
                      '/comparison.js': ('comparison.js', 'text/javascript'),
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
    # Wait for in-flight requests before removing their private data files.
    daemon_threads = False

    def __init__(self, port, explorer, asset_root, search=None, pilot=None):
        self.explorer, self.asset_root = explorer, Path(asset_root)
        self.search = search or PlaceSearch()
        self.pilot = None
        self.download_slots = threading.BoundedSemaphore(2)
        super().__init__(('127.0.0.1', port), ExplorerHandler)
        try:
            self.pilot = RegionalSnapshot(pilot) if pilot is not None else None
        except BaseException:
            super().server_close()
            raise

    def server_close(self):
        super().server_close()
        if self.pilot is not None:
            self.pilot.close()

    def get_request(self):
        connection, address = super().get_request()
        connection.settimeout(5)
        return connection, address
