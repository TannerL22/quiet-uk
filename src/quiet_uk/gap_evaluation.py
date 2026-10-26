"""Bounded interpolation experiments, never production gap filling.

Reported-cell holdouts assess interpolation within the reported support only.
They cannot validate predictions in censored or otherwise unreported cells.
"""
from collections import Counter
import math

import numpy as np
from scipy import ndimage, sparse
from scipy.sparse.linalg import spsolve
from scipy.spatial import cKDTree

PATCH_SIZES = (3, 9, 21)  # Native 10 m cells: 30, 90, 210 m squares.
METHODS = ("nearest", "idw8", "harmonic_db")


def component_inventory(values, reported, cutoff):
    """Summarise enclosed unknown components without assigning levels to them.

    Eight-connected components touching the core border are excluded because
    their full extent and surrounding values are not available in this core.
    """
    labels, count = ndimage.label(~reported, structure=np.ones((3, 3)))
    sizes = np.bincount(labels.ravel(), minlength=count + 1)
    border = np.unique(np.concatenate((labels[0], labels[-1], labels[:, 0], labels[:, -1])))
    enclosed = np.ones(count + 1, dtype=bool); enclosed[border] = False; enclosed[0] = False
    low, high = np.full(count + 1, np.inf), np.full(count + 1, -np.inf)
    height, width = labels.shape
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if not dy and not dx:
                continue
            y0, y1 = max(0, -dy), min(height, height - dy)
            x0, x1 = max(0, -dx), min(width, width - dx)
            ids = labels[y0:y1, x0:x1]
            valid = reported[y0+dy:y1+dy, x0+dx:x1+dx] & (ids > 0)
            adjacent = values[y0+dy:y1+dy, x0+dx:x1+dx]
            np.minimum.at(low, ids[valid], adjacent[valid])
            np.maximum.at(high, ids[valid], adjacent[valid])
    buckets = Counter()
    for i in np.flatnonzero(enclosed):
        size = int(sizes[i])
        scale = "1-4" if size <= 4 else "5-25" if size <= 25 else "26-100" if size <= 100 else "101-1000" if size <= 1000 else "over-1000"
        ring = "lowest_5db_band" if math.isfinite(high[i]) and high[i] <= cutoff + 5 else "higher_reported_ring"
        buckets[(scale, ring)] += 1
    return {"unknown_cells": int((~reported).sum()), "reported_cells": int(reported.sum()),
            "enclosed_components": int(enclosed.sum()), "enclosed_unknown_cells": int(sizes[enclosed].sum()),
            "border_components_excluded": int(len(border[border > 0])),
            "buckets": [{"size_cells": size, "ring": ring, "components": number}
                        for (size, ring), number in sorted(buckets.items())]}


def predict_patch(boundary):
    """Hide an entire square interior; use its one-cell reported perimeter.

    The interior is ignored even if passed truth, making leakage impossible.
    All baselines are convex interpolation in dB and obey boundary extrema.
    """
    boundary = np.asarray(boundary, dtype=float)
    if boundary.ndim != 2 or boundary.shape[0] != boundary.shape[1] or boundary.shape[0] < 3:
        raise ValueError("Supply a square with a one-cell boundary")
    n = boundary.shape[0] - 2
    known = np.ones(boundary.shape, dtype=bool); known[1:-1, 1:-1] = False
    support, targets = np.argwhere(known), np.argwhere(~known)
    levels = boundary[known]
    if not np.isfinite(levels).all():
        raise ValueError("Perimeter must be fully reported")
    distance, index = cKDTree(support).query(targets, k=min(8, len(support)))
    weights = 1 / distance ** 2
    nearest = levels[index[:, 0]].reshape(n, n)
    idw = ((levels[index] * weights).sum(axis=1) / weights.sum(axis=1)).reshape(n, n)
    # Discrete Laplace equation: each interior value equals four neighbours.
    rows, cols, data = [], [], []
    rhs = np.zeros(n*n)
    for y in range(n):
        for x in range(n):
            i = y*n+x; rows.append(i); cols.append(i); data.append(4)
            for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                yy, xx = y+dy, x+dx
                if 0 <= yy < n and 0 <= xx < n:
                    rows.append(i); cols.append(yy*n+xx); data.append(-1)
                else:
                    rhs[i] += boundary[yy+1, xx+1]
    matrix = sparse.csr_matrix((data, (rows, cols)), shape=(n*n, n*n))
    harmonic = spsolve(matrix, rhs).reshape(n, n)
    return dict(zip(METHODS, (nearest, idw, harmonic)))


def evaluate_core(values, reported, cutoff, seed, per_stratum=8, sizes=PATCH_SIZES):
    """Deterministic, nonoverlapping patches per size/stratum within one core."""
    rng = np.random.default_rng(seed)
    results = []
    for n in sizes:
        width = n+2
        eligible = ndimage.minimum_filter(reported.astype('uint8'), size=width, mode='constant', cval=0).astype(bool)
        supported = np.where(reported, values, np.inf)
        radius = width//2
        horizontal = ndimage.maximum_filter(supported, size=(1, width), mode='constant', cval=np.inf)
        vertical = ndimage.maximum_filter(supported, size=(width, 1), mode='constant', cval=np.inf)
        # Stratum uses the visible perimeter only, never hidden target values.
        ring_max = np.maximum.reduce((np.roll(horizontal, radius, axis=0), np.roll(horizontal, -radius, axis=0),
                                      np.roll(vertical, radius, axis=1), np.roll(vertical, -radius, axis=1)))
        ring_low = ring_max <= cutoff+5
        used = np.zeros(values.shape, dtype=bool)
        for stratum, mask in (("near_reporting_floor", eligible & ring_low), ("higher_reported", eligible & ~ring_low)):
            candidates = np.argwhere(mask)
            rng.shuffle(candidates)
            accepted = 0
            for y, x in candidates:
                radius = width//2
                ys, xs = slice(y-radius, y+radius+1), slice(x-radius, x+radius+1)
                if used[ys, xs].any():
                    continue
                used[ys, xs] = True
                window = values[ys, xs]
                truth = window[1:-1, 1:-1]
                for method, prediction in predict_patch(window).items():
                    error = prediction-truth
                    results.append({"patch_size_cells": n, "patch_width_m": n*10, "stratum": stratum,
                                    "row": int(y), "column": int(x), "method": method,
                                    "cells": n*n, "mae_db": float(np.abs(error).mean()),
                                    "squared_error_sum": float(np.square(error).sum()),
                                    "bias_db": float(error.mean()), "max_abs_error_db": float(np.abs(error).max()),
                                    "truth_min_db": float(truth.min()), "truth_max_db": float(truth.max())})
                accepted += 1
                if accepted >= per_stratum:
                    break
    return results


def summarise(rows):
    """Aggregate held-out cell error, plus patch variability; no CI claims."""
    groups = {}
    for row in rows:
        key = (row['metric'], row['patch_width_m'], row['stratum'], row['method'])
        groups.setdefault(key, []).append(row)
    result = []
    for (metric, width, stratum, method), members in sorted(groups.items()):
        cells = sum(row['cells'] for row in members)
        result.append({"metric": metric, "patch_width_m": width, "stratum": stratum, "method": method,
                       "patches": len(members), "cores": len({row['record_id'] for row in members}), "cells": cells,
                       "mae_db": sum(row['mae_db']*row['cells'] for row in members)/cells,
                       "rmse_db": math.sqrt(sum(row['squared_error_sum'] for row in members)/cells),
                       "bias_db": sum(row['bias_db']*row['cells'] for row in members)/cells,
                       "patch_mae_p90_db": float(np.quantile([row['mae_db'] for row in members], .9)),
                       "max_abs_error_db": max(row['max_abs_error_db'] for row in members)})
    return result
