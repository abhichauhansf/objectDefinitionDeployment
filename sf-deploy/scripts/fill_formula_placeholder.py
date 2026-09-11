#!/usr/bin/env python3
"""
fill_formula_placeholder.py — Put a placeholder text formula into the deployed
value column (col H, JP header "数式(設定値)") for FORMULA TEXT fields that are
blocked by an empty-formula validation error. Gated by the user
(sheet-write-confirmation). NOTE: the value lives in the '数式(設定値)' column
(H), NOT the 'displayFormat/…' description column (G) — see
fetch_sheet.build_col_map.

Only touches rows where: check == required.column AND Data Type == 'Formula Text'
AND the formula cell is currently empty. Formula Date / other types are left
alone. Values are written RAW so the literal text formula (e.g. "TBD") is stored.

  python scripts/fill_formula_placeholder.py --spreadsheet-id <ID> \
      --tab "成約:Sales_Deal" [--formula '"TBD"'] [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "scripts")
from fetch_sheet import find_header_row, norm  # noqa: E402
from write_back import get_write_service  # noqa: E402


def col_letter(i: int) -> str:
    s = ""
    while True:
        s = chr(65 + i % 26) + s
        i = i // 26 - 1
        if i < 0:
            break
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--report", default=".build/validation_report.json")
    ap.add_argument("--temp", default="temp_updates.json")
    ap.add_argument("--formula", default='"TBD"', help="placeholder formula to write")
    ap.add_argument("--datatype", default="Formula Text",
                    help="which formula field type to fill (e.g. 'Formula Text', 'Formula Date')")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rep = json.load(open(args.report, encoding="utf-8"))
    reqcol = {i["field"] for i in rep["items"]
              if i["severity"] == "ERROR" and i["check"] == "required.column"}
    tu = json.load(open(args.temp, encoding="utf-8"))
    dtype = {r.get("Field API Name"): r.get("Data Type")
             for r in tu if r.get("_type") != "object_meta"}

    svc = get_write_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    h = find_header_row(grid)
    hdr = [norm(c).lower() for c in grid[h]]
    jp = [norm(c) for c in (grid[h - 1] if h > 0 else [])]
    # The deployed value column is JP '数式(設定値)' (col H), NOT the
    # 'displayFormat/…' description column (col G). Locate it by the unique JP
    # substring '設定値'; fall back to the old displayFormat header only if the
    # JP header is absent (legacy tabs). See fetch_sheet.build_col_map.
    fcol = next((idx for idx in range(len(jp)) if "設定値" in jp[idx]), None)
    if fcol is None:
        fcol = next(idx for idx, c in enumerate(hdr) if c.startswith("displayformat"))
    dcol = hdr.index("fullname")

    targets = []
    for i in range(h + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        api = cells[dcol] if dcol < len(cells) else ""
        cur = cells[fcol] if fcol < len(cells) else ""
        if api in reqcol and dtype.get(api) == args.datatype and not cur:
            targets.append((i + 1, api))

    gcol = col_letter(fcol)
    print(f"tab: {args.tab} | formula col: {gcol} | placeholder: {args.formula}")
    print(f"Formula Text rows to fill: {len(targets)}")
    print("rows:", [r for r, _ in targets])

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return 0

    data = [{"range": f"'{args.tab}'!{gcol}{r}", "values": [[args.formula]]}
            for r, _ in targets]
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=args.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"WROTE placeholder into {resp.get('totalUpdatedCells')} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
