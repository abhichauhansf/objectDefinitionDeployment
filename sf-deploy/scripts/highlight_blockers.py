#!/usr/bin/env python3
"""
highlight_blockers.py — Highlight (pink) the field rows in an object tab that
are blocked by validation errors. Gated by the user (sheet-write-confirmation).

Maps blocked field API names (from .build/validation_report.json) to their sheet
rows by reading the tab's fullName column LIVE, then sets a pink background on the
Column A ("No.") cell of each blocked row. Only Column A is colored (never the
whole row) so existing colors colleagues added in the data columns are preserved.

  python scripts/highlight_blockers.py --spreadsheet-id <ID> --tab "成約:Sales_Deal" \
      [--report .build/validation_report.json] [--apply]

Without --apply it is a dry run (lists rows, changes nothing).
"""
from __future__ import annotations

import argparse
import json
import sys

sys.path.insert(0, "scripts")
from fetch_sheet import find_header_row, norm  # noqa: E402
from write_back import get_write_service  # noqa: E402

PINK = {"red": 0.98, "green": 0.75, "blue": 0.82}
START_COL = 0   # column A ("No.", 0-based)
END_COL = 1     # column A only -> end-exclusive 1 (never the whole row)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--report", default=".build/validation_report.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rep = json.load(open(args.report, encoding="utf-8"))
    blocked = {i["field"] for i in rep["items"]
               if i["severity"] == "ERROR" and i["field"] != "-"}

    svc = get_write_service()
    meta = svc.spreadsheets().get(spreadsheetId=args.spreadsheet_id,
                                  includeGridData=False).execute()
    gid = next(s["properties"]["sheetId"] for s in meta["sheets"]
               if s["properties"]["title"] == args.tab)

    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    hidx = find_header_row(grid)
    header = [norm(c).lower() for c in grid[hidx]]
    full_col = header.index("fullname")

    blocked_rows = []
    for i in range(hidx + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        full = cells[full_col] if full_col < len(cells) else ""
        if full in blocked:
            blocked_rows.append(i + 1)  # 1-based

    print(f"tab: {args.tab} (gid {gid})")
    print(f"blocked field rows: {len(blocked_rows)}")
    print(f"rows: {blocked_rows}")
    print(f"cell per row: A (No.) only  |  fill: pink {PINK}")

    if not args.apply:
        print("DRY RUN — no formatting applied. Re-run with --apply.")
        return 0

    requests = []
    for r in blocked_rows:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": gid,
                    "startRowIndex": r - 1, "endRowIndex": r,
                    "startColumnIndex": START_COL, "endColumnIndex": END_COL,
                },
                "cell": {"userEnteredFormat": {"backgroundColor": PINK}},
                "fields": "userEnteredFormat.backgroundColor",
            }
        })
    svc.spreadsheets().batchUpdate(
        spreadsheetId=args.spreadsheet_id, body={"requests": requests}).execute()
    print(f"APPLIED pink highlight to {len(blocked_rows)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
