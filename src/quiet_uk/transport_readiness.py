"""Inventory shared reported support, without calculating or certifying a total.

Three numbers at one receiver are necessary but not sufficient for a transport
combination. Period, metric and receiver compatibility require separate evidence.
"""
from itertools import combinations

import numpy as np

SOURCES = ('road', 'rail', 'aircraft')


def joint_reported_support(layers):
    """Count each disjoint source combination on an already aligned native grid.

    TIFF masks and zero codes remain unknown; neither substitutes a zero acoustic
    contribution. Unexpected unmasked encodings reject the inventory.
    """
    if set(layers) != set(SOURCES):
        raise ValueError('Supply road, rail and aircraft once each')
    code = None
    counts = {}
    for bit, source in enumerate(SOURCES):
        data = np.ma.asarray(layers[source], dtype=float)
        if data.ndim != 2 or not data.size:
            raise ValueError('Supply nonempty native two-dimensional layers')
        if code is None:
            code = np.zeros(data.shape, dtype='uint8')
        elif data.shape != code.shape:
            raise ValueError('Native layer shapes must match')
        values, mask = data.data, np.ma.getmaskarray(data)
        invalid = ~mask & (~np.isfinite(values) | (values < 0) | (values > 160))
        if invalid.any():
            raise ValueError('Unexpected unmasked noise encoding')
        reported = ~mask & (values > 0)
        code |= reported.astype('uint8') << bit
        counts[source] = int(reported.sum())
    source_sets = []
    for number in range(4):
        for members in combinations(SOURCES, number):
            key = sum(1 << SOURCES.index(source) for source in members)
            source_sets.append({'sources': list(members), 'cells': int((code == key).sum())})
    return {'cells': int(code.size), 'reported_cells_by_source': counts,
            'all_three_reported_cells': int((code == 7).sum()),
            'no_sources_reported_cells': int((code == 0).sum()),
            'disjoint_source_sets': source_sets}
