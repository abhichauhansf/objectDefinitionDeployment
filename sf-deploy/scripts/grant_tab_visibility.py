#!/usr/bin/env python3
"""
grant_tab_visibility.py — MANDATORY pipeline step after a CustomTab is created.

Creating a CustomTab (and adding it to an app) does NOT make it visible to users:
the tab defaults to OFF on every profile/permission set until its visibility is
explicitly granted. This helper grants tab visibility (`DefaultOn`) for one or
more object tabs on a permission set (default `SalesFrontAdmin`) via the Tooling
API `PermissionSetTabSetting` object — CLI-free (reuses the SOAP session token in
.build/orgauth.json), idempotent (skips tabs already set), and verifies live.

Valid Tooling `PermissionSetTabSetting.Visibility` values are `DefaultOn`,
`DefaultOff`, `Hidden` (NOT the metadata words `Visible`/`Available`, which the
restricted picklist rejects). We use `DefaultOn` so the tab shows for users who
hold the permission set.

Usage:
  python scripts/grant_tab_visibility.py --tabs TI_Fnt_Foo__c,TI_Fnt_Bar__c
  python scripts/grant_tab_visibility.py --tabs TI_Fnt_Foo__c --permset SalesFrontAdmin
  python scripts/grant_tab_visibility.py --tabs TI_Fnt_Foo__c --check   # report only

Exit code: 0 = every requested tab is DefaultOn afterwards (or already was);
non-zero = at least one tab could not be granted / verified.
"""
from __future__ import annotations
import argparse, json, sys, urllib.request, urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit_lib as A  # noqa: E402


def _tooling_insert(inst: str, ver: str, tok: str, parent: str, name: str) -> tuple[bool, str]:
    url = f"{inst}/services/data/v{ver}/tooling/sobjects/PermissionSetTabSetting"
    body = json.dumps({"ParentId": parent, "Name": name, "Visibility": "DefaultOn"}).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {tok}",
                                          "Content-Type": "application/json"})
    try:
        res = json.load(urllib.request.urlopen(req, timeout=60))
        return bool(res.get("success")), str(res.get("id") or "")
    except urllib.error.HTTPError as e:
        return False, e.read().decode()[:300]


def main() -> int:
    ap = argparse.ArgumentParser(description="Grant object-tab visibility on a permission set")
    ap.add_argument("--tabs", required=True, help="comma-separated object/tab API names")
    ap.add_argument("--permset", default="SalesFrontAdmin", help="permission set Name (default SalesFrontAdmin)")
    ap.add_argument("--token-file", default=".build/orgauth.json")
    ap.add_argument("--check", action="store_true", help="report current visibility only; do not write")
    args = ap.parse_args()

    a = A.load_auth(args.token_file)
    tok, inst, ver = a["accessToken"], a["instanceUrl"].rstrip("/"), str(a["apiVersion"])
    tabs = [t.strip() for t in args.tabs.split(",") if t.strip()]

    ps_rows = A.rest_query(f"SELECT Id FROM PermissionSet WHERE Name='{args.permset}'", a)
    if not ps_rows:
        print(f"❌ permission set '{args.permset}' not found")
        return 2
    ps = ps_rows[0]["Id"]

    existing = {r["Name"]: r["Visibility"]
                for r in A.rest_query(
                    f"SELECT Name,Visibility FROM PermissionSetTabSetting WHERE ParentId='{ps}'", a)}

    ok_all = True
    for t in tabs:
        cur = existing.get(t)
        if cur == "DefaultOn":
            print(f"  ✓ {t}: already DefaultOn")
            continue
        if args.check:
            print(f"  ⚠️  {t}: {cur or 'NOT SET'} (would grant DefaultOn)")
            ok_all = False
            continue
        ok, info = _tooling_insert(inst, ver, tok, ps, t)
        if ok:
            print(f"  ✅ {t}: granted DefaultOn ({info})")
        else:
            # a pre-existing non-DefaultOn setting must be PATCHed, not inserted
            print(f"  … insert failed ({info[:120]}); trying update")
            rows = A.rest_query(
                f"SELECT Id FROM PermissionSetTabSetting WHERE ParentId='{ps}' AND Name='{t}'", a, tooling=True)
            if rows:
                url = f"{inst}/services/data/v{ver}/tooling/sobjects/PermissionSetTabSetting/{rows[0]['Id']}"
                req = urllib.request.Request(url, data=json.dumps({"Visibility": "DefaultOn"}).encode(),
                                             method="PATCH",
                                             headers={"Authorization": f"Bearer {tok}",
                                                      "Content-Type": "application/json"})
                try:
                    urllib.request.urlopen(req, timeout=60)
                    print(f"  ✅ {t}: updated to DefaultOn")
                except urllib.error.HTTPError as e:
                    print(f"  ❌ {t}: {e.read().decode()[:200]}")
                    ok_all = False
            else:
                print(f"  ❌ {t}: could not grant")
                ok_all = False

    # verify live
    final = {r["Name"]: r["Visibility"]
             for r in A.rest_query(
                 f"SELECT Name,Visibility FROM PermissionSetTabSetting WHERE ParentId='{ps}'", a)}
    missing = [t for t in tabs if final.get(t) != "DefaultOn"]
    if missing and not args.check:
        print(f"❌ still not DefaultOn: {missing}")
        return 1
    if not missing:
        print(f"✅ all {len(tabs)} tab(s) DefaultOn on {args.permset}")
    return 0 if (ok_all or (not missing and not args.check)) else 1


if __name__ == "__main__":
    sys.exit(main())
