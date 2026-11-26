import csv
import io
import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from map_assets import render
from sort_canvases import load_titles, main, review_page
from test_scanner import tag


class CanvasSortTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()

    def dat(self, i, x, pixels):
        saved = {"colors": pixels, "dimension": "minecraft:the_end", "xCenter": x, "zCenter": -1024}
        path = self.data / f"map_{i}.dat"
        path.write_bytes(b"\x0a\0\0" + tag({"data": saved})[1])
        return path

    def test_rank_by_distinct_pixels_and_preserve_all_ids(self):
        a = bytes([4, 5]) * 8192
        for i,x,pixels in [(2,0,a), (25,0,a), (26,0,a[::-1]), (27,0,bytes(16384)),
                           (28,0,bytes([3])*16384), (29,0,bytes([5])*16384), (30,128,a)]:
            self.dat(i, x, pixels)
        archive = self.root / "out.zip"
        self.assertEqual(main([str(self.data), "--out", str(archive), "--frequent-at", "2"]), 0)
        with ZipFile(archive) as z:
            html = z.read("review.html").decode()
            sites = json.loads(html.split('id="data">', 1)[1].split("</script>", 1)[0])
            preview = sites[0]["tiles"]
            self.assertEqual(sites[0]["count"], 2)
            self.assertEqual(next(t["ids"] for t in preview if t["id"] == 2), [2, 25])
            self.assertEqual({i for site in sites for t in site["tiles"] for i in t["ids"]},
                             {2, 25, 26, 27, 28, 29, 30})
            rows = list(csv.DictReader(io.StringIO(z.read("map-index.csv").decode("utf-8-sig"))))
            self.assertEqual([int(r["map_id"]) for r in rows], [2, 25, 26, 27, 28, 29, 30])
            paths = {int(r["map_id"]):r["path"] for r in rows}
            self.assertIn("01_frequent/the_end/x_0_z_-1024_scale_0/copies/map_00025.png", paths[25])
            self.assertIn("/blank/copies/", paths[28])
            self.assertIn("/solid/", paths[29])
            self.assertTrue(paths[30].startswith("03_single_image/"))
            self.assertEqual(rows[1]["representative_id"], "2")
            self.assertEqual(rows[-1]["representative_id"], "30")
            self.assertEqual(z.read(paths[2]), z.read(paths[25]))
            sites = list(csv.DictReader(io.StringIO(z.read("footprints.csv").decode("utf-8-sig"))))
            self.assertEqual([(r["map_ids"], r["distinct_nonuniform"]) for r in sites], [("6","2"),("1","1")])

    def test_existing_pngs_keep_names_and_unlocated_ids(self):
        path = self.dat(25, 0, bytes([5])*16384)
        images = self.root / "png"
        images.mkdir()
        payload = render(path)
        for name in ("map_25.png","map_99999.png"):
            (images / name).write_bytes(payload)
        out = self.root / "sorted"
        self.assertEqual(main([str(self.data), "--out",str(out),"--png-dir",str(images)]), 1)
        self.assertEqual((out/"unclassified/map_99999.png").read_bytes(), payload)
        self.assertEqual((out/"04_uniform_only/the_end/x_0_z_-1024_scale_0/solid/map_25.png").read_bytes(), payload)
        self.assertTrue((images/"map_25.png").is_file())

    def test_review_merges_name_sources_across_footprints_and_escapes_titles(self):
        report = self.root / "map-names.json"
        title = "</script> & 雪"
        report.write_text(json.dumps([{"map_id": 42, "name": None, "observed_names": {title: 2, "Alias": 1}}]))
        catalog = self.root / "curated-map-names.csv"
        with catalog.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["map_id", "name_source_ids"])
            writer.writerow([25, json.dumps({"Alias": [42], "CSV only": [90]})])
        rows = [{"map_id": i, "pixel_sha256": digest, "path": f"{i}/map_{i}.png"}
                for i, digest in [(25, "abc"), (42, "abc"), (51, "other")]]
        plain = review_page(rows).decode()
        page = review_page(rows, load_titles([report, catalog])).decode()
        def store_key(html):
            return html.split('const storeKey = "', 1)[1].split('"', 1)[0]
        self.assertEqual(store_key(page), store_key(plain))
        encoded = page.split('id="names">', 1)[1].split("</script>", 1)[0]
        self.assertEqual(json.loads(encoded), {"abc": {"Alias": [42], title: [42], "CSV only": [90]}})
        self.assertNotIn("<", encoded)
