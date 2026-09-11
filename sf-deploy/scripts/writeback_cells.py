#!/usr/bin/env python3
"""
writeback_cells.py — targeted, gated col-D API-name writeback for SPECIFIC rows
(identified by the sheet's Column-A "No." + label), as opposed to write_back.py's
whole-blank-column alignment. Used for duplicate-label reuse/new resolutions.

Reads the tab LIVE, maps each target (No. + label) to its real grid row, verifies
the current fullName (col D) cell is BLANK and the row is NOT IsDelete, prints a
cell: old -> new preview, and only writes with --apply.

Plan file JSON: [{"no": "24", "label_contains": "取消理由", "api": "TI_Fnt_..._c"}]
"""
from __future__ import annotations
import argparse, json, sys
sys.path.insert(0, "scripts")
import fetch_sheet as F
from fetch_sheet import find_helper_cols
import write_back as WB


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--plan", required=True, help="JSON list of {no,label_contains,api}")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--allow-isdelete", action="store_true",
                    help="permit writing col D on IsDelete=TRUE rows (documented-"
                         "deletion case: record the label-matched API being deleted)")
    args = ap.parse_args()

    plan = json.load(open(args.plan, encoding="utf-8"))
    svc = WB.get_write_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    h = F.find_header_row(grid)
    hdr = [F.norm(c).lower() for c in grid[h]]
    c_api = hdr.index("fullname")
    c_lbl = hdr.index("label")
    hc = find_helper_cols(grid[h])
    c_del, c_wip = hc.get("isdelete"), hc.get("wip")

    def g(r, i):
        return (r[i].strip() if (i is not None and len(r) > i) else "")

    data, problems = [], []
    print(f"tab: {args.tab} | fullName col: D")
    print("=" * 78)
    for item in plan:
        no = str(item["no"]).strip()
        lblc = item["label_contains"]
        api = item["api"]
        # find the grid row whose col-A No. == no AND label contains lblc
        match = None
        for i in range(h + 1, len(grid)):
            r = grid[i]
            a = (r[0].strip() if r else "")
            if a.startswith("END["):
                break
            if a == no and lblc in g(r, c_lbl).replace("　", " "):
                match = (i, r)
                break
        if not match:
            problems.append(f"No.{no} '{lblc}': ROW NOT FOUND")
            continue
        i, r = match
        cur = g(r, c_api)
        dele = g(r, c_del)
        wip = g(r, c_wip)
        row1 = i + 1
        note = ""
        if dele.upper() in ("TRUE", "X") and not args.allow_isdelete:
            problems.append(f"D{row1} No.{no} '{lblc}': IsDelete=TRUE — refusing to write")
            continue
        if wip.upper() in ("TRUE", "X"):
            problems.append(f"D{row1} No.{no} '{lblc}': WIP — refusing to write")
            continue
        if cur and cur != api:
            problems.append(f"D{row1} No.{no} '{lblc}': cell NOT blank (has '{cur}') — refusing to clobber")
            continue
        if cur == api:
            note = "  (already set — no-op)"
        print(f"  D{row1:<4} No.{no:<4} {g(r,c_lbl):<24} (blank) -> {api}{note}")
        if cur != api:
            data.append({"range": f"'{args.tab}'!D{row1}", "values": [[api]]})

    if problems:
        print("\nPROBLEMS (not written):")
        for p in problems:
            print("  ✗", p)
        print("\nABORT — resolve problems above before applying.")
        return 1

    print("=" * 78)
    print(f"cells to write: {len(data)}")
    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply to write.")
        return 0
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=args.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"WROTE: {resp.get('totalUpdatedCells')} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
