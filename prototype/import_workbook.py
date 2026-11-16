#!/usr/bin/env python3
from contextlib import closing
"""Lossless XLSX archive plus searchable cells. Does not approve or calculate rates."""
import argparse
import hashlib
import io
import json
import posixpath
import sqlite3
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
N = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
DDL = [
'''CREATE TABLE IF NOT EXISTS workbook_archives (
 id INTEGER PRIMARY KEY, source_path TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE,
 original_xlsx BLOB NOT NULL, review_status TEXT NOT NULL DEFAULT 'pending_review'
 CHECK(review_status='pending_review'), usable_for_quote INTEGER NOT NULL DEFAULT 0 CHECK(usable_for_quote=0))''',
'''CREATE TABLE IF NOT EXISTS workbook_sheets (
 id INTEGER PRIMARY KEY, workbook_id INTEGER NOT NULL REFERENCES workbook_archives(id),
 name TEXT NOT NULL, position INTEGER NOT NULL, visibility TEXT NOT NULL,
 xml_path TEXT NOT NULL, original_xml BLOB NOT NULL, merged_ranges_json TEXT NOT NULL,
 UNIQUE(workbook_id,name))''',
'''CREATE TABLE IF NOT EXISTS workbook_cells (
 sheet_id INTEGER NOT NULL REFERENCES workbook_sheets(id), coordinate TEXT NOT NULL,
 cell_type TEXT NOT NULL, value_text TEXT, formula TEXT, cached_value TEXT,
 original_xml TEXT NOT NULL, PRIMARY KEY(sheet_id,coordinate))''',
'''CREATE TABLE IF NOT EXISTS user_rule_confirmations (
 workbook_id INTEGER NOT NULL REFERENCES workbook_archives(id), scope TEXT NOT NULL,
 rule_key TEXT NOT NULL, value_json TEXT NOT NULL, original_user_text TEXT NOT NULL,
 confirmation_source TEXT NOT NULL, confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
 PRIMARY KEY(workbook_id,scope,rule_key))'''
]


def import_workbook(path, dbpath):
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    sheets = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(t.text or '' for t in e.findall('.//s:t', N))
                       for e in ET.fromstring(z.read('xl/sharedStrings.xml'))]
        rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        for position, node in enumerate(ET.fromstring(z.read('xl/workbook.xml')).findall('s:sheets/s:sheet', N)):
            target = rels[node.attrib[REL]]
            member = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            xml = z.read(member)
            tree = ET.fromstring(xml)
            cells = []
            for c in tree.findall('s:sheetData/s:row/s:c', N):
                kind = c.get('t', 'n')
                value = c.findtext('s:v', default=None, namespaces=N)
                formula_node = c.find('s:f', N)
                formula = None if formula_node is None else (formula_node.text or '')
                text = strings[int(value)] if kind == 's' and value is not None else value
                if kind == 'inlineStr':
                    text = ''.join(t.text or '' for t in c.findall('s:is//s:t', N))
                cells.append((c.attrib['r'], kind, text, formula, value, ET.tostring(c, encoding='unicode')))
            merges = [m.attrib['ref'] for m in tree.findall('s:mergeCells/s:mergeCell', N)]
            sheets.append((node.attrib['name'], position, node.get('state','visible'), member, xml, json.dumps(merges), cells))
    with closing(sqlite3.connect(str(dbpath))) as db, db:
        db.execute('PRAGMA foreign_keys=ON')
        db.execute('BEGIN IMMEDIATE')
        for ddl in DDL:
            db.execute(ddl)
        existing = db.execute('SELECT id, original_xlsx FROM workbook_archives WHERE sha256=?', (digest,)).fetchone()
        if existing:
            wid = existing[0]
            assert existing[1] == raw, 'Archived bytes differ'
        else:
            wid = db.execute('INSERT INTO workbook_archives(source_path,sha256,original_xlsx) VALUES (?,?,?)',
                             (str(path),digest,raw)).lastrowid
            for name, pos, visibility, member, xml, merges, cells in sheets:
                sid = db.execute('''INSERT INTO workbook_sheets(workbook_id,name,position,visibility,xml_path,original_xml,merged_ranges_json)
                    VALUES(?,?,?,?,?,?,?)''', (wid,name,pos,visibility,member,xml,merges)).lastrowid
                db.executemany('INSERT INTO workbook_cells VALUES(?,?,?,?,?,?,?)', [(sid,) + c for c in cells])
        # Compare every stored cell and sheet with the source on every run.
        assert db.execute('SELECT COUNT(*) FROM workbook_sheets WHERE workbook_id=?',(wid,)).fetchone()[0] == len(sheets)
        for name, pos, visibility, member, xml, merges, cells in sheets:
            saved = db.execute('SELECT id,position,visibility,xml_path,original_xml,merged_ranges_json FROM workbook_sheets WHERE workbook_id=? AND name=?',(wid,name)).fetchone()
            assert saved[1:] == (pos,visibility,member,xml,merges)
            stored = db.execute('SELECT coordinate,cell_type,value_text,formula,cached_value,original_xml FROM workbook_cells WHERE sheet_id=?',(saved[0],)).fetchall()
            assert sorted(stored) == sorted(cells), name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, 'Source changed during import'
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert not db.execute('PRAGMA foreign_key_check').fetchall()
    return {'source_sha256':digest,'new_workbook_versions':int(not existing),
            'sheets':[{'name':s[0], 'cells':len(s[6]), 'nonempty_cells':sum(c[2] is not None or c[3] is not None for c in s[6]),
                       'formulas':sum(c[3] is not None for c in s[6]), 'merged_ranges':len(json.loads(s[5]))} for s in sheets],
            'original_xlsx_bytes':len(raw),'all_cells_verified':True,'source_unchanged':True}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--db',type=Path,default=ROOT/'output/freight_quotes.db')
    args=parser.parse_args()
    files=list((ROOT/'source').glob('*.xlsx'))
    if args.source is None and len(files)!=1:
        parser.error('Specify --source when there is not exactly one XLSX')
    print(json.dumps(import_workbook((args.source or files[0]).resolve(),args.db.resolve()),ensure_ascii=False,indent=2))
