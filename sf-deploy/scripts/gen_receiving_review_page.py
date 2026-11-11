#!/usr/bin/env python3
"""
gen_receiving_review_page.py — ERPPJ-1055: build an UNACTIVATED copy of the
Receiving (輸入・入庫管理) record page from the customer's screen-review workbook
(2026/9/17), plus copies of the two compact layouts.

Reuses the XML builder from gen_deal_review_page.py (ERPPJ-1032). Differences:
  * target tree comes from the コメント sheet drawing + the client's answers on
    the Jira ticket (see TREE below),
  * placement (sheet 「輸入・入庫管理」 AB/AC/AD) is read from a rows JSON that
    was fingerprint-checked against the live Google Sheet,
  * the 入庫明細 grid tabset keeps its tabs and gains 支払情報 / 為替・前払金,
  * the highlights panel loses the 注文書 button (CreateOrderReport),
  * SAP report links go at the top of the right sidebar,
  * the 入庫明細 Raysheet page is swapped for the duplicated one.

Nothing here activates the page or changes a primary compact layout: the
CustomObject is never emitted, so no actionOverrides / compactLayoutAssignment
can change.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from collections import OrderedDict
from pathlib import Path
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gen_deal_review_page as g  # noqa: E402
from gen_deal_review_page import q, new_node, Builder  # noqa: E402

g.GEN_PREFIX = "erp1055_"

OLD_RAYSHEET_PAGE = "a2DBK00000QSklh2AD"          # current 入庫明細 RaysheetPage (do not modify)
SAP_LINK = ("https://dev-ti-btp.launchpad.cfapps.jp20.hana.ondemand.com/site?"
            "siteId=0d5037fb-de2c-4f43-b438-e6b6ad5e5c78#call_transaction-YAFI333?"
            "sap-ui-app-id-hint=sap_s4hana_proto_8EDBF1EAC49D6190AB9E8D903DE54E28")
SAP_LINK_TEXTS = ["SAPレポート（未決済/決済済み）（SAPへ遷移）", "SAPレポート（前払金）（SAPへ遷移）"]

# ---- target tree (drawing on the コメント sheet; empty sections are intended) ----
def sec_node(*sections):
    n = new_node()
    for s in sections:
        n["sections"][s] = []
    return n


def tabs_node(**subs):
    n = new_node()
    for k, v in subs.items():
        n["children"][k] = v
    return n


def build_tree():
    """Client answer 2026-10-07: layer 1 = 購買基本情報 / システム管理・監査情報 / その他 / 取消;
    layer 2 of 購買基本情報 = 購買情報 / 物流情報 / 帳票出力項目."""
    t = OrderedDict()
    kihon = new_node()
    kihon["children"]["購買情報"] = new_node()
    kihon["children"]["購買情報"]["children"]["メイン項目"] = sec_node(
        "基本情報", "基本情報（輸入・海外直送仕入）", "基本情報（海外直送・海外別国仕入）")
    kihon["children"]["購買情報"]["children"]["補助項目"] = sec_node("補助項目", "Shipping Mark", "備考")
    logi = new_node()
    logi["children"]["輸入通関依頼情報"] = sec_node("輸入情報", "通関形態")
    logi["children"]["貨物・荷姿"] = sec_node("商品情報", "本船情報")
    logi["children"]["書類提示"] = sec_node("船積書類関連", "物流部への依頼事項")
    kihon["children"]["物流情報"] = logi
    kihon["children"]["帳票出力項目"] = sec_node("注文書", "Purchase Order")
    t["購買基本情報"] = kihon
    t["システム管理・監査情報"] = new_node()
    t["システム管理・監査情報"]["children"]["システム管理"] = sec_node("システム管理")
    t["システム管理・監査情報"]["children"]["監査情報"] = sec_node("監査情報")
    t["その他"] = sec_node("経営企画管理", "備考")
    t["取消"] = sec_node("取消情報")
    return t


# grid tabset additions (tab -> sections)
GRID_NEW_TABS = OrderedDict([
    ("支払情報", ["支払依頼情報", "SAP連携情報"]),
    ("為替・前払金", ["社内予約", "前払金"]),
])

# sheet (AB, AC, AD) -> target path.  ("T", top, sub, section) = main tabset; ("G", tab, section) = grid tabset
ROUTE = {
    ("購買基本情報", "メイン項目", "基本情報"): ("T", ("購買基本情報", "購買情報", "メイン項目"), "基本情報"),
    ("購買基本情報", "補助項目", "備考"): ("T", ("購買基本情報", "購買情報", "補助項目"), "備考"),
    ("物流情報（輸入）", "輸入通関依頼情報", "輸入情報"): ("T", ("購買基本情報", "物流情報", "輸入通関依頼情報"), "輸入情報"),
    ("物流情報（輸入）", "輸入通関依頼情報", "通関形態"): ("T", ("購買基本情報", "物流情報", "輸入通関依頼情報"), "通関形態"),
    ("物流情報（輸入）", "貨物・荷姿", "商品情報"): ("T", ("購買基本情報", "物流情報", "貨物・荷姿"), "商品情報"),
    ("物流情報（輸入）", "貨物・荷姿", "本船情報"): ("T", ("購買基本情報", "物流情報", "貨物・荷姿"), "本船情報"),
    # sheet says 書類指示 / 書類指示; drawing says 書類提示 with sections 船積書類関連 / 物流部への依頼事項
    ("物流情報（輸入）", "書類指示", "書類指示"): ("T", ("購買基本情報", "物流情報", "書類提示"), "船積書類関連"),
    ("システム管理・監査情報", "システム管理", ""): ("T", ("システム管理・監査情報", "システム管理"), "システム管理"),
    ("システム管理・監査情報", "監査情報", ""): ("T", ("システム管理・監査情報", "監査情報"), "監査情報"),
    ("支払情報", "", "支払依頼情報"): ("G", "支払情報", "支払依頼情報"),
    ("支払情報", "", "SAP連携情報"): ("G", "支払情報", "SAP連携情報"),
    ("支払情報", "", "取消情報"): ("T", ("取消",), "取消情報"),
    ("為替・前払金", "", "社内予約"): ("G", "為替・前払金", "社内予約"),
    ("為替・前払金", "", "前払金"): ("G", "為替・前払金", "前払金"),
}
# client answer: the ver1 取消情報 fields go to the 取消 tab (sheet puts these two in 支払依頼情報)
TO_CANCEL = {"TI_Fnt_ProcessingReason__c", "TI_Fnt_ProcessingReasonFreeText__c"}
# 明細合計 section, in the order of the comment on the sheet
TOTALS = ["TI_Fnt_TotalPaidQuantity__c", "TI_Fnt_TotalPaidAmount__c",
          "TI_Fnt_TotalUnpaidQuantity__c", "TI_Fnt_TotalQuantityPaidUnpaid__c"]
STANDARD_PLACEABLE = g.STANDARD_PLACEABLE


def truthy(v):
    return str(v or "").strip().lower() in ("true", "x")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="rows.json (live-verified sheet placement)")
    ap.add_argument("--orig", required=True, help="backed-up current Receiving page (mdapi .flexipage)")
    ap.add_argument("--org-fields", required=True)
    ap.add_argument("--raysheet-page", required=True, help="ID of the duplicated 入庫明細 RaysheetPage")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--page-dev", default="TI_Fnt_Receiving_Record_Page_v2")
    ap.add_argument("--page-label", default="輸入・入庫管理 レコードページ v2")
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    issues = []

    def add(sev, check, msg):
        issues.append(dict(severity=sev, check=check, message=msg))

    org_fields = set(json.loads(Path(args.org_fields).read_text()))
    rows = json.loads(Path(args.rows).read_text())

    tree = build_tree()
    totals_by_api, grid = {}, OrderedDict((k, OrderedDict((s, []) for s in v)) for k, v in GRID_NEW_TABS.items())
    placed = []
    for x in rows:
        api = (x.get("api") or "").strip()
        ab, ac, ad = (x.get(k) or "" for k in ("AB", "AC", "AD"))
        ab, ac, ad = ab.strip(), ac.strip(), ad.strip()
        if not api or not ab:
            continue
        if truthy(x.get("AH")) or truthy(x.get("AG")):
            continue
        if api.endswith("__c") and api not in org_fields:
            add("WARN", "field.org", f"No.{x['no']} {x['label']} ({api}) is on the sheet but no longer in the org "
                                     "(deleted) — not placed")
            continue
        if not api.endswith("__c") and api not in STANDARD_PLACEABLE:
            add("INFO", "field.standard", f"No.{x['no']} {x['label']} ({api}) standard field — skipped")
            continue
        if api in TOTALS:
            totals_by_api[api] = x
            continue
        route = ROUTE.get((ab, ac, ad))
        if api in TO_CANCEL:
            route = ("T", ("取消",), "取消情報")
        if route is None:
            add("ERROR", "placement.unknown", f"No.{x['no']} {x['label']}: unmapped placement {(ab, ac, ad)}")
            continue
        if route[0] == "T":
            _, path, sec = route
            node = tree[path[0]]
            for step in path[1:]:
                node = node["children"][step]
            node["sections"][sec].append(api)
        else:
            _, tab, sec = route
            grid[tab][sec].append(api)
        placed.append(api)
    totals = [a for a in TOTALS if a in totals_by_api]
    if len(totals) != len(TOTALS):
        add("ERROR", "totals", f"明細合計 fields missing on the sheet: {set(TOTALS) - set(totals)}")
    placed += totals

    # ---------------------------------------------------------------- page XML
    orig_root = ET.parse(args.orig).getroot()
    root = copy.deepcopy(orig_root)
    regs = g.regions(root)
    main_reg = regs["main"]
    main_items = main_reg.findall(q("itemInstances"))
    tabset1, tabset2 = main_items[0], main_items[1]

    old = g.old_tabs(regs, tabset1)
    imp_items, imp_vis = old["輸入通関依頼情報"]
    keep_names = {"console:relatedRecord", "gcss:raysheetpageLwc"}
    carried = [i for i in imp_items
               if i.find(f"{q('componentInstance')}/{q('componentName')}") is not None
               and i.find(f"{q('componentInstance')}/{q('componentName')}").text in keep_names]
    carried_ids = [g.item_identifier(i) for i in carried]

    b = Builder(root)
    LOGI = ("購買基本情報", "物流情報")
    tree["購買基本情報"]["children"]["物流情報"]["visibility"] = imp_vis

    def render_node(node, path):
        items = [b.section(lbl, [p["api"] for p in ps]) for lbl, ps in node["sections"].items()]
        if node["children"]:
            items.append(b.tabset([(t, render_node(ch, path + (t,)), ch["visibility"])
                                   for t, ch in node["children"].items()]))
        if path == LOGI:
            items += carried            # related-record components + Raysheets, below the sub-tabs
        return items

    # fill sections of the tree nodes
    def fill(node):
        for s, apis in list(node["sections"].items()):
            node["sections"][s] = [dict(api=a) for a in apis]
        for ch in node["children"].values():
            fill(ch)
    for n in tree.values():
        fill(n)

    # drop the facets of the old first tabset (carried components hold no facet refs)
    dead = g.reachable_facets(regs, tabset1)
    keep = set()
    for it in main_items[1:]:
        keep |= g.reachable_facets(regs, it)
    for it in regs["header"].findall(q("itemInstances")) + regs["sidebar"].findall(q("itemInstances")):
        keep |= g.reachable_facets(regs, it)
    for name in dead - keep:
        root.remove(regs[name])

    idx = list(main_reg).index(tabset1)
    main_reg.remove(tabset1)
    new_tabset = b.tabset([(t, render_node(n, (t,)), n["visibility"]) for t, n in tree.items()])
    main_reg.insert(idx, new_tabset)
    main_reg.insert(idx + 1, b.section("明細合計", totals))

    # --- grid tabset: swap Raysheet page, add tabs
    swapped = 0
    grid_regions = [regs[n] for n in g.reachable_facets(regs, tabset2)]
    for scope in [tabset2] + grid_regions:
        for p in scope.iter(q("componentInstanceProperties")):
            nm, val = p.find(q("name")), p.find(q("value"))
            if nm.text == "raysheetPage" and val is not None and val.text == OLD_RAYSHEET_PAGE:
                val.text = args.raysheet_page
                swapped += 1
    if swapped != 1:
        add("ERROR", "raysheet.swap", f"expected exactly one 入庫明細 raysheet reference, swapped {swapped}")
    tabs_facet = g.props_of(tabset2.find(q("componentInstance")))["tabs"]
    for title, secs in grid.items():
        body = b.facet([b.section(s, apis) for s, apis in secs.items()])
        regs[tabs_facet].append(b.component("flexipage:tab", [("body", body), ("title", title)], "customTab"))
    # keep <name>/<type> last inside the region
    reg = regs[tabs_facet]
    tail = [e for e in reg if e.tag in (q("name"), q("type"))]
    for e in tail:
        reg.remove(e)
    for e in tail:
        reg.append(e)

    # --- highlights panel: drop 注文書 button
    removed = 0
    for vl in list(root.iter(q("valueList"))):
        for li in list(vl.findall(q("valueListItems"))):
            v = li.find(q("value"))
            if v is not None and v.text == "TI_Fnt_Receiving__c.CreateOrderReport":
                vl.remove(li)
                removed += 1
    if removed != 1:
        add("ERROR", "highlights.action", f"expected to remove one CreateOrderReport action, removed {removed}")

    # --- sidebar: SAP report links at the top
    html = "".join(
        f'<p><a href="{SAP_LINK}" rel="noopener noreferrer" target="_blank" '
        f'style="color: rgb(1, 118, 211); font-size: 18px;">{t}</a></p>' for t in SAP_LINK_TEXTS)
    link_item = b.component("flexipage:richText", [("decorate", "true"), ("richTextValue", html)],
                            "flexipage_richText")
    regs["sidebar"].insert(0, link_item)

    # keep FlexiPage element order
    tail = [e for e in root if e.tag != q("flexiPageRegions")]
    for e in tail:
        root.remove(e)
    for e in tail:
        root.append(e)
    root.find(q("masterLabel")).text = args.page_label

    out = Path(args.out_dir)
    g.write_xml(root, out / "flexipages" / f"{args.page_dev}.flexipage-meta.xml")

    # ------------------------------------------------------- compact layouts
    cls = {
        "TI_Fnt_Domestic_Compact_Layout_v2": ("入庫コンパクトレイアウト v2",
                                       ["Name", "TI_Fnt_GoodsReceiptNo__c", "TI_Fnt_SAPExId__c",
                                        "TI_Fnt_DocumentType__c", "TI_Fnt_Subject__c",
                                        "TI_Fnt_DepartmentCode__c", "TI_Fnt_PaymentRequestNo__c"]),
        "TI_Fnt_Import_Compact_Layout_v2": ("輸入コンパクトレイアウト v2",
                                     ["Name", "TI_Fnt_ImportCustomsRequestNo__c", "TI_Fnt_GoodsReceiptNo__c",
                                      "TI_Fnt_SAPExId__c", "TI_Fnt_DocumentType__c", "TI_Fnt_Subject__c",
                                      "TI_Fnt_DepartmentCode__c", "TI_Fnt_PaymentRequestNo__c"]),
    }
    for dev, (label, fields) in cls.items():
        for f in fields:
            if f.endswith("__c") and f not in org_fields:
                add("ERROR", "compact.field", f"{dev}: {f} not in org")
        g.write_xml(g.compact_layout_xml(dev, fields, label),
                    out / "objects" / "TI_Fnt_Receiving__c" / "compactLayouts" / f"{dev}.compactLayout-meta.xml")

    # ------------------------------------------------------------- reporting
    orig_fields = {e.text.split(".", 1)[1] for e in orig_root.iter(q("fieldItem"))}
    for a in sorted(orig_fields - set(placed)):
        add("INFO", "placement.dropped", f"{a} is on the current page but not placed on the copy (no placement on the sheet)")
    dup = {a for a in placed if placed.count(a) > 1}
    for a in sorted(dup):
        add("WARN", "placement.duplicate", f"{a} placed more than once")
    counts = {"ERROR": 0, "WARN": 0, "INFO": 0}
    for i in issues:
        counts[i["severity"]] += 1
    summary = g.tree_summary({"sections": OrderedDict(), "components": [], "visibility": None,
                              "children": OrderedDict(tree)})
    for tab, secs in grid.items():
        summary.append(f"[grid tab] {tab}: " + ", ".join(f"{s} {len(a)}" for s, a in secs.items()))
    summary.append(f"明細合計 {len(totals)} fields; carried components {carried_ids}")
    rep = dict(counts=counts, items=issues, issues=issues, tree=summary,
               placed_fields=len(placed), unique_fields=len(set(placed)))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(summary))
    print(f"\nplaced {len(placed)} ({len(set(placed))} unique); counts={counts}")
    for i in issues:
        print(f"  [{i['severity']}] {i['check']}: {i['message']}")
    return 1 if counts["ERROR"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
