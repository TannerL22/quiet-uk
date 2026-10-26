"""Acquire a bounded, version-pinned independent acoustic evidence snapshot.

This does not install observations into the annual Defra release. Original files
and provider checksums are retained for qualification and independent evaluation.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

import requests

RECORD_ID = "10672568"
API_URL = f"https://zenodo.org/api/records/{RECORD_ID}"
DEFAULT_FILES = ("ISD v1.0 Data.csv", "ISD v1.0 Metadata.xlsx")
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 120 * 1024 * 1024


def digest(path, algorithm="sha256"):
    h = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def capture(session, url, path, limit, expected_checksum=None):
    if urlsplit(url).scheme != "https" or urlsplit(url).hostname != "zenodo.org":
        raise ValueError("Only HTTPS downloads from zenodo.org are permitted")
    record_path = path.with_name(path.name + ".http.json")
    if path.exists() or record_path.exists():
        if not path.is_file() or not record_path.is_file():
            raise ValueError(f"Incomplete existing acquisition: {path.name}")
        old = json.loads(record_path.read_text(encoding="utf-8"))
        if old["request_url"] != url or old["sha256"] != digest(path) or old["bytes"] != path.stat().st_size:
            raise ValueError(f"Existing acquisition changed: {path.name}")
        if path.stat().st_size > limit:
            raise ValueError("Existing file exceeds acquisition budget")
        if expected_checksum:
            algorithm, expected = expected_checksum.split(":", 1)
            if digest(path, algorithm) != expected:
                raise ValueError("Existing provider checksum mismatch")
        return old
    part = path.with_name(path.name + ".part")
    if part.exists():
        raise ValueError(f"Previous incomplete download retained: {part}")
    with session.get(url, stream=True, timeout=(15, 90)) as response:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("Expected a complete HTTP 200 response")
        if urlsplit(response.url).scheme != "https" or urlsplit(response.url).hostname != "zenodo.org":
            raise ValueError("Unexpected download destination")
        declared = response.headers.get("Content-Length")
        if declared is not None and int(declared) > limit:
            raise ValueError("Download exceeds byte budget")
        size = 0
        with part.open("xb") as stream:
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > limit:
                    raise ValueError("Download exceeds byte budget")
                stream.write(chunk)
        if expected_checksum:
            algorithm, expected = expected_checksum.split(":", 1)
            if algorithm != "md5" or digest(part, algorithm) != expected:
                raise ValueError("Provider checksum mismatch; incomplete file retained")
        entry = {
            "request_url": url, "response_url": response.url,
            "retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": response.status_code, "bytes": size, "sha256": digest(part),
            "provider_checksum": expected_checksum,
            "headers": {key: value for key, value in response.headers.items()
                        if key.lower() in {"content-type", "etag", "last-modified"}},
        }
    part.rename(path)
    write_json(record_path, entry)
    return entry


def acquire(output, include_london_slm=False):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "QuietUK-IndependentEvidence/0.2 (https://github.com/TannerL22/quiet-uk)"
    with session:
        metadata_record = capture(session, API_URL, output / "zenodo-record.json", MAX_METADATA_BYTES)
        metadata = json.loads((output / "zenodo-record.json").read_text(encoding="utf-8"))
        if str(metadata.get("id")) != RECORD_ID:
            raise ValueError("Unexpected Zenodo record identity")
        licence = metadata["metadata"].get("license", {}).get("id")
        if licence != "cc-by-4.0":
            raise ValueError(f"Review unexpected dataset licence: {licence}")
        selected = list(DEFAULT_FILES) + (["SLM_London.zip"] if include_london_slm else [])
        files = {item["key"]: item for item in metadata["files"]}
        if not set(selected) <= files.keys():
            raise ValueError("Required files missing from pinned source record")
        declared_total = sum(files[name]["size"] for name in selected) + metadata_record["bytes"]
        if declared_total > MAX_TOTAL_BYTES:
            raise ValueError("Selected files exceed the 120 MiB acquisition budget")
        entries = []
        total = metadata_record["bytes"]
        for name in selected:
            entry = capture(session, files[name]["links"]["self"], output / name,
                            min(files[name]["size"], MAX_TOTAL_BYTES - total), files[name]["checksum"])
            if entry["bytes"] != files[name]["size"]:
                raise ValueError("Provider size mismatch")
            entries.append({"path": name, **entry})
            total += entry["bytes"]
            print(f"Verified {name}: {entry['bytes']:,} bytes", flush=True)
    inventory = {
        "schema_version": 1, "record_id": RECORD_ID,
        "dataset_title": metadata["metadata"]["title"],
        "doi": metadata["metadata"].get("doi"), "licence": "CC-BY-4.0",
        "source_url": f"https://zenodo.org/records/{RECORD_ID}",
        "metadata": metadata_record, "files": entries, "total_bytes": total,
        "qualification": "acquired_for_evaluation_not_annual_noise_map",
        "limitations": ["Different temporal support from annual Defra indicators",
                        "Mixed environmental sound rather than source-isolated road/rail/aircraft",
                        "Protocol, locations and calibration must be qualified before use"],
    }
    write_json(output / "inventory.json", inventory)
    return inventory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/independent_evidence_v1/source"))
    parser.add_argument("--include-london-slm", action="store_true", help="Also acquire the 93 MB London meter logs")
    args = parser.parse_args()
    result = acquire(args.output, args.include_london_slm)
    print(json.dumps({"record_id": result["record_id"], "total_bytes": result["total_bytes"],
                      "qualification": result["qualification"]}, indent=2))


if __name__ == "__main__":
    main()
