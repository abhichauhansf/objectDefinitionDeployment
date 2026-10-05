#!/usr/bin/env python3
"""
deploy.py — Safe local deploy orchestrator (wraps the Salesforce `sf` CLI).

This is the ONLY script that talks to a live org. It enforces a hard
validation gate before any deploy and defaults to check-only validation.

Safety model:
  * Default action is `validate` (check-only, `sf project deploy validate`) —
    it NEVER writes to the org.
  * `--start` explicitly performs a real deploy (`sf project deploy start`).
  * A deploy is refused unless the validation report shows zero ERRORs, unless
    `--skip-validation-gate` is explicitly passed.

Usage:
  # check-only (safe, default)
  python scripts/deploy.py --package manifest/package.xml --target-org "ERP DEV 02"

  # real deploy
  python scripts/deploy.py --start --package manifest/package.xml \
      --target-org "ERP DEV 02" --test-level RunLocalTests

  # delete-only deploy
  python scripts/deploy.py --start --pre-destructive manifest/destructiveChanges.xml \
      --package manifest/package.xml --target-org "ERP DEV 02"
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

VALID_TEST_LEVELS = {"NoTestRun", "RunSpecifiedTests", "RunLocalTests", "RunAllTestsInOrg"}


def sf_available() -> str | None:
    return shutil.which("sf") or shutil.which("sfdx")


def list_connected_orgs() -> list[dict]:
    """Return connected (authenticated) orgs from `sf org list --json`.

    Each item: {alias, username, isDefault, status, instanceUrl}.
    """
    try:
        out = subprocess.run(["sf", "org", "list", "--json"],
                             capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout or "{}")
    except Exception as e:
        print(f"⚠️  could not list orgs: {e}")
        return []

    result = data.get("result", {}) or {}
    raw: list[dict] = []
    for bucket in ("nonScratchOrgs", "scratchOrgs", "devHubs", "sandboxes", "other"):
        raw.extend(result.get(bucket, []) or [])

    orgs: dict[str, dict] = {}
    for o in raw:
        username = o.get("username", "")
        if not username:
            continue
        status = str(o.get("connectedStatus") or o.get("status") or "").strip()
        # keep only usable (connected) orgs; drop expired/errored auths
        if status and status.lower() not in {"connected", "active"}:
            continue
        orgs[username] = {
            "alias": o.get("alias") or "",
            "username": username,
            "isDefault": bool(o.get("isDefaultUsername")),
            "status": status or "Connected",
            "instanceUrl": o.get("instanceUrl", ""),
        }
    # stable order: default first, then alias/username
    return sorted(orgs.values(),
                  key=lambda x: (not x["isDefault"], x["alias"] or x["username"]))


def print_org_table(orgs: list[dict]) -> None:
    print("=" * 72)
    print("  CONNECTED SALESFORCE ORGS")
    print("=" * 72)
    if not orgs:
        print("  (none) — connect one:  sf org login web --alias \"<ORG>\"")
        print("=" * 72)
        return
    for i, o in enumerate(orgs, 1):
        star = " *default" if o["isDefault"] else ""
        alias = o["alias"] or "(no alias)"
        print(f"  [{i}] {alias:24} {o['username']:34} {o['status']}{star}")
        if o["instanceUrl"]:
            print(f"      {o['instanceUrl']}")
    print("=" * 72)


def resolve_target_org(explicit: str, orgs: list[dict]) -> str | None:
    """Resolve to a connected org. Requires an explicit selection — NEVER
    silently falls back to the configured default (deploy quality gate)."""
    if not explicit:
        return None
    # accept selection by index, alias, or username
    if explicit.isdigit():
        idx = int(explicit)
        if 1 <= idx <= len(orgs):
            return orgs[idx - 1]["alias"] or orgs[idx - 1]["username"]
        return None
    for o in orgs:
        if explicit in (o["alias"], o["username"]):
            return o["alias"] or o["username"]
    # allow a not-yet-listed alias/username to pass through (user knows best),
    # but warn — it still must authenticate at deploy time.
    return explicit


def validation_has_errors(report_path: Path) -> bool | None:
    """True/False if report exists; None if no report (unknown)."""
    if not report_path.exists():
        return None
    try:
        data = json.loads(report_path.read_text())
        return int(data.get("counts", {}).get("ERROR", 0)) > 0
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Deploy generated metadata via sf CLI")
    ap.add_argument("--package", default="manifest/package.xml")
    ap.add_argument("--pre-destructive", default="", help="destructiveChanges.xml (pre-deploy delete)")
    ap.add_argument("--post-destructive", default="", help="destructiveChanges.xml (post-deploy delete)")
    ap.add_argument("--target-org", default="",
                    help="REQUIRED for deploy: org alias, username, or list index. "
                         "No silent default — you must pick from the connected orgs.")
    ap.add_argument("--list-orgs", action="store_true",
                    help="print connected orgs and exit (org-selection gate)")
    ap.add_argument("--test-level", default="RunLocalTests", choices=sorted(VALID_TEST_LEVELS))
    ap.add_argument("--tests", default="", help="comma-separated tests for RunSpecifiedTests")
    ap.add_argument("--start", action="store_true", help="REAL deploy (gated). Default is check-only validate.")
    ap.add_argument("--validation-report", default=".build/validation_report.json")
    ap.add_argument("--skip-validation-gate", action="store_true")
    ap.add_argument("--wait", type=int, default=60)
    ap.add_argument("--ignore-conflicts", action="store_true",
                    help="pass --ignore-conflicts to sf (force local source over "
                         "source-tracking conflicts). Use only for intentional redeploys.")
    ap.add_argument("--allow-permset-shrink", action="store_true",
                    help="allow a PermissionSet deploy whose local file holds fewer "
                         "field/object/tab permissions than the org (full replace removes them)")
    ap.add_argument("--dry-run", action="store_true", help="print the sf command, do not run")
    args = ap.parse_args()

    cli = sf_available()
    if not cli:
        print("❌ Salesforce CLI not found. Install with: npm i -g @salesforce/cli")
        return 1

    # ---- org-selection gate: ALWAYS show connected orgs -------------------- #
    orgs = list_connected_orgs()
    if args.list_orgs:
        print_org_table(orgs)
        return 0

    pkg = Path(args.package)
    if not pkg.exists():
        print(f"❌ package manifest '{pkg}' not found — run build_manifest.py first.")
        return 1

    org = resolve_target_org(args.target_org, orgs)
    if not org:
        print("⛔ DEPLOY BLOCKED — no target org selected.")
        print("   Every deployment must target an explicitly-chosen connected org.\n")
        print_org_table(orgs)
        print("Re-run with your choice (index, alias, or username), e.g.:")
        print("   python scripts/deploy.py --target-org 1            # by list index")
        print("   python scripts/deploy.py --target-org \"ERP DEV 02\"  # by alias")
        if not orgs:
            print("\nNo orgs connected yet:  sf org login web --alias \"<ORG>\"")
        return 2

    # confirm the resolved org is actually a connected one (warn if unknown)
    known = {o["alias"] for o in orgs} | {o["username"] for o in orgs}
    if orgs and org not in known:
        print(f"⚠️  '{org}' is not in the connected-orgs list — it must authenticate at deploy time.")

    # ---- validation gate --------------------------------------------------- #
    if not args.skip_validation_gate:
        has_err = validation_has_errors(Path(args.validation_report))
        if has_err is True:
            print(f"⛔ Validation report '{args.validation_report}' has ERRORS — deploy blocked.")
            print("   Fix the sheet and re-run:  python scripts/validate_sheet.py --json .build/validation_report.json")
            return 2
        if has_err is None:
            print(f"⚠️  No validation report at '{args.validation_report}'.")
            print("   Run validate_sheet.py first, or pass --skip-validation-gate to override.")
            return 2

    # ---- permission-set shrink gate ---------------------------------------- #
    # A PermissionSet metadata deploy REPLACES the whole set in the org: any
    # field/object/tab permission missing from the local file is REMOVED.
    if not args.allow_permset_shrink:
        shrunk = _permset_shrink_check(pkg, org)
        if shrunk:
            print("⛔ DEPLOY BLOCKED — local PermissionSet file(s) would REMOVE permissions from the org:")
            for line in shrunk:
                print(f"   {line}")
            print("   Retrieve the permission set from the org first and edit that copy,")
            print("   or grant additively via the data API (grant_fls.py / grant_object_perms.py /")
            print("   grant_tab_visibility.py). Pass --allow-permset-shrink only for an intended removal.")
            return 2

    # Check-only uses `deploy start --dry-run` (validates, writes nothing,
    # accepts NoTestRun on sandboxes). `deploy validate` is reserved for
    # production quick-deploys and rejects NoTestRun, so we do not use it here.
    cmd = ["sf", "project", "deploy", "start",
           "--manifest", str(pkg),
           "--target-org", org,
           "--test-level", args.test_level,
           "--wait", str(args.wait)]
    if not args.start:
        cmd.append("--dry-run")
    if args.test_level == "RunSpecifiedTests":
        if not args.tests:
            print("❌ --test-level RunSpecifiedTests requires --tests")
            return 1
        for t in args.tests.split(","):
            cmd += ["--tests", t.strip()]
    if args.pre_destructive:
        cmd += ["--pre-destructive-changes", args.pre_destructive]
    if args.post_destructive:
        cmd += ["--post-destructive-changes", args.post_destructive]
    if args.ignore_conflicts:
        cmd.append("--ignore-conflicts")

    banner = "REAL DEPLOY (writes to org)" if args.start else "CHECK-ONLY VALIDATION (no org writes)"
    print("=" * 72)
    print(f"  {banner}")
    print(f"  org        : {org}")
    print(f"  manifest   : {pkg}")
    print(f"  test level : {args.test_level}")
    print(f"  command    : {' '.join(cmd)}")
    print("=" * 72)

    if args.dry_run:
        print("(dry-run) not executing.")
        return 0

    logpath = Path(".build/last_deploy.log")
    rc = _run_tee(cmd, logpath)

    mode_label = "start" if args.start else "validate (dry-run)"
    if rc != 0 and _is_nothing_to_deploy(logpath):
        # Source tracking reports NothingToDeploy when the manifest members
        # already match the org. That is the desired end state, not a failure.
        print("ℹ️  NothingToDeploy — local source already matches the org; treating as success.")
        return 0
    if rc != 0 and _is_transport_drop(logpath):
        status = _server_side_status(org)
        print(f"ℹ️  CLI lost its connection while polling; server-side deploy status = {status!r}.")
        with logpath.open("a", encoding="utf-8") as lf:
            lf.write(f"# server-side status after transport drop: {status}\n")
        if status == "Succeeded":
            print("   The deploy landed in the org; treating as success (live verification still required).")
            return 0
    if rc != 0:
        _write_failure_context(logpath, cmd, org, mode_label, rc)
        _auto_log_failure()
        print("\n" + "─" * 72)
        print(f"❌ Deploy {mode_label} FAILED (exit {rc}).")
        print(f"   Full log : {logpath}")
        print(f"   Context  : .build/last_deploy_failure.json")
        print("   → A DRAFT lesson was auto-written to .cursor/deployment_knowledge.md.")
        print("     Run the SELF-CORRECTION loop: diagnose the root cause, fix the")
        print("     generator/validation, COMPLETE the draft, and flip its Status")
        print("     (see rule sf-deploy-self-correction).")
    return rc


def _auto_log_failure() -> None:
    """Auto-append a DRAFT self-correction lesson so the KB update is never
    forgotten. Never raises into the caller (logging must not mask the failure)."""
    try:
        script = Path(__file__).with_name("log_failure.py")
        subprocess.run([sys.executable, str(script),
                        "--context", ".build/last_deploy_failure.json"],
                       timeout=30)
    except Exception as e:
        print(f"⚠️  auto-log of failure lesson skipped: {e}")


def _is_nothing_to_deploy(logpath: Path) -> bool:
    """True when sf exited non-zero only because source tracking has no diffs.

    `NothingToDeploy` / `No local changes to deploy` means the manifest members
    already match the org — the desired state, not a metadata failure.
    """
    try:
        text = logpath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return ("NothingToDeploy" in text) or ("No local changes to deploy" in text)


def _is_transport_drop(logpath: Path) -> bool:
    """True when sf failed on a network/transport error rather than a metadata error.

    `MetadataTransferError: ... fetch failed` is raised by the CLI's status poll;
    the deploy job itself keeps running server-side and may have succeeded.
    """
    try:
        text = logpath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return False
    return "MetadataTransferError" in text and "fetch failed" in text


def _server_side_status(org: str, attempts: int = 20, delay: float = 15.0) -> str:
    """Poll `sf project deploy report --use-most-recent` until the job is terminal."""
    import time
    terminal = {"Succeeded", "SucceededPartial", "Failed", "Canceled"}
    status = "Unknown"
    for _ in range(attempts):
        try:
            out = subprocess.run(
                ["sf", "project", "deploy", "report", "--use-most-recent",
                 "--target-org", org, "--json"],
                capture_output=True, text=True, timeout=120)
            status = (json.loads(out.stdout or "{}").get("result") or {}).get("status") or "Unknown"
        except Exception:
            status = "Unknown"
        if status in terminal:
            return status
        time.sleep(delay)
    return status


def _permset_members(pkg: Path) -> list[str]:
    import xml.etree.ElementTree as ET
    ns = "{http://soap.sforce.com/2006/04/metadata}"
    try:
        root = ET.parse(pkg).getroot()
    except Exception:
        return []
    names: list[str] = []
    for t in root.findall(f"{ns}types"):
        if (t.findtext(f"{ns}name") or "").strip() == "PermissionSet":
            names += [(m.text or "").strip() for m in t.findall(f"{ns}members")]
    return [n for n in names if n and n != "*"]


def _org_count(org: str, soql: str, tooling: bool = False) -> int | None:
    cmd = ["sf", "data", "query", "--target-org", org, "--json", "--query", soql]
    if tooling:
        cmd.insert(3, "--use-tooling-api")
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        return int((json.loads(out.stdout or "{}").get("result") or {}).get("totalSize"))
    except Exception:
        return None


def _permset_shrink_check(pkg: Path, org: str) -> list[str]:
    """Return one line per PermissionSet whose local file has fewer entries than the org."""
    problems: list[str] = []
    for name in _permset_members(pkg):
        local = Path(f"force-app/main/default/permissionsets/{name}.permissionset-meta.xml")
        text = local.read_text(encoding="utf-8") if local.exists() else ""
        ps = _org_count(org, f"SELECT Id FROM PermissionSet WHERE Name = '{name}'")
        if not ps:
            continue
        checks = [
            ("fieldPermissions", text.count("<fieldPermissions>"),
             _org_count(org, f"SELECT Id FROM FieldPermissions WHERE Parent.Name = '{name}'")),
            ("objectPermissions", text.count("<objectPermissions>"),
             _org_count(org, f"SELECT Id FROM ObjectPermissions WHERE Parent.Name = '{name}'")),
            ("tabSettings", text.count("<tabSettings>"),
             _org_count(org, f"SELECT Id FROM PermissionSetTabSetting WHERE Parent.Name = '{name}'",
                        tooling=True)),
        ]
        for kind, n_local, n_org in checks:
            if n_org is None:
                problems.append(f"{name}: could not count org {kind} — refusing to guess")
            elif n_local < n_org:
                problems.append(f"{name}: {kind} local {n_local} < org {n_org}")
    return problems


def _run_tee(cmd: list[str], logpath: Path) -> int:
    """Run a command, streaming output live to console AND a log file."""
    logpath.parent.mkdir(parents=True, exist_ok=True)
    try:
        with logpath.open("w", encoding="utf-8") as lf:
            lf.write(f"# command: {' '.join(cmd)}\n")
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, bufsize=1)
            assert proc.stdout is not None
            for line in proc.stdout:
                sys.stdout.write(line)
                lf.write(line)
            proc.wait()
            return proc.returncode
    except KeyboardInterrupt:
        print("\n⏹️  aborted.")
        return 130


def _write_failure_context(logpath: Path, cmd: list[str], org: str,
                           mode: str, rc: int) -> None:
    """Persist a small structured record + extracted component failures for RCA."""
    import datetime
    import re

    text = logpath.read_text(encoding="utf-8") if logpath.exists() else ""
    # Pull component-failure lines from the sf human/table output (best-effort).
    failures = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("error", "fail", "invalid", "missing", "not found",
                                  "insufficient", "duplicate", "cannot")):
            failures.append(line.strip())
    err_codes = sorted(set(re.findall(r"\b[A-Z][A-Z_]{4,}\b", text)))
    ctx = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "target_org": org,
        "returncode": rc,
        "command": cmd,
        "log_file": str(logpath),
        "extracted_failures": failures[:50],
        "possible_error_codes": err_codes[:30],
    }
    Path(".build/last_deploy_failure.json").write_text(
        json.dumps(ctx, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
