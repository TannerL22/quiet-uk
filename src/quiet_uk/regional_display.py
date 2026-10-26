"""Bounded display-only overviews made from a verified regional snapshot.

No analytical raster or published release is changed. Overview pixels sample
the existing Mercator PNGs with nearest-neighbour selection, including alpha.
"""
from collections import OrderedDict
import math
import struct
import threading
import warnings

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.warp import transform

from .catalogue import CatalogueIntegrityError
from .explorer import rgba_png

OVERVIEW_SIDE = 1024
MAX_DETAIL_IMAGES = 8
MAX_DECODED_PIXELS = 16 * 1024 * 1024  # 64 MiB RGBA, overview included.
MAX_CACHE_ENTRIES = 8
MAX_CACHE_BYTES = 8 * 1024 * 1024


class RegionalDisplay:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.records = {}
        self.groups = {}
        self.cache = OrderedDict()
        self.cache_bytes = 0
        self.lock = threading.Lock()
        release = snapshot.manifest['release_id']
        for record in snapshot.records.values():
            path = snapshot.verified_path(record['display']['path'])
            with path.open('rb') as stream:
                header = stream.read(33)
            if (len(header) != 33 or header[:8] != b'\x89PNG\r\n\x1a\n'
                    or header[12:16] != b'IHDR' or header[24:26] != b'\x08\x06'):
                raise CatalogueIntegrityError('Expected an 8-bit RGBA display PNG')
            width, height = struct.unpack('>II', header[16:24])
            if not 0 < width * height <= MAX_DECODED_PIXELS:
                raise CatalogueIntegrityError('Display image exceeds the supported pixel budget')
            corners = record['display']['corners']
            xs, ys = transform('EPSG:4326', 'EPSG:3857', *zip(*corners))
            bounds = [min(xs), min(ys), max(xs), max(ys)]
            if not np.isfinite(bounds).all() or bounds[0] >= bounds[2] or bounds[1] >= bounds[3]:
                raise CatalogueIntegrityError('Invalid display bounds')
            info = {'id': record['id'], 'width': width, 'height': height,
                    'bounds': [min(c[0] for c in corners), min(c[1] for c in corners),
                               max(c[0] for c in corners), max(c[1] for c in corners)],
                    'mercator_bounds': bounds}
            self.records[record['id']] = info
            key = '/'.join(record[k] for k in ('site', 'source', 'metric'))
            self.groups.setdefault(key, {'record_ids': []})['record_ids'].append(record['id'])
        for key, group in self.groups.items():
            group['record_ids'].sort()
            boxes = [self.records[r]['mercator_bounds'] for r in group['record_ids']]
            w, s, e, n = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes))
            native_density = min(min(self.records[r]['width']/(b[2]-b[0]),
                                     self.records[r]['height']/(b[3]-b[1]))
                                 for r, b in zip(group['record_ids'], boxes))
            factor = min(OVERVIEW_SIDE / max(e-w, n-s), native_density)
            group.update(width=min(OVERVIEW_SIDE, max(1, math.ceil((e-w)*factor-1e-9))),
                         height=min(OVERVIEW_SIDE, max(1, math.ceil((n-s)*factor-1e-9))), mercator_bounds=[w, s, e, n])
            lon, lat = transform('EPSG:3857', 'EPSG:4326', [w, e, e, w], [n, n, s, s])
            group['corners'] = list(map(list, zip(lon, lat)))
            group['url'] = f'/pilot-images/{release}/overview-v1/{key}.png'

    def metadata(self):
        return {'release_id': self.snapshot.manifest['release_id'], 'display_version': 1,
                'analytical_use': False, 'resampling': 'nearest',
                'budget': {'max_images': MAX_DETAIL_IMAGES, 'max_decoded_pixels': MAX_DECODED_PIXELS},
                'records': self.records, 'groups': self.groups}

    def overview(self, key):
        if key not in self.groups:
            raise KeyError('Unknown display selection')
        group = self.groups[key]
        # One build at a time bounds transient arrays and avoids duplicate work.
        with self.lock:
            paths = {r: self.snapshot.verified_path(self.snapshot.records[r]['display']['path'])
                     for r in group['record_ids']}
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            width, height = group['width'], group['height']
            w, s, e, n = group['mercator_bounds']
            xs = w + (np.arange(width) + .5) * ((e-w)/width)
            ys = n - (np.arange(height) + .5) * ((n-s)/height)
            result = np.zeros((height, width, 4), dtype='uint8')
            for record_id in group['record_ids']:
                info = self.records[record_id]
                left, bottom, right, top = info['mercator_bounds']
                columns = np.flatnonzero((xs >= left) & (xs < right))
                rows = np.flatnonzero((ys > bottom) & (ys <= top))
                if not rows.size or not columns.size:
                    continue
                source_columns = np.clip(np.floor((xs[columns]-left)/(right-left)*info['width']).astype(int), 0, info['width']-1)
                source_rows = np.clip(np.floor((top-ys[rows])/(top-bottom)*info['height']).astype(int), 0, info['height']-1)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore', NotGeoreferencedWarning)
                    with rasterio.open(paths[record_id]) as image:
                        if image.count != 4 or image.shape != (info['height'], info['width']):
                            raise CatalogueIntegrityError('Display PNG shape changed')
                        # PNG is sequentially decoded once per core; only one
                        # full core array is live, rather than a regional mosaic.
                        data = image.read()
                samples = data[:, source_rows[:, None], source_columns].transpose(1, 2, 0)
                target = result[rows[0]:rows[-1]+1, columns[0]:columns[-1]+1]
                present = samples[:, :, 3] > 0
                target[present] = samples[present]
                del data, samples
                self.snapshot.verified_path(self.snapshot.records[record_id]['display']['path'])
            payload = rgba_png(result)
            while self.cache and (len(self.cache) >= MAX_CACHE_ENTRIES or self.cache_bytes + len(payload) > MAX_CACHE_BYTES):
                _, removed = self.cache.popitem(last=False)
                self.cache_bytes -= len(removed)
            if len(payload) <= MAX_CACHE_BYTES:
                self.cache[key] = payload
                self.cache_bytes += len(payload)
            return payload
