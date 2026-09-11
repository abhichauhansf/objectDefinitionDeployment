#!/usr/bin/env python3
"""
sheet_field_rows.py — Live read of an object tab that captures the EXACT sheet
row number of every field, plus which column holds fullName / label. This is the
anchor needed for a safe write-back (never match by label — labels repeat).

  python scripts/sheet_field_rows.py --spreadsheet-id <ID> --tab "成約:Sales_Deal" \
      [--out .build/sales_deal_rows.json]
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "scripts")
from fetch_sheet import get_sheets_service, find_header_row, norm, GRAY_GUARD_SUBSTR, is_field_list_end  # noqa: E402


def col_letter(idx0: int) -> str:
    s = ""
    n = idx0
    while True:
        s = chr(ord("A") + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--out", default=".build/sales_deal_rows.json")
    args = ap.parse_args()

    svc = get_sheets_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])

    hidx = find_header_row(grid)
    if hidx is None:
        print("No header row found")
        return 1
    header = [norm(c).lower() for c in grid[hidx]]
    label_col = header.index("label") if "label" in header else None
    full_col = header.index("fullname") if "fullname" in header else None
    type_col = header.index("type") if "type" in header else None

    rows = []
    for i in range(hidx + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        if is_field_list_end(cells):
            break
        label = cells[label_col] if label_col is not None and label_col < len(cells) else ""
        full = cells[full_col] if full_col is not None and full_col < len(cells) else ""
        ftype = cells[type_col] if type_col is not None and type_col < len(cells) else ""
        if not (label or full):
            continue
        rows.append({
            "sheet_row": i + 1,  # 1-based sheet row
            "label": label,
            "existing_fullName": full,
            "type": ftype,
            "blank": not bool(full),
        })

    json.dump({
        "tab": args.tab,
        "header_row": hidx + 1,
        "fullName_col": col_letter(full_col) if full_col is not None else None,
        "label_col": col_letter(label_col) if label_col is not None else None,
        "rows": rows,
    }, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    blank = [r for r in rows if r["blank"]]
    named = [r for r in rows if not r["blank"]]
    print(f"tab: {args.tab}")
    print(f"header row: {hidx + 1} | fullName col: {col_letter(full_col)} | label col: {col_letter(label_col)}")
    print(f"total field rows: {len(rows)} | blank: {len(blank)} | already named: {len(named)}")
    print("--- first 12 field rows (sheet_row | label | existing_fullName) ---")
    for r in rows[:12]:
        print(f"  row {r['sheet_row']:>3}  {r['label'][:22]:<22}  {r['existing_fullName'] or '(blank)'}")
    print("--- the already-named rows (to see where standard fields sit) ---")
    for r in named:
        print(f"  row {r['sheet_row']:>3}  {r['label'][:22]:<22}  {r['existing_fullName']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
