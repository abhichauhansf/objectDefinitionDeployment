#!/usr/bin/env python3
"""
flag_blocker.py — ONE coupled entry point to flag a blocking field row.

A "blocker" (a field that cannot deploy / was left undeployed — bad/missing
referenceTo, empty required attr, dup API name, dry-run failure, unresolved
attribute drift, etc.) MUST be recorded in THREE places at once, and this script
does all three in a single call so none can be skipped:

  1. AH  — a `[VALIDATION <date>] <sev> <check>: <message>` note on the field row
           (via write_ai_comments.py --report).
  2. Col A — a pink highlight on that row's "No." cell only
           (via highlight_blockers.py --report).
  3. stdout — a plain-English report of what was flagged.

Both underlying writes are GATED sheet writes: this script is DRY-RUN by default
(previews both) and only writes when you pass --apply (after user confirmation).

Usage — single field:
  python scripts/flag_blocker.py --spreadsheet-id <ID> --tab Deal \
      --object TI_Fnt_Deal__c \
      --field TI_Fnt_Forwarder__c --check referenceTo \
      --message "type=Lookup but referenceTo unresolved (要確認) ..." [--apply]

Usage — many fields from a JSON file [{field, check, message, severity?}]:
  python scripts/flag_blocker.py --spreadsheet-id <ID> --tab Deal \
      --object TI_Fnt_Deal__c --blockers .build/blockers.json [--apply]
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPORT_PATH = Path(".build/flag_blocker_report.json")


def build_report(obj: str, blockers: list[dict]) -> dict:
    items = []
    for b in blockers:
        field = str(b.get("field") or "").strip()
        if not field:
            continue
        items.append({
            "object": obj,
            "field": field,
            "severity": (b.get("severity") or "ERROR").upper(),
            "check": b.get("check") or "blocker",
            "message": b.get("message") or "blocked (no deploy)",
        })
    return {"items": items}


def run(cmd: list[str]) -> int:
    print("\n$ " + " ".join(cmd))
    return subprocess.run(cmd, env=os.environ.copy(), text=True).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="Flag a blocking field row: AH note + Col A highlight + report (coupled).")
    ap.add_argument("--spreadsheet-id", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--object", required=True, help="object API name, e.g. TI_Fnt_Deal__c (for AH row matching)")
    ap.add_argument("--blockers", default="", help="JSON list [{field, check, message, severity?}]")
    ap.add_argument("--field", default="", help="single-field mode: field API name")
    ap.add_argument("--check", default="blocker", help="single-field mode: check name")
    ap.add_argument("--message", default="", help="single-field mode: blocker message")
    ap.add_argument("--severity", default="ERROR", help="ERROR (highlighted) or WARN (comment only)")
    ap.add_argument("--apply", action="store_true", help="actually write both (else dry-run preview of both)")
    args = ap.parse_args()

    if args.blockers:
        blockers = json.load(open(args.blockers, encoding="utf-8"))
    elif args.field:
        blockers = [{"field": args.field, "check": args.check,
                     "message": args.message, "severity": args.severity}]
    else:
        print("ERROR: pass either --blockers <json> or --field/--check/--message", file=sys.stderr)
        return 2

    report = build_report(args.object, blockers)
    if not report["items"]:
        print("nothing to flag (no valid field entries).")
        return 0

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 72)
    print(f"FLAG BLOCKER — {len(report['items'])} field(s) on tab '{args.tab}' ({args.object})")
    for it in report["items"]:
        print(f"  • {it['field']}  [{it['severity']}] {it['check']}: {it['message']}")
    print("=" * 72)
    print("  step 1/2 → AH comment (write_ai_comments.py)")
    print("  step 2/2 → Col A highlight (highlight_blockers.py, ERROR rows only)")

    apply = ["--apply"] if args.apply else []
    rc = 0
    # 1) AH validation comment
    rc |= run(["python3", str(HERE / "write_ai_comments.py"),
               "--spreadsheet-id", args.spreadsheet_id, "--tab", args.tab,
               "--report", str(REPORT_PATH), "--object", args.object, *apply])
    # 2) Col A highlight (only ERROR-severity rows are highlighted by the helper)
    rc |= run(["python3", str(HERE / "highlight_blockers.py"),
               "--spreadsheet-id", args.spreadsheet_id, "--tab", args.tab,
               "--report", str(REPORT_PATH), *apply])

    print("\n" + ("APPLIED both AH note + Col A highlight." if args.apply
                  else "DRY RUN — previewed both. Re-run with --apply (after confirmation)."))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
