"""Offline compatibility with byte-preserved public provider responses."""
import json
from pathlib import Path

import pytest
import requests

from quiet_uk import source_pilot as p

ROOT = Path(__file__).parent / "fixtures/provider"
INDEX = json.loads((ROOT / "fixture.json").read_text(encoding="utf-8"))


def test_capture_checksums_and_http_evidence():
    for name, expected_hash in INDEX["files"].items():
        assert p.file_hash(ROOT / name) == expected_hash, name
        if not name.endswith(".http.json"):
            capture = json.loads((ROOT / (name + ".http.json")).read_text(encoding="utf-8"))
            assert capture["status"] == 200
            assert capture["sha256"] == expected_hash
            assert capture["bytes"] == (ROOT / name).stat().st_size
            assert capture["request_url"].startswith("https://environment.data.gov.uk/")


@pytest.mark.parametrize("record", INDEX["records"], ids=lambda r: r["id"])
def test_real_rasters_match_recorded_request_grid_and_encoding(record):
    product = INDEX["products"][record["source"]]
    description = product["requests_by_metric"][record["metric"]]["description_file"]
    grid = p.native_grid((ROOT / description).read_bytes())
    assert grid == record["native_grid"]
    qa = p.inspect_raster(ROOT / record["path"], record["qa"]["bounds"], grid, record["metric"])
    assert qa == record["qa"]
    assert p.file_hash(ROOT / record["path"]) == record["sha256"]
    # Prepare only: no live service call or dependence on provider availability.
    request = requests.Request("GET", product["endpoint"], params=p.coverage_params(
        product, record["metric"], qa["bounds"])).prepare()
    capture = json.loads((ROOT / record["http_record"]).read_text(encoding="utf-8"))
    assert request.url == capture["request_url"]
    expected = {
        "heathrow-road-Lden": (38853, 1147, 0, 40.0),
        "chilterns-road-Lnight": (3757, 36243, 0, 35.0),
        "heathrow-rail-Lden": (0, 40000, 0, None),
        "heathrow-aircraft-Lnight": (33655, 0, 6345, 44.0),
    }
    assert tuple(qa[key] for key in (
        "reported_cells", "zero_sentinel_cells", "nodata_cells", "reported_min_db"
    )) == expected[record["id"]]


@pytest.mark.parametrize("source,name", [
    ("road", "road.html"), ("rail", "rail.html"),
    ("aircraft", "aircraft.html"), ("road", "road-hydrated.html"),
])
def test_real_metadata_preserves_declared_and_unspecified_periods(source, name):
    html = (ROOT / "evidence" / name).read_text(encoding="utf-8")
    expected = None if source == "aircraft" else {"start": "2021-01-01", "end": "2021-12-31"}
    assert p.provider_period(html, p.PROVIDERS[source]["id"]) == expected
