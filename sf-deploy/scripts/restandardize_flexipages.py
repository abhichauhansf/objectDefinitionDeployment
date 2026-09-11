#!/usr/bin/env python3
"""restandardize_flexipages.py — faithfully rebuild a SIMPLE (generator-authored)
record page from the org: extract its exact sections + ordered fields LIVE, then
re-emit the FlexiPage XML with each section split into TWO columns (per
sf-flexipage-two-column-sections.mdc). ONLY the column split (and optionally the
page DeveloperName) changes — the set/order of fields and sections is preserved
byte-for-field from what is currently on the page.

It reuses gen_record_page.build() so the emitted XML matches the known-good
2-column shape. RICH / client-authored pages (tabs, related lists, LWC, Chatter)
must NOT be passed here — this is only for our simple section-only pages.

Usage (dry-run, writes XML to out-dir but does not deploy):
  python scripts/restandardize_flexipages.py --pages DealDetail_Record_Page:TI_Fnt_DealDetail__c ...
"""
from __future__ import annotations
import argparse, json, os, re, sys, urllib.request, urllib.parse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_record_page as G
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _auth():
    a = json.load(open(os.path.join(ROOT, ".build/orgauth.json")))["result"]
    return a["instanceUrl"].rstrip("/"), a["accessToken"], a["apiVersion"]


def _qt(inst, ver, tok, q):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        f"{inst}/services/data/v{ver}/tooling/query/?q=" + urllib.parse.quote(q),
        headers={"Authorization": f"Bearer {tok}"}), timeout=90))["records"]


def _get(inst, ver, tok, path):
    return json.load(urllib.request.urlopen(urllib.request.Request(
        f"{inst}/services/data/v{ver}/{path}",
        headers={"Authorization": f"Bearer {tok}"}), timeout=90))


def fetch_md(inst, ver, tok, page_dev):
    recs = _qt(inst, ver, tok, f"SELECT Id FROM FlexiPage WHERE DeveloperName='{page_dev}'")
    if not recs:
        return None
    return _get(inst, ver, tok, f"tooling/sobjects/FlexiPage/{recs[0]['Id']}")["Metadata"]


def extract_sections(md):
    """Return (master_label, sobject, [(label,[field_api,...]), ...]) in page order.
    Fields are read from the SEC{n}_COL* facets in column then row order, which
    preserves the on-page order regardless of current column count."""
    regions = {r.get("name"): r for r in (md.get("flexiPageRegions") or [])}
    # ordered sections from the 'main' region
    sections = []
    main = next((r for r in (md.get("flexiPageRegions") or []) if r.get("type") == "Region"
                 and r.get("name") == "main"), None)
    if main is None:
        return md.get("masterLabel", ""), md.get("sobjectType", ""), []
    for it in (main.get("itemInstances") or []):
        ci = it.get("componentInstance")
        if not ci or ci.get("componentName") != "flexipage:fieldSection":
            continue
        props = {p["name"]: p["value"] for p in ci.get("componentInstanceProperties") or []}
        label = props.get("label", "")
        wrapper = props.get("columns", "")           # e.g. SEC3_WRAPPER
        pre = wrapper.replace("_WRAPPER", "")          # SEC3
        # gather column facets SEC3_COL1, SEC3_COL2, ... in numeric order
        cols = sorted([n for n in regions if re.match(rf"{re.escape(pre)}_COL\d+$", n)],
                      key=lambda n: int(n.rsplit("COL", 1)[1]))
        fields = []
        for cn in cols:
            for fit in (regions[cn].get("itemInstances") or []):
                fi = fit.get("fieldInstance")
                if fi and fi.get("fieldItem", "").startswith("Record."):
                    fields.append(fi["fieldItem"][len("Record."):])
        sections.append((label, fields))
    return md.get("masterLabel", ""), md.get("sobjectType", ""), sections


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", nargs="+", required=True,
                    help="items 'OldPageDev:Object__c[:NewPageDev]' "
                         "(omit NewPageDev to fix columns in place)")
    ap.add_argument("--out-dir", default="force-app/main/default/flexipages")
    a = ap.parse_args()

    inst, tok, ver = _auth()
    out_dir = os.path.join(ROOT, a.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    plan = []
    for item in a.pages:
        parts = item.split(":")
        old_dev, obj = parts[0], parts[1]
        new_dev = parts[2] if len(parts) > 2 else old_dev
        md = fetch_md(inst, ver, tok, old_dev)
        if md is None:
            print(f"❌ {old_dev}: not found"); continue
        label, sob, sections = extract_sections(md)
        if sob != obj:
            print(f"⚠ {old_dev}: sobjectType {sob} != {obj} (using {sob})"); obj = sob
        # report current vs new columns
        before = []
        for lbl, fields in sections:
            before.append(f"{lbl[:10]}({len(fields)})")
        root = G.build(new_dev, label, obj, sections, desired_cols=2)
        G.indent(root)
        out_file = os.path.join(out_dir, f"{new_dev}.flexipage-meta.xml")
        with open(out_file, "wb") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=False,
                                       short_empty_elements=False)
            f.write(b"\n")
        # verify 2-col in emitted XML
        xml = open(out_file, encoding="utf-8").read()
        ncol_facets = len(re.findall(r"<name>SEC\d+_COL\d+</name>", xml))
        nsec = len(sections)
        rename = f"  RENAME -> {new_dev}" if new_dev != old_dev else "  (in place)"
        print(f"✓ {old_dev:<46} sections={nsec} fields={sum(len(f) for _,f in sections)} "
              f"colFacets={ncol_facets}{rename}")
        print(f"    sections: {', '.join(before)}")
        plan.append({"old": old_dev, "new": new_dev, "object": obj, "file": out_file})
    json.dump(plan, open(os.path.join(ROOT, ".build/flexi_restd_plan.json"), "w"), indent=1)
    print(f"\nwrote {len(plan)} page(s); plan -> .build/flexi_restd_plan.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
