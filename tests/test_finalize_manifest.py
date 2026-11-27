import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from finalize_manifest import finalize
from map_assets import pixel_signature, render
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
        with self.manifest.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(("original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path"))
            for i in range(1, 5):
                w.writerow((i, "same", f"Name, {i}", f"dats/{i}.dat", f"pngs/{i}.png"))
        arts = []
        for sequence in ([1, 2, 1, 99], [2, 3]):
            arts.append({"tiles": [dict(original_map_id=i, row=1, column=j, kept=i!=99,
                                        pixel_key="same" if i!=99 else None,
                                        clockwise_quarters=0, rotation_unknown=False)
                                   for j, i in enumerate(sequence, 1)]})
        (self.root / "layouts.json").write_text(json.dumps({"layouts": arts}))
        (self.root / "ungrouped.csv").write_text("original_map_id,pixel_key\n4,same\n")
        self.keys = [layout_key(a) for a in arts]

    def run_export(self, rows, version="v2"):
        self.decisions.write_text(json.dumps(dict(format=f"framed-map-atlas/artwork-review-{version}",
                                                  collection=collection_key(self.keys), decisions=rows)))
        return finalize(self.manifest, self.root, self.decisions, self.out)

    def test_explicit_removals_only(self):
        orig = self.manifest.read_bytes()
        self.assertEqual(self.run_export([dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=[1,2]),
                                          dict(layout_key=self.keys[1], decision="Remove tiles", remove_ids=[2])]), (2,2))
        with self.out.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["chosen_name"] for r in rows], ["Name, 3","Name, 4"])
        self.assertEqual((self.out.parent / rows[0]["dat_path"]).resolve(), self.root / "dats" / "3.dat")
        self.assertEqual(orig, self.manifest.read_bytes())

    def test_rejects_excluded_or_unrelated_ids(self):
        for selected in ([99], [4], [], [1,1]):
            with self.subTest(selected=selected), self.assertRaises(ValueError):
                self.run_export([dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=selected)])
            self.assertFalse(self.out.exists())

    def test_existing_ignore_removes_nothing(self):
        self.assertEqual(self.run_export([dict(layout_key=self.keys[0], decision="Ignore grouping")], "v1"), (4,0))

    def test_recovery_requires_matching_dat_and_exports_new_pair(self):
        colors = bytes([5, 7]) + bytes(16382)
        key, _ = pixel_signature(colors)
        data = dict(format="framed-map-atlas/artwork-review-v3", collection=collection_key(self.keys),
                    decisions=[dict(layout_key=self.keys[0], decision="Remove tiles", remove_ids=[1])],
                    recover_tiles=[dict(map_id=99, pixel_key=key)])
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
        self.assertEqual(run(), (4,1))
        with self.out.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([int(r["original_map_id"]) for r in rows], [2,3,4,99])
        row = rows[-1]
        self.assertEqual((row["pixel_key"],row["chosen_name"]),(key,"Moon, part two"))
        self.assertEqual((self.out.parent / row["dat_path"]).read_bytes(),src.read_bytes())
        self.assertEqual((self.out.parent / row["png_path"]).read_bytes(),render(src))
        self.assertEqual(original,self.manifest.read_bytes())
