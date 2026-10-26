"""Native physical-scene checks and cautious building surface-height summaries.

DSM minus DTM is a surface above terrain, including trees and structures. A
footprint and an inward buffer reduce edge contamination but cannot establish
an exact roof, eaves or acoustic obstacle height. No ground/height gaps are filled.
"""
from datetime import datetime, timezone

import numpy as np
from rasterio.crs import CRS
from rasterio.features import rasterize
from rasterio.transform import from_origin
from shapely.geometry import shape

AMBIGUOUS_SURVEY = 65535
HEIGHT_RULES = {'inward_buffer_m': 2.0, 'minimum_pixels': 4,
                'minimum_valid_fraction': 0.95, 'minimum_paired_fraction': 0.95,
                'minimum_nonnegative_fraction': 0.95, 'minimum_median_surface_m': 2.0}


def assert_native_grid(raster, bounds, resolution=2):
    west, south, east, north = bounds
    width, height = (east - west) / resolution, (north - south) / resolution
    if width != int(width) or height != int(height):
        raise ValueError('Bounds do not form a complete native grid')
    if (raster.crs != CRS.from_epsg(27700) or raster.count != 1
            or raster.shape != (int(height), int(width)) or tuple(raster.bounds) != tuple(bounds)
            or raster.transform != from_origin(west, north, resolution, resolution)):
        raise ValueError('Native grid mismatch: resampling or shifting is not permitted')


def valid_elevations(values, masks):
    """Zero and below-datum terrain elevations remain valid."""
    return (np.asarray(masks) > 0) & np.isfinite(values)


def surface_difference(dtm, dsm, dtm_mask, dsm_mask):
    if dtm.shape != dsm.shape or dtm.shape != dtm_mask.shape or dtm.shape != dsm_mask.shape:
        raise ValueError('Surface/terrain array dimensions differ')
    valid = valid_elevations(dtm, dtm_mask) & valid_elevations(dsm, dsm_mask)
    result = np.full(dtm.shape, np.nan, dtype=np.float32)
    result[valid] = dsm[valid] - dtm[valid]
    # Negative differences are diagnostics, never silently clamped to zero.
    return result, valid


def survey_key(properties):
    fields = ('polygon_id', 'sd_flown', 'ed_flown', 'resolution', 'comp_year', 'lidar_rtn')
    if any(properties.get(field) is None for field in fields):
        raise ValueError('Incomplete survey identity')
    start, end = properties['sd_flown'], properties['ed_flown']
    if start > end or str(properties['comp_year']) != '2022':
        raise ValueError('Unexpected survey dates/composite')
    return tuple(properties[field] for field in fields)


def qualify_surveys(dtm_document, dsm_document):
    """Share integer codes only for matching survey identity; retain both geometries."""
    sources = []
    for document in (dtm_document, dsm_document):
        if document.get('crs', {}).get('properties', {}).get('name') not in ('EPSG:27700', 'urn:ogc:def:crs:EPSG::27700'):
            raise ValueError('Survey index must declare British National Grid')
        if document.get('exceededTransferLimit'):
            raise ValueError('Truncated survey index')
        rows = {}
        for feature in document['features']:
            key = survey_key(feature['properties'])
            geometry = shape(feature['geometry'])
            if key in rows or geometry.is_empty or not geometry.is_valid:
                raise ValueError('Duplicate/invalid survey feature')
            rows[key] = (feature['properties'], geometry)
        sources.append(rows)
    keys = sorted(set(sources[0]) | set(sources[1]))
    if not keys or len(keys) >= AMBIGUOUS_SURVEY:
        raise ValueError('Unexpected survey index size')
    records, geometries = [], [[], []]
    for code, key in enumerate(keys, 1):
        available = [key in source for source in sources]
        p = next(source[key][0] for source in sources if key in source)
        dates = [datetime.fromtimestamp(p[field] / 1000, timezone.utc).date().isoformat()
                 for field in ('sd_flown', 'ed_flown')]
        records.append({'code': code, 'polygon_id': p['polygon_id'], 'survey_start': dates[0],
                        'survey_end': dates[1], 'provider_year_field': p.get('year'),
                        'source_resolution_m': p['resolution'], 'composite_year': p['comp_year'],
                        'return': p['lidar_rtn'], 'present_in_dtm_and_dsm': all(available),
                        'whole_index_geometry_equal': (sources[0][key][1].equals(sources[1][key][1])
                                                       if all(available) else None)})
        for index, source in enumerate(sources):
            if key in source:
                geometries[index].append((source[key][1], code))
    return records, geometries


def survey_grid(geometries, shape_, transform, records=None):
    """Pixel-centre support with optional conservative provider-rule date inference.

    EA says newest/best-resolution surveys are used. We infer a winner only if
    every competing survey has the same source resolution and ended before the
    winner began. Overlapping date ranges, ties and differing resolutions stay
    ambiguous. This is inference from the provider rule, not a pixel source ID.
    """
    codes = np.zeros(shape_, dtype=np.uint16)
    masks = {code: rasterize([(geometry, 1)], out_shape=shape_, transform=transform,
                             fill=0, dtype='uint8', all_touched=False).astype(bool)
             for geometry, code in geometries}
    for mask in masks.values():
        codes[mask] = AMBIGUOUS_SURVEY
    lookup = {row['code']: row for row in records} if records is not None else {}
    for code, mask in masks.items():
        winner = mask.copy()
        for other, competing in masks.items():
            if other == code:
                continue
            newer_equal_resolution = (records is not None
                and lookup[code]['source_resolution_m'] == lookup[other]['source_resolution_m']
                and lookup[code]['survey_start'] > lookup[other]['survey_end'])
            if not newer_equal_resolution:
                winner &= ~competing
        codes[winner] = code
    return codes


def paired_survey_mask(dtm_codes, dsm_codes):
    if dtm_codes.shape != dsm_codes.shape:
        raise ValueError('Survey grids differ')
    return (dtm_codes == dsm_codes) & (dtm_codes > 0) & (dtm_codes != AMBIGUOUS_SURVEY)


def building_surface_summary(geometry, terrain, surface, terrain_mask, surface_mask,
                             dtm_codes, dsm_codes, transform, fully_inside=True,
                             rules=None):
    rules = HEIGHT_RULES if rules is None else rules
    result = {'status': 'withheld', 'withheld_reason': None,
              'provisional_height_agl_m': None, 'sample_p10_m': None,
              'sample_median_m': None, 'sample_p90_m': None, 'sample_min_m': None,
              'sample_max_m': None, 'interior_pixels': 0, 'valid_pixels': 0,
              'paired_pixels': 0, 'valid_fraction': None, 'paired_fraction': None,
              'nonnegative_fraction': None, 'survey_codes': [],
              'historical_footprint_compatibility': 'unestablished',
              'height_definition': 'median last-return surface minus bare terrain within buffered generalised footprint',
              'acoustic_obstacle_height_m': None}
    if geometry.is_empty or not geometry.is_valid or geometry.geom_type not in ('Polygon', 'MultiPolygon'):
        result['withheld_reason'] = 'invalid_geometry'
        return result
    if not fully_inside:
        result['withheld_reason'] = 'footprint_crosses_extract_boundary'
        return result
    interior = geometry.buffer(-rules['inward_buffer_m'])
    if interior.is_empty:
        result['withheld_reason'] = 'no_buffered_interior'
        return result
    selected = rasterize([(interior, 1)], out_shape=terrain.shape, transform=transform,
                         fill=0, dtype='uint8', all_touched=False).astype(bool)
    difference, valid = surface_difference(terrain, surface, terrain_mask, surface_mask)
    if dtm_codes.shape != terrain.shape or dsm_codes.shape != terrain.shape:
        raise ValueError('Survey/elevation dimensions differ')
    paired = selected & valid & paired_survey_mask(dtm_codes, dsm_codes)
    total, numeric, matched = (int(np.count_nonzero(m)) for m in
                               (selected, selected & valid, paired))
    result.update(interior_pixels=total, valid_pixels=numeric, paired_pixels=matched,
                  valid_fraction=numeric / total if total else None,
                  paired_fraction=matched / total if total else None,
                  survey_codes=sorted(int(c) for c in np.unique(dtm_codes[paired])))
    if matched:
        values = difference[paired]
        p10, median, p90 = np.percentile(values, [10, 50, 90])
        result.update(sample_p10_m=float(p10), sample_median_m=float(median), sample_p90_m=float(p90),
                      sample_min_m=float(values.min()), sample_max_m=float(values.max()),
                      nonnegative_fraction=float(np.count_nonzero(values >= 0) / matched))
    if total < rules['minimum_pixels']:
        reason = 'too_few_interior_pixels'
    elif result['valid_fraction'] < rules['minimum_valid_fraction']:
        reason = 'incomplete_elevation_support'
    elif result['paired_fraction'] < rules['minimum_paired_fraction']:
        reason = 'incomplete_paired_survey_support'
    elif matched < rules['minimum_pixels']:
        reason = 'too_few_paired_pixels'
    elif len(result['survey_codes']) != 1:
        reason = 'mixed_survey_dates'
    elif result['nonnegative_fraction'] < rules['minimum_nonnegative_fraction']:
        reason = 'negative_surface_differences'
    elif result['sample_median_m'] < rules['minimum_median_surface_m']:
        reason = 'no_clear_elevated_surface'
    else:
        reason = None
    result['withheld_reason'] = reason
    if reason is None:
        result['status'] = 'provisional_surface_height'
        result['provisional_height_agl_m'] = result['sample_median_m']
    return result
