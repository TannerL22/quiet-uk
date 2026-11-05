"""How the England map is coloured and described to people. A display layer only.

The stored tiles keep the palette they were built and verified with
(``manifest['display']['palette_rgba']``). When a tile is served, its PLTE and tRNS
chunks are swapped for one of the themes below. The pixel data (IDAT), and so the
class of every cell, is untouched. Point values and evidence never come from here.

Palette entries match the indices written by ``england_map.encode``:
0 outside the downloaded rasters, 1-2 no data (hatched), 3 below the published
cutoff, 4-11 the eight value bands (``source_pilot.BREAKS``).

Colours: one violet hue, lightness falling steadily from quiet to loud, so the
order reads without a legend and survives colour-blindness and greyscale. Both
ramps pass the ordinal checks of the dataviz palette validator (monotone
lightness, steps at least 0.06 apart in OKLCH L, one hue, light end at least 2:1
against the basemap): light against OpenFreeMap Positron (#f2f3f0), dark against
OpenFreeMap Dark (#0c0c0c), where the anchor flips so quiet recedes.
"""
import struct
import zlib

from .source_pilot import BREAKS

VERSION = 'display-v2-violet'
THEMES = {
    'light': {
        'bands': ['#b5a3da', '#a48ad4', '#9371cb', '#825abc', '#7044ac', '#5d3295', '#4a237a', '#37165e'],
        'below': '#e8e0f9', 'no_data': '#9aa5b4',
    },
    'dark': {
        'bands': ['#60438d', '#7555a8', '#8a69be', '#9e7fd3', '#b397e5', '#c7aff5', '#dbcafe', '#eee7ff'],
        'below': '#322843', 'no_data': '#6b7583',
    },
}
ALPHA = {'no_data': (60, 130), 'below': 180, 'band': 235}

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


def palette(theme):
    """Twelve RGBA entries in the stored tiles' index order."""
    t = THEMES[theme]
    return ([(0, 0, 0, 0), (*rgb(t['no_data']), ALPHA['no_data'][0]), (*rgb(t['no_data']), ALPHA['no_data'][1]),
             (*rgb(t['below']), ALPHA['below'])] + [(*rgb(c), ALPHA['band']) for c in t['bands']])


def _chunk(kind, data):
    return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))


_REPLACEMENTS = {theme: {b'PLTE': bytes(v for c in palette(theme) for v in c[:3]),
                         b'tRNS': bytes(c[3] for c in palette(theme))} for theme in THEMES}


def recolour(png, theme):
    """The same indexed PNG with this theme's palette; every other chunk is copied byte for byte."""
    replacements = _REPLACEMENTS[theme]
    if png[:8] != b'\x89PNG\r\n\x1a\n':
        raise ValueError('Not a PNG')
    out, at, seen = [png[:8]], 8, set()
    while at < len(png):
        length, kind = struct.unpack('>I4s', png[at:at + 8])
        end = at + 12 + length
        if kind == b'IHDR' and png[at + 17] != 3:
            raise ValueError('Only indexed-colour tiles can be recoloured')
        if kind in replacements:
            if length != len(replacements[kind]):
                raise ValueError(f'Unexpected {kind.decode()} size {length}')
            out.append(_chunk(kind, replacements[kind]))
            seen.add(kind)
        else:
            out.append(png[at:end])
        at = end
    if seen != set(replacements):
        raise ValueError('Tile has no palette to recolour')
    return b''.join(out)


def presentation():
    """What the page needs to draw the legend and words; served beside the manifest."""
    return {
        'version': VERSION, 'breaks_db': list(BREAKS), 'words': [list(w) for w in WORDS], 'who_2018': WHO_2018,
        'themes': {name: {**t, 'palette_rgba': palette(name)} for name, t in THEMES.items()},
        'note': ('Served tiles are recoloured from the stored tiles by swapping only their palette chunks. '
                 'Cell classes and all point evidence are unchanged; the stored palette stays in the manifest.'),
    }
