"""Build a portable gallery of candidate displays from frames.csv and a kept-maps export."""

import argparse
import csv
import hashlib
import re
import unicodedata
import json
import tempfile
from collections import Counter
from functools import lru_cache
from pathlib import Path

from artwork import infer, title_key
from map_assets import footprint, index_dats, read_map_nbt


def csv_rows(path, required):
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        missing = set(required).difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path}: missing columns {', '.join(sorted(missing))}")
        for row in reader:
            if None in row or any(row[k] is None for k in required):
                raise ValueError(f"{path}, line {reader.line_num}: incomplete or extra fields")
            yield row


def read_manifest(path):
    kept = {}
    for row in csv_rows(path, ("original_map_id", "pixel_key", "chosen_name", "png_path")):
        i = int(row["original_map_id"])
        if i in kept:
            raise ValueError(f"{path}: duplicate map ID {i}")
        kept[i] = row
    return kept


def read_frames(path, kept, world):
    fields = ("frame", "world", "dimension", "map_id", "name", "x", "y", "z", "position_source", "facing", "rotation")
    frames = {}
    worlds = set()
    for row in csv_rows(path, fields):
        worlds.add(row["world"])
        if world is not None and row["world"] != world:
            continue
        f = {key: row[key] for key in fields}
        for key in ("frame", "map_id", "x", "y", "z", "rotation"):
            f[key] = int(row[key]) if row[key] else None
        if f["frame"] is None or f["frame"] in frames:
            raise ValueError(f"{path}: missing or duplicate frame number {f['frame']}")
        if f["facing"] not in ("up", "down", "north", "south", "east", "west") or any(f[k] is None for k in ("x","y","z")):
            f["facing"] = None
        f["name"] = f["name"] or kept.get(f["map_id"], {}).get("chosen_name", "")
        frames[f["frame"]] = f
    if world is not None and world not in worlds:
        raise ValueError(f"{path}: world {world!r} absent. Available: {', '.join(sorted(worlds))}")
    return frames


def reconstruct(kept, frames):
    scan = infer(frames.values())
    layouts = {}
    seen = {f["map_id"] for f in frames.values()}
    singletons, grouped = set(), set()
    for g in scan["groups"]:
        tiles = g["tiles"]
        ids = {t["map_id"] for t in tiles if t["map_id"] in kept}
        if not ids:
            continue
        if len(tiles) == 1:
            singletons.update(ids)
            continue
        grouped.update(ids)
        cells = []
        for t in tiles:
            i, rotation = t["map_id"], t["rotation"]
            # The gallery projects both floors and ceilings with north at the top.
            turns = ((rotation or 0) + (2 if g["facing"] == "down" else 0)) % 4
            cells.append((t["row"], t["column"], i, turns, rotation is None))
        key = tuple(cells)
        if key not in layouts:
            grid = []
            for r, c, i, turns, unknown in cells:
                record = kept.get(i)
                grid.append(dict(row=r, column=c, original_map_id=i, kept=record is not None,
                                 chosen_name=record["chosen_name"] if record else "",
                                 pixel_key=record["pixel_key"] if record else None,
                                 clockwise_quarters=turns, rotation_unknown=unknown))
            names = [t["chosen_name"] for t in grid if t["chosen_name"]] or [t["name"] for t in tiles if t["name"]]
            keys = {title_key(name) for name in names}
            title = ""
            if names and len(set(names)) == 1:
                title = names[0]
            elif len(keys) == 1:
                title = next(iter(keys)).title()
            kept_count = sum(t["kept"] for t in grid)
            layouts[key] = dict(id=g["group"], title=title, width=g["width"], height=g["height"],
                                kept_placements=kept_count, excluded=len(grid)-kept_count, gaps=g["empty_cells"],
                                tiles=grid, displays=[])
        for tile, observation in zip(layouts[key]["tiles"], tiles):
            name = observation["name"]
            if name and name != tile["chosen_name"]:
                aliases = tile.setdefault("observed_names", [])
                if name not in aliases:
                    aliases.append(name)
        display = {k: g[k] for k in ("world", "dimension", "facing", "confidence", "review_notes")}
        display["placements"] = [dict(row=t["row"], column=t["column"], **{
            k: frames[t["frame"]][k] for k in ("frame", "x", "y", "z", "rotation")
        }) for t in tiles]
        layouts[key]["displays"].append(display)
    unmatched = {i: ("no frame observation" if i not in seen else
                     "observed as a single tile" if i in singletons else "no usable placement")
                 for i in sorted(kept.keys() - grouped)}
    unplaced = [dict(f, original_map_id=frames[f["frame"]]["map_id"]) for f in scan["unplaced"]
                if frames[f["frame"]]["map_id"] in kept]
    return list(layouts.values()), unmatched, unplaced


CANVAS_FIELDS = ("dimension", "center_x", "center_z", "scale")


def annotate_canvases(layouts, kept, manifest, map_index=None, data_dir=None):
    ids = {t["original_map_id"] for art in layouts for t in art["tiles"]}
    centers = {}
    if map_index is not None:
        for row in csv_rows(map_index, ("map_id", *CANVAS_FIELDS)):
            i = int(row["map_id"])
            if i not in ids or not all(row[k] for k in CANVAS_FIELDS):
                continue
            if i in kept and row.get("pixel_sha256") and row["pixel_sha256"] != kept[i]["pixel_key"]:
                raise ValueError(f"{map_index}: pixel key for map {i} differs from the kept manifest")
            centers[i] = (row["dimension"], int(row["center_x"]), int(row["center_z"]), int(row["scale"]))
    originals = index_dats(data_dir) if data_dir is not None else {}
    for i in ids - centers.keys():
        dat_path = kept.get(i, {}).get("dat_path")
        path = manifest.parent / dat_path if dat_path else None
        if path is None or not path.is_file():
            matches = originals.get(i, [])
            if len(matches) != 1:
                continue
            path, = matches
        try:
            root = read_map_nbt(path, {"data": dict.fromkeys(("dimension", "xCenter", "zCenter", "scale"))})
        except (ValueError, OSError):
            continue
        data = root.get("data")
        fp = footprint(data) if isinstance(data, dict) else None
        if fp is not None:
            centers[i] = (str(fp[0]), *fp[1:])
    for art in layouts:
        placed, excluded = Counter(), Counter()
        for tile in art["tiles"]:
            center = centers.get(tile["original_map_id"])
            tile["source_canvas"] = dict(zip(CANVAS_FIELDS, center)) if center is not None else None
            if center is not None:
                placed[center] += 1
                if not tile["kept"]:
                    excluded[center] += 1
        art["source_canvases"] = [
            dict(zip(CANVAS_FIELDS, center), placements=count, excluded=excluded[center])
            for center, count in placed.most_common()
        ]


def canvas_label(canvas):
    return (f"{canvas['dimension']} · X {canvas['center_x']} / Z {canvas['center_z']}"
            f" · scale {canvas['scale']}")


def make_previews(layouts, kept, manifest, folder):
    from PIL import Image, ImageDraw

    @lru_cache(maxsize=128)
    def tile(i):
        path = manifest.parent / kept[i]["png_path"]
        try:
            with Image.open(path) as image:
                if image.size != (128, 128) or image.format != "PNG":
                    raise ValueError("expected a 128 by 128 PNG")
                return image.convert("RGBA")
        except (OSError, ValueError) as exc:
            raise ValueError(f"map {i}, {path}: {exc}") from exc

    rotations = (None, Image.Transpose.ROTATE_270, Image.Transpose.ROTATE_180, Image.Transpose.ROTATE_90)
    for art in layouts:
        w, h = art["width"], art["height"]
        # Limit the raster for large displays, leaving the placement data at full resolution.
        scale = min(128, 1024 / max(w, h))
        canvas = Image.new("RGBA", (max(1, round(w*scale)), max(1, round(h*scale))), "#5b6571")
        draw = ImageDraw.Draw(canvas)
        for t in art["tiles"]:
            c, r = t["column"]-1, t["row"]-1
            x, y = round(c*scale), round(r*scale)
            width, height = round((c+1)*scale)-x, round((r+1)*scale)-y
            if width < 1 or height < 1:
                continue
            if not t["kept"]:
                box = (x, y, x+width-1, y+height-1)
                draw.rectangle(box, fill="#805526")
                draw.line(box, fill="#ffc876", width=max(1, width//24))
                draw.line((box[2], y, x, box[3]), fill="#ffc876", width=max(1, width//24))
                continue
            image = tile(t["original_map_id"])
            method = rotations[t["clockwise_quarters"]]
            if method is not None:
                image = image.transpose(method)
            if image.size != (width, height):
                image = image.resize((width, height), Image.Resampling.NEAREST)
            canvas.paste(image, (x,y))
        art["preview"] = "previews/" + art["id"] + ".png"
        canvas.save(folder / art["preview"])


# Credit formats found in display titles, e.g. "Dragon by Alex 0-2",
# "Dragon - Alex" and "Dragon 0-2 Alex & Sam". Preserve joint credits.
GRID_COORD = r"\d+\s*[-_,]\s*\d+"
AFTER_COORD = re.compile(GRID_COORD + r"[, _]+([^\d\s].*)$")
DASH_CREDIT = re.compile(r"\s+[-–—]\s*([^\d\s].*)$")
TRAILING_COORD = re.compile(r"\s*[(\[]?" + GRID_COORD + r"[)\]]?\s*$")


def artist_credit(name):
    name = unicodedata.normalize("NFKC", name).strip()
    marked = re.split(r"\bby\s*[:：]\s*|\bartists?\s*[:：]\s*|\bby\s+", name, flags=re.I)
    if len(marked) > 1:
        credit = marked[-1]
    else:
        match = AFTER_COORD.search(name)
        if not match or not name[:match.start()].strip(" _-"):
            match = DASH_CREDIT.search(name)
        if not match:
            return ""
        credit = match[1]
    credit = TRAILING_COORD.sub("", credit).strip(" -–—")
    return credit if any(c.isalpha() for c in credit) else ""


def gallery_card(art):
    tiles, names, signature = [], set(), []
    canvases = art.get("source_canvases", [])
    canvas_ids = {canvas_label(c): i for i, c in enumerate(canvases)}
    for t in art["tiles"]:
        aliases = t.get("observed_names", [])
        names.update([t["chosen_name"], *aliases])
        canvas = canvas_ids[canvas_label(t["source_canvas"])] if t.get("source_canvas") else None
        tiles.append([t["row"], t["column"], t["original_map_id"], t["chosen_name"], t["kept"], canvas, aliases])
        signature.append([t["row"], t["column"], t["original_map_id"], t["clockwise_quarters"],
                          t["rotation_unknown"], t["kept"], t["pixel_key"]])
    # Scan-local G numbers can change. Use the actual placement data to identify a decision.
    key = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()
    displays, notes = [], set()
    for d in art["displays"]:
        p = d["placements"][0]
        displays.append(f"{d['world']} / {d['dimension']} · {p['x']}, {p['y']}, {p['z']} · {d['facing']} · {d['confidence']}")
        notes.update(d["review_notes"])
    if any(t["rotation_unknown"] for t in art["tiles"]):
        notes.add("A missing rotation was previewed at its default angle.")
    names.add(art["title"])
    artists = sorted({credit for n in names if (credit := artist_credit(n))}, key=lambda s: (s.casefold(), s))
    return dict(key=key, id=art["id"], title=art["title"], width=art["width"], height=art["height"],
                excluded=art["excluded"], gaps=art["gaps"], kept=art["kept_placements"], preview=art["preview"],
                artists=artists, tiles=tiles, displays=displays, notes=sorted(notes),
                canvases=[[canvas_label(c), c["placements"], c["excluded"]] for c in canvases])


def gallery(layouts, unmatched, folder):
    ranked = sorted(layouts, key=lambda a: (bool(a["excluded"] or a["gaps"]), -len(a["displays"]), a["id"]))
    cards = [gallery_card(a) for a in ranked]
    collection = hashlib.sha256("".join(sorted(a["key"] for a in cards)).encode()).hexdigest()
    payload = json.dumps(dict(collection=collection, cards=cards, ungrouped=len(unmatched)),
                         ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    template = Path(__file__).with_name("artwork_review.html").read_text(encoding="utf-8")
    (folder / "review.html").write_text(template.replace("__ARTWORK_DATA__", payload), encoding="utf-8")


def refresh_gallery(folder):
    with (folder / "layouts.json").open(encoding="utf-8") as stream:
        layouts = json.load(stream)["layouts"]
    unmatched = list(csv_rows(folder / "ungrouped.csv", ("original_map_id",)))
    with tempfile.TemporaryDirectory(dir=folder, prefix=".review-") as tmp:
        stage = Path(tmp)
        gallery(layouts, unmatched, stage)
        (stage / "review.html").replace(folder / "review.html")


def build(manifest, frames_csv, out, world=None, map_index=None, data_dir=None):
    if out.exists():
        raise ValueError(f"output already exists: {out}. Choose a new folder.")
    kept = read_manifest(manifest)
    frames = read_frames(frames_csv, kept, world)
    layouts, unmatched, unplaced = reconstruct(kept, frames)
    annotate_canvases(layouts, kept, manifest, map_index, data_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".artwork-") as tmp:
        stage = Path(tmp) / "gallery"
        (stage / "previews").mkdir(parents=True)
        make_previews(layouts, kept, manifest, stage)
        with (stage / "layouts.json").open("w", encoding="utf-8") as f:
            json.dump(dict(layouts=layouts, unplaced_frames=unplaced), f, ensure_ascii=False, separators=(",", ":"))
        with (stage / "ungrouped.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(("original_map_id", "chosen_name", "pixel_key", "reason"))
            for i, reason in unmatched.items():
                writer.writerow((i, kept[i]["chosen_name"], kept[i]["pixel_key"], reason))
        gallery(layouts, unmatched, stage)
        stage.rename(out)
    return len(layouts), sum(len(a["displays"]) for a in layouts), len(unmatched)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--manifest", type=Path, help="manifest.csv from export_kept.py")
    mode.add_argument("--refresh-gallery", type=Path, help="Regenerate review.html in an existing artwork-review folder")
    parser.add_argument("--frames", type=Path, help="frames.csv from the existing world scan")
    parser.add_argument("--out", type=Path, help="New gallery folder")
    parser.add_argument("--world", help="Use only this exact world key from frames.csv")
    sources = parser.add_mutually_exclusive_group()
    sources.add_argument("--map-index", type=Path, help="Full sorter map-index.csv from the same save, including excluded IDs")
    sources.add_argument("--data-dir", type=Path, help="Original folder of map DATs, including excluded IDs")
    args = parser.parse_args()
    try:
        if args.refresh_gallery is not None:
            if any((args.frames, args.out, args.world, args.map_index, args.data_dir)):
                parser.error("use --refresh-gallery by itself")
            refresh_gallery(args.refresh_gallery)
            print(args.refresh_gallery / "review.html")
            return
        if not args.frames or not args.out:
            parser.error("--manifest requires --frames and --out")
        count, displays, unmatched = build(args.manifest, args.frames, args.out, args.world, args.map_index, args.data_dir)
    except ImportError as exc:
        parser.exit(1, f"{exc}. Install Pillow with: python -m pip install 'Pillow>=9.1'\n")
    except (OSError, ValueError, csv.Error) as exc:
        parser.exit(1, f"Reconstruction failed: {exc}\n")
    print(f"{count:,} candidate layouts from {displays:,} displays; {unmatched:,} kept IDs outside layouts.")
    print(args.out / "review.html")


if __name__ == "__main__":
    main()
