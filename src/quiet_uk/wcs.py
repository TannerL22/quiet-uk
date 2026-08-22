from __future__ import annotations
from typing import Iterable
import requests


def discover_coverages(url: str, versions=("1.0.0", "2.0.1"), timeout=60):
    """Try common WCS versions and return live coverage identifiers."""
    from owslib.wcs import WebCoverageService
    errors = {}
    for version in versions:
        try:
            wcs = WebCoverageService(url, version=version, timeout=timeout)
            ids = list(wcs.contents.keys())
            return {"version": version, "identifiers": ids}
        except Exception as exc:
            errors[version] = repr(exc)
    return {"version": None, "identifiers": [], "errors": errors}


def score_lden_identifier(identifier: str, source: str | None = None) -> int:
    """Heuristic score for selecting an all-source Lden coverage."""
    s = identifier.lower()
    score = 0
    if "lden" in s or "l_den" in s or "l-den" in s:
        score += 100
    if "all" in s:
        score += 20
    if source and source.lower() in s:
        score += 10
    # Prefer ordinary A-weighted metric over octave-band/frequency coverages.
    if any(x in s for x in ("octave", "63hz", "125hz", "250hz", "500hz", "1khz", "2khz", "4khz", "8khz")):
        score -= 100
    if "night" in s or "l16" in s or "laeq" in s or "day" in s or "even" in s:
        score -= 20
    return score


def choose_lden_identifier(identifiers: Iterable[str], source: str | None = None):
    ids = list(identifiers)
    if not ids:
        return None
    ranked = sorted(ids, key=lambda x: score_lden_identifier(x, source), reverse=True)
    return ranked[0] if score_lden_identifier(ranked[0], source) > 0 else None


def get_coverage_wcs10(url: str, coverage_id: str, bbox, width: int, height: int,
                       crs="EPSG:27700", timeout=180) -> bytes:
    """Direct WCS 1.0 GetCoverage request returning raw GeoTIFF bytes."""
    params = {
        "service": "WCS",
        "version": "1.0.0",
        "request": "GetCoverage",
        "coverage": coverage_id,
        "bbox": ",".join(str(v) for v in bbox),
        "crs": crs,
        "response_crs": crs,
        "width": str(width),
        "height": str(height),
        "format": "GeoTIFF",
    }
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "xml" in ctype or r.content.lstrip().startswith(b"<"):
        snippet = r.text[:1000]
        raise RuntimeError(f"WCS returned XML instead of GeoTIFF: {snippet}")
    return r.content


def _epsg_uri(crs: str) -> str:
    """Return the CRS URI accepted by the Defra WCS 2.0.1 service."""
    if crs.upper().startswith("EPSG:"):
        return f"http://www.opengis.net/def/crs/EPSG/0/{crs.split(':', 1)[1]}"
    return crs


def get_coverage_wcs20(url: str, coverage_id: str, bbox, width: int, height: int,
                       crs="EPSG:27700", format_="image/tiff", timeout=180,
                       padding_cells: int = 1) -> bytes:
    """Retrieve a WCS 2.0.1 coverage using repeated E/N subset parameters.

    Defra's airport Round 4 endpoint advertises WCS 2.0.1 coverage IDs with
    ``__`` separators and uses ``image/tiff`` as its native response format.
    The subset bounds and requested dimensions are deliberately explicit so
    the returned raster can be checked against the pilot grid.
    """
    minx, miny, maxx, maxy = bbox
    if padding_cells < 0:
        raise ValueError("padding_cells must be non-negative")
    # Defra's WCS 2.0.1 airport grid is on a half-cell-shifted native origin.
    # Request one extra cell at native spacing so the later explicit alignment
    # step has valid source pixels at every tile edge. Inventory downloads can
    # set padding_cells=0 when sampling a coverage's own extent.
    cell_x = (maxx - minx) / width
    cell_y = (maxy - miny) / height
    request_bbox = (
        minx - cell_x * padding_cells / 2.0,
        miny - cell_y * padding_cells / 2.0,
        maxx + cell_x * padding_cells / 2.0,
        maxy + cell_y * padding_cells / 2.0,
    )
    request_width = width + padding_cells
    request_height = height + padding_cells
    params = [
        ("service", "WCS"),
        ("version", "2.0.1"),
        ("request", "GetCoverage"),
        ("coverageId", coverage_id),
        ("format", format_),
        ("outputCRS", _epsg_uri(crs)),
        ("subset", f"E({request_bbox[0]},{request_bbox[2]})"),
        ("subset", f"N({request_bbox[1]},{request_bbox[3]})"),
        ("scaleSize", f"i({request_width}),j({request_height})"),
    ]
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "xml" in ctype or r.content.lstrip().startswith(b"<"):
        snippet = r.text[:1000]
        raise RuntimeError(
            f"WCS 2.0.1 returned XML instead of {format_}: {snippet}"
        )
    return r.content


def get_coverage(url: str, coverage_id: str, bbox, width: int, height: int,
                 crs="EPSG:27700", version="1.0.0", format_=None,
                 timeout=180) -> bytes:
    """Retrieve a coverage using the configured WCS protocol version."""
    if version == "1.0.0":
        return get_coverage_wcs10(
            url, coverage_id, bbox, width, height, crs=crs, timeout=timeout
        ) if format_ in (None, "GeoTIFF") else _get_coverage_wcs10_format(
            url, coverage_id, bbox, width, height, crs, format_, timeout
        )
    if version == "2.0.1":
        return get_coverage_wcs20(
            url, coverage_id, bbox, width, height, crs=crs,
            format_=format_ or "image/tiff", timeout=timeout
        )
    raise ValueError(f"Unsupported WCS request version: {version}")


def _get_coverage_wcs10_format(url: str, coverage_id: str, bbox, width: int,
                               height: int, crs: str, format_: str,
                               timeout: int) -> bytes:
    """WCS 1.0 helper retaining an explicit non-default format identifier."""
    params = {
        "service": "WCS",
        "version": "1.0.0",
        "request": "GetCoverage",
        "coverage": coverage_id,
        "bbox": ",".join(str(v) for v in bbox),
        "crs": crs,
        "response_crs": crs,
        "width": str(width),
        "height": str(height),
        "format": format_,
    }
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    ctype = (r.headers.get("content-type") or "").lower()
    if "xml" in ctype or r.content.lstrip().startswith(b"<"):
        snippet = r.text[:1000]
        raise RuntimeError(f"WCS returned XML instead of {format_}: {snippet}")
    return r.content
