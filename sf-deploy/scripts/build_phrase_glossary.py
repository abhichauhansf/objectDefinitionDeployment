#!/usr/bin/env python3
"""
build_phrase_glossary.py — Option B: pragmatic phrase glossary.

Instead of shattering Japanese labels into single-kanji atoms (which the org's
2-kanji domain vocabulary does not fit), this builds the glossary at the
granularity the org already uses: whole-label and shared base-label level.

For each distinct Japanese term it picks ONE canonical English API name by
usage frequency, collapsing trailing-digit variants (Address1/2/3 -> Address)
and stripping the org namespace prefix (TI_Logi_TradeArrangement ->
TradeArrangement). Rows are then consolidated by English API (multiple JP terms
sharing one English are joined with " / "), matching the sheet Glossary layout:

  [Japanese Term, English API Component, Usage Count, Source Objects]

Output is LOCAL only (no Google Sheet writes):
  .build/phrase_glossary.json
  .build/phrase_glossary.tsv

Usage:
  python scripts/build_phrase_glossary.py --in .build/org_pairs.json
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

OUT_JSON = ".build/phrase_glossary.json"
OUT_TSV = ".build/phrase_glossary.tsv"
OUTPUT_HEADERS = ["Japanese Term", "English API Component", "Usage Count", "Source Objects"]

# namespace-style prefixes the org uses on some fullNames (e.g. TI_Logi_, TI_STC_)
NS_PREFIX_RE = re.compile(r"^TI_[A-Za-z]+_")
TRAILING_DIGITS_RE = re.compile(r"[0-9]+$")


def norm_label(v) -> str:
    s = str(v or "").strip()
    return "" if s in ("", "label", ".") else s


def is_valid_jp_label(label: str) -> bool:
    if not label:
        return False
    if re.fullmatch(r"[\s\-\./0-9]+", label):
        return False
    if re.fullmatch(r"[A-Za-z0-9\s_\-\.]+", label):  # pure ASCII/number => not JP
        return False
    return True


def clean_en(v) -> str:
    """Normalise an English fullName into a clean API component candidate."""
    s = str(v or "").strip()
    if s in ("", ".", "fullName"):
        return ""
    s = re.sub(r"__(c|r)$", "", s, flags=re.I)   # drop custom suffix
    s = NS_PREFIX_RE.sub("", s)                    # drop TI_Logi_ / TI_STC_ prefix
    if not s or re.search(r"[\s/／]", s) or not re.search(r"[A-Za-z]", s):
        return ""
    return s[0].upper() + s[1:]


def en_base(fn: str) -> str:
    """Collapse trailing-digit variants: Address1/Address2 -> Address."""
    return TRAILING_DIGITS_RE.sub("", fn) or fn


# ---- variant base-label expansion (numbered / parenthetical) -----------------
def split_trailing_number(label: str) -> str:
    t = norm_label(label)
    m = re.match(r"^(.*?)([0-9０-９]+)$", t)
    if not m:
        return t
    return norm_label(m.group(1)) or t


def split_trailing_parenthetical(label: str) -> str:
    t = norm_label(label)
    m = re.match(r"^(.+?)\s*[（(](.+)[）)]$", t)
    if not m:
        return t
    return norm_label(m.group(1)) or t


def variant_base_labels(label: str) -> list[str]:
    # Only collapse trailing-number variants (住所1 -> 住所). Parenthetical
    # qualifiers (備考(ネゴ用1)) are kept separate: they carry distinct meaning
    # and must not inherit the generic base term's mapping.
    num_base = split_trailing_number(label)
    return [num_base] if num_base and num_base != label else []


def pick_canonical(candidates: dict[str, dict]) -> str:
    """
    candidates: cleaned_fullName -> {"count": int}
    Group by digit-stripped base, pick the base with the highest total usage,
    then the cleanest concrete form within it (prefer == base, then shortest,
    then no underscore, then alphabetical).
    """
    by_base: dict[str, dict] = defaultdict(lambda: {"score": 0, "forms": set()})
    for fn, st in candidates.items():
        b = en_base(fn)
        by_base[b]["score"] += st["count"]
        by_base[b]["forms"].add(fn)

    best_base = sorted(by_base.items(), key=lambda kv: (-kv[1]["score"], len(kv[0]), kv[0]))[0][0]
    forms = by_base[best_base]["forms"]

    # If a clean un-numbered form exists, use it; otherwise use the digit-stripped
    # base so the glossary canonical is not a numbered variant (Qty1 -> Qty).
    if best_base in forms:
        return best_base
    non_digit = [f for f in forms if not TRAILING_DIGITS_RE.search(f)]
    if non_digit:
        return sorted(non_digit, key=lambda f: (1 if "_" in f else 0, len(f), f))[0]
    return best_base


def build(in_path: str) -> None:
    raw = json.load(open(in_path, encoding="utf-8"))

    # JP label -> cleaned EN fullName -> {count, sources}
    label_map: dict[str, dict[str, dict]] = defaultdict(
        lambda: defaultdict(lambda: {"count": 0, "sources": set()})
    )

    def add(label: str, en: str, src: str) -> None:
        st = label_map[label][en]
        st["count"] += 1
        if src:
            st["sources"].add(src)

    for p in raw:
        label = norm_label(p.get("label"))
        en = clean_en(p.get("fullName"))
        src = p.get("source", "")
        if not label or not en or not is_valid_jp_label(label):
            continue
        add(label, en, src)
        for base in variant_base_labels(label):
            add(base, en, src)

    # canonical EN per JP label
    label_rows = []
    for label, cands in label_map.items():
        canon = pick_canonical({fn: {"count": st["count"]} for fn, st in cands.items()})
        total = sum(st["count"] for st in cands.values())
        sources = set()
        for st in cands.values():
            sources.update(st["sources"])
        label_rows.append({
            "jp": label,
            "api": canon,
            "count": total,
            "sources": sources,
            "variants": len({en_base(fn) for fn in cands}),
        })

    # One row per distinct Japanese BASE term. Numbered variants (住所1, 住所2)
    # were already folded into their base label during expansion, so we keep only
    # labels that are their own base and drop the numbered duplicates.
    rows = [
        {"jp": r["jp"], "api": r["api"], "count": r["count"], "sources": sorted(r["sources"])}
        for r in label_rows
        if split_trailing_number(r["jp"]) == r["jp"]
    ]
    rows.sort(key=lambda r: (-r["count"], r["jp"]))

    Path(".build").mkdir(exist_ok=True)
    json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(OUT_TSV, "w", encoding="utf-8") as f:
        f.write("\t".join(OUTPUT_HEADERS) + "\n")
        for r in rows:
            srcs = ", ".join(r["sources"][:5]) + (
                f" (+{len(r['sources']) - 5} more)" if len(r["sources"]) > 5 else ""
            )
            f.write(f"{r['jp']}\t{r['api']}\t{r['count']}\t{srcs}\n")

    print("=" * 64)
    print("  Phrase glossary built (Option B) — LOCAL only, no sheet write")
    print("=" * 64)
    print(f"  input pairs              : {len(raw)}")
    print(f"  distinct JP terms        : {len(label_map)}")
    print(f"  consolidated glossary rows: {len(rows)}")
    print(f"  -> {OUT_JSON}")
    print(f"  -> {OUT_TSV}")
    print("=" * 64)
    print("  Top 25 by usage:")
    for r in rows[:25]:
        print(f"   {r['count']:>4}  {r['jp'][:26]:<26}  {r['api']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build pragmatic phrase glossary (Option B)")
    ap.add_argument("--in", dest="inp", default=".build/org_pairs.json")
    args = ap.parse_args()
    build(args.inp)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
