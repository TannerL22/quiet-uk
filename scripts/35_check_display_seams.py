"""Check real PNG seam alpha against the continuous native coverage rectangle."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from quiet_uk.source_pilot import SourcePilot


from quiet_uk.display_seams import check


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pilot',type=Path,default=ROOT/'artifacts/tiled_map_v2')
    args=parser.parse_args()
    print(json.dumps(check(SourcePilot(args.pilot)),indent=2))
