# Framed Map Atlas

Recover Minecraft Java map art NAMES and assign them to their respective map art IDs from an extracted world backup. Additional tools attempt to infer multi-tile art pieces via name key matching and geometric relation.

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

## Reconstruct multi-tile displays for review

Use the kept export's manifest and PNGs together with an existing scan's `frames.csv`. Choose the fullest scan of the same map-ID namespace. A scan previously restricted to selected IDs can leave out neighboring frames. If the report covers unrelated saves that reuse IDs, select the matching world with `--world "world"` (use its exact CSV value).

```powershell
python -m pip install "Pillow>=9.1"
python reconstruct_artworks.py --manifest "kept-maps\manifest.csv" --frames "map-report\frames.csv" --map-index "sorted-maps\map-index.csv" --out "artwork-review"
```

Open `artwork-review/review.html`. The gallery starts with filled rectangles, then shows partial layouts. Click a composite PNG to inspect it or expand the tile list to see original IDs, chosen names, and each display's location. Gold crosses indicate scanned IDs outside the kept manifest. Gray cells have no tile assigned to that candidate. A full rectangle describes the observed grid; adjoining pieces can still be grouped together, and a missing outer row can make a partial artwork look complete.

To update the review controls in an existing gallery, run the following from the extracted source folder. The generator reads `layouts.json` and `ungrouped.csv` and writes the new `review.html` beside them. Keep `artwork_review.html` beside `reconstruct_artworks.py` in the source folder; it supplies the template for both new and refreshed galleries.

```powershell
python reconstruct_artworks.py --refresh-gallery "artwork-review"
```

### Search and review layouts

The gallery opens on **Unreviewed**. Search the artwork title, chosen tile names, or available observed names. Enter an exact ID such as `#25970`, `25970`, or `map_25970.png` to highlight every matching cell, including repeats and excluded tiles. Artist search looks for possible credits within those names, such as `by Alex & Sam`, `Moon - Alex`, or `Moon 1-2 Alex`. Credits are inferred from naming conventions. Use the general name search for unusual credit formats.

Filters combine across the entire collection. Set width and height independently, choose layouts with or without excluded tiles or gaps, or select a source canvas. Click a canvas on a card to see every layout using that exact dimension, center, and scale across all decisions. **Clear filters** also restores the full collection.

Use **Keep artwork**, **Ignore grouping**, or **Needs investigation** per layout. Choose **Unreviewed** to clear a decision. The review status filter lets you revisit any decision. Decisions are saved locally in this browser, separately from the tile sorter's Keep choices. Use **Export artwork decisions** to share or back them up. **Import artwork decisions** accepts a matching collection, fills missing decisions, and reports conflicts while keeping the local reviewer's choice.

**Mark tiles for removal** opens a checklist of distinct kept IDs on the layout. On first opening, only IDs appearing in this layout alone in the current gallery are checked. Shared IDs start unchecked. Expand a shared ID to open its other layouts in separate tabs and see each decision. Untick any tile worth keeping individually. **Cancel** discards the draft. Apply the selection to move the layout into the **Remove tiles** queue. **Edit removal** reopens the saved checklist. Changing the layout to another decision withdraws its removal requests. If two layouts marked the same ID, withdraw both requests to restore it. An explicit shared-ID selection also affects a layout marked **Keep artwork**.

### Join fragments from different groups

Open **Merge with another group** on the main layout. Find the fragment by group ID, tile ID, or name, select the match, then click **Add group**. The preview starts with the fragment on the right. Set its **Top row** and **Left column**, or click **Place on preview** and then the cell where the fragment's top-left corner belongs. You can add more groups before saving.

| Repair | Placement |
| --- | --- |
| Fill two adjacent gray cells in a 3×5 layout | Use the 3×5 as the first group. Place the two-tile fragment at the first missing cell. The preview should read **3 × 5 · 15 placed · 0 gaps**. |
| Join two vertical 1×2 layouts | Leave the first at row 1, column 1. Put the second at row 1, column 2. The result is **2 × 2**. For two horizontal strips, place the second at row 2, column 1. |

Conflicting occupied cells are outlined in red and must be resolved before saving. If a gold cross covers the intended spot, the checkbox explicitly allows a kept tile to cover that excluded placement in this merge. A merge preserves the displayed orientation of each source group and each tile's rotation. Give the artwork a name and choose **Save merged artwork**. Use **Saved merged artworks** to reopen, reposition, or undo it.

**Export artwork decisions** includes the corrections in `merged_artworks`. Each entry records the title, source group offsets, final width and height, and a row-major list of tiles with one-based row/column, original map ID, rotation, and eligibility for the migration manifest. Repeated map IDs keep every placement. Earlier decision exports still import. Source-group review decisions and tile recovery/removal choices are managed separately.

### Generate the final migration manifest

**Recover excluded tiles** appears on layouts with gold crosses. The checklist gives the original ID and every affected row and column. To include thumbnails, refresh with `--data-dir "D:\Backup\world\data"` or `--map-index "sorted-maps/map-index.csv"`. Check the pieces you recognize and save. Available thumbnails appear in place on the review card, using the recorded rotations. The linked composite PNG remains the original scan preview. Gray gaps have no observed ID.

Recovery is a collection-wide choice per original ID, independent of layout status. Uncheck an ID from any matching layout to undo it. Importing reviewer exports combines selected recovery IDs. Export the final combined decisions after making any corrections.

Export the final combined decisions from the reviewed gallery, then run:

```powershell
python finalize_manifest.py --manifest "kept-maps/manifest.csv" --gallery "artwork-review" --decisions "artwork-decisions.json" --out "kept-maps/migration-manifest.csv"
```

The helper verifies the gallery collection and the original kept IDs and pixel hashes. It writes a new CSV with the original columns, chosen names, and asset references. Only explicitly selected numeric IDs are omitted. Pixel-identical copies with different IDs and kept maps outside all groupings remain eligible. Your original manifest, DATs, and PNGs stay available. Use the same gallery that produced the decisions file. Sharing checks cover the layouts observed in that gallery.


Open **Source canvases** on an artwork to see each map-making canvas's dimension, center X/Z, scale, and placement count. Under **Tiles and displays**, find a gold cross by row and column to see its original map ID and source canvas. To search for a missing piece, filter the original sorter's `map-index.csv` by that dimension, `center_x`, `center_z`, and `scale`. Its `path` column leads to every indexed image made on that canvas. Other tiles' canvases are leads for gray gaps, which have no recorded map ID in the layout.

Use a full `--map-index` from the same save to look up kept and excluded IDs. Alternatively pass `--data-dir "D:\Backup\world\data"` for the original DATs. With neither option the helper reads the DAT paths in the kept manifest. Unavailable coordinates are shown as unknown. In `layouts.json`, each tile carries its `source_canvas` and each layout lists its distinct `source_canvases`; frame coordinates are recorded separately under `displays`.

The helper groups touching frames on the same face and plane. It uses the frame item's observed name for grouping when available and otherwise tries the chosen name. It renders each kept tile at its displayed quarter-turn, with north at the top on floors and ceilings. An absent rotation uses the default angle and is flagged. Several placements of one ID remain separate tiles. Displays with the same ordered IDs, grid positions, and preview rotations share one preview and retain each location. Different original IDs remain distinguishable even if their pixels match.

The output folder can be shared as a whole. `previews/` holds composite PNGs up to 1,024 pixels on the longer side. `layouts.json` records each candidate's dimensions, one-based row and column, original IDs, chosen names, pixel keys, quarter-turns, and observed displays with coordinates and raw rotations. `ungrouped.csv` accounts for kept IDs outside every candidate, including IDs absent from the supplied scan. Conflicting placements and other unplaceable kept frames appear in `unplaced_frames` in the JSON. All three outputs use the original map IDs.

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


# Result file reference

**Which page do I open?** Open `sorted-maps/review.html` to choose which individual map IDs to keep and what to name them. Open `artwork-review/review.html` to inspect reconstructed multi-tile displays. The numbered pages beside it belong to the same gallery. Folder names below match the examples in this README. Your `--out` choices determine their locations.

### Map sorting and reviewer decisions

| Result | Produced by · source | What you use it for |
| --- | --- | --- |
| `sorted-maps/map-index.csv` | `sort_canvases.py` or the Map Art Sorter app · original map DATs | Look up an original ID, PNG path, pixel hash, or canvas center. Give the full index to reconstruction as `--map-index` to locate excluded tiles too. |
| `sorted-maps/footprints.csv` | Same sorter run · maps grouped by dimension, center X/Z, and scale | Find a canvas folder and see how many IDs and distinct nonuniform images it contains. |
| `sorted-maps/…/map_<id>.png` | Same sorter run · rendered DAT pixels or supplied `--png-dir` images | Inspect individual tiles. The `path` column in `map-index.csv` gives each PNG’s exact location. |
| `sorted-maps/review.html` | Created by the sorter. `review_gallery.py` can rebuild it from `map-index.csv` and name reports. | Review tiles, select names, and export decisions. Keep it with its PNG folders. |
| `map-review.csv` | **Export review CSV** in the sorter’s review page · decisions and chosen names in the browser | This is the finalized reviewer handoff. Find it in the browser’s download location. Pass it as `--review-csv` to `export_kept.py`, `curated_names.py`, or a Keep-filtered world scan. |
| `map-dat-metadata.csv` | `scan_map_dats.py` · original DAT folder | Inspect saved NBT fields and optional pixel keys without scanning world regions. |

### World scan and name recovery

| Result | Produced by · source | What you use it for |
| --- | --- | --- |
| `map-report/frames.csv` | `scan_maps.py` · filled maps in ordinary and glow item frames | Give this to reconstruction as `--frames`. Its XYZ, facing, and rotation describe where a tile was **displayed**. The sorter’s canvas X/Z describes the area the map **depicts**. |
| `map-report/containers.csv` | `scan_maps.py --include-containers` · saved item stacks in supported inventories | Trace a recovered name to its holder and slot. |
| `map-report/map-names.json` | `scan_maps.py` · observed names on framed and, when enabled, stored maps | Supply as `--names-report` to the name matcher, sorter, or gallery updater. |
| `map-report/scan-summary.json` | `scan_maps.py` · scan counters, selection, and read errors | Check which IDs and sources the scan covered before interpreting missing results. |
| `map-report/artwork-groups.json` | `scan_maps.py` · neighboring frame observations and observed names | Inspect the scan’s initial grouping guesses. The reconstruction helper reads `frames.csv` directly to build the kept-artwork gallery. |
| `curated-map-names.csv` | `curated_names.py` · DAT pixels, `map-review.csv`, and one or more scan name reports | Load candidate names and their source IDs into the sorter’s review page. A reviewer’s final choice goes into `map-review.csv`. |
| `map-exports/map-exports.json` and `map-exports/maps/world-001/map_<id>.dat` or `.png` | `scan_maps.py --export-maps` · IDs in a new or existing scan report plus the source DATs | Inspect assets for scan-observed IDs. The JSON identifies the world for each numbered folder and records missing or failed exports. |

### Kept export and reconstructed artwork

| Result | Produced by · source | What you use it for |
| --- | --- | --- |
| `kept-maps/manifest.csv` | `export_kept.py` · finalized `map-review.csv` and original DAT folder | Pass as `--manifest` to reconstruction. One row per kept original ID gives its verified pixel hash, chosen name, and relative asset paths. |
| `kept-maps/dats/map_<id>.dat` and `kept-maps/pngs/map_<id>.png` | Same kept export · byte-for-byte DAT copies and PNGs rendered from them | Keep both folders beside the manifest. Reconstruction uses these PNGs for tiles and can read the DATs for canvas coordinates. |
| `artwork-review/review.html` and `review-2.html`, … | `reconstruct_artworks.py` · kept manifest and PNGs, scan frames, and optional full canvas index or DAT folder | Open `review.html` first. Each page shows up to 48 layouts. Share the whole output folder. |
| `artwork-review/previews/<group>.png` | Same reconstruction · placed and rotated tiles | See a composite image of one candidate display. |
| `artwork-review/layouts.json` | Same reconstruction · kept IDs joined to frame placements and source canvases | Look up tile order, original IDs, rotations, canvas centers, gaps, and observed display locations for each candidate. |
| `artwork-review/ungrouped.csv` | Same reconstruction · kept manifest compared with candidate layouts | Find kept IDs outside every multi-tile candidate and the reason for each. |
