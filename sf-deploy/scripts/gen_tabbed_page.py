#!/usr/bin/env python3
"""
gen_tabbed_page.py — Build a deployable Lightning Record Page (FlexiPage) that
groups fields into TOP-LEVEL TABS (from the sheet Z "Tab / 配置タブ" column) and,
inside each tab, into SECTIONS (from the sheet AA "section / 配置セクション" column).

Input : .build/deal_tabs.json shaped as an ORDERED dict
          { "<TabLabel>": { "<SectionLabel>": ["Api__c", ...], ... }, ... }
Output: force-app/main/default/flexipages/<PageDevName>.flexipage-meta.xml

Structure (mirrors a real org tabbed page):
  main (Region)
    └ flexipage:tabset  (label=Tabs, tabs -> TABSET_TABS facet)
  TABSET_TABS (Facet)
    └ flexipage:tab x N  (title=<TabLabel>, body -> TAB{t}_BODY facet; first active)
  TAB{t}_BODY (Facet)
    └ flexipage:fieldSection x M  (columns -> SEC{g}_WRAPPER facet, label=<SectionLabel>)
  SEC{g}_WRAPPER (Facet) -> flexipage:column x C (body -> SEC{g}_COL{c})
  SEC{g}_COL{c} (Facet) -> fieldInstance x ...
"""
from __future__ import annotations
import argparse
import json
import re
from collections import OrderedDict
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
MAX_PER_COL = 100  # Salesforce hard cap: flexipage:column body max 100 items


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
    cand, i = base, 2
    while cand in used:
        cand = f"{base}{i}"; i += 1
    used.add(cand)
    return cand


def build_section(parent_region: ET.Element, facets: list, used_ids: set,
                  gidx: int, sec_label: str, fields: list[str], desired_cols: int):
    """Append one flexipage:fieldSection to parent_region; register its facets."""
    wrapper_id = f"SEC{gidx}_WRAPPER"
    cap_cols = max(1, -(-len(fields) // MAX_PER_COL))
    want = desired_cols if len(fields) > 1 else 1
    ncols = max(want, cap_cols)
    chunk = -(-len(fields) // ncols)
    col_chunks = [fields[i:i + chunk] for i in range(0, len(fields), chunk)] or [[]]

    inst = ET.SubElement(parent_region, qn("itemInstances"))
    comp = ET.SubElement(inst, qn("componentInstance"))
    p1 = ET.SubElement(comp, qn("componentInstanceProperties"))
    st(p1, "name", "columns"); st(p1, "value", wrapper_id)
    p2 = ET.SubElement(comp, qn("componentInstanceProperties"))
    st(p2, "name", "label"); st(p2, "value", sec_label)
    st(comp, "componentName", "flexipage:fieldSection")
    st(comp, "identifier", f"fieldSection_{gidx}")

    wrap = ET.Element(qn("flexiPageRegions"))
    for ci, chunk_fields in enumerate(col_chunks, 1):
        col_id = f"SEC{gidx}_COL{ci}"
        col_facet = ET.Element(qn("flexiPageRegions"))
        for f in chunk_fields:
            fi_inst = ET.SubElement(col_facet, qn("itemInstances"))
            fi = ET.SubElement(fi_inst, qn("fieldInstance"))
            st(fi, "fieldItem", f"Record.{f}")
            st(fi, "identifier", field_identifier(f, used_ids))
        st(col_facet, "name", col_id); st(col_facet, "type", "Facet")
        facets.append(col_facet)

        col_item = ET.SubElement(wrap, qn("itemInstances"))
        col_comp = ET.SubElement(col_item, qn("componentInstance"))
        bp = ET.SubElement(col_comp, qn("componentInstanceProperties"))
        st(bp, "name", "body"); st(bp, "value", col_id)
        st(col_comp, "componentName", "flexipage:column")
        st(col_comp, "identifier", f"col_SEC{gidx}_{ci}")
    st(wrap, "name", wrapper_id); st(wrap, "type", "Facet")
    facets.append(wrap)


def build(page_dev: str, master_label: str, sobject: str,
          tabs: "OrderedDict[str, OrderedDict[str, list]]", desired_cols: int = 2) -> ET.Element:
    root = ET.Element(qn("FlexiPage"))
    facets: list[ET.Element] = []
    used_ids: set = set()

    # main region holds ONLY the tabset component
    main = ET.SubElement(root, qn("flexiPageRegions"))
    ts_inst = ET.SubElement(main, qn("itemInstances"))
    ts_comp = ET.SubElement(ts_inst, qn("componentInstance"))
    tp1 = ET.SubElement(ts_comp, qn("componentInstanceProperties"))
    st(tp1, "name", "label"); st(tp1, "value", "Tabs")
    tp2 = ET.SubElement(ts_comp, qn("componentInstanceProperties"))
    st(tp2, "name", "tabs"); st(tp2, "value", "TABSET_TABS")
    st(ts_comp, "componentName", "flexipage:tabset")
    st(ts_comp, "identifier", "flexipage_tabset")
    st(main, "name", "main"); st(main, "type", "Region")

    # TABSET_TABS facet holds the flexipage:tab components
    tabs_facet = ET.Element(qn("flexiPageRegions"))
    gidx = 0
    for tnum, (tab_label, sections) in enumerate(tabs.items(), 1):
        body_id = f"TAB{tnum}_BODY"
        t_inst = ET.SubElement(tabs_facet, qn("itemInstances"))
        t_comp = ET.SubElement(t_inst, qn("componentInstance"))
        if tnum == 1:
            ap = ET.SubElement(t_comp, qn("componentInstanceProperties"))
            st(ap, "name", "active"); st(ap, "value", "true")
        bp = ET.SubElement(t_comp, qn("componentInstanceProperties"))
        st(bp, "name", "body"); st(bp, "value", body_id)
        tp = ET.SubElement(t_comp, qn("componentInstanceProperties"))
        st(tp, "name", "title"); st(tp, "value", tab_label)
        st(t_comp, "componentName", "flexipage:tab")
        st(t_comp, "identifier", f"flexipage_tab{tnum}")

        # each tab's body facet holds its fieldSections
        body_facet = ET.Element(qn("flexiPageRegions"))
        for sec_label, fields in sections.items():
            if not fields:
                continue
            gidx += 1
            build_section(body_facet, facets, used_ids, gidx, sec_label, fields, desired_cols)
        st(body_facet, "name", body_id); st(body_facet, "type", "Facet")
        facets.append(body_facet)
    st(tabs_facet, "name", "TABSET_TABS"); st(tabs_facet, "type", "Facet")
    facets.append(tabs_facet)

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
    ap.add_argument("--tabs-json", default=".build/deal_tabs.json")
    ap.add_argument("--out-dir", default="force-app/main/default/flexipages")
    ap.add_argument("--page-dev", default="Deal_Record_Page")
    ap.add_argument("--master-label", default="成約 レコードページ")
    ap.add_argument("--sobject", default="TI_Fnt_Deal__c")
    ap.add_argument("--cols", type=int, default=2)
    ap.add_argument("--tab-order", default="",
                    help="comma-separated tab labels to force ordering")
    args = ap.parse_args()

    raw = json.load(open(args.tabs_json, encoding="utf-8"))
    tabs: "OrderedDict[str, OrderedDict[str, list]]" = OrderedDict()
    order = [t.strip() for t in args.tab_order.split(",") if t.strip()] or list(raw.keys())
    for t in order:
        if t in raw:
            tabs[t] = OrderedDict(raw[t])
    # append any tabs not covered by explicit order
    for t in raw:
        if t not in tabs:
            tabs[t] = OrderedDict(raw[t])

    root = build(args.page_dev, args.master_label, args.sobject, tabs, desired_cols=args.cols)
    indent(root)
    out = Path(args.out_dir) / f"{args.page_dev}.flexipage-meta.xml"
    out.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(root).write(str(out), encoding="UTF-8", xml_declaration=True)

    ntabs = len(tabs)
    nsec = sum(len(s) for s in tabs.values())
    nflds = sum(len(f) for s in tabs.values() for f in s.values())
    print(f"✓ {out.name}: {ntabs} tabs, {nsec} sections, {nflds} fields")
    for t, secs in tabs.items():
        tot = sum(len(f) for f in secs.values())
        print(f"   TAB {t} ({tot}): " + ", ".join(f"{k}={len(v)}" for k, v in secs.items()))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
