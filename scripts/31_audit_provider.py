"""Audit small native WCS requests; default operation verifies saved evidence offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.provider_audit import acquire, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/provider_audit_v1')
    parser.add_argument('--acquire', action='store_true', help='Explicitly allow bounded provider requests')
    parser.add_argument('--reference', type=Path, default=ROOT/'artifacts/source_regions_v1',
                        help='Original regional release used only when creating an acquisition recipe')
    args = parser.parse_args()
    result = acquire(args.output, args.reference) if args.acquire else verify(args.output)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
