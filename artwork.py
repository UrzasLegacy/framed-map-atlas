"""Infer reviewable artwork layouts from frame placements. Map IDs carry no ordering weight."""

import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from functools import lru_cache


GENERIC = set("the a an of by map maps art mapart tile tiles part panel piece row col column x y".split())
GRID_LABEL = (r"(?:r(?:ow)?\s*\d+\s*[-_/ ]*c(?:ol(?:umn)?)?\s*\d+|"
              r"(?:upper|lower|top|bottom|middle)[ _-]*(?:left|right|cent(?:er|re))|"
              r"t[lr]|b[lr])")
GRID_SUFFIX = re.compile(r"[\s([_|:–—-]+" + GRID_LABEL + r"[\s)\]]*$", re.I)
PREFIX = re.compile(r"^[\[(]?(?:" + GRID_LABEL + r"|[a-z]\d+|\d+\s*[/,x]\s*\d+)"
                    r"[\])]?(?:\s*[-:|–—]\s*|\s+)", re.I)
TILE_SUFFIX = re.compile(
    r"(?:[\s_#.,:|/(\[–—-]+(?:tile|part|panel|piece)?\s*#?\s*"
    r"(?:[a-z]\d+|\d+(?:\s*(?:of|[/,x:.-])\s*\d+)?|[ivx]+)"
    r"[\]) ]*)$", re.I)


@lru_cache(maxsize=8192)
def title_key(name):
    title = unicodedata.normalize("NFKC", name)
    title = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", title).casefold()
    title = GRID_SUFFIX.sub("", PREFIX.sub("", title))
    title = TILE_SUFFIX.sub("", title)
    words = re.findall(r"[^\W_]+", title)
    return " ".join(w for w in words if w not in GENERIC and not w.isdecimal())


@lru_cache(maxsize=8192)
def related(a, b):
    if not a or not b:
        return False
    if a == b:
        return True
    left, right = set(a.split()), set(b.split())
    if len(left & right) >= 2 and len(left & right) / len(left | right) >= 0.6:
        return True
    x, y = a.replace(" ", ""), b.replace(" ", "")
    return min(len(x), len(y)) >= 5 and SequenceMatcher(None, x[:256], y[:256], autojunk=False).ratio() >= 0.87


def project(frame):
    """Return (wall plane, viewer's column, viewer's row); north at top on horizontal surfaces."""
    x, y, z = frame["x"], frame["y"], frame["z"]
    if x is None or frame["facing"] is None:
        return None
    return {"north": (z, -x, -y), "south": (z, x, -y),
            "east": (x, -z, -y), "west": (x, z, -y),
            "up": (y, x, z), "down": (y, -x, z)}[frame["facing"]]


def neighbors(cell):
    x, y = cell
    return [(x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1)]


def patches(cells, connects):
    remaining = set(cells)
    for start in cells:
        if start not in remaining:
            continue
        remaining.remove(start)
        group, stack = [start], [start]
        while stack:
            cell = stack.pop()
            for other in neighbors(cell):
                if other in remaining and connects(cell, other):
                    remaining.remove(other)
                    group.append(other)
                    stack.append(other)
        yield group


def partition(cells):
    """Separate incompatible named neighbors first, then place contiguous untitled patches."""
    keys = {c: title_key(f["name"] or "") for c, f in cells.items()}
    named = [c for c in cells if keys[c]]
    groups = list(patches(named, lambda a, b: related(keys[a], keys[b])))
    owners = {cell: i for i, group in enumerate(groups) for cell in group}
    stems = [{keys[c] for c in group} for group in groups]
    parents = list(range(len(groups)))

    def root(i):
        while i != parents[i]:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i

    ambiguous = []
    for patch in patches([c for c in cells if not keys[c]], lambda a, b: True):
        borders = {owners[n] for c in patch for n in neighbors(c) if n in owners}
        if not borders:
            ambiguous.append((patch, None))
            continue
        first = min(borders)
        if len(borders) == 1 or len(set.union(*(stems[i] for i in borders))) == 1:
            groups[first].extend(patch)
            for i in borders:
                parents[root(i)] = root(first)
        else:
            ambiguous.append((patch, "Unlabelled area touches multiple title groups."))
    merged = defaultdict(list)
    for i, group in enumerate(groups):
        merged[root(i)].extend(group)
    for group in merged.values():
        yield group, None
    yield from ambiguous


def infer(frames):
    planes = defaultdict(lambda: defaultdict(list))
    unplaced, results = [], []
    for f in frames:
        if f["map_id"] is None:
            unplaced.append({"frame": f["frame"], "reason": "filled map has no readable ID"})
            continue
        p = project(f)
        if p is None:
            unplaced.append({"frame": f["frame"], "reason": "missing position or facing"})
        else:
            plane, u, v = p
            planes[f["world"], f["dimension"], f["facing"], plane][u, v].append(f)
    for (world, dimension, facing, plane), occupied in sorted(planes.items()):
        cells = {}
        for cell, entries in occupied.items():
            if len(entries) == 1:
                cells[cell] = entries[0]
            else:
                unplaced.extend({"frame": f["frame"], "reason": "multiple observations on this face"}
                                for f in entries)
        for group, warning in partition(cells):
            group.sort(key=lambda c: (c[1], c[0]))
            us, vs = zip(*group)
            u0, v0 = min(us), min(vs)
            width, height = max(us) - u0 + 1, max(vs) - v0 + 1
            tiles = []
            for u, v in group:
                f = cells[u, v]
                tiles.append({"row": v - v0 + 1, "column": u - u0 + 1, "frame": f["frame"],
                              "map_id": f["map_id"], "name": f["name"], "rotation": f["rotation"]})
            keys = Counter(title_key(t["name"] or "") for t in tiles)
            keys.pop("", None)
            count, titled = len(tiles), sum(keys.values())
            holes = width * height - count
            flags = [warning] if warning else []
            if holes:
                flags.append("Bounding rectangle contains gaps.")
            if len({t["map_id"] for t in tiles}) < count:
                flags.append("Layout repeats a map ID.")
            if len({t["rotation"] for t in tiles if t["rotation"] is not None}) > 1:
                flags.append("Displayed rotations differ.")
            if any(cells[c]["position_source"] == "Pos" for c in group):
                flags.append("Some block coordinates were inferred from entity positions.")
            if titled == 0 and count > 1:
                flags.append("Geometry alone cannot resolve adjacent untitled artworks.")
            if len(keys) > 1:
                flags.append("Titles were matched approximately.")
            confidence = "singleton" if count == 1 else "low"
            if count > 1 and titled >= 2 and not warning:
                confidence = "medium"
                if len(keys) == 1 and titled == count and not flags:
                    confidence = "high"
            results.append({"group": f"G{len(results) + 1:05d}", "world": world, "dimension": dimension,
                            "facing": facing, "plane": plane, "width": width, "height": height,
                            "tile_count": count, "empty_cells": holes, "titled_tiles": titled,
                            "title_keys": sorted(keys), "confidence": confidence, "review_notes": flags,
                            "tiles": tiles})
    matches = defaultdict(list)
    for g in results:
        for key in g["title_keys"]:
            matches[g["world"], key].append(g["group"])
    links = [{"world": world, "title_key": key, "groups": ids}
             for (world, key), ids in sorted(matches.items()) if len(ids) > 1]
    return {"groups": results, "possible_related_groups": links, "unplaced": unplaced}

