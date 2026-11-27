"""Finalize reviewed map art, destination-numbered assets and box-builder placements."""

import argparse
import csv
import gzip
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from reconstruct_artworks import collection_key, csv_rows, layout_key
from anvil import LIMIT, NBT, decompress
from map_assets import index_dats, pixel_signature, read_map_data, render_pixels


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
    return removed, recovered, excluded_names, layouts, data


def recover_rows(recovered, names, data_dir, review_csv):
    if not recovered:
        return []
    if data_dir is None:
        raise ValueError("Use --data-dir with the original DAT directory to export recovered tiles.")
    reviewed = {}
    if review_csv:
        for row in csv_rows(review_csv, ("map_id", "pixel_key", "chosen_name")):
            i = int(row["map_id"])
            if i in recovered:
                value = (row["pixel_key"], row["chosen_name"])
                if reviewed.setdefault(i, value) != value:
                    raise ValueError(f"Conflicting review CSV rows for map {i}")
    indexed = index_dats(data_dir)
    rows = []
    for i, expected in recovered.items():
        paths = indexed[i]
        if len(paths) != 1:
            raise ValueError(f"Expected one original DAT for recovered map {i}; found {len(paths)} in {data_dir}")
        title = next(iter(names[i])) if len(names[i]) == 1 else ""
        if i in reviewed:
            reviewed_key, title = reviewed[i]
            if expected is not None and expected != reviewed_key:
                raise ValueError(f"Recovered map {i}: review pixel keys disagree")
            expected = reviewed_key
        rows.append(dict(original_map_id=i, pixel_key=expected, chosen_name=title, source=paths[0]))
    return rows


def locked_dat(source):
    """Change only data.locked, preserving the exact binary encoding of other NBT tags."""
    with source.open("rb") as stream:
        packed = stream.read(LIMIT + 1)
    if len(packed) > LIMIT:
        raise ValueError(f"{source}: map exceeds the decoding limit")
    raw = decompress(1, packed) if packed.startswith(b"\x1f\x8b") else packed
    reader = NBT(raw)
    if reader.number(">B") != 10:
        raise ValueError(f"{source}: expected a compound root")
    reader.string(False)
    while (kind := reader.number(">B")):
        name = reader.string()
        if name != "data" or kind != 10:
            reader.value(kind, False)
            continue
        while (kind := reader.number(">B")):
            field = reader.string()
            if field == "locked":
                if kind != 1:
                    raise ValueError(f"{source}: expected a byte for data.locked")
                pos = reader.pos
                if reader.number(">b") == 1:
                    return packed
                return gzip.compress(raw[:pos] + b"\x01" + raw[pos+1:], mtime=0)
            reader.value(kind, False)
        pos = reader.pos - 1
        return gzip.compress(raw[:pos] + b"\x01\x00\x06locked\x01" + raw[pos:], mtime=0)
    raise ValueError(f"{source}: missing map data compound")


def allocate_ids(pixel_keys, previous, destination_data):
    assignments = {}
    reserved = set()
    if previous:
        for row in csv_rows(previous, ("pixel_key", "destination_map_id")):
            key, dest = row["pixel_key"], int(row["destination_map_id"])
            if (not re.fullmatch("[0-9a-f]{64}", key) or not 1000 <= dest <= 2**31-1
                    or dest in reserved or key in assignments):
                raise ValueError(f"Invalid or duplicate assignment in {previous}: {key}, {dest}")
            assignments[key] = dest
            reserved.add(dest)
    if destination_data:
        occupied = index_dats(destination_data)
        for key in pixel_keys & assignments.keys():
            dest = assignments[key]
            if dest in occupied:
                paths = occupied[dest]
                if len(paths) != 1 or pixel_signature(read_map_data(paths[0])["colors"])[0] != key:
                    raise ValueError(f"Previous destination ID {dest} conflicts with the destination world")
        reserved.update(occupied)
    dest = 1000
    for key in sorted(pixel_keys - assignments.keys()):
        while dest in reserved:
            dest += 1
        if dest > 2**31-1:
            raise ValueError("No destination map IDs remain")
        assignments[key] = dest
        dest += 1
    return assignments


def box_artworks(layouts, review, by_id):
    groups = {layout_key(art): art for art in layouts}
    merges = review["merged_artworks"] if review["format"].endswith("-v4") else []
    consumed = set()
    selected = []
    for art in merges:
        parts = [p["layout_key"] for p in art["parts"]]
        if len(set(parts)) != len(parts) or len(parts) < 2 or set(parts) - groups.keys():
            raise ValueError(f"Invalid source groups for merge {art['title']}")
        consumed.update(parts)
        selected.append((parts, art))
    for decision in review["decisions"]:
        key = decision["layout_key"]
        if decision["decision"] == "Keep artwork" and key not in consumed:
            selected.append(([key], groups[key]))
    result = []
    for parts, art in selected:
        width, height = art["width"], art["height"]
        tiles, occupied = [], set()
        for tile in sorted(art["tiles"], key=lambda t: (t["row"], t["column"])):
            r, c = tile["row"], tile["column"]
            if not (1 <= r <= height and 1 <= c <= width) or (r, c) in occupied:
                raise ValueError(f"Invalid placement at row {r}, column {c} of {art['title']}")
            occupied.add((r, c))
            record = by_id.get(tile["original_map_id"])
            if record is None:
                continue
            tiles.append(dict(row=r, column=c, original_map_id=record["original_map_id"],
                              destination_map_id=record["destination_map_id"], chosen_name=record["chosen_name"],
                              clockwise_quarters=tile["clockwise_quarters"]))
        result.append(dict(title=art["title"], source_groups=[groups[k]["id"] for k in parts],
                           width=width, height=height, gaps=width*height-len(tiles), tiles=tiles))
    return result


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def finalize(manifest, gallery, decisions, out, data_dir=None, review_csv=None, previous=None, destination_data=None):
    assets = out.with_name(out.stem + "-assets")
    if out.exists() or assets.exists():
        raise ValueError(f"Choose a fresh path for the manifest and its assets: {out}")
    fields = ("original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path")
    rows = list(csv_rows(manifest, fields))
    keys = {int(row["original_map_id"]): row["pixel_key"] for row in rows}
    if len(keys) != len(rows):
        raise ValueError("Duplicate original map IDs in kept manifest.")
    removed, recovered, names, layouts, review = reviewed_changes(gallery, decisions, keys)
    eligible = [dict(original_map_id=int(r["original_map_id"]), chosen_name=r["chosen_name"],
                     pixel_key=r["pixel_key"], source=manifest.parent / r["dat_path"])
                for r in rows if int(r["original_map_id"]) not in removed]
    eligible.extend(recover_rows(recovered, names, data_dir, review_csv))
    reps = {}
    for row in eligible:
        data = read_map_data(row["source"])
        key = pixel_signature(data["colors"])[0]
        if row["pixel_key"] is not None and key != row["pixel_key"]:
            raise ValueError(f"Map {row['original_map_id']}: original DAT pixels differ from the review")
        row["pixel_key"] = key
        choice = (not data.get("locked"), row["original_map_id"], row["source"])
        if key not in reps or choice < reps[key]:
            reps[key] = choice
    assignments = allocate_ids(reps.keys(), previous, destination_data)
    by_id = {}
    for row in sorted(eligible, key=lambda r: r["original_map_id"]):
        key, i = row["pixel_key"], row["original_map_id"]
        dest = assignments[key]
        by_id[i] = dict(original_map_id=i, chosen_name=row["chosen_name"], pixel_key=key,
                        destination_map_id=dest, representative_original_map_id=reps[key][1],
                        dat_path=f"{assets.name}/dats/map_{dest}.dat", png_path=f"{assets.name}/pngs/map_{dest}.png")
    artworks = box_artworks(layouts, review, by_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        stage = Path(scratch) / "assets"
        (stage / "dats").mkdir(parents=True)
        (stage / "pngs").mkdir()
        for key, (_, _, source) in reps.items():
            dest = assignments[key]
            dat = stage / "dats" / f"map_{dest}.dat"
            dat.write_bytes(locked_dat(source))
            data = read_map_data(dat)
            if data.get("locked") != 1 or pixel_signature(data["colors"])[0] != key:
                raise ValueError(f"Destination map {dest} failed lock or pixel verification")
            (stage / "pngs" / f"map_{dest}.png").write_bytes(render_pixels(data["colors"]))
        write_csv(stage / "pixel-ids.csv", ("pixel_key", "destination_map_id"),
                  (dict(pixel_key=k, destination_map_id=v) for k, v in sorted(assignments.items(), key=lambda p: p[1])))
        (stage / "artworks.json").write_text(json.dumps(dict(format="framed-map-atlas/box-artworks-v1",
            artworks=artworks), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        staged_manifest = Path(scratch) / "manifest.csv"
        write_csv(staged_manifest, (*fields, "destination_map_id", "representative_original_map_id"), by_id.values())
        out.touch(exist_ok=False)
        try:
            stage.rename(assets)
            try:
                staged_manifest.replace(out)
            except OSError:
                assets.rename(stage)
                raise
        except OSError:
            out.unlink()
            raise
    return len(by_id), len(reps), len(artworks), sum(len(a["tiles"]) for a in artworks)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path, help="Kept manifest.csv")
    parser.add_argument("--gallery", required=True, type=Path, help="Reviewed artwork gallery directory")
    parser.add_argument("--decisions", required=True, type=Path, help="Final exported artwork decisions")
    parser.add_argument("--out", required=True, type=Path, help="New migration manifest CSV")
    parser.add_argument("--data-dir", type=Path, help="Original map DAT directory for recoveries")
    parser.add_argument("--review-csv", type=Path, help="Sorter CSV for recovered names and pixel keys")
    parser.add_argument("--previous-mapping", type=Path, help="Preserve assignments from a previous pixel-ids.csv")
    parser.add_argument("--destination-data", type=Path, help="Skip occupied IDs in the destination map DAT directory")
    args = parser.parse_args()
    try:
        originals, images, arts, placements = finalize(args.manifest, args.gallery, args.decisions, args.out,
            args.data_dir, args.review_csv, args.previous_mapping, args.destination_data)
    except (OSError, ValueError, TypeError, KeyError, csv.Error) as exc:
        parser.exit(1, f"Manifest export failed: {exc}\n")
    print(f"{originals:,} original IDs mapped to {images:,} destination IDs. "
          f"{arts:,} artworks with {placements:,} placements. {args.out}")


if __name__ == "__main__":
    main()
