#!/usr/bin/env python3
"""
picklist_api.py — English picklist value API names from DeepL (JA label → ASCII).

A picklist entry written as plain ``Label`` (no ``Label:ApiName``) is stored in
Salesforce with the label itself as its API name, so Japanese labels become
Japanese API names. This step proposes an ASCII API name for each such entry by
translating the label with DeepL and PascalCasing the result
(``取込成功`` → ``ImportSuccess``), and rewrites the cell as ``Label:ApiName``.

Rules:
  * An explicit ``Label:ApiName`` is never changed.
  * A label that is already a valid ASCII API name (``TAF``, ``AOKI``) is kept.
  * A value that already exists in the org (matched by label or API name) keeps
    the org's API name — renaming it would orphan the data stored against it.
  * DeepL only (no Google, no LLM). A DeepL failure stops the step.
  * The rewritten cell is a gated sheet write (attribute write-back): preview,
    confirm, then apply. The write target is always col H (``value``); col G
    (customer view) is only read when H is blank.

Usage:
  python scripts/picklist_api.py --rows temp_updates.json --org <ORG> \
      [--out .build/picklist_api_preview.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sheet import COL_H_KEY, _PICKLIST_TYPES, norm  # noqa: E402

API_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
API_MAX_LEN = 80
_ESC = "\x00"


def is_picklist(row: dict) -> bool:
    raw = norm(row.get("Data Type")).lower().replace(" ", "").replace("_", "").replace("-", "")
    return raw in _PICKLIST_TYPES


def split_values(text: str) -> list[str]:
    """Picklist entries split on ';', full-width '；', or newline."""
    return [e.strip() for e in re.split(r"[;\n]", str(text or "").translate({0xFF1B: ord(";")}))
            if e.strip()]


def split_entry(entry: str) -> tuple[str, str | None]:
    """'Label:ApiName' -> (label, api); bare 'Label' -> (label, None).

    '\\:' is a literal colon in the label. A full-width '：' is the separator
    only when the entry has no half-width ':'; otherwise it is label text
    ('棚卸差損：実額:AA4' -> label '棚卸差損：実額', api 'AA4').
    """
    # Updated by Divakar N — 2026-10-21. Why: IncidentalExpensesClass2 values
    # ('棚卸差損：実額:AA4') collapsed to duplicate labels '棚卸差損' when '：'
    # was always read as the separator; the org labels keep the '：'.
    e = entry.strip().replace("\\:", _ESC)
    if ":" not in e:
        e = e.translate({0xFF1A: ord(":")})
    if ":" in e:
        label, api = [p.strip() for p in e.split(":", 1)]
        return label.replace(_ESC, ":"), api.replace(_ESC, ":")
    return e.replace(_ESC, ":"), None


def parse_entries(text: str) -> list[tuple[str, str | None]]:
    """Same split as generate_xml: ';' or newline, 'Label:ApiName', '\\:' escapes."""
    return [split_entry(e) for e in split_values(text)]


def resolve_against_org(
    entries: list[tuple[str, str | None]],
    existing: dict[str, str] | None,
) -> list[tuple[str, str | None, str]]:
    """Keep the org API name when the label already exists.

    Salesforce rejects a new picklist value whose label matches an existing
    one (`Duplicate label`), and Metadata API cannot rename a value's
    fullName. Sheet codes like AA4 for a live label stored as AAA must stay AAA.
    `existing` is {label_or_api_lower: org_api} from org_picklist_values.
    Returns (label, api, source) source = org|sheet|none.
    """
    # Updated by Divakar N — 2026-10-21. Why: ERPPJ-1113 Class2 dry-run failed
    # Duplicate label 販売員手数料：単価 — sheet AA4/DW5 vs org AAA/BHB.
    out: list[tuple[str, str | None, str]] = []
    for label, api in entries:
        org_api = (existing or {}).get((label or "").lower())
        if org_api:
            out.append((label, org_api, "org" if (api or "") != org_api else "sheet"))
        elif api:
            out.append((label, api, "sheet"))
        else:
            out.append((label, None, "none"))
    return out


def format_entries(entries: list[tuple[str, str | None]]) -> str:
    parts = []
    for label, api in entries:
        lab = label.replace(":", "\\:")
        parts.append(f"{lab}:{api}" if api and api != label else lab)
    return ";".join(parts)


# Value parts: "しまむら-出荷実績（HMK）" → ["しまむら", "出荷実績", "HMK"].
# '/' and '・' are kept inside a part (小売店/問屋向け販売, ダウ・東レ are one name).
_SEGMENT_SPLIT = re.compile(r"->|→|[-－—–（）()\[\]【】\s]+")


def split_segments(label: str) -> list[str]:
    return [s.strip() for s in _SEGMENT_SPLIT.split(label) if s.strip()]


def to_api_name(english: str) -> str:
    """'Import success (HMK)' → 'ImportSuccessHMK'. Keeps acronyms as written."""
    ascii_text = unicodedata.normalize("NFKD", english).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", ascii_text)
    name = "".join(w[:1].upper() + w[1:] for w in words)
    if name and not name[0].isalpha():
        name = "V" + name
    if len(name) > API_MAX_LEN:
        cut = ""
        for w in words:
            nxt = cut + w[:1].upper() + w[1:]
            if len(nxt) > API_MAX_LEN:
                break
            cut = nxt
        name = cut or name[:API_MAX_LEN]
    return name


def org_picklist_values(objs: list[str], org: str) -> dict[tuple[str, str], dict[str, str]]:
    """{(obj, field): {label_or_api_lower: org_api}} for objects already in the org."""
    if not objs or not org:
        return {}
    from translate_enrich import load_token, read_metadata
    tok = load_token(org)
    recs = read_metadata("CustomObject", objs, tok["accessToken"],
                         tok["instanceUrl"].rstrip("/"), tok["apiVersion"])
    out: dict[tuple[str, str], dict[str, str]] = {}
    for rec, _raw in recs:
        obj = norm(rec.findtext("fullName"))
        for f in rec.iter("fields"):
            fname = norm(f.findtext("fullName"))
            vals: dict[str, str] = {}
            for v in f.iter("value"):
                api = norm(v.findtext("fullName"))
                if not api:
                    continue
                vals[api.lower()] = api
                lab = norm(v.findtext("label"))
                if lab:
                    vals.setdefault(lab.lower(), api)
            if vals:
                out[(obj, fname)] = vals
    return out


def propose(rows: list[dict], org_values: dict, translate) -> list[dict]:
    """Return one proposal per picklist row whose cell would change."""
    pending: list[tuple[dict, list, int]] = []
    plans = []
    for r in rows:
        if r.get("_type") == "object_meta" or not is_picklist(r):
            continue
        if norm(r.get("Global Value Set")):
            continue
        field = norm(r.get("Field API Name"))
        obj = norm(r.get("Object API Name"))
        src_text = norm(r.get(COL_H_KEY))
        entries = parse_entries(src_text)
        if not entries:
            continue
        existing = org_values.get((obj, field), {})
        resolved: list[list] = []
        for label, api, src in resolve_against_org(entries, existing):
            if src == "org":
                resolved.append([label, api, "org"])
            elif api:
                resolved.append([label, api, "sheet"])
            elif API_RE.match(label or ""):
                resolved.append([label, label, "ascii"])
            else:
                resolved.append([label, None, "deepl"])
        plan = {"row": r, "object": obj, "field": field, "label": norm(r.get("Field Label")),
                "sheet_row": r.get("_SheetRow"), "source": r.get("_picklistSource") or "H",
                "old": src_text, "entries": resolved}
        plans.append(plan)
        for i, e in enumerate(resolved):
            if e[2] == "deepl":
                pending.append((plan, resolved, i))

    if pending:
        # Translate each distinct segment once so a term (出荷実績, しまむら) gets
        # the same English in every value of every field.
        seg_lists = [split_segments(plan["entries"][i][0]) for plan, _r, i in pending]
        unique = sorted({s for segs in seg_lists for s in segs if not s.isascii()})
        memory = dict(zip(unique, translate(unique))) if unique else {}
        for (plan, resolved, i), segs in zip(pending, seg_lists):
            en = " ".join(memory.get(s, s) for s in segs)
            resolved[i][1] = to_api_name(en)
            resolved[i].append(en)

    out = []
    for plan in plans:
        used: dict[str, int] = {}
        for e in plan["entries"]:
            if e[2] in ("sheet", "org", "ascii"):
                used[e[1].lower()] = used.get(e[1].lower(), 0) + 1
        for e in plan["entries"]:
            if e[2] != "deepl":
                continue
            if not e[1]:
                e[1], e[2] = e[0], "deepl-empty"
                continue
            base, n = e[1], 1
            while e[1].lower() in used:
                n += 1
                e[1] = f"{base}{n}"
            used[e[1].lower()] = 1
        new_text = format_entries([(e[0], e[1]) for e in plan["entries"]])
        if new_text == format_entries(parse_entries(plan["old"])):
            continue
        plan["new"] = new_text
        out.append(plan)
    return out


def apply_to_rows(rows: list[dict], proposals: list[dict]) -> None:
    for p in proposals:
        p["row"][COL_H_KEY] = p["new"]
        p["row"]["_picklistApiGenerated"] = True


def deepl_translate():
    from mcp_deepl import DeepLProvider
    provider = DeepLProvider()
    provider.preflight()
    return provider


def main() -> int:
    ap = argparse.ArgumentParser(description="Propose ASCII picklist value API names via DeepL")
    ap.add_argument("--rows", default="temp_updates.json")
    ap.add_argument("--org", default="")
    ap.add_argument("--out", default=".build/picklist_api_preview.json")
    args = ap.parse_args()

    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    objs = sorted({norm(r.get("Object API Name")) for r in rows if r.get("Object API Name")})
    org_vals = org_picklist_values(objs, args.org) if args.org else {}
    provider = deepl_translate()
    try:
        proposals = propose(rows, org_vals, provider.translate_batch)
    finally:
        provider.close()

    print("=" * 72)
    print("  PICKLIST VALUE API NAMES (DeepL)  — preview, nothing written")
    print("=" * 72)
    for p in proposals:
        print(f"\n  row {p['sheet_row']}  {p['label']} ({p['field']})  "
              f"read col {p['source']} → write col H")
        for e in p["entries"]:
            tag = {"deepl": "DeepL", "org": "org", "sheet": "sheet", "ascii": "kept",
                   "deepl-empty": "NO ASCII — label kept"}[e[2]]
            extra = f"   ← '{e[3]}'" if len(e) > 3 else ""
            print(f"    {e[0]:40} → {e[1]:40} [{tag}]{extra}")
    if not proposals:
        print("  (no changes — every picklist value already has an API name)")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(
        [{k: v for k, v in p.items() if k != "row"} for p in proposals],
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  preview → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
