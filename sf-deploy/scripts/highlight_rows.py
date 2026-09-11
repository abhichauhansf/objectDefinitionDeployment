#!/usr/bin/env python3
"""
highlight_rows.py — Set a background color on the Column A ("No.") cell of
specific 1-based sheet rows of an object tab. Only Column A is colored (never the
whole row) so existing colors colleagues added in the data columns are preserved.
Gated by the user (sheet-write-confirmation).

  python scripts/highlight_rows.py --spreadsheet-id <ID> --tab "成約:Sales_Deal" \
      --rows 18,19,24 --color green [--apply]
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "scripts")
from write_back import get_write_service  # noqa: E402

COLORS = {
    "green": {"red": 0.72, "green": 0.88, "blue": 0.72},
    "pink": {"red": 0.98, "green": 0.75, "blue": 0.82},
    "white": {"red": 1, "green": 1, "blue": 1},
    "yellow": {"red": 1, "green": 0.95, "blue": 0.6},
    "lime": {"red": 0.85, "green": 0.93, "blue": 0.30},
}
START_COL, END_COL = 0, 1  # column A ("No.") only — never the whole row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--rows", required=True, help="comma-separated 1-based row numbers")
    ap.add_argument("--color", default="green", choices=list(COLORS))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    rows = sorted({int(x) for x in args.rows.split(",") if x.strip()})
    color = COLORS[args.color]

    svc = get_write_service()
    meta = svc.spreadsheets().get(spreadsheetId=args.spreadsheet_id,
                                  includeGridData=False).execute()
    gid = next(s["properties"]["sheetId"] for s in meta["sheets"]
               if s["properties"]["title"] == args.tab)

    print(f"tab: {args.tab} (gid {gid}) | color: {args.color} {color}")
    print(f"rows ({len(rows)}): {rows}")
    print("cell per row: A (No.) only")

    if not args.apply:
        print("DRY RUN — nothing applied. Re-run with --apply.")
        return 0

    requests = [{
        "repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": r - 1, "endRowIndex": r,
                      "startColumnIndex": START_COL, "endColumnIndex": END_COL},
            "cell": {"userEnteredFormat": {"backgroundColor": color}},
            "fields": "userEnteredFormat.backgroundColor",
        }
    } for r in rows]
    svc.spreadsheets().batchUpdate(
        spreadsheetId=args.spreadsheet_id, body={"requests": requests}).execute()
    print(f"APPLIED {args.color} to {len(rows)} rows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
