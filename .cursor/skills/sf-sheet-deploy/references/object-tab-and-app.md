# CustomTab + Lightning-App Prompt After Object Creation (MANDATORY)

Whenever a NEW custom object is created (i.e. the object itself was deployed,
per [object-existence.md](object-existence.md)), the follow-up steps below are
REQUIRED for that object. This applies to a single object AND to any deploy that
creates multiple objects — do it PER newly-created object, every time.

`SHOOT` password text below (if any leftover) is void —
see [deploy-authorization.md](deploy-authorization.md).

> Scope: this triggers only when the OBJECT is newly created. Field-only deploys
> to an already-existing object do not require a new tab, but if that object has
> no CustomTab yet AND its H6 flag is TRUE, still offer to create one.

## The "Create Tab" flag (object sheet cells H5 / H6) — READ FIRST

Each object-definition tab in the Google Sheet carries a per-object tab-creation
control in its object-meta header block:

- **`H5`** = label `タブ作成 (Create Tab)`.
- **`H6`** = the value: `TRUE` → create the CustomTab; `FALSE` or blank → SKIP tab
  creation for that object (deploy the object/fields only, no tab).

Read `H6` **live** from the object's sheet tab (per [read-sheet-live.md](read-sheet-live.md))
before deciding. `fetch_sheet.py` / `prep_deploy.py` surface this value; if it is
missing, treat blank as `FALSE` (do not create a tab) and surface that to the user.

## Step 1 — Create a CustomTab ONLY when H6 = TRUE

For every newly-created object `<Obj>__c` whose object-sheet `H6` = `TRUE`,
generate and deploy a CustomTab. If `H6` is `FALSE`/blank, DO NOT create a tab —
skip Step 1 (and Step 2) for that object. Never create a tab for an object whose
flag is not `TRUE`.

- Source file: `force-app/main/default/tabs/<Obj>__c.tab-meta.xml`
- Template: [custom-tab.xml](../assets/custom-tab.xml)
- The tab `fullName` = the object API name (the file name). Include
  `CustomTab` members in the deploy `package.xml`.
- Verify live afterwards: `sf org list metadata --metadata-type CustomTab`
  must list every new tab.

## Step 1b — GRANT TAB VISIBILITY (MANDATORY, automatic)

A deployed `CustomTab` is INVISIBLE until visibility is granted. For every tab
created in Step 1, immediately grant `DefaultOn` on `SalesFrontAdmin` (or a
user-named alternative) WITHOUT asking. See [tab-visibility.md](tab-visibility.md).

```
python scripts/grant_tab_visibility.py --tabs <Obj>__c[,<Obj2>__c,...]
```

Use `Visibility='DefaultOn'` (Tooling `PermissionSetTabSetting`; the words
`Visible`/`Available` are rejected). Verify live before moving on.

## Step 2 — ASK about Lightning apps (never assume)

Only for objects that actually got a CustomTab in Step 1 (i.e. `H6` = `TRUE`):
after the tab exists AND visibility is granted, you MUST ask the user whether
they want the object(s) added to any Lightning app, and if so WHICH app(s). Do
not add to any app without an explicit answer. Objects whose `H6` was
`FALSE`/blank have no tab, so they are not eligible for app navigation — skip
them here.

- Ask plainly (use the question tool): "Add these new object tab(s) to a
  Lightning app? If yes, which app (e.g. `SalesFrontProto`)?"
- If the user says NO / skip → stop; tab already exists, nothing more to do.
- If the user names an app → retrieve that app live
  (`sf project retrieve start --metadata CustomApplication:<App>`), inject
  `<tabs><Obj>__c</tabs>` for each object (after `<navType>`, before
  `<uiType>`) while PRESERVING all existing app settings (brand, label,
  navType, utilityBar, etc.), then deploy `CustomApplication:<App>` and verify
  the app's `<tabs>` live.

## Guardrails

- The `H6` flag is the single source of truth for whether a tab is created. Never
  create a tab for an object whose `H6` is not `TRUE`, and never silently create
  one when the flag is missing — treat blank as `FALSE`.
- Preserve existing app metadata; only add `<tabs>` — never drop brand/label/
  utilityBar or reorder unrelated elements.
- Tab visibility on `SalesFrontAdmin` is MANDATORY and automatic after Step 1
  ([tab-visibility.md](tab-visibility.md)) — never leave it as a "want me to?"
  question. `DefaultOn` only; never `Visible`/`Available`.
