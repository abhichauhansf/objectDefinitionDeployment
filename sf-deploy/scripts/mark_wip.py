#!/usr/bin/env python3
"""
mark_wip.py — Mark the lookup/MD blocker rows (referenceTo placeholder) as WIP
by writing the WIP marker into the WIP column (header `WIP`, now AE). Gated by the
user (sheet-write-confirmation).

Targets rows where validation check == dependency.ref (severity ERROR). Uses the
marker already present in the WIP column (default 'x') so we stay consistent.

  python scripts/mark_wip.py --spreadsheet-id <ID> --tab "成約:Sales_Deal" \
      [--marker x] [--apply]
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter

sys.path.insert(0, "scripts")
from fetch_sheet import find_header_row, norm, find_helper_cols  # noqa: E402
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
    ap.add_argument("--fields", default="",
                    help="comma-separated Field API Names to mark WIP (overrides report scan)")
    ap.add_argument("--marker", default="", help="WIP marker to write (default: reuse existing)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if args.fields.strip():
        dep = {f.strip() for f in args.fields.split(",") if f.strip()}
    else:
        rep = json.load(open(args.report, encoding="utf-8"))
        dep = {i["field"] for i in rep["items"]
               if i["severity"] == "ERROR" and i["check"] == "dependency.ref"}

    svc = get_write_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    h = find_header_row(grid)
    hdr = [norm(c).lower() for c in grid[h]]
    dcol = hdr.index("fullname")
    wip_idx = find_helper_cols(grid[h])["wip"]  # header-driven (AE fallback)

    # infer existing marker if not supplied
    existing = Counter()
    for i in range(h + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        v = cells[wip_idx] if wip_idx < len(cells) else ""
        if v:
            existing[v] += 1
    marker = args.marker or (existing.most_common(1)[0][0] if existing else "x")

    aacol = col_letter(wip_idx)
    targets = []
    for i in range(h + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        api = cells[dcol] if dcol < len(cells) else ""
        cur = cells[wip_idx] if wip_idx < len(cells) else ""
        if api in dep and cur.strip().lower() != marker.strip().lower():
            targets.append((i + 1, api))

    print(f"tab: {args.tab} | WIP col: {aacol} | marker: {marker!r} "
          f"(existing values: {dict(existing)})")
    print(f"rows to mark WIP ({len(targets)}): {[r for r, _ in targets]}")

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return 0

    data = [{"range": f"'{args.tab}'!{aacol}{r}", "values": [[marker]]}
            for r, _ in targets]
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=args.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"WROTE WIP marker into {resp.get('totalUpdatedCells')} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
