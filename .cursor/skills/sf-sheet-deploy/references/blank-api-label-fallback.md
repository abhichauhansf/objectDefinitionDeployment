# Blank-API Rows: Label-Fallback Match Before Creating New Fields (MANDATORY)

API name is ALWAYS the authoritative match key. This rule adds a **safety
double-check ONLY for rows whose API name (col D) is BLANK**, so we never create
a duplicate field for one that already exists in the org.

Layers on [deploy-delta-and-blockers.md](deploy-delta-and-blockers.md) and
[field-api-naming.md](field-api-naming.md). IsDelete is checked FIRST
([isdelete-column-always.md](isdelete-column-always.md)) — a blank-API row that
is ALSO IsDelete is a deletion, not a label-reuse candidate.

## Why this exists (real incident)

`TI_Fnt_Receiving__c` fields were named + deployed (Aug 10) and their API names
written back to col D of the old `Sales_Receiving` tab (confirmed by an Aug-20
snapshot: 119/122 filled). The client later re-laid the workbook into the new
"Format" layout: the new `Receiving` tab kept the LABELS but came in with a
BLANK API-name column. A naive "blank API ⇒ new field" would have AI-named and
deployed ~90 fields that already existed → mass duplicates. Label-fallback
matching catches exactly this.

## Required flow (per object, before deploy)

1. **Primary match = API name (col D).** Rows with a valid `__c` API name are
   matched to the org by API name as usual (delta + attribute-drift).
2. **For every row with a BLANK API name**, do NOT go straight to "new". First
   run a **label-fallback match** against the org's LIVE field set:
   - Pull org fields FLS-independently (Metadata `readMetadata(CustomObject)` for
     labels, or Tooling `CustomField` — never `sobject describe`/`FieldDefinition`).
   - **Normalize** both sides before comparing: trim; unify full-width↔half-width;
     collapse/strip spaces (incl. `　`); normalize `（）→()`, `／→/`; case-insensitive.
   - Cross-check the local Aug naming record too (`.build/*_FINAL.json`) when present.
3. **Branch on the label match:**
   - **Exactly one org field matches the label** → it ALREADY EXISTS. Reuse that
     org field's API name; **write it back into col D** (gated sheet write) — do
     NOT create a new field.
   - **No org custom-field matches** → genuinely new → **immediately generate**
     the API name per [field-api-naming.md](field-api-naming.md) (glossary
     first, `TI_Fnt_` + `__c`) and write it to col D (gated). Do not leave the
     row blank and do not stop at the validation ERROR. Then deploy.
   - Do **not** count the standard `Name` field as a custom-field label match
     (duplicate labels like Name + Text both 数量単位): Name stays `Name`.
   - **Label matches >1 org field** (duplicate labels, e.g. `…Reason__c` /
     `…Reason2__c`) → AMBIGUOUS: never auto-pick. Surface the candidates and
     confirm the mapping (or map by sheet row order) with the user. Check
     IsDelete on each twin first ([isdelete-column-always.md](isdelete-column-always.md)).
4. **Report the split before any write/deploy:** `blank-API rows → reused via
   label (N) / genuinely new (M) / ambiguous (K)`, so the reviewer sees how many
   "new" fields are actually pre-existing.

## Guardrails

- Label matching is a DOUBLE-CHECK, not the primary key — API name wins whenever
  present. Labels can drift/duplicate, so a label match only *reuses* an existing
  API; it never *renames* or *overwrites* an existing field.
- Reused API names written back to col D are a **gated sheet write**
  ([sheet-write-confirmation.md](sheet-write-confirmation.md)): preview
  `cell: (blank) → TI_Fnt_…__c` and confirm before `--apply`.
- All org reads are LIVE and FLS-independent; all sheet reads are LIVE
  ([read-sheet-live.md](read-sheet-live.md)).
- Never deploy a "new" field for a blank-API row until the label-fallback check
  has cleared it as genuinely absent from the org.
