"""Recover displayed maps and optionally stored maps from an extracted Minecraft Java backup."""

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter, defaultdict, deque
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import datetime
from itertools import chain, islice
from pathlib import Path

from anvil import discover, location, region_chunks
from artwork import infer
from map_assets import export_maps, load_kept_ids, selection_summary


FRAMES = {"minecraft:item_frame", "minecraft:glow_item_frame", "item_frame", "glow_item_frame", "ItemFrame"}
MAPS = ("minecraft:filled_map", "filled_map", 358, "358")
FACES = ("down", "up", "north", "south", "west", "east")
OLD_FACES = ("south", "west", "north", "east")
FORMATTING = re.compile("§[0-9a-fk-orx]", re.I)
FIELDS = ("frame", "world", "dimension", "map_id", "name", "name_source", "name_raw", "frame_name",
          "frame_type", "x", "y", "z", "position_source", "facing", "rotation",
          "region", "chunk_x", "chunk_z", "data_version")
STORAGE_FIELDS = ("storage", "world", "dimension", "map_id", "name", "name_source", "name_raw",
                  "holder_type", "x", "y", "z", "slot_path", "count", "region", "chunk_x", "chunk_z", "data_version")


def obj(value):
    return value if isinstance(value, dict) else {}


def component_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(map(component_text, value))
    if isinstance(value, dict):
        if set(value) == {""}:
            return component_text(value[""])
        body = value.get("text", value.get("fallback", value.get("translate", "")))
        return str(body) + component_text(value.get("extra", []))
    return ""


def name_text(value, legacy_json=True):
    if isinstance(value, str) and legacy_json and value.lstrip().startswith(("{", "[", '"')):
        try:
            value = json.loads(value)
        except (ValueError, RecursionError):
            pass
    return FORMATTING.sub("", component_text(value)).strip() or None


def display_position(entity):
    pos = entity.get("block_pos")
    if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, int) for v in pos):
        return (*pos, "block_pos")
    tile = [entity.get("Tile" + axis) for axis in "XYZ"]
    if all(isinstance(v, int) for v in tile):
        return (*tile, "TileXYZ")
    pos = entity.get("Pos")
    if isinstance(pos, list) and len(pos) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in pos):
        return (*map(math.floor, pos), "Pos")
    return None, None, None, None


def direction(entity, version):
    raw = entity.get("Facing", entity.get("facing"))
    if isinstance(raw, str):
        return raw if raw in FACES else None
    if raw is None:
        raw = entity.get("Direction", entity.get("Dir"))
        faces = OLD_FACES
    elif (version is not None and version <= 1343) or (version is None and entity.get("id") == "ItemFrame"):
        faces = OLD_FACES
    elif (version is not None and version >= 1519) or raw in (4, 5) or "block_pos" in entity:
        faces = FACES
    else:
        return None
    return faces[raw] if isinstance(raw, int) and 0 <= raw < len(faces) else None


def entity_tree(nodes):
    if not isinstance(nodes, list):
        raise ValueError("entity list has unexpected type")
    for e in nodes:
        if isinstance(e, dict):
            yield e
            if "Passengers" in e:
                yield from entity_tree(e["Passengers"])
            if isinstance(e.get("Riding"), dict):
                yield from entity_tree([e["Riding"]])


def map_details(item, version, stats, map_ids, legacy_frame=False):
    if item.get("id") not in MAPS:
        return None
    components, tag = obj(item.get("components")), obj(item.get("tag"))
    map_id = components.get("minecraft:map_id", components.get("map_id", tag.get("map")))
    if map_id is None and (item.get("id") in (358, "358") or
                           (version is not None and version <= 1343) or
                           (version is None and legacy_frame)):
        map_id = item.get("Damage", 0)
    if map_ids is not None and (not isinstance(map_id, int) or map_id not in map_ids):
        return None
    if not isinstance(map_id, int):
        stats["filled_maps_without_id"] += 1
        map_id = None
    source, raw = None, None
    for key in ("minecraft:custom_name", "custom_name"):
        if key in components:
            source, raw = key, components[key]
            break
    if source is None and "Name" in obj(tag.get("display")):
        source, raw = "tag.display.Name", tag["display"]["Name"]
    if source is None:
        for key in ("minecraft:item_name", "item_name"):
            if key in components:
                source, raw = key, components[key]
                break
    # From 1.21.5 onward plain string components can be literal JSON-looking text.
    decode_json = source == "tag.display.Name" or version is None or version < 4325
    name = name_text(raw, decode_json)
    if source in ("minecraft:item_name", "item_name") and name in ("item.minecraft.filled_map", "filled_map.unknown"):
        name = None
    return {"map_id": map_id, "name": name, "name_source": source,
            "name_raw": None if raw is None else json.dumps(raw, ensure_ascii=False)}


def extract(entity, version, stats, map_ids=None):
    if entity.get("id") not in FRAMES:
        return None
    stats["frames_seen"] += 1
    item = obj(entity.get("Item", entity.get("item")))
    if item.get("id") in MAPS:
        stats["filled_map_frames"] += 1
    details = map_details(item, version, stats, map_ids, entity["id"] == "ItemFrame")
    if details is not None:
        x, y, z, origin = display_position(entity)
        decode_json = details["name_source"] == "tag.display.Name" or version is None or version < 4325
        details.update(frame_name=name_text(entity.get("CustomName"), decode_json), frame_type=entity["id"],
                       x=x, y=y, z=z, position_source=origin, facing=direction(entity, version),
                       rotation=entity.get("ItemRotation", entity.get("item_rotation")))
    return details


def slots(items, path, wrapped=False):
    if not isinstance(items, list):
        raise ValueError(f"expected inventory list at {path}")
    for index, entry in enumerate(items):
        if not isinstance(entry, dict):
            raise ValueError(f"expected an item stack at {path}[{index}]")
        slot = entry.get("Slot", entry.get("slot", index))
        stack = obj(entry.get("item")) if wrapped else entry
        yield f"{path}[{slot}]", stack


def packed_items(stack, path):
    prefix = f"{path}/{stack.get('id', '?')}"
    components = obj(stack.get("components"))
    for field in ("container", "bundle_contents"):
        items = components.get("minecraft:" + field, components.get(field, []))
        yield from slots(items, f"{prefix}.{field}", wrapped=field == "container")
    tag = obj(stack.get("tag"))
    yield from slots(tag.get("Items", []), f"{prefix}.tag.Items")
    for field, block in (("BlockEntityTag", tag.get("BlockEntityTag")),
                         ("block_entity_data", components.get("minecraft:block_entity_data", components.get("block_entity_data")))):
        block = obj(block)
        yield from slots(block.get("Items", block.get("items", [])), f"{prefix}.{field}.Items")


def stored_maps(holder, version, stats, map_ids, *, block):
    if not isinstance(holder, dict):
        raise ValueError("expected a compound for an inventory holder")
    holder_type = holder.get("id")
    if not (holder.get("Items") or holder.get("items") or holder_type in FRAMES or holder_type == "minecraft:decorated_pot"):
        return
    if block:
        xyz = [holder.get(axis) for axis in "xyz"]
    else:
        xyz = display_position(holder)[:3]
    pending = list(slots(holder.get("Items", holder.get("items", [])), "Items"))
    if block and holder_type == "minecraft:decorated_pot" and holder.get("item"):
        pending.append(("item", holder["item"]))
    if not block and holder_type in FRAMES:
        held = obj(holder.get("Item", holder.get("item")))
        pending.extend(packed_items(held, "frame.Item"))
    if pending:
        stats["nonempty_inventory_holders_checked"] += 1
    pending.reverse()
    while pending:
        path, stack = pending.pop()
        if stack.get("id") in MAPS:
            stats["stored_map_stacks_before_filter"] += 1
        details = map_details(stack, version, stats, map_ids)
        if details is not None:
            yield dict(details, holder_type=holder_type, x=xyz[0], y=xyz[1], z=xyz[2], slot_path=path,
                       count=stack.get("count", stack.get("Count", 1)))
        pending.extend(reversed(list(packed_items(stack, path))))


def catalog(frames, stored=()):
    by_id = defaultdict(list)
    for f in chain(frames, stored):
        if f["map_id"] is not None:
            by_id[f["world"], f["map_id"]].append(f)
    records = []
    for (world, map_id), seen in sorted(by_id.items()):
        names = Counter(f["name"] for f in seen if f["name"])
        records.append({"world": world, "map_id": map_id,
                        "status": ("conflict" if len(names) > 1 else "named" if names else "unnamed"),
                        "name": next(iter(names)) if len(names) == 1 else None,
                        "observed_names": dict(sorted(names.items())), "observations": len(seen),
                        "frames": [f["frame"] for f in seen if "frame" in f]})
        if stored_ids := [f["storage"] for f in seen if "storage" in f]:
            records[-1]["storage"] = stored_ids
    return records


def write_json(path, value):
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")


def container_summary(rows):
    """Summarize recovered rows; a stack contributes one observation regardless of size."""
    by_holder = Counter()
    titles_by_id = defaultdict(set)
    named = 0
    for row in rows:
        by_holder[row.get("holder_type") or "unknown"] += 1
        title = row.get("name")
        named += bool(title)
        if row["map_id"] not in (None, ""):
            names = titles_by_id[row["world"], int(row["map_id"])]
            if title:
                names.add(title)
    return {"recovered_map_stacks": sum(by_holder.values()), "named_map_stacks": named,
            "unique_world_map_ids": len(titles_by_id),
            "world_map_ids_with_names": sum(bool(names) for names in titles_by_id.values()),
            "world_map_ids_with_conflicting_names": sum(len(names) > 1 for names in titles_by_id.values()),
            "recovered_stacks_by_holder_type": dict(sorted(by_holder.items()))}


def scan_region(root, path, map_ids=None, containers=False):
    frames, stored, issues, stats = [], [], [], Counter()
    world, dimension = location(path.parent, root)
    relative = path.relative_to(root).as_posix()

    def issue(source, chunk, message):
        issues.append({"path": os.path.relpath(source, root), "chunk": chunk, "message": message})

    try:
        for cx, cz, chunk in region_chunks(path, issue, containers=containers):
            version = chunk.get("DataVersion")
            if not isinstance(version, int):
                version = None
            stats["chunks_read"] += 1
            context = dict(world=world, dimension=dimension, region=relative, chunk_x=cx, chunk_z=cz, data_version=version)
            try:
                for node in (chunk, obj(chunk.get("Level"))):
                    for entity in entity_tree(node.get("Entities", node.get("entities", []))):
                        frame = extract(entity, version, stats, map_ids)
                        if frame is not None:
                            frame.update(context)
                            frames.append(frame)
                        if containers:
                            stored.extend(dict(row, **context) for row in stored_maps(entity, version, stats, map_ids, block=False))
                    if containers:
                        for holder in node.get("block_entities", node.get("TileEntities", [])):
                            stored.extend(dict(row, **context) for row in stored_maps(holder, version, stats, map_ids, block=True))
            except (ValueError, TypeError, RecursionError) as exc:
                issue(path, (cx, cz), str(exc))
    except (ValueError, OSError) as exc:
        issue(path, None, str(exc))
    return frames, stored, issues, stats


def init_region_worker(map_ids):
    # Send large selections once per process instead of once per region.
    global _worker_map_ids
    _worker_map_ids = map_ids


def scan_worker_region(root, path, containers):
    return scan_region(root, path, _worker_map_ids, containers)


def region_results(root, regions, workers, map_ids=None, containers=False):
    workers = min(workers, len(regions))
    if workers <= 1:
        for path in regions:
            yield scan_region(root, path, map_ids, containers)
        return

    paths = iter(regions)
    with ProcessPoolExecutor(max_workers=workers, initializer=init_region_worker, initargs=(map_ids,)) as pool:
        # Bound queued results on large backups; consume in discovery order for stable frame IDs.
        pending = deque(pool.submit(scan_worker_region, root, p, containers) for p in islice(paths, workers * 2))
        while pending:
            result = pending.popleft().result()
            path = next(paths, None)
            if path is not None:
                pending.append(pool.submit(scan_worker_region, root, path, containers))
            yield result


def scan(root, out, *, entities_only=False, workers=1, map_ids=None, containers=False):
    issues, frames, stored = [], [], []
    stats = dict.fromkeys(("region_files", "chunks_read", "frames_seen", "filled_map_frames",
                           "filled_maps_without_id", "recovered_observations"), 0)
    if containers:
        stats.update(nonempty_inventory_holders_checked=0, stored_map_stacks_before_filter=0)

    def issue(path, chunk, message):
        issues.append({"path": os.path.relpath(path, root), "chunk": chunk, "message": message})

    regions = discover(root, issue, entities_only=entities_only)
    if not regions:
        storage = "entity region files in entities/ folders" if entities_only else "Anvil region files"
        issue(root, None, f"No {storage} found. Select the extracted world or backup folder.")
    elif containers and not any(p.parent.name == "region" for p in regions):
        issue(root, None, "No terrain region files found. Placed containers require the world's region/ folders.")
    stats["region_files"] = len(regions)
    if regions:
        print(f"Scanning {len(regions)} region files with {min(workers, len(regions))} worker(s).",
              file=sys.stderr, flush=True)
    results = region_results(root, regions, workers, map_ids, containers)
    for index, (observations, inventory_rows, errors, counts) in enumerate(results, 1):
        for frame in observations:
            frame["frame"] = len(frames) + 1
            frames.append(frame)
        for row in inventory_rows:
            row["storage"] = len(stored) + 1
            stored.append(row)
        issues.extend(errors)
        for key, count in counts.items():
            stats[key] += count
        if index % 50 == 0:
            print(f"Read {index}/{len(regions)} regions", file=sys.stderr, flush=True)

    records, guesses = catalog(frames, stored), infer(frames)
    stats.update(recovered_observations=len(frames), unique_world_map_ids=len(records),
                 named_ids=sum(r["status"] == "named" for r in records),
                 conflicting_ids=sum(r["status"] == "conflict" for r in records),
                 unnamed_ids=sum(r["status"] == "unnamed" for r in records),
                 multi_tile_groups=sum(g["tile_count"] > 1 for g in guesses["groups"]),
                 unplaced_frames=len(guesses["unplaced"]), scan_errors=len(issues))
    with (out / "frames.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(frames)
    if containers:
        stats["stored_observations"] = len(stored)
        with (out / "containers.csv").open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=STORAGE_FIELDS)
            writer.writeheader()
            writer.writerows(stored)
    write_json(out / "map-names.json", records)
    write_json(out / "artwork-groups.json", guesses)
    summary = {"scan_mode": "entities-only" if entities_only else "full", "counts": stats, "issues": issues}
    if containers:
        summary["include_containers"] = True
        summary["container_stats"] = container_summary(stored)
        for field in ("nonempty_inventory_holders_checked", "stored_map_stacks_before_filter"):
            summary["container_stats"][field] = stats.pop(field)
    if map_ids is not None:
        summary["selection"] = selection_summary(map_ids, (r["map_id"] for r in records))
    write_json(out / "scan-summary.json", summary)
    return stats


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="extracted save or folder containing saves")
    storage_mode = parser.add_mutually_exclusive_group()
    storage_mode.add_argument("--entities-only", action="store_true",
                        help="read only entity region files (suitable for worlds created on Java 1.17 or newer)")
    storage_mode.add_argument("--include-containers", action="store_true",
                              help="also recover stored maps from block and entity inventories, including nested bundles and boxes")
    parser.add_argument("--workers", type=int, default=1, metavar="N",
                        help="scan up to N region files in parallel using separate processes (default: 1)")
    parser.add_argument("--out", type=Path, help="new report folder (default: timestamped folder in current directory)")
    parser.add_argument("--export-maps", choices=("png", "dat", "both"),
                        help="export the recovered map IDs as PNG images, original .dat files, or both")
    parser.add_argument("--review-csv", type=Path, help="review CSV exported by the gallery; selects IDs marked Keep")
    parser.add_argument("--from-report", type=Path, help="use an existing map-names.json or report folder; skip region scanning")
    parser.add_argument("--map-source", action="append", nargs=2, default=[], metavar=("WORLD", "FOLDER"),
                        help="use this map data directory for a report world key (repeatable)")
    args = parser.parse_args(argv)
    if args.include_containers and args.from_report:
        parser.error("--include-containers needs a fresh world scan; omit --from-report")
    if (args.from_report or args.map_source) and not args.export_maps:
        parser.error("--from-report and --map-source require --export-maps")
    if args.workers < 1 or (os.name == "nt" and args.workers > 61):
        parser.error("--workers must be positive (maximum 61 on Windows)")
    root = args.backup.resolve()
    out = (args.out or Path(datetime.now().strftime("map-report-%Y%m%d-%H%M%S"))).resolve()
    if not root.is_dir() or root.name in {"region", "entities"}:
        parser.error("point backup at the extracted world or a parent of the world folders")
    if out.exists():
        parser.error(f"report destination already exists: {out}. Choose a new folder.")
    stats = asset_counts = None
    try:
        map_ids = load_kept_ids(args.review_csv) if args.review_csv is not None else None
        out.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=out.parent) as temp:
            staging = Path(temp) / "report"
            staging.mkdir()
            if args.from_report:
                catalog_path = args.from_report
                if catalog_path.is_dir():
                    catalog_path = catalog_path / "map-names.json"
            else:
                stats = scan(root, staging, entities_only=args.entities_only, workers=args.workers, map_ids=map_ids,
                             containers=args.include_containers)
                catalog_path = staging / "map-names.json"
            if args.export_maps:
                with catalog_path.open(encoding="utf-8-sig") as f:
                    records = json.load(f)
                print("Exporting recovered maps...", file=sys.stderr, flush=True)
                sources = {world: Path(folder).resolve() for world, folder in args.map_source}
                asset_counts = export_maps(root, staging, records, args.export_maps, sources, map_ids=map_ids)
            staging.rename(out)
    except (OSError, ValueError, BrokenProcessPool) as exc:
        parser.exit(1, f"Unable to complete operation: {exc}\n")
    print(f"Reports: {out}")
    if asset_counts is not None:
        print(f"Exported {asset_counts['png']} PNGs and {asset_counts['dat']} DAT files. "
              f"Incomplete map exports: {asset_counts['incomplete']}. See map-exports.json.")
    partial_scan = False
    if stats is not None:
        if "stored_observations" in stats:
            print(f"Recovered {stats['stored_observations']} stored map stacks. See containers.csv.")
        print(f"Recovered {stats['recovered_observations']} framed map observations; "
              f"{stats['multi_tile_groups']} possible multi-tile artworks.")
        partial_scan = bool(stats["scan_errors"] or stats["filled_maps_without_id"])
        if partial_scan:
            print("Partial scan. See scan-summary.json for skipped data.", file=sys.stderr)
    return int(partial_scan or bool(asset_counts and asset_counts["incomplete"]))


if __name__ == "__main__":
    sys.exit(main())

