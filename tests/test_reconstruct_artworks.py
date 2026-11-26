import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from reconstruct_artworks import annotate_canvases, artist_credit, build
from test_scanner import tag

try:
    from PIL import Image
except ImportError:
    Image = None


class CanvasTest(unittest.TestCase):
    def test_artist_credits_in_real_title_formats(self):
        for name, expected in [("Moon by:Rose & Fox 1-2", "Rose & Fox"), ("Moon 0_1 SZX", "SZX"),
                               ("Moon - Alex", "Alex"), ("Floral Wallpaper", ""), ("Moon 0-1", ""),
                               ("1-2 Moon Over Water", ""), ("1-2 -Lou, RLG", "Lou, RLG")]:
            with self.subTest(name=name):
                self.assertEqual(artist_credit(name), expected)

    def test_canvas_lookup_from_kept_and_excluded_dats(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            originals = root / "world-data"
            originals.mkdir()
            kept = {4: {"dat_path": "map_4.dat"}}
            def dat(path, dimension):
                nbt = {"data": {"xCenter": -64, "zCenter": 0, "dimension": dimension, "scale": 1}}
                path.write_bytes(gzip.compress(b"\x0a\0\0" + tag(nbt)[1]))
            dat(root / "map_4.dat", "minecraft:overworld")
            dat(originals / "map_5.dat", "minecraft:the_end")
            art = {"tiles": [dict(original_map_id=i, kept=i in kept) for i in (4, 5, 4, 6)]}
            manifest = root / "manifest.csv"
            annotate_canvases([art], kept, manifest)
            self.assertEqual(art["source_canvases"][0]["placements"], 2)
            self.assertIsNone(art["tiles"][1]["source_canvas"])
            annotate_canvases([art], kept, manifest, data_dir=originals)
            self.assertEqual([(c["dimension"], c["center_x"], c["center_z"], c["scale"], c["excluded"])
                              for c in art["source_canvases"]],
                             [("minecraft:overworld", -64, 0, 1, 0), ("minecraft:the_end", -64, 0, 1, 1)])
            self.assertIsNone(art["tiles"][3]["source_canvas"])


@unittest.skipUnless(Image, "preview rendering needs Pillow")
class ReconstructionTest(unittest.TestCase):
    def test_rows_rotations_holes_repetition_and_display_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            img = Image.new("RGBA", (128, 128), "black")
            img.putpixel((0, 0), (255, 0, 0, 255))
            img.save(root / "tile.png")
            manifest = root / "manifest.csv"
            with manifest.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["original_map_id", "chosen_name", "pixel_key", "png_path"])
                for i in (3, 4, 7, 8, 10, 11):
                    w.writerow([i, 'Moon <sky>', 'key', 'tile.png'])
            frames = root / "frames.csv"
            with frames.open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow("frame world dimension map_id name x y z position_source facing rotation".split())
                number = 0
                for plane, offset in [(20, 0), (40, 4)]:
                    for x,y,i,r in [(0,8,3,1), (1,8,3,0), (2,8,99,0), (0,7,4,0)]:
                        number += 1
                        w.writerow([number, "world", "overworld", i, "", x,y,plane,"Tile","south", r+offset])
                for i, x in ((7,10),(10,20),(11,20)):
                    number += 1
                    w.writerow([number,"world","overworld",i,"",x,8,20,"Tile","south",0])
            out = root / "out"
            index = root / "map-index.csv"
            index.write_text("map_id,dimension,center_x,center_z,scale,pixel_sha256\n"
                             "3,minecraft:overworld,-128,0,0,key\n99,minecraft:overworld,0,64,2,other\n")
            self.assertEqual(build(manifest, frames, out, map_index=index), (1, 2, 4))
            report = json.loads((out / "layouts.json").read_text(encoding="utf-8"))
            layout, = report["layouts"]
            self.assertEqual((layout["width"],layout["height"],layout["gaps"],layout["excluded"]), (3,2,2,1))
            self.assertEqual([t["original_map_id"] for t in layout["tiles"]], [3,3,99,4])
            self.assertEqual([c["placements"] for c in layout["source_canvases"]], [2,1])
            self.assertEqual(layout["source_canvases"][1]["excluded"], 1)
            self.assertIsNone(layout["tiles"][-1]["source_canvas"])
            self.assertEqual(layout["displays"][1]["placements"][0]["rotation"], 5)
            self.assertEqual(len(report["unplaced_frames"]), 2)
            with Image.open(out / layout["preview"]) as composite:
                self.assertEqual(composite.size, (384,256))
                self.assertEqual(composite.getpixel((127,0)), (255,0,0,255))
                self.assertEqual(composite.getpixel((128,0)), (255,0,0,255))
                self.assertNotEqual(composite.getpixel((260,20)), composite.getpixel((260,140)))
            html = (out / "review.html").read_text(encoding="utf-8")
            payload = html.split('id="data">', 1)[1].split('</script>', 1)[0]
            self.assertNotIn("<sky>", payload)
            art, = json.loads(payload)["cards"]
            self.assertEqual(art["title"], "Moon <sky>")
            excluded = art["tiles"][2]
            self.assertEqual(excluded[:5], [1, 3, 99, "", False])
            self.assertEqual(art["canvases"][excluded[5]][0], "minecraft:overworld · X 0 / Z 64 · scale 2")
            with (out / "ungrouped.csv").open(encoding="utf-8-sig") as f:
                reasons = {int(r["original_map_id"]): r["reason"] for r in csv.DictReader(f)}
            self.assertEqual(reasons, {7:"observed as a single tile",8:"no frame observation",
                                       10:"no usable placement",11:"no usable placement"})
