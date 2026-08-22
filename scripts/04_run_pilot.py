"""One-command live pilot: discover -> download -> combine."""
from __future__ import annotations
import subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
for script in ("01_discover_coverages.py", "02_download_pilot.py", "03_combine_pilot.py"):
    print(f"\n>>> {script}")
    subprocess.run([sys.executable, str(ROOT / "scripts" / script)], check=True, cwd=ROOT)
print("\nPilot complete. See data/processed/pilot/combined_bounds_100m.tif")
