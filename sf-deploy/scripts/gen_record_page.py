#!/usr/bin/env python3
"""
gen_record_page.py — Build a deployable Lightning Record Page (FlexiPage) per
object from the object-tab AA "section" column.

Input : .build/flexi_fields.json  (produced upstream) shaped as
          { "<Tab>": [ {"api": "...", "label": "...", "section": "..."}, ... ] }
        plus a per-object set of field API names that actually EXIST in the org
        (only those are placed on the page).

Output: force-app/main/default/flexipages/<PageDevName>.flexipage-meta.xml

Each distinct AA section becomes a flexipage:fieldSection holding its fields in
sheet order, split across TWO columns by default (per
sf-flexipage-two-column-sections.mdc; a 1-field section stays 1 column). Uses the
recordHomeTemplateDesktop template and the same nested-facet structure the
existing generate_flexipage_xml.py emits.
"""
from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

# Fields that can NEVER be placed on a Lightning record page as a fieldInstance.
# OwnerId is a real field but the Lightning runtime rejects it as a page field
# ("couldn't load Record.OwnerId") — ownership shows in the highlights panel.
HARD_DISALLOWED = frozenset({
    "Id", "IsDeleted", "SystemModstamp",
    "LastViewedDate", "LastReferencedDate", "LastActivityDate",
    "OwnerId",
})
# Standard fields that ARE placeable on a record page as a fieldInstance and
# ALWAYS exist in the org (so they skip the custom-field org-presence check).
# NOTE: system audit fields (CreatedDate/CreatedById/LastModifiedDate/
# LastModifiedById) and OwnerId are NOT reliably placeable as page fields — the
# Lightning runtime rejects them ("couldn't load Record.CreatedDate"); they
# surface via the highlights panel / record-detail component instead. Only place
# Name (and RecordTypeId where present).
STANDARD_PLACEABLE = frozenset({
    "Name", "RecordTypeId",
})
# Non-custom fields that exist but are not placeable → treated like HARD_DISALLOWED
# so a sheet section referencing them doesn't break the deploy.
STANDARD_NOT_PLACEABLE = frozenset({
    "CreatedDate", "CreatedById", "LastModifiedDate", "LastModifiedById",
})


def qn(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def st(parent: ET.Element, tag: str, text: str) -> ET.Element:
    el = ET.SubElement(parent, qn(tag))
    el.text = "" if text is None else str(text)
    return el


def indent(elem: ET.Element, level: int = 0):
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        for c in elem:
            indent(c, level + 1)
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = pad
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad


def field_identifier(field_api: str, used: set) -> str:
    base = "Record" + re.sub(r"[^A-Za-z0-9]", "", field_api) + "Field"
    cand = base
    i = 2
    while cand in used:
        cand = f"{base}{i}"
        i += 1
    used.add(cand)
    return cand


def build(page_dev: str, master_label: str, sobject: str,
          sections: list[tuple[str, list[str]]], desired_cols: int = 1) -> ET.Element:
    root = ET.Element(qn("FlexiPage"))

    main = ET.SubElement(root, qn("flexiPageRegions"))
    facets: list[ET.Element] = []
    used_ids: set = set()

    MAX_PER_COL = 100  # Salesforce hard cap: flexipage:column body max 100 items
    for idx, (sec_label, fields) in enumerate(sections, 1):
        wrapper_id = f"SEC{idx}_WRAPPER"
        # Use the desired column count, but always enough columns so no single
        # column exceeds the Salesforce cap. A 1-field section stays 1 column.
        cap_cols = max(1, -(-len(fields) // MAX_PER_COL))  # ceil to respect cap
        want = desired_cols if len(fields) > 1 else 1
        ncols = max(want, cap_cols)
        chunk = -(-len(fields) // ncols)  # balanced chunk size
        col_chunks = [fields[i:i + chunk] for i in range(0, len(fields), chunk)] or [[]]

        # fieldSection component instance in the main region
        inst = ET.SubElement(main, qn("itemInstances"))
        comp = ET.SubElement(inst, qn("componentInstance"))
        p1 = ET.SubElement(comp, qn("componentInstanceProperties"))
        st(p1, "name", "columns")
        st(p1, "value", wrapper_id)
        p2 = ET.SubElement(comp, qn("componentInstanceProperties"))
        st(p2, "name", "label")
        st(p2, "value", sec_label)
        st(comp, "componentName", "flexipage:fieldSection")
        st(comp, "identifier", f"fieldSection_{idx}")

        # wrapper facet -> one flexipage:column per chunk, each pointing at its facet
        wrap = ET.Element(qn("flexiPageRegions"))
        for ci, chunk_fields in enumerate(col_chunks, 1):
            col_id = f"SEC{idx}_COL{ci}"
            # column facet (holds the fields)
            col_facet = ET.Element(qn("flexiPageRegions"))
            for f in chunk_fields:
                fi_inst = ET.SubElement(col_facet, qn("itemInstances"))
                fi = ET.SubElement(fi_inst, qn("fieldInstance"))
                st(fi, "fieldItem", f"Record.{f}")
                st(fi, "identifier", field_identifier(f, used_ids))
            st(col_facet, "name", col_id)
            st(col_facet, "type", "Facet")
            facets.append(col_facet)

            col_item = ET.SubElement(wrap, qn("itemInstances"))
            col_comp = ET.SubElement(col_item, qn("componentInstance"))
            bp = ET.SubElement(col_comp, qn("componentInstanceProperties"))
            st(bp, "name", "body")
            st(bp, "value", col_id)
            st(col_comp, "componentName", "flexipage:column")
            st(col_comp, "identifier", f"col_SEC{idx}_{ci}")
        st(wrap, "name", wrapper_id)
        st(wrap, "type", "Facet")
        facets.append(wrap)

    st(main, "name", "main")
    st(main, "type", "Region")

    for fc in facets:
        root.append(fc)

    st(root, "masterLabel", master_label)
    st(root, "sobjectType", sobject)
    tmpl = ET.SubElement(root, qn("template"))
    st(tmpl, "name", "flexipage:recordHomeTemplateDesktop")
    st(root, "type", "RecordPage")
    return root


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fields", default=".build/flexi_fields.json")
    ap.add_argument("--present", default=".build/flexi_present.json",
                    help="JSON {tab: [field_api,...]} of org-present fields")
    ap.add_argument("--out-dir", default="force-app/main/default/flexipages")
    ap.add_argument("--cols", type=int, default=2,
                    help="columns per section (default 2 per "
                         "sf-flexipage-two-column-sections.mdc; do not lower to 1)")
    ap.add_argument("--only", default="", help="comma-separated tabs to (re)generate")
    ap.add_argument("--map", default=json.dumps({
        "Deal": ["TI_Fnt_Deal__c", "成約", "Deal_Record_Page"],
        "Shipping": ["TI_Fnt_Shipping__c", "輸出・出庫管理", "Shipping_Record_Page"],
        "Receiving": ["TI_Fnt_Receiving__c", "輸入・入庫管理", "Receiving_Record_Page"],
    }, ensure_ascii=False))
    args = ap.parse_args()

    sheet = json.load(open(args.fields, encoding="utf-8"))
    present = json.load(open(args.present, encoding="utf-8"))
    mp = json.loads(args.map)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    only = {t.strip() for t in args.only.split(",") if t.strip()}
    for tab, (sobject, label, page_dev) in mp.items():
        if only and tab not in only:
            continue
        rows = sheet.get(tab, [])
        present_set = set(present.get(tab, []))
        # group fields by section in first-appearance order, keeping only
        # org-present, non-disallowed fields
        order: list[str] = []
        buckets: dict[str, list[str]] = {}
        dropped = 0
        for r in rows:
            api = r["api"]
            sec = r.get("section") or "(blank)"
            # never-placeable system fields (incl. audit/owner fields)
            if api in HARD_DISALLOWED or api in STANDARD_NOT_PLACEABLE:
                dropped += 1
                continue
            # custom fields must actually exist in the org; standard/placeable
            # fields (no __c) always exist, so they bypass the presence check
            if api.endswith("__c") and api not in present_set:
                dropped += 1
                continue
            if sec not in buckets:
                buckets[sec] = []
                order.append(sec)
            if api not in buckets[sec]:
                buckets[sec].append(api)
        sections = [(s, buckets[s]) for s in order if buckets[s]]
        root = build(page_dev, f"{label} レコードページ", sobject, sections, desired_cols=args.cols)
        indent(root)
        out_file = out_dir / f"{page_dev}.flexipage-meta.xml"
        with out_file.open("wb") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=False,
                                       short_empty_elements=False)
            f.write(b"\n")
        total = sum(len(v) for _, v in sections)
        print(f"✓ {out_file.name}: {len(sections)} sections, {total} fields "
              f"(dropped {dropped} not-in-org/disallowed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
