"""Export the original DAT, a PNG, and one manifest row per kept map ID."""

import argparse
import csv
import shutil
import tempfile
from pathlib import Path

from map_assets import index_dats, load_kept_records, pixel_signature, read_pixels, render_pixels


def export_kept(data_dir, review_csv, out):
    if out.exists():
        raise ValueError(f"output already exists: {out}. Choose a new output folder.")
    kept = load_kept_records(review_csv)
    dats = index_dats(data_dir)
    problems = []
    for map_id in sorted(kept):
        paths = dats.get(map_id, [])
        if len(paths) != 1:
            reason = ", ".join(p.name for p in paths) if paths else "missing DAT"
            problems.append(f"map {map_id}: {reason}")
    if problems:
        raise ValueError(f"Expected one DAT per kept ID in {data_dir}:\n" + "\n".join(problems))

    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent, prefix=".kept-maps-") as tmp:
        stage = Path(tmp) / "export"
        (stage / "dats").mkdir(parents=True)
        (stage / "pngs").mkdir()
        with (stage / "manifest.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(["original_map_id", "pixel_key", "chosen_name", "dat_path", "png_path"])
            for map_id, (expected, title) in sorted(kept.items()):
                dat = Path("dats") / f"map_{map_id}.dat"
                png = Path("pngs") / f"map_{map_id}.png"
                source, = dats[map_id]
                shutil.copyfile(source, stage / dat)
                try:
                    pixels = read_pixels(stage / dat)
                except ValueError as exc:
                    raise ValueError(f"map {map_id} ({source}): {exc}") from exc
                key, _ = pixel_signature(pixels)
                if key != expected:
                    raise ValueError(f"map {map_id}: review pixel key {expected} differs from DAT key {key} ({source})")
                (stage / png).write_bytes(render_pixels(pixels))
                writer.writerow([map_id, key, title, dat.as_posix(), png.as_posix()])
        stage.rename(out)
    return len(kept)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_dir", type=Path, help="Folder containing the original map DAT files")
    parser.add_argument("--review-csv", required=True, type=Path, help="Finalized map review CSV")
    parser.add_argument("--out", required=True, type=Path, help="New folder for the completed export")
    args = parser.parse_args()
    try:
        count = export_kept(args.data_dir, args.review_csv, args.out)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Export failed: {exc}\n")
    print(f"Exported {count:,} kept DATs and PNGs. Manifest: {args.out / 'manifest.csv'}")


if __name__ == "__main__":
    main()
