"""Create an offline visual review page beside an existing sorted map collection."""
import argparse
import csv
from pathlib import Path

from sort_canvases import load_titles, review_page


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("collection", type=Path, help="extracted directory containing map-index.csv and the PNG folders")
    parser.add_argument("--names-report", type=Path, action="append", default=[],
                        help="map-names.json or curated-map-names.csv from this save; repeat to combine")
    args = parser.parse_args()
    try:
        titles = load_titles(args.names_report)
        with (args.collection / "map-index.csv").open(encoding="utf-8-sig", newline="") as f:
            page = review_page(csv.DictReader(f), titles)
        (args.collection / "review.html").write_bytes(page)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Unable to create gallery: {exc}\n")
    print(args.collection / "review.html")
