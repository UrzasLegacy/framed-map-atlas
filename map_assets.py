"""Export Java saved map data and render its 128-by-128 indexed pixel array."""

import csv
import hashlib
import json
import re
import shutil
import struct
import zlib
from collections import defaultdict
from pathlib import Path

from anvil import LIMIT, NBT, decompress


# Java 1.21 base RGB values, indexed by the upper six bits of a saved pixel.
# Source: https://github.com/cerus/minecraft-map-colors/blob/main/mappings.json
BASE_COLORS = (
    0x000000, 0x7FB238, 0xF7E9A3, 0xC7C7C7, 0xFF0000, 0xA0A0FF, 0xA7A7A7, 0x007C00,
    0xFFFFFF, 0xA4A8B8, 0x976D4D, 0x707070, 0x4040FF, 0x8F7748, 0xFFFCF5, 0xD87F33,
    0xB24CD8, 0x6699D8, 0xE5E533, 0x7FCC19, 0xF27FA5, 0x4C4C4C, 0x999999, 0x4C7F99,
    0x7F3FB2, 0x334CB2, 0x664C33, 0x667F33, 0x993333, 0x191919, 0xFAEE4D, 0x5CDBD5,
    0x4A80FF, 0x00D93A, 0x815631, 0x700200, 0xD1B1A1, 0x9F5224, 0x95576C, 0x706C8A,
    0xBA8524, 0x677535, 0xA04D4E, 0x392923, 0x876B62, 0x575C5C, 0x7A4958, 0x4C3E5C,
    0x4C3223, 0x4C522A, 0x8E3C2E, 0x251610, 0xBD3031, 0x943F61, 0x5C191D, 0x167E86,
    0x3A8E8C, 0x562C3E, 0x14B485, 0x646464, 0xD8AF93, 0x7FA796,
)
PALETTE = bytes((color >> shift & 255) * shade // 255
                for color in BASE_COLORS for shade in (180, 220, 255, 135) for shift in (16, 8, 0))


def png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def load_kept_ids(path):
    records = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, strict=True)
        try:
            if next(reader, None) != ["map_id", "pixel_key", "decision", "chosen_name"]:
                raise ValueError(f"{path}: expected the four-column review CSV")
            for row in reader:
                if not row:
                    continue
                if len(row) != 4:
                    raise ValueError(f"{path}, line {reader.line_num}: expected four columns")
                raw_id, key, decision, title = row
                if not re.fullmatch(r"-?[0-9]+", raw_id) or not key:
                    raise ValueError(f"{path}, line {reader.line_num}: invalid map ID or pixel key")
                if decision not in ("Keep", "Terrain", "Unsure") and not (decision == "" and title):
                    raise ValueError(f"{path}, line {reader.line_num}: invalid review decision")
                map_id = int(raw_id)
                record = (key, decision, title)
                if records.setdefault(map_id, record) != record:
                    raise ValueError(f"{path}, line {reader.line_num}: conflicting review rows for map {map_id}")
        except csv.Error as exc:
            raise ValueError(f"{path}, line {reader.line_num}: {exc}") from exc
    kept = frozenset(i for i, (_, decision, _) in records.items() if decision == "Keep")
    if not kept:
        raise ValueError(f"{path}: review has no Keep decisions")
    return kept


def index_dats(folder):
    files = defaultdict(list)
    for path in folder.iterdir():
        match = re.fullmatch(r"(?:map_)?(-?[0-9]+)\.dat", path.name, re.I)
        if match and path.is_file():
            files[int(match[1])].append(path)
    return files


def canonical_palette():
    # Treat transparent entries and duplicate RGB entries as identical visible pixels.
    first = {}
    table = bytearray(range(256))
    for i in range(len(PALETTE) // 3):
        color = None if i < 4 else PALETTE[3*i:3*i+3]
        table[i] = first.setdefault(color, i)
    return bytes(table)


VISIBLE = canonical_palette()


def pixel_signature(colors):
    """Return the gallery key and classification for validated Java 1.21 colors."""
    pixels = colors.translate(VISIBLE)
    kind = "image"
    if pixels.count(pixels[:1]) == len(pixels):
        kind = "blank" if pixels[0] == 0 else "solid"
    return hashlib.sha256(pixels).hexdigest(), kind


def read_map_nbt(source, selection):
    with source.open("rb") as stream:
        size = stream.seek(0, 2)
        if size > LIMIT:
            raise ValueError("map file exceeds the 64 MiB decoding limit")
        stream.seek(0)
        data = stream.read(size + 1)
    if len(data) > LIMIT:
        raise ValueError("map file exceeds the 64 MiB decoding limit")
    if data.startswith(b"\x1f\x8b"):
        data = decompress(1, data)
    return NBT(data).chunk(selection)


def validate_pixels(pixels):
    if not isinstance(pixels, bytes) or len(pixels) != 128 * 128:
        raise ValueError("expected a 16,384-byte data.colors array")
    if max(pixels) >= len(PALETTE) // 3:
        raise ValueError("map uses a color outside the vanilla Java 1.21 palette")


def read_map_data(source):
    root = read_map_nbt(source, {"data": {"colors": bytes, "dimension": None, "xCenter": None,
                                         "zCenter": None, "scale": None}})
    saved = root.get("data")
    validate_pixels(saved.get("colors") if isinstance(saved, dict) else None)
    return saved


def read_pixels(source):
    return read_map_data(source)["colors"]


def render(source):
    pixels = read_pixels(source)
    rows = b"".join(b"\0" + pixels[i:i + 128] for i in range(0, len(pixels), 128))
    header = struct.pack(">IIBBBBB", 128, 128, 8, 3, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) + png_chunk(b"PLTE", PALETTE)
            + png_chunk(b"tRNS", bytes(4)) + png_chunk(b"IDAT", zlib.compress(rows)) + png_chunk(b"IEND", b""))


def data_folders(root, world, overrides):
    world_path = Path(world)
    if world_path.anchor or ".." in world_path.parts:
        raise ValueError(f"expected a backup-relative world path, got {world!r}")
    if world in overrides:
        return [(overrides[world], "map_"), (overrides[world], "")]
    save = root / world_path
    saves = [save]
    # Split Bukkit worlds commonly share the sibling overworld's map storage.
    for suffix in ("_nether", "_the_end"):
        if save != root and save.name.endswith(suffix) and save.name[:-len(suffix)]:
            saves.append(save.with_name(save.name[:-len(suffix)]))
    return [(s / folder, prefix) for s in saves for folder, prefix in
            (("data", "map_"), ("data/minecraft/maps", ""))]


def selection_summary(map_ids, found_ids):
    missing = sorted(map_ids.difference(found_ids))
    return {"requested_ids": len(map_ids), "matched_ids": len(map_ids) - len(missing), "unmatched_ids": missing}


def export_maps(root, out, records, mode, overrides, *, map_ids=None):
    if not isinstance(records, list):
        raise ValueError("expected a list of map records in map-names.json")
    by_world = {}
    for record in records:
        if (not isinstance(record, dict) or not isinstance(record.get("world"), str)
                or type(record.get("map_id")) is not int):
            raise ValueError("map-names.json contains a record without a world and integer map ID")
        if map_ids is None or record["map_id"] in map_ids:
            by_world.setdefault(record["world"], {})[record["map_id"]] = record.get("name")

    for world, folder in overrides.items():
        if map_ids is not None and world not in by_world:
            continue
        if world not in by_world or not folder.is_dir():
            raise ValueError(f"check --map-source world key and directory: {world!r}, {folder}")

    manifest = []
    counts = {"maps": 0, "png": 0, "dat": 0, "incomplete": 0}
    for number, (world, maps) in enumerate(sorted(by_world.items()), 1):
        folders = data_folders(root, world, overrides)
        existing = [(folder, prefix) for folder, prefix in folders if folder.is_dir()]
        destination = out / "maps" / f"world-{number:03d}"
        destination.mkdir(parents=True, exist_ok=True)
        for map_id, name in sorted(maps.items()):
            entry = {"world": world, "map_id": map_id, "name": name, "source": None, "dat": None, "png": None}
            manifest.append(entry)
            counts["maps"] += 1
            try:
                candidates = [p for folder, prefix in existing if (p := folder / f"{prefix}{map_id}.dat").is_file()]
                if len(candidates) != 1:
                    entry["status"] = "ambiguous" if candidates else "missing"
                    entry["checked_folders"] = sorted({str(p) for p, _ in folders})
                    if candidates:
                        entry["candidates"] = [str(p) for p in candidates]
                    continue
                source, = candidates
                entry["source"] = str(source)
                if mode in ("dat", "both"):
                    dat = destination / f"map_{map_id}.dat"
                    shutil.copyfile(source, dat)
                    entry["dat"] = dat.relative_to(out).as_posix()
                    counts["dat"] += 1
                    source = dat
                if mode in ("png", "both"):
                    png = destination / f"map_{map_id}.png"
                    png.write_bytes(render(source))
                    entry["png"] = png.relative_to(out).as_posix()
                    counts["png"] += 1
                entry["status"] = "exported"
            except (ValueError, OSError) as exc:
                entry.update(status="error", error=str(exc))
                for extension in ("dat", "png"):
                    if entry[extension] is None:
                        (destination / f"map_{map_id}.{extension}").unlink(missing_ok=True)
            finally:
                if entry.get("status") != "exported":
                    counts["incomplete"] += 1
    report = {"counts": counts, "maps": manifest}
    if map_ids is not None:
        report["selection"] = selection_summary(map_ids, (r["map_id"] for r in manifest))
    with (out / "map-exports.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return counts
