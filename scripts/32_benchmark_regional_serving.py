"""Offline local serving benchmark; no provider requests or release mutations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import random
import statistics
import sys
import threading
import time
import tracemalloc
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from rasterio.warp import transform
from quiet_uk.source_pilot import SourcePilot
from quiet_uk.explorer_server import ExplorerServer


def peak_rss():
    """Whole-process high-water mark, including GDAL (not an allocation delta)."""
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('faults', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ('peak_working_set', 'working_set',
                'peak_paged', 'paged', 'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile')]
        counters = Counters(); counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        api = ctypes.WinDLL('psapi', use_last_error=True).GetProcessMemoryInfo
        api.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        api.restype = wintypes.BOOL
        if not api(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.peak_working_set
    import resource
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == 'darwin' else value * 1024


def summary(values):
    ordered = sorted(values)
    return {'requests': len(values), 'median_ms': round(statistics.median(values)*1000, 2),
            'p95_ms': round(ordered[math.ceil(len(values)*0.95)-1]*1000, 2),
            'max_ms': round(max(values)*1000, 2)}


def measured_memory(action):
    tracemalloc.start()
    start = time.perf_counter()
    try:
        result = action()
        return {'seconds': round(time.perf_counter()-start, 3),
                'python_peak_bytes': tracemalloc.get_traced_memory()[1],
                'process_lifetime_peak_rss_bytes': peak_rss(), 'result': result}
    finally:
        tracemalloc.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, default=ROOT/'artifacts/source_regions_v2')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/regional-serving-benchmark.json')
    parser.add_argument('--requests', type=int, default=200)
    parser.add_argument('--workers', type=int, default=5)
    args = parser.parse_args()
    if args.requests < 200 or not 1 <= args.workers <= 20:
        parser.error('Use at least 200 requests and 1–20 workers')
    if args.output.resolve().is_relative_to(args.pilot.resolve()):
        parser.error('Write benchmark output outside the immutable release')
    report = {'utc': datetime.now(timezone.utc).isoformat(), 'platform': platform.platform(),
              'processor': platform.processor(), 'logical_cpus': os.cpu_count(),
              'python': platform.python_version(), 'workers': args.workers,
              'notes': ['Offline, loopback HTTP; deterministic varied points; no remote geocoder.',
                        'OS file cache is not flushed; first-request timings are not cold-disk results.',
                        'Python allocation peaks exclude native allocations; RSS is process lifetime high-water.',
                        'Regional release only; results do not establish national scaling.']}
    start = time.perf_counter()
    source = SourcePilot(args.pilot)
    report['source_verification_seconds'] = round(time.perf_counter()-start, 3)
    report['release_id'] = source.manifest['release_id']
    report['source_bytes'] = sum((source.root/name).stat().st_size for name in source.manifest['files'])
    start = time.perf_counter()
    server = ExplorerServer(0, None, ROOT/'explorer', pilot=source)
    report['snapshot_startup_seconds'] = round(time.perf_counter()-start, 3)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    rng = random.Random(20260925)
    sites = list(source.manifest['sites'])
    points = []
    for index in range(args.requests+2):
        site = sites[index % len(sites)]
        record = next(r for r in source.records.values() if r['site'] == site)
        west, south, east, north = record['qa']['bounds']
        x, y = rng.uniform(west+10, east-10), rng.uniform(south+10, north-10)
        lon, lat = transform('EPSG:27700', 'EPSG:4326', [x], [y])
        points.append({'site': site, 'longitude': lon[0], 'latitude': lat[0], 'label': f'Sample {index}'})
    def query(index, comparison=False):
        point = points[index]
        path = ('/api/pilot/comparison?'+urlencode({'release': report['release_id'], 'places': json.dumps(points[index:index+3])})
                if comparison else '/api/pilot/location?'+urlencode({'site': point['site'], 'lon': point['longitude'], 'lat': point['latitude']}))
        begin = time.perf_counter()
        with urlopen(base+path, timeout=60) as response:
            data = json.load(response)
        if data['release_id'] != report['release_id']:
            raise RuntimeError('Mixed release result')
        return time.perf_counter()-begin
    def download():
        size = 0
        with urlopen(base+'/downloads/pilot.zip', timeout=300) as response:
            while chunk := response.read(1024*1024):
                size += len(chunk)
            if size != int(response.headers['Content-Length']):
                raise RuntimeError('Truncated archive')
        return {'download_bytes': size}
    try:
        report['first_point_ms'] = round(query(0)*1000, 2)
        report['first_comparison_ms'] = round(query(0, True)*1000, 2)
        for comparison, name in ((False, 'point'), (True, 'three_places')):
            with ThreadPoolExecutor(max_workers=args.workers) as pool:
                report[name] = summary(list(pool.map(lambda i: query(i, comparison), range(args.requests))))
        report['first_export'] = measured_memory(download)
        report['cached_export'] = measured_memory(download)
        report['private_disk_bytes'] = sum(p.stat().st_size for p in server.pilot.root.parent.rglob('*') if p.is_file())
        report['local_beta_latency_targets_met'] = report['point']['p95_ms'] < 500 and report['three_places']['p95_ms'] < 1500
    finally:
        server.shutdown(); server.server_close(); worker.join(5)
    report['private_copy_cleaned'] = not server.pilot.root.parent.exists()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
