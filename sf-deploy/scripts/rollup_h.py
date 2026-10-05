#!/usr/bin/env python3
"""Parse Rollup summary setup from column H (設定値 / Type Specific Value).

Updated by Divakar N — 2026-09-23.
Why: Rollup summary was emitting only ``<type>Summary</type>`` and looking for
non-existent sheet columns. The setup belongs in col H (設定値), same as
formula / picklist / referenceTo — never col G (description). This parser
turns the labeled H block into Salesforce summaryOperation / summaryForeignKey /
summarizedField / summaryFilterItems.

Sheet contract (E = ``Rollup summary``, H = this block, G = description ignored).
Canonical form has NO space after the colon (``Operation:SUM`` not ``Operation: SUM``).
Spaces after the colon are still accepted.

    Operation:COUNT | SUM | MIN | MAX
    Child:ChildObject__c.MasterDetailField__c
    Field:ChildObject__c.FieldToSummarize__c
    Condition:ChildObject__c.FilterField__c <operator> <value>

``Child:`` is required. ``Field:`` is required for SUM/MIN/MAX and omitted for
COUNT. Extra ``Condition:`` lines are AND filters (Salesforce has no OR on a
roll-up). Blank H is a hard blocker — there is no dummy like empty formulas.

Operators (symbols; word names still accepted):

    =  equals     !=  notEqual     <  lessThan      >  greaterThan
    <= lessOrEqual  >= greaterOrEqual
    ~  contains   !~  notContain   ^= startsWith
    in includes (multi-select)     !in excludes (multi-select)

``<value>`` is a literal, or ``valueField:ChildObject__c.OtherField__c``.
"""
from __future__ import annotations

import re

MAX_FILTERS = 10

# Salesforce Metadata API SummaryOperations enumeration (lowercase).
OPS_SHEET_TO_XML = {
    "COUNT": "count",
    "SUM": "sum",
    "MIN": "min",
    "MAX": "max",
}

# Longest-first. Alpha tokens (`in` / `!in`) need word boundaries.
_SYMBOL_OPS: tuple[tuple[str, str], ...] = (
    ("!in", "excludes"),
    ("!~", "notContain"),
    ("^=", "startsWith"),
    ("!=", "notEqual"),
    ("<=", "lessOrEqual"),
    (">=", "greaterOrEqual"),
    ("in", "includes"),
    ("=", "equals"),
    ("<", "lessThan"),
    (">", "greaterThan"),
    ("~", "contains"),
)

_WORD_OPS: dict[str, str] = {
    "equals": "equals",
    "notequal": "notEqual",
    "lessthan": "lessThan",
    "greaterthan": "greaterThan",
    "lessorequal": "lessOrEqual",
    "greaterorequal": "greaterOrEqual",
    "contains": "contains",
    "notcontain": "notContain",
    "startswith": "startsWith",
    "includes": "includes",
    "excludes": "excludes",
}

# Object.Field — one dot. Custom (__c) or standard (Account.Name).
_QUALIFIED_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*\.[A-Za-z][A-Za-z0-9_]*$")
# Canonical: Label:value (no space after colon). \s* still allows "Label: value".
# Updated by Divakar N — 2026-09-23.
_LINE_RE = re.compile(r"^\s*([A-Za-z]+)\s*[:：]\s*(.*)$")
_SUMMARY_TYPE_KEYS = frozenset({
    "summary", "rollup", "rollupsummary", "roll-upsummary", "集計",
})
_KNOWN_LABELS = {"operation", "child", "field", "condition"}


def _type_key(raw: str) -> str:
    return re.sub(r"[\s\u3000_（）()/]+", "", (raw or "").strip().lower())


def is_summary_type(raw_type: str) -> bool:
    """True when column E is Rollup summary / Summary / 集計."""
    return _type_key(raw_type) in _SUMMARY_TYPE_KEYS


def _qualified(name: str) -> bool:
    return bool(_QUALIFIED_RE.match((name or "").strip()))


def _split_operator(rest: str) -> tuple[str, str, str] | None:
    """Return (field, metadata-op, value) or None if no operator found."""
    s = rest.strip()
    if not s:
        return None

    # Word operators (equals, notEqual, …) — require surrounding whitespace.
    for m in re.finditer(r"\s+(\S+)\s+", s):
        token = m.group(1)
        api = _WORD_OPS.get(token.lower().replace(" ", ""))
        if api:
            return s[: m.start()].strip(), api, s[m.end():].strip()

    # Symbols. Alpha `in` / `!in` need a non-identifier on both sides.
    # Other symbols allow optional spaces.
    best: tuple[int, int, str] | None = None  # start, end, api
    for sym, api in _SYMBOL_OPS:
        if sym.isalpha() or (sym.startswith("!") and sym[1:].isalpha()):
            pat = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(sym)}(?![A-Za-z0-9_])", re.I)
        else:
            pat = re.compile(re.escape(sym))
        m = pat.search(s)
        if not m:
            continue
        start, end = m.start(), m.end()
        # Prefer the earliest match; at the same index, longer already won
        # because _SYMBOL_OPS is longest-first and we skip later hits.
        if best is None or start < best[0]:
            best = (start, end, api)
        elif start == best[0] and (end - start) > (best[1] - best[0]):
            best = (start, end, api)
    if best is None:
        return None
    start, end, api = best
    return s[:start].strip(), api, s[end:].strip()


def _parse_value(raw: str) -> tuple[str | None, str | None]:
    """Return (value, valueField). Exactly one side is set (value may be '')."""
    t = (raw or "").strip()
    low = t.lower()
    if low.startswith("valuefield:"):
        return None, t.split(":", 1)[1].strip()
    return t, None


def parse_rollup_h(text: str) -> dict:
    """Parse one H-cell. Always returns a dict with ``ok``, ``errors``, ``warnings``."""
    errors: list[str] = []
    warnings: list[str] = []
    operation = ""
    child = ""
    summarized = ""
    filters: list[dict] = []
    seen_labels: set[str] = set()

    raw = (text or "").replace("\r\n", "\n").replace("\r", "\n").replace("\u3000", " ")
    if not raw.strip():
        return {
            "ok": False,
            "errors": ["column H (設定値) is empty — Rollup summary needs Operation:COUNT / Child:…"],
            "warnings": [],
            "operation": "",
            "child": "",
            "summarized_field": "",
            "filters": [],
        }

    for i, line in enumerate(raw.split("\n"), 1):
        stripped = line.strip()
        if not stripped:
            continue
        m = _LINE_RE.match(stripped)
        if not m:
            errors.append(f"line {i}: expected 'Label: value', got {stripped[:80]!r}")
            continue
        label = m.group(1).strip().lower()
        rest = m.group(2).strip()
        if label not in _KNOWN_LABELS:
            errors.append(f"line {i}: unknown label '{m.group(1)}' (use Operation:/Child:/Field:/Condition:)")
            continue
        if label in {"operation", "child", "field"}:
            if label in seen_labels:
                errors.append(f"line {i}: duplicate '{m.group(1)}:'")
                continue
            seen_labels.add(label)
        if label == "operation":
            op = rest.upper()
            if op not in OPS_SHEET_TO_XML:
                errors.append(f"line {i}: Operation '{rest}' invalid (COUNT/SUM/MIN/MAX)")
            else:
                operation = op
        elif label == "child":
            if not _qualified(rest):
                errors.append(
                    f"line {i}: Child must be ChildObject__c.MasterDetailField__c (got {rest!r})"
                )
            else:
                child = rest
        elif label == "field":
            if not _qualified(rest):
                errors.append(
                    f"line {i}: Field must be ChildObject__c.FieldToSummarize__c (got {rest!r})"
                )
            else:
                summarized = rest
        elif label == "condition":
            split = _split_operator(rest)
            if not split:
                errors.append(
                    f"line {i}: Condition needs '<field> <operator> <value>' (got {rest!r})"
                )
                continue
            field, op_api, val_raw = split
            if not _qualified(field):
                errors.append(
                    f"line {i}: filter field must be ChildObject__c.FilterField__c (got {field!r})"
                )
                continue
            value, value_field = _parse_value(val_raw)
            item: dict = {"field": field, "operation": op_api}
            if value_field is not None:
                if not _qualified(value_field):
                    errors.append(
                        f"line {i}: valueField must be Object__c.Field__c (got {value_field!r})"
                    )
                    continue
                item["valueField"] = value_field
            else:
                item["value"] = value if value is not None else ""
            filters.append(item)

    if not operation:
        errors.append("missing Operation:COUNT | SUM | MIN | MAX")
    if not child:
        errors.append("missing Child:ChildObject__c.MasterDetailField__c")
    if operation in {"SUM", "MIN", "MAX"} and not summarized:
        errors.append(f"Field: is required for {operation}")
    if operation == "COUNT" and summarized:
        warnings.append("Field: is ignored for COUNT — omit it")
        summarized = ""
    if len(filters) > MAX_FILTERS:
        errors.append(f"{len(filters)} Condition lines (Salesforce max {MAX_FILTERS})")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "operation": operation,
        "child": child,
        "summarized_field": summarized,
        "filters": filters,
    }


def apply_rollup_h(row: dict) -> dict | None:
    """If this row is a Rollup summary, parse H onto the row. Return the parse dict or None."""
    if row.get("_type") == "object_meta":
        return None
    if not is_summary_type(row.get("Data Type") or ""):
        return None
    parsed = parse_rollup_h(str(row.get("Type Specific Value") or ""))
    row["_rollup"] = parsed
    if parsed.get("operation"):
        row["Summary Operation"] = parsed["operation"]
    if parsed.get("child"):
        row["Summary Foreign Key"] = parsed["child"]
    if parsed.get("summarized_field"):
        row["Summarized Field"] = parsed["summarized_field"]
    elif parsed.get("operation") == "COUNT":
        row["Summarized Field"] = ""
    row["Summary Filter Items"] = parsed.get("filters") or []
    return parsed


def summary_operation_xml(op: str) -> str:
    return OPS_SHEET_TO_XML.get((op or "").strip().upper(), "")


def _self_test() -> int:
    cases = []

    p = parse_rollup_h(
        "Operation:COUNT\nChild:ChildObject__c.MasterDetailField__c"
    )
    cases.append(("count no filter", p["ok"] and p["operation"] == "COUNT"
                  and p["child"] == "ChildObject__c.MasterDetailField__c"
                  and p["summarized_field"] == "" and p["filters"] == []))

    p = parse_rollup_h(
        "Operation:COUNT\n"
        "Child:ChildObject__c.MasterDetailField__c\n"
        "Condition:ChildObject__c.FilterField1__c = Value1\n"
        "Condition:ChildObject__c.FilterField2__c > 0"
    )
    cases.append(("count filters", p["ok"] and len(p["filters"]) == 2
                  and p["filters"][0]["operation"] == "equals"
                  and p["filters"][0]["value"] == "Value1"
                  and p["filters"][1]["operation"] == "greaterThan"
                  and p["filters"][1]["value"] == "0"))

    p = parse_rollup_h(
        "Operation:SUM\n"
        "Child:ChildObject__c.MasterDetailField__c\n"
        "Field:ChildObject__c.FieldToSummarize__c"
    )
    cases.append(("sum no filter", p["ok"] and p["operation"] == "SUM"
                  and p["summarized_field"] == "ChildObject__c.FieldToSummarize__c"))

    p = parse_rollup_h(
        "Operation:SUM\n"
        "Child:ChildObject__c.MasterDetailField__c\n"
        "Field:ChildObject__c.FieldToSummarize__c\n"
        "Condition:ChildObject__c.FilterField1__c = Value1\n"
        "Condition:ChildObject__c.FilterField2__c != Value2\n"
        "Condition:ChildObject__c.FilterField3__c >= 1"
    )
    cases.append(("sum filters", p["ok"] and [f["operation"] for f in p["filters"]]
                  == ["equals", "notEqual", "greaterOrEqual"]))

    p = parse_rollup_h(
        "Operation:SUM\n"
        "Child:ChildObject__c.MasterDetailField__c\n"
        "Field:ChildObject__c.FieldToSummarize__c\n"
        "Condition:ChildObject__c.Qty__c > valueField:ChildObject__c.MinQty__c"
    )
    cases.append(("valueField", p["ok"] and p["filters"][0].get("valueField")
                  == "ChildObject__c.MinQty__c" and "value" not in p["filters"][0]))

    p = parse_rollup_h(
        "Operation:COUNT\n"
        "Child:ChildObject__c.MasterDetailField__c\n"
        "Condition:ChildObject__c.Name__c ~ JP\n"
        "Condition:ChildObject__c.Name__c !~ test\n"
        "Condition:ChildObject__c.Code__c ^= TI_\n"
        "Condition:ChildObject__c.Tags__c in A;B\n"
        "Condition:ChildObject__c.Tags__c !in X"
    )
    cases.append(("text/ms ops", p["ok"] and [f["operation"] for f in p["filters"]]
                  == ["contains", "notContain", "startsWith", "includes", "excludes"]))

    p = parse_rollup_h(
        "Operation:COUNT\nChild:ChildObject__c.MasterDetailField__c\n"
        "Condition:ChildObject__c.Status__c equals Active"
    )
    cases.append(("word operator still accepted", p["ok"]
                  and p["filters"][0]["operation"] == "equals"))

    p = parse_rollup_h("Operation:SUM\nChild:ChildObject__c.MD__c")
    cases.append(("sum missing field", (not p["ok"]) and any("Field:" in e for e in p["errors"])))

    p = parse_rollup_h("")
    cases.append(("blank H", not p["ok"]))

    p = parse_rollup_h("これは説明です")
    cases.append(("prose not format", not p["ok"]))

    p = parse_rollup_h(
        "Operation:COUNT\nChild:ChildObject__c.MD__c\nField:ChildObject__c.Amt__c"
    )
    cases.append(("count drops field", p["ok"] and p["summarized_field"] == ""
                  and p["warnings"]))

    row = {"Data Type": "Rollup summary", "Type Specific Value":
           "Operation:MIN\nChild:ChildObject__c.MD__c\nField:ChildObject__c.Amt__c"}
    applied = apply_rollup_h(row)
    cases.append(("apply_to_row", applied and applied["ok"]
                  and row.get("Summary Operation") == "MIN"
                  and row.get("Summary Foreign Key") == "ChildObject__c.MD__c"
                  and row.get("Summarized Field") == "ChildObject__c.Amt__c"))

    cases.append(("is_summary_type", is_summary_type("Rollup summary")
                  and is_summary_type("Summary") and not is_summary_type("Lookup")))
    cases.append(("xml op", summary_operation_xml("sum") == "sum"))
    p = parse_rollup_h(
        "Operation: COUNT\nChild: ChildObject__c.MasterDetailField__c"
    )
    cases.append(("space after colon still accepted", p["ok"] and p["operation"] == "COUNT"))

    failed = [name for name, ok in cases if not ok]
    if failed:
        print("FAIL:", ", ".join(failed))
        return 1
    print(f"ok — {len(cases)} rollup_h checks")
    return 0


if __name__ == "__main__":
    raise SystemExit(_self_test())
