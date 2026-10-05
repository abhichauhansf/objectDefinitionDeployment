#!/usr/bin/env python3
"""
fetch_sheet.py — Local, GAS-free extraction of Data Dictionary object tabs
from a Google Sheet into `temp_updates.json` (the exact shape the existing
`generate_xml.py` generator expects).

Replaces the CI-only inline `fetch_data.js`. Runs fully locally using the
user's Google credentials:
  1. Application Default Credentials (ADC)  — `gcloud auth application-default
     login --scopes=...spreadsheets,drive`  (preferred, deploys as the user), OR
  2. a service-account JSON via GOOGLE_SERVICE_ACCOUNT_JSON.

Usage:
  python scripts/fetch_sheet.py \
      [--spreadsheet-id <ID>]          # default: scripts/sheet_config.py
      [--tabs "成約,輸入・入庫管理明細"]   # default: every object tab (auto-detected)
      [--out temp_updates.json]

Object tabs are auto-detected: any tab NOT in the known non-object set
(表紙 / オブジェクト一覧 / 変更履歴 / 変更ログ / フォーマット / Glossary / Data Glossary /
ユーザー一覧 / データ型のマッピング / IFログ など).

The field header row (row containing the API headers `fullName` + `type`) is
located dynamically per tab, because object tabs place it at different rows
(10, 11 or 12) depending on the object-header block size.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import warnings

from sheet_config import add_spreadsheet_id_arg
from numeric_size import apply_numeric_size, is_numeric_sheet_type, numeric_size_mode_banner
from rollup_h import apply_rollup_h, is_summary_type  # Updated by Divakar N — 2026-09-23

warnings.filterwarnings("ignore")

# --------------------------------------------------------------------------- #
# Column mapping: Row-12 API header (case-insensitive)  ->  temp_updates.json key
# (the JSON keys are what generate_xml.py's build_field_xml/process_fields read)
# --------------------------------------------------------------------------- #
API_HEADER_TO_KEY = {
    "label": "Field Label",
    "field label (en)": "Field Label (EN)",
    "translation provenance": "Translation Provenance",
    "translation origin": "Translation Origin",
    "translation source hash": "Translation Source Hash",
    "translation generated at": "Translation Generated At",
    "fullname": "Field API Name",
    "type": "Data Type",
    "length": "Length",
    "required": "Required",
    "unique": "Unique",
    "externalid": "External ID",
    "defaultvalue": "Default Value",
    "trackhistory": "Track History",
    "inlinehelptext": "Help Text",
    "description": "Description",
    "valueset.restricted": "Restricted",
    "valueset.valuesetname": "Global Value Set",
    "visiblelines": "Visible Lines",
    "precision": "Precision",
    "scale": "Scale",
    "maskchar": "Mask Character",
    "relationshiplabel": "Relationship Label",
    "relationshipname": "Relationship Name",
    "deleteconstraint": "Delete Constraint",
}
# The polymorphic Column H header is long; match it by prefix (header-driven,
# so it follows the column regardless of its physical letter).
COL_H_PREFIX = "displayformat"  # "displayFormat / referenceTo / formula / valueSet"
COL_H_KEY = "Type Specific Value"
# Col G (データ型に応じて…). Description for every type except Lookup /
# Master-Detail, where it is the referenceTo fallback when col H is blank.
# Updated by Divakar N — 2026-10-05.
COL_G_KEY = "Type Specific Note"
_LOOKUP_REF_TYPES = {
    "lookup", "masterdetail", "hierarchy", "参照関係", "主従関係",
}
# Object API only (User, Account, Foo__c). Japanese prose in col G is not a target.
_OBJECT_API_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

# Tabs that are never object metadata sheets.
NON_OBJECT_TABS = {
    "表紙", "オブジェクト一覧", "変更履歴", "変更ログ", "フォーマット",
    "glossary", "data glossary", "ユーザー一覧", "データ型のマッピング",
    "ifログ", "変更ログ", "jetファイル作成指示書",
}

GRAY_GUARD_SUBSTR = "行挿入する場合は当行より上部"  # LEGACY end-of-field-list marker (col C)
# PRIMARY end-of-field-list marker (confirmed by the sheet owner, Takeaki Yagai,
# 2026-09-01): every block on a "Format"-style tab is terminated by an END[XXX]
# cell in column A (No. column), where XXX is the block name — i.e. END[XXX] marks
# the end of the area corresponding to XXX. The FIELD list is always the FIRST
# block, so the first END[...] cell (END[項目]) marks the end of the fields;
# everything after it (END[レコードタイプ], END[入力規則], END[ルックアップ検索条件], …)
# must NOT be parsed as fields. Col A only ever holds row numbers or these
# markers, so matching on the leading "END[" in col A is safe. The gray-guard
# line above is kept only as a fallback for older tabs that predate this marker.
FIELD_LIST_END_PREFIX = "END["


def is_field_list_end(cells) -> bool:
    """Return True if this row terminates the FIELD list.

    Two accepted terminators (confirmed with the sheet owner, 2026-09-01):
      * PRIMARY  — an ``END[…]`` block marker in column A (e.g. ``END[項目]``);
      * LEGACY   — the gray-guard line (``行挿入する場合は当行より上部``) in col C,
                   kept only for older tabs that predate the ``END[…]`` convention.

    Robust to raw or already-normalized cells (substring / stripped-prefix match).
    Every script that scans an object tab row-by-row MUST use this so the
    field-list boundary can never drift between scripts.
    """
    if any(GRAY_GUARD_SUBSTR in str(c) for c in cells):
        return True
    return bool(cells) and str(cells[0]).strip().startswith(FIELD_LIST_END_PREFIX)

# Helper columns per the client "Format" sheet, located by HEADER NAME (the client
# keeps the order stable, but header-driven lookup is reshuffle-proof). Fallback
# 0-based indices: Z=25 Tab, AA=26 section, AD=29 IsDelete, AE=30 WIP.
#   * A row with WIP true/x is work-in-progress → ignored entirely.
#   * A row with IsDelete true is a deletion request → excluded from the create
#     package (routed to the destructive DELETE flow).
# See .cursor/rules/sf-sheet-columns.mdc.
HELPER_FALLBACK = {"tab": 25, "section": 26, "isdelete": 29, "wip": 30,
                   "ai_comment": 33, "deploy_status": 34}
WIP_TRUE = {"x", "true", "1", "yes", "○", "〇"}
DELETE_TRUE = {"true", "1", "yes", "x", "○", "〇"}

# Header aliases for the AI columns (AH review comments, AI deploy status). The
# client renamed AH FreeColumnGDC1 -> "GDC AI Tool Comments" and AI
# FreeColumnGDC2 -> "Deployment Status"; match the new names first, keep the old
# ones for backwards compatibility.
HELPER_HEADER_ALIASES = {
    "ai_comment": ("gdc ai tool comments", "freecolumngdc1"),
    "deploy_status": ("deployment status", "freecolumngdc2"),
}


def find_helper_cols(header_row: list) -> dict:
    """Locate Tab / section / IsDelete / WIP / AH / AI by header name (fallback to index)."""
    lowered = [norm(c).lower() for c in header_row]
    cols = {}
    for key, fb in HELPER_FALLBACK.items():
        idx = None
        # try aliases first (for the AI columns), then the literal key name
        for alias in HELPER_HEADER_ALIASES.get(key, ()):
            if alias in lowered:
                idx = lowered.index(alias)
                break
        if idx is None and key in lowered:
            idx = lowered.index(key)
        cols[key] = idx if idx is not None else fb
    return cols


def get_sheets_service():
    """Build a Sheets API client from ADC or a service-account JSON."""
    from googleapiclient.discovery import build

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets.readonly",
        "https://www.googleapis.com/auth/drive.readonly",
    ]
    sa_raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if sa_raw:
        import base64
        from google.oauth2 import service_account

        if sa_raw.startswith("{"):
            info = json.loads(sa_raw)
        elif sa_raw.startswith("/") or sa_raw.lower().endswith(".json"):
            info = json.load(open(sa_raw))
        else:
            info = json.loads(base64.b64decode(sa_raw).decode())
        creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    else:
        import google.auth

        creds, _ = google.auth.default(scopes=scopes)
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def norm(s) -> str:
    return str(s or "").strip()


def apply_lookup_ref_from_g(rec: dict) -> bool:
    """Lookup / Master-Detail: blank col H (設定値) falls back to col G.

    Updated by Divakar N — 2026-10-05.
    Why: the client sometimes leaves the lookup object only in col G
    (データ型に応じて… / referenceTo). Column H stays the value when it is
    filled. Formula, picklist, and roll-up rows never read G.

    Returns True when referenceTo was copied from G into Type Specific Value.
    """
    raw = norm(rec.get("Data Type")).lower().replace(" ", "").replace("_", "").replace("-", "")
    if raw not in _LOOKUP_REF_TYPES:
        return False
    if norm(rec.get(COL_H_KEY)):
        return False
    g = norm(rec.get(COL_G_KEY))
    if not g:
        return False
    if not _OBJECT_API_RE.match(g):
        # Checked G; it is a note, not an object API. Leave H blank.
        rec["_referenceToGRejected"] = g
        return False
    rec[COL_H_KEY] = g
    rec["_referenceToSource"] = "G"
    return True


def is_object_tab(title: str) -> bool:
    return title.strip().lower() not in {t.lower() for t in NON_OBJECT_TABS}


def parse_tab_list(raw: str) -> list[str]:
    """Split `--tabs` the same way as the rest of this repo: comma-separated titles."""
    return [t.strip() for t in str(raw or "").split(",") if t.strip()]


def unpack_packed(rec: dict, packed_key: str, origin_key: str,
                  hash_key: str, gen_key: str) -> None:
    """Split a packed `origin | hash | generated-at` cell into the row dict."""
    packed = str(rec.get(packed_key) or "").strip()
    if not packed:
        return
    parts = [p.strip() for p in packed.split("|")]
    if not str(rec.get(origin_key) or "").strip() and parts:
        rec[origin_key] = parts[0]
    if not str(rec.get(hash_key) or "").strip() and len(parts) > 1:
        rec[hash_key] = parts[1]
    if not str(rec.get(gen_key) or "").strip() and len(parts) > 2:
        rec[gen_key] = parts[2]


def find_header_row(grid: list[list]) -> int | None:
    """Return 0-based index of the API header row (has 'fullName' and 'type')."""
    for i, row in enumerate(grid[:20]):
        lowered = [norm(c).lower() for c in row]
        if "fullname" in lowered and "type" in lowered:
            return i
    return None


def build_col_map(header_row: list, jp_header_row: list | None = None) -> dict[int, str]:
    """Map column index -> temp_updates.json key.

    The client "Format" sheet has TWO polymorphic value columns whose machine
    API-header row is ambiguous — it carries ``defaultValue`` on BOTH the
    ``数式(設定値)`` column (the REAL type-specific value: formula body /
    picklist values / referenceTo / displayFormat / roll-up setup
    — Updated by Divakar N, 2026-09-23: H is also the Rollup summary block)
    AND the ``デフォルト値``
    column (the real field default). It also carries
    ``displayFormat / referenceTo / formula / valueSet`` on the
    ``データ型に応じて…`` column, which in this sheet is only a human DESCRIPTION
    column, not the deployed value.

    Per the sheet owner's layout (confirmed 2026-09-09), the deployed value
    lives in ``数式(設定値)`` (col H), NOT in ``データ型に応じて…`` (col G). Because
    the English API-header can't tell H from L (both say ``defaultValue``) and
    mislabels G, we disambiguate by the UNIQUE Japanese header substrings:
      * ``設定値`` → ``数式(設定値)`` (col H)  → Type Specific Value
      * ``デフォルト`` → ``デフォルト値`` (col L) → Default Value
    The ``データ型に応じて…`` column (col G) is a description for formula,
    picklist, and roll-up rows. Updated by Divakar N — 2026-10-05: a
    Lookup / Master-Detail whose col H is blank uses col G as referenceTo
    (``apply_lookup_ref_from_g``). H wins when both are filled.
    """
    col_map: dict[int, str] = {}
    jp = [norm(c) for c in (jp_header_row or [])]
    for idx, cell in enumerate(header_row):
        h = norm(cell).lower()
        jph = jp[idx] if idx < len(jp) else ""
        if not h and not jph:
            continue
        # 1) JP-header disambiguation wins (reshuffle-proof, unambiguous).
        if "設定値" in jph:                       # 数式(設定値)  (col H)
            col_map[idx] = COL_H_KEY              # Type Specific Value
            continue
        if "デフォルト" in jph:                    # デフォルト値  (col L)
            col_map[idx] = "Default Value"
            continue
        # English label / provenance (header-driven; JP or EN header text).
        jph_l = jph.lower()
        if ("ラベル" in jph and "(en)" in jph_l) or "項目ラベル名 (en)" in jph_l:
            col_map[idx] = "Field Label (EN)"
            continue
        if "翻訳出典" in jph or h in ("translation provenance",):
            col_map[idx] = "Translation Provenance"
            continue
        if h in ("field label (en)", "translation provenance",
                 "translation origin", "translation source hash",
                 "translation generated at"):
            col_map[idx] = API_HEADER_TO_KEY[h]
            continue
        # 2) Col G (displayFormat / referenceTo / …, JP データ型に応じて…).
        #    Stored separately. Lookup / Master-Detail copy it into the value
        #    only when col H is blank. Updated by Divakar N — 2026-10-05.
        if h.startswith(COL_H_PREFIX) or "データ型に応じて" in jph:
            col_map[idx] = COL_G_KEY
            continue
        # 3) Everything else maps by the English API header.
        if h in API_HEADER_TO_KEY:
            col_map[idx] = API_HEADER_TO_KEY[h]
    return col_map


OBJECT_META_LABELS = {
    "表示ラベル", "オブジェクト名", "説明", "レポートを許可", "活動を許可",
    "項目履歴管理", "検索を許可", "タブ作成 (create tab)",
    "表示ラベル (EN)", "表示ラベル(EN)", "Object Label (EN)",
    "Object Translation Provenance", "Object Translation Origin",
    "Object Translation Source Hash", "Object Translation Generated At",
}


def _meta_value(cells: list[str], start: int) -> str:
    """Read within one object-header region, stopping at the next label."""
    known = {norm(x).rstrip(":").lower() for x in OBJECT_META_LABELS}
    for value in cells[start:]:
        clean = norm(value)
        if clean.rstrip(":").lower() in known:
            return ""
        if clean and not clean.endswith(":"):
            return clean
    return ""


def parse_object_header(grid: list[list], header_idx: int) -> dict:
    """Extract object-level metadata (rows above the field header row).

    Anchors are the Japanese labels; the value sits a few columns to the right.
    """
    meta = {"_type": "object_meta"}
    label = api = desc = ""
    er = ea = eh = es = ""
    # Object-meta only: stop before the JP field-header row so field-column
    # labels (翻訳出典, 項目ラベル名 (EN)) are never read as object EN/provenance.
    meta_end = max(header_idx - 1, 0)
    for ridx, row in enumerate(grid[:meta_end]):
        cells = [norm(c) for c in row]
        joined = [c for c in cells]
        for j, c in enumerate(cells):
            if c in ("表示ラベル (EN)", "表示ラベル(EN)", "Object Label (EN)"):
                meta["Object Label (EN)"] = _meta_value(joined, j + 1)
            elif c in ("Object Translation Provenance",):
                meta["Object Translation Provenance"] = _meta_value(joined, j + 1)
            elif c in ("Object Translation Origin",):
                meta["Object Translation Origin"] = _meta_value(joined, j + 1)
            elif c in ("Object Translation Source Hash",):
                meta["Object Translation Source Hash"] = _meta_value(joined, j + 1)
            elif c in ("Object Translation Generated At",):
                meta["Object Translation Generated At"] = _meta_value(joined, j + 1)
            elif c == "表示ラベル":
                label = _meta_value(joined, j + 1)
                meta["_ObjectHeaderRow"] = ridx
                # オブジェクト名 label is usually further right on the same row
                if "オブジェクト名" in cells:
                    k = cells.index("オブジェクト名")
                    api = _meta_value(joined, k + 1)
            elif c == "説明":
                desc = _meta_value(joined, j + 1)
            elif c == "レポートを許可":
                # value row is typically the next grid row; handled below
                pass
        # enable flags: a row that literally holds the value under the label row
    # Enable flags: scan for the label row then read the row beneath it.
    for r, row in enumerate(grid[:header_idx]):
        cells = [norm(c) for c in row]
        if "レポートを許可" in cells and r + 1 < header_idx:
            vals = [norm(c) for c in grid[r + 1]]
            labelrow = cells
            for lbl, key in (("レポートを許可", "er"), ("活動を許可", "ea"),
                             ("項目履歴管理", "eh"), ("検索を許可", "es")):
                if lbl in labelrow:
                    ci = labelrow.index(lbl)
                    v = vals[ci] if ci < len(vals) else ""
                    if key == "er":
                        er = v
                    elif key == "ea":
                        ea = v
                    elif key == "eh":
                        eh = v
                    elif key == "es":
                        es = v
    meta.update({
        "Object Label": label,
        "Object API Name": api,
        "Object Description": desc,
        "enableReports": er,
        "enableActivities": ea,
        "enableHistory": eh,
        "enableSearch": es,
    })
    unpack_packed(
        meta,
        packed_key="Object Translation Provenance",
        origin_key="Object Translation Origin",
        hash_key="Object Translation Source Hash",
        gen_key="Object Translation Generated At",
    )
    return meta


def parse_tab(title: str, grid: list[list], object_api_hint: str = "") -> list[dict]:
    """Parse one object tab into object_meta + field rows."""
    header_idx = find_header_row(grid)
    if header_idx is None:
        print(f"  ⚠️  {title}: no field header row (fullName/type) found — skipped.")
        return []

    col_map = build_col_map(grid[header_idx], grid[header_idx - 1] if header_idx > 0 else None)
    helper = find_helper_cols(grid[header_idx])
    obj_meta = parse_object_header(grid, header_idx)

    # Resolve object API: header value > index-tab hint > (leave blank -> warn)
    obj_api = norm(obj_meta.get("Object API Name")) or norm(object_api_hint)
    obj_label = norm(obj_meta.get("Object Label")) or title
    obj_meta["Object API Name"] = obj_api
    obj_meta["Object Label"] = obj_label

    # Object EN/provenance live on the object-header row (表示ラベル), in the
    # Field Label (EN) / Translation Provenance columns — not a field row.
    oh = obj_meta.get("_ObjectHeaderRow")
    if oh is not None and 0 <= int(oh) < len(grid):
        hdr_cells = [norm(c) for c in grid[int(oh)]]
        en_idx = next((i for i, k in col_map.items() if k == "Field Label (EN)"), None)
        pv_idx = next((i for i, k in col_map.items() if k == "Translation Provenance"), None)
        if en_idx is not None and not obj_meta.get("Object Label (EN)"):
            obj_meta["Object Label (EN)"] = hdr_cells[en_idx] if en_idx < len(hdr_cells) else ""
        if pv_idx is not None and not obj_meta.get("Object Translation Provenance"):
            obj_meta["Object Translation Provenance"] = (
                hdr_cells[pv_idx] if pv_idx < len(hdr_cells) else "")
        unpack_packed(
            obj_meta,
            packed_key="Object Translation Provenance",
            origin_key="Object Translation Origin",
            hash_key="Object Translation Source Hash",
            gen_key="Object Translation Generated At",
        )

    rows: list[dict] = []
    field_rows: list[dict] = []
    name_field = {}
    wip_skipped = 0
    delete_skipped = 0
    numeric_from_f = 0
    rollup_parsed = 0

    def cell_at(cells, key):
        idx = helper[key]
        return cells[idx].strip() if idx < len(cells) else ""

    for ridx, row in enumerate(grid[header_idx + 1:], start=header_idx + 1):
        cells = [norm(c) for c in row]
        # stop at the end of the FIELD list — END[項目] (col A) or the legacy
        # gray-guard line (col C). Shared helper so the boundary never drifts.
        if is_field_list_end(cells):
            break
        rec = {"_SheetName": title, "Object API Name": obj_api, "Object Label": obj_label,
               "_SheetRow": ridx + 1}
        for idx, key in col_map.items():
            rec[key] = cells[idx] if idx < len(cells) else ""
        unpack_packed(
            rec,
            packed_key="Translation Provenance",
            origin_key="Translation Origin",
            hash_key="Translation Source Hash",
            gen_key="Translation Generated At",
        )
        # capture page-layout Tab (Z) + section (AA) for downstream page work
        rec["Tab"] = cell_at(cells, "tab")
        rec["Section"] = cell_at(cells, "section")
        # a real field row must have an API name or a label+type
        if not (norm(rec.get("Field API Name")) or
                (norm(rec.get("Field Label")) and norm(rec.get("Data Type")))):
            continue
        # WIP gate: skip rows flagged true/x in the WIP column (AE) — work in progress
        if cell_at(cells, "wip").lower() in WIP_TRUE:
            wip_skipped += 1
            continue
        # IsDelete gate: a deletion request (AD) is excluded from the create package
        # (routed to the destructive DELETE flow; see sf-deploy-delta-and-blockers).
        if cell_at(cells, "isdelete").lower() in DELETE_TRUE:
            delete_skipped += 1
            continue
        # Lookup / Master-Detail: blank col H uses col G as referenceTo.
        # Updated by Divakar N — 2026-10-05.
        apply_lookup_ref_from_g(rec)
        # capture Name field for the object's <nameField>
        if norm(rec.get("Field API Name")) == "Name":
            name_field = {
                "Name Field Label": rec.get("Field Label", ""),
                "Name Field Label (EN)": rec.get("Field Label (EN)", ""),
                "Name Field Type": rec.get("Data Type", ""),
                "Name Field Display Format": rec.get("Type Specific Value", ""),
                "Name Translation Provenance": rec.get("Translation Provenance", ""),
                "Name Translation Origin": rec.get("Translation Origin", ""),
                "Name Translation Source Hash": rec.get("Translation Source Hash", ""),
                "Name Translation Generated At": rec.get("Translation Generated At", ""),
            }
            continue
        # Number/Currency/Percent: derive Precision/Scale from col F ("12, 3")
        # so T/U are not required from the client (in-memory only; not a sheet write).
        if is_numeric_sheet_type(rec.get("Data Type")):
            st = apply_numeric_size(rec)
            if st.get("ok") and st.get("source") == "F":
                numeric_from_f += 1
        # Updated by Divakar N — 2026-09-23.
        # Why: Rollup summary setup is in col H (設定値), not G. Parse
        # Operation:COUNT / Child:… / Field:… / Condition:… (no space after colon)
        # here so generate/validate see it.
        # Blank/unparseable H is a validator ERROR (no dummy, unlike formulas).
        if is_summary_type(rec.get("Data Type")):
            apply_rollup_h(rec)
            rollup_parsed += 1
        field_rows.append(rec)

    obj_meta.update(name_field)
    obj_meta["_SheetName"] = title
    if obj_api:
        rows.append(obj_meta)
    else:
        print(f"  ⚠️  {title}: Object API Name not found (header blank & no index hint).")
    rows.extend(field_rows)
    wip_note = f", {wip_skipped} WIP(AE) skipped" if wip_skipped else ""
    del_note = f", {delete_skipped} IsDelete(AD) excluded" if delete_skipped else ""
    f_note = f", {numeric_from_f} numeric size(s) from col F" if numeric_from_f else ""
    ru_note = f", {rollup_parsed} rollup(s) from col H" if rollup_parsed else ""
    print(f"  ✓ {title}: {len(field_rows)} field(s){wip_note}{del_note}{f_note}{ru_note}, object_api='{obj_api or '?'}'")
    return rows


def load_object_index(svc, spreadsheet_id: str) -> dict[str, str]:
    """Read オブジェクト一覧 to map object label -> API name (F=label, G=API, row 3+)."""
    try:
        vals = svc.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id, range="'オブジェクト一覧'",
            valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
    except Exception:
        return {}
    idx: dict[str, str] = {}
    for row in vals[2:]:
        # columns F (5) label, G (6) api per docs; be tolerant
        if len(row) > 6:
            label, api = norm(row[5]), norm(row[6])
            if label and api:
                idx[label] = api
    return idx


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch DD object tabs -> temp_updates.json")
    add_spreadsheet_id_arg(ap)
    ap.add_argument("--tabs", default="", help="comma-separated tab names; default=all object tabs")
    ap.add_argument("--list-tabs", action="store_true",
                    help="list the available object tabs and exit (scope-selection gate)")
    ap.add_argument("--out", default="temp_updates.json")
    args = ap.parse_args()

    numeric_size_mode_banner()

    svc = get_sheets_service()
    meta = svc.spreadsheets().get(spreadsheetId=args.spreadsheet_id,
                                  includeGridData=False).execute()
    all_titles = [s["properties"]["title"] for s in meta["sheets"]]

    if args.list_tabs:
        obj_tabs = [t for t in all_titles if is_object_tab(t)]
        print(f"Spreadsheet: {meta['properties']['title']}")
        print(f"Object tabs ({len(obj_tabs)}):")
        for i, t in enumerate(obj_tabs, 1):
            print(f"  [{i}] {t}")
        skipped = [t for t in all_titles if not is_object_tab(t)]
        if skipped:
            print(f"(non-object tabs, ignored: {', '.join(skipped)})")
        return 0

    if args.tabs.strip():
        wanted = parse_tab_list(args.tabs)
        missing = [t for t in wanted if t not in all_titles]
        if missing:
            print(f"❌ unknown tab(s): {', '.join(missing)}")
            obj_tabs = [t for t in all_titles if is_object_tab(t)]
            print(f"   available object tabs: {', '.join(obj_tabs)}")
            return 1
    else:
        wanted = [t for t in all_titles if is_object_tab(t)]

    print(f"Spreadsheet: {meta['properties']['title']}")
    print(f"Object tabs to fetch ({len(wanted)}): {', '.join(wanted)}")

    obj_index = load_object_index(svc, args.spreadsheet_id)

    out_rows: list[dict] = []
    for title in wanted:
        try:
            grid = svc.spreadsheets().values().get(
                spreadsheetId=args.spreadsheet_id, range=f"'{title}'",
                valueRenderOption="FORMATTED_VALUE").execute().get("values", [])
        except Exception as e:
            print(f"  ⚠️  {title}: read failed — {e}")
            continue
        hint = obj_index.get(title, "")
        out_rows.extend(parse_tab(title, grid, object_api_hint=hint))

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_rows, f, ensure_ascii=False, indent=2)

    n_obj = sum(1 for r in out_rows if r.get("_type") == "object_meta")
    n_fld = len(out_rows) - n_obj
    print(f"\nWrote {args.out}: {n_obj} object(s), {n_fld} field(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
