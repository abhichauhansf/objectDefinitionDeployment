#!/usr/bin/env python3
"""
gen_deal_review_page.py — ERPPJ-1032: build an UNACTIVATED copy of the Deal (成約)
record page from the customer's screen-review workbook (v2), plus a copy of the
Deal compact layout.

Inputs
  --xlsx   the review workbook (sheet 「成約」 holds per-field placement:
           AB 配置タブ / AC サブタブ / AD 配置セクション; IsDelete AG; WIP AH)
  --orig   the backed-up live page (TI_FnT_Deal_Lightning_Page.flexipage)
  --org-fields  JSON list of the org's TI_Fnt_Deal__c custom field API names
               (Tooling CustomField, FLS-independent)

What is rebuilt
  Only the field tabs and the 合計数量・金額 section of the ``main`` region. The
  header (highlights panel + path), the 成約明細/予定諸掛 grid tabset, the
  related-list tabset and the sidebar are kept byte-for-byte. Existing Related
  Record components (輸出入関連情報 quick actions) are MOVED from the original
  page into the new tabs, so their quick actions and visibility rules are reused.

  Once the copy exists in the org, ALWAYS pass ``--base`` with a fresh retrieve
  of it: people edit the copy in App Builder (e.g. Akira added the 納入先 and
  出荷元/搬入先 grid tabs on 2026-10-02), and a rebuild from ``--orig`` alone
  would delete those edits on the next deploy.

Outputs (source format, under --out-dir)
  flexipages/<page>.flexipage-meta.xml
  objects/TI_Fnt_Deal__c/compactLayouts/<cl>.compactLayout-meta.xml
  --report  JSON: counts{ERROR,WARN} + issues + page tree (deploy.py gate input)

Nothing here activates the page or changes the primary compact layout: the
CustomObject is never emitted, so no actionOverrides / compactLayoutAssignment
can change.
"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from collections import OrderedDict
from pathlib import Path
import xml.etree.ElementTree as ET

import openpyxl

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
MAX_PER_COL = 100
# Identifier prefix of every component this generator emits; --base rebuilds
# replace only main-region items carrying it.
GEN_PREFIX = "erp1032_"

# 「成約」 sheet columns (1-based, header-verified at row 9/10 on 2026-10-01)
COL = dict(no=1, label=3, api=4, type=5, tab=28, sub=29, sec=30, is_delete=33, wip=34)
FIRST_ROW = 11

# Target tree, in the order drawn on the 「コメント」 sheet. Sections listed here
# are created even when empty (帳票出力項目 fields are not decided yet).
SKELETON = OrderedDict([
    ("販売情報", OrderedDict([
        ("販売基本情報", OrderedDict([
            ("メイン項目", ["基本情報", "基本情報（輸出・海外直送・海外同別）", "基本情報（海外直送・海外別国）"]),
            ("補助項目", ["補助項目", "備考"]),
        ])),
        ("物流情報", None),
        ("L/C関連", None),
        ("為替・前受金", ["社内予約", "前受金情報"]),
        ("STC", None),
        ("帳票出力項目", ["売約書", "Sales Contract"]),
    ])),
    ("購買情報", OrderedDict([
        ("購買基本情報", OrderedDict([
            ("メイン項目", ["基本情報", "基本情報（輸入・海外直送仕入）", "基本情報（海外直送・海外別国仕入）"]),
            ("補助項目", ["補助項目", "Shipping Mark", "備考"]),
        ])),
        ("物流情報", None),
        ("為替・前受金", ["社内予約", "前払金情報"]),
        ("帳票出力項目", ["注文書", "Purchase Order"]),
    ])),
    ("システム管理・監査情報", OrderedDict([
        ("システム管理", ["システム管理"]),
        ("監査情報", ["監査情報"]),
    ])),
    ("その他", ["経営企画管理", "備考"]),
])
BOTTOM_SECTION = "合計数量・金額"

# AB value -> (top tab, sub tab)
AB_ROUTE = {
    "販売情報": ("販売情報", None),
    "販売基本情報": ("販売情報", "販売基本情報"),
    "LC関連": ("販売情報", "L/C関連"),
    "L/C関連": ("販売情報", "L/C関連"),
    "為替・前受金": ("販売情報", "為替・前受金"),
    "STC": ("販売情報", "STC"),
    "帳票出力項目": ("販売情報", "帳票出力項目"),
    "購買情報": ("購買情報", None),
    "購買基本情報": ("購買情報", "購買基本情報"),
    "為替・前払金": ("購買情報", "為替・前受金"),
    "システム管理・監査情報": ("システム管理・監査情報", None),
    "その他": ("その他", None),
}
AC_ALIAS = {"メイン情報": "メイン項目"}
AD_ALIAS = {"前受金": "前受金情報", "前払金": "前払金情報"}

# Excel copy error: rows No.147-150 all carry ...No1BranchNo__c; labels say No.2-5.
API_FIX_BY_NO = {
    147: "TI_Fnt_InternalReservationNo2BranchNo__c",
    148: "TI_Fnt_InternalReservationNo3BranchNo__c",
    149: "TI_Fnt_InternalReservationNo4BranchNo__c",
    150: "TI_Fnt_InternalReservationNo5BranchNo__c",
}

# Standard fields Dynamic Forms accepts (proven on the live page). CreatedDate /
# LastModifiedDate are rejected ("couldn't retrieve … Record.CreatedDate"); the
# 作成者 / 最終更新者 fields already render the date next to the user.
STANDARD_PLACEABLE = frozenset({"Name", "OwnerId", "CreatedById", "LastModifiedById", "RecordTypeId"})

# Original tabs whose Related Record components are moved into the new tree.
LOGISTICS_TAB, STC_OLD_TAB = "物流情報", "安全保障貿易"
LC_ACTION_MARK = "TI_Fnt_DealScreenInputLC_"


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def split_multi(v) -> list:
    if v is None:
        return []
    return [s.strip() for s in str(v).split("\n")]


class Report:
    def __init__(self):
        self.issues = []

    def add(self, sev, check, msg, **kw):
        self.issues.append(dict(severity=sev, check=check, message=msg, **kw))

    def counts(self):
        c = {"ERROR": 0, "WARN": 0, "INFO": 0}
        for i in self.issues:
            c[i["severity"]] += 1
        return c


# --------------------------------------------------------------------------- #
# Sheet → placements
# --------------------------------------------------------------------------- #
def read_rows(xlsx: Path) -> list[dict]:
    ws = openpyxl.load_workbook(xlsx, data_only=True)["成約"]
    rows = []
    for r in range(FIRST_ROW, ws.max_row + 1):
        cell = lambda k: ws.cell(r, COL[k]).value
        no = cell("no")
        if isinstance(no, str) and no.startswith("END["):
            break
        if not cell("label") and not cell("api"):
            continue
        rows.append({k: cell(k) for k in COL} | {"row": r})
    return rows


def is_true(v) -> bool:
    return str(v).strip().lower() in ("true", "x") if v is not None else False


def route(tab, sub, sec):
    """Return (top, subtab, subsubtab, section) or None."""
    if tab == "明細合計":
        return ("__bottom__", None, None, BOTTOM_SECTION)
    if tab not in AB_ROUTE:
        return None
    top, subtab = AB_ROUTE[tab]
    sub = AC_ALIAS.get(sub, sub) or None
    sec = AD_ALIAS.get(sec, sec) or None
    if top == "販売情報" and sec and "仕入" in sec:  # purchase section mis-tagged 販売基本情報
        top, subtab = "購買情報", "購買基本情報"
    if top == "システム管理・監査情報":
        return (top, sub, None, sub)
    if top == "その他":
        return (top, None, None, sec)
    if subtab is None:                      # common fields directly on 販売情報/購買情報
        return (top, None, None, top)
    if subtab == "L/C関連":
        return (top, subtab, None, sec if sec and sec != "基本情報" else "L/C関連")
    return (top, subtab, sub, sec)


def build_placements(rows, org_fields, rep: Report, dedupe: bool):
    placements, unplaced = [], []
    for x in rows:
        no, label = x["no"], x["label"]
        api = API_FIX_BY_NO.get(no, x["api"])
        if api != x["api"]:
            rep.add("WARN", "sheet.api", f"No.{no} {label}: sheet API {x['api']} is a copy error; using {api}",
                    no=no)
        if is_true(x["wip"]) or is_true(x["is_delete"]):
            continue
        tabs, subs, secs = split_multi(x["tab"]), split_multi(x["sub"]), split_multi(x["sec"])
        if not tabs:
            unplaced.append((no, label, api))
            continue
        if not api:
            rep.add("WARN", "field.api", f"No.{no} {label}: placed but has no API name — skipped", no=no)
            continue
        if api.startswith("TI_Fnt_") and not api.endswith("__c"):
            rep.add("WARN", "sheet.api", f"No.{no} {label}: sheet API {api} lacks __c; using {api}__c", no=no)
            api += "__c"
        if not api.endswith("__c") and api not in STANDARD_PLACEABLE:
            rep.add("INFO", "field.standard",
                    f"No.{no} {label} ({api}): standard field Dynamic Forms cannot place — skipped", no=no)
            continue
        if api.endswith("__c") and api not in org_fields:
            rep.add("WARN", "field.org", f"No.{no} {label} ({api}) is not in the org — skipped", no=no)
            continue
        seen = []
        for i, tab in enumerate(tabs):
            sub = subs[i] if i < len(subs) else None
            sec = secs[i] if i < len(secs) else None
            r = route(tab, sub, sec)
            if r is None:
                rep.add("WARN", "placement.unknown", f"No.{no} {label}: unknown tab '{tab}'", no=no)
                continue
            if r in seen:
                continue
            seen.append(r)
        for i, r in enumerate(seen):
            if i and dedupe:
                rep.add("WARN", "placement.duplicate",
                        f"No.{no} {label}: also placed at {' > '.join(p for p in r if p)} — kept first placement only",
                        no=no)
                continue
            placements.append(dict(no=no, label=label, api=api, path=r, dup_index=i))
    return placements, unplaced


# --------------------------------------------------------------------------- #
# Tree
# --------------------------------------------------------------------------- #
def new_node():
    return {"sections": OrderedDict(), "children": OrderedDict(), "components": [], "visibility": None}


def skeleton_tree():
    def build(spec):
        n = new_node()
        if isinstance(spec, list):
            for s in spec:
                n["sections"][s] = []
        elif isinstance(spec, dict):
            for k, v in spec.items():
                n["children"][k] = build(v)
        return n
    return build(SKELETON)


def place(tree, bottom, p, rep):
    top, sub, subsub, sec = p["path"]
    if top == "__bottom__":
        bottom.append(p)
        return
    node = tree["children"].setdefault(top, new_node())
    for level in (sub, subsub):
        if level:
            node = node["children"].setdefault(level, new_node())
    if not sec:
        sec = next(iter(node["sections"]), None) or "基本情報"
        rep.add("WARN", "placement.section",
                f"No.{p['no']} {p['label']}: no section given under {' > '.join(x for x in (top, sub, subsub) if x)} — put in 「{sec}」",
                no=p["no"])
    node["sections"].setdefault(sec, []).append(p)


# --------------------------------------------------------------------------- #
# FlexiPage XML
# --------------------------------------------------------------------------- #
class Builder:
    def __init__(self, root):
        self.root = root
        self.n = 0
        self.ids = {e.text for e in root.iter(q("identifier"))}

    def uid(self, base):
        self.n += 1
        ident = f"{GEN_PREFIX}{base}{self.n}"
        while ident in self.ids:
            self.n += 1
            ident = f"{GEN_PREFIX}{base}{self.n}"
        self.ids.add(ident)
        return ident

    def facet(self, items):
        name = f"Facet-{uuid.uuid4()}"
        reg = ET.Element(q("flexiPageRegions"))
        for it in items:
            reg.append(it)
        ET.SubElement(reg, q("name")).text = name
        ET.SubElement(reg, q("type")).text = "Facet"
        self.root.append(reg)
        return name

    def component(self, name, props, ident_base, visibility=None):
        it = ET.Element(q("itemInstances"))
        ci = ET.SubElement(it, q("componentInstance"))
        for k, v in props:
            p = ET.SubElement(ci, q("componentInstanceProperties"))
            ET.SubElement(p, q("name")).text = k
            ET.SubElement(p, q("value")).text = v
        ET.SubElement(ci, q("componentName")).text = name
        ET.SubElement(ci, q("identifier")).text = self.uid(ident_base)
        if visibility is not None:
            ci.append(copy.deepcopy(visibility))
        return it

    def field(self, api):
        it = ET.Element(q("itemInstances"))
        fi = ET.SubElement(it, q("fieldInstance"))
        p = ET.SubElement(fi, q("fieldInstanceProperties"))
        ET.SubElement(p, q("name")).text = "uiBehavior"
        ET.SubElement(p, q("value")).text = "none"
        ET.SubElement(fi, q("fieldItem")).text = f"Record.{api}"
        ET.SubElement(fi, q("identifier")).text = self.uid(f"Record{api.replace('__c', '_c')}Field")
        return it

    def section(self, label, apis, cols=2):
        ncols = max(1 if len(apis) <= 1 else cols, -(-len(apis) // MAX_PER_COL))
        per = -(-len(apis) // ncols) if apis else 0
        col_items = []
        for c in range(ncols):
            chunk = apis[c * per:(c + 1) * per] if apis else []
            body = self.facet([self.field(a) for a in chunk])
            col_items.append(self.component("flexipage:column", [("body", body)], "flexipage_column"))
        wrapper = self.facet(col_items)
        return self.component("flexipage:fieldSection",
                              [("columns", wrapper), ("horizontalAlignment", "true"), ("label", label)],
                              "flexipage_fieldSection")

    def tabset(self, tabs):
        """tabs: list of (title, body_items, visibility)."""
        tab_items = []
        for i, (title, items, vis) in enumerate(tabs):
            body = self.facet(items)
            props = ([("active", "true")] if i == 0 else []) + [("body", body), ("title", title)]
            tab_items.append(self.component("flexipage:tab", props, "customTab", vis))
        return self.component("flexipage:tabset", [("label", "Tabs"), ("tabs", self.facet(tab_items))],
                              "flexipage_tabset")

    def render(self, node):
        items = [self.section(lbl, [p["api"] for p in ps]) for lbl, ps in node["sections"].items()]
        items += node["components"]
        if node["children"]:
            items.append(self.tabset([(t, self.render(ch), ch["visibility"])
                                      for t, ch in node["children"].items()]))
        return items


def regions(root):
    return {r.find(q("name")).text: r for r in root.findall(q("flexiPageRegions"))}


def props_of(ci):
    out = {}
    for p in ci.findall(q("componentInstanceProperties")):
        v = p.find(q("value"))
        out[p.find(q("name")).text] = v.text if v is not None else None
    return out


def reachable_facets(regs, item):
    """All facet names referenced (transitively) from one itemInstance."""
    found, stack = set(), [item]
    while stack:
        el = stack.pop()
        for v in el.iter(q("value")):
            if v.text in regs and v.text not in found and regs[v.text].find(q("type")).text == "Facet":
                found.add(v.text)
                stack.append(regs[v.text])
    return found


def old_tabs(regs, tabset_item):
    """title -> (body item list (deep copies), visibilityRule)."""
    tabs_facet = props_of(tabset_item.find(q("componentInstance")))["tabs"]
    out = OrderedDict()
    for it in regs[tabs_facet].findall(q("itemInstances")):
        ci = it.find(q("componentInstance"))
        pr = props_of(ci)
        body = regs[pr["body"]]
        out[pr["title"]] = ([copy.deepcopy(x) for x in body.findall(q("itemInstances"))],
                            ci.find(q("visibilityRule")))
    return out


def action_of(item):
    ci = item.find(q("componentInstance"))
    return props_of(ci).get("updateQuickActionName", "") if ci is not None else ""


def item_identifier(item):
    el = item.find(f"{q('componentInstance')}/{q('identifier')}")
    return el.text if el is not None else ""


def build_page(orig: Path, tree, bottom, page_label, base: Path | None = None):
    """Rebuild the field tabs + bottom section.

    ``orig`` (the live page before ERPPJ-1032) supplies the Related Record
    components that are moved into the new tabs. ``base`` (the copy as it is
    now in the org) supplies everything else, so edits made there in App
    Builder survive a rebuild; only main-region items carrying GEN_PREFIX
    identifiers are replaced. Without ``base`` the copy starts from ``orig``.
    """
    orig_root = ET.parse(orig).getroot()
    orig_regs = regions(orig_root)
    old = old_tabs(orig_regs, orig_regs["main"].findall(q("itemInstances"))[0])

    root = ET.parse(base).getroot() if base else orig_root
    regs = regions(root)
    main = regs["main"]
    main_items = main.findall(q("itemInstances"))
    if base:
        replaced = [it for it in main_items if item_identifier(it).startswith(GEN_PREFIX)]
        if not replaced:
            raise SystemExit(f"--base {base}: no main-region items with identifier {GEN_PREFIX}* — "
                             "not a page this generator built")
    else:
        replaced = [main_items[0]]

    logi_items, logi_vis = old.get(LOGISTICS_TAB, ([], None))
    stc_items, stc_vis = old.get(STC_OLD_TAB, ([], None))
    lc_items = [i for i in logi_items if LC_ACTION_MARK in action_of(i)]
    logi_items = [i for i in logi_items if LC_ACTION_MARK not in action_of(i)]

    sales = tree["children"]["販売情報"]
    purch = tree["children"]["購買情報"]
    sales["children"]["物流情報"]["components"] = logi_items
    sales["children"]["物流情報"]["visibility"] = logi_vis
    sales["children"]["L/C関連"]["components"] = lc_items
    sales["children"]["L/C関連"]["visibility"] = logi_vis
    sales["children"]["STC"]["components"] = stc_items
    sales["children"]["STC"]["visibility"] = stc_vis
    purch["children"]["物流情報"]["visibility"] = logi_vis

    b = Builder(root)
    purch["children"]["物流情報"]["components"] = [b.component(
        "flexipage:richText",
        [("decorate", "true"),
         ("richTextValue", "<p>※購買側の物流情報（輸出入関連情報）の配置は別途反映予定</p>")],
        "flexipage_richText")]

    dead, keep_for_others = set(), set()
    for it in main_items:
        if it in replaced:
            dead |= reachable_facets(regs, it)
        else:
            keep_for_others |= reachable_facets(regs, it)
    for name, reg in regs.items():
        if name in dead and name not in keep_for_others:
            root.remove(reg)

    idx = list(main).index(replaced[0])
    for it in replaced:
        main.remove(it)
    new_tabset = b.tabset([(t, b.render(n), n["visibility"]) for t, n in tree["children"].items()])
    main.insert(idx, new_tabset)
    main.insert(idx + 1, b.section(BOTTOM_SECTION, [p["api"] for p in bottom]))

    # keep FlexiPage element order: flexiPageRegions*, masterLabel, sobjectType, template, type
    tail = [e for e in root if e.tag != q("flexiPageRegions")]
    for e in tail:
        root.remove(e)
    for e in tail:
        root.append(e)
    root.find(q("masterLabel")).text = page_label
    return root


def compact_layout_xml(dev_name, fields, label):
    root = ET.Element(q("CompactLayout"))
    ET.SubElement(root, q("fullName")).text = dev_name
    for f in fields:
        ET.SubElement(root, q("fields")).text = f
    ET.SubElement(root, q("label")).text = label
    return root


def write_xml(root, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root, space="    ")
    path.write_text('<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n",
                    encoding="utf-8")


def tree_summary(node, depth=0, out=None):
    out = [] if out is None else out
    for s, ps in node["sections"].items():
        out.append("  " * depth + f"「{s}」 {len(ps)} fields")
    if node["components"]:
        out.append("  " * depth + f"(existing components: {len(node['components'])})")
    for t, ch in node["children"].items():
        out.append("  " * depth + f"【{t}】" + (" [visibility rule]" if ch["visibility"] is not None else ""))
        tree_summary(ch, depth + 1, out)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--xlsx", required=True)
    ap.add_argument("--orig", required=True)
    ap.add_argument("--base", default="",
                    help="the copy as currently in the org (retrieved). Rebuild on top of it so "
                         "App Builder edits outside the generated field tabs are kept")
    ap.add_argument("--org-fields", required=True)
    ap.add_argument("--out-dir", default="force-app/main/default")
    ap.add_argument("--page-dev", default="TI_FnT_Deal_Lightning_Page_v2")
    ap.add_argument("--page-label", default="成約 レコードページ v2")
    ap.add_argument("--cl-dev", default="Deal_CompactLayout_v2")
    ap.add_argument("--cl-label", default="成約コンパクトレイアウト v2")
    ap.add_argument("--cl-fields", default="Name,TI_Fnt_TradeContractDocument__c,TI_Fnt_SalesOrganization__c,"
                                           "TI_Fnt_PurchasingOrganization__c,TI_Fnt_ContractType__c,TI_Fnt_Subject__c")
    ap.add_argument("--dedupe", action="store_true",
                    help="place multi-placement fields at their first location only "
                         "(default: every location; the Metadata API accepts repeats)")
    ap.add_argument("--report", default="../.build/erppj1032/validation_report.json")
    args = ap.parse_args()

    rep = Report()
    org_fields = set(json.loads(Path(args.org_fields).read_text()))
    rows = read_rows(Path(args.xlsx))
    placements, unplaced = build_placements(rows, org_fields, rep, dedupe=args.dedupe)

    tree, bottom = skeleton_tree(), []
    for p in placements:
        place(tree, bottom, p, rep)

    root = build_page(Path(args.orig), tree, bottom, args.page_label,
                      Path(args.base) if args.base else None)
    out = Path(args.out_dir)
    write_xml(root, out / "flexipages" / f"{args.page_dev}.flexipage-meta.xml")

    cl_fields = [f.strip() for f in args.cl_fields.split(",") if f.strip()]
    for f in cl_fields:
        if f.endswith("__c") and f not in org_fields:
            rep.add("ERROR", "compact.field", f"compact layout field {f} is not in the org")
    write_xml(compact_layout_xml(args.cl_dev, cl_fields, args.cl_label),
              out / "objects" / "TI_Fnt_Deal__c" / "compactLayouts" / f"{args.cl_dev}.compactLayout-meta.xml")

    orig_fields = {e.text.split(".", 1)[1] for e in ET.parse(args.orig).getroot().iter(q("fieldItem"))}
    placed = {p["api"] for p in placements}
    for no, label, api in unplaced:
        if api in orig_fields:
            rep.add("WARN", "placement.dropped",
                    f"No.{no} {label} ({api}) is on the current page but has no placement on the 「成約」 sheet — not placed",
                    no=no)
    for api in sorted(orig_fields - placed - {a for _, _, a in unplaced}):
        rep.add("WARN", "placement.dropped", f"{api} is on the current page but not on the 「成約」 sheet — not placed")

    def empties(node, path):
        for s, ps in node["sections"].items():
            if not ps:
                rep.add("INFO", "section.empty", f"{' > '.join(path + [s])} has no fields yet")
        for t, ch in node["children"].items():
            empties(ch, path + [t])
    empties(tree, [])

    summary = tree_summary(tree) + [f"「{BOTTOM_SECTION}」 {len(bottom)} fields (below the tabs)"]
    report = dict(counts=rep.counts(), issues=rep.issues, tree=summary,
                  placed_fields=len(placements), unique_fields=len(placed))
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")

    print("\n".join(summary))
    print(f"\nplaced {len(placements)} field instances ({len(placed)} unique); counts={rep.counts()}")
    for i in rep.issues:
        if i["severity"] != "INFO":
            print(f"  [{i['severity']}] {i['check']}: {i['message']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
