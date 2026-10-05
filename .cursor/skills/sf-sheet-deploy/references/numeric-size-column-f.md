<!-- Updated by Divakar N — 2026-09-24: col-F numeric size is ON. -->

# Numeric Size Lives in Column F (client fills F only)

The client must not be asked to fill columns **T** (`精度` / precision) and **U**
(`スケール` / scale). Those two are Salesforce Metadata API elements. The
**client-facing size cell is always column F** (`サイズ` / `length`, idx 5).

Canonical always-apply rule: `.cursor/rules/sf-numeric-size-column-f.mdc`.

## Column F by data type

| Data Type | What the client writes in F | What we emit in metadata |
|-----------|-----------------------------|--------------------------|
| Text, EncryptedText | integer length (1–255 / 1–175) | `<length>` |
| LongTextArea, Html (Rich Text Area) | integer length (256–131072) | `<length>` |
| TextArea | optional / ignore | omit `<length>` (fixed 255) |
| **Number, Currency, Percent** | **`"integerDigits, scale"`** | `<precision>` + `<scale>` |
| **Formula Number / Currency / Percent** | same pair as above | `<precision>` + `<scale>` |

## Numeric pair in F (MANDATORY encoding)

Format: `"<digits before decimal>, <digits after decimal>"`.

Example: **`"12, 3"`**

- `12` = integer digits (left of the decimal)
- `3` = scale (right of the decimal)
- Salesforce **precision = 12 + 3 = 15**
- Salesforce **scale = 3**

`"12, 3"` is **not** precision 12 / scale 3 (that would allow only 9 integer
digits). This is SAP-style length/decimals, not Salesforce's native pair.

Accepted spellings: `12, 3` / `12,3` / full-width `12，3` / `12、3`.
A bare integer in F (`12`) on a numeric type = scale 0 → precision 12, scale 0.

Constraints (Salesforce): precision (`integerDigits + scale`) is 1–18; scale is
0–17; scale ≤ precision. Unparseable F → hard blocker; never guess.

## Columns T and U

- Still required **inside generated XML** (`<precision>`, `<scale>`).
- **Blank T/U is NOT a blocker** when F has a valid numeric pair (or a legacy
  integer) on Number/Currency/Percent / their Formula types.
- **Do not write derived precision/scale back to T/U** — that re-confuses the
  client. Column F is the sheet value cell for numeric size.
- **Legacy fallback:** if F is blank but T and U are both filled, use T as
  Salesforce precision and U as scale so already-deployed rows keep working.
  If F has a valid pair, F wins even when T/U also have values.

## Pipeline (keep scripts aligned)

`fetch_sheet.py` / `validate_sheet.py` / `generate_xml.py` / `attr_drift.py`
must parse F for numeric types and derive Precision/Scale **before** requiring
T/U. Drift compares the org against the **derived** precision/scale, not blank
T/U. Blank F **and** blank T/U on a numeric custom field remains a Step-0
blocker (`flag_blocker.py`).
