"""Build the public collection from a Framed Map Atlas final migration."""
import argparse
import csv
import hashlib
import json
import re
import shutil
import tempfile
from collections import defaultdict
from pathlib import Path

from PIL import Image


SITE_FILES = ("index.html", "gallery.css", "gallery.js", "packing.js", "download.js",
              "builder.html", "import-world.cjs", "IMPORT.md")


def migration_inputs(source):
    """Atlas writes <stem>.csv beside <stem>-assets/artworks.json."""
    if source.is_dir():
        matches = sorted(p for p in source.glob("*.csv")
                         if (p.parent / (p.stem + "-assets")).is_dir())
        if len(matches) != 1:
            found = ", ".join(p.name for p in matches) or "none"
            raise ValueError(f"Expected one finalized manifest in {source}; found {found}. "
                             "Pass the exact finalized CSV with --migration or check the finalizer output.")
        manifest, = matches
    elif source.is_file() and source.suffix.lower() == ".csv":
        manifest = source
    else:
        raise ValueError(f"Migration directory or finalized CSV does not exist: {source}")
    layouts = manifest.parent / (manifest.stem + "-assets") / "artworks.json"
    if not layouts.is_file():
        raise ValueError(f"Missing artwork layouts for {manifest.name}: {layouts}")
    return manifest, layouts


def identity(value):
    data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()[:24]


def tile_title(title, tiles, group):
    if title.strip() and not re.fullmatch(r"G\d+", title.strip(), re.I):
        return title
    for t in tiles:
        if t["name"].strip():
            return t["name"]
    return title or f"Artwork {group}"


def generate(manifest, layouts, folder):
    originals, assets, aliases = {}, {}, defaultdict(list)
    with manifest.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f, strict=True):
            i, d = int(row["original_map_id"]), int(row["destination_map_id"])
            if d < 100_000:
                raise ValueError(f"Map {i} uses destination ID {d}. Re-finalize with IDs starting at 100,000 before building this gallery.")
            value = (d, row["chosen_name"])
            paths = (row["pixel_key"], manifest.parent / row["png_path"], manifest.parent / row["dat_path"])
            if i in originals or d > 2147483647 or assets.setdefault(d, paths) != paths:
                raise ValueError(f"Inconsistent manifest entry at original ID {i}")
            originals[i] = value
            aliases[value].append(i)
    if len({v[0] for v in assets.values()}) != len(assets):
        raise ValueError("Expected one destination ID per pixel hash")
    review = json.loads(layouts.read_bytes())
    if review["format"] != "framed-map-atlas/box-artworks-v1":
        raise ValueError("Unknown artwork layout format")
    pngs = folder / "images"
    pngs.mkdir()
    blocks = folder / "maps"
    blocks.mkdir()
    locations = {}
    # Group nearby IDs into small downloadable files; a selection ZIP contains only its own DATs.
    sorted_ids = sorted(assets)
    for start in range(0, len(sorted_ids), 64):
        block = bytearray()
        block_id = start // 64
        for d in sorted_ids[start:start+64]:
            key, png, dat = assets[d]
            data = dat.read_bytes()
            locations[d] = [block_id, len(block), len(data), hashlib.sha256(data).hexdigest()]
            block.extend(data)
            (pngs / f"{d}.png").write_bytes(png.read_bytes())
        (blocks / f"{block_id}.bin").write_bytes(block)
    cards, grouped, used = {}, set(), set()
    for art in review["artworks"]:
        width, height, title = art["width"], art["height"], art["title"]
        tiles, signature, seen = [], [], set()
        for t in sorted(art["tiles"], key=lambda t: (t["row"], t["column"])):
            i, d, name = t["original_map_id"], t["destination_map_id"], t["chosen_name"]
            r, c, q = t["row"], t["column"], t["clockwise_quarters"]
            if originals.get(i) != (d, name) or (r,c) in seen or not (1 <= r <= height and 1 <= c <= width and q in range(4)):
                raise ValueError(f"Invalid placement in {art['source_groups']} at {r}, {c}")
            used.add(i)
            grouped.add((d, name))
            seen.add((r, c))
            tiles.append(dict(id=d, name=name, row=r, col=c, turn=q))
            signature.append((r,c,d,name,q))
        if not tiles:
            continue
        key = "a-" + identity([title, width, height, signature])
        if key in cards:
            cards[key]["groups"].extend(art["source_groups"])
            continue
        size = max(1, min(128, 832 // max(width, height)))
        preview = Image.new("RGBA", (size*width, size*height))
        for t in tiles:
            with Image.open(pngs / f"{t['id']}.png") as src:
                img = src.convert("RGBA").rotate(-90*t["turn"]).resize((size,size), Image.Resampling.NEAREST)
            preview.paste(img, ((t["col"]-1)*size,(t["row"]-1)*size))
        preview.save(pngs / f"{key}.webp", lossless=True, method=2)
        cards[key] = dict(key=key, kind="art", title=tile_title(title,tiles,art["source_groups"][0]),
                          originalTitle=title, groups=list(art["source_groups"]), width=width, height=height,
                          image=f"{key}.webp", tiles=tiles)
    # Membership is decided on original IDs before aliases are collapsed.
    for (d, name), ids in aliases.items():
        if all(i in used for i in ids):
            continue
        key = "s-" + identity([d,name])
        cards[key] = dict(key=key, kind="single", title=name or f"Map #{d}", width=1, height=1,
                          image=f"{d}.png", overlap=(d,name) in grouped,
                          tiles=[dict(id=d,name=name,row=1,col=1,turn=0)])
    catalog = dict(version=identity([list(cards), locations]), cards=list(cards.values()), maps=locations,
                   aliases=[[d,name,ids] for (d,name),ids in aliases.items()])
    (folder / "catalog.js").write_text("window.ATLAS=" + json.dumps(catalog,ensure_ascii=False,separators=(",",":")) + ";\n",encoding="utf-8")
    print(f"{len(cards):,} cards, {len(cards)-sum(c['kind']=='single' for c in cards.values()):,} grouped artworks, "
          f"{len(assets):,} unique map DATs.")


def build(manifest, layouts, out, *, site):
    if out.exists():
        raise ValueError(f"Choose a fresh output directory: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        stage = Path(scratch) / "output"
        collection = stage / "collection" if site else stage
        collection.mkdir(parents=True)
        if site:
            for name in SITE_FILES:
                shutil.copyfile(Path(__file__).with_name(name), stage / name)
            (stage / ".nojekyll").touch()
        generate(manifest, layouts, collection)
        stage.rename(out)
    print(f"{'Website' if site else 'Collection'} ready: {out.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--migration", type=Path, help="Build a complete site from an Atlas migration folder or finalized CSV")
    inputs.add_argument("--manifest", type=Path, help="Build only the collection from this manifest and --artworks")
    parser.add_argument("--artworks", type=Path, help="Explicit artworks.json for collection-only mode")
    parser.add_argument("--out", type=Path, help="Fresh output directory (default: website-dist for a site, collection beside this script otherwise)")
    args = parser.parse_args()
    if bool(args.manifest) != bool(args.artworks):
        parser.error("--manifest and --artworks must be used together")
    try:
        if args.migration:
            manifest, layouts = migration_inputs(args.migration)
            build(manifest, layouts, args.out or Path("website-dist"), site=True)
        else:
            build(args.manifest, args.artworks, args.out or Path(__file__).with_name("collection"), site=False)
    except (OSError, KeyError, ValueError, csv.Error) as exc:
        parser.exit(1, f"Gallery build failed: {exc}\n")


if __name__ == "__main__":
    main()
