import csv
import gzip
import hashlib
import io
import tempfile
import unittest
from pathlib import Path

from curated_names import write_catalog
from map_assets import index_dats, load_kept_ids
from scan_map_dats import metadata
from sort_canvases import build_collection
from test_scanner import tag


class SharedPixelTest(unittest.TestCase):
    def test_metadata_gallery_and_name_matcher_share_historical_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "maps"
            data.mkdir()
            for map_id, transparent in [(25, 0), (-1, 3)]:
                colors = bytes([6, 7, transparent]) + bytes(16381)
                nbt = b"\x0a\0\0" + tag({"data": {"colors": colors, "dimension": "minecraft:overworld",
                                                   "xCenter": 0, "zCenter": 0}})[1]
                path = data / f"map_{map_id}.dat"
                path.write_bytes(gzip.compress(nbt) if map_id == 25 else nbt)
            expected = hashlib.sha256(bytes([6, 7]) + bytes(16382)).hexdigest()
            for paths in index_dats(data).values():
                self.assertEqual(metadata(paths[0], True)["pixel_key"], expected)
                self.assertNotIn("pixel_key", metadata(paths[0], False))
            out = root / "sorted"
            self.assertEqual(build_collection(data, out), (2, 1, 0))
            with (out / "map-index.csv").open(encoding="utf-8-sig", newline="") as f:
                self.assertEqual([r["pixel_sha256"] for r in csv.DictReader(f)], [expected, expected])
            keep = root / "map-review.csv"
            keep.write_text(f"map_id,pixel_key,decision,chosen_name\n-1,{expected},Keep,\n25,{expected},Keep,\n"
                            "26,other,Terrain,\n27,third,Unsure,\n", encoding="utf-8-sig")
            catalog = io.StringIO()
            _, errors = write_catalog(data, load_kept_ids(keep), {-1: {"Sky"}}, catalog)
            self.assertEqual(errors, {})
            catalog.seek(0)
            rows = list(csv.DictReader(catalog))
            self.assertEqual([r["map_id"] for r in rows], ["-1", "25"])
            self.assertEqual([r["name_source_ids"] for r in rows], ['{"Sky": [-1]}'] * 2)

    def test_review_requires_consistent_decisions_and_at_least_one_keep(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map-review.csv"
            for rows, message in [("2,a,Terrain,\n3,b,Unsure,", "no Keep"),
                                  ("2,a,Keep,\n2,a,Terrain,", "conflict"),
                                  ("2,a,Keep,\n2,b,Keep,", "conflict"),
                                  ("2,a,Maybe,", "invalid")]:
                with self.subTest(rows=rows):
                    path.write_text("map_id,pixel_key,decision,chosen_name\n" + rows, encoding="utf-8")
                    with self.assertRaisesRegex(ValueError, message):
                        load_kept_ids(path)

    def test_invalid_and_ambiguous_dats_keep_direct_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "map_1.dat").write_bytes(b"broken")
            (data / "map_2.dat").touch()
            (data / "map_0002.dat").touch()
            catalog = io.StringIO()
            _, errors = write_catalog(data, {1, 2, 3}, {1: {"Known"}}, catalog)
            self.assertEqual([errors[i][0] for i in (1, 2, 3)],
                             ["unreadable_data", "ambiguous_data", "missing_data"])
            catalog.seek(0)
            self.assertEqual(next(csv.DictReader(catalog))["direct_names"], '["Known"]')

    def test_named_reviews_select_kept_ids_independently_of_shared_pixels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "map-review.csv"
            with path.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["map_id", "pixel_key", "decision", "chosen_name"])
                writer.writerow([1, "same", "Keep", 'Moon, "Part 1"\nNorth'])
                writer.writerow([2, "same", "Terrain", ""])
                writer.writerow([3, "same", "", "Other title"])
                writer.writerow([4, "same", "", ""])
            self.assertEqual(load_kept_ids(path), {1})
