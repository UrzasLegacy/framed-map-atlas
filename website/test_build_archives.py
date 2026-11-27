import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from PIL import Image
from build_gallery import SITE_FILES


class ArchiveBuildTest(unittest.TestCase):
    def test_independent_cli_packages_and_failed_rebuild(self):
        script = Path(__file__).with_name("build_archives.py").resolve()
        with tempfile.TemporaryDirectory() as scratch:
            root = Path(scratch)
            web = root / "public"
            # No migration exists, and -S disables site packages (including Pillow).
            subprocess.run([sys.executable, "-S", str(script), "site"], cwd=root, check=True)
            with ZipFile(root / "site.zip") as site:
                self.assertEqual(set(site.namelist()), {*SITE_FILES, ".nojekyll"})
                site.extractall(web)

            migration = root / "migration"
            assets = migration / "maps-assets"
            assets.mkdir(parents=True)
            Image.new("RGB", (128, 128), "green").save(assets / "100042.png")
            (assets / "100042.dat").write_bytes(b"fixture")
            with (migration / "maps.csv").open("w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(("original_map_id", "destination_map_id", "id_source_original_map_id",
                                 "pixel_key", "chosen_name", "dat_path", "png_path"))
                writer.writerow((42, 100042, 42, "fixture", "Fern",
                                 "maps-assets/100042.dat", "maps-assets/100042.png"))
            (assets / "artworks.json").write_text(json.dumps({
                "format": "framed-map-atlas/box-artworks-v1", "artworks": []}))
            cmd = [sys.executable, str(script), "collection", "--migration", str(migration)]
            subprocess.run(cmd, cwd=root, check=True)
            out = root / "collection.zip"
            with ZipFile(out) as collection:
                self.assertEqual(set(collection.namelist()), {"collection/catalog.js",
                                 "collection/maps/0.bin", "collection/images/100042.png"})
                collection.extractall(web)
            self.assertIn("collection/catalog.js", (web / "index.html").read_text(encoding="utf-8"))
            self.assertIn("Fern", (web / "collection/catalog.js").read_text(encoding="utf-8"))
            self.assertEqual((web / "collection/maps/0.bin").read_bytes(), b"fixture")
            good = out.read_bytes()
            (assets / "100042.png").unlink()
            failed = subprocess.run(cmd, cwd=root, capture_output=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertEqual(out.read_bytes(), good)
