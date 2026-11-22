"""Calculate an internal draft from one pinned competition workbook."""
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from freight_tools import CHANNEL, dispatch
from import_workbook import import_workbook

SAMPLES = Path(__file__).resolve().parents[1] / "data/sample/fp01"
FILES = {key: f"rate-{key}-v1.xlsx" for key in ("A", "B")}


def sample_bytes(key):
    filename = FILES[key]
    data = (SAMPLES / filename).read_bytes()
    manifest = dict(line.split()[::-1] for line in (SAMPLES / "SHA256SUMS").read_text().splitlines())
    if hashlib.sha256(data).hexdigest() != manifest[filename]:
        raise ValueError("运价文件校验失败")
    return data


def calculate_sample(key, data, values):
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "rates.db"
        file = Path(tmp) / FILES[key]
        file.write_bytes(data)
        sha = import_workbook(file, db)["source_sha256"]
        rules = {"minimum_billable_cbm": 1, "pricing_method": "whole_shipment",
                 "cbm_boundary_assignment": {"5": "1-5CBM", "10": "5-10CBM"}}
        with closing(sqlite3.connect(db)) as conn, conn:
            for name, rule in rules.items():
                conn.execute("INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES (1,?,?,?,?,?)",
                             (CHANNEL, name, json.dumps(rule), "synthetic test rule", "synthetic_fixture"))
        candidates = dispatch("freight_classify", {"source_sha256": sha, "product_name": values["product_name"]}, db)["candidates"]
        if len(candidates) != 1:
            raise ValueError("该商品没有唯一的合成运价分类")
        result = dispatch("freight_calculate", {"category": candidates[0]["category"], "source_sha256": sha,
                          "weight_kg": values["weight_kg"], "volume_cbm": values["volume_cbm"]}, db)
        if result["base_freight_usd"] is None:
            raise ValueError(result.get("reason", "无法试算"))
        return result
