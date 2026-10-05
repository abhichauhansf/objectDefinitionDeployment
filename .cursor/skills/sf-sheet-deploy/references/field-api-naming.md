# Salesforce Field API Naming (MANDATORY)

Applies whenever field API names are proposed, generated, finalized, or written
back (`name_fields.py`, `finalize_names.py`, generators, sheet write-back).

Also apply [field-namespace-prefix.md](field-namespace-prefix.md) (`TI_Fnt_`).

## 0. Naming source order — GLOSSARY FIRST, manual only as fallback (MANDATORY)

ALWAYS derive API names from the glossary first; hand-name only what the glossary
cannot resolve. Never start with manual naming.

1. Run `name_fields.py --glossary .build/phrase_glossary.json` for the object. It
   matches each blank field via (a) exact glossary term, (b) base term + number,
   (c) compositional longest-substring — all glossary-driven.
2. ACCEPT the glossary proposal for every field it resolves (High / Medium / Low
   compose). Do not rewrite a valid glossary name by hand.
3. Manual/curated names are allowed ONLY for:
   - fields the glossary returns `none` for (no match), and
   - collisions the auto-dedupe cannot resolve meaningfully.
   Curate just those; feed them through `finalize_names.py` overrides.
4. Report the split every time: `glossary-named` vs `manually-named (none-match)`
   so the reviewer sees how few needed hand-naming.

This order is not optional — the glossary is the source of truth for JP→EN API
terms, and manual naming exists only to fill genuine gaps.

## 0b. Blank custom-field API name is NOT a blocker — GENERATE and write back (MANDATORY)

<!-- Updated by Divakar N — 2026-09-21 -->

**Why:** A new / raw object tab arrives with labels and types filled and col D
(`fullName`) blank. GDC owns the `TI_Fnt_` names, not the client. Treating a
blank Field API Name as a STEP-0 ERROR parks the whole tab (seen on
`TradeTermsAppDetail` 2026-09-21, and earlier on raw tabs such as `Sales_Deal`).
`validate_sheet.py` must NOT emit ERROR `field.api` for a missing custom-field
API name. The correct end state is: generate the name, write it back to col D,
then continue validation / deploy.

A missing `fullName` is **not** the end state. When a custom-field row has a
**blank API name (col D)**, generate and write an appropriate name in the same
task — do not stop at "blocked: blank API", and do not `flag_blocker.py` it.

1. Skip WIP, IsDelete, and STANDARD fields.
2. Label-fallback first if the object exists in the org
   ([blank-api-label-fallback.md](blank-api-label-fallback.md)). Reuse a unique
   org custom-field match. Do not treat standard `Name` as a custom match even
   if labels collide.
3. Genuinely new → `name_fields.py --glossary` then
   `finalize_names.py --prefix TI_Fnt_`. Accept glossary hits; hand-name only
   `none`.
4. Write col D (`cell: (blank) → TI_Fnt_<Component>__c`), gated, then re-validate.

## 1. Never generate API names for STANDARD fields

Standard fields already have fixed, Salesforce-owned API names — never invent,
change, or overwrite them. Leave them exactly as-is.

Treat as standard (non-exhaustive):
- `Id`, `Name`, `OwnerId`, `CreatedById`, `CreatedDate`, `LastModifiedById`,
  `LastModifiedDate`, `SystemModstamp`, `IsDeleted`, `LastActivityDate`,
  `LastViewedDate`, `LastReferencedDate`, `RecordTypeId`, `CurrencyIsoCode`.
- Any field the org describe reports with `custom == false`.
- Any field whose existing API name has no `__c` suffix and is a known system
  field.

If a row already has an API name, do not replace it. Only fill BLANK custom
field API names — and only after IsDelete is checked
([isdelete-column-always.md](isdelete-column-always.md)) and a blank-API
label-fallback match has cleared the row as genuinely new
([blank-api-label-fallback.md](blank-api-label-fallback.md)).

## 2. Every CUSTOM field API name MUST end with `__c`

- The API name emitted for a custom field is the component name **plus `__c`**
  (e.g. `SalesOrganization__c`, `Incoterms__c`). Do not output the bare
  component as the API name.
- For Lookup / Master-Detail fields, the FIELD api name still ends with `__c`
  (the relationship name uses `__r`, but that is a separate value).
- Do not double-suffix (never `Foo__c__c`); append `__c` only if absent.

## 3. Validity + limits (enforce before proposing/writing)

- Pattern: component matches `^[A-Za-z][A-Za-z0-9]*$` (letters/digits, starts
  with a letter, no spaces, no leading/trailing underscore before the suffix).
- Length: full API name including `__c` must be **<= 40 characters**. If a
  translated name is too long, shorten sensibly (drop filler, use accepted
  abbreviations) — never silently truncate mid-word; flag it for review.
- Uniqueness: API names must be unique within the object (case-insensitive).
  Resolve collisions with a meaningful qualifier, not a bare trailing number,
  where possible.

## 4. Reporting

When producing a naming file, surface: count named, count backed by glossary,
any names shortened for the 40-char limit, and any collisions resolved — so the
user can review before any sheet write. Writing names back to the sheet remains
gated by [sheet-write-confirmation.md](sheet-write-confirmation.md).
