import csv
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image
from build_gallery import SITE_FILES, build, migration_inputs


class WebsiteBuildTest(unittest.TestCase):
    def test_custom_manifest_name_builds_a_whole_site(self):
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            assets = root / "review-1-assets"
            assets.mkdir()
            Image.new("RGB", (128, 128), "green").save(assets / "100042.png")
            (assets / "100042.dat").write_bytes(b"fixture bytes")
            manifest = root / "review-1.csv"
            with manifest.open("w", encoding="utf-8-sig", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(("original_map_id", "destination_map_id", "id_source_original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path"))
                writer.writerow((42, 100042, 42, "image", "Fern", "review-1-assets/100042.dat", "review-1-assets/100042.png"))
            layouts = assets / "artworks.json"
            layouts.write_text(json.dumps({"format": "framed-map-atlas/box-artworks-v1", "artworks": []}))
            self.assertEqual(migration_inputs(root), (manifest, layouts))
            out = root / "built"
            build(*migration_inputs(root), out, site=True)
            self.assertTrue(all((out / name).is_file() for name in (*SITE_FILES, ".nojekyll")))
            self.assertIn("Fern", (out / "collection/catalog.js").read_text(encoding="utf-8"))
            self.assertEqual((out / "collection/maps/0.bin").read_bytes(), b"fixture bytes")
            self.assertTrue((out / "collection/images/100042.png").is_file())
            with self.assertRaisesRegex(ValueError, "fresh"):
                build(manifest, layouts, out, site=True)
            (root / "another.csv").touch()
            (root / "another-assets").mkdir()
            with self.assertRaisesRegex(ValueError, "exact finalized CSV"):
                migration_inputs(root)
            self.assertEqual(migration_inputs(manifest), (manifest, layouts))
            with self.assertRaisesRegex(ValueError, "Missing artwork layouts"):
                migration_inputs(root / "another.csv")
