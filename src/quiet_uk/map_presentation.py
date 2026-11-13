"""How the England map is coloured and described to people. A display layer only.

The stored tiles keep the palette they were built and verified with
(``manifest['display']['palette_rgba']``). When a tile is served, its PLTE and tRNS
chunks are swapped for one of the themes below. The pixel data (IDAT), and so the
class of every cell, is untouched. Point values and evidence never come from here.

Palette entries match the indices written by ``england_map.encode``:
0 outside the downloaded rasters, 1-2 no data (hatched), 3 below the published
cutoff, 4-11 the eight value bands (``source_pilot.BREAKS``).

Looks (``display-v3-looks``), each with its own basemap treatment in the page:

* ``night`` (Night glow): a magma-like ramp on a near-black map. Quiet recedes into
  the dark and loud corridors glow, from deep purple through ember to pale gold.
* ``atlas`` (Editorial atlas): an ink ramp from sand to oxblood on warm paper.
* ``guide`` (Calm guide): colours split at the WHO 2018 guideline for the layer:
  pale teal for bands wholly under it, sand for the band that contains it, amber to
  red for bands wholly over it. The WHO gives no daytime-only value, so daytime
  layers use one warm ramp with no split.
* ``light`` and ``dark``: the earlier single-violet ramps, kept for older pages.
* ``lens``: five steps for the quiet-area hexagons (share of land under the cutoff,
  0-20% ... 80-100%), a green family so they never read as noise levels.
* ``index``: a hidden palette for the page's pointer readout: entry i is red i*20,
  fully opaque, so a pixel's class can be read back exactly.

Every sequential ramp gets steadily darker (light looks) or lighter (night) from
quiet to loud; tests/test_england_overview.py checks the order in OKLab.
"""
import struct
import zlib

from .source_pilot import BREAKS, METRICS, PROVIDERS

VERSION = 'display-v3-looks'
LAYERS = tuple(f'{source}-{metric}' for source in PROVIDERS for metric in METRICS)
ALPHA = {'no_data': (60, 130), 'below': 180, 'band': 235}
THEMES = {
    'night': {
        'label': 'Night glow', 'basemap': 'dark',
        'bands': ['#341552', '#4b1763', '#701f6f', '#9a2c6c', '#c4405d', '#e6644b', '#f8973f', '#fdd57a'],
        'below': '#1b1630', 'no_data': '#5d6472',
        'alpha': {'no_data': (45, 100), 'below': 110, 'bands': [170, 200, 215, 230, 240, 245, 250, 255]},
        'lens': ['#2a1f3a', '#26384a', '#245a5e', '#2f8277', '#5fb79b'],
    },
    'atlas': {
        'label': 'Editorial atlas', 'basemap': 'light',
        'bands': ['#ead6c3', '#e2c8b2', '#d3a68a', '#c28066', '#ad5a4a', '#8f3a37', '#6b2230', '#481427'],
        'below': '#efe4d3', 'no_data': '#9b9183',
        'alpha': {'no_data': (55, 120), 'below': 110, 'bands': [235] * 8},
        'lens': ['#efe6d6', '#dcdcc2', '#b9c7a3', '#8aa985', '#5b8a6b'],
    },
    'guide': {
        'label': 'Calm guide', 'basemap': 'light',
        'cool': ['#d7ece7', '#b5dcd3', '#8fc8bb'],  # Far under the guideline -> just under it.
        'neutral': '#e4d6b4',
        'warm': ['#f4c06e', '#eea04a', '#e07a34', '#c9572a', '#a63a21', '#7c2617', '#561a10'],  # Just over -> far over.
        'day': ['#efe6d2', '#efd6a4', '#f0bf79', '#eda04f', '#df7c36', '#c55b28', '#9f3f20', '#752a17'],
        'below': '#d7ece7', 'no_data': '#8d99a6',
        'alpha': {'no_data': (60, 130), 'below': 80, 'bands': [235] * 8},
        'lens': ['#f2eee4', '#d6ebe4', '#a8d6ca', '#6fbcab', '#3a9985'],
    },
    'light': {
        'label': 'Violet (light)', 'basemap': 'light', 'legacy': True,
        'bands': ['#b5a3da', '#a48ad4', '#9371cb', '#825abc', '#7044ac', '#5d3295', '#4a237a', '#37165e'],
        'below': '#e8e0f9', 'no_data': '#9aa5b4',
    },
    'dark': {
        'label': 'Violet (dark)', 'basemap': 'dark', 'legacy': True,
        'bands': ['#60438d', '#7555a8', '#8a69be', '#9e7fd3', '#b397e5', '#c7aff5', '#dbcafe', '#eee7ff'],
        'below': '#322843', 'no_data': '#6b7583',
    },
    'index': {'label': 'Class index', 'hidden': True},
}
# The value bands as [low, high) in dB; None is open-ended.
BAND_RANGES = list(zip([None, *BREAKS], [*BREAKS, None]))

# Plain words for each band. They describe the level, not health effects; the
# WHO guideline values below carry that comparison separately.
WORDS = [
    (None, 40, 'Very quiet'), (40, 45, 'Quiet'), (45, 50, 'Fairly quiet'), (50, 55, 'Moderate'),
    (55, 60, 'Noticeable'), (60, 65, 'Loud'), (65, 70, 'Very loud'), (70, None, 'Extremely loud'),
]
# WHO Environmental Noise Guidelines for the European Region (2018): levels above
# which adverse health effects are linked with each source. No daytime-only value.
WHO_2018 = {
    'url': 'https://www.who.int/europe/publications/i/item/9789289053563',
    'road': {'Lden': 53, 'Lnight': 45},
    'rail': {'Lden': 54, 'Lnight': 44},
    'aircraft': {'Lden': 45, 'Lnight': 40},
}


def rgb(colour):
    return tuple(int(colour[i:i+2], 16) for i in (1, 3, 5))


def guide_bands(layer):
    """Calm guide colours for one layer, split at its WHO guideline."""
    t = THEMES['guide']
    source, metric = layer.split('-')
    limit = WHO_2018[source].get(metric)
    if limit is None:
        return list(t['day'])
    under = [i for i, (low, high) in enumerate(BAND_RANGES) if high is not None and high <= limit]
    over = [i for i, (low, high) in enumerate(BAND_RANGES) if low is not None and low >= limit]
    bands = []
    for i in range(len(BAND_RANGES)):
        if i in under:
            bands.append(t['cool'][max(0, len(t['cool']) - len(under) + under.index(i))])
        elif i in over:
            bands.append(t['warm'][min(over.index(i), len(t['warm']) - 1)])
        else:
            bands.append(t['neutral'])
    return bands


def colours(theme, layer=None):
    """Band, below-cutoff and no-data colours of a look (per layer for the Calm guide)."""
    t = THEMES[theme]
    if theme == 'guide':
        if layer not in LAYERS:
            raise KeyError('The Calm guide colours depend on the map layer')
        return {'bands': guide_bands(layer), 'below': t['below'], 'no_data': t['no_data']}
    return {'bands': list(t['bands']), 'below': t['below'], 'no_data': t['no_data']}


def palette(theme, layer=None):
    """Twelve RGBA entries in the stored tiles' index order."""
    if theme == 'index':
        return [(i * 20, 0, 0, 255) for i in range(12)]
    c, alpha = colours(theme, layer), THEMES[theme].get('alpha')
    if alpha is None:
        alpha = {'no_data': ALPHA['no_data'], 'below': ALPHA['below'], 'bands': [ALPHA['band']] * 8}
    return ([(0, 0, 0, 0), (*rgb(c['no_data']), alpha['no_data'][0]), (*rgb(c['no_data']), alpha['no_data'][1]),
             (*rgb(c['below']), alpha['below'])] + [(*rgb(b), a) for b, a in zip(c['bands'], alpha['bands'])])


def _chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


_REPLACEMENTS = {}


def replacements(theme, layer=None):
    key = (theme, layer if theme == 'guide' else None)
    if key not in _REPLACEMENTS:
        entries = palette(theme, layer)
        _REPLACEMENTS[key] = {b'PLTE': bytes(v for c in entries for v in c[:3]), b'tRNS': bytes(c[3] for c in entries)}
    return _REPLACEMENTS[key]


def recolour(png, theme, layer=None):
    """The same indexed PNG with this theme's palette; every other chunk is copied byte for byte."""
    if theme not in THEMES:
        raise KeyError('Unknown map theme')
    chosen = replacements(theme, layer)
    if png[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG')
    out, at, seen = [png[:8]], 8, set()
    while at < len(png):
        length, kind = struct.unpack('>I4s', png[at:at + 8])
        end = at + 12 + length
        if kind == b'IHDR' and png[at + 17] != 3:
            raise ValueError('Only indexed-colour tiles can be recoloured')
        if kind in chosen:
            if length != len(chosen[kind]):
                raise ValueError(f'Unexpected {kind.decode()} size {length}')
            out.append(_chunk(kind, chosen[kind]))
            seen.add(kind)
        else:
            out.append(png[at:end])
        at = end
    if seen != set(chosen):
        raise ValueError('Tile has no palette to recolour')
    return b''.join(out)


def describe_theme(name):
    t = THEMES[name]
    base = {'label': t['label'], 'basemap': t['basemap'], 'legacy': bool(t.get('legacy')), 'lens': t.get('lens')}
    if name == 'guide':
        return {**base, 'split': 'who_2018', 'by_layer': {layer: {**colours(name, layer), 'palette_rgba': palette(name, layer)}
                                                          for layer in LAYERS}}
    return {**base, **colours(name), 'palette_rgba': palette(name)}


def presentation():
    """What the page needs to draw the legend and words; served beside the manifest."""
    return {
        'version': VERSION, 'breaks_db': list(BREAKS), 'words': [list(w) for w in WORDS], 'who_2018': WHO_2018,
        'themes': {name: describe_theme(name) for name, t in THEMES.items() if not t.get('hidden')},
        'index_palette': palette('index'),
        'note': ('Served tiles are recoloured from the stored tiles by swapping only their palette chunks. '
                 'Cell classes and all point evidence are unchanged; the stored palette stays in the manifest.'),
    }
