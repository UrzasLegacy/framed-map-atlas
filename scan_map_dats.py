"""Inventory NBT metadata in one folder of Java map DATs (one CSV row per file)."""

import argparse
import csv
import json
from pathlib import Path

from map_assets import index_dats, pixel_signature, read_map_nbt, validate_pixels


FIELDS = {"dimension": "dimension", "xCenter": "center_x", "zCenter": "center_z",
          "scale": "scale", "locked": "locked", "trackingPosition": "tracking_position",
          "unlimitedTracking": "unlimited_tracking"}


class AllTagsExcept(dict):
    """NBT projections normally skip unlisted tags. Keep them to capture custom metadata."""

    def get(self, key, default=False):
        return super().get(key, None)


def metadata(path, pixel_keys):
    root = read_map_nbt(path, AllTagsExcept(data=AllTagsExcept(colors=bytes if pixel_keys else False)))
    data = root.pop("data", None)
    if not isinstance(data, dict):
        raise ValueError("expected a data compound")
    row = {"data_version": root.pop("DataVersion", None)}
    for tag, column in FIELDS.items():
        row[column] = data.pop(tag, None)
    if pixel_keys:
        colors = data.pop("colors", None)
        try:
            validate_pixels(colors)
            row["pixel_key"], _ = pixel_signature(colors)
        except ValueError as exc:
            row["issue"] = f"pixel key unavailable: {exc}"
    if data:
        root["data"] = data
    if root:
        row["extra_nbt_json"] = json.dumps(root, ensure_ascii=True, separators=(",", ":"))
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path, help="directory containing map_25.dat or 25.dat")
    parser.add_argument("--out", type=Path, default=Path("map-dat-metadata.csv"), help="new CSV file")
    parser.add_argument("--pixel-keys", action="store_true", help="also compute searchable Map Art Sorter pixel keys")
    args = parser.parse_args(argv)
    columns = ["map_id", "filename", "data_version", *FIELDS.values(), "extra_nbt_json"]
    if args.pixel_keys:
        columns.append("pixel_key")
    columns.append("issue")
    issues = total = 0
    try:
        files = index_dats(args.data)
        if not files:
            parser.error(f"no map DATs in {args.data}; choose the folder containing the DAT files")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("x", encoding="utf-8-sig", newline="") as output:
            writer = csv.DictWriter(output, fieldnames=columns)
            writer.writeheader()
            for map_id, paths in sorted(files.items()):
                for path in sorted(paths):
                    try:
                        row = metadata(path, args.pixel_keys)
                    except (ValueError, OSError) as exc:
                        row = {"issue": str(exc)}
                    if len(paths) > 1:
                        row["issue"] = "; ".join(filter(None, ("multiple files share this ID", row.get("issue"))))
                    writer.writerow(dict(row, map_id=map_id, filename=path.name))
                    issues += bool(row.get("issue"))
                    total += 1
    except FileExistsError:
        parser.exit(2, f"Output already exists. Choose a new --out path: {args.out}\n")
    except OSError as exc:
        parser.exit(2, f"Scan failed: {exc}\n")
    print(f"{total:,} DAT files, {issues:,} rows with issues. Output: {args.out}")
    return int(bool(issues))


if __name__ == "__main__":
    raise SystemExit(main())
