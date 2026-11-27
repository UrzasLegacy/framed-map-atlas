"""Build the gallery software and its collection as independently deployable ZIPs."""
import argparse
import csv
import tempfile
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from build_gallery import SITE_FILES, generate, migration_inputs


def build_archive(out, *, migration=None):
    if out.suffix.lower() != ".zip":
        raise ValueError("Choose an output filename ending in .zip")
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out.parent) as scratch:
        stage = Path(scratch)
        archive = stage / "package.zip"
        with ZipFile(archive, "w", compression=ZIP_DEFLATED) as package:
            if migration is None:
                for name in SITE_FILES:
                    package.write(Path(__file__).with_name(name), name)
                package.writestr(".nojekyll", b"")
            else:
                manifest, layouts = migration_inputs(migration)
                collection = stage / "collection"
                collection.mkdir()
                generate(manifest, layouts, collection)
                for path in sorted(collection.rglob("*")):
                    if path.is_file():
                        package.write(path, path.relative_to(stage).as_posix())
        archive.replace(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="build", required=True)
    site = commands.add_parser("site", help="Package the static site software")
    site.add_argument("--out", type=Path, default=Path("site.zip"))
    collection = commands.add_parser("collection", help="Convert a finalized migration into collection.zip")
    collection.add_argument("--migration", type=Path, required=True, help="Finalized migration folder or exact CSV")
    collection.add_argument("--out", type=Path, default=Path("collection.zip"))
    args = parser.parse_args()
    try:
        build_archive(args.out, migration=args.migration if args.build == "collection" else None)
    except ModuleNotFoundError as exc:
        if exc.name != "PIL":
            raise
        parser.exit(1, "The collection build requires Pillow. Run: python -m pip install Pillow\n")
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        parser.exit(1, f"Archive build failed: {exc}\n")
    print(f"Created {args.out.resolve()}")


if __name__ == "__main__":
    main()
