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
parser.add_argument("manifest", type=Path, help="Independently verified QA delivery JSON")
parser.add_argument("candidate", type=Path)
parser.add_argument("output", type=Path)
args = parser.parse_args()
root = args.candidate.resolve()
out = args.output.resolve()
if out.exists():
    parser.error("Choose a new output JSON path")
if not __debug__:
    parser.error("Run without -O so assertions are checked")
delivery_bytes = args.manifest.read_bytes()
delivery = json.loads(delivery_bytes)
inputs = {key: Path(path) for key, path in delivery["inputs"].items()}
fixture_dir = inputs["SOP"].parent
files = delivery["files"]
required = {"prototype/freight_tools.py", "prototype/import_workbook.py",
            (fixture_dir / "verify.py").as_posix(), (fixture_dir / "SHA256SUMS").as_posix(),
            *(inputs[key].as_posix() for key in ("A", "B", "SOP"))}
if not re.fullmatch(r"[0-9a-f]{40}", delivery["commit"]) or not required <= files.keys():
    parser.error("Incomplete delivery manifest")
for filename, expected in files.items():
    source = (root / filename).resolve()
    if root not in source.parents or not source.is_file():
        parser.error(f"Missing candidate file: {filename}")
    raw = source.read_bytes()
    if len(raw) != expected["bytes"] or hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        parser.error(f"Input bytes differ from delivery manifest: {filename}")
sys.path.insert(0, str(root / "prototype"))
from freight_tools import CHANNEL, dispatch
from import_workbook import import_workbook
for module in ("freight_tools", "import_workbook"):
    if Path(sys.modules[module].__file__).resolve() != root / "prototype" / (module + ".py"):
        parser.error(f"Unexpected runtime source: {module}")

fixture = root / fixture_dir
manifest = dict(line.split()[::-1] for line in (fixture / "SHA256SUMS").read_text(encoding="utf-8").splitlines())
assert manifest == {path.name: files[path.as_posix()]["sha256"] for path in inputs.values()}
observations = {"commit": delivery["commit"], "version": delivery["version"],
                "runtime_source": delivery["runtime_source"], "manifest_sha256": hashlib.sha256(delivery_bytes).hexdigest(),
                "files": {}, "runs": []}
for key, path in inputs.items():
    name = path.name
    raw = (root / path).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    assert digest == files[path.as_posix()]["sha256"]
    evidence = {"sha256": digest, "bytes": len(raw)}
    if name.endswith(".xlsx"):
        with ZipFile(root / path) as archive:
            book = ElementTree.fromstring(archive.read("xl/workbook.xml"))
            sheet, = book.findall(".//{*}sheet")
            rel_id = next(value for key, value in sheet.attrib.items() if key.endswith("}id"))
            rels = ElementTree.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target, = [rel.get("Target") for rel in rels if rel.get("Id") == rel_id]
            cells = ElementTree.fromstring(archive.read(target.lstrip("/")))
            values = {cell.get("r"): cell.findtext("{*}v") for cell in cells.findall(".//{*}c")}
            assert sheet.get("name") == CHANNEL
            assert values["B5"] == "塑料收纳盒" and values["A5"] == "普货一类"
            assert values["A1"] == f"合成验收运价 {key} · {delivery['version']}"
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
        filename = inputs[key].name
        result = import_workbook(root / inputs[key], db)
        assert result["source_sha256"] == manifest[filename]
        with closing(sqlite3.connect(db)) as con, con:
            wid, archived = con.execute("SELECT id,original_xlsx FROM workbook_archives WHERE sha256=?", (manifest[filename],)).fetchone()
            assert archived == (root / inputs[key]).read_bytes()
            rules = {"minimum_billable_cbm": 1, "pricing_method": "whole_shipment", "cbm_boundary_assignment": {"5": "1-5CBM", "10": "5-10CBM"}}
            for rule, value in rules.items():
                con.execute("INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES(?,?,?,?,?,?)",
                            (wid, CHANNEL, rule, json.dumps(value), "synthetic QA", "synthetic_fixture"))
    for key, expected in (("A", "2460.00"), ("B", "2280.00"), ("A", "2460.00")):
        filename = inputs[key].name
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
