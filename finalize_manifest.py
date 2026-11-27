"""Write a migration manifest with reviewed removals and recovered map tiles."""

import argparse
import csv
import json
import os
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from reconstruct_artworks import collection_key, csv_rows, layout_key
from map_assets import index_dats, pixel_signature, read_pixels, render_pixels


def reviewed_changes(gallery, review, manifest_keys):
    with (gallery / "layouts.json").open(encoding="utf-8") as stream:
        layouts = json.load(stream)["layouts"]
    kept_by_layout = {}
    gallery_keys = {}
    excluded_names = defaultdict(set)
    for art in layouts:
        for t in art["tiles"]:
            if not t["kept"]:
                excluded_names[t["original_map_id"]].update(t.get("observed_names", []))
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
        "framed-map-atlas/artwork-review-v1", "framed-map-atlas/artwork-review-v2",
        "framed-map-atlas/artwork-review-v3", "framed-map-atlas/artwork-review-v4"
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
    recovered = {}
    entries = data["recover_tiles"] if data["format"].endswith(("-v3","-v4")) else []
    if not isinstance(entries, list):
        raise ValueError("Expected a list of recovery IDs.")
    for entry in entries:
        i, key = entry["map_id"], entry["pixel_key"]
        if type(i) is not int or i in recovered or i in manifest_keys or i not in excluded_names:
            raise ValueError(f"Invalid or repeated excluded map ID: {i}")
        if key is not None and not re.fullmatch("[0-9a-f]{64}", key):
            raise ValueError(f"Invalid recovery pixel key for map {i}")
        recovered[i] = key
    return removed, recovered, excluded_names


def recover_assets(stage, asset_dir, recovered, names, data_dir, review_csv):
    if data_dir is None:
        raise ValueError("Use --data-dir with the original DAT directory to export recovered tiles.")
    if asset_dir.exists():
        raise ValueError(f"Recovery asset folder already exists: {asset_dir}. Choose a fresh output name.")
    reviewed = {}
    if review_csv:
        for row in csv_rows(review_csv, ("map_id", "pixel_key", "chosen_name")):
            i = int(row["map_id"])
            if i in recovered:
                value = (row["pixel_key"], row["chosen_name"])
                if reviewed.setdefault(i, value) != value:
                    raise ValueError(f"Conflicting review CSV rows for map {i}")
    indexed = index_dats(data_dir)
    stage.mkdir()
    rows = []
    for i, expected in sorted(recovered.items()):
        paths = indexed.get(i, [])
        if len(paths) != 1:
            raise ValueError(f"Expected one original DAT for recovered map {i}; found {len(paths)} in {data_dir}")
        dat = stage / f"map_{i}.dat"
        png = stage / f"map_{i}.png"
        shutil.copyfile(paths[0], dat)
        try:
            pixels = read_pixels(dat)
        except ValueError as exc:
            raise ValueError(f"Recovered map {i}: {exc}") from exc
        digest, _ = pixel_signature(pixels)
        if (expected and digest != expected) or (i in reviewed and reviewed[i][0] != digest):
            raise ValueError(f"Recovered map {i}: original DAT pixels differ from the review")
        png.write_bytes(render_pixels(pixels))
        title = next(iter(names[i])) if len(names[i]) == 1 else ""
        if i in reviewed:
            title = reviewed[i][1]
        rows.append(dict(original_map_id=i, chosen_name=title, pixel_key=digest,
                         dat_path=f"{asset_dir.name}/{dat.name}", png_path=f"{asset_dir.name}/{png.name}"))
    return rows


def finalize(manifest, gallery, decisions, out, data_dir=None, review_csv=None):
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
    removed, recovered, names = reviewed_changes(gallery, decisions, keys)
    out.parent.mkdir(parents=True, exist_ok=True)
    relocate = out.parent.resolve() != manifest.parent.resolve()
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        stage = Path(scratch) / "manifest.csv"
        asset_dir = out.with_name(out.stem + "-assets")
        staged_assets = Path(scratch) / "assets"
        added_rows = recover_assets(staged_assets, asset_dir, recovered, names, data_dir, review_csv) if recovered else []
        with stage.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            retained = [r for r in rows if int(r["original_map_id"]) not in removed]
            if relocate:
                for row in retained:
                    for field in ("dat_path", "png_path"):
                        asset = (manifest.parent / row[field]).resolve()
                        try:
                            row[field] = Path(os.path.relpath(asset, out.parent)).as_posix()
                        except ValueError:  # Windows paths on different drives
                            row[field] = asset.as_posix()
            writer.writerows(sorted(retained + added_rows, key=lambda r: int(r["original_map_id"])))
        out.touch(exist_ok=False)
        published_assets = False
        try:
            if recovered:
                staged_assets.rename(asset_dir)
                published_assets = True
            stage.replace(out)
        except OSError:
            out.unlink()
            if published_assets:
                asset_dir.rename(staged_assets)
            raise
    return len(keys) - len(removed) + len(recovered), len(removed)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Original kept manifest.csv")
    parser.add_argument("--gallery", required=True, type=Path, help="Artwork gallery used by the reviewers")
    parser.add_argument("--decisions", required=True, type=Path, help="Final merged artwork-decisions.json")
    parser.add_argument("--out", required=True, type=Path, help="New migration manifest CSV")
    parser.add_argument("--data-dir", type=Path, help="Original DAT folder for IDs selected for recovery")
    parser.add_argument("--review-csv", type=Path, help="Original sorter review CSV to verify recovered pixels and restore chosen names")
    args = parser.parse_args()
    try:
        kept, removed = finalize(args.manifest, args.gallery, args.decisions, args.out, args.data_dir, args.review_csv)
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        parser.exit(1, f"Manifest export failed: {exc}\n")
    print(f"{kept:,} IDs in migration manifest; {removed:,} distinct IDs omitted. {args.out}")


if __name__ == "__main__":
    main()
