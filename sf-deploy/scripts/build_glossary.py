#!/usr/bin/env python3
"""
build_glossary.py — Faithful local port of the GAS glossary pipeline
(GenerateGlossaryDataDictionary.gs + GenerateGlossaryChunkProcessor.gs),
following BUILD_GLOSSARY_SPEC.md (minimum-unit + overlap-consistency).

Difference from the GAS: the AI atom-decomposition step is performed by the
Cursor agent itself (no external LLM gateway). So the build runs in two
deterministic phases with a manual decomposition step in between:

  PHASE 1  prepare
    org_pairs.json  ->  (a) deterministic pre-pass entries (atomic 1:1, no AI)
                        (b) decompose_queue.json  (compound pairs needing AI)

  [agent step]  the assistant decomposes decompose_queue.json into atoms per
                the spec and writes decomposed_atoms.json:
                  [{"jp":"成約","en":"SalesDeal","source":"..."}, ...]

  PHASE 2  assemble
    prepass + decomposed_atoms  ->  merge/normalize/consolidate  ->
    glossary.json + glossary.tsv   (exact 4-col layout, LOCAL only — no sheet)

Output columns match the sheet Glossary exactly:
  [Japanese Term, English API Component, Usage Count, Source Objects]

Usage:
  python scripts/build_glossary.py prepare  --in .build/org_pairs.json
  #  (assistant fills .build/decomposed_atoms.json)
  python scripts/build_glossary.py assemble
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from pathlib import Path

# 2-kanji compounds that are semantically indivisible in this domain and thus
# allowed as atoms (identical to GGEN_ATOMIC_COMPOUND_WHITELIST in the GAS).
ATOMIC_COMPOUND_WHITELIST = {
    "保険", "物流", "通関", "関税", "貨物", "書類",
    "期間", "航空", "貿易", "輸入", "輸出", "倉庫",
    "運賃", "運送", "為替", "税関", "課税", "発行",
    "商品", "一般", "特定",
}

PREPASS_PATH = ".build/glossary_prepass.json"
QUEUE_PATH = ".build/decompose_queue.json"
ATOMS_PATH = ".build/decomposed_atoms.json"
OUT_JSON = ".build/glossary.json"
OUT_TSV = ".build/glossary.tsv"

KANJI_RE = re.compile(r"[\u4E00-\u9FFF]")
OUTPUT_HEADERS = ["Japanese Term", "English API Component", "Usage Count", "Source Objects"]


# --------------------------------------------------------------------------- #
# shared normalisation (ports of the GGEN_* helpers)
# --------------------------------------------------------------------------- #
def norm_label(v) -> str:
    s = str(v or "").strip()
    return "" if s in ("", "label", ".") else s


def norm_raw_fullname(v) -> str:
    s = str(v or "").strip()
    if s in ("", ".", "fullName"):
        return ""
    s = re.sub(r"__(c|r)$", "", s, flags=re.I)
    if not s or re.search(r"[\s/／]", s) or not re.search(r"[A-Za-z]", s):
        return ""
    return s


def upper_camel_first(v: str) -> str:
    s = str(v or "").strip()
    return s[0].upper() + s[1:] if s else ""


def count_kanji(t: str) -> int:
    return len(KANJI_RE.findall(str(t or "")))


def is_valid_jp_label(label: str) -> bool:
    if not label:
        return False
    if re.fullmatch(r"[\s\-\./0-9]+", label):
        return False
    if re.fullmatch(r"[A-Za-z0-9\s_\-\.]+", label):
        return False
    return True


def split_trailing_number(label: str) -> str:
    t = norm_label(label)
    m = re.match(r"^(.*?)([0-9０-９]+)$", t)
    if not m:
        return t
    base = norm_label(m.group(1))
    return base or t


def split_trailing_parenthetical(label: str) -> str:
    t = norm_label(label)
    m = re.match(r"^(.+?)\s*[（(](.+)[）)]$", t)
    if not m:
        return t
    base = norm_label(m.group(1))
    return base or t


def variant_base_labels(label: str) -> list[str]:
    bases = []
    num_base = split_trailing_number(label)
    if num_base and num_base != label:
        bases.append(num_base)
    paren_base = split_trailing_parenthetical(num_base)
    if paren_base and paren_base != num_base:
        bases.append(paren_base)
    seen, out = set(), []
    for b in bases:
        if b not in seen:
            seen.add(b); out.append(b)
    return out


def norm_jp_atom(v: str) -> str:
    t = str(v or "").strip()
    if not t:
        return ""
    if re.fullmatch(r"[A-Za-z0-9\s_\-\./]+", t):
        return ""
    if re.fullmatch(r"[\s\-\./0-9]+", t):
        return ""
    if count_kanji(t) >= 2 and t not in ATOMIC_COMPOUND_WHITELIST:
        return ""  # min-unit defense: drop multi-kanji non-whitelist compounds
    return t


def norm_en_atom(v: str) -> str:
    t = str(v or "").strip()
    if not t:
        return ""
    t = re.sub(r"__(c|r)$", "", t, flags=re.I)
    if re.search(r"[\s/／]", t):
        return ""
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", t):
        return ""
    return t[0].upper() + t[1:]


def is_atomic_candidate(label: str, full_name: str) -> bool:
    if not label or re.search(r"[\s/／]", label):
        return False
    if not full_name or re.search(r"[\s/\._\-]", full_name):
        return False
    if re.search(r"[A-Z].*[A-Z]", full_name):  # camel-case boundary => compound
        return False
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", full_name):
        return False
    k = count_kanji(label)
    if k == 0:
        return len(label) <= 4
    if k == 1:
        return True
    return label in ATOMIC_COMPOUND_WHITELIST


# --------------------------------------------------------------------------- #
# PHASE 1 — prepare
# --------------------------------------------------------------------------- #
def phase_prepare(in_path: str) -> None:
    raw = json.load(open(in_path, encoding="utf-8"))

    # collect + expand variant base labels
    pairs = []
    for p in raw:
        label = norm_label(p.get("label"))
        full = norm_raw_fullname(p.get("fullName"))
        src = p.get("source", "")
        if not label or not full or not is_valid_jp_label(label):
            continue
        pairs.append({"label": label, "fullName": full, "source": src})
        for base in variant_base_labels(label):
            pairs.append({"label": base, "fullName": full, "source": src})

    # aggregate label -> fullName -> {count, sources}
    label_map: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"count": 0, "sources": set()}))
    for p in pairs:
        st = label_map[p["label"]][p["fullName"]]
        st["count"] += 1
        if p["source"]:
            st["sources"].add(p["source"])

    prepass, queue = [], []
    for label, by_full in label_map.items():
        if len(by_full) == 1:
            full = next(iter(by_full))
            st = by_full[full]
            if is_atomic_candidate(label, full):
                prepass.append({
                    "labels": [label],
                    "api": upper_camel_first(full),
                    "count": st["count"],
                    "sources": sorted(st["sources"]),
                })
                continue
        for full, st in by_full.items():
            queue.append({
                "label": label,
                "fullName": full,
                "count": st["count"],
                "sources": sorted(st["sources"]),
            })

    Path(".build").mkdir(exist_ok=True)
    json.dump(prepass, open(PREPASS_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    json.dump(queue, open(QUEUE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("=" * 64)
    print("  PHASE 1 — prepare complete")
    print("=" * 64)
    print(f"  input pairs (post-expand) : {len(pairs)}")
    print(f"  distinct JP labels        : {len(label_map)}")
    print(f"  pre-pass atoms (no AI)    : {len(prepass)}  -> {PREPASS_PATH}")
    print(f"  queue for AI decompose    : {len(queue)}  -> {QUEUE_PATH}")
    print("=" * 64)
    print("NEXT: the assistant decomposes the queue into atoms and writes")
    print(f"      {ATOMS_PATH} as [{{'jp','en','source'}}...], then run:")
    print("      python scripts/build_glossary.py assemble")


# --------------------------------------------------------------------------- #
# PHASE 2 — assemble
# --------------------------------------------------------------------------- #
def phase_assemble() -> None:
    prepass = json.load(open(PREPASS_PATH, encoding="utf-8")) if Path(PREPASS_PATH).exists() else []
    atoms = json.load(open(ATOMS_PATH, encoding="utf-8")) if Path(ATOMS_PATH).exists() else []

    # accumulator: jp -> {api, count, sources:set}
    acc: dict[str, dict] = {}
    collisions: dict[str, set] = defaultdict(set)

    def merge(jp: str, en: str, count: int, sources) -> None:
        jp = norm_jp_atom(jp)
        en = norm_en_atom(en)
        if not jp or not en:
            return
        cur = acc.get(jp)
        if not cur:
            acc[jp] = {"api": en, "count": count, "sources": set(sources or [])}
            return
        if cur["api"] == en:
            cur["count"] += count
            cur["sources"].update(sources or [])
        else:
            collisions[jp].add(cur["api"])
            collisions[jp].add(en)
            cur["count"] += count
            cur["sources"].update(sources or [])

    for e in prepass:
        for label in e.get("labels", []):
            merge(label, e.get("api", ""), int(e.get("count", 1)), e.get("sources", []))
    for a in atoms:
        merge(a.get("jp", ""), a.get("en", ""), int(a.get("count", 1)), a.get("source") and [a["source"]] or a.get("sources", []))

    # apply collisions: English cell shows "A / B" (spec §5.5 legacy collision)
    for jp, ens in collisions.items():
        if jp in acc and len(ens) >= 2:
            acc[jp]["api"] = " / ".join(sorted(ens))

    # consolidate by English token (rows sharing one API merge their labels)
    by_api: dict[str, dict] = {}
    for jp, v in acc.items():
        key = v["api"]
        b = by_api.setdefault(key, {"labels": set(), "count": 0, "sources": set()})
        b["labels"].add(jp)
        b["count"] += v["count"]
        b["sources"].update(v["sources"])

    rows = []
    for api, b in by_api.items():
        rows.append({
            "jp": " / ".join(sorted(b["labels"])),
            "api": api,
            "count": b["count"],
            "sources": sorted(b["sources"]),
        })
    rows.sort(key=lambda r: (-r["count"], r["api"]))

    json.dump(rows, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    with open(OUT_TSV, "w", encoding="utf-8") as f:
        f.write("\t".join(OUTPUT_HEADERS) + "\n")
        for r in rows:
            srcs = ", ".join(r["sources"][:5]) + (f" (+{len(r['sources'])-5} more)" if len(r["sources"]) > 5 else "")
            f.write(f"{r['jp']}\t{r['api']}\t{r['count']}\t{srcs}\n")

    print("=" * 64)
    print("  PHASE 2 — assemble complete (LOCAL only, no sheet write)")
    print("=" * 64)
    print(f"  prepass atoms      : {len(prepass)}")
    print(f"  decomposed atoms   : {len(atoms)}")
    print(f"  glossary entries   : {len(rows)}")
    print(f"  collisions (A / B) : {len(collisions)}")
    print(f"  -> {OUT_JSON}")
    print(f"  -> {OUT_TSV}")
    print("=" * 64)


def main() -> int:
    ap = argparse.ArgumentParser(description="Build glossary locally (agent-decomposed)")
    sub = ap.add_subparsers(dest="phase", required=True)
    p1 = sub.add_parser("prepare")
    p1.add_argument("--in", dest="inp", default=".build/org_pairs.json")
    sub.add_parser("assemble")
    args = ap.parse_args()

    if args.phase == "prepare":
        phase_prepare(args.inp)
    elif args.phase == "assemble":
        phase_assemble()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
