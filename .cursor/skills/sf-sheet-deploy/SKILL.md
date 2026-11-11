---
name: sf-sheet-deploy
description: >-
  Runs the Salesforce Data-Dictionary pipeline (Google Sheet → validate →
  package → deploy) from sf-deploy/. Use when fetching or validating a DD sheet,
  naming Salesforce fields (TI_Fnt_), deploying objects/fields, writing back to
  the sheet, granting FLS, building FlexiPages, granting tab visibility, checking
  IsDelete/WIP/drift (including standard Name), or any Toray sheet-to-org work.
---

# sheet-deploy — Sheet → Org Deployment

Working directory is always `sf-deploy/`. The Google Sheet is the single source
of truth. **Default spreadsheet ID** lives in
`sf-deploy/scripts/sheet_config.py` (`DEFAULT_SPREADSHEET_ID`). Omit
`--spreadsheet-id` to use it. To switch workbooks, edit **only** that file.
The rules in `.cursor/rules` are the only source of truth. This skill links to
them and does not copy them, so there is no second version to drift; read the
linked rule when that step is active. Before every deploy, apply past lessons in
[deployment_knowledge.md](../../deployment_knowledge.md).
`log_failure.py` appends DRAFT lessons to the same knowledge base.

**Authoritative policy:** there is **no `SHOOT` password**. Residual `SHOOT`
text in older notes is void. See [deploy-no-password-policy.mdc](../../rules/deploy-no-password-policy.mdc).
Sheet-write confirmation is **not** a password — it still applies.

## Hard gates (never skip)

1. **Scope** — confirm object tab(s) before ANY sheet/deploy work. Never assume
   the whole sheet. Never reuse a prior task's scope.
2. **Live reads** — fetch the sheet via the API now. Never answer from
   `temp_updates.json`, `.build/whole_sheet.json`, conversation memory, or
   pasted excerpts. See [read-sheet-live.mdc](../../rules/read-sheet-live.mdc).
3. **Sheet writes** — preview `cell: old → new`, get confirmation, then
   `--apply`. One confirmation per change set.
   See [sheet-write-confirmation.mdc](../../rules/sheet-write-confirmation.mdc).
4. **Org** — `python scripts/deploy.py --list-orgs`, user picks one, pass
   `--target-org` / `--org` explicitly. No silent default.
5. **Validation** — any ERROR in `.build/validation_report.json` stops generate /
   package / deploy.
6. **Dry-run first** — check-only before every real deploy.
7. **Live verify** — `verify_deploy.py` exit 0 required. Never trust
   `.build/last_deploy.log` alone (it is overwritten by dry-run and real deploy).
   Query Tooling `CustomField` with **no** `LIKE 'TI_Fnt_%'` filter.
8. **Self-correction** — complete every DRAFT lesson in
   [deployment_knowledge.md](../../deployment_knowledge.md) before claiming
   success. See [sf-deploy-self-correction.mdc](../../rules/sf-deploy-self-correction.mdc).
9. **IsDelete first** — read AD live before naming, delta, drift, or dedupe.
   WIP wins. See [sf-isdelete-column-always.mdc](../../rules/sf-isdelete-column-always.mdc).
10. **Value column = H (`設定値`)** — never read col G (description) as formula /
    picklist / referenceTo. Locate by JP substring, not English machine headers.
11. **Blank API ≠ new** — label-fallback match the org before creating.
    See [sf-blank-api-label-fallback-match.mdc](../../rules/sf-blank-api-label-fallback-match.mdc).
12. **Drift includes `Name`** — type + AutoNumber `displayFormat` every run.
    Custom-only drift is a false all-clear. Name Text→AutoNumber is destructive —
    never auto-redeploy. See [sf-drift-includes-standard-fields.mdc](../../rules/sf-drift-includes-standard-fields.mdc).
13. **AI status by operation** — `Deployed` / `Deleted` / `Not Deployed` /
    `Standard`. A create/delete is not done until AI matches the org.
    See [sf-deploy-status-column-always.mdc](../../rules/sf-deploy-status-column-always.mdc).
14. **AH re-evaluate** — on every field-state change, de-stale AH notes.
    See [sf-ah-comment-reevaluate-on-change.mdc](../../rules/sf-ah-comment-reevaluate-on-change.mdc).

## Preferred entry

```
# prepare + validate + existence + check-only (no org writes)
python scripts/prep_deploy.py --org "<ORG>" --tabs "<tab1>,<tab2>"

# real deploy + live verify + report (no password)
python scripts/prep_deploy.py --org "<ORG>" --phase deploy --tabs "<tab1>,<tab2>"
```

`scripts/run.py` refuses to run without `--tabs` unless `--all-tabs` is passed.
Hand-running individual scripts is allowed; the same gates apply. Drift is also
enforced on the SOAP path (`mdapi_deploy.py` `_drift_gate()`).

Do **not** hand-run `sf project deploy start` for the real path — wrappers
auto-log failures. `--start` is the only `deploy.py` flag that writes; there is
no `--run` alias.

## Pipeline (fixed order)

Before step 1: list tabs (`python scripts/fetch_sheet.py --list-tabs`),
wait for explicit selection, then proceed.

1. **Fetch** — `python scripts/fetch_sheet.py --tabs "<tab1,tab2>"`
2. **Blocker scan (STEP 0)** — live scan; flag via `flag_blocker.py` only
   (coupled AH note + Column A highlight). Unresolved blockers stop the deploy.
   Empty **formula** body (col H) is **not** a hard blocker (dummy formula is
   injected and written back).
   <!-- Updated by Divakar N — 2026-09-21 -->
   **Blank Field API Name (col D) is also NOT a hard blocker** — generate
   `TI_Fnt_<Component>__c` and write it back (gated). See
   [sf-deploy-delta-and-blockers.mdc](../../rules/sf-deploy-delta-and-blockers.mdc) and
   [sf-field-api-naming.mdc](../../rules/sf-field-api-naming.mdc) §0b.
3. **Validate** — `python scripts/validate_sheet.py --in temp_updates.json --json .build/validation_report.json`
4. **Existence** — live `EntityDefinition` per object. Missing object → deploy
   CustomObject + fields. Existing → delta only.
   See [sf-object-existence-precheck.mdc](../../rules/sf-object-existence-precheck.mdc).
5. **Delta + drift** — Tooling `CustomField` for names (case-insensitive);
   `attr_drift.py` for definitions **including standard Name**. Deploy new
   fields only. Surface drift; never silently redeploy a type change (may be
   delete+recreate). Blank-API rows: label-fallback first.
   **Labels are NOT covered by the name delta** — run both label syncs for
   fields already in the org: §3c English (`Field Label (EN)` → org
   translation) and §3d Japanese (`Field Label` → org `CustomField.label`,
   `label_sync.py`). `prep_deploy.py` does both automatically.
   See [sf-deploy-delta-and-blockers.mdc](../../rules/sf-deploy-delta-and-blockers.mdc).
   Lookup/MD `relationshipName`: existing field = org value; derive for new
   fields only.
   <!-- Updated by Divakar N — 2026-10-06. -->
6. **Generate / manifest** — `generate_xml.py` then
   `build_manifest.py --only <Obj__c,...>` for incremental.
7. **Check-only** then **real deploy** (`deploy.py --start` or
   `prep_deploy.py --phase deploy`). Lookup→MasterDetail is check-only-incompatible
   — isolate those members; confirm child record count == 0.
8. **Verify** — `python scripts/verify_deploy.py --target-org "<ORG>" --objects <Obj>__c`
   Use Tooling `CustomField`, **never** `sobject describe` / `FieldDefinition`
   (FLS-gated, false "missing").
9. **Report** — `build_object_report.py` (local xlsx; not a sheet write).
10. **Post-deploy gates** — Deployment Status (AI, incl. `Deleted`) → re-evaluate
    AH → auto-grant FLS on `SalesFrontAdmin` (re-check after requiredness flips)
    → FlexiPage only on first object create, when Tab/section is present
    → org-default `actionOverrides`. Later sheet deploys do not modify that page,
    except to unplace an IsDelete field before deleting it.
    See [sf-post-deploy-fls-and-flexipage.mdc](../../rules/sf-post-deploy-fls-and-flexipage.mdc).
11. **New object tab** — if object was newly created and sheet **H6** = TRUE,
    create CustomTab, then **auto-grant tab visibility** `DefaultOn` on
    `SalesFrontAdmin` ([sf-tab-visibility-mandatory.mdc](../../rules/sf-tab-visibility-mandatory.mdc)), then
    ASK about Lightning apps.
    See [sf-object-tab-and-app.mdc](../../rules/sf-object-tab-and-app.mdc).

Full pipeline notes: [sf-sheet-deployment.mdc](../../rules/sf-sheet-deployment.mdc).

## Sheet columns (header-driven)

Locate by **header name**, not index. WIP (`TRUE`/`X`) = skip entirely (wins over
IsDelete). IsDelete = destructive set, not create. Field list ends at first
`END[項目]` in Column A. Highlight **Column A only**.

Value cells: **H = `設定値`** (real formula/picklist/referenceTo); **L = `デフォルト`**;
**G = description — do not read as value.**
<!-- Updated by Divakar N -->
**F = `サイズ`** — text length, **or** Number/Currency/Percent as `"12, 3"`
(integer digits, scale → Salesforce precision 15 / scale 3). Client does **not**
fill T/U. See [sf-numeric-size-column-f.mdc](../../rules/sf-numeric-size-column-f.mdc).

AI writes: AH = `GDC AI Tool Comments`; AI = `Deployment Status`
(`Deployed` / `Not Deployed` / `Standard` / `Deleted`).

See [sf-sheet-columns.mdc](../../rules/sf-sheet-columns.mdc).

## Field naming

Glossary first (`name_fields.py --glossary .build/phrase_glossary.json`).
Never name standard fields, WIP rows, or IsDelete rows.
<!-- Updated by Divakar N — 2026-09-21 -->
**Blank col D is NOT a blocker** (do not ERROR / park / `flag_blocker`):
label-match the org first; if genuinely new, **generate**
`TI_Fnt_<Component>__c` immediately, write it back to col D (gated) + AH
`[FIX]`, then continue. `<= 40` chars, unique per object (case-insensitive).
Why: the client ships labels/types first; GDC owns the API name. Stopping at
"blocked: blank API" parked whole new tabs (`TradeTermsAppDetail` 2026-09-21).

See [sf-field-api-naming.mdc](../../rules/sf-field-api-naming.mdc) and
[sf-field-namespace-prefix.mdc](../../rules/sf-field-namespace-prefix.mdc).

## Attribute write-back (mandatory)

Any attribute you fill/fix to make a deploy succeed (formula, relationshipName,
API name, length, picklist, referenceTo, deleteConstraint) MUST be written to
the **sheet value cell** via `write_attr_fixes.py` **and** an AH `[FIX]` note.
A local-artifact-only or AH-only fix is a defect. Then re-evaluate AH.

## Destructive deletes

IsDelete rows → `build_destructive.py`, review with the user, then
`deploy.py --start --pre-destructive …`. Irreversible; do not batch with create.
After verify-absent: AI=`Deleted` + AH deletion note.

## On failure

Diagnose, fix the **producer** (`generate_xml.py` / `build_manifest.py`) **and**
add a **validator guard**, complete the DRAFT lesson, then
`python scripts/log_failure.py --check` must exit 0.

## Templates

- CustomTab XML — [custom-tab.xml](assets/custom-tab.xml)
- Org-default View overrides — [action-overrides.xml](assets/action-overrides.xml)
- Dummy formulas + column map — [dummy-formulas.md](assets/dummy-formulas.md),
  [column-map.md](assets/column-map.md)

## Rule index

| When | Read |
|------|------|
| No password / residual SHOOT | [deploy-no-password-policy.mdc](../../rules/deploy-no-password-policy.mdc) |
| Any sheet read | [read-sheet-live.mdc](../../rules/read-sheet-live.mdc) |
| Any sheet write | [sheet-write-confirmation.mdc](../../rules/sheet-write-confirmation.mdc) |
| Failure / before deploy | [sf-deploy-self-correction.mdc](../../rules/sf-deploy-self-correction.mdc), [deployment_knowledge.md](../../deployment_knowledge.md) |
| Blockers, delta, drift, deletes | [sf-deploy-delta-and-blockers.mdc](../../rules/sf-deploy-delta-and-blockers.mdc) |
| Object exists? | [sf-object-existence-precheck.mdc](../../rules/sf-object-existence-precheck.mdc) |
| After fields land | [sf-post-deploy-fls-and-flexipage.mdc](../../rules/sf-post-deploy-fls-and-flexipage.mdc) |
| New object tab + app | [sf-object-tab-and-app.mdc](../../rules/sf-object-tab-and-app.mdc) |
| Tab not visible | [sf-tab-visibility-mandatory.mdc](../../rules/sf-tab-visibility-mandatory.mdc) |
| Columns / END[項目] / AH / AI | [sf-sheet-columns.mdc](../../rules/sf-sheet-columns.mdc) |
| Numeric size in col F (`12, 3`) | [sf-numeric-size-column-f.mdc](../../rules/sf-numeric-size-column-f.mdc) <!-- Updated by Divakar N --> |
| Naming | [sf-field-api-naming.mdc](../../rules/sf-field-api-naming.mdc), [sf-field-namespace-prefix.mdc](../../rules/sf-field-namespace-prefix.mdc) |
| Blank API name | [sf-blank-api-label-fallback-match.mdc](../../rules/sf-blank-api-label-fallback-match.mdc) |
| Drift of standard Name | [sf-drift-includes-standard-fields.mdc](../../rules/sf-drift-includes-standard-fields.mdc) |
| IsDelete flag | [sf-isdelete-column-always.mdc](../../rules/sf-isdelete-column-always.mdc) |
| AI status after op | [sf-deploy-status-column-always.mdc](../../rules/sf-deploy-status-column-always.mdc) |
| Stale AH comments | [sf-ah-comment-reevaluate-on-change.mdc](../../rules/sf-ah-comment-reevaluate-on-change.mdc) |
| Prep playbook / lookups | [sf-sheet-deployment.mdc](../../rules/sf-sheet-deployment.mdc) |
