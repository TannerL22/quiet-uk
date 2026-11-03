"""Load the frozen real-scene inputs into a RoadSceneBuilder.

Formerly scripts/81_build_real_road_scenes.py:load_builder. The 2 m terrain grid
and mask are memory-mapped from a derived cache keyed by the source GeoTIFF's
sha256, so parallel workers share one copy. Values are the GeoTIFF's own pixels.
"""
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import uuid

import numpy as np
import pyproj
import rasterio
from shapely import from_wkt

from quiet_uk.digests import file_hash
from quiet_uk.ground_cover import CLASSES, validate_native
from quiet_uk.physical_scene import assert_native_grid
from quiet_uk.road_scene_builder import NativeTerrain, RoadSceneBuilder

ROOT = Path(__file__).resolve().parents[2]
RASTER_CACHE = ROOT / 'artifacts/_derived_raster_cache'


def read(path):return json.loads(Path(path).read_bytes())


def rows(path):
    with Path(path).open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))


def _array_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 22), b''):
            digest.update(chunk)
    return digest.hexdigest()


def shared_band(path, bounds):
    """Band 1 and its mask as read-only memory maps, created once per source file."""
    with rasterio.open(path) as d:
        assert_native_grid(d, bounds)
    source_hash = file_hash(path)
    directory = RASTER_CACHE / source_hash
    meta = directory / 'meta.json'
    if not meta.exists():
        with rasterio.open(path) as d:
            values, mask = d.read(1), d.read_masks(1)
        staging = RASTER_CACHE / f'.writing-{uuid.uuid4().hex[:12]}'
        RASTER_CACHE.mkdir(parents=True, exist_ok=True)
        staging.mkdir()
        np.save(staging / 'values.npy', values, allow_pickle=False)
        np.save(staging / 'mask.npy', mask, allow_pickle=False)
        (staging / 'meta.json').write_text(json.dumps(dict(source=str(path), source_sha256=source_hash,
            values_sha256=_array_hash(staging / 'values.npy'), mask_sha256=_array_hash(staging / 'mask.npy'))), encoding='utf-8')
        try:
            os.replace(staging, directory)
        except OSError:
            # Another worker created it first; use theirs.
            for name in ('values.npy', 'mask.npy', 'meta.json'):
                (staging / name).unlink(missing_ok=True)
            staging.rmdir()
    record = json.loads(meta.read_bytes())
    for name in ('values', 'mask'):
        if _array_hash(directory / f'{name}.npy') != record[f'{name}_sha256']:
            raise ValueError('Derived raster cache is damaged: ' + str(directory))
    return (np.load(directory / 'values.npy', mmap_mode='r', allow_pickle=False),
            np.load(directory / 'mask.npy', mmap_mode='r', allow_pickle=False))


def load_builder(physical,ground,*,maximum_reflections=2):
    meta=read(physical/'report.json');bounds=meta['halo_bounds_bng']
    values,mask=shared_band(physical/'terrain_odn_2m.tif',bounds)
    terrain=NativeTerrain(values,mask,bounds)
    with rasterio.open(ground/'landcover_native_wgs84.tif') as d:
        validate_native(d);classes,mask,transform=d.read(1),d.read_masks(1),d.transform
    pipeline=read(ground/'report.json')['coordinate_transform']['pipeline']
    operation=pyproj.Transformer.from_pipeline(pipeline)
    def sample_classes(xy):
        longitude,latitude=operation.transform(xy[:,0],xy[:,1])
        result=[]
        for lon,lat in zip(longitude,latitude):
            col=math.floor((lon-transform.c)/transform.a);row=math.floor((lat-transform.f)/transform.e)
            if not 0<=row<classes.shape[0] or not 0<=col<classes.shape[1]:result.append(dict(row=None,column=None,code=None,state='outside'));continue
            code=int(classes[row,col]);state='nodata' if not mask[row,col] or code==0 else 'reported_class' if code in CLASSES else 'unsupported_class'
            result.append(dict(row=row,column=col,code=code,state=state))
        return result
    buildings=[dict(feature_id='os:'+r['source_id']+':'+r['source_record_number'],geometry=from_wkt(r['geometry_wkt_bng']),
        provisional_height_agl_m=float(r['provisional_height_agl_m']) if r['provisional_height_agl_m'] else None,
        acoustic_top_elevation_odn_m=None,opacity=None,roof_g=None,historical_compatibility=r['historical_footprint_compatibility']) for r in rows(physical/'building_heights_bng.csv')]
    barriers=[dict(feature_id='osm:'+str(r['osm_way_id']),geometry=from_wkt(r['geometry_wkt_bng']),
        acoustic_top_elevation_odn_m=r['acoustic_top_elevation_odn_m'],opacity=r['opaque_and_sealed'],roof_g=None,
        historical_compatibility=r['snapshot_consistency']) for r in read(ground/'barrier_candidates_bng.json')['candidates']]
    return RoadSceneBuilder(terrain,sample_classes,buildings,barriers,maximum_reflections=maximum_reflections)
