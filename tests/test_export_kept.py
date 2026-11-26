import csv
import gzip
import tempfile
import unittest
from pathlib import Path

from export_kept import export_kept
from map_assets import pixel_signature, render
from test_scanner import tag


class KeptExportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.review = self.root / "review.csv"
        self.out = self.root / "export"
        colors = bytes([6, 7]) + bytes(16382)
        self.key, _ = pixel_signature(colors)
        self.title = 'Sky, "North"\n☁'
        for i in (1, 2, 3):
            body = tag({"data": {"colors": colors, "xCenter": i}})[1]
            (self.data / f"map_{i:05d}.dat").write_bytes(gzip.compress(b"\x0a\0\0" + body))
        with self.review.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["map_id", "pixel_key", "decision", "chosen_name"])
            for i, decision, title in [(3, "Keep", self.title), (1, "Keep", ""), (2, "Terrain", ""),
                                       (4, "Unsure", ""), (5, "", "Name only")]:
                writer.writerow([i, self.key, decision, title])

    def test_exports_distinct_originals_even_with_identical_pixels(self):
        self.assertEqual(export_kept(self.data, self.review, self.out), 2)
        with (self.out / "manifest.csv").open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual([(r["original_map_id"], r["chosen_name"]) for r in rows],
                         [("1", ""), ("3", self.title)])
        files = {"manifest.csv"}
        for row in rows:
            source = self.data / f"map_{int(row['original_map_id']):05d}.dat"
            self.assertEqual(row["pixel_key"], self.key)
            self.assertEqual((self.out / row["dat_path"]).read_bytes(), source.read_bytes())
            self.assertEqual((self.out / row["png_path"]).read_bytes(), render(source))
            files.update((row["dat_path"], row["png_path"]))
        self.assertEqual(files, {p.relative_to(self.out).as_posix() for p in self.out.rglob("*") if p.is_file()})
        with self.assertRaisesRegex(ValueError, "already exists"):
            export_kept(self.data, self.review, self.out)

    def test_failed_export_can_be_retried_in_place(self):
        source = self.data / "map_00003.dat"
        original = source.read_bytes()
        source.unlink()
        with self.assertRaisesRegex(ValueError, "map 3: missing DAT"):
            export_kept(self.data, self.review, self.out)
        source.write_bytes(b"\x0a\0\0" + tag({"data": {"colors": bytes(16384)}})[1])
        with self.assertRaisesRegex(ValueError, "map 3: review pixel key"):
            export_kept(self.data, self.review, self.out)
        self.assertFalse(self.out.exists())
        source.write_bytes(original)
        self.assertEqual(export_kept(self.data, self.review, self.out), 2)
