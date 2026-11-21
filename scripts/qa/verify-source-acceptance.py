"""Recheck FP-01's fixed synthetic A/B files, including fee attribution."""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import ZipFile

parser = argparse.ArgumentParser()
parser.add_argument("candidate", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = args.candidate.resolve()
out = args.output.resolve()
if out.exists():
    parser.error("Choose a new output JSON path")
if not __debug__:
    parser.error("Run without -O so assertions are checked")
for filename, expected in {
    "prototype/freight_tools.py": "17629c80d44a52ce7e91f1ed36773f9e0015117d112f0ab8182f68b9bdf1eb39",
    "prototype/import_workbook.py": "f34ebfd9fe4f8e2c3cca9fb8e9f9c663b94ba3bd1912d589dc1850b2ad61ba03",
    "data/sample/fp01/verify.py": "08003999234336b94d959fa3d01563d118e85c9725b2a65ee01b59a7faf114a3",
}.items():
    if hashlib.sha256((root / filename).read_bytes()).hexdigest() != expected:
        parser.error(f"Source differs from the fixed commit: {filename}")
sys.path.insert(0, str(root / "prototype"))
from freight_tools import CHANNEL, dispatch
from import_workbook import import_workbook

fixture = root / "data/sample/fp01"
manifest = dict(line.split()[::-1] for line in (fixture / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
assert manifest == {
    "rate-A-v1.xlsx": "72b99ebc0b9b5aea45d6f291bc2ee699716a704c5f845c84934426e7f86206cc",
    "rate-B-v1.xlsx": "b17fb83927e41102509dd93fbecc1cdf3492ce66dd3800196b8dad8205a6c8a0",
    "SOP-01-v1.md": "8cb172b47d33620c0fd973ebd60fa742ba38fbd70576bb824dbad41c653652f8",
}
observations = {"commit": "50cc37e6336a2b1c635bfd7e4addc3e8d4eb04b3", "files": {}, "runs": []}
for name, expected_hash in manifest.items():
    raw = (fixture / name).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == expected_hash
    evidence = {"sha256": digest, "bytes": len(raw)}
    if name.endswith(".xlsx"):
        with ZipFile(fixture / name) as archive:
            book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheet, = book.findall(".//{*}sheet")
            rel_id = next(value for key, value in sheet.attrib.items() if key.endswith("}id"))
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target, = [rel.get("Target") for rel in rels if rel.get("Id") == rel_id]
            cells = ElementTree.fromstring(archive.read(target.lstrip("/")))
            values = {cell.get("r"): cell.findtext("{*}v") for cell in cells.findall(".//{*}c")}
            assert sheet.get("name") == CHANNEL
            assert values["B5"] == "塑料收纳盒" and values["A5"] == "普货一类"
            assert values["A1"] == f"合成验收运价 {name[5]} · v1"
            evidence["sheet"] = sheet.get("name")
            evidence["cells"] = {at: values.get(at) for at in ("A1", "A3", "A5", "B5", "E4", "E5", "H5", "B19", "B20", "B22", "B23", "B24")}
    else:
        sop = raw.decode("utf-8")
        assert "§1" in sop and "箱数不能替代总体积" in sop and "免堆" not in sop
        evidence.update(section="§1", requires_volume=True, free_storage_clause_present=False)
    observations["files"][name] = evidence

with TemporaryDirectory(dir=out.parent) as isolated:
    db = Path(isolated) / "synthetic.db"
    for key in ("A", "B"):
        filename = f"rate-{key}-v1.xlsx"
        result = import_workbook(fixture / filename, db)
        assert result["source_sha256"] == manifest[filename]
        with closing(sqlite3.connect(db)) as con, con:
            wid, archived = con.execute("SELECT id,original_xlsx FROM workbook_archives WHERE sha256=?", (manifest[filename],)).fetchone()
            assert archived == (fixture / filename).read_bytes()
            rules = {"minimum_billable_cbm": 1, "pricing_method": "whole_shipment", "cbm_boundary_assignment": {"5": "1-5CBM", "10": "5-10CBM"}}
            for rule, value in rules.items():
                con.execute("INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES(?,?,?,?,?,?)",
                            (wid, CHANNEL, rule, json.dumps(value), "synthetic QA", "synthetic_fixture"))
    for key, expected in (("A", "2460.00"), ("B", "2280.00"), ("A", "2460.00")):
        filename = f"rate-{key}-v1.xlsx"
        digest = manifest[filename]
        cells = observations["files"][filename]["cells"]
        classified = dispatch("freight_classify", {"product_name": "塑料收纳盒", "source_sha256": digest}, db)
        category, = classified["candidates"]
        assert classified["source"] == {"file": filename, "sheet": CHANNEL, "sha256": digest}
        assert category["category"] == cells["A5"] and category["category_cell"] == "A5" and category["evidence_cell"] == "B5"
        result = dispatch("freight_calculate", {"category": category["category"], "volume_cbm": "3", "weight_kg": "400", "source_sha256": digest}, db)
        assert result["base_freight_usd"] == expected and Decimal(expected) == Decimal(3) * Decimal(cells["E5"])
        assert all(result["source"][field] == value for field, value in {"file": filename, "sheet": CHANNEL, "sha256": digest, "price_cell": "E5", "band_cell": "E4"}.items())
        assert result["total_payable"] is None and result["usable_for_quote"] is False
        observations["runs"].append({"selected": key, "classification_source": classified["source"], "category": category, "source": result["source"], "input": result["input"], "formula": result["calculation"], "base_usd": expected, "total_payable": result["total_payable"], "usable_for_quote": result["usable_for_quote"], "selected_fees": result["selected_fees"], "unresolved": result["unresolved"]})

observations["base_freight_checks"] = "PASS"
cell_reference = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,3}[1-9][0-9]*)(?![A-Za-z0-9])")
fee_amount = re.compile(r"\d+(?:\.\d+)?\s*(?:USD|CNY|%)(?:/(?:万|票|件))?")
unsupported = []
for run in observations["runs"]:
    name = run["source"]["file"]
    values = observations["files"][name]["cells"]
    for note in run["unresolved"]:
        cited = {cell: values.get(cell) for cell in cell_reference.findall(note)}
        missing_cells = [cell for cell, value in cited.items() if not value]
        source_amounts = set(fee_amount.findall(" ".join(value for value in cited.values() if value)))
        missing_amounts = sorted(set(fee_amount.findall(note)) - source_amounts)
        if missing_cells or missing_amounts:
            finding = {"file": name, "note": note, "cited_cells": cited,
                       "empty_cells": missing_cells, "unsupported_amounts": missing_amounts}
            if finding not in unsupported:
                unsupported.append(finding)
fee_status = "FAIL" if unsupported else "PASS"
observations["fee_attribution"] = {
    "status": fee_status,
    "expected": "Each cited cell must have a value; each quoted USD, CNY or percentage amount must appear in the cited cells of the selected workbook.",
    "findings": unsupported,
    "scope": "Freight calculation tool output. Display of these notes in UI, approval invalidation on source change, and SOP retrieval not tested."
}
out.write_bytes((json.dumps(observations, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
print(f"Base freight: PASS. Fee attribution: {fee_status} ({len(unsupported)} distinct unsupported source/note pairs).")
sys.exit(1 if unsupported else 0)
