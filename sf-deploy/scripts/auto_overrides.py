#!/usr/bin/env python3
"""
auto_overrides.py — Build a finalize_names overrides map GLOSSARY-FIRST.

Per the naming rule (sf-field-api-naming.mdc §0), API names come from the glossary
first; manual naming only fills what the glossary cannot resolve. This reads the
name_fields.py proposal JSON and emits an overrides file where:

  * every field the glossary resolved (method != 'none') uses the proposed
    component as-is, and
  * fields the glossary could not match (method == 'none') are left for manual
    naming — supplied via an optional --manual JSON keyed by 1-based position.

Positions are 1-based over the "new fields" list (fields with a label and no API
name), matching finalize_names.py's ordering.

  python scripts/auto_overrides.py \
      --proposals .build/sales_shipping_api_names.json \
      --manual .build/sales_shipping_manual.json \
      --out .build/sales_shipping_overrides.json
"""
from __future__ import annotations

import argparse
import json
import re


def norm(s) -> str:
    return str(s or "").strip()


def valid_component(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name)) and not name.endswith("_")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--proposals", required=True, help="name_fields.py *.json output")
    ap.add_argument("--manual", default="", help="optional manual overrides {position: component} for none-matches")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = json.load(open(args.proposals, encoding="utf-8"))
    new_fields = [r for r in rows if r.get("method") != "existing"]
    manual = {}
    if args.manual:
        try:
            manual = {str(k): norm(v) for k, v in json.load(open(args.manual, encoding="utf-8")).items()}
        except FileNotFoundError:
            manual = {}

    overrides: dict[str, str] = {}
    glossary_named = 0
    manual_named = 0
    need_manual = []  # (position, label)
    invalid = []
    # Precedence: an explicit, valid manual override always WINS — it is the
    # curator's deliberate decision to replace a weak/ambiguous glossary match
    # (e.g. compose-partial "Category"/"Amount" fragments that would collide).
    # The glossary proposal is used only where no manual override was supplied.
    for i, r in enumerate(new_fields, 1):
        label = norm(r.get("label"))
        proposed = norm(r.get("proposed"))
        method = norm(r.get("method"))
        pos = str(i)
        if pos in manual and manual[pos]:
            comp = manual[pos]
            if valid_component(comp):
                overrides[pos] = comp
                manual_named += 1
            else:
                invalid.append((pos, label, comp))
        elif method != "none" and proposed and valid_component(proposed):
            overrides[pos] = proposed
            glossary_named += 1
        else:
            need_manual.append((pos, label))

    json.dump(overrides, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"  overrides written: {args.out}")
    print(f"  total new fields   : {len(new_fields)}")
    print(f"  glossary-named     : {glossary_named}")
    print(f"  manually-named     : {manual_named}")
    print(f"  STILL need manual  : {len(need_manual)}")
    for pos, label in need_manual:
        print(f"     [{pos}] {label}")
    if invalid:
        print(f"  INVALID manual comps: {len(invalid)}")
        for pos, label, comp in invalid:
            print(f"     [{pos}] {label} -> '{comp}'")
    print("=" * 60)
    return 1 if need_manual or invalid else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
