#!/usr/bin/env python3
"""Import this project's Brazil ocean extraction into a pending-only SQLite store.

No Excel writes, approval, date interpretation, band-boundary inference, or quoting.
Paths default to this script's directory, independently of the caller's cwd.
"""

import argparse
import hashlib
import io
import json
import sqlite3
import sys
from decimal import Decimal
from pathlib import Path

from openpyxl import load_workbook


ROOT = Path(__file__).resolve().parent
SHEET = "巴西海运双清包税"
BANDS = {"1-5CBM": "E", "5-10CBM": "F", "10+CBM": "G"}
APPLICATION_ID = 0x46525150  # FRQP: Freight Quotes Pending
SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE quote_sources (
    id INTEGER PRIMARY KEY,
    source_file TEXT NOT NULL,
    source_excel_path TEXT NOT NULL,
    source_excel_sha256 TEXT NOT NULL,
    sheet_name TEXT NOT NULL,
    first_import_json_path TEXT NOT NULL,
    first_import_json_sha256 TEXT NOT NULL,
    channel_raw TEXT,
    channel_cell TEXT NOT NULL DEFAULT 'A2',
    effective_date_raw TEXT NOT NULL,
    effective_date_cell TEXT NOT NULL DEFAULT 'A3',
    valid_from TEXT CHECK (valid_from IS NULL),
    valid_until TEXT CHECK (valid_until IS NULL),
    review_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (review_status = 'pending_review'),
    usable_for_quote INTEGER NOT NULL DEFAULT 0 CHECK (usable_for_quote = 0),
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (source_excel_sha256, sheet_name)
);
CREATE TABLE pending_categories (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES quote_sources(id),
    source_row INTEGER NOT NULL CHECK (source_row BETWEEN 5 AND 12),
    category TEXT NOT NULL,
    category_cell TEXT NOT NULL,
    products_raw TEXT,
    products_cell TEXT NOT NULL,
    source_cells TEXT NOT NULL,
    volume_weight_rule_raw TEXT,
    volume_weight_rule_cell TEXT NOT NULL,
    source_reviewed INTEGER NOT NULL CHECK (source_reviewed = 0),
    review_status TEXT NOT NULL DEFAULT 'pending_review'
        CHECK (review_status = 'pending_review'),
    usable_for_quote INTEGER NOT NULL DEFAULT 0 CHECK (usable_for_quote = 0),
    source_record_json TEXT NOT NULL,
    input_json_path TEXT NOT NULL,
    input_json_sha256 TEXT NOT NULL,
    imported_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (source_id, source_row)
);
CREATE TABLE pending_prices (
    id INTEGER PRIMARY KEY,
    category_id INTEGER NOT NULL REFERENCES pending_categories(id),
    band_label TEXT NOT NULL CHECK (band_label IN ('1-5CBM','5-10CBM','10+CBM')),
    band_header_raw TEXT NOT NULL,
    band_header_cell TEXT NOT NULL,
    price_amount TEXT NOT NULL,
    currency TEXT NOT NULL DEFAULT 'USD' CHECK (currency = 'USD'),
    price_unit TEXT NOT NULL DEFAULT 'CBM' CHECK (price_unit = 'CBM'),
    price_cell TEXT NOT NULL,
    boundary_status TEXT NOT NULL DEFAULT 'unconfirmed' CHECK (boundary_status = 'unconfirmed'),
    review_status TEXT NOT NULL DEFAULT 'pending_review' CHECK (review_status = 'pending_review'),
    usable_for_quote INTEGER NOT NULL DEFAULT 0 CHECK (usable_for_quote = 0),
    UNIQUE (category_id, band_label),
    UNIQUE (category_id, price_cell)
);
CREATE VIEW pending_quote_details AS
SELECT s.source_file, s.source_excel_path, s.source_excel_sha256, s.sheet_name,
       s.channel_raw, s.effective_date_raw, s.effective_date_cell,
       s.valid_from, s.valid_until, c.source_row, c.category, c.category_cell,
       c.products_raw, c.products_cell, c.source_cells,
       c.volume_weight_rule_raw, c.volume_weight_rule_cell,
       p.band_label, p.band_header_raw, p.band_header_cell,
       p.price_amount, p.currency, p.price_unit, p.price_cell,
       p.boundary_status, p.review_status, p.usable_for_quote
FROM pending_prices p
JOIN pending_categories c ON c.id = p.category_id
JOIN quote_sources s ON s.id = c.source_id;
"""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def canonical(record):
    return json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def amount(value):
    require(type(value) in (int, float), "价格必须是数字，不能是空值、布尔值或文本")
    number = Decimal(str(value))
    require(number.is_finite() and number > 0, "价格必须是有限正数")
    # Exact decimal text; monetary calculations are intentionally out of scope.
    return format(number.normalize(), "f")


def load_input(json_path, source_dir):
    raw = json_path.read_bytes()
    data = json.loads(raw)
    require(isinstance(data, dict), "JSON 顶层必须是对象")
    filename = data.get("source_file")
    require(isinstance(filename, str) and Path(filename).name == filename
            and filename.lower().endswith(".xlsx"), "source_file 必须为 Excel 文件名")
    require(data.get("sheet") == SHEET, "此导入器仅支持巴西海运双清包税工作表")
    require(isinstance(data.get("effective_date_raw"), str), "缺少原始生效信息")
    records = data.get("records")
    require(isinstance(records, list) and len(records) == 8, "原始提取结果必须含 8 类货物")
    rows, categories = set(), set()
    for record in records:
        require(isinstance(record, dict), "货物记录必须是对象")
        row = record.get("row")
        require(type(row) is int and 5 <= row <= 12 and row not in rows, "来源行必须为互不重复的 5–12 行")
        rows.add(row)
        category = record.get("category")
        require(isinstance(category, str) and category.strip() and category not in categories, "类别为空或重复")
        categories.add(category)
        require(type(record.get("reviewed")) is bool, "reviewed 必须明确为 true 或 false；缺失状态不导入")
        require(record.get("source_cells") == "A{0}:H{0}".format(row), "来源单元格范围不符合原提取器")
        for key in ("products", "volume_weight_rule"):
            require(key in record and (record[key] is None or isinstance(record[key], str)), "缺少或无效的 " + key)
        prices = record.get("prices_usd_per_cbm")
        require(isinstance(prices, dict) and set(prices) == set(BANDS), "每类必须保留原始三档价格")
        for value in prices.values():
            amount(value)

    excel_path = (source_dir / filename).resolve()
    excel_bytes = excel_path.read_bytes()
    # Read a single immutable byte snapshot. Never save the workbook.
    wb = load_workbook(io.BytesIO(excel_bytes), read_only=True, data_only=False)
    try:
        require(SHEET in wb.sheetnames, "Excel 缺少指定工作表")
        ws = wb[SHEET]
        require(ws["A3"].value == data["effective_date_raw"], "JSON 生效原文与 Excel A3 不一致，请重新提取")
        headers = {band: ws[col + "4"].value for band, col in BANDS.items()}
        for band, header in headers.items():
            compact = "".join(str(header).upper().split())
            require(band in compact and "USD/CBM" in compact, "Excel 档位表头已变化，需人工确认映射：" + band)
        for record in records:
            row = record["row"]
            for field, column in (("category", "A"), ("products", "B"), ("volume_weight_rule", "H")):
                require(record[field] == ws[f"{column}{row}"].value, f"JSON {field} 与 Excel {column}{row} 不一致")
            for band, column in BANDS.items():
                require(amount(record["prices_usd_per_cbm"][band]) == amount(ws[f"{column}{row}"].value),
                        f"JSON 价格与 Excel {column}{row} 不一致")
        channel = ws["A2"].value
    finally:
        wb.close()
    return {
        "data": data, "json_path": str(json_path), "json_sha256": sha256(raw),
        "excel_path": str(excel_path), "excel_sha256": sha256(excel_bytes),
        "headers": headers, "channel": channel,
    }


def initialize(conn):
    app_id = conn.execute("PRAGMA application_id").fetchone()[0]
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    objects = conn.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if not objects and app_id == 0 and version == 0:
        # execute individual DDL statements to keep schema and data in one transaction.
        for statement in SCHEMA.split(";"):
            if statement.strip():
                conn.execute(statement)
        conn.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    else:
        require(app_id == APPLICATION_ID and version == SCHEMA_VERSION,
                "目标数据库不是此版本的待审核报价库，已停止，未覆盖")


def import_pending(json_path, db_path, source_dir):
    json_path, db_path, source_dir = (Path(p).resolve() for p in (json_path, db_path, source_dir))
    require(db_path != json_path and db_path.suffix.lower() == ".db", "数据库必须使用独立的 .db 路径")
    source = load_input(json_path, source_dir)
    data = source["data"]
    pending = sorted((r for r in data["records"] if r["reviewed"] is False), key=lambda r: r["row"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        initialize(conn)
        inserted_categories = inserted_prices = existing_categories = 0
        if pending:
            conn.execute("""INSERT INTO quote_sources
                (source_file, source_excel_path, source_excel_sha256, sheet_name,
                 first_import_json_path, first_import_json_sha256, channel_raw, effective_date_raw)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (source_excel_sha256, sheet_name) DO NOTHING""",
                (data["source_file"], source["excel_path"], source["excel_sha256"], data["sheet"],
                 source["json_path"], source["json_sha256"], source["channel"], data["effective_date_raw"]))
            source_id, effective_raw = conn.execute("""SELECT id, effective_date_raw FROM quote_sources
                WHERE source_excel_sha256 = ? AND sheet_name = ?""", (source["excel_sha256"], data["sheet"])).fetchone()
            require(effective_raw == data["effective_date_raw"], "数据库已有来源信息不一致，已停止")
            for record in pending:
                row = record["row"]
                snapshot = canonical(record)
                existing = conn.execute("""SELECT id, source_record_json FROM pending_categories
                    WHERE source_id = ? AND source_row = ?""", (source_id, row)).fetchone()
                if existing:
                    category_id, previous = existing
                    require(previous == snapshot, f"第 {row} 行已存在但内容不同，已停止，未覆盖")
                    existing_categories += 1
                else:
                    category_id = conn.execute("""INSERT INTO pending_categories
                        (source_id, source_row, category, category_cell, products_raw, products_cell,
                         source_cells, volume_weight_rule_raw, volume_weight_rule_cell, source_reviewed,
                         source_record_json, input_json_path, input_json_sha256)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                        (source_id, row, record["category"], f"A{row}", record["products"], f"B{row}",
                         record["source_cells"], record["volume_weight_rule"], f"H{row}", snapshot,
                         source["json_path"], source["json_sha256"])).lastrowid
                    inserted_categories += 1
                expected_prices = []
                for band, column in BANDS.items():
                    price = (band, source["headers"][band], column + "4", amount(record["prices_usd_per_cbm"][band]), f"{column}{row}")
                    expected_prices.append(price)
                    if not existing:
                        conn.execute("""INSERT INTO pending_prices
                            (category_id, band_label, band_header_raw, band_header_cell, price_amount, price_cell)
                            VALUES (?, ?, ?, ?, ?, ?)""", (category_id,) + price)
                        inserted_prices += 1
                actual_prices = conn.execute("""SELECT band_label, band_header_raw, band_header_cell, price_amount, price_cell
                    FROM pending_prices WHERE category_id = ?""", (category_id,)).fetchall()
                require(sorted(actual_prices) == sorted(expected_prices), f"数据库第 {row} 行价格不完整或已被修改，已停止")

        require(not conn.execute("PRAGMA foreign_key_check").fetchall(), "数据库外键检查失败")
        require(conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok", "数据库完整性检查失败")
        counts = {}
        for table in ("quote_sources", "pending_categories", "pending_prices"):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            require(conn.execute(f"SELECT COUNT(*) FROM {table} WHERE review_status != 'pending_review' OR usable_for_quote != 0").fetchone()[0] == 0,
                    "发现非待审核记录，已停止")
        require(not conn.execute("""SELECT c.id FROM pending_categories c LEFT JOIN pending_prices p ON p.category_id = c.id
            GROUP BY c.id HAVING COUNT(p.id) != 3""").fetchall(), "数据库存在缺失价格的类别")
        require(sha256(Path(source["excel_path"]).read_bytes()) == source["excel_sha256"], "导入期间 Excel 已变化，已回滚")
        require(sha256(json_path.read_bytes()) == source["json_sha256"], "导入期间 JSON 已变化，已回滚")
        conn.commit()
        return {
            "database": str(db_path), "review_status": "pending_review", "usable_for_quote": False,
            "input_categories": len(data["records"]), "pending_input_categories": len(pending),
            "skipped_reviewed_categories": len(data["records"]) - len(pending),
            "inserted_categories": inserted_categories, "inserted_prices": inserted_prices,
            "already_present_categories": existing_categories, "database_counts": counts,
            "integrity_check": "ok", "source_excel_unchanged": True, "source_json_unchanged": True,
        }
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="将巴西海运 JSON 导入仅供人工审核的 SQLite 库，不产生正式有效运价")
    parser.add_argument("--input", type=Path, default=ROOT / "output/brazil_ocean_raw.json")
    parser.add_argument("--db", type=Path, default=ROOT / "output/freight_quotes.db")
    parser.add_argument("--source-dir", type=Path, default=ROOT / "source")
    args = parser.parse_args()
    try:
        result = import_pending(args.input, args.db, args.source_dir)
    except (ValueError, KeyError, OSError, sqlite3.Error) as exc:
        print("导入失败（未提交任何报价变更）：" + str(exc), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
