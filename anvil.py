"""Read entity lists from Minecraft Java Anvil region files without opening them for writing."""

import os
import re
import struct
import zlib
from copy import deepcopy
from pathlib import Path


REGION = re.compile(r"r\.(-?\d+)\.(-?\d+)\.mca$")
LIMIT = 64 * 1024 * 1024
FORMATS = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}
WIDTH = {kind: struct.calcsize(fmt) for kind, fmt in FORMATS.items()}

# None means read the complete value. False means skip without building Python objects.
ITEM = {"id": None, "Damage": None, "tag": {"map": None, "display": {"Name": None}},
        "components": {k: None for k in ("minecraft:map_id", "map_id", "minecraft:custom_name", "custom_name",
                                         "minecraft:item_name", "item_name")}}
ENTITY = {k: None for k in ("id", "block_pos", "TileX", "TileY", "TileZ", "Facing", "facing",
                            "Direction", "Dir", "Pos", "ItemRotation", "item_rotation", "CustomName")}
ENTITY.update(Item=ITEM, item=ITEM, Passengers=ENTITY, Riding=ENTITY)
ROOT = {"DataVersion": None, "Entities": ENTITY, "entities": ENTITY,
        "Level": {"Entities": ENTITY}}


def container_selection():
    root = deepcopy(ROOT)
    entity = root["Entities"]
    stack = entity["Item"]
    stack.update(Slot=None, slot=None, Count=None, count=None)
    inventory = {"Items": stack, "items": stack}
    entity.update(inventory)
    stack["tag"].update(Items=stack, BlockEntityTag=inventory)
    for prefix in ("minecraft:", ""):
        components = stack["components"]
        components[prefix + "container"] = {"slot": None, "item": stack}
        components[prefix + "bundle_contents"] = stack
        components[prefix + "block_entity_data"] = inventory
    block = dict(inventory, id=None, x=None, y=None, z=None, item=stack)
    root["block_entities"] = root["Level"]["TileEntities"] = block
    return root


CONTAINER_ROOT = container_selection()


class NBT:
    def __init__(self, data):
        self.data = memoryview(data)
        self.pos = 0

    def take(self, size):
        if size < 0 or size > len(self.data) - self.pos:
            raise ValueError("invalid NBT size or truncated chunk")
        start = self.pos
        self.pos += size
        return self.data[start:self.pos]

    def number(self, fmt):
        return struct.unpack(fmt, self.take(struct.calcsize(fmt)))[0]

    def string(self, keep=True):
        raw = self.take(self.number(">H"))
        if keep:
            # Java's modified UTF-8 encodes supplementary characters as surrogate pairs.
            value = bytes(raw).replace(b"\xc0\x80", b"\0").decode("utf-8", "surrogatepass")
            if any(0xD800 <= ord(ch) <= 0xDFFF for ch in value):
                return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")
            return value

    def value(self, kind, selection, depth=0):
        if depth > 128:
            raise ValueError("NBT nesting exceeds 128 levels")
        keep = selection is not False
        if kind in FORMATS:
            data = self.take(WIDTH[kind])
            return struct.unpack(FORMATS[kind], data)[0] if keep else None
        if kind == 8:
            return self.string(keep)
        if kind in (7, 11, 12):
            fmt = {7: ">b", 11: ">i", 12: ">q"}[kind]
            n = self.number(">i")
            raw = self.take(n * struct.calcsize(fmt))
            if kind == 7 and selection is bytes:
                return bytes(raw)
            if keep:
                if n > 1_000_000:
                    raise ValueError("selected NBT array too large")
                return [v for v, in struct.iter_unpack(fmt, raw)]
            return None
        if kind == 9:
            elem = self.number(">B")
            n = max(0, self.number(">i"))
            if not keep and elem in WIDTH:
                self.take(n * WIDTH[elem])
                return None
            if n > 1_000_000 or n > len(self.data) - self.pos:
                raise ValueError("invalid or oversized NBT list")
            if keep:
                return [self.value(elem, selection, depth + 1) for _ in range(n)]
            for _ in range(n):
                self.value(elem, False, depth + 1)
            return None
        if kind == 10:
            result = {} if keep else None
            while (child := self.number(">B")):
                key = self.string(keep)
                pick = selection.get(key, False) if isinstance(selection, dict) else selection
                val = self.value(child, pick, depth + 1)
                if pick is not False:
                    result[key] = val
            return result
        raise ValueError(f"unknown NBT tag {kind}")

    def chunk(self, selection=ROOT):
        if self.number(">B") != 10:
            raise ValueError("expected NBT compound root")
        self.string(False)
        return self.value(10, selection)


def decompress(codec, data):
    if codec == 3:
        return data
    if codec in (1, 2):
        decoder = zlib.decompressobj(31 if codec == 1 else 15)
        try:
            raw = decoder.decompress(data, LIMIT + 1)
        except zlib.error as exc:
            raise ValueError(f"damaged compressed chunk: {exc}") from exc
        if len(raw) > LIMIT or not decoder.eof:
            raise ValueError("oversized or truncated compressed chunk")
        return raw
    if codec == 4:
        out = bytearray()
        pos = 0
        while pos + 21 <= len(data) and data[pos:pos + 8] == b"LZ4Block":
            token, packed, size, _checksum = struct.unpack_from("<BIII", data, pos + 8)
            pos += 21
            if not packed and not size:
                return out
            if not size or size > 1 << (10 + (token & 15)) or size > LIMIT - len(out) or not 0 < packed <= len(data) - pos:
                raise ValueError("invalid LZ4 block sizes")
            block = data[pos:pos + packed]
            pos += packed
            if token >> 4 == 2:
                try:
                    from lz4.block import decompress as decode_block
                except ImportError as exc:
                    raise ValueError("LZ4 chunk requires optional dependency: python -m pip install lz4") from exc
                try:
                    block = decode_block(block, uncompressed_size=size)
                except Exception as exc:
                    raise ValueError(f"invalid LZ4 block: {exc}") from exc
            elif token >> 4 != 1:
                raise ValueError("unknown LZ4 block type")
            if len(block) != size:
                raise ValueError("incorrect LZ4 block length")
            out.extend(block)
        raise ValueError("incomplete LZ4 stream")
    raise ValueError(f"unsupported region compression type {codec}")


def region_chunks(path, issue, *, containers=False):
    """Yield (chunk X, chunk Z, decoded data). Continue after damaged slots."""
    rx, rz = map(int, REGION.fullmatch(path.name).groups())
    with path.open("rb") as stream:
        size = os.fstat(stream.fileno()).st_size
        header = stream.read(8192)
        if len(header) < 8192:
            raise ValueError("incomplete region header")
        for slot, (entry,) in enumerate(struct.iter_unpack(">I", header[:4096])):
            if not entry:
                continue
            cx, cz = rx * 32 + slot % 32, rz * 32 + slot // 32
            try:
                start, sectors = (entry >> 8) * 4096, entry & 255
                if start < 8192 or not sectors or start + 5 > size:
                    raise ValueError("invalid region location")
                stream.seek(start)
                length, codec = struct.unpack(">IB", stream.read(5))
                if length < 1 or length + 4 > min(sectors * 4096, size - start):
                    raise ValueError("invalid chunk length")
                if codec & 128:
                    external = path.with_name(f"c.{cx}.{cz}.mcc")
                    with external.open("rb") as src:
                        if os.fstat(src.fileno()).st_size > LIMIT:
                            raise ValueError("oversized external chunk")
                        payload = src.read(LIMIT + 1)
                else:
                    payload = stream.read(length - 1)
                chunk = NBT(decompress(codec & 127, payload)).chunk(CONTAINER_ROOT if containers else ROOT)
            except (OSError, ValueError, struct.error) as exc:
                issue(path, (cx, cz), str(exc))
                continue
            yield cx, cz, chunk


def discover(root, issue, *, entities_only=False):
    regions = []
    storage_dirs = {"entities"} if entities_only else {"entities", "region"}
    for current, _, files in os.walk(root, onerror=lambda e: issue(Path(e.filename or root), None, str(e))):
        folder = Path(current)
        if folder.name in storage_dirs:
            regions.extend(folder / f for f in files if REGION.fullmatch(f))
    return sorted(regions)


def location(folder, root):
    """Keep independent save folders separate, including Bukkit split-world folders."""
    base = folder.parent
    for ancestor in (base, *base.parents):
        if not ancestor.is_relative_to(root):
            break
        if ancestor.name == "dimensions" and ancestor != root:
            parts = base.relative_to(ancestor).parts
            if len(parts) >= 2:
                return ancestor.parent.relative_to(root).as_posix(), parts[0] + ":" + "/".join(parts[1:])
    if base.name in ("DIM-1", "DIM1") and base != root:
        dimension = "minecraft:the_nether" if base.name == "DIM-1" else "minecraft:the_end"
        return base.parent.relative_to(root).as_posix(), dimension
    return base.relative_to(root).as_posix(), "minecraft:overworld"

