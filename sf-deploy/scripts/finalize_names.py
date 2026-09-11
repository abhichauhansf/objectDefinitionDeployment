#!/usr/bin/env python3
"""
finalize_names.py — Merge curated API-name overrides with the live-fetched
object fields to produce the final LOCAL API-name file for review.

Ordering matches name_fields.py's "new fields" order (fields without an API
name, in sheet order). Overrides are keyed by 1-based position in that list.
Enforces API-name uniqueness within the object as a safety net.

  python scripts/finalize_names.py \
      --fields .build/sales_deal_live.json \
      --overrides .build/sales_deal_overrides.json \
      --glossary .build/phrase_glossary.json \
      --out .build/sales_deal_FINAL
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def norm(s) -> str:
    return str(s or "").strip()


MAX_API_LEN = 40  # Salesforce custom field API name limit, INCLUDING prefix + __c suffix


def valid_component(name: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", name)) and not name.endswith("_")


def to_api_c(component: str, prefix: str = "") -> str:
    """Custom field API name = prefix + component + __c (never double-applied)."""
    c = component.strip()
    if prefix and not c.startswith(prefix):
        c = prefix + c
    return c if c.endswith("__c") else c + "__c"


def load_glossary_terms(path: str) -> set[str]:
    try:
        rows = json.load(open(path, encoding="utf-8"))
    except Exception:
        return set()
    terms = set()
    for r in rows:
        for jp in norm(r.get("jp")).split(" / "):
            if norm(jp):
                terms.add(norm(jp))
    return terms


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", default=".build/sales_deal_live.json")
    ap.add_argument("--overrides", default=".build/sales_deal_overrides.json")
    ap.add_argument("--glossary", default=".build/phrase_glossary.json")
    ap.add_argument("--out", default=".build/sales_deal_FINAL")
    ap.add_argument("--prefix", default="TI_Fnt_",
                    help="namespace prefix for custom field API names")
    args = ap.parse_args()

    fields = json.load(open(args.fields, encoding="utf-8"))
    overrides = json.load(open(args.overrides, encoding="utf-8"))
    gloss_terms = load_glossary_terms(args.glossary)

    obj = next((r for r in fields if r.get("_type") == "object_meta"), {})
    obj_api = norm(obj.get("Object API Name"))
    obj_label = norm(obj.get("Object Label"))

    new_fields = [r for r in fields
                  if r.get("_type") != "object_meta"
                  and norm(r.get("Field Label"))
                  and not norm(r.get("Field API Name"))]

    problems = []
    too_long = []
    used: dict[str, int] = {}
    rows = []
    for i, r in enumerate(new_fields, 1):
        label = norm(r.get("Field Label"))
        ftype = norm(r.get("Data Type"))
        comp = norm(overrides.get(str(i)))
        source = "curated"
        if not comp:
            problems.append(f"[{i}] '{label}' has NO override")
        elif not valid_component(comp):
            problems.append(f"[{i}] '{label}' -> invalid component '{comp}'")
        # source tag: exact glossary term?
        base = re.sub(r"\d+$", "", label)
        if label in gloss_terms or base in gloss_terms:
            source = "glossary+curated"
        # uniqueness safety net (compare on the final prefixed __c name, case-insensitive)
        if comp:
            key = to_api_c(comp, args.prefix).lower()
            if key not in used:
                used[key] = 1
            else:
                used[key] += 1
                comp = f"{comp}{used[key]}"
        api_c = to_api_c(comp, args.prefix) if comp else ""
        if api_c and len(api_c) > MAX_API_LEN:
            too_long.append(f"[{i}] '{label}' -> '{api_c}' ({len(api_c)} chars > {MAX_API_LEN})")
        rows.append({
            "n": i, "label": label, "type": ftype,
            "component": comp, "api": api_c,
            "len": len(api_c), "source": source,
        })

    Path(".build").mkdir(exist_ok=True)
    json.dump(
        {"object": {"api": obj_api, "label": obj_label}, "fields": rows},
        open(args.out + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=2,
    )
    with open(args.out + ".tsv", "w", encoding="utf-8") as f:
        f.write("#\tField Label\tData Type\tField API Name\tLen\tSource\n")
        for r in rows:
            f.write(f"{r['n']}\t{r['label']}\t{r['type']}\t{r['api']}\t{r['len']}\t{r['source']}\n")

    dup = [k for k, v in used.items() if v > 1]
    print("=" * 60)
    print(f"  {obj_label} ({obj_api}) — FINAL API names (LOCAL only)")
    print("=" * 60)
    print(f"  fields named          : {sum(1 for r in rows if r['api'])}/{len(rows)}")
    print(f"  from glossary terms   : {sum(1 for r in rows if r['source']=='glossary+curated')}")
    print(f"  all end with __c      : {all(r['api'].endswith('__c') for r in rows if r['api'])}")
    print(f"  uniqueness fixes      : {len(dup)}")
    print(f"  over {MAX_API_LEN} chars (__c)   : {len(too_long)}")
    for t in too_long:
        print("    -", t)
    print(f"  invalid/missing       : {len(problems)}")
    for p in problems[:20]:
        print("    -", p)
    print(f"  -> {args.out}.tsv")
    print(f"  -> {args.out}.json")
    print("=" * 60)
    return 1 if (problems or too_long) else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
