# Build the site and collection separately

From the Framed Map Atlas repository root, build either ZIP independently. Each command writes into the current directory by default. Use `--out "path/to/name.zip"` to change the destination. A successful rebuild replaces that ZIP; a failed build leaves the previous ZIP in place.

| Command | Inputs | ZIP contents |
| --- | --- | --- |
| `python website/build_archives.py site` | The gallery source files and Python 3.10+ | `site.zip`: HTML, CSS, browser scripts, classic builder, world importer, import instructions and `.nojekyll` at the web root |
| `python website/build_archives.py collection --migration migration` | A finalized migration directory and Pillow | `collection.zip`: `collection/catalog.js`, tile and artwork previews in `collection/images/`, and downloadable map blocks in `collection/maps/` |

## Site maintainers

Commit the tooling and gallery source under `website/`. Build and distribute `site.zip` when the interface, command packing or importer changes. The site build uses the Python standard library and can run in a checkout with no migration or artwork. The archive uses an explicit list of runtime files, including the scripts needed for visitors' world downloads.

## Collection maintainers

Install Pillow in your build environment with `python -m pip install Pillow`. Run the collection command wherever you have the finalized CSV, the matching `<manifest-stem>-assets/artworks.json` and referenced DAT/PNG files. If a migration folder contains several exports, pass the exact finalized CSV to `--migration`.

The collection builder uses destination IDs, chosen names and placements from `finalize_manifest.py`. Gallery IDs use **100,000 + original map ID**. Pixel-identical copies share the ID based on their lowest eligible original at first assignment. For the first offset build, re-finalize in a fresh folder without a sequential ledger. The downloaded DATs use these gallery IDs directly.

Send `collection.zip` to the server admin separately. It contains the compiled gallery data, names, previews and downloadable map content. Keep it, generated collection folders and migration data outside the public source repository. Git ignore rules apply to untracked files; check any previously committed data separately.

## Server admin

1. Extract **both ZIPs into the same directory**. You should see `index.html` and `collection/catalog.js` underneath that directory. Each ZIP already has the correct internal paths.
2. To preview, run `python -m http.server 8000 --bind 127.0.0.1` there and open `http://localhost:8000/`.
3. Serve that directory as the static site. For a site-only update, replace the site files and keep the collection. For a collection-only update, replace the entire `collection/` directory with the new one to retire removed assets. If your host deploys a complete directory snapshot, submit the assembled directory with both parts present.

The directory-output workflow is also available with `python website/build_gallery.py --migration migration --out website-dist`. See `IMPORT.md` for Minecraft installation and collision handling.
