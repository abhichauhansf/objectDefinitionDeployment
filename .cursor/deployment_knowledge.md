# Deployment Knowledge Base (Self-Learning Memory)

Append-only log of deployment/validation failures, their root causes, the fixes
applied, and the prevention rule added so the same failure never recurs. Newest
entries on top. Written by the self-correction loop (see
`.cursor/rules/sf-deploy-self-correction.mdc`).

## Entry format (copy for each new lesson)

```
### [YYYY-MM-DD] <short title>
- **Error signature:** <error code / message / component failure text>
- **Command:** <the command that failed>
- **Component:** <metadata type> <API name>  (<file>:<line> if known)
- **Category:** Schema | Dependency | Order-of-Execution | Environment/Org | Tooling
- **Root cause:** <one-paragraph diagnosis>
- **Fix applied:** <script/file changed + what changed>
- **Prevention added:** <validate_sheet.py check / generator guard / manifest ordering>
- **Status:** Resolved | Monitoring
```

---

## Lessons

### [2026-10-05] ERPPJ-1032 activation: false source-tracking conflict on a settings-only CustomObject deploy
- **Error signature:** `Conflict │ TI_Fnt_Deal__c │ CustomObject` / `There are changes in the org that conflict with the local changes you're trying to deploy.`
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60` (from `sf-deploy/work/erppj1032_activate/`)
- **Component:** CustomObject `TI_Fnt_Deal__c` (object-meta only: View `actionOverrides` + `compactLayoutAssignment`)
- **Category:** Tooling
- **Root cause:** To activate the v2 record page and compact layout without redeploying the object's 227 local field files, the object was retrieved with `--target-metadata-dir` (mdapi), stripped to object-level settings and converted into a fresh, isolated sfdx project. That project has no source-tracking baseline, so every org-side change to the object counts as a "remote change" and the CLI flags a conflict. Nothing had actually changed: a fresh re-retrieve showed the object settings identical to the copy that was edited. The check-only run passed because `--dry-run` skips the conflict check. `deploy.py`'s auto-draft did not fire for this failure (no lesson was appended), so this entry was added by hand.
- **Fix applied:** Re-retrieved the object and compared all object-level settings (children excluded) with the edited source → identical; then redeployed through `deploy.py --start --ignore-conflicts` (0AfBK00000CjyM50AJ, Succeeded). Verified live: View Large/Small → `TI_FnT_Deal_Lightning_Page_v2`, `compactLayoutAssignment` = `Deal_CompactLayout_v2`, field count unchanged by the deploy.
- **Prevention added:** Procedure for settings-only object deploys from an isolated project: (1) retrieve mdapi, strip child arrays (`fields`, `listViews`, `compactLayouts`, `validationRules`, `webLinks`, `fieldSets`, `recordTypes`, `businessProcesses`, `sharingReasons`, `indexes`) so no stale field file is redeployed; (2) keep the stripped "before" copy as the rollback; (3) immediately before the real deploy, re-retrieve and confirm the object-level settings still equal the edited copy, and only then pass `--ignore-conflicts`. Never use `--ignore-conflicts` without that comparison. Project lives under `sf-deploy/work/` (not under a dot-folder: the CLI ignores hidden directories, so convert/retrieve there yields nothing).
- **Status:** Resolved

### [2026-10-05] Picklist with duplicate value LABELS (unique ApiNames)  «sig:9b521ce3b1»
- **Error signature:** `Duplicate label: 有償（直送） (217:24)`
- **Command:** `sf project deploy start --manifest manifest/dealdetail_update_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run --ignore-conflicts`
- **Component:** CustomField `TI_Fnt_DealDetail__c.TI_Fnt_LineItemCategory__c`
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [1]
    │ CustomField │ TI_Fnt_DealDetail__c.TI_Fnt_LineItemCategory__c │ Duplicate label: 有償（直送） (217:24) │ 217:24      │
- **Root cause:** Salesforce requires picklist value LABELS to be unique, not just ApiNames. The sheet's col H for 明細カテゴリ lists 44 `Label:Code` pairs with unique SAP codes (YB11…YS28) but only 26 distinct labels (e.g. `有償（直送）` maps to both YB11 and YB21). `validate_sheet.py` only de-duplicated ApiNames, so it passed.
- **Fix applied:** No generator change (labels come verbatim from the sheet; inventing distinct labels is a client/business decision). The field was held back from deploy pending a sheet fix.
- **Prevention added:** `validate_sheet.py` picklist block: new ERROR `picklist.dup_label` (case-insensitive) alongside `picklist.dup`. Re-run on DealDetail now reports 18 duplicate labels before deploy.
- **Status:** Monitoring (guard live; field still awaiting unique labels on the sheet)

### [2026-10-05] IsDelete fields still placed on a FlexiPage; page-reference scan silently passed  «sig:d109451e13»
- **Error signature:** `The <label> custom field is used in a component on the 成約明細 レコードページ Lightning page. : Lightning Page.` (9 CustomField failures)
- **Command:** `sf project deploy start --manifest manifest/destructive_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run --pre-destructive-changes manifest/destructiveChanges.xml`
- **Component:** CustomField `TI_Fnt_DealDetail__c.{ApplicableCategory, AppendixCargoNo, MinisterialOrdinanceNumber, PermitCategory, ReportFormType, PermitNumber, ApplicationDate, PermitDate, ExportPermitExpiry}__c`; FlexiPage `TI_Fnt_DealDetail_Record_Page`
- **Category:** Dependency (+ Tooling bug)
- **Extracted failure lines:**
    Status: Failed
    Component Failures [9]
- **Root cause:** Salesforce refuses to delete a field that a Lightning record page still places. `build_destructive.flexipage_refs()` is meant to catch this, but it retrieved with `--target-metadata-dir` WITHOUT `--unzip`, so the output was a single `unpackaged.zip`; `rglob("*.flexipage*")` matched nothing and the scan returned "no references" — a false all-clear. A second real-deploy attempt then hit a source-tracking `Conflict` on the hand-edited page (org copy was unchanged since the base retrieve — verified by diff — so `--ignore-conflicts` was safe).
- **Fix applied:** `scripts/build_destructive.py`: FlexiPage retrieve now passes `--unzip`; if the retrieve yields zero page files the scan fails CLOSED (every delete reported BLOCKED as unverified) instead of passing. Deploy path: removed the 9 `fieldInstance` items from the live page copy and deployed `FlexiPage` + `--post-destructive-changes` in one transaction (page first, then delete). Deploy `0AfBK00000CjmCt0AJ` Succeeded; Tooling `CustomField` confirms all 9 absent.
- **Prevention added:** Fail-closed guard in `flexipage_refs()` (empty retrieve = BLOCKED). Delete sets that touch placed fields must ship the edited page with `--post-destructive`, never `--pre-destructive`. For a hand-edited page derived from a fresh org retrieve, diff the org copy before using `--ignore-conflicts`.
- **Status:** Resolved

### [2026-10-01] ERPPJ-1032 Deal page copy: CompactLayout missing fullName + CreatedDate not placeable  «sig:33458dec18»
- **Error signature:** `element fullName missing for a child of type CompactLayout`; `FlexiPage TI_FnT_Deal_Lightning_Page_v2: Something went wrong. We couldn't retrieve or load the information on the field: Record.CreatedDate. (5044:28)`
- **Command:** `sf project deploy start --manifest manifest/erppj1032_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** CompactLayout `TI_Fnt_Deal__c.Deal_CompactLayout_v2`; FlexiPage `TI_FnT_Deal_Lightning_Page_v2`
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [2]
    │ CompactLayout │                               │ element fullName missing for a child of type CompactLayout                                                     │             │
    Warning: CompactLayout, , returned from org, but not found in the local project
- **Root cause:** (1) A source-format `*.compactLayout-meta.xml` is a decomposed child of the CustomObject and must carry its own `<fullName>`; the new generator emitted only `<fields>`/`<label>`. (2) The review sheet places `CreatedDate`/`LastModifiedDate` in 「システム管理」. Dynamic Forms rejects those as `fieldInstance` (the live page proves `OwnerId`, `CreatedById`, `LastModifiedById`, `Name` ARE placeable — so the older `gen_tabbed_page.py` deny-list is stricter than needed).
- **Fix applied:** `scripts/gen_deal_review_page.py`: `compact_layout_xml()` now writes `<fullName>`; field placement only accepts standard fields in `STANDARD_PLACEABLE` (`Name`, `OwnerId`, `CreatedById`, `LastModifiedById`, `RecordTypeId`) and reports the rest as INFO `field.standard`. Also auto-repairs sheet API typos (missing `__c`, copy-pasted `No1BranchNo` on rows 147-150) with WARN `sheet.api`. Re-run dry-run 0AfBK00000Ch2J00AJ: Succeeded.
- **Prevention added:** Generator guards above (this is a page-only package; `validate_sheet.py` does not see it). The generator writes its own gate report `.build/erppj1032/validation_report.json` (ERROR on compact-layout fields missing from the org) that `deploy.py --validation-report` consumes.
- **Status:** Resolved

### [2026-10-01] Auto-derived relationshipName collided on a shared CUSTOM master parent  «sig:f131840617»
- **Error signature:** `There is already a Child Relationship named TI_Fnt_Incoterms on インコタームズマスタ` / `… named TI_Fnt_Payer on BPマスタ`; knock-on `CustomObjectTranslation … no CustomField named TI_Fnt_DateOfArrival__c.TI_Fnt_Incoterms__c found`
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** CustomField `TI_Fnt_DateOfArrival__c.TI_Fnt_Incoterms__c` (→ `TI_Logi_IncotermsMaster__c`), `TI_Fnt_DateOfArrival__c.TI_Fnt_Payer__c` (→ `TI_Logi_TINETAccount__c`)
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [3]
- **Root cause:** Col X was blank, so `relname.derive_relationship_name` used field-API-minus-`__c`. Object-scoping applied only to standard shared parents (User/Account/…), but widely referenced custom masters have the same problem: `TI_Fnt_Deal__c` already owns `TI_Fnt_Incoterms` / `TI_Fnt_Payer` on those parents. The existing `relationshipname.collision` guard never fired because describe reports child names as `Foo__r` and the guard compared them to bare `Foo` (case-sensitive), so it could never match.
- **Fix applied:** `relname.py`: `derive_relationship_name` now takes the parent's live child-relationship names (`taken`; default = `.build/relname_taken.json` written by the validator, excluding the field's own relationship) and object-scopes on collision (`DOA_Incoterms`, numeric suffix if still taken). `generate_xml.py` picks this up via the same cache, so the generator and validator derive the same name. `TI_Fnt_DateOfArrival__c` col X written back as `DOA_Incoterms` / `DOA_Payer` / `TI_Fnt_DepartmentCode`.
- **Prevention added:** `validate_sheet.py` strips `__r` from described child relationships and compares case-insensitively, so an explicit col X that collides is now an ERROR (`relationshipname.collision`). `--target-org` runs persist the cache, and offline runs delete it so it can't go stale. Verified offline (explicit `TI_Fnt_Incoterms` → ERROR; blank → `DOA_Incoterms`) and live against TI_ERPDEV01.
- **Status:** Resolved

### [2026-09-30] PermissionSet metadata deploy is a FULL REPLACE — partial file wiped SalesFrontAdmin
- **Error signature:** none — deploy reported `Succeeded`. Afterwards the org's `SalesFrontAdmin` had FieldPermissions 0 (was 2591), ObjectPermissions 0 (was 50), PermissionSetTabSetting 0 (was 26), and only 2 recordTypeVisibilities. 60 users assigned.
- **Command:** `deploy.py --start --package manifest/salesfrontadmin_rt_visibility.xml` with a local `SalesFrontAdmin.permissionset-meta.xml` holding ONLY the 2 new `recordTypeVisibilities`.
- **Component:** PermissionSet `SalesFrontAdmin`
- **Category:** Tooling
- **Root cause:** A PermissionSet deploy through the Metadata API replaces the whole permission set with the file's contents. Any field, object or tab permission missing from the file is removed. The local file was also stale (Sep 24: 2210/45/18). Even a full-file deploy would have dropped every grant made since then through the data API (`grant_fls.py`, `grant_object_perms.py`, `grant_tab_visibility.py`).
- **Fix applied:** Restored from the pre-deploy live snapshot (`.build/permset_before.json`: field Read/Edit, object RCED, tab visibility), plus the Sep 24 file for the older RT visibilities and viewAll/modifyAll. Deployed as one complete file. A live re-snapshot then diffed 0 missing / 0 extra on fields, objects and tabs, and all 7 RT visibilities are present. The local permset file was then replaced with a fresh retrieve from the org.
- **Prevention added:** `deploy.py` now has a PermissionSet shrink gate (`_permset_shrink_check`). For every PermissionSet in the package it compares the local counts of fieldPermissions, objectPermissions and tabSettings with the org's live counts. If any local count is lower, or an org count can't be read, the deploy is blocked (exit 2). The only override is `--allow-permset-shrink`. Tested: the stale Sep 24 file is now blocked. Rule of thumb: grant additively through the data API. For record-type visibility, always retrieve the permission set from the org first and edit that copy.
- **Status:** Resolved

### [2026-09-30] AUTO-DRAFT: deploy validate (dry-run) failed (exit 1)  «sig:62b75f81da»
- **Error signature:** (none extracted)
- **Command:** `sf project deploy start --manifest manifest/salesfrontadmin_rt_visibility.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** PermissionSet `SalesFrontAdmin` (restore file, tabSettings)
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Status: Failed
    Component Failures [1]
    │ PermissionSet │ SalesFrontAdmin │ Error parsing file: 'DefaultOn' is not a valid value for the enum 'PermissionSetTabVisibility' (13440:43) │ 13440:43    │
- **Root cause:** The tab-visibility values were copied straight from the Tooling `PermissionSetTabSetting.Visibility` values (`DefaultOn`/`DefaultOff`/`Hidden`). The Metadata API `PermissionSetTabVisibility` enum uses different words: `Visible`/`Available`/`None`. The two APIs are not interchangeable.
- **Fix applied:** Mapped DefaultOn→Visible, DefaultOff→Available, Hidden→None in the restore file. The dry run then passed, and so did the real deploy.
- **Prevention added:** Knowledge-base note only. No generator writes permset tabSettings XML; tab visibility goes through `grant_tab_visibility.py` (Tooling, `DefaultOn`). Any hand-built PermissionSet XML must use `Visible`/`Available`/`None`.
- **Status:** Resolved

### [2026-09-30] AUTO-DRAFT: deploy start failed (exit 10)  «sig:9323be3033»
- **Error signature:** (none extracted)
- **Command:** `sf project deploy start --manifest manifest/flexipage_salescontractapplication.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60`
- **Component:** FlexiPage `TI_Fnt_SalesContractApplication_Record_Page`
- **Category:** Environment/Org (Tooling side-effect)
- **Extracted failure lines:**
    Error (MetadataTransferError): Metadata API request failed: fetch failed
- **Root cause:** Not a metadata error. The sf CLI lost its HTTP connection while polling the deploy job and exited 10. The job kept running server-side and finished `Succeeded` (job `0AfBK00000CgC5t0AF`, 1 component, 0 errors; page confirmed live via `listMetadata FlexiPage`). `deploy.py` treated any non-zero sf exit as a failed deploy, so a transport drop produced a false failure.
- **Fix applied:** `scripts/deploy.py` — on a non-zero exit whose log shows `MetadataTransferError … fetch failed`, it now polls `sf project deploy report --use-most-recent` until the job is terminal, appends the server-side status to `.build/last_deploy.log`, and returns 0 only when that status is `Succeeded`. Any other status still takes the failure path (context + DRAFT lesson).
- **Prevention added:** Not sheet-related, so no `validate_sheet.py` guard applies; the guard is the server-side status check in `deploy.py` above. Live post-deploy verification (listMetadata / `verify_deploy.py`) remains mandatory regardless of the reconciled status.
- **Status:** Monitoring (org=TI_ERPDEV01; flip to Resolved after the next transport drop is reconciled automatically)

### [2026-09-25] Object English ending in a plural word → "plural identical to singular" abort
- **Error signature:** `⛔ object plural English is identical to singular ('Txn Approval Request Performance Trends')`
- **Command:** `python scripts/prep_deploy.py --org TI_ERPDEV01 --tabs TradeTermsAppPerfHistory` (step 6/8, `generate_object_translation.py`)
- **Component:** CustomObjectTranslation `TI_Fnt_TradeTermsAppPerfHistory__c-en_US` / top-level `caseValues`
- **Category:** Tooling
- **Root cause:** `translate_enrich.english_plural_label` intentionally leaves an already-plural last word unchanged (`Trends`, `Details`), but `generate_object_translation.py` rejected ANY plural equal to the singular. The two halves of the translation tooling contradicted each other, so every object whose English ends in a plural word failed before packaging. Salesforce accepts identical singular/plural caseValues.
- **Fix applied:** added `translate_enrich.ends_in_plural_word()`; the generator now rejects an identical plural only when the last word is NOT already plural. Redeployed; live verify confirmed object label + plural + 10 field/Name labels.
- **Prevention added:** the generator guard still fires for a genuine pluralization failure (e.g. plural dropped because it would exceed 40 chars), so a silent singular-as-plural cannot slip through; already-plural labels are no longer false positives.
- **Status:** Resolved

### [2026-09-24] RecordType developer names: hyphen is illegal; Metadata API cannot delete RecordTypes
- **Error signature:** `The Record Type Name field can only contain underscores and alphanumeric characters` AND `Cannot delete record type through API` AND `The label:… is not unique`
- **Command:** destructive delete of `TI_Logi_Customer_RecType` / `TI_Logi_ShipToParty_RecType`; create of `TI_Fnt_Ship-toParty_RecType`
- **Component:** RecordType on `TI_Logi_TINETAccount__c`
- **Category:** Schema
- **Root cause:** (1) DeveloperName allows only `[A-Za-z][A-Za-z0-9_]*` — a hyphen (`Ship-toParty`) is rejected. (2) Metadata API cannot delete RecordTypes; REST delete returned `INSUFFICIENT_ACCESS_OR_READONLY`. Deactivate (`IsActive=false`) works. (3) Creating a replacement RT with the same label as an inactive RT fails (`label is not unique`) until the retired RT's Name is changed.
- **Fix applied:** Reverted sheet names (sheet is source of truth). Created `TI_Fnt_Customer_RecType` after renaming the retired Logi Customer label. Did not invent a hyphen-free ship-to API name. Retired Logi RTs left inactive for Setup UI delete.
- **Prevention added:** Before deploying a RecordType, validate DeveloperName against `^[A-Za-z][A-Za-z0-9_]*$`. Never rewrite the sheet to make a name deployable unless the user asks. Never assume Metadata API can delete RecordTypes — deactivate, then ask the user to delete in Setup.
- **Status:** Resolved

### [2026-09-24] Cannot convert Text → Formula in place — delete+recreate, and strip Flow/Apex refs first
- **Error signature:** `Cannot update a field to a Formula from something else` AND `This custom field is referenced elsewhere … Flow Version / Apex Class`
- **Command:** `deploy.py` in-place update of `TI_Logi_TINETAccount__c.LegalEntityCD__c`; earlier pre-destructive delete of the same field
- **Component:** CustomField `TI_Logi_TINETAccount__c.LegalEntityCD__c`
- **Category:** Schema | Dependency
- **Root cause:** Salesforce rejects changing an existing Text field into a Formula. Delete+recreate is required, but delete is blocked while ANY Flow version (including Obsolete) or Apex references the field. Obsolete versions and paused Flow Interviews also count. A formula field is not insertable, so Apex test factories that assign the field on insert must drop that assignment permanently.
- **Fix applied:** (1) Removed `LegalEntityCD__c` assignments from `TI_Logi_TINETAccount__c` inserts in `TI_Logi_TestRestDataFactory`. (2) Stubbed reads in `DemoFlow_DealTrigger_UpdateSoldToPartyCode` and `TI_Logi_TINETAccountAccountForSectionsSet`, deployed, deleted Obsolete Flow versions (and one paused Flow Interview), destructively deleted the field, recreated as Formula Text `AccountForSections__r.ViewLegalEntityCD__c`, restored the Flow reads. (3) FlexiPage fieldInstances were removed before the delete and regenerated after.
- **Prevention added:** `generate_xml.py` now treats a known GlobalValueSet name in col H (`Prefectures`, `ActiveStatus`, …) as `valueSetName`, not a 1-value inline list. For type drift Text→Formula / Text→Picklist: never attempt an in-place field update; always (a) list Tooling/Metadata dependencies (Flow including Obsolete, Apex, FlexiPage), (b) strip page + stub refs, (c) delete, (d) recreate. `attr_drift.py` already surfaces these as type drift — do not skip that report.
- **Status:** Resolved

### [2026-09-24] Field delete blocked by Flow versions (active, obsolete, AND paused interviews)
- **Error signature:** `This custom field is referenced elsewhere in salesforce.com. : Flow Version - 301… / Apex Class - TI_Logi_TestRestDataFactory`
- **Command:** `deploy.py --pre-destructive manifest/tinet_typefix_destructive.xml` (LegalEntityCD__c + Prefecture__c)
- **Component:** CustomField `TI_Logi_TINETAccount__c.LegalEntityCD__c`
- **Category:** Dependency
- **Extracted failure lines:** Component Failures [2] on LegalEntityCD__c (Prefecture deleted cleanly once isolated)
- **Root cause:** Same as the Text→Formula lesson: delete is FLS-independent metadata-dependency gated. Prefecture had no Flow/Apex refs so it deleted after the FlexiPage strip. LegalEntityCD did not. Deleting an Obsolete Flow version can itself fail with `DEPENDENCY_EXISTS` if a Flow Interview still points at it.
- **Fix applied:** Isolated Prefecture into its own destructive deploy. For LegalEntityCD, stubbed deps, deleted Flow Interviews then Obsolete versions, then the field.
- **Prevention added:** Before a type-change delete, query `Flow` (all statuses) and `FlowInterview`, plus Apex `SymbolTable`/retrieve. Strip FlexiPage fieldItems in an earlier deploy (same lesson as deleting a CustomObject).
- **Status:** Resolved

### [2026-09-24] `attr_drift.py` did not read `○` as TRUE — false drift on Required/Unique/ExternalID
- **Error signature:** drift reported `required: sheet=False org=True` (`ActiveStatus__c`) and `externalId: sheet=False org=True` (`SupplierCode__c`) while the sheet cells plainly read `○`
- **Command:** `python scripts/attr_drift.py --object TI_Logi_TINETAccount__c --rows temp_updates.json --org TI_ERPDEV01`
- **Component:** `scripts/attr_drift.py::_sbool` (line ~202)
- **Category:** Tooling
- **Root cause:** The client marks boolean columns (`Required`, `Unique`, `External ID`, `Track History`) with the Japanese circle `○`/`〇`, never `TRUE`. `generate_xml.is_truthy` accepts `○`/`〇`, but `attr_drift._sbool` only accepted `true/x/yes/1`, so the two readers disagreed. Every `○`-marked row was compared as sheet=False. That produces false-positive drift when the org is true (seen here), and — far worse — SILENTLY HIDES real drift in the opposite direction: a field the sheet marks `○` Required but that is optional in the org compares False-vs-False and reports in-sync.
- **Fix applied:** `_sbool` now accepts `("true","x","yes","1","○","〇")`, matching `generate_xml.is_truthy`. Re-run on `TI_Logi_TINETAccount__c` dropped drift from 6 to 4 with no new entries, confirming both were false positives.
- **Prevention added:** Sheet truthiness must be parsed in ONE place. Any new reader of a boolean sheet column must reuse `generate_xml.is_truthy` (or replicate its full set incl. `○`/`〇`) — never hand-roll a `in ("true","x","yes","1")` test. When a drift row's reason is a bare boolean flip, re-read the raw sheet cell before believing it.
- **Status:** Resolved

### [2026-09-24] Deleting a FlexiPage: omitting `actionOverrides` does NOT deactivate it
- **Error signature:** `You can't delete an active Lightning page. Open the page in Lightning App Builder and click Activation to deactivate it.`
- **Command:** `sf project deploy start --manifest manifest/tinetaccount_fnt_destructive_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 30 --pre-destructive-changes manifest/tinetaccount_fnt_destroy_page.xml`
- **Component:** FlexiPage `TI_Fnt_TINETAccount_Record_Page` (org default on `TI_Fnt_TINETAccount__c`)
- **Category:** Dependency | Order-of-Execution
- **Extracted failure lines:**
    Component Failures [2]
    │ FlexiPage │ TI_Fnt_TINETAccount_Record_Page │ You can't delete an active Lightning page …
- **Root cause:** A FlexiPage assigned as an object's org default (GATE 2b `actionOverrides`) cannot be deleted until it is deactivated. The first deactivation attempt DELETED the two `View`/`Flexipage` `actionOverrides` blocks from the CustomObject and redeployed — but the Metadata API treats `actionOverrides` additively: an override that is simply ABSENT from the payload is left untouched in the org, so the page stayed active and the delete failed again with the identical error.
- **Fix applied:** Deactivated explicitly by REPLACING each block with `<actionName>View</actionName><formFactor>Large|Small</formFactor><type>Default</type>` and deploying the CustomObject; the FlexiPage delete then succeeded, followed by the CustomTab + CustomObject delete.
- **Prevention added:** To retire a record page, never just remove its override — always write an explicit `<type>Default</type>` View override for BOTH form factors, deploy the CustomObject, and only then run the destructive FlexiPage delete. Same rule applies when repointing a default (GATE 2b), where overwriting `<content>` is sufficient because the block is still present.
- **Status:** Resolved

### [2026-09-24] Deleting a whole CustomObject: its FlexiPage must go in an EARLIER deploy
- **Error signature:** `The STC推進室コメント custom field is used in a component on the BPマスタ レコードページ Lightning page. : Lightning Page.` (× 217 components)
- **Command:** `sf project deploy start --manifest manifest/tinetaccount_fnt_destructive_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 30 --dry-run --pre-destructive-changes manifest/tinetaccount_fnt_destructive.xml`
- **Component:** CustomObject `TI_Fnt_TINETAccount__c` + its 216 CustomFields, FlexiPage `TI_Fnt_TINETAccount_Record_Page`, CustomTab
- **Category:** Order-of-Execution
- **Extracted failure lines:**
    Component Failures [217]
    │ CustomField │ TI_Fnt_TINETAccount__c.TI_Fnt_STCAnnotation__c │ custom field is used in a component on the BPマスタ レコードページ Lightning page
- **Root cause:** The destructive set listed the FlexiPage AND the CustomObject together, assuming `destructiveChanges.xml` type order would delete the page first. It does not: all deletions in one destructive payload are validated against the PRE-deploy org state, so every field still appeared to be referenced by the (not-yet-deleted) record page, failing all 217 components. This is the whole-object analogue of the 2026-09-15 field-delete lesson, which `build_destructive.flexipage_refs()` only guards for FIELD deletes.
- **Fix applied:** Split into sequential deploys — (1) reset the object's `View` override to `Default`, (2) destructive-delete the FlexiPage, (3) destructive-delete the CustomTab + CustomObject. Each step verified live before the next.
- **Prevention added:** Treat a page-owning object teardown as a 3-phase sequence, never one destructive payload. `build_destructive.flexipage_refs()` already blocks the field-level case; for object-level deletes, delete every FlexiPage whose `sobjectType` is the target object in a PRIOR deploy.
- **Status:** Resolved

### [2026-09-24] FlexiPage: sheet writes `RecordType`, only `RecordTypeId` is placeable
- **Error signature:** `Something went wrong. We couldn't retrieve or load the information on the field: Record.RecordType. (203:22)`
- **Command:** `sf project deploy start --manifest manifest/tinetaccount_logi_page_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 30 --dry-run`
- **Component:** FlexiPage `TI_Logi_TINETAccount_Record_Page`
- **Category:** Schema
- **Extracted failure lines:**
    Component Failures [1]
    │ FlexiPage │ TI_Logi_TINETAccount_Record_Page │ couldn't retrieve or load the information on the field: Record.RecordType │ 203:22
- **Root cause:** The object tab lists the standard field as `RecordType` (the relationship), but the Dynamic-Forms-placeable field is `RecordTypeId`. `gen_record_page.py` used a DENY-list for standard fields: anything not in `HARD_DISALLOWED` / `STANDARD_NOT_PLACEABLE` and not ending `__c` skipped the org-presence check and was placed, so the unknown spelling went straight onto the page.
- **Fix applied:** `gen_record_page.py` + `gen_tabbed_page.py` gained `STANDARD_ALIASES = {"RecordType": "RecordTypeId"}` applied before any filtering.
- **Prevention added:** Both generators are now DEFAULT-DENY for standard fields — a non-`__c` field is placed only if it is in `STANDARD_PLACEABLE` (`Name`, `RecordTypeId`); anything else is dropped with a printed warning instead of shipping a page that deploys but will not render.
- **Status:** Resolved

### [2026-09-24] Lookup relationshipName collides across TI_Fnt_ / TI_Logi_ object twins
- **Error signature:** `There is already a Child Relationship named CountryList_TINETAccount on 国マスタ` (same for CurrencyMaster / IncotermsMaster1/2 / PaymentTermsMaster)
- **Command:** `sf project deploy start --manifest manifest/tinetaccount_logi_delta_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 30 --dry-run`
- **Component:** CustomField `TI_Logi_TINETAccount__c.TI_Fnt_CountryCode__c` (and CurrencyCode, Incoterms1/2, PaymentTermsCode)
- **Category:** Schema
- **Extracted failure lines:**
    Component Failures [7]
    │ CustomField │ TI_Logi_TINETAccount__c.TI_Fnt_CountryCode__c │ already a Child Relationship named CountryList_TINETAccount
    │ CustomField │ TI_Logi_TINETAccount__c.TI_Fnt_CurrencyCode__c │ already a Child Relationship named CurrencyMaster_TINETAccount
    │ CustomField │ TI_Logi_TINETAccount__c.TI_Fnt_Incoterms1__c │ already a Child Relationship named IncotermsMaster1_TINETAccount
    │ CustomField │ TI_Logi_TINETAccount__c.TI_Fnt_Incoterms2__c │ already a Child Relationship named IncotermsMaster2_TINETAccount
    │ CustomField │ TI_Logi_TINETAccount__c.TI_Fnt_PaymentTermsCode__c │ already a Child Relationship named PaymentTermsMaster_TINETAccount
    (CountryName / CurrencyName formulas failed only because those lookups did not create)
- **Root cause:** Child relationship names are unique on the PARENT object, not the child. Sheet col X reused `{Parent}_TINETAccount` names coined for `TI_Fnt_TINETAccount__c`. Deploying the same names onto `TI_Logi_TINETAccount__c` collides because both objects share the component `TINETAccount` after the `TI_Fnt_` / `TI_Logi_` prefix is stripped.
- **Fix applied:** `relname.qualify_relationship_name` inserts Fnt/Logi/Stc (`CountryList_LogiTINETAccount`). `generate_xml.py` qualifies TI_Fnt_ lookups on a TI_Logi_ object before emit. This deploy used those five qualified names.
- **Prevention added:** `validate_sheet.py` live-describes each lookup parent (`sf sobject describe`) and ERRORs `relationshipname.collision` when col X is already used by a different child object/field. In-memory qualify + INFO write-back when a TI_Fnt_ field lands on a TI_Logi_ object.
- **Status:** Resolved

### [2026-09-23] Account (standard object) roll-up fields: do not rewrite to Account__c
- **Error signature:** `object.api` ERROR Object API 'Account' invalid (must end __c); or CustomObject Account stub in package
- **Command:** deploy Account Summary fields from tab `Rollup Test` to SEAP-Conn
- **Component:** CustomField Account.TI_Fnt_Opty*  (standard parent)
- **Category:** Schema | Tooling
- **Root cause:** Pipeline assumed every object API ends `__c`. `prep_deploy.patch_object_apis` rewrote Account → Account__c; validator ERROR'd; `generate_xml` would emit a fake CustomObject. Roll-up test fields must land on existing standard Account.
- **Fix applied:** `relname.is_standard_object`. Validator allows known standard objects. generate_xml skips CustomObject XML (fields dir only). prep_deploy does not append `__c` to Account/Opportunity/….
- **Prevention added:** `is_standard_object()` shared by validate / generate / prep_deploy. Manifest is CustomField-only for those entities.
- **Status:** Resolved

### [2026-09-23] FLS on standard objects: skip non-__c; Account CRUD needs Contact
- **Error signature:** `INVALID_OR_NULL_FOR_RESTRICTED_PICKLIST` Field Name Account.Name/OwnerId/Person*; `FIELD_INTEGRITY_EXCEPTION` Permission Read Account depends on Read Contact
- **Command:** `grant_fls.py --object Account`; `grant_object_perms.py --objects Account`
- **Component:** FieldPermissions / ObjectPermissions on SalesFrontAdmin
- **Category:** Dependency | Tooling
- **Root cause:** `readMetadata(CustomObject)` for Account includes standard fields that cannot carry FieldPermissions. Account object Read on a permset requires Contact Read on Person-Account/FSC orgs.
- **Fix applied:** `grant_fls.py` skips names that do not end `__c`. `grant_object_perms.py` grants Contact first (separate insert) when Account is in the set.
- **Prevention added:** custom-only FLS filter; Contact-before-Account object-perm insert.
- **Status:** Resolved

### [2026-09-23] Formula compile: Long Text Area, `__c__r`, and typed `"TDB"` placeholders
- **Error signature:** `unsupported field type called "Long Text Area"` / `Field …__r does not exist` / Formula Date/Checkbox with `"TDB"`
- **Command:** `sf project deploy start --manifest manifest/tinetaccount_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 30 --dry-run`
- **Component:** CustomField `TI_Fnt_TINETAccount__c.TI_Fnt_STCAnnotation__c`, `TI_Fnt_CountryName__c`, `TI_Fnt_CurrencyName__c`
- **Category:** Schema
- **Extracted failure lines:**
    Component Failures [3]
    │ CustomField │ TI_Fnt_TINETAccount__c.TI_Fnt_CountryName__c │ Field CountryList_TINETAccount__r does not exist
    │ CustomField │ TI_Fnt_TINETAccount__c.TI_Fnt_CurrencyName__c │ Field CurrencyMaster_TINETAccount__r does not exist
    │ CustomField │ TI_Fnt_TINETAccount__c.TI_Fnt_STCAnnotation__c │ unsupported field type Long Text Area TI_STC_TISTCComment__c
- **Root cause:** Formulas cannot reference Long Text Area fields. Same-object lookup formulas compile against the field API (`TI_Fnt_CountryCode__r`), not a custom relationshipName; `__c__r` and a space in the API also fail. `"TDB"` is only legal on Formula Text; Date/Checkbox need TODAY() / false.
- **Fix applied:** Dummy STCAnnotation to `"TBD"`. Country/Currency formulas use `TI_Fnt_CountryCode__r` / `TI_Fnt_CurrencyCode__r`. Date placeholders → TODAY(), Checkbox → false. `generate_xml.py` normalizes `__c__r` → `__r` and replaces typed `"TDB"`/`"TBD"` on non-Text return types with dummies.
- **Prevention added:** `validate_sheet.py` ERROR `formula.rel` on `__c__r`; WARN `formula.placeholder` when `"TDB"`/`"TBD"` is used on a non-Text formula return type.
- **Status:** Resolved

### [2026-09-23] Rollup summary is column H, labeled Operation/Child/Field/Condition
<!-- Updated by Divakar N — 2026-09-23 -->
- **Error signature:** `type>Summary</type>` with no `summaryOperation` / `summaryForeignKey` / `summarizedField` / `summaryFilterItems`; or treating col G as the roll-up setup
- **Command:** fetch → validate → generate for `Rollup summary` rows
- **Component:** CustomField Summary (roll-up)
- **Category:** Schema | Tooling
- **Root cause:** Format-tab H (`設定値`) is the type-specific value for every other type, but Summary XML was emitted as `<type>Summary</type>` only. Validator looked for non-existent columns `Summary Foreign Key` / `Summary Operation`. A col-G convention was considered, then corrected to H.
- **Fix applied:** `scripts/rollup_h.py` parses H (`Operation:COUNT` / `Child:…` / `Field:…` / `Condition:…`, no space after the colon; symbol operators). `fetch_sheet.py` applies it; `validate_sheet.py` ERRORs blank/unparseable H (`summary.parse`); `generate_xml.py` emits the four Metadata API elements; `attr_drift.py` compares them.
- **Prevention added:** blank H on Rollup summary is a hard blocker (no dummy). Reference: `.cursor/rules/sf-rollup-summary-column-h.mdc`.
- **Status:** Resolved

### [2026-09-21] Lookup relationshipName longer than 40 chars is undeployable
- **Error signature:** `relationshipName` / `The relationship name is too long` / data value too large (Metadata API max 40)
- **Command:** validate `TradeTermsAppBalanceHistory` (also seen on `TradeTermsAppCompanyTrade` the same day)
- **Component:** CustomField Lookup `relationshipName` col X
- **Category:** Schema
- **Root cause:** Object-scoped child relationship names built as `<full object component>_<parent>` overflow Salesforce's 40-character `relationshipName` limit. Example: `TradeTermsAppBalanceHistory_TradeTermsApplication` is 49 chars. The validator previously only checked blank vs filled, so a too-long name passed STEP-0 and would fail at deploy.
- **Fix applied:** Shorten to a unique object token + parent (`BalanceHistory_TradeTermsApplication`, 36 chars), matching `PerfHistory_` / `CompanyTrade_`. `generate_xml.py` no longer emits a >40 name; it falls back to `derive_relationship_name` (already capped).
- **Prevention added:** `validate_sheet.py` ERROR `relationshipname.length` when col X exceeds `MAX_RELATIONSHIP_NAME` (40) in `relname.py`.
- **Status:** Resolved

### [2026-09-17] recordHomeTemplateDesktop without header/sidebar does not render
- **Error signature:** Lightning record page "not coming" / blank at runtime while FlexiPage deploy Succeeded
- **Command:** `gen_record_page.py` then `deploy.py --start` FlexiPage `TI_Fnt_CreditSecurityInfo_Record_Page`
- **Component:** FlexiPage `TI_Fnt_CreditSecurityInfo_Record_Page`
- **Category:** Schema
- **Root cause:** `flexipage:recordHomeTemplateDesktop` requires Region names `header`, `main`, and `sidebar`. The generator emitted only `main` (field sections). Salesforce accepts the metadata deploy, but Lightning Runtime will not load the page.
- **Fix applied:** `gen_record_page.py` and `gen_tabbed_page.py` now emit a `header` with `force:highlightsPanel` and an empty `sidebar`. Redeployed CSI page; live read confirms all three regions + org-default `defaultPages` Large/Small.
- **Prevention added:** `require_desktop_regions()` in both generators exits non-zero if any of header/main/sidebar is missing.
- **Status:** Resolved

### [2026-09-17] Picklist Label:ApiName collides with existing JP values
- **Error signature:** `Duplicate label: 追加`
- **Command:** `sf project deploy start --manifest manifest/paymenttermsbplink_picklist.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run --ignore-conflicts`
- **Component:** CustomField `TI_Fnt_PaymentTermsBPLink__c.TI_Fnt_PrimaryAdditionalCategory__c`
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [1]
    │ CustomField │ TI_Fnt_PaymentTermsBPLink__c.TI_Fnt_PrimaryAdditionalCategory__c │ Duplicate label: 追加 (30:13) │ 30:13 │
- **Root cause:** The field was first deployed with values `代表` / `追加` (label = stored value). Changing the sheet to `代表:Primary;追加:Additional` adds NEW value API names (`Primary`, `Additional`) that reuse the same labels. Salesforce picklist deploy MERGES the value set; the old JP fullNames remain, so labels `代表` and `追加` appear twice.
- **Fix applied:** With 0 records, delete+recreate the field (strip it from the FlexiPage first — page refs block delete). Recreate with `Primary`/`Additional` as fullNames and JP labels. Did NOT set IsDelete on the sheet (the row is being kept).
- **Prevention added:** Do not try to retarget an existing picklist from `Label` to `Label:ApiName` via a normal field update. Either (a) delete+recreate when the object has 0 rows, after stripping FlexiPage refs, or (b) keep the old fullNames and only change labels. `validate_sheet.py` still allows Label:ApiName (correct for NEW fields); this is an UPDATE-time collision.
- **Status:** Resolved

### [2026-09-17] Shared package.xml clobber → NothingToDeploy (real start)
- **Error signature:** `Error (NothingToDeploy): No local changes to deploy.`
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60`
- **Component:** CustomObject `TI_Fnt_PaymentTermsBPLink__c` (intended); actual manifest had been overwritten to `TI_Fnt_CreditSecurityInfo__c`
- **Category:** Tooling
- **Extracted failure lines:**
    Error (NothingToDeploy): No local changes to deploy.
- **Root cause:** Same race as the dry-run sibling: shared `manifest/package.xml` was overwritten to CreditSecurityInfo before this start. Those members had no local delta, so the CLI reported NothingToDeploy. Not a Salesforce metadata defect.
- **Fix applied:** Object-scoped `manifest/paymenttermsbplink_package.xml`. Do not reuse the shared package.xml while another tab's pipeline is running.
- **Prevention added:** Same as the dry-run lesson — per-object `--out manifest/<object>_package.xml`. Confirm manifest members before start.
- **Status:** Resolved

### [2026-09-17] Shared package.xml clobber → NothingToDeploy
- **Error signature:** `Error (NothingToDeploy): No local changes to deploy.`
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** CustomObject `TI_Fnt_PaymentTermsBPLink__c` (intended); actual manifest had been overwritten to `TI_Fnt_CreditSecurityInfo__c`
- **Category:** Tooling
- **Extracted failure lines:**
    Error (NothingToDeploy): No local changes to deploy.
- **Root cause:** `manifest/package.xml` is a shared disposable artifact. A parallel CreditSecurityInfo build overwrote it between our generate step and the retry dry-run. Source tracking then compared the CreditSecurityInfo members (already in the org / no local delta) and reported NothingToDeploy. PaymentTermsBPLink metadata was still on disk and was never in that manifest.
- **Fix applied:** Built an object-scoped manifest `manifest/paymenttermsbplink_package.xml` and deployed from that path so the shared `package.xml` cannot steal the target set.
- **Prevention added:** For any deploy that can race with another tab's `build_manifest.py`, write `--out manifest/<object>_package.xml` instead of the shared `manifest/package.xml`. When NothingToDeploy appears after a known-good generate, cat the manifest members first.
- **Status:** Resolved

### [2026-09-17] TextArea must not emit Metadata API `<length>`
- **Error signature:** `Can not specify 'length' for a CustomField of type TextArea`
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** CustomField `TI_Fnt_PaymentTermsBPLink__c.TI_Fnt_ApplicationDetail__c`
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [1]
    │ CustomField │ TI_Fnt_PaymentTermsBPLink__c.TI_Fnt_ApplicationDetail__c │ Can not specify 'length' for a CustomField of type TextArea (51:13) │ 51:13 │
- **Root cause:** Salesforce `TextArea` (the 255-char multi-line type) has a fixed length. The Metadata API rejects an explicit `<length>` element. `generate_xml.py` copied the sheet Size cell into `<length>255</length>`, and `validate_sheet.py` incorrectly required Length for TextArea, which pushed us to fill that cell.
- **Fix applied:** `generate_xml.py` omits `<length>` for TextArea (logs an INFO if the sheet has a size). Regenerated `TI_Fnt_ApplicationDetail__c` without `<length>`.
- **Prevention added:** `validate_sheet.py` no longer requires Length on TextArea; if the sheet has a size it emits INFO `textarea.length` that the generator will omit it. LongTextArea/Html still require Length.
- **Status:** Resolved

### [2026-09-17] FlexiPageRegion itemInstances after name/type
- **Error signature:** `Element itemInstances is duplicated at this location in type FlexiPageRegion`
- **Command:** `sf project deploy start --manifest manifest/flexipage_shipping.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 10 --dry-run`
- **Component:** FlexiPage `TI_Fnt_Shipping_Record_Page`
- **Category:** Schema
- **Extracted failure lines:**
    Status: Failed
    Component Failures [1]
    │ FlexiPage │ TI_Fnt_Shipping_Record_Page │ Error parsing file: Element itemInstances is duplicated at this location in type FlexiPageRegion (93:22) │ 93:22       │
- **Root cause:** FlexiPageRegion's Metadata XSD sequence is `itemInstances*, name, type`. When merging the generated field tabset with the org page's RaySheet/related-list tabsets, the extra `<itemInstances>` were appended AFTER `<name>main</name>` / `<type>Region</type>`. The parser then reports those late siblings as a duplicated `itemInstances` element (not a true duplicate identifier).
- **Fix applied:** Reordered every `flexiPageRegions` child list so all `itemInstances` precede `name`/`type`. Dry-run then real deploy of `TI_Fnt_Shipping_Record_Page` succeeded (Deploy ID 0AfBK00000CVB3V0AX). Added `normalize_region_child_order()` in `scripts/gen_tabbed_page.py` and call it at the end of `build()`.
- **Prevention added:** `gen_tabbed_page.build()` now always normalizes region child order before write. Any merge that adds tabsets to an existing `main` region must insert them before `<name>`/`<type>`, never after.
- **Status:** Resolved

### [2026-09-17] AUTO-DRAFT: deploy validate (dry-run) failed (exit 1)  «sig:e75c18de20»
- **Error signature:** (none extracted)
- **Command:** `sf project deploy start --manifest manifest/flexipage_receiving.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** FlexiPage `TI_Fnt_Receiving_Record_Page`
- **Category:** Environment/Org (API-version mismatch between manifest and source)
- **Extracted failure lines:**
    Status: Failed
    Component Failures [1]
    │ FlexiPage │ TI_Fnt_Receiving_Record_Page │ Invalidnull property [label] in component [flexipage:tabset] │             │
- **Root cause:** The hand-written `manifest/flexipage_receiving.xml` declared
  `<version>62.0</version>` while the project's `sourceApiVersion` is `66.0` and the
  page had been RETRIEVED at 66.0. The `label` property on `flexipage:tabset` only
  exists in the newer schema, so validating the 66.0-shaped XML against the 62.0
  metadata schema rejected it as an invalid/null property. The error was NOT caused
  by the field edits being deployed and was NOT a real defect in the page.
- **Fix applied:** Set the manifest `<version>` to `66.0` to match
  `sfdx-project.json` → `sourceApiVersion`. Re-validated clean and deployed
  successfully (Deploy ID 0AfBK00000CV3Ir0AL, then real deploy Succeeded).
- **Prevention added:** Rule of thumb now recorded here — any hand-written
  `manifest/*.xml` MUST carry the same `<version>` as `sfdx-project.json`'s
  `sourceApiVersion`; a lower manifest version silently re-validates retrieved
  metadata against an older schema and produces bogus "Invalid/null property
  [X] in component [Y]" failures. When that error text appears, check the
  manifest-vs-project API version FIRST before editing the metadata.
- **Status: Resolved** (org=TI_ERPDEV01, log=.build/last_deploy.log)

### [2026-09-15] AUTO-DRAFT: deploy start failed (exit 1)  «sig:7c8b9116ed»
- **Error signature:** (none extracted)
- **Command:** `sf project deploy start --manifest manifest/destructive_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --pre-destructive-changes manifest/destructiveChanges.xml --ignore-conflicts`
- **Component:** CustomField TI_Fnt_Receiving__c.TI_Fnt_DepartmentCode__c and .TI_Fnt_PartnerBank__c (destructive delete)
- **Category:** Dependency
- **Extracted failure lines:**
    Status: Failed
    Component Failures [4]
- **Root cause:** Both fields were still placed on the Lightning record page TI_Fnt_Receiving_Record_Page. Salesforce refuses to delete a field referenced by a FlexiPage component ("The <label> custom field is used in a component on the <page> Lightning page") and fails the ENTIRE destructive set with it — page references block a delete just as formula or layout references do.
- **Fix applied:** Retrieved the FlexiPage, removed the two <itemInstances> blocks holding Record.TI_Fnt_DepartmentCode__c and Record.TI_Fnt_PartnerBank__c, deployed the page, deleted the fields, recreated them as Lookups, then redeployed the ORIGINAL page (backed up to .build/) so both fields returned to their exact former positions.
- **Prevention added:** build_destructive.py now has flexipage_refs(): before writing destructiveChanges.xml it retrieves every FlexiPage in the org and maps each delete-set field to the pages referencing it. Any field still on a page BLOCKS the build (exit 2) and prints the page names, so the page-strip step happens up front instead of the delete failing at the org.
- **Status:** Resolved — (auto-written by log_failure.py; agent must complete + flip to Resolved/Monitoring; org=TI_ERPDEV01, log=.build/last_deploy.log)

### [2026-09-15] AUTO-DRAFT: deploy start failed (exit 1)  «sig:707ee2be5d»
- **Error signature:** STATE
- **Command:** `sf project deploy start --manifest manifest/destructive_package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --pre-destructive-changes manifest/destructiveChanges.xml`
- **Component:** CustomField TI_Fnt_Receiving__c.TI_Fnt_PartnerBank__c (destructive delete)
- **Category:** Environment/Org
- **Extracted failure lines:**
    Error (1): There are changes in the org that conflict with the local changes you're trying to deploy.
- **Root cause:** The org has source tracking enabled. The local TI_Fnt_PartnerBank__c.field-meta.xml (regenerated from the sheet with referenceTo TI_Logi_TINETAccount__c) differed from the org copy (referenceTo TI_Fnt_BankMaster__c), so the CLI classified the component as a Conflict and refused the deploy before reaching the org. This is a source-tracking guard, not a metadata error.
- **Fix applied:** Re-ran the destructive deploy with --ignore-conflicts (deploy.py already exposes the flag). Safe here because the field was being deleted outright, so the org copy was discarded deliberately.
- **Prevention added:** When deleting, or intentionally overwriting a drifted field, pass --ignore-conflicts to deploy.py. Treat "changes in the org that conflict" as the expected drift already surfaced by attr_drift.py, and confirm that drift is the one being discarded before using the flag.
- **Status:** Resolved — (auto-written by log_failure.py; agent must complete + flip to Resolved/Monitoring; org=TI_ERPDEV01, log=.build/last_deploy.log)

### [2026-09-15] AUTO-DRAFT: deploy validate (dry-run) failed (exit 1)  «sig:d513d6a762»
- **Error signature:** (none extracted)
- **Command:** `sf project deploy start --manifest manifest/package.xml --target-org TI_ERPDEV01 --test-level NoTestRun --wait 60 --dry-run`
- **Component:** CustomField (27 members of TI_Fnt_Receiving__c) — manifest/package.xml
- **Category:** Tooling
- **Extracted failure lines:**
    Error (ComponentSetError): No source-backed components present in the package.
- **Root cause:** generate_xml.py writes force-app/ relative to the CURRENT WORKING DIRECTORY. It was run from sf-deploy/, so the metadata landed in sf-deploy/force-app, which is not a package directory of the sf project (sfdx-project.json at the workspace root declares only the root force-app). The sf CLI resolved the manifest against the project root, found no matching source files, and aborted with "No source-backed components present in the package" — the package never reached the org.
- **Fix applied:** Re-ran the build from the project root so generated metadata lands in the root force-app (the declared package directory), then re-validated and deployed from there.
- **Prevention added:** Run generate_xml.py / build_manifest.py / deploy.py from the sf project root (the directory holding sfdx-project.json), never from sf-deploy/. Read "No source-backed components present in the package" as a cwd/package-directory mismatch, not as missing metadata.
- **Status:** Resolved — (auto-written by log_failure.py; agent must complete + flip to Resolved/Monitoring; org=TI_ERPDEV01, log=.build/last_deploy.log)

### [2026-09-11] FlexiPage fieldInstance rejects CreatedDate/LastModifiedDate/OwnerId
- **Error signature:** `Something went wrong. We couldn't retrieve or load the information on the field: Record.CreatedDate`
- **Command:** `sf project deploy start --manifest package_qum_flexi.xml --target-org TI_ERPDEV01 --dry-run`
- **Component:** `FlexiPage` `TI_Fnt_QuantityUnitMaster_Record_Page` (Record.CreatedDate)
- **Category:** Schema
- **Root cause:** `gen_tabbed_page.py` placed sheet standard audit/ownership fields as Dynamic Forms `fieldInstance`s. Lightning rejects them; `gen_record_page.py` already skipped them, the tabbed generator did not.
- **Fix applied:** `gen_tabbed_page.py` now filters `FLEXIPAGE_NOT_PLACEABLE` and drops empty sections.
- **Prevention added:** generator guard in `gen_tabbed_page.py`. `grant_tab_visibility.py` TabSetting reads now use Tooling.
### [2026-09-22] Isolated CustomObject description deploy hits source-tracking conflict
- **Error signature:** `There are changes in the org that conflict with the local changes you're trying to deploy.` / `Conflict TI_Fnt_ShippingDetail__c CustomObject`
- **Command:** `python scripts/deploy.py --start --package manifest/package.xml --target-org ERPDEV01 --test-level NoTestRun --skip-validation-gate` (cwd `.build/sd-desc-fix`)
- **Component:** CustomObject `TI_Fnt_ShippingDetail__c` (object-meta description only)
- **Category:** Tooling
- **Extracted failure lines:**
    Error (1): There are changes in the org that conflict with the local changes you're trying to deploy.
- **Root cause:** Check-only succeeded. The real `deploy start` uses Salesforce source tracking. The mini-project object-meta was retrieved then edited locally; tracking still saw org-side CustomObject drift vs that snapshot, so the write was refused. `--dry-run` did not surface this conflict the same way.
- **Fix applied:** Re-run the same package with `deploy.py --start --ignore-conflicts` so the intended description overwrite is the only change sent. Pipeline no longer copies sheet layout-note `説明` (レコードタイプ/入力規則/後で追加) into `CustomObject.description`.
- **Prevention added:** `generate_xml._org_object_description` and `fill_org_object_descriptions` skip layout-note 説明. Isolated object-meta deploys from a retrieved snapshot must pass `--ignore-conflicts` when tracking reports Conflict on that same CustomObject.
- **Status:** Resolved

### [2026-09-21] Existing-field EN/provenance edit skipped by name-delta
- **Error signature:** sheet `TI_Fnt_MoveInDestination__c` EN `Delivery destination` (google provenance) while org still had `Move-in destination`; first `--phase deploy` reported complete because the field already existed
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs ShipoutMovein --phase deploy`
- **Component:** CustomObjectTranslation `TI_Fnt_ShipoutMovein__c-en_US` / `TI_Fnt_MoveInDestination__c`
- **Category:** Tooling
- **Extracted failure lines:**
    sheet EN/provenance changed; field not in schema delta; verify only checked packaged rows
- **Root cause:** Schema delta is name-only (`sheet fields − org fields`). An existing field whose English or provenance changed is excluded from the create package. Translation classify can pick `CHANGED_TRANSLATION`, but (1) that delta was not printed next to the schema delta, (2) `verify_deploy` only compared packaged rows, (3) an empty plan after a later EN edit still reported success, (4) sync state stored only packaged labels so a Google overwrite on an already-translated field was easy to miss if completeness was inferred from field existence.
- **Fix applied:** `classify_against_org` also diffs sheet EN against last deployed sync-state hash; `print_delta` always prints TRANSLATIONS new/changed; `verify_deploy` compares every in-scope sheet EN to the live org; empty plans still run that verify; sync state stores every verified label.
- **Prevention added:** STEP 2 §3c in `sf-deploy-delta-and-blockers.mdc` — translation EN/provenance drift is a mandatory delta, independent of field name-existence.
- **Status:** Resolved

### [2026-09-21] Empty .sfhome shim → false missing-referenceTo ERROR
- **Error signature:** `dependency.ref.org: referenceTo 'TI_Fnt_Shipping__c' does NOT exist in the target org and is not created in this deploy` while EntityDefinition live shows the object present
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs ShipoutMovein --phase deploy`
- **Component:** `validate_sheet.py` `query_org_objects` / env `HOME=.sfhome`
- **Category:** Environment/Org
- **Extracted failure lines:**
    ❌ [TI_Fnt_ShipoutMovein__c::TI_Fnt_Shipping__c] (dependency.ref.org) referenceTo 'TI_Fnt_Shipping__c' does NOT exist in the target org and is not created in this deploy
    NamedOrgNotFoundError: No authorization information found for ERPDEV01
- **Root cause:** `prep_deploy.py` ran `validate_sheet.py` with `HOME=.sfhome`. That shim has empty `.sfdx/alias.json` (`{"orgs":{}}`), so `sf data query --target-org ERPDEV01` returns status=2 JSON with no `result.records`. `query_org_objects` treated that as a successful empty set, so every custom `referenceTo` looked missing. The parent object actually exists in ERPDEV01.
- **Fix applied:** `query_org_objects` returns None (skip live check, fail-open) when `sf` JSON `status != 0`. `prep_deploy.py` `--sf-home` defaults to the real HOME so org aliases resolve.
- **Prevention added:** non-zero `sf` query status is never interpreted as "object absent".
- **Status:** Resolved

### [2026-09-21] nameFieldLabel deploy Succeeds but does not apply without nonempty caseValues
- **Error signature:** live verify `Name: org '' != sheet 'Shipping origin / move-in destination'` after `sf project deploy start` Status: Succeeded for `CustomObjectTranslation TI_Fnt_ShipoutMovein__c-en_US`
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs ShipoutMovein --phase deploy`
- **Component:** CustomObjectTranslation `TI_Fnt_ShipoutMovein__c-en_US` / `<nameFieldLabel>`
- **Category:** Schema
- **Extracted failure lines:**
    ✗ Name: org '' != sheet 'Shipping origin / move-in destination'
    VERIFICATION FAILED — the deploy did NOT fully land.
- **Root cause:** Salesforce Metadata API docs: when deploying a change to `nameFieldLabel`, the payload MUST include at least one top-level `<caseValues>` entry with a nonempty `<value>`. Otherwise the deploy reports Succeeded but the Name translation is not applied. The generator emitted only `<nameFieldLabel>` (no Object Label EN on this tab, org caseValues were comment-only `<!-- 日本語 -->`). Custom field labels landed; Name did not. `findtext("nameFieldLabel")` then correctly read empty text from the org.
- **Fix applied:** `generate_object_translation.py` always emits nonempty top-level `caseValues` when packaging `nameFieldLabel`, using (1) sheet Object Label EN, else (2) org comment / sheet Japanese object master as a carrier so we do not invent object English from AJ1, else (3) the Name English as last resort. Guard raises if Name is packaged with blank caseValues. `read_metadata` keeps the raw SOAP fragment so comment-only labels can be used as that carrier without being treated as live English.
- **Prevention added:** generator hard-fail if `nameFieldLabel` would be written without a nonempty `caseValues` value — the same silent Salesforce no-op cannot be packaged again.
- **Status:** Resolved

### [2026-09-21] CustomFieldTranslation for standard Name → Cannot translate standard field
- **Error signature:** `CustomObjectTranslation TI_Fnt_ShipoutMovein__c-en_US: Cannot translate standard field: TI_Fnt_ShipoutMovein__c.Name`
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs ShipoutMovein` (check-only)
- **Component:** CustomObjectTranslation `TI_Fnt_ShipoutMovein__c-en_US` / `Name.fieldTranslation-meta.xml`
- **Category:** Schema
- **Extracted failure lines:**
    │ CustomObjectTranslation │ TI_Fnt_ShipoutMovein__c-en_US │ Cannot translate standard field: TI_Fnt_ShipoutMovein__c.Name (4:13) │ 4:13        │
- **Root cause:** Salesforce translates the object's standard Name via `<nameFieldLabel>` on CustomObjectTranslation. Emitting `Name.fieldTranslation-meta.xml` (CustomFieldTranslation) is rejected as translating a standard field.
- **Fix applied:** `generate_object_translation.py` writes Name English only to `<nameFieldLabel>`, never as a field translation file (and deletes a leftover `Name.fieldTranslation-meta.xml`).
- **Prevention added:** generator skip for `name == "Name"` in both org-field copy and field-file emit, so the illegal file cannot be packaged again.
- **Status:** Resolved

### [2026-09-21] Missing sfdx-project.json → InvalidProjectWorkspaceError on dry-run
- **Error signature:** `Error (InvalidProjectWorkspaceError): …/sf-deploy does not contain a valid Salesforce DX project.`
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs ShipoutMovein` → `sf project deploy start --manifest manifest/package.xml --dry-run`
- **Component:** Tooling / project workspace (`sfdx-project.json`), not a metadata member
- **Category:** Tooling
- **Extracted failure lines:**
    Error (InvalidProjectWorkspaceError): /Users/ankita.nair/Documents/objectDefinitionDeployment-main/sf-deploy does not contain a valid Salesforce DX project.
- **Root cause:** `sf project deploy` requires a DX project file in the cwd. README listed `sf-deploy/sfdx-project.json` but the file was never in the repo, so check-only failed before any metadata was sent.
- **Fix applied:** added `sf-deploy/sfdx-project.json` (`packageDirectories.path = force-app`, `sourceApiVersion` 60.0, matching `build_manifest.py` default).
- **Prevention added:** keep `sf-deploy/sfdx-project.json` in the repo. `sf project deploy` already errors if it is missing.
- **Status:** Resolved

### [2026-09-11] FLS grant was DELTA/local-file scoped → historical gaps never backfilled (106 fields across 11 objects)
- **Error signature:** Yagai report + live audit — fields deployed in earlier batches lacked `FieldPermissions` on `SalesFrontAdmin` even though later deploys "granted FLS". True classified gap: **106** deployable custom fields with no FLS (e.g. ExportImportRelatedInfo 36, Shipping 33, ShippingDetail 14, DeliveryDestination 9, IncidentalExpenses/StandaloneIE 4 each).
- **Command:** N/A (post-deploy access gap). Surfaced via live `CustomField` (Tooling) vs `FieldPermissions` diff, then classified via `readMetadata(CustomObject)`.
- **Component:** `FieldPermissions` on PermissionSet `SalesFrontAdmin`, across TI_Fnt_ objects.
- **Category:** Order-of-Execution (grant scoped to the wrong input set).
- **Root cause:** `grant_fls.py` granted from the LOCALLY-GENERATED field-meta files of the CURRENT deploy (`force-app/main/default/objects/<Obj>/fields/*.xml`) — i.e. only the delta being deployed. Fields created in a PRIOR batch were never in the current local dir, so the grant never looked at them; if their original grant was incomplete (partial batch error, required→optional flip, or the pre-gate era), the gap was permanent because nothing ever reconciled against the org's FULL live field set. Delta-scoping = no self-healing. NOTE the raw "no FLS" counts (Deal 66, Shipping 52…) were INFLATED by required/MasterDetail/already-granted-readonly fields that legitimately cannot/do not need a new FieldPermissions row — the real actionable gap is smaller (Deal was actually 0) and only visible after live classification.
- **Fix applied:** rewrote `grant_fls.py` to be **LIVE-DRIVEN**: for each object it reads the org's full custom-field set via `readMetadata(CustomObject)` (FLS-independent), classifies each field (required/MD → skip; AutoNumber/Summary/Formula → read-only; else read+edit), diffs against existing `FieldPermissions`, and inserts only the MISSING ones (idempotent, non-destructive — existing/manual read-only untouched). Added `--all-tifnt` for a whole-project reconciliation and a live re-verify per object. Backfill run: **106 inserted, 0 errors, 0 still-missing.**
- **Prevention added:** live-driven `grant_fls.py` (no longer depends on local delta files) — GATE 1 now reconciles the FULL live field set every run, so any historical or future FLS gap self-heals on the next grant. Rule `sf-post-deploy-fls-and-flexipage.mdc` GATE 1 updated to mandate the live-driven helper.
- **Status:** Resolved.

### [2026-09-09] Field FLS granted but OBJECT permissions never granted → object unreachable (ALL 31 TI_Fnt_ objects)
- **Error signature:** user report — cannot open the `容器証明書` (ContainerCertificate) tab/object; "insufficient privileges". Live audit: `ObjectPermissions` on `SalesFrontAdmin` = NONE for **0/31** deployed `TI_Fnt_` objects, despite `FieldPermissions` (FLS) being present.
- **Command:** N/A (post-deploy access gap, not a deploy-command failure). Surfaced via ObjectPermissions SOQL on `SalesFrontAdmin`.
- **Component:** `ObjectPermissions` on PermissionSet `SalesFrontAdmin` for all 31 `TI_Fnt_*__c` objects.
- **Category:** Dependency (access control) / Order-of-Execution (missing pipeline gate).
- **Root cause:** the post-deploy pipeline auto-granted FIELD-level security (GATE 1, `FieldPermissions`) but never granted OBJECT-level permissions (`ObjectPermissions` CRUD). FLS only controls which fields are visible on a record the user can already reach; with no `ObjectPermissions` row the user cannot open/list/create the record at all, so the object (and its tab) is completely inaccessible even though every field is "granted". No gate existed to catch this, so it silently affected the whole project.
- **Fix applied:** (1) immediate — built `scripts/grant_object_perms.py` (idempotent CRUD grant + live verify; supports `--all-tifnt`) and granted Read+Create+Edit+Delete on `SalesFrontAdmin` for all 31 objects. 2 junction objects (`RecevingAndQuoteAndWorkRelation`, `ShippingAndQuoteAndWorkRelation`) failed with `FIELD_INTEGRITY_EXCEPTION: Permission Read <child> depends on permission(s): Read TI_Logi_QuotationAndWork__c` — granted the Master parent's Read first (minimal, cross-namespace), then the children. Final live verify: **31/31** have full R/C/E/D. (2) pipeline — added **GATE 1c** to `sf-post-deploy-fls-and-flexipage.mdc`: object-level CRUD auto-grant on `SalesFrontAdmin` is now MANDATORY + AUTOMATIC for every deployed object, with the Master-Detail-parent-Read dependency handling and mandatory live verification.
- **Prevention added:** GATE 1c in `sf-post-deploy-fls-and-flexipage.mdc` (mandatory object-perm auto-grant + live verify, MD-parent dependency handling) and the reusable `scripts/grant_object_perms.py` helper. A deploy is no longer "done" while any deployed object shows `ObjectPermissions = NONE`.
- **Status:** Resolved.

### [2026-09-09] Bare field-derived relationshipName collides on a SHARED parent (User) → object-scope it
- **Error signature:** dry-run Component Failure — `CustomField TI_Fnt_ExportControlClassification__c.TI_Fnt_SalesDeliveryInCharge__c: There is already a Child Relationship named TI_Fnt_SalesDeliveryInCharge on User. (140:13)`
- **Command:** `deploy.py --package manifest/ecc_package.xml` (check-only), deploying `TI_Fnt_ExportControlClassification__c` + 21 fields.
- **Component:** CustomField `TI_Fnt_SalesDeliveryInCharge__c` (Lookup → User); also `TI_Fnt_SalesRepresentative__c`.
- **Category:** Dependency (metadata uniqueness)
- **Root cause:** `fill_relationship_name.py` derived the relationshipName as the field API name minus `__c`
  (`TI_Fnt_SalesDeliveryInCharge`). Its docstring assumed the namespace made that globally unique, but a
  **child-relationship name must be unique across ALL child objects that look up the same parent**. `User` is a
  shared parent with lookups from many objects; another object already had a `TI_Fnt_SalesDeliveryInCharge__c`
  → same derived child-relationship name → collision.
- **Fix applied:** (1) immediate — changed the two User-lookup relationshipNames to object-scoped values on the
  sheet (col X, gated) + AH `[FIX]`: `ExpControlCls_SalesDeliveryInCharge` / `ExpControlCls_SalesRepresentative`;
  redeploy passed. (2) producer — `fill_relationship_name.py` now OBJECT-SCOPES the generated name whenever the
  parent is a SHARED standard object (`SHARED_PARENTS`: User/Account/Contact/Lead/Group/Case/Opportunity/…): it
  prefixes a short acronym of the owning object (capitals, ≥3, else trimmed component), e.g.
  `ExportControlClassification → ECC_SalesDeliveryInCharge` (capped 40). Custom parents keep the namespaced bare
  name (cross-object collision there is unlikely and namespaced).
- **Prevention added:** the `SHARED_PARENTS` object-scoping in `fill_relationship_name.py::rel_name` (the producer
  that fills blank relationship names) — blank rel names on shared-parent lookups can no longer be auto-filled
  with a collision-prone bare name.
- **Status:** Resolved.

### [2026-09-09] New object's Name field = AutoNumber with a BLANK display format → object cannot deploy (PARK, do not guess a format)
- **Error signature:** pre-deploy blocker (would otherwise fail Metadata deploy). New object
  `TI_Fnt_ExportControlClassification__c` declared its standard `Name` field as **AutoNumber** in the object-meta
  block but left the **display format blank**. Salesforce REJECTS an AutoNumber Name field with no `displayFormat`
  (a CustomObject cannot be created/updated without a valid `<nameField>`), so the whole object is un-deployable
  until a format is supplied.
- **Command:** STEP-0 blocker scan for the 4 new objects (`AppendedTableMaster`, `ExportControlClassification`,
  `ContainerCertificate`, `ConditionsPerformanceReport`); caught before packaging.
- **Component:** CustomObject `<nameField>` (`type=AutoNumber`, `displayFormat` empty) for
  `TI_Fnt_ExportControlClassification__c`.
- **Category:** Schema.
- **Root cause:** the sheet's object-meta `Name Field Type` = `Autonumber` but `Name Field Display Format` is
  empty. AutoNumber requires a format string (e.g. `ECC-{0000000000}`). The format is a client/business decision
  (prefix + width), so it must NOT be auto-invented by the tool — a wrong format would permanently mis-number
  every record.
- **Fix applied (initial):** did NOT deploy the object. Parked it: coupled `flag_blocker.py` note on the tab (AH
  note + Col-A pink highlight on the object's lead field row) recording "OBJECT PARKED — AutoNumber Name field has
  a blank display format; pending Yagai-san". The other 3 objects deployed normally.
- **Update (2026-09-09, later, per explicit user instruction):** user directed us to GENERATE an interim
  AutoNumber format and proceed with the deploy now (rather than keep it parked), while still informing Yagai. We
  set `displayFormat = ECC-{0000000000}` (ECC = Export Control Classification, matching the sibling
  `CPR-{0000000000}` convention: 3-letter prefix + 10-digit zero-padded counter). NOTE: the client had typed the
  same string into col G (the DESCRIPTION column, `データ型に応じて…`), which the pipeline deliberately ignores;
  the real value column is col H (`数式(設定値)`), which was blank — so the format was written to **H12** (the Name
  row's 数式設定値 cell), the cell `fetch_sheet` actually reads. Object then deployed + verified.
- **⚠️ OPEN ACTION — INFORM YAGAI-SAN (Takeaki Yagai, sheet owner):** the `ECC-{0000000000}` format is an
  INTERIM assistant-generated numbering scheme, NOT client-confirmed. Yagai must confirm the prefix + digit width
  (changing an AutoNumber format later renumbers/relabels future records; existing record Names already generated
  are NOT retroactively changed). Keep this reminder until Yagai confirms or supplies the final format.
- **Prevention added:** RULE — **when a new/updated object's Name field is AutoNumber with a blank display
  format, treat it as a HARD Step-0 blocker: PARK the object, flag it via `flag_blocker.py` (AH + Col-A), and ask
  the sheet owner for the format. NEVER auto-generate/guess an AutoNumber display format on your own initiative**
  (unlike a blank formula body, which IS auto-filled with a dummy — a Name numbering scheme is not a safe thing to
  fabricate). The interim-format exception above was a DELIBERATE, explicit user override for this one object and
  does NOT relax the default rule. Codified in `.cursor/rules/sf-object-name-autonumber-format.mdc`.
- **Status:** Monitoring (deployed with interim format; awaiting Yagai-san's confirmation of the numbering scheme).

### [2026-09-09] Drift check skipped the standard Name field — Text-vs-AutoNumber drift went undetected
- **Error signature:** no deploy error — a SILENT data-correctness miss. `attr_drift.py` reported
  `drifted: 0` for `TI_Fnt_Receiving__c` and `TI_Fnt_ReceivingDetail__c`, but both had `Name = Text` in the
  org while the sheet declared **AutoNumber** with display formats `RCV{0000000}` / `RCD{0000000}`. The
  mismatch was invisible to every drift run until the user questioned it.
- **Command:** `attr_drift.py --object TI_Fnt_Receiving__c` / `--object TI_Fnt_ReceivingDetail__c`.
- **Component:** `scripts/attr_drift.py` (`read_org_object`, `main`); standard `Name` field via CustomObject
  `<nameField>`.
- **Category:** Tooling.
- **Root cause:** `attr_drift.py` filtered rows with `if not api.endswith("__c"): continue` and read only the
  CustomObject `<fields>` block. The standard `Name` field is neither a `__c` field nor under `<fields>` (it
  lives in the `<nameField>` block), so it was never compared — a custom-only drift check is a false "all clear".
- **Fix applied:** `read_org_object` now captures `org["__nameField__"] = {type, displayFormat, label}` from the
  CustomObject `<nameField>`; `main` locates the sheet object-meta row (`Name Field Type` / `Name Field Display
  Format`) and emits a `Name` drift entry when the type or (for AutoNumber) the displayFormat differs, and prints
  a WARNING if either side's Name definition is missing instead of silently passing.
- **Prevention added:** new always-apply rule `.cursor/rules/sf-drift-includes-standard-fields.mdc` mandates the
  Name-field comparison on every drift run; the Name fix is flagged DESTRUCTIVE (overwrites existing record Names)
  and requires explicit user approval before deploy.
- **Status:** Resolved.

### [2026-09-09] Pipeline read the wrong value column (G, a description) instead of H (the real value) + attr_drift never compared formula bodies
- **Error signature:** no deploy error — a SILENT data-correctness miss. EIRI Formula Text fields shipped
  with a DUMMY `"TBD"` formula (e.g. `TI_Fnt_SupplierCountryName__c`, 9 more) while the sheet's REAL formula
  (`TI_Logi_TINETAccount__r.CountryCode__c`, `PlaceOfDestination1__r.CountryList__r.CountryCode__c`, …) sat
  in a column we weren't reading. `attr_drift.py` reported `in-sync: 133 drifted: 0` — falsely clean.
- **Command:** `fetch_sheet.py` (all objects) → `generate_xml.py` → `attr_drift.py`.
- **Component:** `scripts/fetch_sheet.py` `build_col_map()`; `scripts/attr_drift.py` formula compare;
  `scripts/fill_formula_placeholder.py` column locator.
- **Category:** Tooling.
- **Root cause:** the client "Format" sheet has TWO polymorphic value columns and the machine API-header row
  is ambiguous/mislabeled: it stamps `defaultValue` on BOTH `数式(設定値)` (col H, the REAL value: formula /
  picklist / referenceTo / displayFormat) AND `デフォルト値` (col L, the real default), and stamps
  `displayFormat / referenceTo / formula / valueSet` on `データ型に応じて…` (col G) which in this sheet is only
  a human DESCRIPTION (often JP prose like `BPマスタ.国コード`). `build_col_map` matched the English header, so
  it mapped Type Specific Value ← G (description) and Default Value ← L (H's data was read then overwritten by
  L). Formula/picklist/referenceTo therefore came from the description column. Compounding it, `attr_drift`
  only checked formula PRESENCE (`exp_formula and not ohf`), never the formula BODY, so a dummy `"TBD"` vs the
  real formula was invisible → false 0-drift.
- **Fix applied:** (1) `fetch_sheet.build_col_map()` now disambiguates by the UNIQUE Japanese header
  substrings — `設定値` → Type Specific Value (col H), `デフォルト` → Default Value (col L) — and no longer reads
  the `displayFormat/…` description column (col G); no fall-back to G when H is blank (blank H → surfaced as a
  blocker), per the sheet owner (2026-09-09). Caller passes the JP header row (`grid[header_idx-1]`).
  (2) `attr_drift.py` now compares normalized formula BODIES when both sides are formulas
  (`formula body differs (org=…)`), catching dummy-vs-real. (3) `fill_formula_placeholder.py` locates the
  value column by the `設定値` JP substring (falls back to the old `displayFormat` header only for legacy tabs).
- **Impact:** re-fetch + re-drift EIRI → 10 Formula Text fields flagged (9 real cross-object formulas to
  deploy in place; `TI_Fnt_LoadingPlaceCountryCode__c` still a client placeholder `”TBD”` with full-width
  quotes → park). Receiving / ReceivingDetail / DeliveryDestination analyses MUST be re-run with the corrected
  reader (their prior numbers were computed from col G and are suspect).
- **Prevention added:** header-driven column mapping is now JP-substring disambiguated (reshuffle-proof) and
  documented in `build_col_map`; `attr_drift` formula-body compare closes the presence-only gap.
- **Status:** Monitoring (EIRI formula redeploy + other-object re-analysis pending).

### [2026-09-09] EIRI picklists shipped broken: newline-delimited values collapsed into one value
- **Error signature:** drift showed 14 EIRI picklists as "values differ (N sheet / 1 org)"; org held ONE
  value literally equal to the whole cell, e.g. `必要\n不要`, `OCEAN B/L\nSURRENDERD\nWAYBILL\nCargo Receipt\nその他`.
  A naive re-deploy then failed check-only with `Duplicate label: Required` (unrestricted picklists MERGE
  new values with the old junk value → label collision).
- **Command:** original EIRI field deploy (`mdapi_deploy.py`, force-app picklists) + attempted redeploy
  `.build/eiri_pfix`.
- **Component:** `scripts/generate_xml.py` `_build_picklist_valueset()` (value split + `<restricted>` placement).
- **Category:** Schema.
- **Root cause:** (1) picklist entries were split on `;` ONLY (`norm_picklist.split(";")`), so newline-
  delimited cells (the client uses `\n`) became a single giant value. (2) Separately, `<restricted>` was
  written under `<valueSetDefinition>` instead of `<valueSet>` → `Element restricted invalid at this
  location in type ValueSetValuesDefinition` when trying a restricted-replace workaround.
- **Fix applied:** `generate_xml.py`: split entries on `[;\n]` for both values and default-values; moved
  `<restricted>` to be a child of `<valueSet>` (before `<valueSetDefinition>`). Because unrestricted picklists
  MERGE (junk value cannot be removed in place — restricted-replace also merged), the 14 broken fields were
  DELETE+RECREATED (EIRI has 0 records → safe): strip page → destructive delete (purgeOnDelete) → recreate
  with correct split values → restore page → re-grant FLS (14/14). Post-fix drift = 0/133.
- **Prevention added:** `attr_drift.py` now maps `boolean`->Checkbox and normalizes full-width `：/；` before
  splitting picklist label:api, so it stops false-flagging Checkbox and colon-labeled picklists; the corrected
  `[;\n]` split in `generate_xml.py` prevents the collapse at the source. (Follow-up TODO: add a
  validate_sheet.py guard that flags a picklist whose parsed value count == 1 while the cell contains a
  newline, catching this pre-deploy.)
- **Status:** Resolved

### [2026-09-09] Attribute-drift gate silently skipped on the SOAP (`mdapi_deploy.py`) path
- **Error signature:** No error was thrown — the MISS was a *silent skip*. `TI_Fnt_Receiving__c`
  (36) + `TI_Fnt_ReceivingDetail__c` (19) = **55 existing fields drifted** in DEFINITION
  (type/formula/referenceTo/picklist/precision/scale/length) between the sheet and the org, and the
  deploy was closed out as "done" without any of it being surfaced. Found only when the user asked
  why drift wasn't checked.
- **Command:** manual `python scripts/mdapi_deploy.py --package .build/newpkg/package.xml --file …`
  (used because the `sf` CLI is auth-locked in this sandbox, so `prep_deploy.py` could not run).
- **Component:** `scripts/mdapi_deploy.py` (had no drift gate); `scripts/attr_drift.py` (only ever
  invoked from `prep_deploy.py` step 6b).
- **Category:** Tooling / Order-of-Execution (mandatory gate wired to only one deploy path).
- **Root cause:** `attr_drift.py` — the mandatory attribute-level drift check per
  `sf-deploy-delta-and-blockers.mdc` — was invoked from EXACTLY ONE place: `prep_deploy.py` build
  step 6b, which routes through `deploy.py` → the `sf` CLI. In this sandbox the CLI cannot rotate its
  auth file, so all real deploys go through the CLI-free SOAP path `mdapi_deploy.py`, which never
  called `attr_drift`. The name-based package only proves a field is being created; it cannot catch a
  field that already exists but whose definition changed. Net effect: on the SOAP path, drift was
  never computed at all.
- **Fix applied:** added a MANDATORY attribute-drift gate directly into `scripts/mdapi_deploy.py`
  (`_customfield_objects()` + `_drift_gate()`): before a real deploy, it extracts the distinct
  objects from every `CustomField` block in `package.xml`, runs `attr_drift.py` per object using the
  token file (`--token-file .build/orgauth.json`, no CLI), ignores genuinely-new absent fields, and
  **aborts the real deploy (exit 2) on any real drift** unless `--ack-drift` is passed. New flags:
  `--rows` (default `temp_updates.json`), `--ack-drift`, `--no-drift-check`. Non-field packages
  (FlexiPage/CustomTab/CustomObject-only) have no `CustomField` members → gate is a clean no-op.
  Verified: field package w/ drift + no ack → exit 2 (no SOAP sent); FlexiPage package → no-op;
  `--no-drift-check` → bypass.
- **Prevention added:** drift is now enforced on BOTH deploy paths — `prep_deploy.py` (CLI) step 6b
  AND `mdapi_deploy.py` (SOAP) `_drift_gate()`. A CustomField SOAP deploy can no longer proceed past
  unreviewed attribute drift. (Follow-up: surface the 55 drifted fields to the user for redeploy
  decisions — most are destructive type/formula changes needing delete+recreate.)
- **Status:** Resolved

### [2026-09-02] verify_deploy.py false-negative: hardcoded `DeveloperName LIKE 'TI_Fnt_%'` misses legacy-named fields
- **Error signature:** post-deploy `verify_deploy.py` reported `fields expected: 3 | present: 0 |
  missing: 3` for `TI_Fnt_RecevingAndQuoteAndWorkRelation__c`, even though a direct Tooling
  `CustomField` query confirmed all 3 fields live. Caused `prep_deploy --phase deploy` to abort with
  "VERIFICATION FAILED" on a deploy that had actually succeeded.
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --phase deploy --tabs "RecevingAndQuoteAndWorkRelation"`.
- **Component:** `scripts/verify_deploy.py` `org_fields()` Tooling query.
- **Category:** Tooling
- **Root cause:** `org_fields()` filtered `CustomField` with `AND DeveloperName LIKE 'TI_Fnt_%'`,
  assuming every custom field uses the `TI_Fnt_` prefix. This object's fields are legacy-named
  (`QuotationAndWork__c`, `Receiving__c`, `DeduplicateKey__c`) with NO prefix, so the query returned
  0 rows → false "missing" for fields that were present.
- **Fix applied:** removed the `LIKE 'TI_Fnt_%'` filter — `org_fields()` now fetches ALL custom
  fields for the object and the caller compares against the expected set derived from the generated
  package (which already scopes correctly regardless of naming). Re-ran → `[OK] present: 3 | missing: 0`.
- **Prevention added:** verification no longer assumes any naming convention; expected-vs-present is
  driven purely by generated metadata, so mixed/legacy field prefixes verify correctly. Applies to
  every future object whose fields don't use `TI_Fnt_`.
- **Status:** Resolved

### [2026-09-02] Field trackHistory=true fails when the object lacks enableHistory (+ manifest --only leaked FlexiPage/PermissionSet)
- **Error signature:** `CustomField <Obj>.<Field>: The entity: <Obj> does not have history
  tracking enabled` — 3 component failures on the check-only dry-run of the new object
  `TI_Fnt_RecevingAndQuoteAndWorkRelation__c` (all 3 fields carried `<trackHistory>true`).
- **Command:** `python scripts/prep_deploy.py --org ERPDEV01 --tabs "RecevingAndQuoteAndWorkRelation" --phase build`
  (→ `deploy.py … --dry-run`).
- **Component:** CustomObject `TI_Fnt_RecevingAndQuoteAndWorkRelation__c` + its 3 CustomFields
  (`QuotationAndWork__c`, `Receiving__c`, `DeduplicateKey__c`).
- **Category:** Dependency (Schema dependency: field history requires object history)
- **Root cause:** The sheet marks fields with 履歴管理 (col M = ○) → generator emits
  `<trackHistory>true</trackHistory>` on each field. But object-level `<enableHistory>` was driven
  ONLY by the object header's 項目履歴管理 flag (blank here), so the CustomObject was emitted
  WITHOUT `<enableHistory>true</enableHistory>`. Salesforce rejects field-level history unless the
  parent object enables history → all 3 fields failed. A second, latent issue surfaced in the same
  package: `build_manifest.py --only` filtered CustomObject/CustomField/Layout by object but added
  ALL FlexiPage + PermissionSet unconditionally, so every object deploy silently dragged in 6
  unrelated FlexiPages + the `SalesFrontAdmin` permission set.
- **Fix applied:** (1) `scripts/generate_xml.py` — compute `history_objects` (any field row with
  Track History truthy) parallel to `md_objects`, and force `enableHistory=true` on those objects'
  meta regardless of the header flag. (2) `scripts/build_manifest.py` — under `--only`, skip
  FlexiPage/PermissionSet entirely (they have no object prefix and are deployed by their own gated
  manifests). Re-ran build → package scoped to CustomObject 1 + CustomField 3; dry-run PASSED.
- **Prevention added:** generator now derives object history from its fields, so a history-tracked
  field can never be packaged without object history again. `--only` deploys are now object-scoped
  only (no FlexiPage/PermissionSet leakage). (Optional future validate_sheet guard: warn if a field
  sets Track History while the object header 項目履歴管理 is blank — informational, since the
  generator now self-corrects.)
- **Status:** Resolved

### [2026-08-28] Flipping required=true→false leaves fields INVISIBLE (implicit FLS lost, no explicit FLS)
- **Error signature:** no deploy error — a silent VISIBILITY gap. Fields deployed as
  `required=true` are implicitly visible and cannot carry `fieldPermissions`; when later
  flipped to `required=false` they lose that implicit visibility but have **no explicit FLS**,
  so users (via `SalesFrontAdmin`) can no longer see them.
- **Command:** post-flip of 91 fields on `TI_Fnt_IncidentalExpenses__c` (42),
  `TI_Fnt_StandaloneIncidentalExpenses__c` (41), `TI_Fnt_IncidentalExpensesDetail__c` (8).
- **Component:** PermissionSet `SalesFrontAdmin` fieldPermissions (91 fields missing).
- **Category:** Dependency (FLS lifecycle)
- **Root cause:** The original FLS grant skipped these fields precisely because they were
  `required=true` (required fields reject `fieldPermissions`). After the requiredness flip, the
  post-deploy FLS gate was not re-evaluated for the now-optional fields, so they had zero FLS.
- **Fix applied:** `scripts/_fix_fls_flipped.py` — computes the missing set LIVE per object
  (`all custom − existing FLS − Master-Detail`), upserts Read+Edit `fieldPermissions`, strips
  `<viewAllFields>` (invalid at v62), re-sorts children so fieldPermissions stay contiguous, and
  emits `manifest/fix_fls_flipped.xml`. Deployed → 91 grants; live FLS now 57/46/14.
- **Prevention added:** Whenever a field's `required` flips true→false (a redeploy of an existing
  field), the post-deploy FLS gate (`sf-post-deploy-fls-and-flexipage.mdc` Gate 1) MUST be re-run
  for those fields — they are newly FLS-eligible and start with none. Treat a requiredness flip as
  an FLS-affecting change, not just a schema tweak.
- **Status:** Resolved

### [2026-08-27] Lookup→MasterDetail conversion cannot be validated in check-only (dry-run)
- **Error signature:** `Test only deployment cannot update a field from a Lookup to MasterDetail`
- **Command:** `sf project deploy start --dry-run --manifest manifest/fix_batch1_package.xml`
  (bundling 91 `required` flips + 3 Lookup→MasterDetail conversions)
- **Component:** CustomField (3 fields): `TI_Fnt_DeliveryDestination__c.TI_Fnt_Receiving__c`,
  `TI_Fnt_DocumentDestination__c.TI_Fnt_Shipping__c`, `TI_Fnt_ShipoutMovein__c.TI_Fnt_Shipping__c`
- **Category:** Tooling
- **Root cause:** Salesforce forbids converting a Lookup to Master-Detail inside a **check-only /
  validateOnly** deployment. It is a real-deploy-only operation (and only succeeds when the child
  object has 0 records, or every existing child row already has the lookup populated). The dry-run
  therefore ALWAYS reports these 3 as failures even though the change is valid — the 91 non-conversion
  members validated fine (91/94).
- **Fix applied:** Split the batch — deploy the 91 `required=false` flips as their own dry-run-clean
  real deploy (`manifest/fix_reqflip.xml`); deploy the 3 conversions separately as a REAL deploy
  (`manifest/fix_conversions.xml`) with NO dry-run gate, after live-confirming each child object has
  0 records (`SELECT COUNT() FROM <obj>`). Never bundle a Lookup→MD conversion with fields you want
  dry-run-gated (a real-deploy rollback would take the good members with it).
- **Prevention added:** Deploy playbook rule — a Lookup→MasterDetail (or any relationship-type/
  referenceTo change) is check-only-incompatible: skip the dry-run for those members, verify child
  record count == 0 first, and isolate them in their own manifest so they can't roll back validated
  members.
- **Status:** Resolved

### [2026-08-27] fieldPermissions on a required (or Master-Detail) field → deploy rejected
- **Error signature:** `PermissionSet SalesFrontAdmin — You cannot deploy to a required field:
  TI_Fnt_IncidentalExpenses__c.TI_Fnt_ProductProduct__c`
- **Command:** `sf project deploy start --dry-run --manifest manifest/incexp_fls.xml` (adding FLS
  for the IncidentalExpenses family to SalesFrontAdmin)
- **Component:** PermissionSet SalesFrontAdmin (fieldPermissions)
- **Category:** Schema
- **Root cause:** Universally-required fields (`<required>true</required>`) and Master-Detail
  fields are ALWAYS visible/editable and cannot carry `fieldPermissions`. The FLS builder was
  emitting a fieldPermissions entry for every custom `__c` field, including required ones, so the
  deploy was rejected.
- **Fix applied:** When building fieldPermissions from retrieved field metadata, classify each
  field and SKIP it entirely if `type == MasterDetail` or `required == true`; emit read-only
  (`editable=false, readable=true`) for formula / Summary / AutoNumber; editable otherwise. These
  skipped fields need no FLS — they are always visible. (Also keep the contiguous-grouping fix
  from the earlier lesson.)
- **Prevention added:** Standard FLS-builder rule = "skip Master-Detail + required fields, RO for
  formula/summary/autonumber, else editable". Applies to every permission-set FLS pass.
- **Status:** Resolved

### [2026-08-27] PermissionSet fieldPermissions non-contiguous → "Element ... is duplicated"
- **Error signature:** `PermissionSet SalesFrontAdmin — Error parsing file: Element
  fieldPermissions is duplicated at this location in type PermissionSet (3621:17)`
- **Command:** `deploy.py --start --package manifest/new5_fls.xml` (adding FLS for the 5 new
  objects' 66 fields to SalesFrontAdmin)
- **Component:** PermissionSet SalesFrontAdmin (.permissionset-meta.xml)
- **Category:** Schema
- **Root cause:** New `<fieldPermissions>` elements were appended at the END of the root via
  `ET.SubElement(root, …)`, i.e. AFTER other element types (objectPermissions, etc.) that
  already followed the existing fieldPermissions block. The Metadata API requires all elements
  of the same type to be CONTIGUOUS; a second, separated fieldPermissions group is rejected as
  "duplicated at this location".
- **Fix applied:** When editing a PermissionSet/Profile XML, do NOT append same-type elements at
  the end. Regroup root children by tag (stable, preserving first-appearance tag order and
  within-tag order) and merge new `<fieldPermissions>` INTO the existing fieldPermissions group
  (sorted by `<field>` for a clean diff), so every element type stays contiguous. Verified with a
  contiguity assertion before deploy.
- **Prevention added:** Standard pattern for all future permission-set/profile edits = "regroup by
  tag + merge into the matching group + assert contiguity", never a bare append. (No
  validate_sheet.py guard applies — this is a permission-set XML-shape issue, not a sheet-row
  issue.)
- **Status:** Resolved

### [2026-08-26] referenceTo points at a non-existent object (X__c vs XMaster__c shorthand)
- **Error signature:** dry-run `CustomField …TI_Fnt_Condition__c: referenceTo value of
  'TI_Fnt_PaymentTerms__c' does not resolve to a valid sObject type` (same class earlier
  for `TI_Fnt_BusinessPartner__c`)
- **Command:** `sf project deploy start --manifest … (dry-run)` for Shipping/Receiving fields
- **Component:** CustomField Lookups whose `Type Specific Value` (referenceTo) was the
  shorthand `TI_Fnt_BusinessPartner__c` / `TI_Fnt_PaymentTerms__c` while the org only has
  the `…Master__c` twin (`TI_Fnt_BusinessPartnerMaster__c`, `TI_Fnt_PaymentTermsMaster__c`).
- **Category:** Dependency
- **Root cause:** `validate_sheet.py` only checked referenceTo OFFLINE (non-empty, valid
  API-name pattern, in-deploy-set hint) and emitted a mere INFO for a custom referenceTo
  not in the batch. It never verified the parent EXISTS in the target org, so a referenceTo
  to a non-existent object passed validation and only blew up at the deploy dry-run.
- **Fix applied:** Added an optional LIVE org-existence check to `validate_sheet.py`:
  `--target-org <org>` collects every distinct Lookup/MasterDetail custom referenceTo,
  queries `EntityDefinition` once (case-insensitive), and raises `dependency.ref.org` ERROR
  for any referenceTo that is neither created in this deploy nor present in the org — with a
  `did you mean 'X Master__c'?` hint when the Master twin exists. Wired into
  `prep_deploy.py` step [3/6] so every batch validates referenceTo against the org.
- **Prevention added:** `validate_sheet.py` `dependency.ref.org` guard (live) + prep_deploy
  now always passes `--target-org`. A missing-parent referenceTo is now a pre-deploy ERROR,
  not a dry-run surprise.
- **Status:** Resolved

### [2026-08-26] Case-insensitive API-name collision missed by case-sensitive delta
- **Error signature:** `CustomField TI_Fnt_Shipping__c.TI_Fnt_NoTifyParty__c: Not in
  package.xml` + `returned from org, but not found in the local project`
- **Command:** `sf project deploy start --manifest manifest/ship_recv_bp.xml … (dry-run)`
- **Component:** CustomField `TI_Fnt_Shipping__c.TI_Fnt_NotifyParty__c` (new) vs
  existing org field `TI_Fnt_NoTifyParty__c` (differs only by the case of one letter)
- **Category:** Schema / Tooling
- **Root cause:** The delta (sheet fields − org fields) was computed with a
  CASE-SENSITIVE set difference. Salesforce API names are CASE-INSENSITIVE for
  uniqueness, so a newly-curated `TI_Fnt_NotifyParty__c` was treated as "new" even
  though the org already had `TI_Fnt_NoTifyParty__c` (an earlier odd glossary casing
  "NoTifyParty"). The deploy then tried to create a case-variant duplicate and the
  Metadata API rejected it with the confusing "Not in package.xml" message.
- **Fix applied:** Recompute the field delta case-insensitively — exclude any sheet
  field whose `.lower()` matches an existing org field's `.lower()`; report such
  case-variants as "already in org (SKIP)" rather than deploying them. NotifyParty
  was dropped from the delta; the org keeps its existing `TI_Fnt_NoTifyParty__c`.
- **Prevention added:** Delta computation for existing objects MUST compare field
  API names case-insensitively (both the Tooling-API delta step in
  `sf-deploy-delta-and-blockers.mdc` and any ad-hoc delta script). A case-variant
  match = field already exists → SKIP, never redeploy/rename.
- **Status:** Resolved

### [2026-08-25] META: self-correction loop silently skipped for a whole deploy series
- **Error signature:** No KB entries between 2026-08-10 and 2026-08-25 despite
  multiple real failures during the Aug-20 Deal field + FlexiPage + FLS work.
- **Command:** various (manual `sf project deploy start … --ignore-conflicts`,
  hand-run dry-runs, `sf data query`) — mostly NOT via `scripts/deploy.py`.
- **Component:** process (the self-correction loop itself).
- **Category:** Tooling
- **Root cause:** Two compounding gaps. (1) `sf-deploy-self-correction.mdc` was
  `alwaysApply: false`, so it was not loaded into context during those sessions and
  the loop was invisible. (2) Even when `deploy.py` ran, it only *printed* a
  reminder on failure ("→ Run the SELF-CORRECTION loop…") — advisory text with no
  enforcement — and many failures came from MANUAL `sf` calls that bypassed
  `deploy.py` entirely, so no `last_deploy_failure.json` artifact was even produced.
- **Fix applied:** (a) `sf-deploy-self-correction.mdc` → `alwaysApply: true`.
  (b) New `scripts/log_failure.py` auto-appends a DRAFT KB entry from
  `.build/last_deploy_failure.json`; `deploy.py` now calls it automatically on any
  non-zero exit (KB update can no longer be forgotten). (c) Rule now MANDATES all
  org-touching deploys go through `deploy.py`/`prep_deploy.py` so the artifact +
  auto-log always fire.
- **Prevention added:** Automated KB draft-append on failure (no human step),
  always-applied rule, and a quality gate that refuses to claim deploy success while
  a failure in this run has no KB entry.
- **Status:** Resolved

### [2026-08-20] 28× "Must specify: relationshipName" on Deal lookups/MD
- **Error signature:** `Must specify a non-null value for: relationshipName` ×28 on
  `TI_Fnt_Deal__c` Lookup/MasterDetail fields during field dry-run.
- **Command:** `sf project deploy start … --dry-run` (Deal fields)
- **Component:** CustomField (Lookup/MasterDetail) on `TI_Fnt_Deal__c`
- **Category:** Schema
- **Root cause:** `generate_xml.py` deliberately omits `<relationshipName>` when the
  sheet cell is blank (to avoid collisions), but Salesforce REQUIRES it on every
  Lookup/MasterDetail. 28 Deal relationship rows had a blank relationshipName.
- **Fix applied:** Derived `relationshipName = <FieldAPI minus __c>` into the
  `temp_updates.json` build artifact (not the sheet) for the 28 rows.
- **Prevention added:** `validate_sheet.py` now flags a blank `Relationship Name`
  on Lookup rows as an ERROR (was optional) so it is caught pre-deploy, matching the
  MasterDetail rule.
- **Status:** Resolved

### [2026-08-20] "40 custom relationships max" exceeded on Deal
- **Error signature:** `reached maximum number of custom fields: Each object can have
  no more than 40 custom relationships` on `TI_Fnt_Deal__c`.
- **Command:** `sf project deploy start … --dry-run` (Deal fields, 43 relationships)
- **Component:** CustomField (Lookup/MasterDetail) on `TI_Fnt_Deal__c`
- **Category:** Schema/Limit
- **Root cause:** The Deal package tried to create 43 relationship fields; the org
  hard-caps custom relationships at 40 per object. No pre-deploy counter existed.
- **Fix applied:** Parked 3 relationship fields locally (ShippingDestination,
  SoldToParty, Supplier) to land 40; sheet left untouched per user instruction.
- **Prevention added:** `validate_sheet.py` now counts Lookup+MasterDetail rows per
  object and ERRORs when the deploy set would push an object past 40 relationships.
- **Status:** Resolved

### [2026-08-20] FlexiPage column body 119 > max 100
- **Error signature:** `Component [flexipage:column] attribute [body]: count [119]
  exceeded max [100]` deploying `Shipping_Record_Page`.
- **Command:** FlexiPage dry-run (`gen_record_page.py` output)
- **Component:** FlexiPage `Shipping_Record_Page` (基本情報 section)
- **Category:** Tooling/Limit
- **Root cause:** A single `flexipage:column` body may hold at most 100 items; the
  基本情報 section had 119 fields in one column.
- **Fix applied:** `gen_record_page.py` (and `gen_tabbed_page.py`) now compute the
  number of columns per section dynamically (`ceil(fields/100)`) and split fields
  evenly so no column exceeds 100 items.
- **Prevention added:** Page generators cap-split columns automatically; a section
  can never emit a >100-item column again.
- **Status:** Resolved

### [2026-08-20] Source-tracking conflicts blocked field/FlexiPage deploys
- **Error signature:** `sf project deploy start` refused with conflicts (local vs org
  out of sync) when re-deploying existing fields / FlexiPages.
- **Command:** `sf project deploy start …` (Deal fields, Shipping/Receiving pages)
- **Component:** CustomField / FlexiPage
- **Category:** Tooling
- **Root cause:** Source tracking flagged already-present metadata as conflicting;
  `deploy.py` did not expose `--ignore-conflicts`, so commands were run by hand.
- **Fix applied:** Re-ran with `--ignore-conflicts` to force intended local state.
- **Prevention added:** Documented that intentional redeploys of known metadata use
  `--ignore-conflicts`; TODO to surface the flag through `deploy.py`.
- **Status:** Monitoring

### [2026-08-20] Google ADC failure — HOME clobbered by sf shim
- **Error signature:** `google.auth.exceptions.DefaultCredentialsError: Your default
  credentials were not found.` (Sheets API) and later `sf data query failed: unknown`.
- **Command:** `prep_deploy.py` / `fetch_sheet.py` after `sf`-oriented env was set.
- **Component:** environment (`HOME`, `XDG_DATA_HOME`, `CLOUDSDK_CONFIG`).
- **Category:** Environment/Org
- **Root cause:** `HOME` was pointed at `$PWD/.sfhome` for `sf` CLI, which hid the
  gcloud ADC from `google.auth.default`; conversely a stale `.sfhome` `HOME` broke
  `sf` path computation.
- **Fix applied:** For Google calls, `unset CLOUDSDK_CONFIG GOOGLE_APPLICATION_CREDENTIALS
  XDG_DATA_HOME` and `export HOME=/Users/abhi.chauhan`; for `sf` calls set the
  `.sfhome`/XDG pair. Never share one env for both.
- **Prevention added:** Documented the two distinct env profiles (Google-ADC vs
  sf-shim); `prep_deploy.py` manages them per phase.
- **Status:** Monitoring

### [2026-08-10] "Succeeded" deploy that never ran — wrong flag + stale log
- **Error signature:** deploy reported `Status: Succeeded / 13/13` yet the object
  did not exist in the org (`EntityDefinition` totalSize 0).
- **Command:** `python scripts/deploy.py … --run` (intended real deploy)
- **Component:** CustomObject/CustomField — `Sales_IncidentalExpensesDetail__c`,
  `Sales_StandaloneIncidentalExpenses__c`
- **Category:** Tooling
- **Root cause:** `deploy.py`'s real-deploy flag is `--start`; `--run` does not
  exist, so argparse exited (exit 2) WITHOUT deploying. The success signal was a
  `grep` of `.build/last_deploy.log`, which still held the previous CHECK-ONLY
  (`--dry-run`) run's `Status: Succeeded`. Redirecting deploy output to
  `/dev/null` hid the argparse error.
- **Fix applied:** Re-deployed with the correct `python scripts/deploy.py --start …`.
  Added rule `.cursor/rules/sf-object-existence-precheck.mdc` and helper
  `scripts/verify_deploy.py`.
- **Prevention added:** MANDATORY pre-deploy object-existence check + MANDATORY
  automated live post-deploy verification (`verify_deploy.py`, exit-code gated).
  Never infer real-deploy success from `last_deploy.log`; confirm the log's first
  line is `deploy start` WITHOUT `--dry-run`, check the exit code, and verify live.
- **Status:** Resolved

### [2026-08-10] False "field missing" — FLS-gated verification query
- **Error signature:** `verify_deploy.py` reported 3/12 fields missing
  (`ConditionRate`, `FixedDate`, `IsDeleted`); `SELECT <field>` → "No such column";
  `sf sobject describe` → 9 fields. Tooling `CustomField` → all 12 present (real Ids).
- **Command:** `sf data query "SELECT … FROM FieldDefinition …"` / `sobject describe`
- **Component:** CustomField — 3 fields on `Sales_IncidentalExpensesDetail__c`
- **Category:** Environment/Org (Tooling)
- **Root cause:** `FieldDefinition`, `sObject describe`, and plain SOQL are all
  Field-Level-Security gated. The 3 fields deployed fine but their FLS was not
  auto-granted to the running admin, so they were invisible to those APIs →
  false "missing". The Tooling `CustomField` object is metadata-level and
  FLS-independent, and showed all 12.
- **Fix applied:** `verify_deploy.py::org_fields` now queries Tooling
  `CustomField` (`--use-tooling-api`) and re-adds the `__c` suffix, instead of
  FieldDefinition.
- **Prevention added:** Rule documents that existence checks MUST use the Tooling
  API, never FLS-gated FieldDefinition/describe/SOQL. (Note: if those fields must
  be user-visible, grant FLS separately via a permission set — a deploy-success
  ≠ FLS-visible.)
- **Status:** Resolved

### [2026-08-10] MasterDetail child object rejected sharingModel=ReadWrite
- **Error signature:** `Cannot set sharingModel to ReadWrite on a CustomObject with a MasterDetail relationship field`
- **Command:** `sf project deploy start --manifest manifest/package.xml --dry-run` (check-only against ERPDEV01)
- **Component:** CustomObject — `Sales_DealDetail__c` (has MasterDetail field `TI_Fnt_Deal__c` → `Sales_Deal__c`)
- **Category:** Schema
- **Root cause:** `generate_xml.py::write_object_meta` emitted `<sharingModel>ReadWrite</sharingModel>`
  unconditionally. A detail (child) object in a Master-Detail relationship inherits sharing
  from its parent, so Salesforce forces `ControlledByParent`; ReadWrite is rejected.
- **Fix applied:** `generate_xml.py` — `write_object_meta` now takes `has_master_detail`;
  `process_fields` pre-scans field rows (via `get_sf_field_type`) to find objects owning a
  MasterDetail field and emits `<sharingModel>ControlledByParent</sharingModel>` for them
  (ReadWrite otherwise).
- **Prevention added:** sharingModel is derived from field composition, so any object with a
  MasterDetail field automatically deploys as ControlledByParent — no manual step.
- **Status:** Resolved

### [2026-08-10] Standing default: raw-object prep playbook (no re-ask)
- **Error signature:** Fresh object tab fails validation en masse — `field.api` (blank
  API names), `required.column` (empty formula body / MasterDetail rel name),
  `dependency.ref` (placeholder `referenceTo`), `object.api` (missing `__c`).
  Seen on `成約明細:Sales_DealDetail` (68 ERRORs) — same shape as `Sales_Deal` when raw.
- **Command:** `python scripts/run.py --tabs "<JP:Object>" --only <Object__c>`
- **Component:** Schema/Dependency — whole object tab, unprepped.
- **Category:** Order-of-Execution
- **Root cause:** A "raw" object tab has not been through naming/formula/relationship
  prep, so the deployment gate blocks it. Previously required a per-object strategy
  decision; the user standardized the approach on 2026-08-10.
- **Fix applied:** Documented the fixed prep cycle in `sf-sheet-deployment.mdc`
  ("Raw / unprepped object tab — standard prep playbook (DEFAULT)"): normalize object
  API → name blank fields (`TI_Fnt_`+glossary, `__c`, ≤40, unique) → fill formula
  placeholders by return type → wire relationships (`referenceTo`, Relationship Name)
  → resolve/park lookups → re-validate → deploy (full package if object is new) →
  append report tab.
- **Prevention added:** Apply this playbook automatically for any raw object tab
  without re-asking; every sheet write stays gated; highlight Column A only.
- **Status:** Resolved (standing convention)

### [2026-08-10] Standing default: cross-object lookup resolution (check org, else WIP)
- **Error signature:** `dependency.ref` placeholders like
  `referenceTo '<API名未定(GDC付与)>：数量単位マスタ'`; and at deploy time
  `Entity '<Object>' not found` when a lookup target object is absent from the org.
- **Command:** `python scripts/validate_sheet.py` / `sf project deploy start --dry-run`
- **Component:** Dependency — Lookup/MasterDetail to custom master objects
  (e.g. 成約→`Sales_Deal__c`, 原産国→国マスタ, 保管場所→保管場所マスタ, 数量単位マスタ).
- **Category:** Dependency
- **Root cause:** A lookup can only deploy if its `referenceTo` object already exists
  in the target org. Some masters aren't deployed yet, and some `referenceTo` values
  are still GDC placeholders.
- **Fix applied:** Documented decision in `sf-sheet-deployment.mdc` ("Cross-object
  lookup resolution (DEFAULT)"): resolve the placeholder to a real API, then check the
  target org — if the object exists, keep the lookup; if missing/placeholder, mark the
  row WIP (col AB=`X`) and park it for a later deploy.
- **Prevention added:** Never deploy a lookup whose target object is absent; park as
  WIP (lime-yellow, Column A only) and revisit once the master is deployed.
- **Status:** Resolved (standing convention)

### [2026-08-07] Restrict deleteConstraint rejected on Lookup to User
- **Error signature:** `Cannot add a lookup relationship child with cascade or restrict options to User`
- **Command:** `sf project deploy start --manifest ... --dry-run` (check-only against devpro3)
- **Component:** CustomField — `TI_Fnt_SalesRep__c`, `TI_Fnt_SalesDeliveryPerson__c` (both referenceTo `User`)
- **Category:** Schema
- **Root cause:** The previous fix added `<deleteConstraint>Restrict</deleteConstraint>` to all
  required Lookups. But `User` (and some other standard objects: Group, RecordType, Profile)
  reject Restrict/Cascade child lookups — only `SetNull` is allowed. `SetNull` cannot coexist
  with `required`, so a required Lookup to User is impossible at the DB level.
- **Fix applied:** `generate_xml.py` — for a required Lookup whose referenceTo is in
  `RESTRICT_INCOMPATIBLE_REFS = {User, Group, RecordType, Profile}`, emit
  `<deleteConstraint>SetNull</deleteConstraint>` and DROP `<required>` (degrade to optional).
- **Prevention added:** `validate_sheet.py` INFO now distinguishes required Lookups to these
  standard objects and documents the SetNull/optional degrade.
- **Status:** Resolved

### [2026-08-07] Required Lookup missing deleteConstraint; required LongTextArea
- **Error signature:**
  - `field integrity exception: unknown (must specify either cascade delete or restrict delete for required lookup foreign key)`
  - `Can not specify 'required' for a CustomField of type LongTextArea`
- **Command:** `sf project deploy start --manifest ... --dry-run` (check-only against devpro3)
- **Component:** CustomField — 11 required Lookups + 1 LongTextArea on `Sales_Deal__c`
  (e.g. `TI_Fnt_Incoterms__c`, `TI_Fnt_SalesRep__c`, `TI_Fnt_PackingStyleAfterPacking__c`)
- **Category:** Schema
- **Root cause:** `generate_xml.py` emitted `<required>true</required>` uniformly for
  any non-formula / non-Checkbox / non-AutoNumber / non-Summary field. But
  (a) a *required* Lookup must also declare a `<deleteConstraint>` (SetNull is invalid
  when required), and (b) LongTextArea / Html / Location / MultiselectPicklist / MasterDetail
  cannot be `required` at the schema level.
- **Fix applied:** `generate_xml.py` — required Lookup now emits
  `<deleteConstraint>Restrict</deleteConstraint>`; the `non_settable` set for `required`
  now also excludes LongTextArea, Html, Location, MultiselectPicklist, MasterDetail.
- **Prevention added:** `validate_sheet.py` emits INFO when `Required`=true on a type that
  can't be required (generator drops it) or on a Lookup (generator adds Restrict).
- **Status:** Resolved

### [2026-08-07] Check-only used `deploy validate`, rejected NoTestRun
- **Error signature:** `Expected --test-level=NoTestRun to be one of: RunAllTestsInOrg, RunLocalTests, RunSpecifiedTests, RunRelevantTests`
- **Command:** `sf project deploy validate --manifest ... --test-level NoTestRun`
- **Component:** Tooling — `scripts/deploy.py`
- **Category:** Tooling
- **Root cause:** The check-only path used `sf project deploy validate`, which is
  meant for production quick-deploys and mandates a real Apex test level; it
  rejects `NoTestRun`. Metadata-only (fields) changes to a sandbox do not need
  Apex tests, so `NoTestRun` is legitimate.
- **Fix applied:** `deploy.py` now always uses `sf project deploy start` and adds
  `--dry-run` for check-only. `deploy start --dry-run` validates without writing
  and accepts `NoTestRun`.
- **Prevention added:** Check-only and real deploy share one command builder;
  only the `--dry-run` flag differs, so test-level handling stays consistent.
- **Status:** Resolved

### [2026-08-07] Generator crashed on Python 3.9 (PEP 604 unions)
- **Error signature:** `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'` at `def build_field_xml(row: dict) -> ET.Element | None`
- **Command:** `python scripts/generate_xml.py`
- **Component:** Tooling — `scripts/generate_xml.py`
- **Category:** Tooling
- **Root cause:** The generators use `X | None` return annotations (Python 3.10+),
  but the local interpreter is 3.9.6, which evaluates annotations at runtime.
- **Fix applied:** Prepended `from __future__ import annotations` to all copied
  `generate_*.py` so annotations are lazy strings — no behavior change.
- **Prevention added:** Keep the future-import at the top of every generator;
  `scripts/run.py` invokes them via the same interpreter used for the pipeline.
- **Status:** Resolved

### [2026-08-07] Custom object API name missing `__c` suffix
- **Error signature:** validation `object.api` — `Object API 'Sales_ReceivingDetail' invalid (must ... end __c)`
- **Command:** `python scripts/validate_sheet.py`
- **Component:** Schema — object metadata from オブジェクト一覧 index tab
- **Category:** Schema
- **Root cause:** The object-index tab stored the API name without the `__c`
  suffix required for custom objects; would deploy to the wrong/invalid name.
- **Fix applied:** N/A (data fix in sheet).
- **Prevention added:** `validate_sheet.py` errors when an object API name does
  not match the API-name regex AND end with `__c`.
- **Status:** Resolved

### [2026-08-07] Placeholder text in referenceTo (Lookup/MasterDetail)
- **Error signature:** validation `dependency.ref` — `referenceTo '要確認' is not a valid API name`
- **Command:** `python scripts/validate_sheet.py`
- **Component:** Dependency — relationship fields
- **Category:** Dependency
- **Root cause:** Draft sheet rows carried Japanese placeholder `要確認`
  ("to be confirmed") in Column G instead of a real target object API name;
  Salesforce would reject the relationship at deploy time.
- **Fix applied:** N/A (data fix in sheet).
- **Prevention added:** `validate_sheet.py` requires Column G on Lookup/MasterDetail
  to be a valid API name, and flags a custom-object target not present in the
  deploy set (INFO) so missing parents are noticed before deploy.
- **Status:** Resolved

### [2026-08-10] Checkbox with empty Default Value blocked validation
- **Error signature:** validation `required.column` — `Checkbox: required column 'Default Value' is empty`
- **Command:** `python scripts/validate_sheet.py` (Sales_Shipping, 17 checkboxes)
- **Component:** Schema — Checkbox fields with blank Default Value
- **Category:** Schema
- **Root cause:** The validator marked `Default Value` as REQUIRED for Checkbox,
  but `generate_xml.py` already emits `<defaultValue>false</defaultValue>` when the
  cell is blank, so the metadata is always valid. The hard ERROR was over-strict.
- **Fix applied:** `validate_sheet.py` — Checkbox `Default Value` moved from
  `required` to `optional`; an empty value now emits INFO ("generator defaults to
  false") instead of ERROR. A present non-TRUE/FALSE value still errors.
- **Prevention added:** Checkbox default is only validated for format, not presence,
  keeping it in sync with the generator's safe default.
- **Status:** Resolved

### [2026-08-10] Child relationship name collision on a shared master object
- **Error signature:** deploy — `There is already a Child Relationship named
  TI_Fnt_Incoterms on インコタームズマスタ`
- **Command:** `sf project deploy start --manifest ... --dry-run` (ERPDEV01)
- **Component:** CustomField — `Sales_Shipping__c.TI_Fnt_Incoterms__c` (Lookup to
  `TI_Logi_IncotermsMaster__c`)
- **Category:** Dependency
- **Root cause:** `fill_relationship_name.py` derives `relationshipName` as the field
  API minus `__c` (e.g. `TI_Fnt_Incoterms`). When two different child objects both
  have an `Incoterms` lookup to the SAME master (Sales_Deal already deployed
  `TI_Fnt_Incoterms`), the relationshipName collides — child relationship names must
  be unique per parent object.
- **Fix applied:** Set a unique `relationshipName` in the sheet (Column X) for the
  colliding field, qualified by the child object token
  (`TI_Fnt_Incoterms` -> `TI_Fnt_ShippingIncoterms`), then regenerate.
- **Prevention added:** When a lookup targets a master already referenced by an
  earlier-deployed object with the same base relationshipName, qualify the child's
  relationshipName with the object token before deploy.
- **Status:** Resolved

### [2026-08-10] Metadata-only dry-run stalled 7+ min on RunLocalTests
- **Error signature:** dry-run hangs at `⣷ Running Tests` after
  `Components: 179/179 (100%)`
- **Command:** `sf project deploy start --manifest ... --dry-run --test-level RunLocalTests`
- **Component:** Tooling — `scripts/deploy.py` default test level
- **Category:** Tooling
- **Root cause:** ERPDEV01 has a large/slow Apex test suite. For metadata-only
  deploys (custom object + fields, no Apex) `RunLocalTests` runs every local test
  needlessly, taking many minutes and risking failure on unrelated tests.
- **Fix applied:** Use `--test-level NoTestRun` for metadata-only object/field
  deploys and dry-runs (validated 179/179 in ~40s).
- **Prevention added:** Prefer `NoTestRun` for pure schema deploys to sandboxes;
  reserve `RunLocalTests` for changes that touch Apex.
- **Status:** Resolved

### [2026-08-10] Placeholder referenceTo `<API名未定(GDC付与)>：<label>`
- **Error signature:** validation `dependency.ref` — referenceTo not a valid API name
  (11 lookups on `Sales_IncidentalExpenses`)
- **Command:** `python scripts/validate_sheet.py`
- **Component:** Lookup fields whose Column G holds a GDC placeholder instead of a
  real target object API name.
- **Category:** Dependency
- **Root cause:** The sheet author left the lookup target as a placeholder
  (`<API名未定(GDC付与)>：成約`); the label after `：` names the intended object.
- **Fix applied:** Resolve each placeholder by label → real API
  (成約→`Sales_Deal__c`, 輸入・入庫管理→`Sales_Receiving__c`, 輸出・出庫管理→
  `Sales_Shipping__c`, 国マスタ→`CountryList__c`, …), write the API into Column G
  for targets that EXIST in the org; WIP-park lookups whose master is missing.
- **Prevention added:** Standard cross-object flow — map placeholder label→API,
  check org, fill referenceTo (exists) or WIP-park (missing).
- **Status:** Resolved

### [2026-08-10] Number/Currency Precision > 18 rejected
- **Error signature:** validation `constraint` — `'Precision'=20 out of range [1, 18]`
- **Command:** `python scripts/validate_sheet.py` (`TI_Fnt_IVAmount__c`)
- **Component:** Number/Currency field with precision beyond Salesforce max.
- **Category:** Schema
- **Root cause:** Sheet specified precision 20; Salesforce caps total digits at 18.
- **Fix applied:** Set Column T (precision) to 18 for the field.
- **Prevention added:** Cap precision at 18 for Number/Currency; flag any sheet
  value above it for review before deploy.
- **Status:** Resolved

### [2026-08-10] Spurious errors on standard system rows (no `__c`)
- **Error signature:** `required.column` / `forbidden.column` errors on `CreatedDate`,
  `LastModifiedDate`, `CreatedById`, `OwnerId` (rows the sheet lists as system fields)
- **Command:** `python scripts/validate_sheet.py`
- **Component:** Tooling — `scripts/validate_sheet.py`
- **Category:** Tooling
- **Root cause:** These rows have no `__c` suffix, so `generate_xml.py` never deploys
  them, yet the validator still enforced field-definition columns on them, producing
  false blockers.
- **Fix applied:** `validate_sheet.py` now emits only the standard-field WARN and
  skips all further field-def checks for any field whose API name lacks `__c`.
- **Prevention added:** Standard/system rows can never block a deploy.
- **Status:** Resolved

### [2026-08-10] Recurring child-relationship collisions on shared masters
- **Error signature:** `There is already a Child Relationship named <X> on <master>`
  (Incoterms, Currency, Deal, OriginCountry across objects)
- **Command:** `sf project deploy start --dry-run` (ERPDEV01)
- **Component:** Lookup relationshipName derived as field-API-minus-`__c`.
- **Category:** Dependency
- **Root cause:** Multiple objects lookup the SAME shared master (CurrencyMaster,
  IncotermsMaster, CountryList, Sales_Deal…) with the same base relationshipName.
- **Fix applied:** Proactively qualify the relationshipName with the child object
  token when the master is already referenced by an earlier-deployed object
  (e.g. `TI_Fnt_Incoterms`→`TI_Fnt_ReceivingIncoterms`,
  `TI_Fnt_Currency`→`TI_Fnt_IncidentalCurrency`).
- **Prevention added:** For shared masters, object-qualify relationshipNames before
  the first dry-run to avoid the collision loop.
- **Status:** Resolved
