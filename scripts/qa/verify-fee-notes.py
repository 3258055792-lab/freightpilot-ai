"""Check fee-source records and adjacent synthetic controls against original XLSX cells."""
import argparse
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree as ET
from zipfile import ZipFile

parser = argparse.ArgumentParser(description=__doc__)
for arg in ("manifest", "candidate", "output"):
    parser.add_argument(arg, type=Path)
args = parser.parse_args()
if not __debug__ or args.output.exists():
    parser.error("Enable assertions and choose a new output path")
manifest = json.loads(args.manifest.read_bytes())
root = args.candidate.resolve()
for path, item in manifest["files"].items():
    content = (root / path).read_bytes()
    assert len(content) == item["bytes"] and hashlib.sha256(content).hexdigest() == item["sha256"], path
sys.path.insert(0, str(root / "prototype"))
from freight_tools import CHANNEL, dispatch
from import_workbook import import_workbook
assert Path(sys.modules["freight_tools"].__file__).resolve() == root / "prototype/freight_tools.py"
assert Path(sys.modules["import_workbook"].__file__).resolve() == root / "prototype/import_workbook.py"

results = {"commit": manifest["commit"], "manifest_sha256": hashlib.sha256(args.manifest.read_bytes()).hexdigest(), "source_runs": [], "adjacent_controls": []}
inputs = {}
with TemporaryDirectory(dir=args.output.resolve().parent) as temporary:
    dbpath = Path(temporary) / "rates.db"
    for label in ("A", "B"):
        relative = manifest["inputs"][label]
        book = root / relative
        sha = manifest["files"][relative]["sha256"]
        with ZipFile(book) as xlsx:
            sheet, = ET.fromstring(xlsx.read("xl/workbook.xml")).findall(".//{*}sheet")
            rel = next(v for k, v in sheet.attrib.items() if k.endswith("}id"))
            target, = [row.get("Target") for row in ET.fromstring(xlsx.read("xl/_rels/workbook.xml.rels")) if row.get("Id") == rel]
            cells = {c.get("r"): c.findtext("{*}v") for c in ET.fromstring(xlsx.read(target.lstrip("/"))).findall(".//{*}c")}
        notes = [{"source_sha256": sha, "sheet": sheet.get("name"), "cell": cell, "raw_text": cells[cell]}
                 for cell in ("B19", "B20", "B22", "B23", "B24") if cells.get(cell)]
        assert [row["cell"] for row in notes] == ["B19"] and sheet.get("name") == CHANNEL
        assert cells.get("F5") is None and cells.get("B23") is None
        inputs[label] = (sha, cells, notes)
        assert import_workbook(book, dbpath)["source_sha256"] == sha
        with closing(sqlite3.connect(dbpath)) as db, db:
            workbook_id, = db.execute("SELECT id FROM workbook_archives WHERE sha256=?", (sha,)).fetchone()
            rules = {"minimum_billable_cbm": 1, "pricing_method": "whole_shipment", "cbm_boundary_assignment": {"5": "1-5CBM", "10": "5-10CBM"}}
            for rule, value in rules.items():
                db.execute("INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES(?,?,?,?,?,?)",
                           (workbook_id, CHANNEL, rule, json.dumps(value), "synthetic QA", "synthetic_fixture"))
    for label, volume, weight in (("A", "3", "400"), ("B", "3", "400"), ("A", "3", "400"), ("B", "0.5", "120")):
        sha, cells, notes = inputs[label]
        classified = dispatch("freight_classify", {"product_name": "塑料收纳盒", "source_sha256": sha}, dbpath)
        matched, = classified["candidates"]
        assert matched["evidence_cell"] == "B5" and matched["category_cell"] == "A5" and matched["category"] == cells["A5"]
        fields = {"source_sha256": sha, "category": matched["category"], "weight_kg": weight, "volume_cbm": volume}
        actual = dispatch("freight_calculate", fields, dbpath)
        assert actual["source"]["sha256"] == sha and actual["source"]["sheet"] == CHANNEL
        assert actual["source"]["file"] == Path(manifest["inputs"][label]).name and actual["source"]["price_cell"] == "E5"
        assert actual["fee_source_notes"] == notes and not any(c.isdigit() for c in "".join(actual["unresolved"]))
        assert actual["selected_fees"] == [] and actual["total_payable"] is None and actual["usable_for_quote"] is False
        assert Decimal(actual["base_freight_usd"]) == max(Decimal(volume), Decimal(1)) * Decimal(cells["E5"])
        result = {"selected": label, "volume_cbm": volume, "weight_kg": weight, "base_usd": actual["base_freight_usd"], "fee_source_notes": actual["fee_source_notes"],
                  "unresolved": actual["unresolved"], "selected_fees": actual["selected_fees"], "total_payable": actual["total_payable"], "usable_for_quote": actual["usable_for_quote"]}
        group = "adjacent_controls" if volume == "0.5" else "source_runs"
        results[group].append(result)
    for label in ("A", "B"):
        sha, cells, _ = inputs[label]
        for option, value, missing, expected_error in (("volume_cbm", "6", "F5", "原表单价"), ("fumigation", True, "B23", "来源费用原文已变化")):
            fields = {"source_sha256": sha, "category": cells["A5"], "weight_kg": "400", "volume_cbm": "3", option: value}
            try:
                dispatch("freight_calculate", fields, dbpath)
            except ValueError as exc:
                error = str(exc)
                assert expected_error in error, error
                results["adjacent_controls"].append({"selected": label, "input_change": {option: value}, "cell": missing, "source_value": cells.get(missing),
                                                     "error": error, "outcome": "blocked; no calculation result returned"})
            else:
                raise AssertionError(f"{label} {missing} allowed calculation without a source value")
results["structured_fee_checks"] = "PASS"
args.output.write_bytes((json.dumps(results, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print("PASS: three A-B-A results, structured notes, adjacent B minimum and four missing-source controls")
