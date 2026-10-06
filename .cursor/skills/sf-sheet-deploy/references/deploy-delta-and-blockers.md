# Deploy: Blocker-Scan → Existence → Delta-Only → Post-Deploy Validation (MANDATORY)

Whenever the user asks to deploy ANY object or its fields to a target org, you
MUST run the ordered flow below, every time, for EACH object in the request
(single-object AND multi-object commands alike). No step is optional, no step is
skippable "because we just did it". This rule LAYERS ON TOP OF
[object-existence.md](object-existence.md) — it does not replace it.

`SHOOT` password text below (if any leftover) is void —
see [deploy-authorization.md](deploy-authorization.md).

## STEP 0 — PRE-DEPLOY SHEET BLOCKER SCAN (HIGHEST PRIORITY, runs FIRST)

Before anything else — before existence checks, before packaging, before any
dry-run — you MUST read the object's sheet tab(s) **live** (per
[read-sheet-live.md](read-sheet-live.md)) and scan every candidate row for entries
that would BLOCK or corrupt the deploy. This is the single most important gate;
if Step 0 is not clean (or explicitly accepted by the user), you do NOT proceed.

Scan for blockers including (non-exhaustive):
- Blank required attributes for the field's type (e.g. `type`, picklist values,
  `length`/`precision`/`scale`, Lookup/Master-Detail `referenceTo`).
  <!-- Updated by Divakar N -->
  - **EXCEPTION — blank Field API Name (col D) is NOT a hard blocker.** A
    custom-field row with an empty `fullName` must NOT be parked and must NOT
    be flagged via `flag_blocker.py`. GDC owns `TI_Fnt_` names; the client
    ships labels/types first. Generate `TI_Fnt_<Component>__c` (glossary first,
    label-fallback if the object already exists), write it back to col D
    (gated) plus an AH `[FIX]` note, then continue. See
    [field-api-naming.md](field-api-naming.md) §0b. **Why:** treating blank
    col D as ERROR parked whole new tabs (`TradeTermsAppDetail` 2026-09-21).
    Skip WIP / IsDelete / standard rows — those are never named.
  - **EXCEPTION — Number/Currency/Percent size is column F, not T/U.** The
    client writes `"integerDigits, scale"` in F (e.g. `"12, 3"` → Salesforce
    precision 15, scale 3). Blank T (`精度`) / U (`スケール`) is NOT a blocker
    when F is valid. Blank F **and** blank T/U still is. Do not write derived
    precision/scale back to T/U (see [numeric-size-column-f.md](numeric-size-column-f.md)).
  - **EXCEPTION — empty FORMULA body is NOT a hard blocker.** A Formula field
    whose formula body (col **H**, header substring `設定値` — NEVER col G, which
    is a human DESCRIPTION) is blank must NOT be parked: Salesforce rejects an
    empty formula, so `generate_xml.py` AUTO-INJECTS a return-type-appropriate
    DUMMY formula (see [dummy-formulas.md](../assets/dummy-formulas.md))
    so the field stays deployable. The injected dummy is a deployment fix and is
    therefore subject to the MANDATORY attribute write-back (STEP 0 §3 /
    `write_attr_fixes.py`): write the dummy back into the field's col-H value cell
    (gated) and note it in AH, so the sheet matches the org. The real formula is
    supplied later by the client.
- Missing `relationshipName` on a Lookup / Master-Detail row **only when col H
  (`設定値` / referenceTo) is also blank.** If col X is blank but col H has the
  lookup object, that is NOT a blocker:
  - field already in the org → write back the org's `relationshipName` to col X
    (never invent a new child-relationship name on an existing field). The org
    value is read live (`readMetadata`) by `validate_sheet.py --target-org` and
    used by `generate_xml.py`; a filled col X that differs from the org is a
    WARN and the org value is kept. If the org cannot be read, blank X is an
    ERROR — never fall back to deriving;
  - new field → auto-fill from the field API (minus `__c`; object-scoped for
    shared parents like User) via `relname.derive_relationship_name`, write it
    back to col X, and proceed. Shortening and Fnt/Logi qualifying apply to new
    fields only.
  <!-- Updated by Divakar N — 2026-10-06. -->
- API names violating [field-api-naming.md](field-api-naming.md) /
  [field-namespace-prefix.md](field-namespace-prefix.md)
  (bad pattern, `> 40` chars, missing `__c`, wrong/duplicated prefix).
- Duplicate API names within the object (case-insensitive collisions).
- Would-be relationship count pushing the object past the 40 custom-relationship
  limit.
- Rows flagged WIP (column AE header `WIP` = `TRUE`/`true`/`X`/`x`) — these must be
  IGNORED entirely, never deployed (per [sheet-columns.md](sheet-columns.md)).
- Rows flagged IsDelete (column AD = `TRUE`/`true`) — these are DELETE requests,
  NOT create/update; read AD FIRST
  ([isdelete-column-always.md](isdelete-column-always.md)), route them to the
  separate destructive DELETE set (below), and exclude them from the normal field
  package. Never AI-name or label-fallback-reuse an IsDelete row.
- Blank API name (col D) on a custom-field row is NOT automatically "new" —
  run the label-fallback match
  ([blank-api-label-fallback.md](blank-api-label-fallback.md)) before creating.

Use `validate_sheet.py` (and the object's live field list) to produce the
blocker set. Then:

1. **FLAG EVERY BLOCKER THROUGH THE ONE COUPLED COMMAND — `flag_blocker.py`.**
   The three blocker actions — (a) pink-highlight the row's Column A ("No.") cell,
   (b) write a `[VALIDATION <date>] <sev> <check>: <message>` note in AH, and
   (c) report it — MUST be done together via the single coupled entry point so
   none is ever skipped (this is exactly how `Forwarder`'s highlight was missed
   while its AH note was written):
   ```
   # single field
   python scripts/flag_blocker.py --tab <Tab> \
       --object <Obj>__c --field <API>__c --check <check> --message "<why>"   # +--apply
   # many fields
   python scripts/flag_blocker.py --tab <Tab> \
       --object <Obj>__c --blockers <json:[{field,check,message,severity?}]>   # +--apply
   ```
   It internally calls `write_ai_comments.py` (AH) and `highlight_blockers.py`
   (Column A only), is DRY-RUN by default, and writes only with `--apply`. Both
   underlying writes are **gated sheet writes** — preview the AH `cell: old → new`
   diff and the highlighted row, get confirmation, THEN `--apply`. Do NOT call the
   two sub-scripts by hand for blocker flagging (hand-calling one and forgetting
   the other is the failure this command exists to prevent).
   - **Late-discovered blockers count too.** Any field left undeployed by a
     dry-run failure, an attribute-drift you are not redeploying, a missing
     `referenceTo`/`relationshipName`, or a dup API name — even if found MID-FLOW
     (not in the initial STEP-0 scan) — MUST be routed through `flag_blocker.py`,
     not just noted in chat.
   - `severity=ERROR` gets highlighted + commented; `severity=WARN` is
     comment-only (no highlight).
2. **Report the blockers in plain English** (row No., field, why it blocks) and
   STOP. Do not deploy a set that still contains unresolved blockers.
   (`flag_blocker.py` already prints this report; restate it to the user.)
3. **MANDATORY ATTRIBUTE WRITE-BACK — the sheet value cell, not just AH.** If you
   change/fill/repair ANY field attribute in order to make a deploy succeed (e.g.
   a formula body in `設定値` (col H — Type Specific Value; never col G), a
   blank `relationshipName` (col X), an API name, a length/precision, a picklist
   value set, a `referenceTo`, a `deleteConstraint`), that fixed value MUST be
   written back into the CORRESPONDING SHEET VALUE CELL so the sheet (source of
   truth) matches what was deployed to the org. Use
   `scripts/write_attr_fixes.py --tab <Tab> --fixes <json>` (locates the column by
   header name; preview `cell: old → new`, then `--apply`). This is a **gated**
   sheet write ([sheet-write-confirmation.md](sheet-write-confirmation.md)): show
   the diff and confirm before `--apply`. This is IN ADDITION to the AH `[FIX]`
   note (Step 2 §6) — the AH note documents WHY, the value cell carries WHAT. A
   local-build-artifact-only fix is NOT sufficient and is NOT allowed as an end
   state: never leave the sheet value cell diverging from the org. The ONLY
   exception is when the user, for a SPECIFIC field, explicitly instructs you to
   keep the sheet cell as-is — and even then you must state the divergence plainly
   and record it in AH. Never write speculatively.
4. Proceed to Step 1 ONLY once the scan is clean or the user has explicitly
   accepted/parked every remaining blocker.

## STEP 1 — OBJECT-EXISTENCE CHECK (live, per object)

Follow [object-existence.md](object-existence.md) exactly:
- Query the target org live for the object (EntityDefinition).
- Object MISSING → the deploy MUST include the CustomObject itself + its fields.
- Object EXISTS → go to Step 2 (delta) and deploy fields only.

Never assume existence from a cached describe or a prior deploy log.

## STEP 2 — DELTA COMPUTATION: deploy ONLY what is new (object EXISTS)

When the object already exists, you MUST NOT blindly redeploy every sheet field.
Compute the delta between what is ALREADY in the org and what the sheet defines,
and deploy ONLY the new (and, if explicitly requested, changed) fields.

1. **Fetch the org's current field set LIVE via the Tooling API** (FLS-
   independent — do NOT use `sobject describe` / `FieldDefinition`, which hide
   fields whose FLS the running user lacks and produce a false "missing"):
   ```
   sf data query --use-tooling-api --target-org "<ORG>" --json --query \
     "SELECT DeveloperName FROM CustomField \
      WHERE EntityDefinition.QualifiedApiName='<Obj>__c'"
   ```
   (Tooling `DeveloperName` omits `__c` — re-add it when comparing.)
2. **Build the sheet's intended field set** live from the object tab (skip WIP
   rows / column AE `WIP` = `TRUE`/`true`/`X`/`x`; skip standard fields per
   [field-api-naming.md](field-api-naming.md); exclude IsDelete rows — they go
   to the DELETE set).
3. **Diff:** `delta = sheet_custom_fields − org_existing_fields`.
   - `delta` = fields to deploy (new in sheet, absent in org).
   - fields present in BOTH are already deployed BY NAME → EXCLUDE from the create
     package, but they MUST still be run through the attribute-drift check in §3b
     (name-existence does NOT mean the definition matches).
   - fields present in org but ABSENT from the sheet → do NOT delete; just
     report them so the user is aware.
3b. **ATTRIBUTE-LEVEL DRIFT (MANDATORY for fields present in BOTH).** The name
   delta only proves a field EXISTS — not that its DEFINITION matches the sheet. A
   field whose sheet TYPE/FORMULA/referenceTo/PICKLIST changed but whose API name
   is unchanged is invisible to the name delta and gets silently skipped (this is
   exactly how ~27 Deal fields — Lookup→Formula, Checkbox→Picklist, picklist-value
   edits — sat undeployed). For EVERY existing object you MUST compare definitions:
   ```
   python scripts/attr_drift.py --object <Obj>__c --rows temp_updates.json --org <ORG>
   ```
   It reads the org's ACTUAL metadata via the Metadata API `readMetadata`
   (FLS-independent) and compares, per field, **type, formula, referenceTo,
   picklist values, length, visibleLines, precision, scale, required, unique, and
   externalId** (and flags any unmapped Data Type). It writes
   `.build/attr_drift_<Obj>.json`. `prep_deploy.py` runs this automatically in its
   build phase (step 6b). SURFACE any drift to the user. Redeploying drift is a
   user decision, not automatic — many type changes (Lookup↔Formula,
   Checkbox↔Picklist, Text→Lookup) require delete+recreate and DESTROY the
   field's data, so never silently redeploy a drifted field.
   **STANDARD `Name` field is MANDATORY on every drift run**
   ([drift-includes-standard-fields.md](drift-includes-standard-fields.md)):
   compare sheet `Name Field Type` / `Name Field Display Format` against the org
   CustomObject `<nameField>`. A custom-only drift check is a false "all clear".
   Changing Name Text→AutoNumber is DESTRUCTIVE — never auto-redeploy; get
   explicit user approval. Drift is also enforced on the SOAP path
   (`mdapi_deploy.py` `_drift_gate()`); abort real deploy on unreviewed drift
   unless `--ack-drift`.
3c. **TRANSLATION-LEVEL DRIFT (MANDATORY for fields present in BOTH).** The name
   delta also does NOT prove the **English label** matches the sheet. A field
   whose `Field Label (EN)` / `Translation Provenance` changed while its API name
   stayed the same must still be packaged as `CustomObjectTranslation`
   (`CHANGED_TRANSLATION`). `prep_deploy.py` prints this as a separate
   TRANSLATIONS delta (new / changed / unchanged). Post-deploy `verify_deploy.py`
   compares **every** in-scope sheet English value to the live org, so a later EN
   edit cannot be reported as "complete" while the org still has the old label.
3d. **JAPANESE LABEL DRIFT — THE SHEET OVERWRITES THE ORG (automatic).** The
   sheet is the master for Japanese labels. For every existing object,
   `prep_deploy.py` compares the sheet `Object Label`, `Name Field Label` and
   each custom `Field Label` with the org (`readMetadata`, FLS-independent) and
   packages every difference, printed as `LABELS (JA)`. `label_sync.py` builds
   the relabel from the org's CURRENT definition with only `<label>` replaced, so
   it never redeploys attribute drift (§3b stays a user decision).
   `verify_deploy.py --plan` fails if a planned relabel did not land.
4. **Report the delta before deploying:** e.g. "org already has N fields; sheet
   defines M; deploying the D new ones; X already present (skipped); Y org-only
   (left untouched)." **Also report translation new/changed.** Keep the schema
   package limited to the field-name delta so redeploys stay fast; still include
   every CHANGED/NEW English label in the translation package.
5. Package + validate (check-only dry-run) the delta, then deploy for real with
   `deploy.py --start` (the ONLY flag that writes).
6. **Log every on-the-go fix in TWO places — the value cell AND the AH note.**
   If, while packaging or deploying, you fix any attribute value (fill a blank
   `relationshipName`, supply a placeholder/real formula, resolve a collision,
   adjust length/precision/picklist values, etc.) you MUST:
   (a) **Write the fixed value back into the field's SHEET VALUE CELL** via
   `scripts/write_attr_fixes.py` (per Step 0 §3 — mandatory, gated), so the sheet
   matches the org; AND
   (b) record a `[FIX <date>] <note>` on that field's AH cell via
   `scripts/write_ai_comments.py --fixes <fixes.json> --object <Obj>__c`.
   Both are gated sheet writes — preview + confirm before `--apply`. Writing ONLY
   the AH note (or ONLY the local build artifact) is NOT sufficient: the value cell
   must carry the deployed value. After write-back, re-read the sheet live and
   confirm the value cell equals the org value (close the sheet↔org loop).

## STEP 2b — DELETE SET: fields flagged IsDelete (column AD = TRUE)

Rows whose `IsDelete` (AD) = `TRUE`/`true` are DELETIONS, handled separately from
the create/update delta. `fetch_sheet.py` already EXCLUDES them from the create
package; `build_destructive.py` handles the actual deletion:

1. **Scan + build (safe, no writes):**
   ```
   python scripts/build_destructive.py \
       --tabs "<tab1>,<tab2>" --target-org "<ORG>"
   ```
   It skips WIP rows, requires a valid custom Field API Name, and with
   `--target-org` verifies each field EXISTS in the org (Tooling API) — fields
   already absent are dropped as no-ops. It writes `manifest/destructiveChanges.xml`
   (CustomField members `<Obj>__c.<Field>__c`) + an empty `manifest/destructive_package.xml`.
   With `--target-org` it also exits **2 (BLOCKED)** if a field to delete is
   still placed on a Lightning page.
1b. **Unplace from the Lightning page first (the ONE page-freeze exception).**
   For each blocked field: retrieve the listed FlexiPage(s), remove ONLY the
   `fieldInstance` entries for the IsDelete field(s) (`Record.<Field>__c`), and
   deploy the page through `deploy.py` (dry-run, then `--start`). Change nothing
   else on the page and do not touch `actionOverrides`. Confirm live, then
   re-run step 1. See [post-deploy-fls-and-flexipage.md](post-deploy-fls-and-flexipage.md).
   <!-- Updated by Divakar N — 2026-10-06. -->
2. **Review** the printed delete set with the user — deletion is irreversible and
   also destroys the field's data.
3. **Delete for real (DESTRUCTIVE):**
   ```
   python scripts/deploy.py --start \
       --pre-destructive manifest/destructiveChanges.xml \
       --package manifest/destructive_package.xml \
       --target-org "<ORG>" --test-level NoTestRun --skip-validation-gate
   ```
   Do not silently batch this under a create deploy — surface it as its own step.
4. **Verify + log:** confirm live (Tooling `CustomField`) that each field is gone,
   set AI to `Deleted`
   ([deploy-status-column-always.md](deploy-status-column-always.md)),
   log a `[FIX <date>] deleted per IsDelete` note in AH (gated), re-evaluate AH
   ([ah-comment-reevaluate.md](ah-comment-reevaluate.md)), and refresh the
   object report.

## STEP 3 — POST-DEPLOY VALIDATION: fetch from org, validate against the SHEET

After the real deploy returns, do NOT trust the deploy log. Fetch the org's
field set LIVE again and validate it against the SHEET's intended set (not just
against the delta), so you confirm the WHOLE object matches the source of truth.

1. Run `verify_deploy.py --target-org "<ORG>" --objects <Obj>__c` (derives the
   expected object+fields from the generated metadata and confirms them in the
   org via the Tooling API). Exit 0 = every expected field confirmed; non-zero =
   HARD FAILURE regardless of any "Succeeded" text in the log.
2. **Sheet-completeness check:** re-fetch the org field set live and confirm that
   EVERY non-WIP custom-field entry in the sheet is now present in the org. Report
   any sheet entry still missing from the org as a FAILURE to investigate — do
   not claim success while sheet rows remain unlanded.
3. On success, create/refresh the object's tab in
   `sf-deploy/reports/Object_Deployment_Report.xlsx` via `build_object_report.py`
   (mandatory for every deployed object).

## Guardrails

- Step 0 (blocker scan) ALWAYS runs first and is the top priority; a deploy with
  unresolved, unaccepted blockers must not proceed.
- All sheet reads are LIVE ([read-sheet-live.md](read-sheet-live.md)); never
  evaluate blockers or the delta from a cached snapshot.
- Any sheet write (blocker highlight, API-name/relationshipName fill) is gated by
  [sheet-write-confirmation.md](sheet-write-confirmation.md) — one confirmation
  per distinct change set.
- Delta-only is the default for existing objects; redeploying already-present
  fields requires an explicit user request.
- Existence checks, delta queries, and post-deploy verification must all use the
  Tooling API for field existence (FLS-independent), never `sobject describe`.
- **Name-delta is NOT enough — attribute drift is MANDATORY.** For every existing
  object, run `scripts/attr_drift.py` (auto-run by `prep_deploy.py` build step 6b
  AND by `mdapi_deploy.py` `_drift_gate()`) to compare the sheet's field
  DEFINITIONS (type/formula/referenceTo/picklist **and standard Name**) against
  the org's live metadata, and surface any drift. A field that exists by name
  but whose definition changed is a real, un-deployed change — never treat
  "exists in org" as "matches the sheet." A drift run that skips `Name` is
  incomplete.
- **Attribute write-back is MANDATORY, not optional.** Any field attribute you
  fix to enable a deploy (formula, `relationshipName`, API name, length/precision,
  picklist set, `referenceTo`, etc.) MUST be written back into the field's SHEET
  VALUE CELL (`write_attr_fixes.py`, gated), in addition to the AH `[FIX]` note.
  A fix that lives only in a local build artifact / only in AH leaves the sheet
  diverging from the org and is a defect — close the loop every time and verify
  the value cell live afterwards. Only a per-field explicit user opt-out excuses it.
