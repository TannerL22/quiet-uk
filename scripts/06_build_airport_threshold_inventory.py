"""Build a bounded empirical inventory of airport Lden low-end behaviour."""
from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
from xml.etree import ElementTree as ET

import numpy as np
import rasterio
import requests
from rasterio.io import MemoryFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.raster import read_single_band_db
from quiet_uk.wcs import get_coverage_wcs20


NS = {
    "wcs": "http://www.opengis.net/wcs/2.0",
    "gml": "http://www.opengis.net/gml/3.2",
}
TARGET_AIRPORTS = (
    "Heathrow", "Gatwick", "Manchester", "Birmingham", "Stansted",
    "Bristol", "LondonCity", "Liverpool", "Southampton", "Newcastle",
)


def _local_name(value: str) -> str:
    return value.rsplit("__", 1)[-1]


def _coverage_ids(url: str) -> dict[str, str]:
    response = requests.get(
        url,
        params={"service": "WCS", "request": "GetCapabilities", "version": "2.0.1"},
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    found = {}
    for summary in root.findall(".//wcs:CoverageSummary", NS):
        coverage_id = summary.findtext("wcs:CoverageId", namespaces=NS) or ""
        for airport in TARGET_AIRPORTS:
            if coverage_id.endswith(f"_Airport_Noise_{airport}_Lden"):
                found[airport] = coverage_id
    return found


def _describe(url: str, coverage_id: str) -> dict:
    response = requests.get(
        url,
        params={
            "service": "WCS",
            "request": "DescribeCoverage",
            "version": "2.0.1",
            "coverageId": coverage_id,
        },
        timeout=60,
    )
    response.raise_for_status()
    root = ET.fromstring(response.content)
    envelope = root.find(".//gml:Envelope", NS)
    grid = root.find(".//gml:GridEnvelope", NS)
    lower = [float(v) for v in (envelope.findtext("gml:lowerCorner", namespaces=NS) or "").split()]
    upper = [float(v) for v in (envelope.findtext("gml:upperCorner", namespaces=NS) or "").split()]
    low = [int(v) for v in (grid.findtext("gml:low", namespaces=NS) or "").split()]
    high = [int(v) for v in (grid.findtext("gml:high", namespaces=NS) or "").split()]
    width = high[0] - low[0] + 1
    height = high[1] - low[1] + 1
    return {
        "bbox": tuple(lower + upper),
        "width": width,
        "height": height,
        "cells": width * height,
    }


def _quadrant_stats(array: np.ndarray) -> dict:
    rows = np.array_split(np.arange(array.shape[0]), 2)
    cols = np.array_split(np.arange(array.shape[1]), 2)
    result = {}
    for r, row_indices in enumerate(rows):
        for c, col_indices in enumerate(cols):
            values = array[np.ix_(row_indices, col_indices)]
            values = values[np.isfinite(values)]
            key = f"q{r + 1}{c + 1}"
            if values.size:
                result[key] = {
                    "valid_cells": int(values.size),
                    "min_db": float(values.min()),
                    "q10_db": float(np.percentile(values, 10)),
                }
            else:
                result[key] = {"valid_cells": 0, "min_db": None, "q10_db": None}
    return result


def _low_tail(values: np.ndarray, minimum: float) -> dict:
    quantiles = np.percentile(values, [1, 5, 10, 50, 90])
    near_two = values[values <= minimum + 2.0]
    rounded, counts = np.unique(np.round(near_two, 2), return_counts=True)
    order = np.argsort(counts)[::-1]
    first_values = [
        {"db": float(rounded[i]), "cells": int(counts[i])}
        for i in order[:12]
    ]
    return {
        "q01_db": float(quantiles[0]),
        "q05_db": float(quantiles[1]),
        "q10_db": float(quantiles[2]),
        "median_db": float(quantiles[3]),
        "q90_db": float(quantiles[4]),
        "cells_within_min_plus_0_5_db": int((values <= minimum + 0.5).sum()),
        "cells_within_min_plus_1_db": int((values <= minimum + 1.0).sum()),
        "first_values_near_min": first_values,
    }


def main() -> None:
    cfg = json.loads((ROOT / "config.json").read_text())
    url = cfg["wcs"]["airport"]
    ids = _coverage_ids(url)
    inventory = []
    for airport in TARGET_AIRPORTS:
        coverage_id = ids.get(airport)
        if coverage_id is None:
            inventory.append({"airport": airport, "status": "not_found"})
            continue
        description = _describe(url, coverage_id)
        bbox = description["bbox"]
        data = get_coverage_wcs20(
            url,
            coverage_id,
            bbox,
            description["width"],
            description["height"],
            crs=cfg.get("crs", "EPSG:27700"),
            format_="image/tiff",
            padding_cells=0,
            timeout=300,
        )
        with MemoryFile(data) as memory:
            with memory.open() as dataset:
                array, read_diagnostics = read_single_band_db(dataset)
                returned_shape = list(dataset.shape)
                returned_bounds = list(dataset.bounds)
        values = array[np.isfinite(array)]
        if values.size == 0:
            inventory.append({
                "airport": airport,
                "coverage_id": coverage_id,
                "status": "no_positive_reported_cells",
            })
            continue
        minimum = float(values.min())
        min_count = int(np.isclose(values, minimum, rtol=0.0, atol=1e-6).sum())
        quadrants = _quadrant_stats(array)
        quadrant_minima = [
            item["min_db"] for item in quadrants.values() if item["min_db"] is not None
        ]
        spread = max(quadrant_minima) - min(quadrant_minima) if quadrant_minima else 0.0
        if min_count >= 10 and _low_tail(values, minimum)["q01_db"] <= minimum + 0.2:
            endpoint_assessment = (
                "observed low endpoint and concentrated q01 tail are consistent with a "
                "reporting/censoring floor; not proof of the competent-authority threshold"
            )
        else:
            endpoint_assessment = "insufficient evidence for a deliberate cutoff from this sample"
        if spread > 1.0:
            multiple_assessment = (
                "quadrant minima differ by more than 1 dB; this may reflect footprint geometry "
                "or multiple cutoff behaviours and needs source-level confirmation"
            )
        else:
            multiple_assessment = (
                "no >1 dB quadrant-minimum split observed; this does not prove a uniform cutoff"
            )
        center = ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)
        tail = _low_tail(values, minimum)
        inventory.append({
            "airport": airport,
            "status": "sampled_full_coverage",
            "coverage_id": coverage_id,
            "location_epsg27700_center": json.dumps([round(center[0], 1), round(center[1], 1)]),
            "coverage_bbox_epsg27700": json.dumps([round(v, 1) for v in bbox]),
            "declared_shape": json.dumps([description["height"], description["width"]]),
            "declared_cells": description["cells"],
            "returned_shape": json.dumps(returned_shape),
            "returned_bounds_epsg27700": json.dumps([round(v, 1) for v in returned_bounds]),
            "valid_reported_cells": int(values.size),
            "minimum_positive_lden_db": minimum,
            "minimum_value_cell_count": min_count,
            "low_tail_distribution": json.dumps(tail, separators=(",", ":")),
            "quadrant_minima": json.dumps(quadrants, separators=(",", ":")),
            "quadrant_minimum_spread_db": spread,
            "apparent_cutoff_assessment": endpoint_assessment,
            "multiple_cutoff_behaviour_assessment": multiple_assessment,
            "nodata_and_encoding": json.dumps(read_diagnostics, separators=(",", ":")),
        })

    output_dir = ROOT / "data" / "processed"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_csv = output_dir / "airport_threshold_inventory.csv"
    columns = sorted({key for row in inventory for key in row})
    with output_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(inventory)
    summary = {
        "method": {
            "coverage_endpoint": url,
            "wcs_version": "2.0.1",
            "coverage_selection": "individual *_Lden coverages for ten named airports",
            "sampling": "full individual coverage extent at native 10 m grid; padding_cells=0",
            "reported_cells": "finite positive values after declared nodata/mask and zero-sentinel handling",
            "low_tail": "minimum, quantiles, repeated values within minimum + 0.5/1/2 dB, and 2x2 spatial quadrant minima",
            "interpretation_limit": "a repeated low endpoint is evidence consistent with censoring, not proof of the authority threshold",
        },
        "airports_requested": list(TARGET_AIRPORTS),
        "airports_found": sorted(ids),
        "inventory_csv": str(output_csv.relative_to(ROOT)),
    }
    (output_dir / "airport_threshold_inventory_summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps({"summary": summary, "inventory": inventory}, indent=2))


if __name__ == "__main__":
    main()
