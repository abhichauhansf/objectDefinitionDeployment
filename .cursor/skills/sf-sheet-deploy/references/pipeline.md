# Salesforce Sheet → Org Deployment (local, Cursor-driven)

All deployment for this workspace happens from `sf-deploy/`. The user connects
their org here and deploys from here.

`SHOOT` password text below (if any leftover) is void —
see [deploy-authorization.md](deploy-authorization.md).

## Scope-selection gate (MANDATORY — before ANY task)

Before ANY operation on the sheet — validation, fetch, analysis, metadata
generation, packaging, or deployment — you MUST first confirm with the user the
exact object(s) / sheet tab(s) to work on. This is not deploy-only: it applies to
every task. Never assume "the whole sheet", and never reuse a previous task's
scope for a new task without re-confirming.

1. List the available object tabs (run `python scripts/fetch_sheet.py
   --list-tabs`, or read them from a prior fetch) and
   present them to the user (one option per tab, allow multiple).
   Spreadsheet ID defaults to `scripts/sheet_config.py`; pass `--spreadsheet-id`
   only to override.
2. Wait for the user's explicit selection. That selection is the scope for the
   whole task: pass it as `--tabs "<tab1,tab2>"` to fetch/validate and
   `--only "<Obj1__c,Obj2__c>"` to the manifest.
3. Only if the user EXPLICITLY chooses "all objects" do you run without `--tabs`.
   `scripts/run.py` refuses to run without `--tabs` unless `--all-tabs` is passed.
4. If a request is ambiguous about scope (e.g. "validate the sheet", "deploy the
   changes"), STOP and ask which tab/object first — do not guess.

## Pipeline (fixed order — never skip the gate)

Working directory is always `sf-deploy/`.

1. **Fetch** — `python scripts/fetch_sheet.py [--tabs "<tab1,tab2>"]`
   Produces `temp_updates.json`. Uses the user's Google ADC creds
   (`gcloud auth application-default login --scopes=...spreadsheets,drive`).
2. **Validate (HARD GATE)** — `python scripts/validate_sheet.py --in temp_updates.json --json .build/validation_report.json`
   - If the report has **any ERROR**, STOP. Do not generate XML, do not build a
     package, do not deploy. Show the user the failing rows and ask them to fix
     the sheet, then re-fetch.
3. **Generate** — `python scripts/generate_xml.py` → writes `force-app/main/default/objects/**`.
4. **Build manifest** — `python scripts/build_manifest.py --out manifest/package.xml`
   (add `--only Obj__c,Obj2__c` for an incremental / partial deploy).
5. **Deploy** — `scripts/deploy.py` (see gating below).
6. **Report** — after a successful real deploy, append/refresh the object's tab in
   `reports/Object_Deployment_Report.xlsx` via `scripts/build_object_report.py`
   (see "Post-deploy report" below).

Steps 1–4 can be run together with:
`python scripts/run.py [--tabs ...] [--only ...]`

Layer the blocker-scan → existence → delta flow from
[deploy-delta-and-blockers.md](deploy-delta-and-blockers.md) on top of this.

## Incremental deploys

To deploy only changed/new fields (not the whole object), pass `--tabs` to fetch
just the relevant object tab(s) and `--only <Object__c>` to `build_manifest.py`
so `package.xml` contains only those components. For deletions, hand-write a
`deletions.json` and run `build_manifest.py --destroy deletions.json` to emit
`destructiveChanges.xml`, then deploy with `deploy.py --pre-destructive ...`.

## Org-selection quality gate (MANDATORY before every deploy)

Before ANY deployment — check-only or real — you MUST present the list of
connected orgs and have the user pick the target. There is NO silent default.

1. Run `python scripts/deploy.py --list-orgs` to get the connected orgs.
2. Show that list to the user (use the ask_user / question UI with one option
   per org) and let them choose exactly one.
3. Pass the chosen org explicitly: `--target-org "<alias>"` (or the list index).
4. `deploy.py` refuses to run without an explicit `--target-org` and will print
   the org list + a BLOCK message instead of falling back to the sf default.

The org the user selects in this step is the deployment target — never assume
or reuse a previous selection for a new deploy.

## Deployment is a GATED action

`deploy.py` is the only script that touches a live org.
- Default (`deploy.py` with no `--start`) is **check-only validation**
  (`sf project deploy start --dry-run`) — safe, no org writes. You may run this
  after confirming the target org, but still surface the exact command first.
  (`deploy validate` is NOT used: it mandates a real Apex test level and rejects
  `NoTestRun`, whereas `--dry-run` validates fine with `NoTestRun` on sandboxes.)
- A **real deploy** is `deploy.py --start` (i.e. `sf project deploy start`).
  No password is required ([deploy-authorization.md](deploy-authorization.md)).
  Still: state the target org, manifest, and test level in plain English, and
  dry-run first.
- `deploy.py` also refuses to deploy unless `.build/validation_report.json` shows
  zero ERRORs. Never pass `--skip-validation-gate` to bypass this for a real org
  without explicit user instruction.

## Post-deploy report (MANDATORY after every real deploy)

After every SUCCESSFUL real deploy of an object, append/refresh that object's tab
in the shared workbook `sf-deploy/reports/Object_Deployment_Report.xlsx` — one tab
per object, so the workbook accumulates a tab for each deployed object:

```
python scripts/build_object_report.py \
  --sheet-tab "<JP:Object tab>" --object-api <Object__c> \
  --target-org <ORG> --deploy-id <ID from deploy> \
  --fields-dir force-app/main/default/objects/<Object__c>/fields \
  --sf-home "$PWD/.sfhome"     # only if the sf CLI needs the sandbox HOME shim
```

- The script reads the object tab LIVE from the sheet and queries the target org
  LIVE (Tooling API `FieldDefinition`) — no cached data.
- It writes a single self-contained tab named after the object (short name),
  replacing that tab if it already exists (safe to re-run). Other objects' tabs
  are left untouched.
- Keep the MAIN process on the real `HOME` (Google ADC lives there); `--sf-home`
  scopes the shim to the `sf` subprocess only. Remove the shim afterwards (it
  holds copied auth tokens).
- Writing this LOCAL workbook is NOT a Google-Sheet write, so the sheet-write
  confirmation gate does not apply.

## Org connection

The user connects orgs themselves:
`sf org login web --alias "<ORG>"` then `sf config set target-org "<ORG>"`.
Pass `--target-org "<ORG>"` explicitly to `deploy.py`. Never hardcode credentials.

## On failure

If any pipeline or deploy command fails, follow
[deploy-self-correction.md](deploy-self-correction.md): diagnose the root cause,
fix the generator/validation, and append a lesson to
[deployment-knowledge.md](references/deployment-knowledge.md). Before deploying,
consult that knowledge base to apply past lessons.

## Lookup delete behavior (`deleteConstraint` column — optional)

Object tabs may include an optional `deleteConstraint` API-header column (mapped
to the `Delete Constraint` key) that controls a Lookup's delete behavior:
`SetNull` | `Restrict` | `Cascade`. It applies to **Lookup** only (MasterDetail
manages its own delete behavior).

- **Blank (default)** → the generator picks a safe value: a *required* custom-object
  Lookup gets `Restrict`; a required Lookup to `User`/`Group`/`RecordType`/`Profile`
  is degraded to an optional `SetNull` (those objects reject Restrict/Cascade); an
  optional Lookup omits the element (SF default `SetNull`).
- **Explicit value** → honored as-is, except `Restrict`/`Cascade` on a Lookup to
  `User`/`Group`/`RecordType`/`Profile`, which is degraded to `SetNull` (and the
  validator flags it as an ERROR so it's corrected at the source).
- A `SetNull` lookup **cannot be `required`**; the generator drops `required` and
  the validator emits an INFO. Enforce requiredness via page layout / validation rule.

## Raw / unprepped object tab — standard prep playbook (DEFAULT)

When a selected object tab fails validation because it is "raw" (blank field API
names, empty formula bodies, placeholder `referenceTo`, object API missing `__c`),
apply the SAME cycle we used for `Sales_Deal` — automatically, without re-asking
which strategy to use. This is the standing default (decided 2026-08-10):

1. **Normalize object API** — ensure `Object API Name` ends with `__c`
   (e.g. `Sales_DealDetail` → `Sales_DealDetail__c`). Blank/invalid = source fix.
2. **Name blank custom fields (NOT a blocker)** — <!-- Updated by Divakar N -->
   a blank Field API Name (col D) is **not** a validation/deploy blocker. First
   read IsDelete ([isdelete-column-always.md](isdelete-column-always.md)); skip
   WIP. For blank API (col D), run label-fallback
   ([blank-api-label-fallback.md](blank-api-label-fallback.md)) before treating
   as new. Then generate from the JP→EN glossary, apply `TI_Fnt_` + `__c`,
   enforce ≤40 chars and per-object uniqueness
   (see [field-api-naming.md](field-api-naming.md) §0b,
   [field-namespace-prefix.md](field-namespace-prefix.md)). Write back — GATED
   ([sheet-write-confirmation.md](sheet-write-confirmation.md)) plus an AH
   `[FIX]` note. Never touch standard fields (OwnerId, CreatedDate, …) or
   WIP/IsDelete rows. Do not stop at "blocked: blank API".
3. **Fill empty formula bodies** — for Formula fields with an empty
   `Type Specific Value` (**col H / `設定値`**, never col G), insert a generalized
   placeholder formula that matches the return type (see
   [dummy-formulas.md](../assets/dummy-formulas.md)) to unblock now; flag for
   later refinement. Highlight updated formula rows green (Column A only).
4. **Wire relationships** — for Lookup/MasterDetail, col H (`設定値`) is the
   lookup object (`referenceTo`). If col X (`relationshipName`) is blank, check
   the org first: an existing field keeps the org's relationshipName (write it
   back to col X, gated). Only for a NEW field, a populated lookup object means
   auto-fill X from the field API minus `__c` (object-scoped for shared parents
   User/Account/…). Only blank X **and** blank H is a blocker. Write the name
   back with `fill_relationship_name.py --target-org <ORG>` (gated).
   <!-- Updated by Divakar N — 2026-10-06. -->
5. **Resolve or park cross-object lookups** — see next section.
6. **Re-validate to 0 ERRORs → generate → build manifest → check-only dry-run →
   real deploy → append the post-deploy report tab.** If the object does
   not yet exist in the target org, deploy the FULL `package.xml` (object + fields),
   not a fields-only manifest.

All sheet writes in this playbook are individually gated; highlight Column A only.

## Cross-object lookup resolution (DEFAULT)

For any Lookup/MasterDetail whose `referenceTo` is another CUSTOM object, decide
per-field automatically against the chosen deploy org (decided 2026-08-10):

1. Resolve the `referenceTo` placeholder (`<API名未定(GDC付与)>：…`, `要確認`) to the
   real target object API name (via glossary / the object-index tab).
2. **Check the target org** (describe / Tooling `EntityDefinition`) for that object:
   - **Exists in org** → keep the lookup in this deploy.
   - **Missing, or still a placeholder** → mark that row **WIP** (col AB = `X`),
     exclude it from this round, and note it so it can be deployed later once the
     master object exists. Highlight parked rows lime-yellow (Column A only).
3. Never deploy a lookup whose `referenceTo` object is absent from the org — it
   fails with `Entity '<X>' not found`. Parking as WIP is the safe default.

## Guardrails

- Keep all deployment scripts and improvements inside `sf-deploy/scripts/`.
- Keep validation rules in `scripts/validate_sheet.py` aligned with
  `../sheet-editor/context/FIELD_TYPE_VALIDATION_RULES.md` and related specs.
- `temp_updates.json`, `force-app/**` generated output, and `.build/**` are
  disposable build artifacts — safe to regenerate, not sources of truth.
