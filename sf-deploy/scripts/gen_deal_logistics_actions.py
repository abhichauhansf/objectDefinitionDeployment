#!/usr/bin/env python3
"""
gen_deal_logistics_actions.py — ERPPJ-1032: 物流情報 sub-tabs on the Deal page.

The 物流情報 fields live on TI_Fnt_ExportImportRelatedInfo__c (輸出入関連情報), not
on Deal, so a Dynamic-Forms section cannot show them. Each section is instead a
Related Record component (console:relatedRecord) whose quick action lists
exactly that section's fields. This script emits:

  quickActions/TI_Fnt_ExportImportRelatedInfo__c.TI_Fnt_DealLogi<Key>_Action.quickAction-meta.xml
      one Update action per section (two columns, sheet row order)
  --spec JSON  {"販売情報": [[subtab, [[section, action], ...]], ...], "購買情報": [...]}
      consumed by gen_deal_review_page.py --logistics-spec
  --report JSON  placed / skipped fields, assumptions

Placement source: sheet 「輸出入関連情報」 of the review workbook, columns
AB 配置タブ / AC サブタブ / AD 配置セクション. Multi-line cells pair by line
(line i of AB goes with line i of AC and AD). Only lines whose AB is 物流情報 /
物流情報タブ are handled here. The sub-tab / section layout follows the
「コメント」 sheet mock-up; the sheet does not say sales vs purchase, so
貨物・荷姿 and 書類指示 are mirrored on both sides.

New actions are created instead of editing the existing
TI_Fnt_DealScreenInput*_Action ones: the original (backup) record page still
uses those.
"""
from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from collections import OrderedDict
from pathlib import Path

import openpyxl

OBJ = "TI_Fnt_ExportImportRelatedInfo__c"
LOOKUP = "TI_Fnt_Deal__c.TI_Fnt_ExportImportRelatedInfo__c"
COL = dict(no=0, label=2, api=3, type=4, tab=27, sub=28, sec=29, is_delete=32, wip=33)
FIRST_ROW = 11
LOGI_TABS = {"物流情報", "物流情報タブ"}

# (sub-tab, section) -> action key; order = display order within the sub-tab
SECTIONS = OrderedDict([
    (("基本情報", "船積補足情報"), "ShipSupplement"),
    (("基本情報", "AIR用追加項目"), "AirAdditional"),
    (("貨物・荷姿", "搬入時荷姿"), "PackingOnArrival"),
    (("貨物・荷姿", "Shipping Mark"), "ShippingMark"),
    (("書類指示", "船積書類関連"), "ShippingDocs"),
    (("書類指示", "物流部へ依頼事項"), "LogisticsRequest"),
    (("書類指示", "補助項目"), "DocAuxiliary"),
    (("輸入通関依頼情報", "輸入情報"), "ImportInfo"),
    (("輸入通関依頼情報", "通関形態"), "ClearanceType"),
])
# mock-up name wins where the sheet spells a section differently
SEC_ALIAS = {"物流部への依頼事項": "物流部へ依頼事項"}
# rows with a sub-tab but no section: the 梱包後荷姿数量 rows sit next to 梱包後荷姿
BLANK_SEC_DEFAULT = {"貨物・荷姿": "搬入時荷姿"}

LAYOUT = OrderedDict([
    ("販売情報", ["基本情報", "貨物・荷姿", "書類指示"]),
    ("購買情報", ["輸入通関依頼情報", "貨物・荷姿", "書類指示"]),
])
READONLY_TYPES = {"Summary", "AutoNumber"}


def norm(v) -> str:
    return str(v or "").strip()


def lines(v) -> list[str]:
    return [s.strip() for s in norm(v).split("\n")]


def org_fields(obj_file: Path) -> dict[str, dict]:
    ns = "{http://soap.sforce.com/2006/04/metadata}"
    out = {}
    for f in ET.parse(obj_file).getroot().findall(f"{ns}fields"):
        out[f.findtext(f"{ns}fullName")] = {"type": f.findtext(f"{ns}type") or "",
                                            "formula": bool(f.findtext(f"{ns}formula"))}
    return out


def read_placements(xlsx: Path, fields: dict, rep: dict):
    ws = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)["輸出入関連情報"]
    placed = OrderedDict((k, []) for k in SECTIONS)
    for r in ws.iter_rows(min_row=FIRST_ROW, values_only=True):
        if norm(r[COL["no"]]).startswith("END["):
            break
        api, label = norm(r[COL["api"]]), norm(r[COL["label"]])
        if not label:
            continue
        tabs, subs, secs = lines(r[COL["tab"]]), lines(r[COL["sub"]]), lines(r[COL["sec"]])
        hits = [i for i, t in enumerate(tabs) if t in LOGI_TABS]
        if not hits:
            continue
        no = norm(r[COL["no"]])
        if norm(r[COL["wip"]]).upper() in {"TRUE", "X"}:
            rep["skipped"].append({"no": no, "label": label, "api": api, "why": "WIP"})
            continue
        if norm(r[COL["is_delete"]]).upper() == "TRUE":
            rep["skipped"].append({"no": no, "label": label, "api": api, "why": "IsDelete"})
            continue
        if api not in fields:
            rep["skipped"].append({"no": no, "label": label, "api": api, "why": "not in org"})
            continue
        for i in hits:
            sub = subs[i] if i < len(subs) else ""
            sec = SEC_ALIAS.get(secs[i] if i < len(secs) else "", secs[i] if i < len(secs) else "")
            if not sec and sub in BLANK_SEC_DEFAULT:
                sec = BLANK_SEC_DEFAULT[sub]
                rep["assumptions"].append(f"No.{no} {label}: no section on the sheet → 「{sec}」")
            key = (sub, sec)
            if key not in SECTIONS:
                rep["skipped"].append({"no": no, "label": label, "api": api,
                                       "why": f"unknown sub-tab/section {sub}/{sec}"})
                continue
            if api not in placed[key]:
                placed[key].append(api)
    return placed


def action_xml(label: str, apis: list[str], fields: dict) -> str:
    ns = "http://soap.sforce.com/2006/04/metadata"
    ET.register_namespace("", ns)
    q = lambda t: f"{{{ns}}}{t}"
    root = ET.Element(q("QuickAction"))
    ET.SubElement(root, q("label")).text = label
    ET.SubElement(root, q("optionsCreateFeedItem")).text = "false"
    lay = ET.SubElement(root, q("quickActionLayout"))
    ET.SubElement(lay, q("layoutSectionStyle")).text = "TwoColumnsLeftToRight"
    cols = [ET.SubElement(lay, q("quickActionLayoutColumns")) for _ in range(2)]
    for i, api in enumerate(apis):
        it = ET.SubElement(cols[i % 2], q("quickActionLayoutItems"))
        ET.SubElement(it, q("emptySpace")).text = "false"
        ET.SubElement(it, q("field")).text = api
        f = fields[api]
        ro = f["formula"] or f["type"] in READONLY_TYPES
        ET.SubElement(it, q("uiBehavior")).text = "Readonly" if ro else "Edit"
    ET.SubElement(root, q("type")).text = "Update"
    ET.indent(root, "    ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--object-file", required=True, help=f"retrieved {OBJ}.object (mdapi)")
    ap.add_argument("--out-dir", default="force-app/main/default")
    ap.add_argument("--spec", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    fields = org_fields(Path(args.object_file))
    rep = {"skipped": [], "assumptions": [], "actions": []}
    placed = read_placements(Path(args.xlsx), fields, rep)

    qa_dir = Path(args.out_dir) / "quickActions"
    qa_dir.mkdir(parents=True, exist_ok=True)
    action_of = {}
    for (sub, sec), key in SECTIONS.items():
        apis = placed[(sub, sec)]
        if not apis:
            rep["assumptions"].append(f"{sub} > {sec}: no fields on the sheet → section omitted")
            continue
        name = f"TI_Fnt_DealLogi{key}_Action"
        label = f"成約画面_物流_{sec}"
        (qa_dir / f"{OBJ}.{name}.quickAction-meta.xml").write_text(action_xml(label, apis, fields),
                                                                  encoding="utf-8")
        action_of[(sub, sec)] = f"{OBJ}.{name}"
        rep["actions"].append({"action": f"{OBJ}.{name}", "label": label, "subtab": sub,
                               "section": sec, "fields": apis})

    spec = OrderedDict()
    for top, subs in LAYOUT.items():
        spec[top] = [[sub, [[sec, action_of[(s, sec)]] for (s, sec) in SECTIONS
                            if s == sub and (s, sec) in action_of]] for sub in subs]
    Path(args.spec).write_text(json.dumps({"lookupFieldName": LOOKUP, "tabs": spec},
                                          ensure_ascii=False, indent=1), encoding="utf-8")
    Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")

    for a in rep["actions"]:
        print(f"{len(a['fields']):3d}  {a['subtab']} > {a['section']}  ({a['action'].split('.')[1]})")
    print(f"skipped {len(rep['skipped'])}; assumptions {len(rep['assumptions'])}")
    for s in rep["skipped"]:
        print(f"   skip No.{s['no']} {s['label']} ({s['api']}): {s['why']}")
    for a in rep["assumptions"]:
        print(f"   assume {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
