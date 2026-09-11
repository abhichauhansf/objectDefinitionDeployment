#!/usr/bin/env python3
"""
fill_relationship_name.py — Fill the relationshipName column (X) for Lookup /
Master-Detail fields that are missing it. Gated by the user
(sheet-write-confirmation).

relationshipName is derived as the field API name minus the trailing '__c'.
That bare name is NOT globally unique on a SHARED parent: the child-relationship
name must be unique across ALL child objects that look up the same parent, so
two different objects each carrying (say) TI_Fnt_SalesDeliveryInCharge__c → User
collide ("There is already a Child Relationship named … on User"). To prevent
that, when the parent is a SHARED standard object (User/Account/Contact/…), the
name is OBJECT-SCOPED with a short acronym of the owning object
(e.g. ExportControlClassification → 'ECC_SalesDeliveryInCharge'). Only rows that
are Lookup/MD with a currently-empty relationshipName are touched.

  python scripts/fill_relationship_name.py --spreadsheet-id <ID> \
      --tab "成約:Sales_Deal" [--apply]
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


# Standard parents that accumulate child relationships from MANY objects, so a
# bare field-derived relationshipName is collision-prone and must be scoped by
# the owning object.
SHARED_PARENTS = frozenset({
    "User", "Account", "Contact", "Lead", "Group", "Case", "Opportunity",
    "Product2", "Pricebook2", "Asset", "Campaign", "Order", "Contract",
})


def _obj_acronym(obj_api: str) -> str:
    """Short token for the owning object: acronym of capitals if >=3, else a
    trimmed component. e.g. ExportControlClassification -> 'ECC'."""
    comp = obj_api
    if comp.startswith("TI_Fnt_"):
        comp = comp[len("TI_Fnt_"):]
    if comp.endswith("__c"):
        comp = comp[:-3]
    caps = "".join(ch for ch in comp if ch.isupper())
    return caps if len(caps) >= 3 else comp[:12]


def rel_name(api: str, obj_api: str = "", parent: str = "") -> str:
    base = api[:-3] if api.endswith("__c") else api
    if parent in SHARED_PARENTS and obj_api:
        # object-scope: strip our namespace from the field component, prefix acronym
        comp = base[len("TI_Fnt_"):] if base.startswith("TI_Fnt_") else base
        scoped = f"{_obj_acronym(obj_api)}_{comp}"
        return scoped[:40]
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--temp", default="temp_updates.json")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    tu = json.load(open(args.temp, encoding="utf-8"))
    obj_api = next((r.get("Object API Name", "") for r in tu
                    if r.get("_type") == "object_meta"), "")
    # Custom lookups only — never touch standard fields (OwnerId, CreatedById…).
    lk = {r.get("Field API Name") for r in tu
          if r.get("_type") != "object_meta"
          and r.get("Data Type") in ("Lookup", "MasterDetail")
          and str(r.get("Field API Name", "")).endswith("__c")}
    # map field api -> parent (referenceTo) so we can object-scope shared parents
    parent_of = {r.get("Field API Name"): (r.get("Type Specific Value") or "").strip()
                 for r in tu if r.get("_type") != "object_meta"}

    svc = get_write_service()
    grid = svc.spreadsheets().values().get(
        spreadsheetId=args.spreadsheet_id, range=f"'{args.tab}'",
        valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    h = find_header_row(grid)
    hdr = [norm(c).lower() for c in grid[h]]
    dcol = hdr.index("fullname")
    rncol = hdr.index("relationshipname")
    rncol_letter = col_letter(rncol)

    targets = []
    for i in range(h + 1, len(grid)):
        cells = [norm(c) for c in grid[i]]
        api = cells[dcol] if dcol < len(cells) else ""
        cur = cells[rncol] if rncol < len(cells) else ""
        if api in lk and not cur:
            targets.append((i + 1, api, rel_name(api, obj_api, parent_of.get(api, ""))))

    print(f"tab: {args.tab} | relationshipName col: {rncol_letter}")
    print(f"lookup/MD rows to fill ({len(targets)}):")
    for r, api, rn in targets:
        print(f"  row {r:>3} | {api:<34} -> {rn}")

    if not args.apply:
        print("DRY RUN — nothing written. Re-run with --apply.")
        return 0

    data = [{"range": f"'{args.tab}'!{rncol_letter}{r}", "values": [[rn]]}
            for r, _, rn in targets]
    resp = svc.spreadsheets().values().batchUpdate(
        spreadsheetId=args.spreadsheet_id,
        body={"valueInputOption": "RAW", "data": data}).execute()
    print(f"WROTE relationshipName into {resp.get('totalUpdatedCells')} cells.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
