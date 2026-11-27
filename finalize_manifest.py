"""Write a migration manifest from a kept manifest and reviewed tile removals."""

import argparse
import csv
import json
import os
import tempfile
from pathlib import Path

from reconstruct_artworks import collection_key, csv_rows, layout_key


def removal_ids(gallery, review, manifest_keys):
    with (gallery / "layouts.json").open(encoding="utf-8") as stream:
        layouts = json.load(stream)["layouts"]
    kept_by_layout = {}
    gallery_keys = {}
    for art in layouts:
        kept = {t["original_map_id"]: t["pixel_key"] for t in art["tiles"] if t["kept"]}
        kept_by_layout[layout_key(art)] = kept.keys()
        gallery_keys.update(kept)
    for row in csv_rows(gallery / "ungrouped.csv", ("original_map_id", "pixel_key")):
        gallery_keys[int(row["original_map_id"])] = row["pixel_key"]
    if gallery_keys != manifest_keys:
        raise ValueError("The original IDs or pixel keys in the kept manifest differ from this gallery.")
    with review.open(encoding="utf-8-sig") as stream:
        data = json.load(stream)
    if not isinstance(data, dict) or data.get("format") not in (
        "framed-map-atlas/artwork-review-v1", "framed-map-atlas/artwork-review-v2"
    ) or not isinstance(data.get("decisions"), list):
        raise ValueError("Expected an exported artwork-decisions.json file.")
    if data.get("collection") != collection_key(kept_by_layout):
        raise ValueError("The decisions file belongs to a different gallery.")
    removed, seen = set(), set()
    for row in data["decisions"]:
        key = row["layout_key"]
        if key not in kept_by_layout or key in seen:
            raise ValueError("Unknown or repeated layout in the decisions file.")
        seen.add(key)
        if row["decision"] == "Remove tiles":
            selected = row.get("remove_ids")
            if (not isinstance(selected, list) or not selected or
                    any(type(i) is not int or i not in kept_by_layout[key] for i in selected) or
                    len(set(selected)) != len(selected)):
                raise ValueError(f"Invalid removal IDs for layout {key}.")
            removed.update(selected)
        elif row["decision"] not in ("Keep artwork", "Ignore grouping", "Needs investigation") or "remove_ids" in row:
            raise ValueError(f"Invalid decision for layout {key}.")
    return removed


def finalize(manifest, gallery, decisions, out):
    if out.exists():
        raise ValueError(f"Choose a new output filename: {out}")
    with manifest.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        fields = reader.fieldnames
        required = {"original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path"}
        if not required.issubset(fields or []):
            raise ValueError("Expected manifest.csv from export_kept.py.")
        rows = list(reader)
    keys = {int(row["original_map_id"]): row["pixel_key"] for row in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate original map IDs in kept manifest.")
    removed = removal_ids(gallery, decisions, keys)
    out.parent.mkdir(parents=True, exist_ok=True)
    relocate = out.parent.resolve() != manifest.parent.resolve()
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        stage = Path(scratch) / "manifest.csv"
        with stage.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in rows:
                if int(row["original_map_id"]) in removed:
                    continue
                if relocate:
                    for field in ("dat_path", "png_path"):
                        asset = (manifest.parent / row[field]).resolve()
                        try:
                            row[field] = Path(os.path.relpath(asset, out.parent)).as_posix()
                        except ValueError:  # Windows paths on different drives
                            row[field] = asset.as_posix()
                writer.writerow(row)
        out.touch(exist_ok=False)
        try:
            stage.replace(out)
        except OSError:
            out.unlink()
            raise
    return len(keys) - len(removed), len(removed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Original kept manifest.csv")
    parser.add_argument("--gallery", required=True, type=Path, help="Artwork gallery used by the reviewers")
    parser.add_argument("--decisions", required=True, type=Path, help="Final merged artwork-decisions.json")
    parser.add_argument("--out", required=True, type=Path, help="New migration manifest CSV")
    args = parser.parse_args()
    try:
        kept, removed = finalize(args.manifest, args.gallery, args.decisions, args.out)
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        parser.exit(1, f"Manifest export failed: {exc}\n")
    print(f"{kept:,} IDs in migration manifest; {removed:,} distinct IDs omitted. {args.out}")


if __name__ == "__main__":
    main()
