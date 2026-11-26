"""Sort Java map PNGs by canvas reuse, dimension, center and scale, preserving map IDs."""

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from curated_names import catalog_names
from map_assets import index_dats, pixel_signature, read_map_data, render


def load_titles(reports):
    titles = defaultdict(lambda: defaultdict(set))
    for path in reports:
        try:
            if path.suffix.lower() != ".csv":
                for map_id, names in catalog_names([path]).items():
                    for name in names:
                        titles[map_id][name].add(map_id)
                continue
            with path.open(encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                if not {"map_id", "name_source_ids"}.issubset(reader.fieldnames or []):
                    raise ValueError("expected a curated-map-names.csv with map_id and name_source_ids")
                for row in reader:
                    target = int(row["map_id"])
                    evidence = json.loads(row["name_source_ids"])
                    if not isinstance(evidence, dict):
                        raise ValueError(f"invalid name_source_ids for map {target}")
                    for name, sources in evidence.items():
                        if not isinstance(sources, list) or any(type(i) is not int for i in sources):
                            raise ValueError(f"invalid source IDs for map {target}")
                        if sources:
                            titles[target][name].update(sources)
                        for source in sources:
                            titles[source][name].add(source)
        except (ValueError, TypeError, KeyError, csv.Error) as exc:
            raise ValueError(f"Could not read map names from {path}: {exc}") from exc
    return titles


def review_page(rows, titles=None):
    sites = {}
    evidence = defaultdict(lambda: defaultdict(set))
    terrain = {}
    for row in rows:
        path = row.get("path")
        if not path:
            continue
        parts = path.split("/")
        if any(s in ("", ".", "..") or "\\" in s or ":" in s for s in parts):
            raise ValueError(f"unexpected image path: {path!r}")
        folder = "/".join(parts[:3]) if len(parts) >= 4 and parts[0][:2].isdigit() else parts[0]
        site = sites.setdefault(folder, {"folder": folder, "dimension": row.get("dimension") or "unknown",
                                         "x": row.get("center_x"), "z": row.get("center_z"),
                                         "scale": row.get("scale"), "tiles": {}})
        map_id = int(row["map_id"])
        if row.get("terrain_reason"):
            terrain[map_id] = row["terrain_reason"]
        digest = row.get("pixel_sha256") or f"unclassified-{map_id}"
        if titles:
            for name, source_ids in titles.get(map_id, {}).items():
                evidence[digest][name].update(source_ids)
        tiles = site["tiles"]
        tile = tiles.setdefault(digest, {"key": digest, "kind": row.get("pixel_kind") or "unclassified", "ids": []})
        tile["ids"].append(map_id)
        if "id" not in tile or str(map_id) == str(row.get("representative_id")):
            tile.update(id=map_id, path=quote(path, safe="/"))
    for site in sites.values():
        site["tiles"] = sorted(site["tiles"].values(), key=lambda t: t["id"])
        site["count"] = sum(t["kind"] == "image" for t in site["tiles"])
    ordered = sorted(sites.values(), key=lambda s: (-s["count"], s["folder"]))
    data = json.dumps(ordered, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c")
    key = hashlib.sha256(data.encode("ascii")).hexdigest()[:24]
    names = {digest: {name: sorted(sources[name]) for name in sorted(sources, key=lambda n: (n.casefold(), n))}
             for digest, sources in evidence.items()}
    name_data = json.dumps(names, separators=(",", ":"), ensure_ascii=True).replace("<", "\\u003c")
    terrain_data = json.dumps(terrain, ensure_ascii=True, separators=(",", ":")).replace("<", "\\u003c")
    before_names, after_names = REVIEW_TEMPLATE.split("__MAP_NAMES__")
    return (before_names.replace("__COLLECTION_KEY__", key).replace("__MAP_DATA__", data)
            + name_data + after_names.replace("__COLLECTION_KEY__", key).replace("__TERRAIN_DATA__", terrain_data)).encode("utf-8")


# Vanilla surface palette families: grass, sand, foliage, snow, clay, dirt, stone, water and wood.
SURFACE_COLORS = (1, 2, 7, 8, 9, 10, 11, 12, 13)
BASE_INDEX = bytes(i // 4 for i in range(256))


def terrain_pattern(colors):
    """Explain a conservative pixel match. An empty string leaves the map for manual review."""
    base = colors.translate(BASE_INDEX)
    visible = len(base) - base.count(0)
    if visible < 2048:
        return ""
    counts = [base.count(i) for i in SURFACE_COLORS]
    surface = sum(counts) / visible
    if surface < 0.95 or sum(n >= visible * 0.02 for n in counts) < 3:
        return ""
    # Flags and repeating patterns can also consist of broad surface-color bands.
    if len({base[y:y+128] for y in range(0, len(base), 128)}) < 32:
        return ""
    if len({base[x::128] for x in range(128)}) < 32:
        return ""
    # Sample at a fixed stride. A landscape tends to agree with nearby pixels
    # more often than with pixels several blocks away. Ignore unexplored areas.
    pairs = near = far = 0
    for y in range(0, 120, 3):
        for x in range(0, 120, 3):
            p = y * 128 + x
            value = base[p]
            if not value:
                continue
            for step in (1, 128):
                adjacent, distant = base[p + step], base[p + 8 * step]
                if adjacent and distant:
                    pairs += 1
                    near += value == adjacent
                    far += value == distant
    if pairs < 250 or near < 0.73 * pairs or near - far < 0.10 * pairs:
        return ""
    return (f"{surface:.0%} surface colors. Adjacent pixels share a base color {near/pairs:.0%} "
            f"of the time, versus {far/pairs:.0%} eight pixels apart.")


def footprint(data):
    dimension = data.get("dimension")
    x, z, scale = data.get("xCenter"), data.get("zCenter"), data.get("scale", 0)
    if not isinstance(dimension, (str, int)) or not str(dimension) or not all(type(v) is int for v in (x, z, scale)):
        return None
    return dimension, x, z, scale


def folder_for(fp, count, threshold):
    if fp is None:
        return "unknown_footprint"
    dimension, x, z, scale = fp
    if count >= threshold:
        tier = "01_frequent"
    elif count >= 2:
        tier = "02_repeated"
    elif count == 1:
        tier = "03_single_image"
    else:
        tier = "04_uniform_only"
    if dimension in ("minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"):
        label = dimension.split(":")[1]
    else:
        label = "dim_" + quote(str(dimension), safe="")[:64] + "_" + hashlib.sha256(repr(dimension).encode()).hexdigest()[:12]
    return f"{tier}/{label}/x_{x}_z_{z}_scale_{scale}"


def png_files(folder):
    selected = defaultdict(list)
    for p in folder.iterdir():
        m = re.fullmatch(r"map_(-?[0-9]+)\.png", p.name, re.IGNORECASE)
        if m and p.is_file():
            selected[int(m[1])].append(p)
    if not selected:
        raise ValueError(f"no map_<ID>.png files in {folder}")
    return selected


def csv_table(columns, rows):
    text = io.StringIO(newline="")
    writer = csv.DictWriter(text, fieldnames=columns)
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode("utf-8-sig")


def sort_maps(data_dir, write, threshold, png_dir=None, names_reports=()):
    titles = load_titles(names_reports)
    dats = index_dats(data_dir)
    pngs = png_files(png_dir) if png_dir is not None else None
    ids = sorted(pngs if pngs is not None else dats)
    if not ids:
        raise ValueError(f"no map DAT files in {data_dir}")
    groups = defaultdict(lambda: defaultdict(list))
    rows = {}
    pattern_by_key = {}
    issues = 0
    for i in ids:
        row = rows[i] = {"map_id": i}
        try:
            if pngs is not None and len(pngs[i]) != 1:
                raise ValueError("multiple input PNGs have this numeric ID")
            paths = dats.get(i, [])
            if len(paths) != 1:
                raise ValueError(f"expected one matching DAT, found {len(paths)}")
            data = read_map_data(paths[0])
            fp = footprint(data)
            digest, kind = pixel_signature(data["colors"])
            row.update(pixel_kind=kind, pixel_sha256=digest)
            if kind == "image" and fp and fp[0] in ("minecraft:overworld", 0) and not data.get("locked"):
                if digest not in pattern_by_key:
                    pattern_by_key[digest] = terrain_pattern(data["colors"])
            if fp is not None:
                row.update(zip(("dimension", "center_x", "center_z", "scale"), fp))
            else:
                row["issue"] = "incomplete footprint; excluded from canvas reuse counts"
                issues += 1
            groups[fp][kind, digest].append(i)
        except (ValueError, OSError) as exc:
            issues += 1
            row["issue"] = str(exc)
            if pngs is not None and len(pngs[i]) == 1:
                row["path"] = f"unclassified/{pngs[i][0].name}"
                write(row["path"], pngs[i][0].read_bytes())

    copies_by_key = Counter(row.get("pixel_sha256") for row in rows.values())
    sites = []
    for fp, patterns in groups.items():
        count = sum(kind == "image" for kind, _ in patterns)
        base = folder_for(fp, count, threshold)
        if fp is not None:
            sites.append(dict(dimension=fp[0], center_x=fp[1], center_z=fp[2], scale=fp[3],
                              distinct_nonuniform=count, map_ids=sum(map(len, patterns.values())), folder=base))
        for (kind, digest), copies in patterns.items():
            representative = copies[0]
            if count == 1 and copies_by_key[digest] == 1 and pattern_by_key.get(digest):
                rows[representative]["terrain_reason"] = pattern_by_key[digest]
            image = render(dats[representative][0]) if pngs is None else None
            for i in copies:
                parts = [base]
                if kind != "image":
                    parts.append(kind)
                if i != representative:
                    parts.append("copies")
                parts.append(pngs[i][0].name if pngs is not None else f"map_{i:05d}.png")
                path = "/".join(parts)
                write(path, image if pngs is None else pngs[i][0].read_bytes())
                rows[i].update(path=path, representative_id=representative)
    sites.sort(key=lambda r: (-r["distinct_nonuniform"], r["folder"]))
    write("footprints.csv", csv_table(("dimension", "center_x", "center_z", "scale", "distinct_nonuniform",
                                       "map_ids", "folder"), sites))
    write("map-index.csv", csv_table(("map_id", "path", "dimension", "center_x", "center_z", "scale",
                                      "pixel_kind", "representative_id", "pixel_sha256", "issue", "terrain_reason"), rows.values()))
    write("review.html", review_page(rows.values(), titles))
    return len(rows), len(sites), issues


def build_collection(data, out, threshold=10, png_dir=None, names_reports=()):
    if out.exists():
        raise ValueError(f"Choose a new output path: {out}")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as temp:
        stage = Path(temp) / "result"
        if out.suffix.lower() == ".zip":
            with ZipFile(stage, "x", ZIP_DEFLATED, compresslevel=1) as archive:
                counts = sort_maps(data, archive.writestr, threshold, png_dir, names_reports)
        else:
            stage.mkdir()

            def write(name, payload):
                path = stage / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(payload)

            counts = sort_maps(data, write, threshold, png_dir, names_reports)
        stage.rename(out)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="one save's map DAT directory")
    parser.add_argument("--out", required=True, type=Path, help="new directory or .zip archive")
    parser.add_argument("--png-dir", type=Path, help="sort copies of existing map_<ID>.png files from one flat folder")
    parser.add_argument("--names-report", type=Path, action="append", default=[],
                        help="map-names.json or curated-map-names.csv from this save; repeat to combine")
    parser.add_argument("--frequent-at", type=int, default=10, help="distinct nonuniform images for the frequent tier (default: 10)")
    args = parser.parse_args(argv)
    if args.out.exists() or args.frequent_at < 2:
        parser.error("choose a new output path and a --frequent-at threshold of at least 2")
    try:
        counts = build_collection(args.data, args.out, args.frequent_at, args.png_dir, args.names_report)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Sort failed: {exc}\n")
    print(f"{counts[0]:,} map IDs, {counts[1]:,} located footprints, {counts[2]:,} issues. Output: {args.out}")
    return int(bool(counts[2]))


# Bundled so existing installations only need this sorter file to generate the offline page.
REVIEW_TEMPLATE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Map art review</title>
<script>
const themeKey="framed-map-atlas/theme";
try {
  const saved=localStorage.getItem(themeKey);
  if(saved==="light"||saved==="dark")document.documentElement.dataset.theme=saved
} catch {}
</script>
<style>
:root{
  color-scheme:light dark;font-family:"Work Sans","Segoe UI",sans-serif;
  --ink:light-dark(#182b24,#edf1e7);--page:light-dark(#f4f1e7,#141c18);--paper:light-dark(#fffcf3,#202b25);
  --pine:light-dark(#203e33,#a5d7b9);--pressed-text:light-dark(#fff,#12251b);--header:light-dark(#203e33,#192e24);
  --line:light-dark(#c7c9b8,#54665a);--hover:light-dark(#e9eddf,#304036);--focus:light-dark(#bf7612,#ffc569);
  --muted:light-dark(#526154,#b5c5b8);--notice:light-dark(#783c0d,#ffc788);--strip:light-dark(#d9ddce,#303c34);
  --check-a:light-dark(#d7d9ca,#354038);--check-b:light-dark(#e7eadc,#465249);
  color:var(--ink);background:var(--page)
}
:root[data-theme=light]{color-scheme:light}
:root[data-theme=dark]{color-scheme:dark}
*{box-sizing:border-box}body{margin:0}button,select,input{font:inherit}button,select,input[type=search]{border:1px solid var(--line);border-radius:5px;padding:.47rem .68rem;background:var(--paper);color:inherit}button{cursor:pointer}button:hover{border-color:var(--pine);background:var(--hover)}button:focus-visible,a:focus-visible,select:focus-visible,input:focus-visible{outline:3px solid var(--focus);outline-offset:2px}button:disabled{opacity:.46;cursor:default}button[aria-pressed=true]{background:var(--pine);color:var(--pressed-text);border-color:var(--pine)}header{background:var(--header);color:#fffbea;padding:1.1rem max(4vw,1rem);display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap}h1,h2,h3{font-family:"Source Serif 4",Georgia,serif;margin:0;line-height:1.1}h1{font-size:clamp(1.8rem,4vw,2.7rem)}header small{display:block;margin-top:.4rem;color:#d8e1cf}header button,header select{color:var(--ink)}.actions,nav,.controls,.paging,.buttons{display:flex;align-items:center;flex-wrap:wrap;gap:.5rem}main{max-width:1600px;margin:auto;padding:1.3rem max(3vw,1rem) 3rem}.bar{display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap}.controls{padding:.9rem 0;border-bottom:1px solid var(--line)}.controls label{display:flex;gap:.35rem;align-items:center;font-size:.87rem}#stats{color:var(--muted);margin:.5rem 0}.heading{margin:1.3rem 0 .7rem}h2{font-size:1.6rem}.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(280px,100%),1fr));gap:.85rem;margin:1rem 0}.tiles{grid-template-columns:repeat(auto-fill,minmax(min(205px,100%),1fr))}.site,.tile{background:var(--paper);border:1px solid var(--line);border-radius:7px;padding:.85rem}.site{text-align:left;display:flex;flex-direction:column;gap:.6rem}.strip{display:grid;grid-template-columns:repeat(3,1fr);gap:3px;background:var(--strip);align-self:stretch}.strip img{width:100%;aspect-ratio:1;object-fit:contain}.strip:has(img:only-child){grid-template-columns:1fr}.strip:has(img:only-child) img{height:106px}.thumb,img{image-rendering:pixelated}.thumb{display:grid;place-items:center;background:repeating-conic-gradient(var(--check-a) 0% 25%,var(--check-b) 0% 50%) 0 / 16px 16px;border-radius:4px}.thumb img{width:min(100%,256px);aspect-ratio:1;object-fit:contain}.tile h3{font-size:1.2rem;margin:.65rem 0 .3rem}small,.meta{color:var(--muted)}.buttons{margin-top:.6rem;gap:.25rem}.buttons button{font-size:.85rem;padding:.38rem .47rem}.paging{justify-content:flex-end}.note{color:var(--muted);font-size:.87rem}.message{margin:.4rem 0;color:var(--notice)}details{font-size:.85rem;margin-top:.4rem}summary{cursor:pointer}details p{overflow-wrap:anywhere}a{color:var(--pine)}[hidden]{display:none!important}
.map-name{white-space:pre-wrap;overflow-wrap:anywhere;font-weight:600;margin:.45rem 0 .1rem}.name-source{overflow-wrap:anywhere}.tile details .map-name{margin-top:.6rem}
.site-open{display:grid;gap:.5rem;text-align:left;width:100%}.site h3{margin:0}.tile select{width:100%;min-width:0}.choice{display:grid;gap:.25rem;margin:.6rem 0}.bulk{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem}.bulk small{flex-basis:100%}
</style></head><body>
<header><div><h1>Map art review</h1><small id="overview"></small></div><div class="actions"><label>Theme <select id="theme"><option value="system">System</option><option value="light">Light</option><option value="dark">Dark</option></select></label><button id="export">Export review CSV</button><button id="import">Import review</button><input type="file" id="file" accept=".csv" hidden></div></header>
<main><div class="bar"><nav><button id="sites">Canvases</button><button id="all">All distinct images</button><button id="terrain">Terrain suggestions</button><button id="named">Named map suggestions</button></nav><form id="lookup"><input type="search" id="query" aria-label="Map ID, PNG filename, title, or pixel key" placeholder="Map ID, filename, title, or pixel key" title="Paste a full pixel key, or use key: followed by its first few characters" size="34"> <button>Find</button></form></div>
<div class="controls"><label id="tierBox">Canvas reuse <select id="tier"><option value="">All locations</option><option value="01_frequent">Frequent</option><option value="02_repeated">Repeated</option><option value="03_single_image">Single image</option><option value="04_uniform_only">Uniform only</option></select></label>
<label id="decisionBox">Review <select id="filter"><option value="">All decisions</option><option>Unreviewed</option><option>Keep</option><option>Terrain</option><option>Unsure</option></select></label>
<label id="uniformBox"><input type="checkbox" id="uniform">Include blank and solid</label>
<label>Entries per page <select id="pageSize"><option>36</option><option selected>72</option><option>144</option><option>288</option></select></label>
<span id="progress" class="note"></span><button type="button" id="remaining">Review remaining IDs</button></div>
<p id="notice" role="status" class="message"></p><div class="heading"><h2 id="title"></h2><p id="stats"></p></div>
<div id="canvasActions"></div><button id="applySuggestions" hidden></button> <button id="undo" hidden>Undo last bulk decision</button>
<div class="paging" id="topPage"></div><div class="grid" id="grid"></div><div class="paging" id="bottomPage"></div>
</main><script type="application/json" id="data">__MAP_DATA__</script>
<script type="application/json" id="names">__MAP_NAMES__</script>
<script type="application/json" id="terrainData">__TERRAIN_DATA__</script>
<script>
"use strict";
const sites = JSON.parse(document.getElementById("data").textContent);
const $ = id => document.getElementById(id);
const names = JSON.parse($("names").textContent);
const terrainReasons = JSON.parse($("terrainData").textContent);
const theme=$("theme");
theme.value=document.documentElement.dataset.theme||"system";
theme.onchange=()=>{
  const root=document.documentElement;
  if(theme.value==="system")delete root.dataset.theme;
  else root.dataset.theme=theme.value;
  try {
    if(theme.value==="system")localStorage.removeItem(themeKey);
    else localStorage.setItem(themeKey,theme.value)
  } catch {}
};
const nf = n => Number(n).toLocaleString("en-US");
const labels = ["Keep","Terrain","Unsure"];
const storeKey = "framed-map-review/__COLLECTION_KEY__";
const decisions = new Map(), chosenNames = new Map();
let lastBulk=null, page = 0, homePage = 0, mode = "sites", site = null, wanted = null;
let searchText = "";
const globalTiles = new Map(), byId = new Map();
for(const s of sites) for(const t of s.tiles){
  if(!globalTiles.has(t.key)) globalTiles.set(t.key,{...t,ids:[]});
  const g=globalTiles.get(t.key);
  for(const id of t.ids){g.ids.push(id);byId.set(id,g)}
}
const allTiles = [...globalTiles.values()].sort((a,b)=>a.id-b.id);
const nonuniformTotal=allTiles.reduce((sum,t)=>sum+(t.kind==="image"?t.ids.length:0),0);
const defaultNames=new Map(), namedKeys=new Set();
for(const [key,candidates] of Object.entries(names)){
  const titles=Object.keys(candidates).filter(Boolean);
  if(titles.length)namedKeys.add(key);
  if(titles.length===1)defaultNames.set(key,titles[0])
}
function reviewName(id){return chosenNames.get(id)??defaultNames.get(byId.get(id).key)??""}
const terrainTiles = allTiles.filter(t=>terrainReasons[t.id]&&!Object.keys(names[t.key]||{}).length);
function pendingTerrain(){return terrainTiles.filter(t=>!decisions.has(t.id)&&!reviewName(t.id))}
function pendingNamed(){
  const tiles=[];
  for(const t of allTiles){
    const hasCandidates=namedKeys.has(t.key);
    const ids=t.ids.filter(id=>!decisions.has(id)&&(chosenNames.has(id)?Boolean(chosenNames.get(id)):hasCandidates));
    if(ids.length)tiles.push({...t,id:ids[0],ids})
  }
  return tiles
}
function notice(text){$("notice").textContent=text}
try {
  const loaded=JSON.parse(localStorage.getItem(storeKey));
  if(loaded){
    for(const [id,v] of loaded.decisions) if(byId.has(id)&&labels.includes(v)) decisions.set(id,v);
    for(const [id,v] of loaded.names) if(byId.has(id)&&typeof v==="string") chosenNames.set(id,v)
  }
} catch {notice("Could not load the saved review. You can import a review CSV.")}
function save(){
  try{localStorage.setItem(storeKey,JSON.stringify({decisions:[...decisions],names:[...chosenNames]}))}
  catch{notice("Browser storage is unavailable. Export your review CSV before closing.")}
}
function el(tag,cls,text){const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n}
function button(label,fn){const b=el("button","",label);b.type="button";b.onclick=fn;return b}
function photo(t){const img=el("img");img.src=t.path;img.alt="Map "+t.id;img.loading="lazy";return img}
function locationLabel(s){return s.x==null||s.x===""?"Unknown footprint":String(s.dimension).replace(/^minecraft:/,"").replaceAll("_"," ")+" · X "+s.x+" / Z "+s.z+" · scale "+s.scale}
function pressed(id,yes){$(id).setAttribute("aria-pressed",String(yes))}
function openMode(next){
  wanted=null;searchText="";mode=next;page=next==="sites"?homePage:0;
  $("filter").value="";$("query").value="";notice("");render();window.scrollTo(0,0)
}
function setValue(map,id,value){if(value)map.set(id,value);else map.delete(id)}
function canvasControls(s){
  const bar=el("div","bulk");
  if(s.x==null||s.x==="")return bar;
  const ids=s.tiles.flatMap(tile=>tile.ids);
  bar.append(el("small","","Mark all "+nf(ids.length)+" map IDs on this canvas"));
  for(const label of labels){
    const b=button(label,()=>{
      const changed=ids.filter(id=>decisions.get(id)!==label);
      if(!changed.length)return;
      lastBulk=changed.map(id=>[id,decisions.get(id)]);
      for(const id of changed)decisions.set(id,label);
      notice("Marked "+nf(changed.length)+" map IDs "+label+" on "+locationLabel(s)+".");save();render()
    });
    b.setAttribute("aria-pressed",String(ids.every(id=>decisions.get(id)===label)));
    b.setAttribute("aria-label","Mark canvas "+locationLabel(s)+" "+label);bar.append(b)
  }
  return bar
}
$("undo").onclick=()=>{
  if(!lastBulk)return;
  for(const [id,value] of lastBulk)setValue(decisions,id,value);
  lastBulk=null;notice("Restored the previous decisions.");save();render()
};
function applySuggestions(ids,label){
  if(!ids.length)return;
  lastBulk=ids.map(id=>[id,undefined]);
  for(const id of ids)decisions.set(id,label);
  notice("Marked "+nf(ids.length)+" suggested map IDs "+label+".");save();render()
}
function siteCard(s){
  const card=el("article","site");
  const open=button("",()=>{homePage=page;site=s;$("uniform").checked=s.count===0;openMode("site")});open.className="site-open";
  let pool=s.tiles.filter(t=>t.kind==="image");if(!pool.length)pool=s.tiles;
  const strip=el("div","strip"), n=Math.min(6,pool.length);
  for(let i=0;i<n;i++)strip.append(photo(pool[Math.floor(i*(pool.length-1)/Math.max(1,n-1))]));
  open.append(strip,el("h3","",locationLabel(s)),el("small","",nf(s.count)+" distinct non-uniform · "+nf(s.tiles.reduce((n,t)=>n+t.ids.length,0))+" IDs"));
  card.append(open,canvasControls(s));
  return card
}
function tileCard(t){
  const card=el("article","tile"), link=el("a","thumb");link.href=t.path;link.target="_blank";link.rel="noopener";link.title="Open original PNG";link.append(photo(t));
  const ids=globalTiles.get(t.key).ids;
  const id=wanted??t.id;
  const targets=wanted===null?t.ids:[wanted];
  card.append(link,el("h3","","Map "+id));
  if(mode==="terrain")card.append(el("p","note",terrainReasons[id]));
  if(mode==="remaining"||mode==="named"&&targets.length>1)card.append(el("small","",nf(targets.length)+" pending "+(targets.length===1?"ID":"IDs")+" with these pixels"));
  const entries=Object.entries(names[t.key]||{}).sort((a,b)=>Number(b[1].includes(id))-Number(a[1].includes(id)));
  function showName(parent,[name,sources]){
    parent.append(el("p","map-name",name));
    parent.append(el("small","name-source",sources.includes(id)?"Recorded on map "+id:"Pixel match from map "+sources[0]));
    if(sources.length>1){
      const more=el("details");more.append(el("summary","","All "+nf(sources.length)+" source IDs"),el("p","",sources.join(", ")));parent.append(more)
    }
  }
  if(entries.length){
    showName(card,entries[0]);
    if(entries.length>1){
      const more=el("details");more.append(el("summary","",(entries.length-1)+" other recorded title"+(entries.length===2?"":"s")));
      for(const entry of entries.slice(1))showName(more,entry);
      card.append(more)
    }
    if(["blank","solid"].includes(t.kind)&&entries.some(([,sources])=>!sources.includes(id)))card.append(el("p","note","Many maps can share a single color. Treat pixel matches here as tentative."));
  }
  const selected=new Set(targets.map(reviewName));
  const options=new Set([...entries.map(([name])=>name),...selected]);options.delete("");
  if(options.size){
    const label=el("label","choice","Chosen name");
    const select=el("select");
    function option(value,text){const opt=el("option","",text);opt.value=value;select.append(opt);return opt}
    if(selected.size>1){const mixed=option("","Multiple choices");mixed.disabled=true;mixed.selected=true}
    option("","No name selected");
    for(const name of options)option(name,name);
    if(selected.size===1)select.value=[...selected][0];
    select.onchange=()=>{for(const i of targets)chosenNames.set(i,select.value);save();render()};
    label.append(select);card.append(label)
  }
  const info=el("details");info.append(el("summary","",nf(ids.length)+" map ID"+(ids.length===1?"":"s")+" with these pixels"),el("p","",ids.join(", ")));card.append(info);
  info.append(el("p","meta","Pixel key · "+t.key));
  const buttons=el("div","buttons");
  if(new Set(targets.map(i=>decisions.get(i))).size>1)card.append(el("small","","Mixed decisions"));
  for(const label of labels){
    const active=targets.every(i=>decisions.get(i)===label);
    const b=button(label,()=>{lastBulk=null;for(const i of targets)setValue(decisions,i,active?"":label);save();render()});
    b.setAttribute("aria-pressed",String(active));b.setAttribute("aria-label",label+" map "+id);buttons.append(b)
  }
  card.append(buttons);return card
}
function pager(id,pages){
  const p=$(id);p.replaceChildren();if(pages<2)return;
  function turn(delta){page+=delta;render();window.scrollTo(0,0)}
  const back=button("Previous",()=>turn(-1)),next=button("Next",()=>turn(1));
  back.disabled=page===0;next.disabled=page+1===pages;
  p.append(back,el("small","","Page "+nf(page+1)+" of "+nf(pages)),next)
}
function render(){
  const home=mode==="sites";
  const remaining=mode==="remaining";
  const suggestions=mode==="terrain"||mode==="named";
  const terrain=pendingTerrain(), named=pendingNamed();
  const namedCount=named.reduce((sum,t)=>sum+t.ids.length,0);
  $("terrain").textContent="Terrain suggestions ("+nf(terrain.length)+")";
  $("named").textContent="Named map suggestions ("+nf(namedCount)+")";
  pressed("terrain",mode==="terrain");pressed("named",mode==="named");
  $("applySuggestions").hidden=true;
  $("canvasActions").replaceChildren();if(mode==="site")$("canvasActions").append(canvasControls(site));
  $("undo").hidden=!lastBulk;
  $("tierBox").hidden=!home;$("decisionBox").hidden=home||remaining||suggestions||wanted!==null||!!searchText;$("uniformBox").hidden=home||remaining||suggestions||wanted!==null||!!searchText;
  pressed("remaining",remaining);
  pressed("sites",home);pressed("all",mode==="all"&&wanted===null);
  let rows;
  if(home){
    rows=sites.filter(s=>!$("tier").value||s.folder.split("/")[0]===$("tier").value);
    $("title").textContent="Canvas contact sheets";$("stats").textContent=nf(rows.length)+" footprints · most distinct images first. Open a canvas to review its tiles."
  }else if(remaining){
    rows=[];
    for(const t of allTiles){
      const ids=t.ids.filter(id=>!decisions.has(id));
      if(ids.length)rows.push({...t,id:ids[0],ids})
    }
    $("title").textContent="Remaining map IDs";
    $("stats").textContent=nf(byId.size-decisions.size)+" unreviewed map IDs across "+nf(rows.length)+" distinct images, including blank and solid maps. Each decision applies only to the pending IDs on that card."
  }else if(suggestions){
    const isNamed=mode==="named";
    rows=isNamed?named:terrain;
    const ids=rows.flatMap(t=>t.ids),label=isNamed?"Keep":"Terrain";
    $("applySuggestions").hidden=!ids.length;
    $("applySuggestions").textContent="Mark all "+nf(ids.length)+" as "+label;
    $("applySuggestions").onclick=()=>applySuggestions(ids,label);
    $("title").textContent=isNamed?"Named map suggestions":"Terrain suggestions";
    $("stats").textContent=isNamed
      ?"Unreviewed IDs with a candidate or chosen name. Keep applies across every page and leaves name choices as they are."
      :"Unreviewed, unnamed Overworld images with one ID and one distinct image on their canvas. Review the pixel evidence or apply Terrain to all suggestions across every page."
  }else{
    rows=wanted!==null?[byId.get(wanted)]:mode==="site"?site.tiles:allTiles;
    if(mode==="pixelKey"){
      rows=globalTiles.has(searchText)?[globalTiles.get(searchText)]:[]
    }else if(mode==="pixelPrefix"){
      rows=rows.filter(t=>t.key.startsWith(searchText))
    }else if(searchText){
      const term=searchText.toLowerCase();
      rows=rows.filter(t=>Object.keys(names[t.key]||{}).some(name=>name.toLowerCase().includes(term)))
    }else if(wanted===null){
      const choice=$("filter").value;
      rows=rows.filter(t=>($("uniform").checked||!["solid","blank"].includes(t.kind))&&(!choice||t.ids.some(id=>(decisions.get(id)||"Unreviewed")===choice)))
    }
    $("title").textContent=mode.startsWith("pixel")?"Pixel key matches":wanted!==null?"Lookup · map "+wanted:searchText?"Titles matching “"+searchText+"”":mode==="site"?locationLabel(site):"All distinct images";
    const scope=wanted!==null?"Changes apply to this map ID.":mode==="site"?"Changes apply to copies on this canvas.":"Changes apply to all copies of each image.";
    $("stats").textContent=nf(rows.length)+" distinct image"+(rows.length===1?"":"s")+". "+scope
  }
  const size=Number($("pageSize").value),pages=Math.ceil(rows.length/size);
  page=Math.max(0,Math.min(page,pages-1));if(home)homePage=page;
  $("grid").className=home?"grid":"grid tiles";
  $("grid").replaceChildren(...rows.slice(page*size,(page+1)*size).map(home?siteCard:tileCard));
  if(!rows.length)$("grid").append(el("p","",remaining?"Every map ID has a decision.":mode==="named"?"No pending named map suggestions.":mode==="terrain"?"No pending terrain suggestions.":"No results for these filters."));
  pager("topPage",pages);pager("bottomPage",pages);
  let nonuniformReviewed=0;
  for(const id of decisions.keys())if(byId.get(id).kind==="image")nonuniformReviewed++;
  $("progress").textContent=nf(decisions.size)+" / "+nf(byId.size)+" map IDs reviewed · "+nf(nonuniformReviewed)+" / "+nf(nonuniformTotal)+" nonuniform IDs reviewed"
}
$("overview").textContent=nf(byId.size)+" map IDs · "+nf(sites.length)+" footprints";
$("sites").onclick=()=>openMode("sites");$("all").onclick=()=>openMode("all");
$("terrain").onclick=()=>openMode("terrain");
$("named").onclick=()=>openMode("named");
$("remaining").onclick=()=>openMode("remaining");
$("pageSize").onchange=()=>{page=0;homePage=0;render()};
for(const id of ["tier","filter","uniform"])$(id).onchange=()=>{page=0;render()};
$("lookup").onsubmit=e=>{
  e.preventDefault();
  const query=$("query").value.trim();
  if(!query)return;
  const explicitKey=/^key:/i.test(query);
  const pixelSearch=explicitKey||/^(?:[a-f0-9]{64}|unclassified--?\d+)$/i.test(query);
  const term=explicitKey?query.slice(4).trim():query;
  if(!term){notice("Enter a pixel key or prefix after key:.");return}
  const id=pixelSearch?null:query.match(/^(?:map_)?(-?\d+)(?:\.png)?$/i);
  if(id&&!byId.has(Number(id[1]))){notice("That map ID is not in this collection.");return}
  if(mode==="sites")homePage=page;
  wanted=id?Number(id[1]):null;
  searchText=pixelSearch?term.toLowerCase():id?"":query;
  mode=explicitKey?"pixelPrefix":pixelSearch?"pixelKey":"all";page=0;notice("");render()
};
function download(name,content,type){const url=URL.createObjectURL(new Blob([content],{type}));const a=el("a");a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),3000)}
$("export").onclick=()=>{
  const ids=[...byId.keys()].filter(id=>decisions.has(id)||chosenNames.has(id)||reviewName(id)).sort((a,b)=>a-b);
  if(!ids.length){notice("Mark a tile or choose a name first.");return}
  const lines=["map_id,pixel_key,decision,chosen_name"];
  for(const id of ids){
    const title=reviewName(id).replaceAll('"','""');
    lines.push([id,byId.get(id).key,decisions.get(id)||"",'"'+title+'"'].join(","))
  }
  download("map-review.csv",lines.join("\r\n")+"\r\n","text/csv")
};
function csvRows(text){
  text=text.replace(/^\uFEFF/,"");
  const field=/("(?:[^"]|"")*"|[^",\r\n]*)(,|\r\n|\r|\n|$)/gy;
  const rows=[];let row=[];
  while(field.lastIndex<text.length){
    const cell=field.exec(text);if(!cell)throw Error("Invalid review CSV quoting.");
    const raw=cell[1];row.push(raw.startsWith('"')?raw.slice(1,-1).replaceAll('""','"'):raw);
    if(cell[2]!==","){if(row.length>1||row[0]!=="")rows.push(row);row=[]}
    else if(field.lastIndex===text.length){row.push("");rows.push(row)}
  }
  return rows
}
$("import").onclick=()=>$("file").click();
$("file").onchange=async e=>{
 const f=e.target.files[0];if(!f)return;
 try{
  const rows=csvRows(await f.text()), header=rows.shift()||[];
  if(header.length!==4||header.join(",")!=="map_id,pixel_key,decision,chosen_name")throw Error("Choose a CSV exported by this review page.");
  const incoming=new Map();
  for(const row of rows){
    const [rawId,key,value,title]=row,id=Number(rawId);
    if(row.length!==4||!/^-?\d+$/.test(rawId)||byId.get(id)?.key!==key||
       (value!==""&&!labels.includes(value)))throw Error("A review row does not match this gallery.");
    const previous=incoming.get(id);
    if(previous&&(previous[0]!==value||previous[1]!==title))throw Error("Conflicting review rows for map "+id+".");
    incoming.set(id,[value,title])
  }
  let added=0,conflicts=0;
  for(const [id,[decision,title]] of incoming){
    for(const [map,value] of [[decisions,decision],[chosenNames,title]]){
      // A blank name suppresses a default. A row with both fields empty records an explicit clear.
      if(!value && (map===decisions || (decision && !defaultNames.has(byId.get(id).key))))continue;
      if(!map.has(id)){map.set(id,value);added++}else if(map.get(id)!==value)conflicts++
    }
  }
  lastBulk=null;notice("Imported "+added+" choices. Kept existing values for "+conflicts+" conflicts.");save();render()
 }catch(err){notice(err.message)}finally{e.target.value=""}
};
render();
</script></body></html>
'''


if __name__ == "__main__":
    sys.exit(main())
