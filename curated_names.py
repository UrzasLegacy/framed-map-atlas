"""Build one row per curated tile using all observed titles in one shared map-ID namespace."""

import argparse
import csv
import json
import os
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from map_assets import index_dats, load_kept_ids, pixel_signature, read_pixels


def catalog_names(reports):
    names = defaultdict(set)
    for path in reports:
        path = path / "map-names.json" if path.is_dir() else path
        with path.open(encoding="utf-8-sig") as stream:
            records = json.load(stream)
        if not isinstance(records, list):
            raise ValueError(f"expected a list of map records in {path}")
        for record in records:
            i = record["map_id"]
            if type(i) is not int:
                raise ValueError(f"invalid map ID in {path}: {i!r}")
            observed = record["observed_names"]
            if not isinstance(observed, dict) or not all(isinstance(k, str) for k in observed):
                raise ValueError(f"invalid observed_names for map {i} in {path}")
            names[i].update(observed)
            if record.get("name"):
                names[i].add(record["name"])
    return names


def fingerprint(files, map_id):
    paths = files.get(map_id, [])
    if not paths:
        return None, None, "missing_data", "no DAT file for this ID"
    if len(paths) > 1:
        return None, None, "ambiguous_data", ", ".join(sorted(p.name for p in paths))
    try:
        digest, kind = pixel_signature(read_pixels(paths[0]))
    except (OSError, ValueError) as exc:
        return None, None, "unreadable_data", str(exc)
    return digest, kind != "image", None, None


def write_catalog(data_dir, curated, names, stream):
    files = index_dats(data_dir)
    images = defaultdict(list)
    tile_data = {}
    errors = {}
    for i in sorted(curated | names.keys()):
        digest, uniform, error, detail = fingerprint(files, i)
        if error:
            errors[i] = (error, detail)
        else:
            images[digest].append(i)
        if i in curated:
            tile_data[i] = (digest, uniform)
    columns = ("map_id", "filename", "candidate_count", "candidate_names", "name_source_ids",
               "direct_names", "other_identical_ids", "uniform", "pixel_status", "detail")
    writer = csv.DictWriter(stream, fieldnames=columns)
    writer.writeheader()
    counts = Counter()
    for i in sorted(curated):
        digest, uniform = tile_data[i]
        sources = defaultdict(list)
        # An unreadable DAT can still have titles observed directly on its ID.
        copies = images[digest] if digest is not None else [i]
        for j in copies:
            for title in names.get(j, ()):
                sources[title].append(j)
        titles = sorted(sources, key=lambda t: (t.casefold(), t))
        others = [j for j in copies if j != i]
        pixel_status, detail = errors.get(i, ("identical_copies" if others else "no_other_copy", ""))
        counts["multiple_names" if len(titles) > 1 else "one_name" if titles else "no_name"] += 1
        writer.writerow({"map_id": i, "filename": f"map_{i:05d}.png", "candidate_count": len(titles),
                         "candidate_names": "\n".join(titles),
                         "name_source_ids": json.dumps({t: sources[t] for t in titles}, ensure_ascii=False),
                         "direct_names": json.dumps(sorted(names.get(i, ())), ensure_ascii=False),
                         "other_identical_ids": ", ".join(map(str, others)), "uniform": uniform,
                         "pixel_status": pixel_status, "detail": detail})
    return counts, errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="DAT directory shared by this curated list and all name catalogs")
    parser.add_argument("--review-csv", required=True, type=Path, help="review CSV exported by the gallery; selects IDs marked Keep")
    parser.add_argument("--names-report", required=True, action="append", type=Path,
                        help="map-names.json or report folder from this map-ID namespace; repeat for additional reports")
    parser.add_argument("--out", required=True, type=Path, help="new output CSV")
    args = parser.parse_args(argv)
    if args.out.exists():
        parser.error(f"choose a new CSV path: {args.out}")
    try:
        ids = load_kept_ids(args.review_csv)
        names = catalog_names(args.names_report)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=args.out.parent) as temp:
            stage = Path(temp) / "catalog.csv"
            with stage.open("w", encoding="utf-8-sig", newline="") as f:
                counts, errors = write_catalog(args.data, ids, names, f)
            os.rename(stage, args.out)
    except (OSError, ValueError, KeyError) as exc:
        parser.exit(2, f"Unable to build catalog: {exc}\n")
    print(f"{len(ids):,} curated IDs: {counts['one_name']:,} with one name, "
          f"{counts['multiple_names']:,} with multiple names, {counts['no_name']:,} without names.")
    for i, (status, detail) in errors.items():
        print(f"Map {i}: {status} ({detail})")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
