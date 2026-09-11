#!/usr/bin/env python3
"""
log_failure.py — Automated self-correction memory writer.

Turns the machine-readable failure context that `deploy.py` writes on any
non-zero deploy/validate (`.build/last_deploy_failure.json`) into a DRAFT entry
appended to `.cursor/deployment_knowledge.md` (newest on top). This removes the
one manual step that was silently skipped for a whole deploy series (see the
2026-08-25 META lesson): the KB update now fires automatically and can no longer
be forgotten.

Design:
  * Idempotent — a stable signature (org + mode + top failure lines) is hashed;
    if an entry with the same signature already exists in the KB, nothing is
    appended (re-running the same failed deploy will not duplicate entries).
  * Draft, not final — the entry is written with Status: DRAFT and TODO markers
    for Root cause / Fix / Prevention. The agent (per sf-deploy-self-correction)
    must complete it and flip Status to Resolved/Monitoring. A DRAFT entry is the
    quality gate's signal that a failure still needs a real diagnosis.
  * Never raises into the caller — logging must not mask the original failure.

Usage:
  python scripts/log_failure.py                       # reads default paths
  python scripts/log_failure.py --context .build/last_deploy_failure.json
  python scripts/log_failure.py --check               # exit 1 if any DRAFT entry
                                                      # exists (quality gate)
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

DEFAULT_CONTEXT = ".build/last_deploy_failure.json"
KB_NAME = "deployment_knowledge.md"
LESSONS_ANCHOR = "## Lessons\n"
DRAFT_TAG = "Status: DRAFT"


def find_kb() -> Path | None:
    """Locate .cursor/deployment_knowledge.md walking up from cwd."""
    here = Path.cwd().resolve()
    for base in [here, *here.parents]:
        cand = base / ".cursor" / KB_NAME
        if cand.exists():
            return cand
    return None


def signature(ctx: dict) -> str:
    """Stable short hash of the failure identity (org+mode+top failure lines)."""
    key = "|".join([
        str(ctx.get("target_org", "")),
        str(ctx.get("mode", "")),
        "\n".join(ctx.get("extracted_failures", [])[:5]),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:10]


def build_entry(ctx: dict, sig: str) -> str:
    today = datetime.date.today().isoformat()
    org = ctx.get("target_org", "?")
    mode = ctx.get("mode", "?")
    rc = ctx.get("returncode", "?")
    cmd = " ".join(ctx.get("command", [])) if isinstance(ctx.get("command"), list) else str(ctx.get("command", ""))
    fails = ctx.get("extracted_failures", [])[:8]
    codes = ", ".join(ctx.get("possible_error_codes", [])[:12]) or "(none extracted)"
    fail_block = "\n".join(f"    {ln}" for ln in fails) or "    (none extracted)"
    return (
        f"### [{today}] AUTO-DRAFT: deploy {mode} failed (exit {rc})  «sig:{sig}»\n"
        f"- **Error signature:** {codes}\n"
        f"- **Command:** `{cmd}`\n"
        f"- **Component:** TODO — identify metadata type + API name from the log\n"
        f"- **Category:** TODO — Schema | Dependency | Order-of-Execution | Environment/Org | Tooling\n"
        f"- **Extracted failure lines:**\n{fail_block}\n"
        f"- **Root cause:** TODO — diagnose against Salesforce Metadata API rules\n"
        f"- **Fix applied:** TODO — generator/validation/manifest change\n"
        f"- **Prevention added:** TODO — validate_sheet.py guard / generator guard\n"
        f"- **{DRAFT_TAG}** (auto-written by log_failure.py; agent must complete + "
        f"flip to Resolved/Monitoring; org={org}, log={ctx.get('log_file','?')})\n"
    )


def append_entry(kb: Path, entry: str, sig: str) -> str:
    text = kb.read_text(encoding="utf-8")
    if f"«sig:{sig}»" in text:
        return "skip-duplicate"
    idx = text.find(LESSONS_ANCHOR)
    if idx == -1:
        # no anchor: append at end
        kb.write_text(text.rstrip() + "\n\n" + entry + "\n", encoding="utf-8")
        return "appended-end"
    insert_at = idx + len(LESSONS_ANCHOR)
    # keep one blank line after the anchor, then the new entry
    new_text = text[:insert_at] + "\n" + entry + "\n" + text[insert_at:].lstrip("\n")
    kb.write_text(new_text, encoding="utf-8")
    return "appended-top"


def count_drafts(kb: Path) -> int:
    return kb.read_text(encoding="utf-8").count(DRAFT_TAG)


def main() -> int:
    ap = argparse.ArgumentParser(description="Auto-append a draft KB lesson from a deploy failure")
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--check", action="store_true",
                    help="quality gate: exit 1 if any DRAFT lesson remains unresolved")
    args = ap.parse_args()

    kb = find_kb()
    if kb is None:
        print("log_failure: KB not found (.cursor/deployment_knowledge.md) — skipping.")
        return 0

    if args.check:
        n = count_drafts(kb)
        if n:
            print(f"⛔ QUALITY GATE: {n} AUTO-DRAFT lesson(s) still unresolved in {kb}.")
            print("   Complete each draft (root cause + fix + prevention) and flip "
                  "Status to Resolved/Monitoring before claiming deploy success.")
            return 1
        print("✓ quality gate clear: no unresolved DRAFT lessons.")
        return 0

    ctx_path = Path(args.context)
    if not ctx_path.exists():
        print(f"log_failure: no context at {ctx_path} — nothing to log.")
        return 0
    try:
        ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"log_failure: could not read context ({e}) — skipping.")
        return 0

    sig = signature(ctx)
    result = append_entry(kb, build_entry(ctx, sig), sig)
    if result == "skip-duplicate":
        print(f"log_failure: entry for sig:{sig} already present — not duplicated.")
    else:
        print(f"log_failure: {result} a DRAFT lesson (sig:{sig}) to {kb}.")
        print("   → Complete it per sf-deploy-self-correction and flip Status.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
