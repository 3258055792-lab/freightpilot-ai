from pathlib import Path
from openpyxl import load_workbook
import json

ROOT = Path(__file__).resolve().parent
files = list((ROOT / "source").glob("*.xlsx"))
if len(files) != 1:
    raise SystemExit("请确保 source 中只有一份待处理的xlsx文件")

file = files[0]
wb = load_workbook(file, read_only=True, data_only=False)
sheet = "巴西海运双清包税"
ws = wb[sheet]

records = []
for row in range(5, 13):
    records.append({
        "row": row,
        "category": ws[f"A{row}"].value,
        "products": ws[f"B{row}"].value,
        "prices_usd_per_cbm": {
            "1-5CBM": ws[f"E{row}"].value,
            "5-10CBM": ws[f"F{row}"].value,
            "10+CBM": ws[f"G{row}"].value
        },
        "volume_weight_rule": ws[f"H{row}"].value,
        "source_cells": f"A{row}:H{row}",
        "reviewed": False
    })

result = {
    "source_file": file.name,
    "sheet": sheet,
    "effective_date_raw": ws["A3"].value,
    "records": records
}

destination = ROOT / "output/brazil_ocean_raw.json"
destination.parent.mkdir(parents=True, exist_ok=True)
destination.write_text(
    json.dumps(result, ensure_ascii=False, indent=2, default=str),
    encoding="utf-8"
)

wb.close()
print("提取完成：", destination)
print("货物分类数量：", len(records))
