from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.wcs import discover_coverages, choose_lden_identifier

cfg_path = ROOT / "config.json"
if not cfg_path.exists():
    cfg_path.write_text((ROOT / "config.example.json").read_text())

cfg = json.loads(cfg_path.read_text())
catalog = {}
changed = False

for source, url in cfg["wcs"].items():
    print(f"\n=== {source.upper()} ===\n{url}")
    request_version = cfg.get("wcs_versions", {}).get(source)
    versions = (request_version,) if request_version else ("1.0.0", "2.0.1")
    result = discover_coverages(url, versions=versions)
    chosen = choose_lden_identifier(result.get("identifiers", []), source)
    result["chosen_lden"] = chosen
    result["request_version"] = request_version or result.get("version")
    result["request_format"] = cfg.get("wcs_formats", {}).get(source)
    catalog[source] = result
    print("WCS version:", result.get("version"))
    print("GetCoverage format:", result.get("request_format"))
    print("Chosen Lden:", chosen)
    for ident in result.get("identifiers", []):
        print(" -", ident)
    if chosen and cfg["coverage_ids"].get(source) != chosen:
        cfg["coverage_ids"][source] = chosen
        changed = True

out = ROOT / "data" / "processed" / "coverage_catalog.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(catalog, indent=2))
if changed:
    cfg_path.write_text(json.dumps(cfg, indent=2))
    print("\nUpdated config.json with discovered Lden coverage IDs.")
print("Saved:", out)
