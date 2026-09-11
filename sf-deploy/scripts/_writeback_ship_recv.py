#!/usr/bin/env python3
"""Gated write-back of the Ship/Recv fixes to the Google Sheet.

Writes, matched to LIVE rows:
  * Field API Name (col D)      — for every blank-API row we named (ALL scope)
  * Relationship Name (col X)   — only for fields DEPLOYED this session
  * AH comment (FreeColumnGDC1) — on ReceivingDetail dup row (skipped 2nd LineItem)

Default = DRY-RUN preview (cell: old -> new). --apply writes (gated by user).
NotifyParty casing: org has TI_Fnt_NoTifyParty__c, so we write THAT (not our
TI_Fnt_NotifyParty__c) to keep the sheet == org.
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import defaultdict, deque

sys.path.insert(0, "scripts")
from fetch_sheet import (get_sheets_service, find_header_row, build_col_map,
                         find_helper_cols, norm, GRAY_GUARD_SUBSTR, is_field_list_end, WIP_TRUE)

SID = "1_TaxDe-Qxl8BAUmuZc01vUoxpBEPxJ4Opx4tEe8ulNQ"
TABS = {"Shipping": "TI_Fnt_Shipping__c", "ShippingDetail": "TI_Fnt_ShippingDetail__c",
        "Receiving": "TI_Fnt_Receiving__c", "ReceivingDetail": "TI_Fnt_ReceivingDetail__c"}
ARTIFACT = ".build/ship_recv_deploy.json"
DEPLOYED = ".build/deployed_session.txt"
AH_IDX = 33  # column AH FreeColumnGDC1
# org actual casing overrides (sheet must match org)
API_OVERRIDE = {("TI_Fnt_Shipping__c", "TI_Fnt_NotifyParty__c"): "TI_Fnt_NoTifyParty__c"}


def a1col(i0):
    s = ""
    while True:
        s = chr(65 + i0 % 26) + s
        i0 = i0 // 26 - 1
        if i0 < 0:
            break
    return s


def key(o, label, ref):
    return (o, norm(label), norm(ref))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--tab", default="", help="restrict to ONE tab (object-by-object)")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    tabs = {args.tab: TABS[args.tab]} if args.tab else TABS
    if args.tab and args.tab not in TABS:
        print(f"unknown tab {args.tab!r}; choose from {list(TABS)}"); return 2

    art = json.load(open(ARTIFACT, encoding="utf-8"))
    # index artifact rows by (object,label,ref) -> deque of {api,rel}
    idx = defaultdict(deque)
    for r in art:
        if r.get("_type") == "object_meta":
            continue
        o = norm(r.get("Object API Name"))
        api = norm(r.get("Field API Name"))
        if not api.endswith("__c"):
            continue
        ref = norm(r.get("Reference To")) or norm(r.get("Type Specific Value"))
        idx[key(o, r.get("Field Label"), ref)].append(
            {"api": api, "rel": norm(r.get("Relationship Name"))})

    deployed = {ln.strip().lower() for ln in open(DEPLOYED) if ln.strip()}  # obj.field lower

    svc = get_sheets_service()
    data = []           # batch value updates
    preview = []        # (tab, cell, kind, old, new)
    ORG = "ERPDEV01"
    PARKED_REF = "TI_Fnt_PaymentTerms__c"
    DUP_SKIP = {("ReceivingDetail", 31)}   # skipped duplicate LineItem — dedicated AH note only

    for tab, obj in tabs.items():
        grid = svc.spreadsheets().values().get(
            spreadsheetId=SID, range=f"'{tab}'",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        h = find_header_row(grid)
        cm = build_col_map(grid[h])
        helper = find_helper_cols(grid[h])
        col = {v: k for k, v in cm.items()}
        c_api = col.get("Field API Name")
        c_lab = col.get("Field Label")
        c_ref = col.get("Type Specific Value")
        c_rel = col.get("Relationship Name")
        c_ah = col.get("FreeColumnGDC1", AH_IDX)   # AH by header, fallback idx 33
        row_fix = {}   # rown -> list[str] note parts (one AH note per touched row)

        def cell(cells, i):
            return norm(cells[i]) if (i is not None and i < len(cells)) else ""

        def hcell(cells, k):
            i = helper[k]
            return norm(cells[i]) if i < len(cells) else ""

        for gi in range(h + 1, len(grid)):
            cells = grid[gi]
            if is_field_list_end(cells):
                break
            label = cell(cells, c_lab)
            api_live = cell(cells, c_api)
            ref = cell(cells, c_ref)
            typ = cell(cells, col.get("Data Type"))
            rel_live = cell(cells, c_rel)
            if not (api_live or (label and typ)):
                continue
            if hcell(cells, "wip").lower() in WIP_TRUE:
                continue
            rown = gi + 1
            if (tab, rown) in DUP_SKIP:
                continue   # dedicated dup AH note handles this row

            # PARKED rows (referenceTo not in org) — no value write, but AH-note it
            if ref == PARKED_REF:
                row_fix.setdefault(rown, []).append(
                    f"parked — referenceTo {PARKED_REF} does not exist in {ORG}; not deployed")
                continue

            # resolve the field's final API (from live, else artifact match)
            final_api = api_live
            if not final_api:
                q = idx.get(key(obj, label, ref))
                if q:
                    final_api = q.popleft()["api"]
                    over = API_OVERRIDE.get((obj, final_api))
                    if over:
                        final_api = over  # use org's actual casing
                    # API-name write (blank -> new)
                    data.append({"range": f"'{tab}'!{a1col(c_api)}{rown}", "values": [[final_api]]})
                    preview.append((tab, f"{a1col(c_api)}{rown}", "API ", "", final_api, label))
                    depl = f"{obj}.{final_api}".lower() in deployed
                    row_fix.setdefault(rown, []).append(
                        f"filled Field API Name = {final_api}"
                        + ("" if depl else " (already existed in org)"))
            # relationshipName write — only for fields DEPLOYED this session, blank cell
            if final_api and typ == "Lookup" and not rel_live:
                if f"{obj}.{final_api}".lower() in deployed:
                    # relname from artifact — OBJECT-SCOPED (same field api can live
                    # on several objects with different relnames, e.g. StorageLocation)
                    rel = ""
                    for r in art:
                        if norm(r.get("Object API Name")) == obj and \
                           norm(r.get("Field API Name")).lower() == final_api.lower():
                            rel = norm(r.get("Relationship Name")); break
                    if rel and c_rel is not None:
                        data.append({"range": f"'{tab}'!{a1col(c_rel)}{rown}", "values": [[rel]]})
                        preview.append((tab, f"{a1col(c_rel)}{rown}", "REL ", "", rel, label))
                        row_fix.setdefault(rown, []).append(f"filled relationshipName = {rel}")

        # emit ONE AH [FIX] note per touched row on THIS tab (append to existing AH)
        for rown, parts in sorted(row_fix.items()):
            gi = rown - 1
            ah_old = norm(grid[gi][c_ah]) if len(grid) > gi and len(grid[gi]) > c_ah else ""
            note = f"[FIX {today}] " + "; ".join(parts) + f"; deployed to {ORG}." \
                if any("filled Field API Name" in p or "relationshipName" in p for p in parts) \
                else f"[FIX {today}] " + "; ".join(parts) + "."
            ah_new = (ah_old + "\n" + note) if ah_old else note
            data.append({"range": f"'{tab}'!{a1col(c_ah)}{rown}", "values": [[ah_new]]})
            lbl = norm(grid[gi][c_lab]) if len(grid) > gi and len(grid[gi]) > c_lab else ""
            preview.append((tab, f"{a1col(c_ah)}{rown}", "AH  ", ah_old, note, lbl))

    # AH comment on ReceivingDetail duplicate 2nd LineItem row (row 31)
    if "ReceivingDetail" in tabs:
        dup_note = f"[FIX {today}] duplicate of row 18 (TI_Fnt_LineItem__c) — skipped; deployed once."
        grid = svc.spreadsheets().values().get(
            spreadsheetId=SID, range="'ReceivingDetail'",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        ah_old = norm(grid[30][AH_IDX]) if len(grid) > 30 and len(grid[30]) > AH_IDX else ""
        ah_new = (ah_old + "\n" + dup_note) if ah_old else dup_note
        data.append({"range": f"'ReceivingDetail'!{a1col(AH_IDX)}31", "values": [[ah_new]]})
        preview.append(("ReceivingDetail", f"{a1col(AH_IDX)}31", "AH  ", ah_old, dup_note, "明細 (dup)"))

    # print preview grouped
    print("=" * 100)
    print(f"  SHEET WRITE-BACK PREVIEW  ({len(data)} cells)   [{'APPLY' if args.apply else 'DRY-RUN'}]")
    print("=" * 100)
    by = defaultdict(list)
    for t, c, k, o, n, lab in preview:
        by[(t, k.strip())].append((c, o, n, lab))
    for (t, k), items in sorted(by.items()):
        print(f"\n--- {t}  [{k}]  ({len(items)}) ---")
        for c, o, n, lab in items:
            print(f"   {c:6s} {lab:26s} : {o!r:20s} -> {n!r}")
    print("\n" + "=" * 100)
    print(f"  TOTAL cells: {len(data)}  (API + REL + AH)")

    if not args.apply:
        print("  DRY-RUN — nothing written. Re-run with --apply after confirmation.")
        return 0
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SID, body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"  WROTE {resp.get('totalUpdatedCells')} cells across {resp.get('totalUpdatedRanges')} ranges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
