#!/usr/bin/env python3
"""
write_attr_fixes.py — Write deployment-time ATTRIBUTE fixes back into the object
sheet (the value cells themselves, not the AH comment column). This closes the
sheet<->org divergence when the assistant fills/repairs a field attribute (e.g.
a formula body in `Type Specific Value`, a `Relationship Name`) to make a deploy
succeed. Per the sheet-write-confirmation rule, default is a DRY-RUN preview
(cell: old -> new); only --apply writes.

Fixes are supplied as JSON: [{"field": "<API>", "column": "<Header>", "value": "<new>"}]
Columns are located by HEADER NAME (normalized, case-insensitive), reshuffle-proof.

Usage:
  python scripts/write_attr_fixes.py --spreadsheet-id <ID> --tab Deal \
      --fixes .build/attr_fixes.json [--apply]
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sheet import get_sheets_service, find_header_row, norm  # noqa: E402


def col_letter(idx0: int) -> str:
    s = ""
    n = idx0
    while True:
        s = chr(65 + n % 26) + s
        n = n // 26 - 1
        if n < 0:
            break
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--fixes", required=True, help="JSON [{field, column, value}]")
    ap.add_argument("--apply", action="store_true", help="write (else dry-run preview)")
    args = ap.parse_args()

    fixes = json.load(open(args.fixes))
    svc = get_sheets_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id,
        range=f"'{args.tab}'", valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [])

    hidx = find_header_row(grid)
    header = [norm(c).lower() for c in grid[hidx]]
    full_col = header.index("fullname")

    # map field API -> row number (1-based sheet row)
    row_of = {}
    for r in range(hidx + 1, len(grid)):
        cells = grid[r]
        api = (cells[full_col] if full_col < len(cells) else "").strip()
        if api:
            row_of[api] = r + 1  # 1-based

    data = []
    print("=" * 72)
    for fx in fixes:
        field, colname, newval = fx["field"], fx["column"], fx["value"]
        try:
            cidx = header.index(norm(colname).lower())
        except ValueError:
            print(f"  ⚠️  column '{colname}' not found in header — skipped {field}")
            continue
        row = row_of.get(field)
        if not row:
            print(f"  ⚠️  field '{field}' not found on tab — skipped")
            continue
        cell = f"{col_letter(cidx)}{row}"
        old = grid[row - 1][cidx] if cidx < len(grid[row - 1]) else ""
        print(f"  {cell}  ({field} · {colname})")
        print(f"      OLD={old!r}")
        print(f"      NEW={newval!r}")
        data.append({"range": f"'{args.tab}'!{cell}", "values": [[newval]]})
    print("=" * 72)
    print(f"cells to write: {len(data)}")

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply (after confirmation).")
        return 0
    if not data:
        print("nothing to write.")
        return 0
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=args.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data},
    ).execute()
    print(f"APPLIED. totalUpdatedCells = {resp.get('totalUpdatedCells')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
