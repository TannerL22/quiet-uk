"""Profile verified point lookup separately from snapshot construction and export."""
import argparse
import cProfile
from collections import defaultdict
from contextlib import contextmanager
import io
import json
from pathlib import Path
import pstats
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from rasterio.warp import transform
from quiet_uk.source_pilot import SourcePilot
from quiet_uk.serving_snapshot import RegionalSnapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot', type=Path, default=ROOT/'artifacts/oxford_london_map_v1')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.output.resolve().is_relative_to(args.pilot.resolve()):
        parser.error('Write profiling evidence outside the immutable release')
    begin = time.perf_counter()
    source = SourcePilot(args.pilot)
    print(f'Source verified in {time.perf_counter()-begin:.2f}s', flush=True)
    begin = time.perf_counter()
    with RegionalSnapshot(source) as snapshot:
        print(f'Snapshot ready in {time.perf_counter()-begin:.2f}s', flush=True)
        cores = [r for r in source.records.values() if r['source']=='road' and r['metric']=='Lden']
        points = []
        for r in cores[:12]:
            w,s,e,n = r.get('core_bounds',r['qa']['bounds'])
            lon,lat=transform('EPSG:27700','EPSG:4326',[(w+e)/2+25],[(s+n)/2+25])
            points.append((r['site'],lon[0],lat[0]))
        samples=[]
        native = defaultdict(list)
        class MeasuredReader:
            def __init__(self, reader):
                self.reader = reader

            def __getattr__(self, name):
                started = time.perf_counter()
                value = getattr(self.reader, name)
                native[name+'.attribute'].append(time.perf_counter()-started)
                if not callable(value):
                    return value
                def measured(*args, **kwargs):
                    started = time.perf_counter()
                    try:
                        return value(*args, **kwargs)
                    finally:
                        native[name+'.call'].append(time.perf_counter()-started)
                return measured

        original_open = snapshot.open_raster
        @contextmanager
        def measured_open(name):
            started = time.perf_counter()
            with original_open(name) as reader:
                native['acquire_reader'].append(time.perf_counter()-started)
                yield MeasuredReader(reader)
        snapshot.open_raster = measured_open
        profiler=cProfile.Profile()
        profiler.enable()
        for point in points+points:
            started=time.perf_counter()
            result=snapshot.lookup(*point)
            assert len(result['observations'])==9
            samples.append(round((time.perf_counter()-started)*1000,3))
        profiler.disable()
        output=io.StringIO()
        pstats.Stats(profiler,stream=output).strip_dirs().sort_stats('cumulative').print_stats(35)
        report={'release_id':source.manifest['release_id'],'lookup_ms':samples,'profile':output.getvalue(),
                'raster_operations': {name: {'calls':len(values),'total_ms':round(sum(values)*1000,3)}
                                      for name,values in sorted(native.items())}}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
        print(output.getvalue(),flush=True)


if __name__=='__main__':
    main()
