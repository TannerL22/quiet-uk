"""Qualified, short-duration observed sound; separate from annual model grids."""
from collections import defaultdict
from datetime import datetime, timezone
import csv
import hashlib
import json
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile

from .catalogue import CatalogueIntegrityError
from .explorer import file_hash, json_bytes

SOURCE_URL = "https://zenodo.org/records/10672568"
PROTOCOL_URL = "https://doi.org/10.3390/app10072397"
CSV_NAME = "ISD v1.0 Data.csv"
METADATA_NAME = "ISD v1.0 Metadata.xlsx"
MAX_RELEASE_BYTES = 2 * 1024 * 1024
FIELDS = ("LocationID", "SessionID", "GroupID", "RecordingLength", "LAeq_L(A)")


def metadata_locations(path):
    """Read only the author's location table; never extract archive members."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    rel_ns = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zipfile.ZipFile(path) as archive:
        def read(name):
            if archive.getinfo(name).file_size > 20 * 1024 * 1024:
                raise ValueError("Oversized source metadata member")
            return ET.fromstring(archive.read(name))
        shared = ["".join(t.text or "" for t in item.iter("{" + ns["m"] + "}t"))
                  for item in read("xl/sharedStrings.xml")]
        relationships = {item.get("Id"): item.get("Target")
                         for item in read("xl/_rels/workbook.xml.rels")}
        sheet = next(item for item in read("xl/workbook.xml").find("m:sheets", ns)
                     if item.get("name") == "Locations")
        target = relationships[sheet.get(rel_ns)]
        name = target.lstrip("/") if target.startswith("/") else "xl/" + target
        locations = {}
        for row in read(name).findall(".//m:sheetData/m:row", ns):
            cells = {}
            for cell in row:
                value = cell.find("m:v", ns)
                if value is None:
                    continue
                cells[re.sub(r"\d", "", cell.get("r"))] = shared[int(value.text)] if cell.get("t") == "s" else value.text
            label, key = cells.get("A", ""), cells.get("B", "")
            latitude = re.search(r"Lat:\s*(-?[\d.]+)", label)
            longitude = re.search(r"Lon:\s*(-?[\d.]+)", label)
            # The pinned workbook omits "Lon:" on this one documented row.
            # Retain its literal published coordinate, with an explicit QA note.
            if key == "StPaulsRow" and longitude is None:
                longitude = re.search(r"\n(-0\.0991)\s*$", label)
            if key and ("London" in label or key == "StPaulsRow") and latitude and longitude:
                locations[key] = {"name": label.split("\n")[0],
                                  "latitude": float(latitude[1]), "longitude": float(longitude[1]),
                                  "position_support": "Approximate site position published in source metadata"}
                if key == "StPaulsRow":
                    locations[key]["position_support"] += "; longitude label omitted in workbook, literal final line retained"
                if not (-0.6 < locations[key]["longitude"] < 0.3 and 51.3 < locations[key]["latitude"] < 51.8):
                    raise ValueError("Documented London site is outside expected geographic support")
    if not locations:
        raise ValueError("No documented London locations found")
    return locations


def qualify_rows(rows, locations):
    """Deduplicate shared recordings; quarantine inconsistent acoustic identities."""
    groups = defaultdict(list)
    selected_rows = 0
    for index, row in enumerate(rows, 2):
        if row["LocationID"] in locations:
            groups[row["GroupID"]].append((index, row))
            selected_rows += 1
    observations, excluded = [], []
    for group_id, members in sorted(groups.items()):
        if not group_id or group_id == "UNKNOWN" or any(
                len({r[field] for _, r in members}) != 1 for field in FIELDS):
            excluded.append({"group_id": group_id, "reason": "conflicting_or_missing_recording_identity", "rows": len(members)})
            continue
        row = members[0][1]
        try:
            level, duration = float(row["LAeq_L(A)"]), float(row["RecordingLength"])
            if not math.isfinite(level) or not math.isfinite(duration) or not 0 < duration <= 600 or not 0 <= level <= 150:
                raise ValueError("Invalid acoustic value")
        except ValueError:
            excluded.append({"group_id": group_id, "reason": "no_valid_recorded_level_or_duration", "rows": len(members)})
            continue
        dates = sorted({r.get("start_time", "")[:10] for _, r in members if r.get("start_time")})
        observations.append({
            "id": group_id, "location_id": row["LocationID"], "session_id": row["SessionID"],
            "metric": "LAeq,T", "value_db_a": level, "duration_seconds": duration,
            "evidence_type": "derived_from_acoustic_recording", "source_scope": "mixed_environmental_sound",
            "survey_dates": dates, "date_support": "Survey dates; exact recording timestamp not established",
            "source_column": "LAeq_L(A)", "source_rows": [index for index, _ in members],
            "calibration_status": "calibrated_protocol_reported_per_record_trace_not_verified",
            "uncertainty_db": None, "receiver_height_m": None,
        })
    features = []
    for key, location in sorted(locations.items()):
        records = [row for row in observations if row["location_id"] == key]
        if not records:
            continue
        features.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [location["longitude"], location["latitude"]]},
                         "properties": {"id": key, "name": location["name"],
                                        "position_support": location["position_support"], "sample_count": len(records),
                                        "sample_min_db_a": min(row["value_db_a"] for row in records),
                                        "sample_max_db_a": max(row["value_db_a"] for row in records),
                                        "duration_min_seconds": min(row["duration_seconds"] for row in records),
                                        "duration_max_seconds": max(row["duration_seconds"] for row in records),
                                        "survey_years": sorted({date[:4] for row in records for date in row["survey_dates"]})}})
    return observations, {"type": "FeatureCollection", "features": features}, {
        "selected_survey_rows": selected_rows, "accepted_recordings": len(observations),
        "excluded_groups": excluded, "site_count": len(features),
        "duplicate_policy": "One acoustic recording per GroupID; conflicting identities withheld",
    }


def build_release(source, output):
    source, output = Path(source), Path(output)
    if output.exists():
        raise ValueError("Use a new destination for an independent evidence release")
    inventory = json.loads((source / "inventory.json").read_text(encoding="utf-8"))
    if inventory["record_id"] != "10672568" or inventory["licence"] != "CC-BY-4.0":
        raise ValueError("Unexpected independent evidence identity/licence")
    expected = {entry["path"]: entry["sha256"] for entry in inventory["files"]}
    for name in (CSV_NAME, METADATA_NAME):
        if file_hash(source / name) != expected[name]:
            raise CatalogueIntegrityError("Independent source evidence changed")
    locations = metadata_locations(source / METADATA_NAME)
    with (source / CSV_NAME).open(encoding="utf-8-sig", newline="") as stream:
        observations, geojson, qa = qualify_rows(csv.DictReader(stream), locations)
    for name in (CSV_NAME, METADATA_NAME):
        if file_hash(source / name) != expected[name]:
            raise CatalogueIntegrityError("Independent source changed during processing")
    payloads = {"observations.json": json_bytes(observations), "locations.geojson": json_bytes(geojson)}
    manifest = {
        "schema_version": 1, "title": "Recorded sound samples · London",
        "source_url": SOURCE_URL, "protocol_url": PROTOCOL_URL, "licence": "CC-BY-4.0",
        "attribution": "Mitchell et al., International Soundscape Database, version 1.0.1-alpha.1, DOI 10.5281/zenodo.10672568",
        "metric": "LAeq,T", "source_scope": "mixed_environmental_sound",
        "temporal_support": "Short recordings associated with historical soundscape surveys",
        "allowed_use": "Historical observed sound context at documented sites",
        "not_supported": ["annual_model_validation", "fill_annual_raster_gaps", "present_day_sound_prediction", "source_isolated_noise"],
        "qa": qa, "source_hashes": {name: expected[name] for name in (CSV_NAME, METADATA_NAME)},
        "files": {name: hashlib.sha256(data).hexdigest() for name, data in payloads.items()},
    }
    manifest["release_id"] = "observations-" + hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    manifest["created_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True)
    for name, data in payloads.items():
        (output / name).write_bytes(data)
    (output / "manifest.json").write_bytes(json_bytes(manifest))
    IndependentObservations(output)
    return manifest


class IndependentObservations:
    """Verify a small release once and pin its served JSON to immutable memory."""
    def __init__(self, root):
        root = Path(root)
        if (root / "manifest.json").stat().st_size > MAX_RELEASE_BYTES:
            raise CatalogueIntegrityError("Independent observation manifest exceeds serving budget")
        manifest_bytes = (root / "manifest.json").read_bytes()
        self.manifest = json.loads(manifest_bytes)
        identity = {key: value for key, value in self.manifest.items() if key not in {"release_id", "created_at_utc"}}
        expected_id = "observations-" + hashlib.sha256(json_bytes(identity)).hexdigest()[:20]
        if self.manifest.get("schema_version") != 1 or self.manifest.get("release_id") != expected_id:
            raise CatalogueIntegrityError("Independent observation manifest identity mismatch")
        self.payloads = {}
        for name in ("observations.json", "locations.geojson"):
            path = root / name
            if path.stat().st_size > MAX_RELEASE_BYTES:
                raise CatalogueIntegrityError("Independent observation release exceeds serving budget")
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != self.manifest["files"].get(name):
                raise CatalogueIntegrityError("Independent observation content mismatch")
            self.payloads[name] = payload
        self.geojson = json.loads(self.payloads["locations.geojson"])
        self.observations = json.loads(self.payloads["observations.json"])
        if (self.geojson.get("type") != "FeatureCollection"
                or len(self.geojson.get("features", [])) != self.manifest["qa"]["site_count"]
                or len(self.observations) != self.manifest["qa"]["accepted_recordings"]):
            raise CatalogueIntegrityError("Independent observation count/schema mismatch")
        if (root / "manifest.json").read_bytes() != manifest_bytes:
            raise CatalogueIntegrityError("Independent observation manifest changed during verification")

    def context(self):
        return {"manifest": self.manifest, "locations": self.geojson}

    def download(self):
        return json_bytes({"manifest": self.manifest, "locations": self.geojson, "observations": self.observations})
