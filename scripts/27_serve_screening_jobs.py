#!/usr/bin/env python3
"""Serve the bounded local Quiet UK screening-job API."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.catalogue import CatalogueError  # noqa: E402
from quiet_uk.screening_jobs import (  # noqa: E402
    ScreeningAPIServer,
    ScreeningJobManager,
    ScreeningJobStartupError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Serve the loopback-only bounded Quiet UK screening-job API."
    )
    parser.add_argument("--catalogue-dir", type=Path, required=True, help="Reviewed catalogue directory")
    parser.add_argument("--tile-root", type=Path, required=True, help="Directory containing reviewed catalogue tiles")
    parser.add_argument("--land-mask", type=Path, required=True, help="Authoritative England land-mask GeoTIFF")
    parser.add_argument("--job-root", type=Path, required=True, help="Dedicated root for generated job output directories")
    parser.add_argument("--port", type=int, required=True, help="Unused loopback TCP port")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manager = None
    server = None
    try:
        manager = ScreeningJobManager(
            args.catalogue_dir,
            args.tile_root,
            args.land_mask,
            args.job_root,
        )
        server = ScreeningAPIServer(("127.0.0.1", args.port), manager)
        print(f"Quiet UK screening jobs: http://127.0.0.1:{args.port}/")
        print(f"Catalogue build ID: {manager.catalogue_build_id}")
        print(f"Job root: {manager.job_root}")
        print("Press Ctrl+C to stop; an active bounded job is allowed to finish.")
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping screening service; waiting for any active job.")
    except (ScreeningJobStartupError, CatalogueError, OSError) as exc:
        print(f"Screening service could not start: {exc}", file=sys.stderr)
        return 2
    finally:
        if manager is not None:
            manager.begin_shutdown()
        if server is not None:
            server.shutdown()
            server.server_close()
        if manager is not None:
            manager.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
