"""Check NoiseModelling (CNOSSOS-EU) against Defra Round 4 road noise in one test square.

Before any quiet place is filled in, our own road calculation must reproduce
Defra where Defra publishes values. This prepares open inputs for the
north-Heathrow test square, runs NoiseModelling at Defra's own 10 m cell centres
(4 m above ground) and compares the two cell by cell.

  --prepare   build inputs for each case (roads differ between cases;
              --sample N uses every Nth Defra cell per axis)
  --run       run NoiseModelling for each case. Test variants go in <case>/<label>:
              --label NAME, --set SETTING=VALUE (NoiseModelling settings),
              --favourable D=,E=,N= (share of time with sound-carrying weather),
              --ground-file NAME (another ground-type file in shared/)
  --compare   compare every finished run with Defra and write report.json

Findings and the full test history are in notes/NOISEMODELLING_CHECK.md.

The pass bar was set before any result was seen: RMSE <= 3 dB and |bias| <= 1 dB
over cells where Defra publishes a value.
"""
import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sqlite3
import statistics
import subprocess
import sys
import time

import numpy as np
from pyproj import CRS
from pyproj.enums import WktVersion
import rasterio
from rasterio import features
from rasterio.transform import from_origin
from rasterio.warp import Resampling, reproject
import shapefile
from shapely import make_valid, wkb, wkt
from shapely.geometry import LineString, Point, box, shape
from shapely.geometry.polygon import orient
from shapely.strtree import STRtree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk import england_acquisition as ea
from quiet_uk.digests import file_hash, json_bytes

TOOLS = ROOT.parent / 'tools'
NOISEMODELLING = TOOLS / 'noisemodelling' / 'NoiseModelling_6.0.0'
JAVA = next((TOOLS / 'jdk').glob('jdk-25*'), TOOLS / 'jdk') / 'bin' / 'java.exe'
ACQUISITION = ROOT / 'artifacts/england_acquisition_v1'
SCENE = ROOT / 'artifacts/physical_scene_inputs_v2'
COVER = ROOT / 'artifacts/ground_cover_inputs_v3/landcover_native_wgs84.tif'
TIMING = ROOT / 'artifacts/road_temporal_audit_v2/national_2021_assumption_candidates.json'
RAW = ROOT / 'data/raw/phase2b_road'
AADF = RAW / 'aadf/dft_traffic_counts_aadf.csv'
MRDB = RAW / 'mrdb_2021/MRDB_2021.shp'
OS_ROADS = RAW / 'os_open_roads/Data/oproad_gb.gpkg'

CORE = (506000, 178000, 508000, 180000)  # receivers: Defra cell centres in this square
SOURCE_DISTANCE_M = 3000  # Defra's road source-distance limit
SOURCES = (CORE[0] - SOURCE_DISTANCE_M, CORE[1] - SOURCE_DISTANCE_M, CORE[2] + SOURCE_DISTANCE_M, CORE[3] + SOURCE_DISTANCE_M)
SCENE_MARGIN_M = 200
RECEIVER_HEIGHT_M = 4.0  # above local ground: NoiseModelling's default reading of Z
SOURCE_HEIGHT_M = 0.05  # CNOSSOS-EU road source height above the road surface
YEAR = 2021
DEM_CELL_M = 10
GROUND_CELL_M = 10
PASS_RMSE_DB, PASS_BIAS_DB = 3.0, 1.0
CUTOFFS = {'Lden': 40, 'Lnight': 35}
# England's noise periods (Lday 07-19, Levening 19-23, Lnight 23-07).
PERIODS = {'D': range(7, 19), 'E': range(19, 23), 'N': [23, 0, 1, 2, 3, 4, 5, 6]}
# DfT AADF vehicle categories -> CNOSSOS-EU classes, and the TRA0307/TRA0308 time profile each follows.
CATEGORIES = {
    'cars_and_taxis': ('LV', 'Cars and Taxis'), 'LGVs': ('LV', 'Light Commercial Vehicles'),
    'buses_and_coaches': ('MV', 'All motor vehicles'), 'HGVs_2_rigid_axle': ('MV', 'Heavy Goods Vehicles'),
    'HGVs_3_rigid_axle': ('HGV', 'Heavy Goods Vehicles'), 'HGVs_4_or_more_rigid_axle': ('HGV', 'Heavy Goods Vehicles'),
    'HGVs_3_or_4_articulated_axle': ('HGV', 'Heavy Goods Vehicles'), 'HGVs_5_articulated_axle': ('HGV', 'Heavy Goods Vehicles'),
    'HGVs_6_articulated_axle': ('HGV', 'Heavy Goods Vehicles'), 'two_wheeled_motor_vehicles': ('WBV', 'All motor vehicles'),
}
CLASSES = ('LV', 'MV', 'HGV', 'WAV', 'WBV')
# DfT SPE0102 2021 free-flow speed table row -> speed per CNOSSOS class (published vehicle column).
SPEED_COLUMNS = {'LV': 'Cars', 'MV': 'Rigid Heavy Goods Vehicles 2 axles', 'HGV': 'All Artciulated Heavy Goods Vehicles',
                 'WAV': 'Motorcycles', 'WBV': 'Motorcycles'}
# ESA WorldCover class -> CNOSSOS ground factor G. Built-up and water are hard; vegetated ground is porous.
GROUND_G = {10: 1.0, 20: 1.0, 30: 1.0, 40: 1.0, 50: 0.0, 60: 0.5, 70: 0.0, 80: 0.0, 90: 1.0, 95: 1.0, 100: 1.0}
# Variant written as shared/ground_soft.shp: WorldCover built-up (50) mixes houses with gardens and lawns, so try it as soft.
GROUND_G_SOFT_BUILT = {**GROUND_G, 50: 1.0}
ACCESS_FUNCTIONS = {'Restricted Local Access Road', 'Local Access Road', 'Secondary Access Road'}
CASES = {
    'base': {'description': 'Counted major roads; minor roads at nearby DfT minor-road medians; 30 mph-road speeds off motorways',
             'minor_flow_factor': 1.0, 'a_road_speed_row': '30 mph speed limit roads'},
    'minor_half': {'description': 'As base, with every imputed minor-road flow halved', 'minor_flow_factor': 0.5,
                   'a_road_speed_row': '30 mph speed limit roads'},
    'a_roads_faster': {'description': 'As base, with A roads at national-speed-limit single-carriageway speeds',
                       'minor_flow_factor': 1.0, 'a_road_speed_row': 'National Speed Limit Single Carriageways'},
    'major_only': {'description': 'Diagnostic: only the counted MRDB major roads, no B, minor or access roads',
                   'minor_flow_factor': 1.0, 'a_road_speed_row': '30 mph speed limit roads', 'os_roads': False},
}
GROOVY = '''
import groovy.sql.Sql
import java.sql.Connection
import org.h2gis.api.ProgressVisitor
import org.noise_planet.noisemodelling.scripts.Import_and_Export.Import_File
import org.noise_planet.noisemodelling.scripts.Import_and_Export.Import_Asc_File
import org.noise_planet.noisemodelling.scripts.Geometric_Tools.Clean_Buildings_Table
import org.noise_planet.noisemodelling.scripts.NoiseModelling.Noise_level_from_traffic

title = 'Quiet UK Defra check'
description = 'Imports prepared inputs, runs Noise_level_from_traffic and writes receiver levels to CSV'
inputs = [:]
outputs = [result: [name: 'result', title: 'result', type: String.class]]

def exec(Connection connection, Map input, ProgressVisitor progress) {
    def sql = new Sql(connection)
    for (name in ['BUILDINGS', 'ROADS', 'RECEIVERS', 'GROUND', 'DEM', 'ATMOS', 'RECEIVERS_LEVEL']) sql.execute("DROP TABLE IF EXISTS " + name)
    def files = %(files)s
    files.each { table, path -> new Import_File().exec(connection, [pathFile: path, inputSRID: 27700, tableName: table], progress) }
    new Clean_Buildings_Table().exec(connection, [tableName: 'BUILDINGS'])
    new Import_Asc_File().exec(connection, [pathFile: %(dem)s, inputSRID: 27700])
    sql.execute("CREATE TABLE ATMOS(PERIOD VARCHAR PRIMARY KEY, WINDROSE DOUBLE PRECISION ARRAY[16], TEMPERATURE DOUBLE PRECISION, PRESSURE DOUBLE PRECISION, HUMIDITY DOUBLE PRECISION, GDISC BOOLEAN, PRIME2520 BOOLEAN)")
    %(atmos)s.each { period, p -> sql.execute("INSERT INTO ATMOS VALUES ('" + period + "', ARRAY[" + ([p] * 16).join(',') + "], 15, 101325, 70, true, false)") }
    new Noise_level_from_traffic().exec(connection, %(params)s + [tableBuilding: 'BUILDINGS', tableRoads: 'ROADS', tableReceivers: 'RECEIVERS',
        tableDEM: 'DEM', tableGroundAbs: 'GROUND', tablePeriodAtmosphericSettings: 'ATMOS'], progress)
    sql.execute("CALL CSVWRITE(" + %(result)s.inspect() + ", 'SELECT IDRECEIVER, PERIOD, LAEQ FROM RECEIVERS_LEVEL ORDER BY IDRECEIVER, PERIOD')")
    return [result: 'done']
}
'''
NOISE_PARAMS = {'confReflOrder': 1, 'confMaxSrcDist': float(SOURCE_DISTANCE_M), 'confMaxReflDist': 50.0,
                'confDiffHorizontal': True, 'confDiffVertical': False, 'paramWallAlpha': 0.1, 'confThreadNumber': 10,
                'confMaxError': 0.1, 'coefficientVersion': 2}
# Favourable-propagation occurrence per period, the usual CNOSSOS-EU defaults where no local statistics exist.
ATMOS = {'D': 0.5, 'E': 0.75, 'N': 1.0}


def now():
    return datetime.now(timezone.utc).isoformat()


def write_prj(path):
    Path(path).with_suffix('.prj').write_text(CRS.from_epsg(27700).to_wkt(WktVersion.WKT1_ESRI), encoding='ascii')


def gpkg_geometry(blob):
    envelope = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(blob[3] >> 1) & 0b111]
    return wkb.loads(blob[8 + envelope:])


def defra_grids():
    """Defra road Lden and Lnight rasters covering the test square, as (array, transform)."""
    records = ea.load_records(ACQUISITION)
    grids = {}
    for metric in CUTOFFS:
        for record in records:
            if record['source'] != 'road' or record['metric'] != metric or record['status'] != 'accepted':
                continue
            west, south = (int(v) for v in record['tile'][1:].split('N'))
            if west <= CORE[0] and CORE[2] <= west + 10000 and south <= CORE[1] and CORE[3] <= south + 10000:
                with ea.open_record(ACQUISITION, record) as memory, memory.open() as ds:
                    grids[metric] = (ds.read(1), ds.transform, record)
    if set(grids) != set(CUTOFFS):
        raise RuntimeError('Defra road rasters for the test square were not found in the sealed acquisition')
    return grids


def load_buildings():
    """Every footprint in the scene area: measured height where qualified, else the median as a declared fallback."""
    rows = list(csv.DictReader(open(SCENE / 'building_heights_bng.csv', encoding='utf-8')))
    measured = [float(r['provisional_height_agl_m']) for r in rows if r['provisional_height_agl_m']]
    fallback = statistics.median(measured)
    area = box(*SOURCES).buffer(SCENE_MARGIN_M)
    buildings = []
    for r in rows:
        geometry = make_valid(wkt.loads(r['geometry_wkt_bng']))
        if geometry.intersects(area):
            height = float(r['provisional_height_agl_m']) if r['provisional_height_agl_m'] else fallback
            buildings.append((geometry, max(height, 1.0), 'measured' if r['provisional_height_agl_m'] else 'median_fallback'))
    return buildings, fallback


def receivers(grids, buildings, sample):
    """Defra cell centres in the test square, 4 m above ground, outside every building footprint."""
    _, transform, _ = grids['Lden']
    tree = STRtree([b[0] for b in buildings])
    points, skipped = [], 0
    step, offset = 10 * sample, 10 * (sample // 2)
    for y in range(CORE[3] - 10 - offset, CORE[1] - 1, -step):
        for x in range(CORE[0] + offset, CORE[2], step):
            point = Point(x, y)
            if any(tree.geometries[i].contains(point) for i in tree.query(point)):
                skipped += 1
                continue
            row, col = rasterio.transform.rowcol(transform, x, y)
            centre = rasterio.transform.xy(transform, row, col)
            if abs(centre[0] - x) > 1e-6 or abs(centre[1] - y) > 1e-6:
                raise RuntimeError('Receiver is not at a Defra cell centre')
            values = {m: float(grids[m][0][row, col]) for m in CUTOFFS}
            points.append({'pk': len(points) + 1, 'x': float(x), 'y': float(y), **{f'defra_{m}': v for m, v in values.items()}})
    return points, skipped


def period_shares():
    """Share of daily traffic in each England noise period, per DfT TRA0307/TRA0308 vehicle group (2021, whole week)."""
    timing = json.loads(TIMING.read_text('utf-8'))['timing']
    shares = {}
    for table in ('tra0307', 'tra0308'):
        for row in timing[table]['rows']:
            shares.setdefault(row['vehicle_group'], {})[row['hour_start']] = sum(row['weekday_indices'].values())
    result = {}
    for group, hours in shares.items():
        if sorted(hours) != list(range(24)):
            raise RuntimeError(f'Incomplete 24-hour profile for {group}')
        total = sum(hours.values())
        result[group] = {p: sum(hours[h] for h in hs) / total for p, hs in PERIODS.items()}
    return result


def speed_table():
    rows = json.loads(TIMING.read_text('utf-8'))['free_flow_speeds']['rows']
    table = {}
    for row in rows:
        values = {k.split(' [')[0]: v for k, v in row['published_vehicle_values'].items()}
        table[row['road_type']] = {c: round(float(values[col]) * 1.609344, 1) for c, col in SPEED_COLUMNS.items()}
    return table


def aadf_rows():
    rows = [r for r in csv.DictReader(open(AADF, encoding='utf-8-sig')) if r['year'] == str(YEAR) and r['easting']]
    for r in rows:
        r['xy'] = (float(r['easting']), float(r['northing']))
        r['daily'] = {k: float(r[k] or 0) for k in CATEGORIES}
    return rows


def minor_profiles(rows):
    """Typical daily flow and vehicle mix from DfT's counted minor-road points within 25 km (2021)."""
    centre = ((CORE[0] + CORE[2]) / 2, (CORE[1] + CORE[3]) / 2)
    near = [r for r in rows if r['estimation_method'] == 'Counted' and math.dist(r['xy'], centre) <= 25000]
    profiles = {}
    for name, category, quantile in (('b_road', 'MB', 0.5), ('minor', 'MCU', 0.5), ('access', 'MCU', 0.25)):
        group = [r for r in near if r['road_category'] == category]
        totals = sorted(sum(r['daily'].values()) for r in group)
        mix = {k: sum(r['daily'][k] for r in group) for k in CATEGORIES}
        share = {k: v / sum(mix.values()) for k, v in mix.items()}
        flow = float(np.quantile(totals, quantile))
        profiles[name] = {'daily': {k: flow * s for k, s in share.items()}, 'flow': flow, 'count_points': len(group),
                          'basis': f'DfT {category} counted points within 25 km, {YEAR}, quantile {quantile}'}
    return profiles


def hourly(daily, shares):
    """Hourly flow per CNOSSOS class and period from daily flows per DfT category."""
    flows = {(c, p): 0.0 for c in CLASSES for p in PERIODS}
    for category, vehicles in daily.items():
        cls, group = CATEGORIES[category]
        for p, hours in PERIODS.items():
            flows[(cls, p)] += vehicles * shares[group][p] / len(hours)
    return flows


def load_roads(case, shares, speeds, rows, profiles):
    """Major roads from DfT MRDB with their own 2021 counts; every other road from OS Open Roads with typical flows."""
    area = box(*SOURCES)
    aadf = {r['count_point_id']: r for r in rows}
    roads = []
    reader = shapefile.Reader(str(MRDB))
    names = [f[0] for f in reader.fields[1:]]
    for item in reader.iterShapeRecords():
        line = LineString(item.shape.points)
        if line.is_empty or not line.intersects(area):
            continue
        record = dict(zip(names, item.record))
        count = aadf[str(int(float(record['CP_Number'])))]
        motorway = count['road_name'].startswith('M')
        roads.append({'geometry': line.intersection(area), 'flows': hourly(count['daily'], shares),
                      'speeds': speeds['Motorways' if motorway else case['a_road_speed_row']],
                      'kind': 'motorway' if motorway else 'a_road', 'name': count['road_name'],
                      'traffic': f"DfT {YEAR} AADF count point {count['count_point_id']} ({count['estimation_method']})"})
    if not case.get('os_roads', True):
        return roads
    b_points = [r for r in rows if r['road_name'].startswith('B')]
    connection = sqlite3.connect(f'file:{OS_ROADS}?mode=ro', uri=True)
    query = ('SELECT r.geometry, r.road_classification, r.road_function, r.road_classification_number FROM road_link r '
             'JOIN rtree_road_link_geometry i ON r.fid = i.id WHERE i.maxx >= ? AND i.minx <= ? AND i.maxy >= ? AND i.miny <= ?')
    for blob, classification, function, number in connection.execute(query, (SOURCES[0], SOURCES[2], SOURCES[1], SOURCES[3])):
        if classification in ('Motorway', 'A Road'):
            continue  # carried by the MRDB centrelines with their own counts
        line = gpkg_geometry(blob)
        if not line.intersects(area):
            continue
        if classification == 'B Road':
            same = [r for r in b_points if r['road_name'] == number and math.dist(r['xy'], line.centroid.coords[0]) <= 5000]
            if same:
                nearest = min(same, key=lambda r: math.dist(r['xy'], line.centroid.coords[0]))
                daily, traffic = nearest['daily'], f"DfT {YEAR} AADF count point {nearest['count_point_id']} on {number}"
            else:
                daily, traffic = profiles['b_road']['daily'], profiles['b_road']['basis']
            kind = 'b_road'
        else:
            kind = 'access' if function in ACCESS_FUNCTIONS else 'minor'
            daily = {k: v * case['minor_flow_factor'] for k, v in profiles[kind]['daily'].items()}
            traffic = profiles[kind]['basis'] + (f" x {case['minor_flow_factor']}" if case['minor_flow_factor'] != 1 else '')
        roads.append({'geometry': line.intersection(area), 'flows': hourly(daily, shares), 'speeds': speeds['30 mph speed limit roads'],
                      'kind': kind, 'name': number or '', 'traffic': traffic})
    connection.close()
    return roads


def write_points(path, points, z):
    with shapefile.Writer(str(path), shapeType=shapefile.POINTZ) as w:
        w.field('PK', 'N', 10, 0)
        for p in points:
            w.pointz(p['x'], p['y'], z)
            w.record(p['pk'])
    write_prj(path)


def write_polygons(path, items, fields):
    """items: (geometry, values) with values in the order of fields [(name, type, size, decimals)]."""
    with shapefile.Writer(str(path), shapeType=shapefile.POLYGON) as w:
        for name, kind, size, decimals in fields:
            w.field(name, kind, size, decimals)
        for geometry, values in items:
            parts = []
            for polygon in getattr(geometry, 'geoms', [geometry]):
                if polygon.geom_type != 'Polygon' or polygon.is_empty or polygon.area < 1:
                    continue
                polygon = orient(polygon, sign=-1.0)
                parts.append(list(polygon.exterior.coords))
                parts.extend(list(ring.coords) for ring in polygon.interiors)
            if parts:
                w.poly(parts)
                w.record(*values)
    write_prj(path)


def write_roads(path, roads):
    with shapefile.Writer(str(path), shapeType=shapefile.POLYLINEZ) as w:
        w.field('PK', 'N', 10, 0)
        for cls in CLASSES:
            for p in PERIODS:
                w.field(f'{cls}_{p}', 'N', 14, 4)
        for cls in CLASSES:
            for p in PERIODS:
                w.field(f'{cls}_SPD_{p}', 'N', 8, 2)
        w.field('PVMT', 'C', 8)
        w.field('KIND', 'C', 10)
        pk = 0
        for road in roads:
            for line in getattr(road['geometry'], 'geoms', [road['geometry']]):
                if line.geom_type != 'LineString' or line.length < 0.5:
                    continue
                pk += 1
                w.linez([[(x, y, SOURCE_HEIGHT_M) for x, y, *_ in line.coords]])
                w.record(pk, *[road['flows'][(c, p)] for c in CLASSES for p in PERIODS],
                         *[road['speeds'][c] for c in CLASSES for p in PERIODS], 'DEF', road['kind'])
    write_prj(path)
    return pk


def write_dem(path):
    """Bare terrain (EA 2 m DTM, 2022 composite) averaged to 10 m, as an ESRI ASCII grid."""
    west, south, east, north = (v + d for v, d in zip(SOURCES, (-SCENE_MARGIN_M, -SCENE_MARGIN_M, SCENE_MARGIN_M, SCENE_MARGIN_M)))
    with rasterio.open(SCENE / 'terrain_odn_2m.tif') as ds:
        window = rasterio.windows.from_bounds(west, south, east, north, ds.transform)
        data = ds.read(1, window=window, masked=True)
    factor = DEM_CELL_M // 2
    blocks = data.reshape(data.shape[0] // factor, factor, data.shape[1] // factor, factor)
    mean = blocks.mean(axis=(1, 3))
    if np.ma.count_masked(mean):
        raise RuntimeError('Terrain has gaps inside the scene area')
    with open(path, 'w', encoding='ascii', newline='\n') as f:
        f.write(f'ncols {mean.shape[1]}\nnrows {mean.shape[0]}\nxllcorner {west}\nyllcorner {south}\n'
                f'cellsize {DEM_CELL_M}\nNODATA_value -9999\n')
        for row in np.asarray(mean):
            f.write(' '.join(f'{v:.2f}' for v in row) + '\n')
    return mean.shape


def ground_polygons(g_map=GROUND_G):
    """ESA WorldCover 2021 resampled (nearest) to a 10 m BNG grid, mapped to G and polygonised."""
    west, south, east, north = (v + d for v, d in zip(SOURCES, (-SCENE_MARGIN_M, -SCENE_MARGIN_M, SCENE_MARGIN_M, SCENE_MARGIN_M)))
    width, height = int((east - west) / GROUND_CELL_M), int((north - south) / GROUND_CELL_M)
    transform = from_origin(west, north, GROUND_CELL_M, GROUND_CELL_M)
    classes = np.zeros((height, width), np.uint8)
    with rasterio.open(COVER) as ds:
        reproject(rasterio.band(ds, 1), classes, dst_transform=transform, dst_crs='EPSG:27700', resampling=Resampling.nearest)
    if (classes == 0).any():
        raise RuntimeError('Land cover does not cover the scene area')
    code = np.vectorize(lambda c: int(round(g_map[int(c)] * 10)))(classes).astype(np.uint8)
    return [(shape(g), (v / 10,)) for g, v in features.shapes(code, transform=transform)], np.bincount(classes.ravel(), minlength=101)


def prepare(output, sample):
    output.mkdir(parents=True, exist_ok=True)
    grids = defra_grids()
    buildings, fallback = load_buildings()
    points, inside_buildings = receivers(grids, buildings, sample)
    shared = output / 'shared'
    shared.mkdir(exist_ok=True)
    write_points(shared / 'receivers.shp', points, RECEIVER_HEIGHT_M)
    with open(shared / 'receivers.csv', 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(points[0]))
        writer.writeheader()
        writer.writerows(points)
    write_polygons(shared / 'buildings.shp', [(g, (h,)) for g, h, _ in buildings], [('HEIGHT', 'N', 8, 2)])
    dem_shape = write_dem(shared / 'dem.asc')
    ground, class_counts = ground_polygons()
    write_polygons(shared / 'ground.shp', ground, [('G', 'N', 4, 2)])
    ground_soft, _ = ground_polygons(GROUND_G_SOFT_BUILT)
    write_polygons(shared / 'ground_soft.shp', ground_soft, [('G', 'N', 4, 2)])
    shares, speeds, rows = period_shares(), speed_table(), aadf_rows()
    profiles = minor_profiles(rows)
    cases = {}
    for name, case in CASES.items():
        folder = output / name
        folder.mkdir(exist_ok=True)
        roads = load_roads(case, shares, speeds, rows, profiles)
        lines = write_roads(folder / 'roads.shp', roads)
        length = {}
        for road in roads:
            length[road['kind']] = length.get(road['kind'], 0) + road['geometry'].length / 1000
        cases[name] = {**case, 'road_lines': lines, 'road_km_by_kind': {k: round(v, 1) for k, v in length.items()},
                       'roads_sha256': file_hash(folder / 'roads.shp'), 'roads_dbf_sha256': file_hash(folder / 'roads.dbf')}
    manifest = {
        'schema': 'quiet-uk-noisemodelling-check', 'created_at_utc': now(), 'sample_every_nth_cell': sample,
        'test_square_bng': CORE, 'source_area_bng': SOURCES, 'receiver_height_m': RECEIVER_HEIGHT_M, 'source_height_m': SOURCE_HEIGHT_M,
        'receivers': len(points), 'receivers_skipped_inside_buildings': inside_buildings,
        'defra_records': {m: {k: grids[m][2][k] for k in ('tile', 'path', 'sha256')} for m in CUTOFFS},
        'pass_bar': {'rmse_db': PASS_RMSE_DB, 'abs_bias_db': PASS_BIAS_DB, 'over': 'cells where Defra publishes a value',
                     'set': 'before any result was seen'},
        'buildings': {'count': len(buildings), 'measured': sum(b[2] == 'measured' for b in buildings),
                      'median_fallback_height_m': round(fallback, 2), 'source': 'OS OpenMap Local 2026-04 footprints, EA 2 m LiDAR DSM-DTM median'},
        'dem': {'cell_m': DEM_CELL_M, 'shape': dem_shape, 'source': 'EA 2 m DTM 2022 composite, block mean'},
        'ground': {'cell_m': GROUND_CELL_M, 'polygons': len(ground), 'g_by_worldcover_class': GROUND_G, 'soft_built_variant': {'file': 'ground_soft.shp', 'g_by_worldcover_class': GROUND_G_SOFT_BUILT},
                   'class_pixels': {str(c): int(n) for c, n in enumerate(class_counts) if n}},
        'traffic': {'year': YEAR, 'period_shares': shares, 'minor_profiles': {k: {kk: vv for kk, vv in v.items() if kk != 'daily'}
                    for k, v in profiles.items()}, 'speeds_kmh': speeds, 'pavement': 'DEF (CNOSSOS reference surface)'},
        'noisemodelling': {'version': '6.0.0', 'params': NOISE_PARAMS, 'favourable_occurrence': ATMOS,
                           'temperature_c': 15, 'humidity_pct': 70},
        'cases': cases,
        'inputs_sha256': {str(p.relative_to(ROOT)): file_hash(p) for p in (AADF, MRDB, OS_ROADS, COVER, TIMING,
                          SCENE / 'building_heights_bng.csv', SCENE / 'terrain_odn_2m.tif')},
        'shared_sha256': {p.name: file_hash(p) for p in sorted(shared.iterdir())},
    }
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    print(json.dumps({k: manifest[k] for k in ('receivers', 'receivers_skipped_inside_buildings', 'buildings', 'cases')}, indent=1, default=str))


def run(output, names, overrides=None, label=None, atmos=None, ground='ground.shp'):
    """Run each case; overrides change NoiseModelling settings, and label keeps those results in their own subfolder."""
    shared = output / 'shared'
    params = {**NOISE_PARAMS, **(overrides or {})}
    atmos = atmos or ATMOS
    for name in names:
        folder = output / name / label if label else output / name
        folder.mkdir(exist_ok=True)
        files = {'RECEIVERS': shared / 'receivers.shp', 'BUILDINGS': shared / 'buildings.shp',
                 'GROUND': shared / ground, 'ROADS': output / name / 'roads.shp'}
        groovy = GROOVY % {
            'files': '[' + ', '.join(f'{k}: {str(v.as_posix())!r}' for k, v in files.items()) + ']',
            'dem': repr((shared / 'dem.asc').as_posix()), 'atmos': '[' + ', '.join(f'{k}: {v}' for k, v in atmos.items()) + ']',
            'params': '[' + ', '.join(f'{k}: {str(v).lower() if isinstance(v, bool) else v}' for k, v in params.items()) + ']',
            'result': repr((folder / 'receiver_levels.csv').as_posix())}
        (folder / 'run.groovy').write_text(groovy, encoding='utf-8')
        work = folder / 'db'
        work.mkdir(exist_ok=True)
        start = time.time()
        with open(folder / 'run.log', 'w', encoding='utf-8') as log:
            done = subprocess.run([str(NOISEMODELLING / 'bin' / 'ScriptRunner.bat'), '-w', str(work), '-s', str(folder / 'run.groovy')],
                                  cwd=NOISEMODELLING, env={**os.environ, 'JAVACMD': str(JAVA)}, stdout=log, stderr=subprocess.STDOUT)
        seconds = time.time() - start
        ok = done.returncode == 0 and (folder / 'receiver_levels.csv').exists()
        (folder / 'run.json').write_bytes(json_bytes({'case': name, 'label': label, 'params': params, 'favourable_occurrence': atmos,
                                                       'ground_file': ground, 'ground_sha256': file_hash(shared / ground), 'ok': ok, 'seconds': round(seconds, 1), 'finished_at_utc': now(),
                                                       'java': str(JAVA.relative_to(ROOT.parent)), 'returncode': done.returncode}))
        print(f'{name}: {"finished" if ok else "FAILED"} in {seconds / 60:.1f} min', flush=True)


def stats(diff):
    return {'cells': int(diff.size), 'bias_db': round(float(diff.mean()), 2), 'rmse_db': round(float(np.sqrt((diff ** 2).mean())), 2),
            'mae_db': round(float(np.abs(diff).mean()), 2), 'within_3_db': round(float((np.abs(diff) <= 3).mean()), 3),
            'within_5_db': round(float((np.abs(diff) <= 5).mean()), 3),
            'p5_p95_db': [round(float(v), 2) for v in np.percentile(diff, [5, 95])]}


def compare(output):
    manifest = json.loads((output / 'manifest.json').read_text('utf-8'))
    points = {int(p['pk']): p for p in csv.DictReader(open(output / 'shared' / 'receivers.csv', encoding='utf-8'))}
    report = {'schema': 'quiet-uk-noisemodelling-check-report', 'created_at_utc': now(), 'pass_bar': manifest['pass_bar'], 'cases': {}}
    folders = [f for name in CASES for f in (output / name, *sorted((output / name).glob('*/'))) if (f / 'receiver_levels.csv').exists()]
    for folder in folders:
        name = folder.relative_to(output).as_posix()
        result = folder / 'receiver_levels.csv'
        levels = {}
        for row in csv.DictReader(open(result, encoding='utf-8')):
            levels.setdefault(int(row['IDRECEIVER']), {})[row['PERIOD']] = float(row['LAEQ'])
        case = {'run': json.loads((folder / 'run.json').read_text('utf-8'))}
        for metric, period in (('Lden', 'DEN'), ('Lnight', 'N')):
            ours = np.array([levels[pk][period] for pk in points if pk in levels])
            defra = np.array([float(points[pk][f'defra_{metric}']) for pk in points if pk in levels])
            published, under = defra > 0, defra == 0  # Defra: 0 = below the cutoff, -96 = no data
            diff = ours[published] - defra[published]
            bands = {}
            for low, high in ((0, 50), (50, 55), (55, 60), (60, 65), (65, 70), (70, 200)):
                inside = published & (defra >= low) & (defra < high)
                if inside.sum() >= 20:
                    bands[f'{low}-{high}'] = stats(ours[inside] - defra[inside])
            cutoff = CUTOFFS[metric]
            case[metric] = {**stats(diff), 'by_defra_level': bands, 'receivers_with_results': int(ours.size),
                            'defra_no_data_cells': int((defra == -96).sum()), 'defra_under_cutoff_cells': int(under.sum()),
                            'ours_under_cutoff_where_defra_under': int(((ours < cutoff) & under).sum()),
                            'ours_under_cutoff_where_defra_published': int(((ours < cutoff) & published).sum())}
        case['passes'] = all(abs(case[m]['bias_db']) <= PASS_BIAS_DB and case[m]['rmse_db'] <= PASS_RMSE_DB for m in CUTOFFS)
        report['cases'][name] = case
        print(name, {m: {k: case[m][k] for k in ('cells', 'bias_db', 'rmse_db', 'within_3_db')} for m in CUTOFFS}, 'PASS' if case['passes'] else 'FAIL')
    (output / 'report.json').write_bytes(json_bytes(report))


def parse_setting(item):
    name, value = item.split('=', 1)
    if name not in NOISE_PARAMS:
        raise SystemExit(f'Unknown setting {name}')
    if value.lower() in ('true', 'false'):
        return name, value.lower() == 'true'
    return name, type(NOISE_PARAMS[name])(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/noisemodelling_check_v1')
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', nargs='*', metavar='CASE')
    parser.add_argument('--compare', action='store_true')
    parser.add_argument('--set', nargs='*', default=[], metavar='NAME=VALUE', help='override a NoiseModelling setting for this run')
    parser.add_argument('--label', help='keep this run in <case>/<label>')
    parser.add_argument('--favourable', help='favourable-propagation occurrence per period, e.g. D=0,E=0,N=0')
    parser.add_argument('--ground-file', default='ground.shp', help='ground-type shapefile in shared/ to use')
    parser.add_argument('--sample', type=int, default=1, help='use every Nth Defra cell along each axis')
    args = parser.parse_args()
    args.output = args.output.resolve()  # NoiseModelling runs from its own folder
    if args.prepare:
        prepare(args.output, args.sample)
    if args.run is not None:
        atmos = {k: float(v) for k, v in (item.split('=') for item in args.favourable.split(','))} if args.favourable else None
        if atmos is not None and sorted(atmos) != sorted(ATMOS):
            raise SystemExit('--favourable needs D, E and N')
        run(args.output, args.run or list(CASES), dict(parse_setting(item) for item in args.set), args.label, atmos, args.ground_file)
    if args.compare:
        compare(args.output)


if __name__ == '__main__':
    main()
