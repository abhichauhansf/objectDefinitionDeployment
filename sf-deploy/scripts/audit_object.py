#!/usr/bin/env python3
"""audit_object.py — random spot-audit of a deployed object.

For N randomly-picked custom fields it checks, LIVE:
  (1) attribute parity  sheet vs org  (type/formula/referenceTo/length/precision/
      scale/required/unique/externalId/picklist)
  (2) page-layout placement + section  (org-default FlexiPage)
  (3) FLS on the permission set (default SalesFrontAdmin)
Plus object-level: CustomTab existence + tab visibility (PermissionSetTabSetting).

Usage:
  python scripts/audit_object.py --object TI_Fnt_Deal__c --tab Deal \
      --spreadsheet-id <ID> --n 10 [--seed 7] [--flexipage <name>] \
      [--perm-set SalesFrontAdmin] [--out .build/audit_deal.json]
Sheet rows are read live (fetch_sheet.py). Org read via audit_lib (session token).
"""
from __future__ import annotations
import argparse, json, os, random, re, subprocess, sys
from pathlib import Path
sys.path.insert(0, "scripts")
import audit_lib as A
from attr_drift import map_dt

SID_DEFAULT = "1_TaxDe-Qxl8BAUmuZc01vUoxpBEPxJ4Opx4tEe8ulNQ"


def _num(v):
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except Exception:
        return s


def _sbool(v):
    return str(v or "").strip().lower() in ("true", "x", "yes", "1")


def _obool(v):
    return str(v or "").strip().lower() == "true"


def compare_field(r: dict, om: dict) -> list[str]:
    """Return list of mismatch strings (empty => in sync). Mirrors attr_drift."""
    dt = (r.get("Data Type") or "").strip()
    tsv = (r.get("Type Specific Value") or "").strip()
    exp_base, exp_formula, mapped = map_dt(dt)
    ot = om.get("type", "")
    ohf = bool(om.get("formula"))
    oref = om.get("referenceTo", "")
    why = []
    if not mapped:
        why.append(f"unmapped sheet type '{dt}' (org={ot})")
    if ot != exp_base:
        why.append(f"type: sheet={exp_base} org={ot}")
    if exp_formula and not ohf:
        why.append("sheet=Formula, org=NOT formula")
    if not exp_formula and ohf:
        why.append("sheet=non-formula, org=HAS formula")
    if exp_base in ("Lookup", "MasterDetail") and tsv and oref and oref != tsv:
        why.append(f"referenceTo: sheet={tsv} org={oref}")
    if exp_base in ("Picklist", "MultiselectPicklist") and om.get("_picklist"):
        sv = [p.split(":")[-1].strip() for p in re.split(r"[;\n]", tsv) if p.strip()]
        if sv and set(sv) != set(om["_picklist"]):
            why.append(f"picklist: {len(sv)} sheet / {len(om['_picklist'])} org")
    if exp_base in ("Text", "TextArea", "LongTextArea", "EncryptedText", "Html") and not exp_formula:
        sl, ol = _num(r.get("Length")), _num(om.get("length"))
        if sl is not None and ol is not None and sl != ol:
            why.append(f"length: sheet={sl} org={ol}")
    if exp_base in ("Number", "Currency", "Percent") and not exp_formula:
        sp, op = _num(r.get("Precision")), _num(om.get("precision"))
        ssc, osc = _num(r.get("Scale")), _num(om.get("scale"))
        if sp is not None and op is not None and sp != op:
            why.append(f"precision: sheet={sp} org={op}")
        if ssc is not None and osc is not None and ssc != osc:
            why.append(f"scale: sheet={ssc} org={osc}")
    if exp_base not in ("MasterDetail", "Summary", "AutoNumber") and not exp_formula:
        sreq = str(r.get("Required") or "").strip()
        if sreq and _sbool(sreq) != _obool(om.get("required")):
            why.append(f"required: sheet={_sbool(sreq)} org={_obool(om.get('required'))}")
    for scol, ocol in (("Unique", "unique"), ("External ID", "externalId")):
        sv = str(r.get(scol) or "").strip()
        if sv and _sbool(sv) != _obool(om.get(ocol)):
            why.append(f"{ocol}: sheet={_sbool(sv)} org={_obool(om.get(ocol))}")
    return why


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--object", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--spreadsheet-id", default=SID_DEFAULT)
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--flexipage", default="")
    ap.add_argument("--perm-set", default="SalesFrontAdmin")
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    auth = A.load_auth()

    # 1) sheet rows (live)
    rowfile = f".build/audit_rows_{args.object}.json"
    subprocess.run(["python3", "scripts/fetch_sheet.py", "--spreadsheet-id", args.spreadsheet_id,
                    "--tabs", args.tab, "--out", rowfile], check=True,
                   stdout=subprocess.DEVNULL)
    rows = json.load(open(rowfile))
    sheet = {}
    for r in rows:
        api = (r.get("Field API Name") or "").strip()
        if api.endswith("__c"):
            sheet[api] = r

    # 2) org meta (live)
    m = A.read_object_meta(args.object, auth)
    if not m:
        print(f"❌ {args.object} not in org"); return 1
    org_fields = m["fields"]

    # 3) flexipage (org-default) placement
    fp_name = args.flexipage or (m["defaultPages"].get("Large") or m["defaultPages"].get("Small") or "")
    fp = A.read_flexipage(fp_name, auth) if fp_name else None
    fp_fields = fp["fields"] if fp else {}

    # 4) FLS on the permission set
    fls = {}
    fp_rows = A.rest_query(
        f"SELECT Field,PermissionsRead,PermissionsEdit FROM FieldPermissions "
        f"WHERE Parent.Name='{args.perm_set}' AND SobjectType='{args.object}'", auth)
    for r in fp_rows:
        fls[r["Field"].split(".", 1)[1]] = (r["PermissionsRead"], r["PermissionsEdit"])

    # 5) tab + visibility
    tab = A.read_customtab(args.object, auth)
    tabvis = A.rest_query(
        f"SELECT Visibility,Parent.Name,Parent.Type FROM PermissionSetTabSetting "
        f"WHERE Name='{args.object}'", auth, tooling=True)

    # pick N fields present in BOTH sheet and org
    common = sorted(set(sheet) & set(org_fields))
    random.seed(args.seed)
    picks = random.sample(common, min(args.n, len(common)))
    picks.sort()

    report = {"object": args.object, "flexipage": fp_name or None,
              "tab": {"exists": bool(tab),
                      "visibility": [(v.get("Visibility"), v.get("Parent", {}).get("Name")) for v in tabvis]},
              "counts": {"sheet_custom": len(sheet), "org_custom": len([f for f in org_fields if f.endswith('__c')]),
                         "common": len(common)},
              "fields": []}

    print("=" * 100)
    print(f"AUDIT  {args.object}   (sheet_custom={len(sheet)}  org_custom={report['counts']['org_custom']}  common={len(common)})")
    print(f"  org-default FlexiPage : {fp_name or 'NONE (system default)'}")
    print(f"  CustomTab            : {'YES' if tab else 'NO'}")
    if tabvis:
        for v in tabvis:
            print(f"  Tab visibility       : {v.get('Visibility')} on {v.get('Parent',{}).get('Name')} ({v.get('Parent',{}).get('Type')})")
    else:
        print(f"  Tab visibility       : (no PermissionSetTabSetting found)")
    print("-" * 100)
    print(f"{'FIELD':<42}{'ATTR':<8}{'LAYOUT/section':<34}{'FLS(R/E)'}")
    print("-" * 100)
    for f in picks:
        r, om = sheet[f], org_fields[f]
        why = compare_field(r, om)
        attr = "OK" if not why else "DRIFT"
        in_fp = f in fp_fields
        sect = fp_fields.get(f, "")
        sheet_sect = (r.get("Section") or "").strip()
        if fp_name:
            layout = ("on: " + (sect or "?")) if in_fp else "NOT on page"
        else:
            layout = f"(no page; sheet sect='{sheet_sect}')" if sheet_sect else "(no page)"
        if f in fls:
            rd, ed = fls[f]
            flss = "R+E" if ed else ("R" if rd else "none")
        else:
            # required / master-detail are implicitly visible (no FLS row expected)
            req = _obool(om.get("required")); mdt = om.get("type") == "MasterDetail"
            flss = "implicit" if (req or mdt) else "MISSING"
        print(f"{f:<42}{attr:<8}{layout:<34}{flss}")
        if why:
            print(f"    ↳ drift: {'; '.join(why)}")
        report["fields"].append({"field": f, "attr": attr, "drift": why,
                                 "on_page": in_fp, "section": sect,
                                 "sheet_section": sheet_sect, "fls": flss})
    print("=" * 100)
    out = args.out or f".build/audit_{args.object}.json"
    json.dump(report, open(out, "w"), ensure_ascii=False, indent=2)
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
