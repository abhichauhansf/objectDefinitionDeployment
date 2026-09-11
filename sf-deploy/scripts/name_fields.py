#!/usr/bin/env python3
"""
name_fields.py — Propose Salesforce field API names for an object's blank
fields, using the locally-built phrase glossary (Option B).

Reads a live-fetched object tab (from fetch_sheet.py) plus phrase_glossary.json,
and for every field that has a Japanese label but no API name, proposes a
CamelCase API component via:

  1. exact glossary match on the whole label,
  2. base match  — strip trailing digits / parenthetical qualifier, match the
     base term, then re-append the number (住所3 -> Address3),
  3. compositional greedy longest-substring match — tokenize the label using
     glossary terms (longest first) and concatenate the English pieces; ASCII
     runs (No, ID, TEW…) are carried through cleaned.

Output is a LOCAL proposal only (no sheet write). Every row shows the match
method, a confidence, and any unmatched segments so you can review.

  python scripts/name_fields.py \
      --fields .build/sales_deal_live.json \
      --glossary .build/phrase_glossary.json \
      --out .build/sales_deal_api_names
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

TRAILING_NUM_RE = re.compile(r"([0-9０-９]+)$")
ASCII_RUN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*")

# structural tokens that may not be their own glossary row
FALLBACK = {
    "No": "No", "No.": "No", "ID": "Id", "ＩＤ": "Id",
    "コード": "Code", "名": "Name", "日": "Date", "フラグ": "Flag",
    "枝番": "BranchNo", "区分": "Type", "／": "", "/": "", "・": "", "＿": "",
}


def norm(s) -> str:
    return str(s or "").strip()


def upper_camel(s: str) -> str:
    s = norm(s)
    return s[0].upper() + s[1:] if s else ""


def zenkaku_num_to_ascii(s: str) -> str:
    return s.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def load_glossary(path: str) -> dict[str, str]:
    rows = json.load(open(path, encoding="utf-8"))
    g: dict[str, str] = {}
    for r in rows:
        api = norm(r.get("api"))
        if not api:
            continue
        for jp in norm(r.get("jp")).split(" / "):
            jp = norm(jp)
            if jp and jp not in g:
                g[jp] = api
    for k, v in FALLBACK.items():
        g.setdefault(k, v)
    return g


def strip_trailing_num(label: str) -> tuple[str, str]:
    """Return (base, number_suffix)."""
    m = TRAILING_NUM_RE.search(label)
    if not m:
        return label, ""
    return label[: m.start()], zenkaku_num_to_ascii(m.group(1))


def strip_parenthetical(label: str) -> str:
    m = re.match(r"^(.+?)\s*[（(].+[）)]$", label)
    return norm(m.group(1)) if m else label


def compositional(label: str, terms_sorted: list[str], g: dict[str, str]) -> tuple[str, list[str]]:
    """Greedy longest-substring tokenization. Returns (api, unmatched_segments)."""
    i, n = 0, len(label)
    parts: list[str] = []
    unmatched: list[str] = []
    while i < n:
        # skip pure separators
        if label[i] in "／/・＿_ 　-":
            i += 1
            continue
        # try longest glossary term at this position
        best = ""
        for t in terms_sorted:
            if t and label.startswith(t, i):
                best = t
                break
        if best:
            api = g[best]
            if api:
                parts.append(upper_camel(api))
            i += len(best)
            continue
        # ASCII run carried through
        m = ASCII_RUN_RE.match(label, i)
        if m:
            parts.append(upper_camel(m.group(0)))
            i = m.end()
            continue
        # unmatched single char
        unmatched.append(label[i])
        i += 1
    return "".join(parts), unmatched


def propose(label: str, terms_sorted: list[str], g: dict[str, str]) -> dict:
    raw = norm(label)
    # 1) exact
    if raw in g:
        return {"api": upper_camel(g[raw]), "method": "exact", "conf": "High", "unmatched": ""}
    # 2) base match: strip parenthetical then trailing number
    base_np = strip_parenthetical(raw)
    base, num = strip_trailing_num(base_np)
    base = norm(base)
    if base and base in g:
        return {"api": upper_camel(g[base]) + num, "method": "base", "conf": "High", "unmatched": ""}
    # 3) compositional
    api, unmatched = compositional(base_np, terms_sorted, g)
    if api and not unmatched:
        return {"api": api, "method": "compose", "conf": "Medium", "unmatched": ""}
    if api and unmatched:
        return {"api": api, "method": "compose-partial", "conf": "Low", "unmatched": "".join(unmatched)}
    return {"api": "", "method": "none", "conf": "None", "unmatched": raw}


def dedupe(rows: list[dict]) -> None:
    seen: dict[str, int] = {}
    for r in rows:
        api = r["proposed"]
        if not api:
            continue
        if api not in seen:
            seen[api] = 1
        else:
            seen[api] += 1
            r["proposed"] = f"{api}{seen[api]}"
            r["notes"] = (r.get("notes", "") + " ; de-duplicated").strip(" ;")


def main() -> int:
    ap = argparse.ArgumentParser(description="Propose field API names from glossary (local only)")
    ap.add_argument("--fields", default=".build/sales_deal_live.json")
    ap.add_argument("--glossary", default=".build/phrase_glossary.json")
    ap.add_argument("--out", default=".build/sales_deal_api_names")
    args = ap.parse_args()

    fields = json.load(open(args.fields, encoding="utf-8"))
    g = load_glossary(args.glossary)
    terms_sorted = sorted((t for t in g if t), key=len, reverse=True)

    obj = next((r for r in fields if r.get("_type") == "object_meta"), {})
    obj_api = norm(obj.get("Object API Name"))

    out_rows = []
    for r in fields:
        if r.get("_type") == "object_meta":
            continue
        label = norm(r.get("Field Label"))
        existing = norm(r.get("Field API Name"))
        if not label:
            continue
        if existing:
            out_rows.append({
                "label": label, "type": norm(r.get("Data Type")),
                "existing": existing, "proposed": existing,
                "method": "existing", "conf": "—", "unmatched": "", "notes": "already named",
            })
            continue
        p = propose(label, terms_sorted, g)
        out_rows.append({
            "label": label, "type": norm(r.get("Data Type")),
            "existing": "", "proposed": p["api"],
            "method": p["method"], "conf": p["conf"],
            "unmatched": p["unmatched"], "notes": "",
        })

    # dedupe only the newly proposed names (skip existing standard fields)
    dedupe([r for r in out_rows if r["method"] not in ("existing",)])

    Path(".build").mkdir(exist_ok=True)
    json.dump(out_rows, open(args.out + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(args.out + ".tsv", "w", encoding="utf-8") as f:
        f.write("Field Label\tData Type\tProposed API Name\tMatch\tConfidence\tUnmatched\tNotes\n")
        for r in out_rows:
            f.write(f"{r['label']}\t{r['type']}\t{r['proposed']}\t{r['method']}\t{r['conf']}\t{r['unmatched']}\t{r.get('notes','')}\n")

    # summary
    new = [r for r in out_rows if r["method"] != "existing"]
    from collections import Counter
    by_conf = Counter(r["conf"] for r in new)
    by_meth = Counter(r["method"] for r in new)
    print("=" * 60)
    print(f"  Object: {obj_api}  ({obj.get('Object Label','')})")
    print("  Field API-name proposals (LOCAL only, no sheet write)")
    print("=" * 60)
    print(f"  fields needing names : {len(new)}")
    print(f"  by confidence        : " + ", ".join(f"{k}={v}" for k, v in by_conf.most_common()))
    print(f"  by method            : " + ", ".join(f"{k}={v}" for k, v in by_meth.most_common()))
    print(f"  -> {args.out}.tsv")
    print(f"  -> {args.out}.json")
    print("=" * 60)
    print("  Sample proposals:")
    shown = 0
    for r in new:
        if shown >= 22:
            break
        print(f"   {r['label']:<18} -> {r['proposed'] or '(none)':<28} [{r['conf']}]")
        shown += 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
