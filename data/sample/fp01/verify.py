"""Verify fixed synthetic inputs in a disposable database. Run with Python 3.9+."""
import hashlib
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2] / "prototype"))
from freight_tools import CHANNEL, dispatch
from import_workbook import import_workbook


def verify():
    if not __debug__:
        raise RuntimeError("Run without -O so fixture assertions are checked.")
    hashes = {}
    for entry in (HERE / "SHA256SUMS").read_text().splitlines():
        digest, name = entry.split()
        assert hashlib.sha256((HERE / name).read_bytes()).hexdigest() == digest, name
        hashes[name] = digest
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "fixture.db"
        for version in ("A", "B"):
            name = f"rate-{version}-v1.xlsx"
            import_workbook(HERE / name, db)
            with closing(sqlite3.connect(db)) as conn, conn:
                wid, = conn.execute("SELECT id FROM workbook_archives WHERE sha256=?", (hashes[name],)).fetchone()
                rules = {"minimum_billable_cbm": 1, "pricing_method": "whole_shipment",
                         "cbm_boundary_assignment": {"5": "1-5CBM", "10": "5-10CBM"}}
                for key, val in rules.items():
                    conn.execute("INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES(?,?,?,?,?,?)",
                                 (wid, CHANNEL, key, json.dumps(val), "synthetic test rule", "synthetic_fixture"))
        for version, expected in [("A", "2460.00"), ("B", "2280.00"), ("A", "2460.00")]:
            sha = hashes[f"rate-{version}-v1.xlsx"]
            classification = dispatch("freight_classify", {"product_name":"塑料收纳盒","source_sha256":sha}, db)
            category, = classification["candidates"]
            result = dispatch("freight_calculate", {"category":category["category"], "weight_kg":"400",
                              "volume_cbm":"3", "source_sha256":sha}, db)
            assert category["category"] == "普货一类"
            assert result["base_freight_usd"] == expected
            assert result["source"]["sha256"] == sha and result["source"]["price_cell"] == "E5"
            assert result["total_payable"] is None and result["usable_for_quote"] is False
            assert result["fee_source_notes"] == [{
                "source_sha256": sha, "sheet": CHANNEL, "cell": "B19",
                "raw_text": "不足1CBM按1CBM；未列费用待核。仅首档有样例运价。"}]
            assert not any(char.isdigit() for char in "".join(result["unresolved"]))
            print(version, sha, result["calculation"], result["source"]["price_cell"])


if __name__ == "__main__":
    verify()
