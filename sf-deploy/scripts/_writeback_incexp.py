#!/usr/bin/env python3
"""Gated write-back for the IncExp/DealDetail batch (Deal set aside).

Per row touched (all currently BLANK cells only; AH appends):
  * Relationship Name (col X)  — for the 17 DEPLOYED lookups
  * AH [FIX] note              — one per deployed row
  * AH [VALIDATION] + Col-A highlight — the parked GoodsMovementStockAdj row

Default = DRY-RUN. --apply writes (gated by user confirmation).
"""
from __future__ import annotations
import argparse, datetime, sys
from collections import defaultdict
sys.path.insert(0, "scripts")
from fetch_sheet import (get_sheets_service, find_header_row, build_col_map,
                         find_helper_cols, norm, GRAY_GUARD_SUBSTR, is_field_list_end, WIP_TRUE)

SID = "1_TaxDe-Qxl8BAUmuZc01vUoxpBEPxJ4Opx4tEe8ulNQ"
ORG = "ERPDEV01"
AH_IDX = 33
TABS = {  # tab -> (object api, object code for relname)
    "DealDetail": ("TI_Fnt_DealDetail__c", "DealDtl"),
    "IncidentalExpenses": ("TI_Fnt_IncidentalExpenses__c", "IncExp"),
    "IncidentalExpensesDetail": ("TI_Fnt_IncidentalExpensesDetail__c", "IncExpDtl"),
    "StandaloneIncidentalExpenses": ("TI_Fnt_StandaloneIncidentalExpenses__c", "StdIncExp"),
}
DEPLOYED = {
    "TI_Fnt_DealDetail__c": ["TI_Fnt_ConditionUnit__c","TI_Fnt_QuantityUnit__c","TI_Fnt_SalesUnit__c","TI_Fnt_StorageLocation__c","TI_Fnt_Unit__c"],
    "TI_Fnt_IncidentalExpenses__c": ["TI_Fnt_Account__c","TI_Fnt_BobbinShipTo__c","TI_Fnt_Condition__c","TI_Fnt_QuantityUnit__c"],
    "TI_Fnt_IncidentalExpensesDetail__c": ["TI_Fnt_Account__c","TI_Fnt_Condition__c","TI_Fnt_QuantityUnit__c"],
    "TI_Fnt_StandaloneIncidentalExpenses__c": ["TI_Fnt_Account__c","TI_Fnt_BobbinShipTo__c","TI_Fnt_Condition__c","TI_Fnt_DepartmentCode__c","TI_Fnt_QuantityUnit__c"],
}
PARKED = {"TI_Fnt_IncidentalExpenses__c": "TI_Fnt_GoodsMovementStockAdj__c"}
PARK_REASON = "referenceTo '<API名未定(GDC付与)>：在庫移動・在庫調整' is a placeholder, not a real object — parked, not deployed"


def a1(i0):
    s = ""
    while True:
        s = chr(65 + i0 % 26) + s; i0 = i0 // 26 - 1
        if i0 < 0:
            break
    return s


def relname(code, api):
    return f"{code}_{api[len('TI_Fnt_'):-len('__c')]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    today = datetime.date.today().isoformat()
    svc = get_sheets_service()
    data, preview = [], []
    for tab, (obj, code) in TABS.items():
        grid = svc.spreadsheets().values().get(spreadsheetId=SID, range=f"'{tab}'",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        h = find_header_row(grid); cm = build_col_map(grid[h]); col = {v: k for k, v in cm.items()}
        helper = find_helper_cols(grid[h])
        c_api = col.get("Field API Name"); c_lab = col.get("Field Label"); c_rel = col.get("Relationship Name")
        c_ah = col.get("FreeColumnGDC1", AH_IDX)
        def g(c, i): return norm(c[i]) if (i is not None and i < len(c)) else ""
        dep = set(DEPLOYED.get(obj, [])); park = PARKED.get(obj)
        for gi in range(h + 1, len(grid)):
            cells = grid[gi]
            if is_field_list_end(cells):
                break
            api = g(cells, c_api); lab = g(cells, c_lab); rown = gi + 1
            if not api:
                continue
            hi = helper.get("wip"); wip = norm(cells[hi]) if (hi is not None and hi < len(cells)) else ""
            if wip.lower() in WIP_TRUE:
                continue
            ah_old = g(cells, c_ah)
            if api in dep:
                rel = relname(code, api)
                if c_rel is not None and not g(cells, c_rel):
                    data.append({"range": f"'{tab}'!{a1(c_rel)}{rown}", "values": [[rel]]})
                    preview.append((tab, f"{a1(c_rel)}{rown}", "REL", "", rel, lab))
                note = f"[FIX {today}] filled relationshipName = {rel}; deployed to {ORG}."
                ah_new = (ah_old + "\n" + note) if ah_old else note
                data.append({"range": f"'{tab}'!{a1(c_ah)}{rown}", "values": [[ah_new]]})
                preview.append((tab, f"{a1(c_ah)}{rown}", "AH", ah_old, note, lab))
            elif api == park:
                note = f"[VALIDATION {today}] dependency.ref: {PARK_REASON}."
                ah_new = (ah_old + "\n" + note) if ah_old else note
                data.append({"range": f"'{tab}'!{a1(c_ah)}{rown}", "values": [[ah_new]]})
                preview.append((tab, f"{a1(c_ah)}{rown}", "AH-PARK", ah_old, note, lab))
                preview.append((tab, f"A{rown}", "HILITE", "", "yellow (Col A only)", lab))

    print("=" * 96)
    print(f"  INCEXP/DEALDTL WRITE-BACK PREVIEW  ({len(data)} value cells)  [{'APPLY' if args.apply else 'DRY-RUN'}]")
    print("=" * 96)
    by = defaultdict(list)
    for t, c, k, o, n, lab in preview:
        by[(t, k)].append((c, o, n, lab))
    for (t, k), items in sorted(by.items()):
        print(f"\n--- {t} [{k}] ({len(items)}) ---")
        for c, o, n, lab in items:
            print(f"   {c:6s} {lab:20s} : {o!r:14s} -> {n!r}")
    print(f"\n  TOTAL value cells: {len(data)}  (+1 Col-A highlight applied separately)")
    if not args.apply:
        print("  DRY-RUN — nothing written.")
        return 0
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=SID, body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"  WROTE {resp.get('totalUpdatedCells')} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
