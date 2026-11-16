"""Generate entirely synthetic fixtures. Never import or overwrite business data."""
import json
import sqlite3
from pathlib import Path
from openpyxl import Workbook
from import_quotes import import_pending
from import_workbook import import_workbook

ROOT = Path(__file__).resolve().parent

def main():
    source = ROOT / 'source'
    output = ROOT / 'output'
    # Refuse existing destinations: rebuilding requires an explicit local cleanup.
    if source.exists() or output.exists():
        raise SystemExit('source/output already exist; refusing to overwrite. Use existing demo or a fresh checkout.')
    source.mkdir()
    output.mkdir()
    wb = Workbook()
    ws = wb.active
    ws.title = '巴西海运双清包税'
    ws['A1'] = 'SYNTHETIC TEST DATA — NOT A COMMERCIAL RATE SHEET'
    ws['A2'] = ws.title
    ws['A3'] = '合成测试数据，无商业有效期'
    for col, band in zip('EFG', ['1-5CBM', '5-10CBM', '10+CBM']):
        ws[f'{col}4'] = f'{band} USD/CBM'
    categories = ['普货一类', '普货二类', '普货三类', '四类', '五类', '六类', '七类', '八类', '九类', '十类', '十一类', '十二类']
    products = ['塑料收纳盒', '一次性塑料杯、泳镜', '宠物玩具/塑料泳池/泳镜', '支架、支架', '电子秤（无蓝牙）', '测试商品六', '测试商品七', '测试商品八', '非侵权充电宝', '平板电脑', '蜡烛', '手机']
    records = []
    for row, (category, product) in enumerate(zip(categories, products), 5):
        ws[f'A{row}'], ws[f'B{row}'], ws[f'H{row}'] = category, product, '1CBM:300KG'
        # Deliberately invented regression constants, not copied customer data.
        rates = [820, 800, 780] if row == 5 else ([1100, 1070, 1050] if row == 7 else [1500+row, 1400+row, 1300+row])
        for col, rate in zip('EFG', rates):
            ws[f'{col}{row}'] = ('单寻' if row == 15 else '不走') if row >= 15 else rate
        if row <= 12:
            records.append(dict(row=row, category=category, products=product, volume_weight_rule='1CBM:300KG', source_cells=f'A{row}:H{row}', reviewed=False, prices_usd_per_cbm=dict(zip(['1-5CBM','5-10CBM','10+CBM'], rates))))
    ws['B19'] = '合成规则：不足1CBM按1CBM；其他费用待确认'
    ws['B21'] = '合成测试密度限制 1CBM:300KG'
    ws['B23'] = '合成测试服务费：800CNY/票；100CNY/件；60CNY/件'
    other = wb.create_sheet('墨西哥空运专线')
    other['A1'], other['A2'] = '合成测试占位', '无商业报价'
    for i in range(15):
        wb.create_sheet(f'合成占位{i+1}')['A1'] = '测试分页与多工作表读取'
    xlsx = source / 'synthetic-demo.xlsx'
    wb.save(xlsx)
    raw = output / 'brazil_ocean_raw.json'
    raw.write_text(json.dumps(dict(source_file=xlsx.name, sheet=ws.title, effective_date_raw=ws['A3'].value, records=records), ensure_ascii=False), encoding='utf-8')
    db = output / 'freight_quotes.db'
    import_pending(raw, db, source)
    import_workbook(xlsx, db)
    with sqlite3.connect(db) as conn:
        for key, value in {'cbm_boundary_assignment': {'5':'1-5CBM','10':'5-10CBM'}, 'pricing_method':'whole_shipment', 'minimum_billable_cbm':1}.items():
            conn.execute('INSERT INTO user_rule_confirmations(workbook_id,scope,rule_key,value_json,original_user_text,confirmation_source) VALUES(1,?,?,?,?,?)', (ws.title, key, json.dumps(value), 'SYNTHETIC TEST FIXTURE ONLY; not human approval', 'synthetic_fixture'))
    print('Synthetic demo generated. Never use for real quotes or customer approval.')

if __name__ == '__main__':
    main()
