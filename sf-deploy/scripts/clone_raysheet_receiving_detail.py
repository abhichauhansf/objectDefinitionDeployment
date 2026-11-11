#!/usr/bin/env python3
"""
clone_raysheet_receiving_detail.py — ERPPJ-1055.

Duplicate the 入庫明細 Raysheet so the Receiving page COPY can show a different
column order without touching what the customer sees today.

  gcss__ViewSettings__c  a2HBK000001eTfR2AU   (the view: columns, filters, actions)
  gcss__RaysheetPage__c  a2DBK00000QSklh2AD   (the page: Config JSON -> viewId)

Creates ONE new view (copy of the view, columns re-ordered) and ONE new page (copy
of the page, Config.part1Config.viewId -> the new view). The originals are only
READ. The new page Id is what the page copy puts in `raysheetPage`.

Column order (client comment, コメント sheet): these go first, in this order,
then every other existing column keeps its current relative order.
  品目コード=TI_Fnt_Item__c, 品目テキスト=TI_Fnt_MaterialDescription__c,
  数量=TI_Fnt_Quantity__c, 単位=TI_Fnt_Unit__c,
  支払金額（税抜）=TI_Fnt_PaymentAmountExclTax__c, 発注単価=TI_Fnt_OrderUnitPrice__c,
  通貨=TI_Fnt_Currency__c, 保管場所=TI_Fnt_StorageLocation2__c

Default is DRY-RUN (prints the plan). --apply creates the two records and writes
.build/erppj1055/raysheet_clone.json (ids, for rollback). --rollback deletes them.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import subprocess
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

SRC_VIEW = "a2HBK000001eTfR2AU"
SRC_PAGE = "a2DBK00000QSklh2AD"
FIRST = [
    "TI_Fnt_Item__c",
    "TI_Fnt_MaterialDescription__c",
    "TI_Fnt_Quantity__c",
    "TI_Fnt_Unit__c",
    "TI_Fnt_PaymentAmountExclTax__c",
    "TI_Fnt_OrderUnitPrice__c",
    "TI_Fnt_Currency__c",
    "TI_Fnt_StorageLocation2__c",
]
SUFFIX = "_v2"
PREFIX = "TI_Fnt_"  # every component we create carries TI_Fnt_ (sf-field-namespace-prefix.mdc)
STATE = Path(__file__).resolve().parents[2] / ".build" / "erppj1055" / "raysheet_clone.json"
API = "v66.0"


ORG = ""


def org_session(org: str):
    """The sf CLI redacts the access token, so every call goes through
    `sf api request rest` (the CLI keeps the session). Returns dummies."""
    global ORG
    ORG = org
    return "", ""


def call(method, url, token, body=None):
    path = url.split(".com", 1)[1] if url.startswith("http") else url
    cmd = ["sf", "api", "request", "rest", path, "--method", method, "--target-org", ORG]
    tmp = None
    if body is not None:
        import tempfile
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(body, tmp, ensure_ascii=False)
        tmp.close()
        cmd += ["--body", "@" + tmp.name]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if tmp:
        Path(tmp.name).unlink(missing_ok=True)
    txt = out.stdout.strip()
    try:
        data = json.loads(txt) if txt else {}
    except json.JSONDecodeError:
        raise SystemExit(f"{method} {path} failed: {out.stdout[:500]} {out.stderr[:500]}")
    if out.returncode != 0 or (isinstance(data, list) and data and "errorCode" in data[0]):
        raise SystemExit(f"{method} {path} failed: {txt[:800]}")
    return data


def soql(base, token, q):
    from urllib.parse import quote
    return call("GET", f"/services/data/{API}/query?q={quote(q)}", token)["records"]


def describe_createable(base, token, sobject):
    d = call("GET", f"/services/data/{API}/sobjects/{sobject}/describe", token)
    return {f["name"] for f in d["fields"] if f["createable"]}


def decode_columns(blob: str) -> list:
    z = zipfile.ZipFile(io.BytesIO(base64.b64decode(blob)))
    return json.loads(z.read("columns").decode("utf-8"))


def encode_columns(cols: list) -> str:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("columns", json.dumps(cols, ensure_ascii=False, separators=(",", ":")))
    return base64.b64encode(buf.getvalue()).decode()


def reorder(cols: list) -> list:
    by = {c["columnPath"]: c for c in cols}
    missing = [p for p in FIRST if p not in by]
    if missing:
        raise SystemExit(f"columns not in the view: {missing}")
    return [by[p] for p in FIRST] + [c for c in cols if c["columnPath"] not in FIRST]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target-org", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    base, token = org_session(a.target_org)

    if a.rollback:
        st = json.loads(STATE.read_text())
        for obj, key in (("gcss__RaysheetPage__c", "page_id"), ("gcss__ViewSettings__c", "view_id")):
            call("DELETE", f"/services/data/{API}/sobjects/{obj}/{st[key]}", token)
            print(f"deleted {obj} {st[key]}")
        STATE.unlink()
        return 0

    v = soql(base, token, f"SELECT FIELDS(ALL) FROM gcss__ViewSettings__c WHERE Id='{SRC_VIEW}' LIMIT 1")[0]
    p = soql(base, token, f"SELECT FIELDS(ALL) FROM gcss__RaysheetPage__c WHERE Id='{SRC_PAGE}' LIMIT 1")[0]
    cfg = json.loads(p["gcss__Config__c"])
    if cfg["part1Config"]["viewId"] != SRC_VIEW:
        raise SystemExit(f"page {SRC_PAGE} does not point at view {SRC_VIEW}: {cfg['part1Config']['viewId']}")

    cols = decode_columns(v["gcss__Columns__c"])
    new_cols = reorder(cols)
    print(f"view   {v['Name']}  columns: {len(cols)}")
    print("old first 10:", [c["columnPath"] for c in cols[:10]])
    print("new first 10:", [c["columnPath"] for c in new_cols[:10]])
    assert sorted(c["columnPath"] for c in cols) == sorted(c["columnPath"] for c in new_cols)

    v_ok, p_ok = describe_createable(base, token, "gcss__ViewSettings__c"), describe_createable(base, token, "gcss__RaysheetPage__c")
    v_body = {k: val for k, val in v.items() if k in v_ok and val not in (None, "") and k != "gcss__GlobalID__c"}
    v_body["Name"] = PREFIX + v["Name"] + SUFFIX
    v_body["gcss__Columns__c"] = encode_columns(new_cols)
    assert decode_columns(v_body["gcss__Columns__c"]) == new_cols

    if not a.apply:
        print(f"\nDRY-RUN. Would create view '{v_body['Name']}' ({len(v_body)} fields) and page "
              f"'{PREFIX + p['Name'] + SUFFIX}'. Re-run with --apply.")
        return 0

    new_view = call("POST", f"/services/data/{API}/sobjects/gcss__ViewSettings__c", token, v_body)
    vid = new_view["id"]
    cfg["part1Config"]["viewId"] = vid
    p_body = {k: val for k, val in p.items() if k in p_ok and val not in (None, "") and k != "gcss__GlobalID__c"}
    p_body["Name"] = PREFIX + p["Name"] + SUFFIX
    p_body["gcss__Config__c"] = json.dumps(cfg, ensure_ascii=False, separators=(",", ":"))
    try:
        new_page = call("POST", f"/services/data/{API}/sobjects/gcss__RaysheetPage__c", token, p_body)
    except SystemExit:
        call("DELETE", f"/services/data/{API}/sobjects/gcss__ViewSettings__c/{vid}", token)
        print(f"page create failed; deleted the new view {vid}")
        raise
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(dict(view_id=vid, page_id=new_page["id"], src_view=SRC_VIEW, src_page=SRC_PAGE,
                                     view_name=v_body["Name"], page_name=p_body["Name"]), ensure_ascii=False, indent=1))
    print(f"created view {vid} and page {new_page['id']}; state -> {STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
