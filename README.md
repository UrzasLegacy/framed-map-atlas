# Framed Map Atlas

Recover Minecraft Java map art NAMES and assign them to their respective map art IDs from an extracted world backup. Additional tools attempt to infer tile multi-tile art pieces via name key matching and geometric relation.

Start with extracting map images from dats, choose the artwork you want to keep, then search frames and containers for the names players gave those tiles. The catalog keeps each candidate title and the IDs on which that title was observed.

## Requirements

Use Python 3.10 or newer. Run the commands below from the extracted source folder. Ordinary Java map DATs and gzip, zlib, or uncompressed region chunks use the Python standard library. If the scan reports an LZ4 chunk that needs a decoder, install it with `python -m pip install lz4`. The optional desktop sorter uses Tkinter.

Use one backup snapshot and one shared map-ID namespace per catalog. For a Java 1.21 save, the map DAT directory is usually `world/data`. The DAT tools also accept a folder of numeric filenames such as `25.dat`. For the world scan, choose the parent containing all relevant dimension folders. Replace the example paths with yours. Give each scan or export a fresh output path.

## 1. Review and curate the DATs

```powershell
python sort_canvases.py "D:\Backup\world\data" --out "sorted-maps"
```

Open `sorted-maps/review.html`. The **Theme** menu offers **System**, **Light**, and **Dark**. System follows your device preference. A manual choice is saved in the browser when local storage is available. Each canvas groups maps by dimension, center coordinates, and scale. Repeated use of one canvas is a lead for finding artwork. Click a canvas or **All distinct images** to see its tiles. Use **Find** with an ID, a PNG filename, a name from a loaded report, a full pixel key, or a prefix such as `key:7fa2`.

On a canvas card, **Keep**, **Terrain**, and **Unsure** mark every map ID at that footprint, including copies, blank tiles, and tiles hidden by a filter. The same controls appear inside the canvas. Other canvases keep their decisions, even when they contain identical artwork. **Undo last bulk decision** restores the prior values until the next decision edit, import, or reload.

### Mark likely terrain in bulk

Open **Terrain suggestions** to preview the eligible maps and the pixel evidence for each. **Mark all as Terrain** applies the decision across every page of suggestions. You can override any tile afterward, undo the last batch, or export the decisions in the normal review CSV. Opening the gallery alone leaves the review untouched.

The sorter considers unlocked Overworld tiles with known coordinates, a unique image ID, and just one distinct nonuniform image at their footprint in the collection. Use the full DAT directory so the sorter can see reused canvases and copies. A candidate needs at least 2,048 explored pixels. At least 95% of those pixels must belong to common surface-color families, with three families each covering at least 2%. The pixel pattern must vary across rows and columns and show stronger color agreement between immediate neighbors than between pixels eight blocks apart. These are heuristics: landscape artwork can qualify, and oceans, snowy areas, and heavily built terrain can be missed.

The gallery skips tiles with a title in the loaded reports, a name selected in the review, or an existing decision. Supply name reports when available. The sorter writes pixel evidence to `terrain_reason` in `map-index.csv`, so refreshing the gallery preserves it. For collections made before this feature, sort the DATs into a new directory to calculate it.

### Review named maps in bulk

Open **Named map suggestions** for unreviewed IDs with a recorded title, a title from an identical pixel match, or a chosen name. Identical images share a card. The count on the button is the number of eligible map IDs. **Mark all as Keep** applies to those IDs across every page, preserves their name choices, and supports **Undo last bulk decision**. IDs with an existing decision or an explicitly cleared name stay out of the queue. Tiles with several candidate titles remain available for review even before you pick a name.

**Entries per page** defaults to 72 in every view. Select 36, 144, or 288 to change how many canvases or images appear at once.

To review a tile individually, open a canvas or choose **All distinct images**. If a pixel key has exactly one distinct candidate title, **Chosen name** defaults to it for every matching ID, even when several source IDs supplied that title. With multiple candidates, choose one manually. You can clear a default with **No name selected**. Manual choices take priority. Inside a canvas, tile edits apply to that canvas’s copies. In **All distinct images**, they apply to every ID with those pixels. Look up one numeric map ID to edit just that copy. Bulk canvas decisions preserve chosen names.

The progress counter counts individual map IDs, including blank and solid-color copies. A second count shows progress on nonuniform maps. Choose **Review remaining IDs** to see every ID still missing a decision, even when blank and solid maps are hidden in the gallery. Each card groups the pending copies of one image. Decisions and name choices in this view apply only to those pending copies.

Click **Export review CSV** and save `map-review.csv` in your working folder. Each reviewed or named ID gets a row with `map_id`, `pixel_key`, `decision`, and `chosen_name`. The export includes single-candidate defaults even on tiles you haven’t opened. A name alone leaves the decision blank. Clearing a default is also saved and exported. The scanner and name matcher select rows marked **Keep**. Include the full DAT directory when building the gallery so copies under other IDs are eligible.

Use **Import review** to restore decisions and names. An import fills missing choices and preserves existing choices when reviewers disagree.

The sorter preserves each numeric ID in its PNG filename. `map-index.csv` records each ID, image path, footprint, pixel key, representative ID, and read issues. `footprints.csv` ranks canvases by distinct nonuniform images. The default frequent tier starts at ten images. Use `--frequent-at` to adjust it, `--png-dir` to organize existing PNGs from the same save, or a fresh `.zip` as the output to share a gallery. Extract a gallery ZIP in full before opening the page.

**Optional metadata inventory.** To inspect the DATs without rendering images or reading regions, run:

```powershell
python scan_map_dats.py "D:\Backup\world\data" --pixel-keys --out "map-dat-metadata.csv"
```

This produces one row per DAT with its ID, dimension, canvas center, scale, flags, and additional saved tags in `extra_nbt_json`. Saved frame markers appear there when present. Omit `--pixel-keys` for metadata alone. The gallery and DAT inventory use the same pixel-key calculation. A map's center locates its depicted area. Custom titles on individual copies are recovered in the world scan.

### Export kept DATs and PNGs

Run this after finalizing the review. Point it at the original DAT folder and give it a new output folder.

```powershell
python export_kept.py "D:\Backup\world\data" --review-csv "map-review.csv" --out "kept-maps"
```

Each `Keep` ID gets a byte-for-byte copy in `dats/map_<id>.dat` and a 128×128 preview in `pngs/map_<id>.png`. The helper verifies the gallery pixel key against the copied DAT and renders the PNG from those same pixels. The export needs only the DAT folder and CSV.

`manifest.csv` lists `original_map_id`, `pixel_key`, `chosen_name`, `dat_path`, and `png_path` in numeric ID order. Paths are relative to the export folder. IDs with identical pixels keep their own rows and names. An empty chosen name stays empty. If a kept DAT is missing, ambiguous, unreadable, or has different pixels, the helper reports the error and publishes no partial export. After correcting the input, rerun the same command.

## 2. Search for names

```powershell
python scan_maps.py "D:\Backup" --include-containers --review-csv "map-review.csv" --workers 4 --out "map-report"
```

This searches saved ordinary and glow item frames plus supported block and entity inventories across the supplied dimensions. It descends into shulker boxes and bundles, including containers displayed in frames. The review CSV limits the retained observations to IDs marked **Keep**. The scanner still reads eligible region files, since a player can move a copy anywhere. Keep the region and entity directories and any external `.mcc` chunk files in their original layout.

Omit `--review-csv` to build a reusable catalog of all IDs in the scanned sources. For a frame-only search on a world created on Java 1.17 or later, use `--entities-only` instead of `--include-containers`. The default frame scan also reads terrain regions for legacy embedded entities. `--workers N` controls how many region files are read concurrently.

**Coverage.** The catalog captures all observed titles within the selected IDs and supported storage. Player inventories, personal Ender Chests, dropped items, plugin databases, and Bedrock saves are outside this scanner's scope. Read the issues in `scan-summary.json` before treating a search as complete. A title may survive on only one copy of an image.

## 3. Match titles across IDs and refresh the gallery

```powershell
python curated_names.py "D:\Backup\world\data" --review-csv "map-review.csv" --names-report "map-report" --out "curated-map-names.csv"
python review_gallery.py "sorted-maps" --names-report "curated-map-names.csv"
```

The CSV includes one row per requested ID, including IDs with no recovered name. `direct_names` lists names found on that ID. `name_source_ids` identifies every source ID for each candidate. `other_identical_ids` lists pixel-identical copies among the compared IDs. Matching uses SHA-256 of the visible Java 1.21 palette values in saved orientation. Blank or solid tiles can coincide across unrelated pieces. A missing or ambiguous DAT limits pixel matching, while titles recorded directly on an ID remain available.

Repeat `--names-report` on the catalog command to combine scans from the same map store. For saves with separate DAT folders and overlapping numeric IDs, build separate catalogs. The gallery updater accepts the candidate CSV or one or more scraper `map-names.json` files. It rebuilds the HTML from the existing index and reuses the PNGs. Export your review CSV before refreshing or moving a gallery, then import it if your browser opens the page without your previous decisions.

## World scan outputs

| File | Contents |
| --- | --- |
| `map-names.json` | Per-world map IDs, all observed names and their counts, and references to the observations. Conflicting names are preserved. |
| `frames.csv` | Framed map IDs and item titles with position, facing, rotation, region, chunk, and frame reference. The frame entity's own label has a separate field. |
| `containers.csv` | One row per recovered stored stack when container scanning is enabled, with its outer holder, slot path, item count, location, and title. |
| `scan-summary.json` | Scan coverage, read errors, and selection totals. With container scanning enabled, `container_stats` includes nonempty holders checked, stored map stacks before filtering, recovered and named stacks, unique world/ID pairs, conflicts, and counts by outer holder. |
| `artwork-groups.json` | Candidate multi-tile displays with positions, title evidence, gaps, and review notes. |

Artwork groups use adjacent, coplanar frames with a common facing, together with similarities in the tile names. An unnamed patch can join an adjacent named group. Conflicting neighboring groups and missing tiles are flagged for review. Rows and columns start at 1. For example, filter groups by width 3, height 3, and tile count 9 for complete 3×3 candidates.

## Export original DATs or PNGs from a scan

```powershell
python scan_maps.py "D:\Backup" --from-report "map-report" --export-maps both --out "map-exports"
```

Use `dat`, `png`, or `both`. Reusing a report skips region scanning. You can also add `--export-maps` to the original scan. `map-exports.json` records each source world and ID, file paths, and errors. DATs are copied byte for byte. PNGs show the saved tile at 128×128 with transparent unexplored pixels. The exporter checks legacy and newer DAT locations, including sibling Bukkit dimensions. For an explicit source, pass `--map-source "world_nether" "D:\Backup\world\data"` using the exact world key from the report.

## Desktop sorter and development

Run `python map_sorter_app.py` for the graphical sorter. Choose the world or DAT directory, a destination, and any optional name reports. Reviewers only need the generated gallery and a browser.

Run the test suite from the source folder:

```powershell
python -m unittest discover -s tests -v
```

To build a Windows executable, use Windows Python with Tkinter:

```powershell
python -m pip install pyinstaller
python -m PyInstaller --onefile --windowed --noupx --name "Map Art Sorter" map_sorter_app.py
```

Distribute the resulting `dist/Map Art Sorter.exe` as a separate release asset.
