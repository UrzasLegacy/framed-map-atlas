import csv
import gzip
import importlib.util
import io
import json
import struct
import tempfile
import unittest
import zlib
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from anvil import NBT, decompress
from artwork import project, related, title_key
from scan_maps import main


def utf(value):
    raw = value.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def tag(value):
    if isinstance(value, bytes):
        return 7, struct.pack(">i", len(value)) + value
    if isinstance(value, int):
        return 3, struct.pack(">i", value)
    if isinstance(value, str):
        return 8, utf(value)
    if isinstance(value, dict):
        contents = bytearray()
        for key, child in value.items():
            kind, payload = tag(child)
            if key == "block_pos":
                kind, payload = 11, struct.pack(">iiii", 3, *child)
            elif key in ("Facing", "ItemRotation"):
                kind, payload = 1, struct.pack(">b", child)
            elif key == "Damage":
                kind, payload = 2, struct.pack(">h", child)
            contents.extend(bytes([kind]) + utf(key) + payload)
        return 10, bytes(contents) + b"\0"
    if isinstance(value, list):
        entries = [tag(child) for child in value]
        kind = entries[0][0] if entries else 0
        assert all(t == kind for t, _ in entries)
        return 9, struct.pack(">Bi", kind, len(entries)) + b"".join(p for _, p in entries)
    raise TypeError(value)


def region(folder, root, codec=2, external=False, rx=-1, slot=0):
    folder.mkdir(parents=True, exist_ok=True)
    raw = b"\x0a\0\0" + tag(root)[1]
    packed = {1: gzip.compress, 2: zlib.compress, 3: bytes}[codec](raw)
    if external:
        (folder / f"c.{rx * 32 + slot % 32}.{slot // 32}.mcc").write_bytes(packed)
        packed, codec = b"", codec | 128
    chunk = struct.pack(">IB", 1 + len(packed), codec) + packed
    sectors = (len(chunk) + 4095) // 4096
    header = bytearray(8192)
    struct.pack_into(">I", header, slot * 4, 512 + sectors)
    path = folder / f"r.{rx}.0.mca"
    path.write_bytes(header + chunk.ljust(4096 * sectors, b"\0"))
    return path


def modern(x, y, map_id, name=None, face=3, glow=False):
    components = {"minecraft:map_id": map_id}
    if name is not None:
        components["minecraft:custom_name"] = name
    return {"id": "minecraft:glow_item_frame" if glow else "minecraft:item_frame",
            "block_pos": [x, y, 7], "Facing": face, "ItemRotation": 0,
            "Item": {"id": "minecraft:filled_map", "components": components}}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


class ScannerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.world = self.base / "backup"
        self.world.mkdir()
        self.out = self.base / "report"

    def run_scan(self, expected=0, flags=()):
        with redirect_stderr(io.StringIO()), redirect_stdout(io.StringIO()):
            self.assertEqual(main([str(self.world), "--out", str(self.out), *flags]), expected)

    def test_entities_only_across_dimensions(self):
        for i, folder in enumerate(("world/entities", "world_nether/DIM-1/entities", "world/DIM1/entities")):
            region(self.world / folder, {"DataVersion": 3953, "Entities": [modern(-509, 60, i, "Moon")]})
        terrain = self.world / "world/region"
        terrain.mkdir()
        (terrain / "r.0.0.mca").write_bytes(b"unreadable terrain")
        self.run_scan(flags=["--entities-only"])
        summary = read_json(self.out / "scan-summary.json")
        self.assertEqual(summary["scan_mode"], "entities-only")
        self.assertEqual(summary["counts"]["region_files"], 3)
        self.assertEqual(summary["issues"], [])
        self.assertEqual([r["map_id"] for r in read_json(self.out / "map-names.json")], [0, 2, 1])

    def test_entities_only_without_entity_files(self):
        region(self.world / "region", {"Level": {"Entities": []}})
        self.run_scan(expected=1, flags=["--entities-only"])
        summary = read_json(self.out / "scan-summary.json")
        self.assertEqual(summary["counts"]["region_files"], 0)
        self.assertIn("entities/", summary["issues"][0]["message"])

    def saved_map(self, filename, pixels):
        path = self.world / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = gzip.compress(b"\x0a\0\0" + tag({"data": {"colors": pixels}, "DataVersion": 3953})[1])
        path.write_bytes(raw)
        return raw

    def test_frames_and_nested_containers_share_catalog_only(self):
        def item(number, title):
            return modern(0, 60, number, title)["Item"]
        bundle = {"id": "minecraft:bundle", "components": {"minecraft:bundle_contents": [
            dict(item(367, "Bundle tile"), count=3), item(900, "Filtered out")]}}
        box = {"id": "minecraft:blue_shulker_box", "Slot": 8,
               "components": {"minecraft:container": [{"slot": 17, "item": bundle}]}}
        chest = {"id": "minecraft:chest", "x": 11, "y": 62, "z": -4,
                 "Items": [dict(item(25, "Stored alias"), Slot=4), box]}
        cart = {"id": "minecraft:chest_minecart", "Pos": [20, 60, 3], "Items": [dict(item(1082, "Cart"), Slot=3)]}
        region(self.world / "region", {"DataVersion": 3953, "block_entities": [chest]})
        region(self.world / "entities", {"DataVersion": 3953, "Entities": [modern(11, 62, 25, "On wall"), cart]})
        keep = self.base / "keep.txt"
        keep.write_text("map_00025.png\nmap_00367.png\nmap_01082.png\nmap_02628.png")
        for n in (25, 367, 1082):
            self.saved_map(f"data/map_{n}.dat", bytes(16384))
        self.run_scan(flags=["--include-containers", "--workers", "2", "--map-list", str(keep), "--export-maps", "both"])
        records = read_json(self.out / "map-names.json")
        self.assertEqual([r["map_id"] for r in records], [25, 367, 1082])
        self.assertEqual(records[0]["observed_names"], {"On wall": 1, "Stored alias": 1})
        self.assertEqual(records[0]["observations"], 2)
        self.assertEqual(records[1]["frames"], [])
        with (self.out / "containers.csv").open(encoding="utf-8-sig", newline="") as file:
            rows = {int(r["map_id"]): r for r in csv.DictReader(file)}
        self.assertEqual(rows[367]["slot_path"], "Items[8]/minecraft:blue_shulker_box.container[17]/minecraft:bundle.bundle_contents[0]")
        self.assertEqual(rows[367]["count"], "3")
        self.assertEqual((rows[367]["holder_type"], rows[367]["x"]), ("minecraft:chest", "11"))
        self.assertEqual(records[1]["storage"], [int(rows[367]["storage"])])
        self.assertEqual((rows[1082]["holder_type"], rows[1082]["x"]), ("minecraft:chest_minecart", "20"))
        groups = read_json(self.out / "artwork-groups.json")["groups"]
        self.assertEqual([t["map_id"] for g in groups for t in g["tiles"]], [25])
        summary = read_json(self.out / "scan-summary.json")
        self.assertEqual(summary["selection"]["unmatched_ids"], [2628])
        self.assertEqual(summary["counts"]["stored_observations"], 3)
        self.assertEqual(read_json(self.out / "map-exports.json")["counts"], {"maps": 3, "dat": 3, "png": 3, "incomplete": 0})

    def test_legacy_container_opt_in(self):
        old_map = {"id": 358, "Damage": 21, "Slot": 0, "Count": 2, "tag": {"display": {"Name": "Old title"}}}
        nested = {"id": "minecraft:shulker_box", "Slot": 3, "tag": {"BlockEntityTag": {"Items": [old_map]}}}
        region(self.world / "DIM-1/region", {"DataVersion": 1343, "Level": {"TileEntities": [
            {"id": "minecraft:chest", "x": 0, "y": 70, "z": 8, "Items": [nested]}]}})
        self.run_scan()
        self.assertFalse((self.out / "containers.csv").exists())
        self.assertEqual(read_json(self.out / "map-names.json"), [])
        self.out = self.base / "with-storage"
        self.run_scan(flags=["--include-containers"])
        record, = read_json(self.out / "map-names.json")
        self.assertEqual((record["map_id"], record["name"]), (21, "Old title"))
        with (self.out / "containers.csv").open(encoding="utf-8-sig", newline="") as file:
            row, = csv.DictReader(file)
        self.assertEqual(row["dimension"], "minecraft:the_nether")

    def test_container_scan_reports_missing_terrain(self):
        region(self.world / "entities", {"DataVersion": 3953, "Entities": [modern(1, 60, 8, "Moon")]})
        self.run_scan(expected=1, flags=["--include-containers"])
        self.assertIn("terrain", read_json(self.out / "scan-summary.json")["issues"][0]["message"])

    def test_framed_bundle_and_decorated_pot(self):
        stack = modern(2, 65, 15, "In bundle")["Item"]
        bundle = {"id": "minecraft:bundle", "components": {"minecraft:bundle_contents": [stack]}}
        frame = dict(modern(2, 65, 1, glow=True), Item=bundle)
        dropped = {"id": "minecraft:item", "Item": modern(3, 65, 42)["Item"]}
        region(self.world / "entities", {"DataVersion": 3953, "Entities": [frame, dropped]})
        region(self.world / "region", {"DataVersion": 3953, "block_entities": [
            {"id": "minecraft:decorated_pot", "x": 7, "y": 71, "z": 3, "item": modern(7, 71, 9)["Item"]}]})
        self.run_scan(flags=["--include-containers"])
        self.assertEqual([r["map_id"] for r in read_json(self.out / "map-names.json")], [9, 15])
        self.assertEqual(read_json(self.out / "artwork-groups.json")["groups"], [])

    def test_export_from_existing_report_without_region_files(self):
        pixels = bytes([0, 4, 5, 6, 7, 247]) + bytes(16378)
        original = self.saved_map("world/data/map_8.dat", pixels)
        catalog = self.base / "map-names.json"
        catalog.write_text(json.dumps([{"world": "world_nether", "map_id": 8, "name": "Harbor"}]))
        self.run_scan(flags=["--from-report", str(catalog), "--export-maps", "both"])
        result = read_json(self.out / "map-exports.json")
        self.assertEqual(result["counts"], {"maps": 1, "png": 1, "dat": 1, "incomplete": 0})
        entry, = result["maps"]
        self.assertEqual(entry["name"], "Harbor")
        self.assertEqual((self.out / entry["dat"]).read_bytes(), original)
        png = (self.out / entry["png"]).read_bytes()
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        chunks, offset = {}, 8
        while offset < len(png):
            size, kind = struct.unpack_from(">I4s", png, offset)
            data = png[offset+8:offset+8+size]
            self.assertEqual(struct.unpack_from(">I", png, offset+8+size)[0], zlib.crc32(kind + data))
            chunks[kind] = data
            offset += 12 + size
        self.assertEqual(struct.unpack(">IIBBBBB", chunks[b"IHDR"]), (128, 128, 8, 3, 0, 0, 0))
        self.assertEqual(chunks[b"tRNS"], bytes(4))
        self.assertEqual(chunks[b"PLTE"][12:24], bytes.fromhex("597d27 6d9930 7fb238 435e1d"))
        rows = zlib.decompress(chunks[b"IDAT"])
        self.assertEqual(rows, b"".join(b"\x00" + pixels[i:i+128] for i in range(0, 16384, 128)))

    def test_combined_scan_and_partial_map_export(self):
        pixels = bytes([10]) * 16384
        self.saved_map("data/minecraft/maps/4.dat", pixels)
        raw = self.saved_map("data/map_5.dat", b"short")
        region(self.world / "entities", {"DataVersion": 5000, "Entities": [
            modern(-512, 60, i, "Title") for i in (4, 4, 5, 6)]})
        self.run_scan(expected=1, flags=["--export-maps", "both", "--entities-only"])
        result = read_json(self.out / "map-exports.json")
        self.assertEqual(result["counts"], {"maps": 3, "png": 1, "dat": 2, "incomplete": 2})
        self.assertEqual([r["status"] for r in result["maps"]], ["exported", "error", "missing"])
        self.assertEqual((self.out / result["maps"][1]["dat"]).read_bytes(), raw)
        self.assertTrue((self.out / "frames.csv").exists())

    def test_duplicate_id_in_multiple_stores_needs_explicit_source(self):
        catalog = self.base / "map-names.json"
        catalog.write_text('[{"world":"world_the_end","map_id":1}]')
        raw = self.saved_map("world/data/map_1.dat", bytes([4]) * 16384)
        self.saved_map("world_the_end/data/map_1.dat", bytes([5]) * 16384)
        flags = ["--from-report", str(catalog.parent), "--export-maps", "dat"]
        self.run_scan(expected=1, flags=flags)
        entry, = read_json(self.out / "map-exports.json")["maps"]
        self.assertEqual(entry["status"], "ambiguous")
        self.out = self.base / "resolved"
        self.run_scan(flags=[*flags, "--map-source", "world_the_end", str(self.world / "world/data")])
        entry, = read_json(self.out / "map-exports.json")["maps"]
        self.assertIsNone(entry["png"])
        self.assertEqual((self.out / entry["dat"]).read_bytes(), raw)

    def test_map_list_filters_report_export(self):
        catalog = self.base / "map-names.json"
        catalog.write_text(json.dumps([{"world": ".", "map_id": i} for i in (25, 26)]))
        for i in (25, 26):
            self.saved_map(f"data/map_{i}.dat", bytes(16384))
        keep = self.base / "keep.txt"
        keep.write_bytes(b"\xef\xbb\xbf map_00025.png \r\n\r\nmap_25.PNG\r\nmap_00367.png")
        self.run_scan(flags=["--from-report", str(catalog), "--export-maps", "both", "--map-list", str(keep)])
        report = read_json(self.out / "map-exports.json")
        self.assertEqual(report["selection"], {"requested_ids": 2, "matched_ids": 1, "unmatched_ids": [367]})
        self.assertEqual([r["map_id"] for r in report["maps"]], [25])
        self.assertEqual(sorted(p.name for p in (self.out / "maps").rglob("*") if p.is_file()),
                         ["map_25.dat", "map_25.png"])

    def test_map_list_filters_before_grouping_in_parallel(self):
        folder = self.world / "entities"
        region(folder, {"DataVersion": 3953, "Entities": [modern(-1, 60, 25, "Moon")]}, rx=-1, slot=31)
        region(folder, {"DataVersion": 3953, "Entities": [modern(0, 60, 26, "Moon"),
                                                          modern(1, 60, 367, "Moon")]}, rx=0)
        keep = self.base / "keep.txt"
        keep.write_text("map_00025.png\nmap_00367.png\nmap_01082.png\n")
        self.run_scan(flags=["--entities-only", "--workers", "2", "--map-list", str(keep)])
        self.assertEqual([r["map_id"] for r in read_json(self.out / "map-names.json")], [25, 367])
        with (self.out / "frames.csv").open(encoding="utf-8-sig", newline="") as f:
            self.assertEqual(sorted(int(r["map_id"]) for r in csv.DictReader(f)), [25, 367])
        groups = read_json(self.out / "artwork-groups.json")["groups"]
        self.assertEqual(sorted([t["map_id"] for t in g["tiles"]] for g in groups), [[25], [367]])
        self.assertEqual(read_json(self.out / "scan-summary.json")["selection"]["unmatched_ids"], [1082])

    def test_invalid_or_empty_map_list_never_runs_unfiltered(self):
        keep = self.base / "keep.txt"
        for contents, error in [(" \n", "contains no filenames"), ("map_00025.png\nmap_*.png", "line 2")]:
            keep.write_text(contents)
            with self.subTest(contents=contents):
                stderr = io.StringIO()
                with redirect_stderr(stderr), self.assertRaises(SystemExit) as failure:
                    main([str(self.world), "--map-list", str(keep), "--out", str(self.out)])
                self.assertNotEqual(failure.exception.code, 0)
                self.assertIn(error, stderr.getvalue())
                self.assertFalse(self.out.exists())

    def test_mural_and_neighbor_end_to_end(self):
        frames = []
        for r in range(3):
            for c in range(3):
                frames.append(modern(-512 + c, 65 - r, 150 - r * 7 + c,
                                     {"text": "Moon", "extra": [{"text": f" Harbor ({r+1},{c+1})"}]},
                                     glow=(r == 1)))
        frames += [modern(-509, 65, 9, "Dragon"), modern(-508, 65, 28, "Dragon #2"),
                   modern(-512, 65, 150, "Another Title", face=2),
                   modern(-505, 65, 812, "Moon Harbor #10")]
        empty = {"id": "minecraft:item_frame"}
        sword = {"id": "minecraft:item_frame", "Item": {"id": "minecraft:diamond_sword"}}
        drop = {"id": "minecraft:item", "Item": frames[0]["Item"]}
        chest = {"id": "minecraft:chest", "Items": [frames[0]["Item"]]}
        region(self.world / "dimensions/minecraft/overworld/entities",
               {"DataVersion": 5000, "Entities": [*frames, empty, sword, drop], "block_entities": [chest]},
               external=True)
        self.run_scan()
        guesses = read_json(self.out / "artwork-groups.json")
        mural, = [g for g in guesses["groups"] if g["tile_count"] == 9]
        self.assertEqual((mural["width"], mural["height"], mural["confidence"]), (3, 3, "high"))
        self.assertEqual([t["map_id"] for t in mural["tiles"]], [150,151,152,143,144,145,136,137,138])
        self.assertEqual(mural["tiles"][-1]["row"], 3)
        self.assertEqual(sorted(g["tile_count"] for g in guesses["groups"]), [1,1,2,9])
        link, = guesses["possible_related_groups"]
        self.assertIn(mural["group"], link["groups"])
        records = read_json(self.out / "map-names.json")
        conflict, = [r for r in records if r["status"] == "conflict"]
        self.assertEqual(conflict["map_id"], 150)
        self.assertIsNone(conflict["name"])
        with (self.out / "frames.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), 13)
        self.assertEqual(rows[0]["dimension"], "minecraft:overworld")
        self.assertEqual(rows[0]["chunk_x"], "-32")
        self.assertEqual(json.loads(rows[0]["name_raw"])["text"], "Moon")
        self.assertEqual(sum(r["frame_type"] == "minecraft:glow_item_frame" for r in rows), 3)

    def test_legacy_and_separate_saves(self):
        frame = {"id": "ItemFrame", "TileX": -502, "TileY": 70, "TileZ": 1, "Facing": 2,
                 "CustomName": "Frame label", "Item": {"id": 358, "Damage": 21,
                                                        "tag": {"display": {"Name": "§bAurora"}}}}
        region(self.world / "A/region", {"Level": {"Entities": [frame]}}, codec=1)
        frame = modern(-502, 70, 21)
        frame["CustomName"] = '{"text":"Frame only"}'
        frame["Item"] = {"id": "minecraft:filled_map", "tag": {"map": 21}}
        region(self.world / "B/DIM-1/region", {"DataVersion": 1976, "Level": {"Entities": [frame]}}, codec=3)
        frame["Item"]["tag"]["display"] = {"Name": '{"text":"Ice","extra":[{"text":" Cap"}]}'}
        region(self.world / "B/DIM1/region", {"DataVersion": 1976, "Level": {"Entities": [frame]}})
        self.run_scan()
        records = read_json(self.out / "map-names.json")
        self.assertEqual([(r["world"], r["name"]) for r in records], [("A","Aurora"), ("B","Ice Cap")])
        with (self.out / "frames.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["facing"], "north")
        nether, = [r for r in rows if r["dimension"] == "minecraft:the_nether"]
        self.assertEqual((nether["name"], nether["frame_name"]), ("", "Frame only"))

    def test_unnamed_bridge_and_duplicate_position(self):
        frames = [modern(i, 65, 7 + i, name) for i, name in enumerate(
            ["Dragon", "Dragon 2", None, "Ship", "Ship #2", None, None])]
        frames += [modern(9, 65, 90), modern(9, 65, 91)]
        region(self.world / "entities", {"DataVersion": 4325, "Entities": frames})
        self.run_scan()
        data = read_json(self.out / "artwork-groups.json")
        self.assertEqual(sorted(g["tile_count"] for g in data["groups"]), [1,2,4])
        bridge, = [g for g in data["groups"] if g["tile_count"] == 1]
        self.assertIn("multiple title groups", bridge["review_notes"][0])
        self.assertEqual(len(data["unplaced"]), 2)

    def test_unnamed_wall_and_missing_facing(self):
        frames = [modern(i % 3, i // 3, 100 + i) for i in range(9)]
        unknown = modern(3, 0, 107, "Frame Ten")
        del unknown["Facing"]
        region(self.world / "entities", {"DataVersion": 5000, "Entities": [*frames, unknown]})
        self.run_scan()
        data = read_json(self.out / "artwork-groups.json")
        wall, = data["groups"]
        self.assertEqual((wall["width"], wall["height"], wall["confidence"]), (3,3,"low"))
        self.assertEqual(len(data["unplaced"]), 1)

    def test_corrupt_chunk_and_valid_neighbor(self):
        folder = self.world / "entities"
        path = region(folder, {"DataVersion": 4325, "Entities": [modern(-512, 60, 9, "Good")]})
        data = bytearray(path.read_bytes())
        data[4:8] = bytes.fromhex("00 00 03 01")
        data.extend(struct.pack(">IB", 1, 99).ljust(4096, b"\0"))
        path.write_bytes(data)
        self.run_scan(expected=1)
        summary = read_json(self.out / "scan-summary.json")
        self.assertEqual(summary["counts"]["chunks_read"], 1)
        self.assertEqual(summary["issues"][0]["chunk"], [-31,0])
        self.assertEqual(read_json(self.out / "map-names.json")[0]["name"], "Good")

    def test_empty_backup(self):
        self.run_scan(expected=1)
        self.assertEqual(read_json(self.out / "map-names.json"), [])
        self.assertTrue(read_json(self.out / "scan-summary.json")["issues"])

    def test_java_modified_utf(self):
        self.assertEqual(NBT(b"\0\x08\xc0\x80\xed\xa0\xbd\xed\xb8\x80").string(), "\0😀")

    def test_truncated_compression(self):
        with self.assertRaises(ValueError):
            decompress(2, zlib.compress(b"payload")[:-1])
        with self.assertRaisesRegex(ValueError, "damaged"):
            decompress(2, b"garbled")

    def test_name_variations(self):
        for label in ("Star Harbor (2,1)", "B2 - Star Harbor", "Star Harbor – Bottom Left",
                      "Star Harbor r2c1", "Star Harbor 4 of 9", "1/9 | Star Harbor"):
            with self.subTest(label=label):
                self.assertEqual(title_key(label), "star harbor")
        self.assertTrue(related("star harbor", "star harbour"))
        self.assertFalse(related("dragon by ivy", "ships by ivy"))

    def test_six_surface_orientations(self):
        for face, view in {"north": (5,-3,-4), "south": (5,3,-4), "west": (3,5,-4),
                           "east": (3,-5,-4), "up": (4,3,5), "down": (4,-3,5)}.items():
            self.assertEqual(project({"x": 3, "y": 4, "z": 5, "facing": face}), view)

    def test_missing_id_and_component_precedence(self):
        a = modern(-509, 63, 42, '{"text":"literal"}')
        a["Item"]["components"]["minecraft:item_name"] = "Base title"
        b = modern(-510, 63, 17)
        b["Item"]["components"] = {"minecraft:item_name": {"text": "Found title"}}
        region(self.world / "entities", {"DataVersion": 5000, "Entities": [a, b]})
        self.run_scan(expected=1)
        with (self.out / "frames.csv").open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(rows[0]["name"], '{"text":"literal"}')
        self.assertEqual((rows[1]["map_id"], rows[1]["name_source"], rows[1]["name"]),
                         ("", "minecraft:item_name", "Found title"))
        groups = read_json(self.out / "artwork-groups.json")
        self.assertEqual(len(groups["unplaced"]), 1)
        self.assertEqual(len(read_json(self.out / "map-names.json")), 1)

    def test_lz4_raw_block_framing(self):
        def header(n):
            return b"LZ4Block" + struct.pack("<BIII", 0x10, n, n, 0)
        self.assertEqual(decompress(4, header(3) + b"abc" + header(0)), b"abc")

    def test_lz4_compressed_block_or_install_message(self):
        stream = (b"LZ4Block" + struct.pack("<BIII", 0x20, 4, 3, 0) + b"\x30abc" +
                  b"LZ4Block" + struct.pack("<BIII", 0x10, 0, 0, 0))
        if importlib.util.find_spec("lz4"):
            self.assertEqual(decompress(4, stream), b"abc")
        else:
            with self.assertRaisesRegex(ValueError, "pip install lz4"):
                decompress(4, stream)

    def test_mural_crosses_region_boundary(self):
        folder = self.world / "entities"
        for x, rx, slot in [(-1,-1,31), (0,0,0)]:
            entity = modern(x, 50, 82 - x, "Nebula")
            region(folder, {"DataVersion": 5000, "Entities": [entity]}, rx=rx, slot=slot)
        self.run_scan()
        group, = read_json(self.out / "artwork-groups.json")["groups"]
        self.assertEqual([t["map_id"] for t in group["tiles"]], [83,82])
        self.assertEqual((group["width"], group["height"]), (2,1))

    def test_parallel_reports_match_single_worker_with_partial_scan(self):
        folder = self.world / "entities"
        for x, rx, slot in [(-1, -1, 31), (0, 0, 0)]:
            region(folder, {"DataVersion": 3953, "Entities": [modern(x, 50, 82 - x, "Nebula")]},
                   rx=rx, slot=slot)
        (folder / "r.1.0.mca").write_bytes(b"bad header")
        region(self.world / "DIM1/entities",
               {"DataVersion": 3953, "Entities": [modern(0, 50, 82, "Another title")]})
        self.run_scan(expected=1, flags=["--entities-only", "--workers", "1"])
        serial = self.out
        self.out = self.base / "parallel"
        self.run_scan(expected=1, flags=["--entities-only", "--workers", "2"])
        for name in ("map-names.json", "frames.csv", "artwork-groups.json", "scan-summary.json"):
            with self.subTest(report=name):
                self.assertEqual((serial / name).read_bytes(), (self.out / name).read_bytes())

