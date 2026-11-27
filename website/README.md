# Build the website from a finalized Framed Map Atlas migration

This `website/` folder containing a gallery website generator and the gallery files. Run the following from the Atlas root after `finalize_manifest.py` has produced the migration.

```powershell
python -m pip install Pillow
python website/build_gallery.py --migration migration --out website-dist
```

| Stage | Input | Output |
| --- | --- | --- |
| Finalize | Kept maps, reviewed layouts and artwork decisions | `migration/migration-manifest.csv` and `migration/migration-manifest-assets/` |
| Convert | The complete `migration/` directory | `website-dist/index.html`, gallery and export scripts, and `collection/` with catalog, previews and downloadable map data |
| Preview or deploy | Contents of `website-dist/` | The browsable gallery and both selection exports |

The generator discovers the finalized CSV and its matching `<manifest-stem>-assets/artworks.json`, then reads the DAT and PNG paths from the CSV. Destination IDs, names and placements come from the finalizer. Multiple exports in one folder can be disambiguated by passing the exact CSV path to `--migration`. Choose a fresh output directory for each build.

Preview the site with `python -m http.server 8000 --bind 127.0.0.1 --directory website-dist` and open `http://localhost:8000/`. When ready, deploy the contents of `website-dist/` with `index.html` at the publishing root.

Gallery destination IDs start at **100,000**. For the first build in this range, re-run finalization in a fresh output folder and omit any previous low-ID mapping. Downloaded map files use the gallery IDs directly. See `IMPORT.md` for installation and collision handling.
