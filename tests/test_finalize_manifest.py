import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from finalize_manifest import finalize, allocate_ids
from map_assets import pixel_signature, render, read_map_data
from reconstruct_artworks import collection_key, layout_key
from test_scanner import tag


class MigrationManifestTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.manifest = self.root / "manifest.csv"
        self.decisions = self.root / "review.json"
        self.out = self.root / "next" / "migration.csv"
        (self.root / "dats").mkdir()
        colors = bytes([5]) * 16384
        self.pixel_key = pixel_signature(colors)[0]
        raw = b"\x0a\0\0" + tag({"data": {"colors": colors}})[1]
        with self.manifest.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(("original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path"))
            for i in range(1, 5):
                # Only ID 3 is already locked.
                lock = b"\x01\x00\x06locked" + bytes([int(i == 3)])
                (self.root / f"dats/{i}.dat").write_bytes(raw[:-2] + lock + raw[-2:])
                w.writerow((i, self.pixel_key, f"Name, {i}", f"dats/{i}.dat", f"pngs/{i}.png"))
        arts = []
        for sequence in ([1, 2, 1, 99], [2, 3]):
            arts.append({"width":len(sequence), "height":1, "id":f"G{len(arts)}", "title":"Mural",
                         "tiles": [dict(original_map_id=i, row=1, column=j, kept=i!=99,
                                        pixel_key=self.pixel_key if i!=99 else None,
                                        clockwise_quarters=0, rotation_unknown=False)
                                   for j, i in enumerate(sequence, 1)]})
        (self.root / "layouts.json").write_text(json.dumps({"layouts": arts}))
        (self.root / "ungrouped.csv").write_text(f"original_map_id,pixel_key\n4,{self.pixel_key}\n")
        self.keys = [layout_key(a) for a in arts]

    def run_export(self, rows, version="v2"):
        self.decisions.write_text(json.dumps(dict(format=f"framed-map-atlas/artwork-review-{version}",
                                                  collection=collection_key(self.keys), decisions=rows)))
        return finalize(self.manifest, self.root, self.decisions, self.out)

    def test_explicit_removals_only(self):
        orig = self.manifest.read_bytes()
        self.assertEqual(self.run_export([dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=[1,2]),
                                          dict(layout_key=self.keys[1], decision="Remove tiles", remove_ids=[2])]), (2,1,0,0))
        with self.out.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["chosen_name"] for r in rows], ["Name, 3","Name, 4"])
        self.assertEqual({r["destination_map_id"] for r in rows}, {"1000"})
        self.assertEqual({r["representative_original_map_id"] for r in rows}, {"3"})
        self.assertEqual((self.out.parent / rows[0]["dat_path"]).read_bytes(), (self.root / "dats/3.dat").read_bytes())
        self.assertEqual(orig, self.manifest.read_bytes())

    def test_rejects_excluded_or_unrelated_ids(self):
        for selected in ([99], [4], [], [1,1]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.run_export([dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=selected)])
            self.assertFalse(self.out.exists())

    def test_existing_ignore_removes_nothing(self):
        self.assertEqual(self.run_export([dict(layout_key=self.keys[0], decision="Ignore grouping")], "v1"), (4,1,0,0))

    def test_recovery_requires_matching_dat_and_exports_new_pair(self):
        colors = bytes([5, 7]) + bytes(16382)
        key, _ = pixel_signature(colors)
        data = dict(format="framed-map-atlas/artwork-review-v4", collection=collection_key(self.keys),
                    decisions=[dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=[1])],
                    recover_tiles=[dict(map_id=99, pixel_key=key)], merged_artworks=[])
        self.decisions.write_text(json.dumps(data))
        original = self.manifest.read_bytes()
        src = self.root / "map_00099.dat"
        def run():
            return finalize(self.manifest, self.root, self.decisions, self.out, self.root, review)
        review = self.root / "sorter-review.csv"
        review.write_text(f'map_id,pixel_key,chosen_name\n99,{key},"Moon, part two"\n')
        with self.assertRaisesRegex(ValueError, "found 0"):
            run()
        src.write_bytes(b"\x0a\0\0" + tag({"data":{"colors":bytes(16384)}})[1])
        with self.assertRaisesRegex(ValueError, "pixels differ"):
            run()
        self.assertFalse(self.out.exists())
        self.assertFalse((self.out.parent / "migration-assets").exists())
        src.write_bytes(b"\x0a\0\0" + tag({"data":{"colors":colors}})[1])
        self.assertEqual(run(), (4,2,0,0))
        with self.out.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([int(r["original_map_id"]) for r in rows], [2,3,4,99])
        row = rows[-1]
        self.assertEqual((row["pixel_key"],row["chosen_name"]),(key,"Moon, part two"))
        self.assertEqual(read_map_data(self.out.parent / row["dat_path"])["locked"],1)
        self.assertNotIn("locked", read_map_data(src))
        self.assertEqual((self.out.parent / row["png_path"]).read_bytes(),render(src))
        self.assertEqual(original,self.manifest.read_bytes())

    def test_merged_positions_and_named_aliases(self):
        tiles = [{"row": 2, "column": j, "original_map_id": i, "clockwise_quarters": j % 4}
                 for j, i in enumerate([1, 2, 1, 99], 1)]
        merge = dict(title="Final mural", width=4, height=2, tiles=tiles,
                     parts=[dict(layout_key=k) for k in self.keys])
        self.decisions.write_text(json.dumps(dict(format="framed-map-atlas/artwork-review-v4",
            collection=collection_key(self.keys), recover_tiles=[], merged_artworks=[merge],
            decisions=[dict(layout_key=k, decision="Keep artwork") for k in self.keys])))
        self.assertEqual(finalize(self.manifest, self.root, self.decisions, self.out), (4,1,1,3))
        art, = json.loads((self.out.parent / "migration-assets/artworks.json").read_text())["artworks"]
        self.assertEqual((art["title"], art["gaps"], art["source_groups"]), ("Final mural",5,["G0","G1"]))
        self.assertEqual([(t["row"],t["column"],t["chosen_name"],t["destination_map_id"],t["clockwise_quarters"])
                          for t in art["tiles"]], [(2,1,"Name, 1",1000,1),(2,2,"Name, 2",1000,2),(2,3,"Name, 1",1000,3)])

    def test_approved_original_keeps_repeated_cells(self):
        self.assertEqual(self.run_export([dict(layout_key=self.keys[0], decision="Keep artwork")]), (4,1,1,3))
        art, = json.loads((self.out.parent / "migration-assets/artworks.json").read_text())["artworks"]
        self.assertEqual([t["original_map_id"] for t in art["tiles"]], [1,2,1])
        self.assertEqual(art["gaps"], 1)

    def test_repeat_assignment_reserves_retired_ids_and_skips_occupied(self):
        keys = {"a"*64, "b"*64, "c"*64}
        previous = self.root / "pixel-ids.csv"
        previous.write_text("pixel_key,destination_map_id\n" + "d"*64 + ",1000\n" + "c"*64 + ",1003\n")
        (self.root / "map_1001.dat").touch()
        result = allocate_ids(keys, previous, self.root)
        self.assertEqual(result, {"d"*64:1000, "c"*64:1003, "a"*64:1002, "b"*64:1004})
        (self.root / "map_1003.dat").write_bytes((self.root / "dats/3.dat").read_bytes())
        with self.assertRaisesRegex(ValueError, "conflicts"):
            allocate_ids(keys, previous, self.root)
