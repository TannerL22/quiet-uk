"""Run the offline historical source-grid reconciliation audit."""
from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.source_grid_audit import main


if __name__ == "__main__":
    raise SystemExit(main())
