"""Land-cover evidence and barrier candidates, never measured acoustic inputs.

WorldCover v200/PUM table 3 defines land cover, not acoustic impedance.
Candidate G analogies refer to the retained JRC 2012 ground descriptions.
"""
import math
import re

from rasterio.windows import Window

CLASSES = {10: 'Tree cover', 20: 'Shrubland', 30: 'Grassland', 40: 'Cropland',
           50: 'Built-up', 60: 'Bare / sparse vegetation', 70: 'Snow and ice',
           80: 'Permanent water bodies', 90: 'Herbaceous wetland', 95: 'Mangroves',
           100: 'Moss and lichen'}


def crosswalk():
    """Explicit surface analogies, with all observed G assignments withheld."""
    conditions = {
        30: (1., 'Underlying soil must be acoustically porous; grass cover alone does not establish this.'),
        40: (1., 'Underlying cultivated soil must be acoustically porous; seasonal compaction/wetting is unobserved.'),
        50: (0., 'Path must lie on a sealed hard surface; a mixed built-up pixel or roof is not a ground surface.'),
        80: (0., 'Path must lie over the actual water surface; banks, seasonal exposure and mixed pixels are unqualified.'),
    }
    return {str(code): dict(class_name=name, observed_ground_g=None,
                conditional_analogy_g=conditions.get(code, (None, None))[0],
                condition=conditions.get(code, (None, 'Class does not determine acoustic ground. Canopy, substrate and wetting/compaction remain unobserved.'))[1],
                sensitivity_cases_g=[0., .3, .7, 1.],
                sensitivity_meaning='Declared stress cases, not probabilities, a confidence interval or validated error bound.')
            for code, name in CLASSES.items()}


def validate_native(dataset):
    t = dataset.transform
    if (dataset.crs.to_epsg() != 4326 or dataset.count != 1 or dataset.dtypes != ('uint8',)
            or dataset.nodata != 0 or t.b != 0 or t.d != 0 or t.a != 1 / 12000 or t.e != -1 / 12000):
        raise ValueError('Expected original categorical WorldCover v200 native grid')


def native_window(transform, width, height, envelope):
    """Smallest integer native window containing a WGS84 envelope; no resampling."""
    west, south, east, north = envelope
    if not all(math.isfinite(v) for v in envelope) or west >= east or south >= north:
        raise ValueError('Invalid envelope')
    if transform.b or transform.d or transform.a <= 0 or transform.e >= 0:
        raise ValueError('Expected north-up native grid')
    left = math.floor((west - transform.c) / transform.a)
    right = math.ceil((east - transform.c) / transform.a)
    top = math.floor((north - transform.f) / transform.e)
    bottom = math.ceil((south - transform.f) / transform.e)
    if left < 0 or top < 0 or right > width or bottom > height:
        raise ValueError('Envelope extends outside acquired native tile')
    return Window(left, top, right - left, bottom - top)


def sample_class(values, mask, transform, longitude, latitude):
    """Native containing cell, west/north included, east/south excluded.

    Class zero means provider nodata, not an acoustic G of zero.
    """
    if (not math.isfinite(longitude) or not math.isfinite(latitude)
            or not transform.c <= longitude < transform.c + values.shape[1] * transform.a
            or not transform.f + values.shape[0] * transform.e < latitude <= transform.f):
        return dict(row=None, column=None, code=None, state='outside')
    column = math.floor((longitude - transform.c) / transform.a)
    row = math.floor((latitude - transform.f) / transform.e)
    if row < 0 or column < 0 or row >= values.shape[0] or column >= values.shape[1]:
        return dict(row=None, column=None, code=None, state='outside')
    code = int(values[row, column])
    state = 'nodata' if not mask[row, column] or code == 0 else 'reported_class' if code in CLASSES else 'unsupported_class'
    return dict(row=row, column=column, code=code, state=state)


def midpoint_samples(line, maximum_step_m):
    """Equal length diagnostic intervals; avoid duplicated endpoint weights."""
    if not math.isfinite(maximum_step_m) or maximum_step_m <= 0 or line.length <= 0:
        raise ValueError('Positive step and nonempty line required')
    n = math.ceil(line.length / maximum_step_m)
    weight = line.length / n
    for i in range(n):
        chainage = (i + .5) * weight
        p = line.interpolate(chainage)
        yield chainage, weight, p.x, p.y


def conditional_ground(lengths):
    """Full-length denominator, with ambiguous/missing classes held, never dropped."""
    if not lengths or any(not math.isfinite(v) or v < 0 for v in lengths.values()) or sum(lengths.values()) <= 0:
        raise ValueError('Positive finite path evidence required')
    analogies = {30: 1., 40: 1., 50: 0., 80: 0.}
    total = math.fsum(lengths.values())
    unknown = math.fsum(v for code, v in lengths.items() if code not in analogies)
    weighted = math.fsum(analogies.get(code, 0.) * v for code, v in lengths.items())
    return dict(observed_ground_g=None, conditional_analogy_g=weighted / total if unknown == 0 else None,
                ambiguous_or_missing_length_m=unknown, path_length_m=total,
                status='conditional_surface_analogy_only' if unknown == 0 else 'withheld_ambiguous_or_missing_class')


def parse_height(raw):
    """Only an explicit positive metre height; unhandled units/ranges stay unknown."""
    if raw is None or not str(raw).strip():
        return dict(tagged_height_m=None, height_state='missing')
    match = re.fullmatch(r'(\d+(?:\.\d+)?|\.\d+)\s*(?:m)?', str(raw).strip())
    if not match:
        return dict(tagged_height_m=None, height_state='ambiguous_or_unhandled_units')
    value = float(match[1])
    if not math.isfinite(value) or value <= 0:
        return dict(tagged_height_m=None, height_state='nonpositive_or_nonfinite')
    return dict(tagged_height_m=value, height_state='explicit_metres_unverified')


def barrier_candidate(tags):
    """No ordinary fence, hedge or material tag becomes an opaque acoustic screen."""
    barrier = tags.get('barrier')
    if tags.get('wall') == 'noise_barrier':
        category = 'explicit_noise_barrier_candidate'
    elif barrier in ('wall', 'retaining_wall'):
        category = 'generic_wall_candidate'
    elif barrier in ('fence', 'wire_fence'):
        category = 'generic_fence_candidate'
    elif tags.get('man_made') == 'embankment':
        category = 'embankment_candidate'
    else:
        category = 'other_barrier_tag_no_acoustic_classification'
    return dict(category=category, **parse_height(tags.get('height')),
                tagged_material=tags.get('material'), acoustic_screen_height_m=None,
                acoustic_top_elevation_odn_m=None, acoustic_absorption=None, opaque_and_sealed=None,
                acoustic_status='withheld: presence, geometry, height/datum, sealing and acoustic properties unqualified')


def snapshot_consistency(start, end):
    """Edit snapshots bracket the year; matching content is not observed persistence."""
    if start is None:
        return 'not_in_start_snapshot; creation/presence date unestablished'
    if end is None:
        return 'not_in_end_snapshot; deletion/physical absence unestablished'
    if start['geometry'] == end['geometry'] and start['tags'] == end['tags']:
        return 'matching_geometry_and_tags_at_both_snapshots; physical persistence unestablished'
    return 'geometry_or_tags_differ_at_snapshots; physical change date unestablished'
