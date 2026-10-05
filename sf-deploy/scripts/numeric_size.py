#!/usr/bin/env python3
"""Derive Salesforce <precision>/<scale> from column F (サイズ / length).

Updated by Divakar N.

Client-facing size for Number / Currency / Percent (and those Formula types)
is column F, not T/U. Encoding is SAP-style integer-digits + scale:

    "12, 3"  →  12 digits before the decimal, 3 after
             →  Salesforce precision = 12 + 3 = 15, scale = 3

T (精度) and U (スケール) remain Metadata API fields. They are derived in
memory for XML generation; they are NOT written back to the sheet.

Precedence (sf-numeric-size-column-f.mdc):
  1. F has a valid pair or bare integer → F wins (even if T/U are filled).
  2. F is blank and T+U are both filled → legacy fallback (already-deployed rows).
  3. F is non-blank but unparseable → hard error; never guess, never fall back.
  4. F blank AND T/U blank → hard error.
"""
from __future__ import annotations

import re

# Updated by Divakar N — 2026-09-24.
# True = client fills col F ("12, 3"); T/U are derived in memory, not required.
USE_COLUMN_F_FOR_NUMERIC_SIZE = True

# Sheet Data Type spellings (after stripping spaces / punctuation).
_NUMERIC_TYPE_KEYS = frozenset({
    "number", "currency", "percent",
    "formulanumber", "formulacurrency", "formulapercent",
})

# "12, 3" / "12,3" / full-width comma / Japanese enumeration comma.
_PAIR_RE = re.compile(r"^\s*(\d+)\s*[,，、]\s*(\d+)\s*$")
_INT_RE = re.compile(r"^\s*(\d+)\s*$")


def _type_key(raw: str) -> str:
    return re.sub(r"[\s_（）()/\-]+", "", (raw or "").strip().lower())


def is_numeric_sheet_type(raw_type: str) -> bool:
    """True for Number/Currency/Percent and Formula Number/Currency/Percent."""
    return _type_key(raw_type) in _NUMERIC_TYPE_KEYS


def _to_int(v):
    s = str(v or "").strip()
    if not s:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def parse_f_numeric(length_val) -> tuple[int, int] | None:
    """Parse column F into Salesforce (precision, scale).

    Pair ``integerDigits, scale`` → precision = integerDigits + scale.
    Bare integer → scale 0, precision = that integer.
    Blank or unparseable → None.
    """
    s = str(length_val or "").strip()
    if not s:
        return None
    m = _PAIR_RE.match(s)
    if m:
        integer_digits = int(m.group(1))
        scale = int(m.group(2))
        return integer_digits + scale, scale
    m = _INT_RE.match(s)
    if m:
        return int(m.group(1)), 0
    return None


def check_bounds(precision: int, scale: int) -> str | None:
    if not (1 <= precision <= 18):
        return f"precision ({precision} = integer digits + scale) must be 1–18"
    if not (0 <= scale <= 17):
        return f"scale ({scale}) must be 0–17"
    if scale > precision:
        return f"scale ({scale}) must be <= precision ({precision})"
    return None


def apply_numeric_size(row: dict) -> dict:
    """Mutate ``row`` so Precision/Scale are set from F (or legacy T/U).

    Returns:
      {"ok": None}  — not a numeric type (row unchanged)
      {"ok": True, "source": "F"|"TU", "precision": int, "scale": int}
      {"ok": False, "error": str}
    """
    raw = str(row.get("Data Type") or "").strip()
    if not is_numeric_sheet_type(raw):
        return {"ok": None}

    # Temporary: today's deploy reads T/U only. F is ignored until the flag flips.
    if not USE_COLUMN_F_FOR_NUMERIC_SIZE:
        return _apply_legacy_tu(row, raw)

    f_raw = row.get("Length")
    f_str = str(f_raw or "").strip()
    parsed = parse_f_numeric(f_raw)

    if parsed is not None:
        precision, scale = parsed
        err = check_bounds(precision, scale)
        if err:
            return {"ok": False, "error": f"col F {f_str!r}: {err}"}
        row["Precision"] = str(precision)
        row["Scale"] = str(scale)
        row["_numeric_size_from"] = "F"
        return {"ok": True, "source": "F", "precision": precision, "scale": scale}

    if f_str:
        return {
            "ok": False,
            "error": (
                f"col F {f_str!r} is not a numeric size "
                f"(expected '12, 3' = 12 integer digits + scale 3 → precision 15, "
                f"or a bare integer for scale 0)"
            ),
        }

    # F blank → legacy T/U (Salesforce precision/scale already on the sheet).
    tp, ts = _to_int(row.get("Precision")), _to_int(row.get("Scale"))
    if tp is not None and ts is not None:
        err = check_bounds(tp, ts)
        if err:
            return {"ok": False, "error": f"legacy T/U precision/scale: {err}"}
        row["_numeric_size_from"] = "TU"
        return {"ok": True, "source": "TU", "precision": tp, "scale": ts}

    return {
        "ok": False,
        "error": (
            f"{raw}: col F (サイズ) is empty and T/U (precision/scale) are empty "
            f"— write F as 'integerDigits, scale' (e.g. '12, 3')"
        ),
    }


def _apply_legacy_tu(row: dict, raw: str) -> dict:
    """T/U-only path for the current object deploy (flag off)."""
    tp, ts = _to_int(row.get("Precision")), _to_int(row.get("Scale"))
    if tp is not None and ts is not None:
        err = check_bounds(tp, ts)
        if err:
            return {"ok": False, "error": f"T/U precision/scale: {err}"}
        row["_numeric_size_from"] = "TU"
        return {"ok": True, "source": "TU", "precision": tp, "scale": ts}
    return {
        "ok": False,
        "error": (
            f"{raw}: columns T (精度 / precision) and U (スケール / scale) are "
            f"required for this deploy (col-F numeric size is deferred)"
        ),
    }


def numeric_size_mode_banner() -> None:
    if USE_COLUMN_F_FOR_NUMERIC_SIZE:
        print("ℹ️  numeric size: column F ('12, 3') → precision/scale")
    else:
        print("ℹ️  numeric size: T/U (precision/scale) — col F deferred "
              "(flip USE_COLUMN_F_FOR_NUMERIC_SIZE after this deploy)")


def _self_test() -> None:
    global USE_COLUMN_F_FOR_NUMERIC_SIZE
    cases = [
        ("12, 3", (15, 3)),
        ("12,3", (15, 3)),
        ("12，3", (15, 3)),
        ("12、3", (15, 3)),
        ("18,0", (18, 0)),
        ("12", (12, 0)),
        ("", None),
        ("abc", None),
        ("12.3", None),
    ]
    for raw, exp in cases:
        got = parse_f_numeric(raw)
        assert got == exp, f"parse_f_numeric({raw!r})={got!r} expected {exp!r}"

    saved = USE_COLUMN_F_FOR_NUMERIC_SIZE
    USE_COLUMN_F_FOR_NUMERIC_SIZE = True

    row = {"Data Type": "Number", "Length": "12, 3", "Precision": "", "Scale": ""}
    st = apply_numeric_size(row)
    assert st == {"ok": True, "source": "F", "precision": 15, "scale": 3}, st
    assert row["Precision"] == "15" and row["Scale"] == "3"

    row = {"Data Type": "Currency", "Length": "12, 3", "Precision": "18", "Scale": "2"}
    st = apply_numeric_size(row)
    assert st["source"] == "F" and row["Precision"] == "15"

    row = {"Data Type": "Percent", "Length": "", "Precision": "18", "Scale": "2"}
    st = apply_numeric_size(row)
    assert st == {"ok": True, "source": "TU", "precision": 18, "scale": 2}, st

    row = {"Data Type": "Number", "Length": "数値(18,0)", "Precision": "18", "Scale": "0"}
    st = apply_numeric_size(row)
    assert st["ok"] is False

    row = {"Data Type": "Formula Number", "Length": "", "Precision": "", "Scale": ""}
    st = apply_numeric_size(row)
    assert st["ok"] is False

    row = {"Data Type": "Number", "Length": "16, 3"}
    st = apply_numeric_size(row)
    assert st["ok"] is False

    row = {"Data Type": "Text", "Length": "255"}
    st = apply_numeric_size(row)
    assert st == {"ok": None}

    USE_COLUMN_F_FOR_NUMERIC_SIZE = False
    row = {"Data Type": "Number", "Length": "12, 3", "Precision": "18", "Scale": "2"}
    st = apply_numeric_size(row)
    assert st["source"] == "TU" and row["Precision"] == "18" and row["Scale"] == "2", st
    row = {"Data Type": "Number", "Length": "12, 3", "Precision": "", "Scale": ""}
    st = apply_numeric_size(row)
    assert st["ok"] is False

    USE_COLUMN_F_FOR_NUMERIC_SIZE = saved
    print("numeric_size self-test: OK")


if __name__ == "__main__":
    _self_test()
