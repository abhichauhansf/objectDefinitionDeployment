<!-- Updated by Divakar N -->

# Object-tab column map (header-driven; indices are fallback only)

Locate columns by **header name**. Full rules: [sheet-columns.md](../references/sheet-columns.md).

| Col | idx | Header | Role |
|-----|----:|--------|------|
| A | 0 | No. | Row number. `END[項目]` ends the field list. Highlight this cell only. |
| D | 3 | Field API Name | Blank ≠ new. Label-fallback first. |
| E | 4 | Data Type (`データ型`) | Field type. |
| F | 5 | Size (`サイズ` / `length`) | **Client-facing size.** Text → char length. Number/Currency/Percent → `"integerDigits, scale"` (e.g. `"12, 3"` = precision 15, scale 3). See [numeric-size-column-f.md](../references/numeric-size-column-f.md). |
| G | 6 | (JP description / `データ型に応じて…`) | Human DESCRIPTION only. **Do not read as value.** |
| H | 7 | Type Specific Value (`設定値`) | REAL formula / picklist / referenceTo / displayFormat. Dummy formulas write back here. |
| L | 11 | Default Value (`デフォルト`) | Real default. Do not confuse with H. |
| T | 19 | Precision (`精度`) | Metadata API `<precision>` only. Client does **not** fill this — derived from F for numeric types. Legacy T+U still accepted if F is blank. |
| U | 20 | Scale (`スケール`) | Metadata API `<scale>` only. Same as T: do not ask the client to fill it. |
| X | 23 | relationshipName | Lookup/MD relationship name. Existing field: the org's value. New field: field API minus `__c`. |
| Y | 24 | deleteConstraint | Lookup delete: `SetNull` / `Restrict` / `Cascade`. Blank = generator default. |
| Z | 25 | Tab | FlexiPage top-level tab. |
| AA | 26 | section | FlexiPage section within a tab. |
| AB | 27 | SAPItemName | Ignore (discussion). |
| AC | 28 | sourceItemNo | Ignore (discussion). |
| AD | 29 | IsDelete | `TRUE` → destructive delete set. WIP wins if both set. |
| AE | 30 | WIP | `TRUE`/`X` → skip entirely. |
| AF | 31 | ConfirmComments | Ignore (discussion). |
| AG | 32 | reviewComments | Ignore (discussion). |
| AH | 33 | GDC AI Tool Comments | AI notes. Also match legacy `FreeColumnGDC1`. |
| AI | 34 | Deployment Status | `Deployed` / `Not Deployed` / `Standard` / `Deleted`. Also match legacy `FreeColumnGDC2`. |
| AJ–BA | 35–52 | FreeColumnGDC3–20 | Spare AI columns. Unassigned. |
| BB–BV | 53–73 | actor / Persona | FLS later. Ignore for now. |
| BW+ | 74+ | FreeColumnJP1… | Ignore (discussion). |

## Object-meta header (Create Tab)

| Cell | Meaning |
|------|---------|
| H5 | Label `タブ作成 (Create Tab)` |
| H6 | `TRUE` → create CustomTab; blank/`FALSE` → skip. Then grant tab visibility. |

## Block-end markers (Column A)

| Marker | Closes |
|--------|--------|
| `END[項目]` | **Field list** (stop field scan here) |
| `END[レコードタイプ]` | Record Types |
| `END[入力規則]` | Validation Rules |
| `END[ルックアップ検索条件]` | Lookup Search Filters |
