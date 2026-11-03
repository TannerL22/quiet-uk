"""Compare every saved road of a reference calculation generation against a newer one.

Results, primary and node files, first-held witnesses and every saved observation
(keyed by exact coordinate) must match; only run identity and effort counters may
differ. Roads the reference never finished are compared on the observations it saved.
"""
import argparse
from datetime import datetime, timezone
import gzip
import json
from multiprocessing import Pool
from pathlib import Path
import sys

ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"
REFERENCE = ARTIFACTS / "real_road_reflection_calculation_v4"
CANDIDATE = ARTIFACTS / "real_road_reflection_calculation_v5"
FILES = ("result.json", "primary.json", "primary_nodes.json.gz", "finer_nodes.json.gz", "first_held_profile.json.gz")
# Run identity and effort counters, expected to differ between generations.
RUN_FIELDS = {"fingerprint", "new_native_points", "reused_points", "elapsed_seconds"}
ROADS = {}


def strip(value, dropped):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in RUN_FIELDS:
                dropped.add(k)
            else:
                out[k] = strip(v, dropped)
        return out
    if isinstance(value, list):
        return [strip(v, dropped) for v in value]
    return value


def differences(a, b, path=""):
    if type(a) is not type(b):
        return [path or "/"]
    if isinstance(a, dict):
        out = [f"{path}/{k}" for k in a.keys() ^ b.keys()]
        for k in a.keys() & b.keys():
            out += differences(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}[len]"]
        out = []
        for i, (x, y) in enumerate(zip(a, b)):
            out += differences(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [path or "/"]


def read(path):
    data = path.read_bytes()
    return gzip.decompress(data) if path.suffix == ".gz" else data


def compare_file(name, a, b, dropped):
    if not a.exists():
        return None
    if not b.exists():
        return f"{name}: missing in candidate"
    x, y = read(a), read(b)
    if x == y:
        return None
    diff = differences(strip(json.loads(x), dropped), strip(json.loads(y), dropped))
    return f"{name}: {diff[:5]}" if diff else None


def observations(directory):
    records = {}
    for path in sorted(directory.glob("chunk-*.jsonl.gz")) + [directory / "active.jsonl"]:
        if path.exists():
            for line in read(path).decode("utf-8").splitlines():
                if line.strip():
                    record = json.loads(line)
                    records[tuple(record["xy_bng"])] = json.dumps(record["value"], separators=(",", ":"), allow_nan=False)
    return records


def initialise(reference, candidate):
    ROADS.update(reference=reference, candidate=candidate)


def road(link):
    a, b = ROADS["reference"] / link, ROADS["candidate"] / link
    dropped = set()
    problems = [p for p in (compare_file(n, a / n, b / n, dropped) for n in FILES) if p]
    old, new = observations(a / "observations"), observations(b / "observations")
    differing = [xy for xy in old if xy in new and old[xy] != new[xy]]
    return dict(link=link, finished=(a / "result.json").exists(), problems=problems, dropped=sorted(dropped),
                reference_observations=len(old), candidate_observations=len(new),
                missing=sum(xy not in new for xy in old), differing=len(differing), differing_xy=differing[:3])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", default=str(REFERENCE))
    parser.add_argument("--candidate", default=str(CANDIDATE))
    parser.add_argument("--receipt", default=str(ARTIFACTS / "real_road_reflection_v4_v5_comparison.json"))
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    reference, candidate = Path(args.reference).resolve() / "roads", Path(args.candidate).resolve() / "roads"
    links = sorted(p.name for p in reference.iterdir() if p.is_dir())
    results = []
    with Pool(args.workers, initialise, (reference, candidate)) as pool:
        for i, r in enumerate(pool.imap_unordered(road, links), 1):
            results.append(r)
            if i % 25 == 0:
                print(f"{i}/{len(links)} roads compared", flush=True)
    finished = [r for r in results if r["finished"]]
    summary = dict(
        roads=len(results), finished_in_reference=len(finished),
        roads_with_file_differences=sum(bool(r["problems"]) for r in results),
        reference_observations=sum(r["reference_observations"] for r in results),
        missing_in_candidate=sum(r["missing"] for r in results),
        differing_observations=sum(r["differing"] for r in results),
        fields_ignored=sorted({k for r in results for k in r["dropped"]}),
    )
    identical = not any(r["problems"] or r["missing"] or r["differing"] for r in results)
    receipt = dict(status="identical" if identical else "different", generated=datetime.now(timezone.utc).isoformat(),
                   reference=str(reference.parent), candidate=str(candidate.parent), summary=summary,
                   roads=sorted(results, key=lambda r: r["link"]))
    Path(args.receipt).write_text(json.dumps(receipt, indent=1), encoding="utf-8")
    print(json.dumps(dict(status=receipt["status"], **summary), indent=1), flush=True)
    sys.exit(0 if identical else 1)


if __name__ == "__main__":
    main()
