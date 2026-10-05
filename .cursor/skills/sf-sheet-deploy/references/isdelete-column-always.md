# Always Consider the IsDelete Column (AD) — MANDATORY

The **IsDelete** flag (column **AD**, header `IsDelete`) is a first-class input to
EVERY field decision on an object tab. You MUST read it **live** (per
[read-sheet-live.md](read-sheet-live.md)) for every field row and factor it in
BEFORE you name, diff, drift-check, dedupe, package, or deploy anything. Missing
IsDelete is how a field that the client wants **deleted** gets silently
(re)created or treated as a duplicate-to-keep.

Locate AD by HEADER NAME, not fixed index. Layers on
[sheet-columns.md](sheet-columns.md),
[deploy-delta-and-blockers.md](deploy-delta-and-blockers.md), and
[blank-api-label-fallback.md](blank-api-label-fallback.md).

## Why this rule exists (real incident)

When resolving duplicate labels on `TI_Fnt_Receiving__c`, several "which of the
two fields do we keep?" questions were actually already answered by the sheet:
the client had set **`IsDelete=TRUE`** on one of each pair (e.g. `処理理由`,
`処理理由（自由入力欄）`, `別法人利用理由`, `別法人利用理由詳細`). The duplicate only
existed because the IsDelete flag on the retiring row had not been read. Reading
AD first collapses many "duplicate" and "ambiguous" cases to a clear keep-one +
delete-the-other.

## Required behavior (every object tab, every run)

1. **Read AD live per row.** Locate the column by HEADER NAME `IsDelete`
   (fallback index 29 / col AD only if the header is absent). Never evaluate it
   from a cached snapshot, a prior fetch, or memory.
2. **Interpret the value:** `TRUE`/`true` (and the sheet's checkbox-checked
   state) ⇒ the row is a **DELETION**. Blank/`FALSE` ⇒ normal create/update.
3. **Route deletions to the destructive DELETE set** (`build_destructive.py` →
   `destructiveChanges.xml`), handled by the destructive-deploy flow in
   [deploy-delta-and-blockers.md](deploy-delta-and-blockers.md) (STEP 2b). An
   IsDelete row is NEVER part of the normal create/update field package.
4. **Never create/update an IsDelete row** — regardless of:
   - a blank API name (do NOT AI-name it, do NOT label-fallback-reuse it into the
     create set),
   - a collision or duplicate label with another row (the IsDelete row is the one
     being retired — keep the non-IsDelete twin, delete the flagged one),
   - attribute drift (do not "fix" and redeploy a field that is flagged for
     deletion).
5. **WIP wins over IsDelete.** A row that is BOTH `WIP` (AE) and `IsDelete` (AD)
   is skipped entirely (per [sheet-columns.md](sheet-columns.md)) — neither
   created nor deleted.
6. **Use IsDelete to resolve duplicates/ambiguity FIRST.** Before asking the user
   (or the sheet owner) "which of these duplicate-label fields do we keep?",
   check whether one of them already carries `IsDelete=TRUE`. If so, that answers
   it: keep the unflagged one, delete the flagged one — no question needed.
7. **Report the IsDelete set explicitly** in every recon/delta summary: list the
   rows (No., label, API name) flagged for deletion, separately from create/update
   and skip sets, so the destructive action is visible and never silent.

After a confirmed destructive delete, set that row's Deployment Status to
`Deleted` ([deploy-status-column-always.md](deploy-status-column-always.md)) and
re-evaluate AH ([ah-comment-reevaluate.md](ah-comment-reevaluate.md)).

## Guardrails

- IsDelete is DESTRUCTIVE and irreversible (it also destroys the field's data);
  surface the delete set to the user and follow the destructive-deploy gates in
  [deploy-delta-and-blockers.md](deploy-delta-and-blockers.md) before executing.
  No password ([deploy-authorization.md](deploy-authorization.md)).
- All AD reads are LIVE and header-driven; never infer the flag from a stale file
  or a fixed column letter without confirming the header.
- This rule complements [blank-api-label-fallback.md](blank-api-label-fallback.md):
  a blank-API row that is ALSO IsDelete is a deletion, not a "new field" and not
  a label-reuse candidate — the IsDelete check runs before the label-fallback
  branch.
