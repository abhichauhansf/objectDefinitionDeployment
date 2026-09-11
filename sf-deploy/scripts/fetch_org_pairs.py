#!/usr/bin/env python3
"""
fetch_org_pairs.py — Harvest (Japanese label -> English API name) pairs from a
connected Salesforce org via the Tooling API. This is the *training data* for
the glossary builder (build_glossary.py), and a far richer source than the
mostly-blank definition workbook.

Read-only. No LLM. Uses the `sf` CLI (must already be authenticated).

For every CUSTOM object (`*__c`, non-managed) it pulls the custom `*__c` fields
and keeps the ones whose Label contains Japanese characters — i.e. genuine
JP->EN pairs. Managed-package entities (box__, gcss__, …) and English-only
standard fields are skipped.

Usage:
  python scripts/fetch_org_pairs.py --org devpro3 [--out .build/org_pairs.json]
      [--scope all|toray] [--limit-objects N] [--include-namespaced]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

JP_RE = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]")  # hira/kana/kanji
# Objects that are Toray-domain relevant when --scope toray is used.
TORAY_HINT = re.compile(r"(Sales_|Master__c$|List__c$|諸掛|成約|CA__c$|Plan__c$|Rel__c$)")


def sf_query(org: str, soql: str, tooling: bool = True) -> list[dict]:
    """Run a SOQL query through the sf CLI and return records."""
    env = dict(os.environ, SF_DISABLE_LOG_FILE="true", SFDX_DISABLE_LOG_FILE="true")
    cmd = ["sf", "data", "query", "-o", org, "-q", soql, "--json"]
    if tooling:
        cmd.append("--use-tooling-api")
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(f"sf query failed: {proc.stderr[:400] or proc.stdout[:400]}")
    data = json.loads(proc.stdout or "{}")
    return data.get("result", {}).get("records", []) or []


def is_managed(api_name: str) -> bool:
    """True if the API name carries a managed-package namespace (ns__Name__c)."""
    core = api_name[:-3] if api_name.endswith("__c") else api_name
    return "__" in core  # any remaining "__" means a namespace prefix


def list_custom_objects(org: str, include_namespaced: bool) -> list[dict]:
    recs = sf_query(
        org,
        "SELECT QualifiedApiName, Label FROM EntityDefinition "
        "WHERE QualifiedApiName LIKE '%__c' ORDER BY QualifiedApiName",
    )
    out = []
    for r in recs:
        api = r.get("QualifiedApiName", "")
        if not api.endswith("__c"):
            continue
        if not include_namespaced and is_managed(api):
            continue
        out.append({"api": api, "label": r.get("Label", "")})
    return out


def fields_for(org: str, obj_api: str) -> list[dict]:
    return sf_query(
        org,
        "SELECT QualifiedApiName, Label, DataType FROM FieldDefinition "
        f"WHERE EntityDefinition.QualifiedApiName = '{obj_api}' "
        "ORDER BY QualifiedApiName",
    )


def has_jp(text: str) -> bool:
    return bool(JP_RE.search(text or ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest JP->EN field pairs from an org")
    ap.add_argument("--org", required=True, help="sf org alias or username")
    ap.add_argument("--out", default=".build/org_pairs.json")
    ap.add_argument("--scope", choices=["all", "toray"], default="all")
    ap.add_argument("--limit-objects", type=int, default=0)
    ap.add_argument("--include-namespaced", action="store_true",
                    help="also include managed-package objects/fields")
    ap.add_argument("--include-object-labels", action="store_true", default=True,
                    help="also emit the object label itself as a pair")
    args = ap.parse_args()

    print(f"Org: {args.org}   scope: {args.scope}")
    objects = list_custom_objects(args.org, args.include_namespaced)
    if args.scope == "toray":
        objects = [o for o in objects if TORAY_HINT.search(o["api"]) or has_jp(o["label"])]
    if args.limit_objects:
        objects = objects[: args.limit_objects]
    print(f"Custom objects to scan: {len(objects)}")

    pairs: list[dict] = []
    skipped_no_jp = 0
    obj_with_jp_fields = 0
    for i, obj in enumerate(objects, 1):
        api = obj["api"]
        try:
            fields = fields_for(args.org, api)
        except Exception as e:
            print(f"  [{i}/{len(objects)}] {api}: query failed — {e}")
            continue

        obj_pairs = 0
        for f in fields:
            fapi = f.get("QualifiedApiName", "")
            label = (f.get("Label") or "").strip()
            if not fapi.endswith("__c"):
                continue  # skip standard fields
            if not args.include_namespaced and is_managed(fapi):
                continue
            if not has_jp(label):
                skipped_no_jp += 1
                continue
            pairs.append({
                "label": label,
                "fullName": fapi[:-3],  # strip __c (builder re-adds it)
                "source": api,
                "dataType": f.get("DataType", ""),
            })
            obj_pairs += 1

        if obj_pairs:
            obj_with_jp_fields += 1
        # object-level pair (object label is often a reusable term too)
        if args.include_object_labels and has_jp(obj["label"]):
            pairs.append({"label": obj["label"].strip(), "fullName": api[:-3],
                          "source": api, "dataType": "Object"})
        print(f"  [{i}/{len(objects)}] {api:<40} {obj_pairs} JP field(s)")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(pairs, fh, ensure_ascii=False, indent=2)

    print("\n" + "=" * 60)
    print(f"  objects scanned          : {len(objects)}")
    print(f"  objects with JP fields   : {obj_with_jp_fields}")
    print(f"  JP field pairs collected : {len(pairs)}")
    print(f"  non-JP fields skipped    : {skipped_no_jp}")
    print(f"  written                  : {args.out}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
