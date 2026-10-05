#!/usr/bin/env python3
"""
build_destructive.py — Build a Salesforce destructive-delete package from the
sheet's IsDelete (column AD) flag.

For every field row whose `IsDelete` (AD) = TRUE (and which is NOT WIP), this
emits:
  * manifest/destructiveChanges.xml  — CustomField members `<Obj>__c.<Field>__c`
  * manifest/destructive_package.xml — an EMPTY package (version only), required
    alongside destructiveChanges by the Metadata API.

The actual delete is performed by `deploy.py`:
    python scripts/deploy.py --start \
        --pre-destructive manifest/destructiveChanges.xml \
        --package manifest/destructive_package.xml \
        --target-org "<ORG>" --test-level NoTestRun --skip-validation-gate
That real deploy is DESTRUCTIVE and requires explicit review plus `--start`.

Safety:
  * Reads the sheet LIVE (never a cached snapshot).
  * Skips WIP rows (they are never touched).
  * Only includes rows with a valid custom Field API Name (ends `__c`).
  * With --target-org, verifies each field EXISTS in the org (Tooling API,
    FLS-independent); fields already absent are reported as no-ops and dropped
    from the delete set (deleting a non-existent field would error).
  * Default is a DRY RUN (prints the delete set, writes the manifests). The delete
    only happens when you subsequently run deploy.py --start (gated).

Usage:
  python scripts/build_destructive.py --spreadsheet-id <ID> --tabs "Shipping,Receiving" \
      [--target-org ERPDEV01] [--out-dir manifest] [--api-version 66.0]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "scripts")
from fetch_sheet import (  # noqa: E402
    get_sheets_service, find_header_row, find_helper_cols, build_col_map,
    norm, GRAY_GUARD_SUBSTR, is_field_list_end, WIP_TRUE, DELETE_TRUE,
)
from sheet_config import add_spreadsheet_id_arg  # noqa: E402

API_NAME_OK = __import__("re").compile(r"^[A-Za-z][A-Za-z0-9_]*__c$")


def scan_delete_rows(svc, sid: str, tabs: list[str]) -> list[dict]:
    """Return [{object, field, tab, row}] for every IsDelete=TRUE, non-WIP row."""
    deletes: list[dict] = []
    for tab in tabs:
        grid = svc.spreadsheets().values().get(
            spreadsheetId=sid, range=f"'{tab}'",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        hidx = find_header_row(grid)
        if hidx is None:
            print(f"  ⚠️  {tab}: no field header row — skipped.")
            continue
        col_map = build_col_map(grid[hidx])
        helper = find_helper_cols(grid[hidx])
        # object API from the object-meta header is not needed here; derive per row
        # via the fullName column + tab. We still need the object API name: read it
        # from the object header block the same way fetch_sheet does — but simplest
        # is to pull it from the fullName column's object. Use the sheet's object
        # meta by re-using fetch_sheet.parse_object_header indirectly is heavy; the
        # object API is constant per tab, so read it from the first meta occurrence.
        obj_api = _object_api(grid, hidx, col_map)
        full_idx = next((i for i, k in col_map.items() if k == "Field API Name"), None)
        n = 0
        for i in range(hidx + 1, len(grid)):
            cells = [norm(c) for c in grid[i]]
            if is_field_list_end(cells):
                break
            def at(key):
                idx = helper[key]
                return cells[idx].strip() if idx < len(cells) else ""
            if at("wip").lower() in WIP_TRUE:
                continue
            if at("isdelete").lower() not in DELETE_TRUE:
                continue
            fapi = (cells[full_idx].strip() if full_idx is not None and full_idx < len(cells) else "")
            if not fapi or not API_NAME_OK.match(fapi):
                print(f"  ⚠️  {tab} row {i+1}: IsDelete set but Field API Name "
                      f"'{fapi}' is blank/invalid — skipped (cannot delete).")
                continue
            deletes.append({"object": obj_api, "field": fapi, "tab": tab, "row": i + 1})
            n += 1
        print(f"  ✓ {tab}: {n} field(s) flagged IsDelete, object_api='{obj_api or '?'}'")
    return deletes


def _object_api(grid: list[list], hidx: int, col_map: dict) -> str:
    """Best-effort object API: the 'オブジェクト名' value in the meta header block."""
    for row in grid[:hidx]:
        cells = [norm(c) for c in row]
        if "オブジェクト名" in cells:
            k = cells.index("オブジェクト名")
            for c in cells[k + 1:]:
                if norm(c):
                    return norm(c)
    return ""


def org_has_field(target_org: str, obj: str, field: str) -> bool:
    dev = field[:-3] if field.endswith("__c") else field
    q = ("SELECT DeveloperName FROM CustomField WHERE "
         f"EntityDefinition.QualifiedApiName='{obj}' AND DeveloperName='{dev}'")
    try:
        out = subprocess.run(
            ["sf", "data", "query", "--use-tooling-api", "--target-org", target_org,
             "--json", "--query", q],
            capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}")
        return int(data.get("result", {}).get("totalSize", 0)) > 0
    except Exception as e:
        print(f"  ⚠️  existence check failed for {obj}.{field}: {e}")
        return True  # fail-open: keep it; the deploy will surface a real error


def flexipage_refs(target_org: str, deletes: list[dict]) -> dict[str, list[str]]:
    """Map '<Obj>__c.<Field>__c' -> [FlexiPage names placing that field].

    Salesforce REFUSES to delete a field that a Lightning record page still
    references ("The <label> custom field is used in a component on the <page>
    Lightning page"), so a delete set containing such a field fails as a whole.
    The field must be removed from the page (deploy the page), then deleted.
    """
    refs: dict[str, list[str]] = {}
    try:
        out = subprocess.run(
            ["sf", "org", "list", "metadata", "--metadata-type", "FlexiPage",
             "--target-org", target_org, "--json"],
            capture_output=True, text=True, timeout=120)
        pages = [p["fullName"] for p in json.loads(out.stdout or "{}").get("result", [])]
    except Exception as e:
        print(f"  ⚠️  FlexiPage scan skipped ({e}) — deletes may fail on page references.")
        return refs
    if not pages:
        return refs
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                ["sf", "project", "retrieve", "start", "--metadata",
                 ",".join(f"FlexiPage:{p}" for p in pages),
                 "--target-org", target_org, "--target-metadata-dir", td,
                 "--unzip", "--json"],
                capture_output=True, text=True, timeout=600)
        except Exception as e:
            print(f"  ⚠️  FlexiPage retrieve skipped ({e}).")
            return refs
        bodies = {p.name: p.read_text(encoding="utf-8", errors="replace")
                  for p in Path(td).rglob("*.flexipage*")}
    if not bodies:
        print(f"  ⚠️  FlexiPage retrieve returned no page files for {len(pages)} page(s) "
              "— cannot rule out page references; treating as BLOCKED.")
        return {f"{d['object']}.{d['field']}": ["<unverified: retrieve empty>"]
                for d in deletes}
    for d in deletes:
        key = f"{d['object']}.{d['field']}"
        hits = [name for name, body in bodies.items()
                if f"Record.{d['field']}" in body]
        if hits:
            refs[key] = hits
    return refs


def write_manifests(deletes: list[dict], out_dir: Path, api_version: str) -> tuple[Path, Path]:
    members = "\n".join(f"        <members>{d['object']}.{d['field']}</members>"
                        for d in deletes)
    destructive = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        '    <types>\n'
        f"{members}\n"
        '        <name>CustomField</name>\n'
        '    </types>\n'
        f'    <version>{api_version}</version>\n'
        '</Package>\n'
    )
    empty_pkg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Package xmlns="http://soap.sforce.com/2006/04/metadata">\n'
        f'    <version>{api_version}</version>\n'
        '</Package>\n'
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    dpath = out_dir / "destructiveChanges.xml"
    ppath = out_dir / "destructive_package.xml"
    dpath.write_text(destructive, encoding="utf-8")
    ppath.write_text(empty_pkg, encoding="utf-8")
    return dpath, ppath


def main() -> int:
    ap = argparse.ArgumentParser(description="Build destructiveChanges from IsDelete (AD) flag")
    add_spreadsheet_id_arg(ap)
    ap.add_argument("--tabs", required=True, help="comma-separated object tab names")
    ap.add_argument("--target-org", default="", help="verify each field exists (Tooling API)")
    ap.add_argument("--out-dir", default="manifest")
    ap.add_argument("--api-version", default="66.0")
    args = ap.parse_args()

    tabs = [t.strip() for t in args.tabs.split(",") if t.strip()]
    svc = get_sheets_service()
    print(f"Scanning IsDelete (AD) across {len(tabs)} tab(s): {', '.join(tabs)}")
    deletes = scan_delete_rows(svc, args.spreadsheet_id, tabs)

    if not deletes:
        print("\nNo IsDelete=TRUE fields found — nothing to delete.")
        return 0

    if args.target_org:
        print(f"\nVerifying existence in org '{args.target_org}' (Tooling API)…")
        kept, dropped = [], []
        for d in deletes:
            if org_has_field(args.target_org, d["object"], d["field"]):
                kept.append(d)
            else:
                dropped.append(d)
        for d in dropped:
            print(f"  – {d['object']}.{d['field']} already absent in org — no-op, dropped.")
        deletes = kept

    if not deletes:
        print("\nAll IsDelete fields are already absent — no destructive package needed.")
        return 0

    blocked = flexipage_refs(args.target_org, deletes) if args.target_org else {}
    if blocked:
        print("\n" + "=" * 72)
        print("  ⛔ BLOCKED — these fields are still placed on a Lightning record page")
        print("     (Salesforce rejects the whole delete set until they are removed):")
        for key, pages in blocked.items():
            print(f"     {key}  ->  {', '.join(pages)}")
        print("  Remove the field(s) from the page, deploy the FlexiPage, then re-run.")
        print("=" * 72)
        return 2

    dpath, ppath = write_manifests(deletes, Path(args.out_dir), args.api_version)

    print("\n" + "=" * 72)
    print(f"  DESTRUCTIVE DELETE SET — {len(deletes)} custom field(s)")
    print("=" * 72)
    for d in deletes:
        print(f"  DELETE  {d['object']}.{d['field']}   ({d['tab']} row {d['row']})")
    print("=" * 72)
    print(f"  destructiveChanges : {dpath}")
    print(f"  empty package      : {ppath}")
    print("\nNEXT (DESTRUCTIVE — review before running):")
    print(f"  python scripts/deploy.py --start \\")
    print(f"      --pre-destructive {dpath} \\")
    print(f"      --package {ppath} \\")
    print(f"      --target-org \"<ORG>\" --test-level NoTestRun --skip-validation-gate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
