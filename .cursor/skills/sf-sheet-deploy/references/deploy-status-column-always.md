# Always Mark the Deployment-Status Column (AI) by Operation — MANDATORY

The **Deployment Status** column (**AI**, header `Deployment Status`, fallback
index 34) must ALWAYS reflect the last org operation performed on each field row.
Updating it is a **must-do** step of every operation — never optional, never
deferred. A create/update/delete is NOT "done" until the row's AI cell matches
the org, verified LIVE.

Layers on [sheet-columns.md](sheet-columns.md),
[post-deploy-fls-and-flexipage.md](post-deploy-fls-and-flexipage.md) (GATE 0),
and [isdelete-column-always.md](isdelete-column-always.md).

## The operation → status mapping (always)

Locate AI by HEADER NAME. After the org write returns, re-check the field LIVE
(Tooling `CustomField` — FLS-independent, never `sobject describe`) and write:

- **`Deployed`** — the row's field was CREATED or UPDATED and its API name now
  EXISTS in the target org.
- **`Deleted`** — the row was processed as a DELETION (IsDelete=TRUE per
  [isdelete-column-always.md](isdelete-column-always.md)) and the field is now
  confirmed ABSENT from the org after the destructive deploy.
- **`Not Deployed`** — a deployable custom (`__c`) field whose API name is still
  NOT in the org (e.g. parked, blocked, or a create that did not land).
- **`Standard`** — a standard field (no `__c`, e.g. `OwnerId`, `Name`,
  `CreatedDate`), always present, not tool-deployed.

WIP rows (AE = TRUE/x) are skipped; blank-API/noise rows are skipped.

## When this fires (every time)

1. **After any create/update deploy** — refresh AI for every deployed object's
   rows (this is GATE 0 of [post-deploy-fls-and-flexipage.md](post-deploy-fls-and-flexipage.md)).
   Deployed fields → `Deployed`.
2. **After any destructive delete** — for EACH row that was deleted, set AI to
   `Deleted` (verify the field is live-absent first). This is the case that was
   previously unhandled: `write_deploy_status.py` only wrote
   Deployed/Not Deployed/Standard, so deletes left AI stale. Deletes MUST now
   flip AI to `Deleted`.
3. **Whenever the user asks for a field-level status snapshot.**

## Guardrails

- **Derived LIVE, per operation.** Never infer AI from a deploy log or a cached
  snapshot — re-check the org (Tooling API) at write time. For a delete, confirm
  the field is actually gone before writing `Deleted`.
- **Gated sheet write.** Writing AI is gated by
  [sheet-write-confirmation.md](sheet-write-confirmation.md):
  preview the `cell: old → new` diff and get confirmation before `--apply`. The
  helper is `scripts/write_deploy_status.py` (extended to emit `Deleted` for rows
  confirmed removed / flagged IsDelete and absent from the org).
- **No operation is complete without its AI update.** Do not report a
  create/update as done without AI=`Deployed`, and do not report a delete as done
  without AI=`Deleted`. Pair the AI update with the AH note (`[FIX]`/deletion
  note) so WHAT and WHY are both recorded
  ([ah-comment-reevaluate.md](ah-comment-reevaluate.md)).
- Header-driven, live reads only; WIP wins (skip). This complements — does not
  replace — the AH review-comment writeback.
