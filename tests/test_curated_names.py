import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from curated_names import catalog_names, write_catalog
from test_scanner import tag


class CuratedNameTest(unittest.TestCase):
    def test_every_curated_id_and_every_alias(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for i, shade in [(1, 4), (2, 4), (3, 4), (4, 4), (5, 6)]:
                data = {"data": {"colors": bytes([shade]) * 16384}}
                (root / f"map_{i}.dat").write_bytes(b"\x0a\0\0" + tag(data)[1])
            reports = [root / "first.json", root / "second.json"]
            def entry(i, titles):
                return {"map_id": i, "observed_names": dict.fromkeys(titles, 1)}
            reports[0].write_text(json.dumps([entry(1, ["A"]), entry(2, ["B", "C"])]))
            reports[1].write_text(json.dumps([entry(1, ["A", "B"]), entry(6, ["Lost"])]))
            output = io.StringIO()
            totals, errors = write_catalog(root, {1, 3, 4, 5, 6}, catalog_names(reports), output)
        rows = list(csv.DictReader(io.StringIO(output.getvalue())))
        self.assertEqual([int(r["map_id"]) for r in rows], [1, 3, 4, 5, 6])
        for row in rows[:3]:
            self.assertEqual(json.loads(row["name_source_ids"]), {"A": [1], "B": [1, 2], "C": [2]})
        self.assertEqual(rows[0]["other_identical_ids"], "2, 3, 4")
        self.assertEqual(json.loads(rows[0]["direct_names"]), ["A", "B"])
        self.assertEqual(rows[3]["candidate_names"], "")
        self.assertEqual(rows[4]["candidate_names"], "Lost")
        self.assertEqual(errors[6][0], "missing_data")
        self.assertEqual(totals, {"multiple_names": 3, "no_name": 1, "one_name": 1})
