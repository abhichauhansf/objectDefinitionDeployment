#!/usr/bin/env python3
"""Google credentials for every sheet script, with automatic re-login.

All Sheets/Drive clients in sf-deploy are built through this module:

    from google_auth import get_credentials, sheets_service
    svc = sheets_service(write=True)

Credential order:
  1. GOOGLE_SERVICE_ACCOUNT_JSON (inline JSON, a path, or base64) — no login.
  2. Application Default Credentials (~/.config/gcloud/application_default_credentials.json).

ADC is checked before use. If it is missing, expired (RefreshError /
"Reauthentication is needed"), or was created by
`gcloud auth application-default login` (Google's ADC client needs a quota
project for the Sheets API and fails with 403 SERVICE_DISABLED), the module
runs the login that works without a quota project:

    gcloud auth login <account> --enable-gdrive-access --update-adc --force

This opens a browser once; the script then continues. The old ADC file is
backed up next to it first.

CLI:
    python scripts/google_auth.py            # check, re-login only if needed
    python scripts/google_auth.py --login    # force a fresh login
    python scripts/google_auth.py --check    # report only, never log in

Set SF_DEPLOY_NO_AUTO_LOGIN=1 to fail instead of opening a browser.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

READ_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]
WRITE_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

ADC_PATH = Path.home() / ".config" / "gcloud" / "application_default_credentials.json"
# gcloud CLI's own OAuth client: Sheets works without a quota project.
GCLOUD_CLI_CLIENT_ID = "32555940559.apps.googleusercontent.com"
LOGIN_TIMEOUT_S = 300


class GoogleAuthError(RuntimeError):
    pass


def find_gcloud() -> str | None:
    for cand in (
        shutil.which("gcloud"),
        str(Path.home() / "google-cloud-sdk" / "bin" / "gcloud"),
        "/opt/homebrew/bin/gcloud",
        "/usr/local/bin/gcloud",
        "/opt/homebrew/share/google-cloud-sdk/bin/gcloud",
    ):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return cand
    return None


def _gcloud_account(gcloud: str) -> str:
    acct = os.environ.get("GOOGLE_ACCOUNT", "").strip()
    if acct:
        return acct
    try:
        out = subprocess.run(
            [gcloud, "config", "get-value", "account"],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return "" if out in ("", "(unset)") else out
    except Exception:
        return ""


def _backup_adc() -> None:
    if ADC_PATH.exists():
        stamp = time.strftime("%Y%m%d_%H%M%S")
        shutil.copy2(ADC_PATH, ADC_PATH.with_name(f"{ADC_PATH.name}.bak_{stamp}"))


def login(reason: str = "") -> None:
    """Run the browser login that refreshes ADC with the gcloud CLI client."""
    if os.environ.get("SF_DEPLOY_NO_AUTO_LOGIN") == "1":
        raise GoogleAuthError(
            f"Google login needed ({reason}) and SF_DEPLOY_NO_AUTO_LOGIN=1. "
            "Run: python scripts/google_auth.py --login")
    gcloud = find_gcloud()
    if not gcloud:
        raise GoogleAuthError(
            "Google login needed but gcloud was not found. Install the Google "
            "Cloud SDK (expected at ~/google-cloud-sdk/bin/gcloud).")
    cmd = [gcloud, "auth", "login"]
    acct = _gcloud_account(gcloud)
    if acct:
        cmd.append(acct)
    cmd += ["--enable-gdrive-access", "--update-adc", "--force"]
    print(f"[google_auth] {reason or 'login requested'} — opening browser for "
          f"Google sign-in{f' as {acct}' if acct else ''} ...", file=sys.stderr)
    try:
        _backup_adc()
        res = subprocess.run(cmd, timeout=LOGIN_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise GoogleAuthError("Google sign-in was not completed within 5 minutes.")
    except OSError as e:
        raise GoogleAuthError(
            f"Could not run gcloud login ({e}). If this runs in a sandbox, re-run "
            "outside it: python scripts/google_auth.py --login")
    if res.returncode != 0:
        raise GoogleAuthError(
            f"gcloud auth login exited {res.returncode}. If this runs in a "
            "sandbox, re-run outside it: python scripts/google_auth.py --login")
    print("[google_auth] login complete; ADC updated.", file=sys.stderr)


def _service_account_creds(scopes):
    raw = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    from google.oauth2 import service_account

    if raw.startswith("{"):
        info = json.loads(raw)
    elif raw.startswith("/") or raw.lower().endswith(".json"):
        with open(raw) as fh:
            info = json.load(fh)
    else:
        info = json.loads(base64.b64decode(raw).decode())
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def _adc_problem(scopes):
    """Return (creds, None) if ADC is usable, else (None, reason)."""
    import google.auth
    from google.auth.exceptions import DefaultCredentialsError, RefreshError
    from google.auth.transport.requests import Request

    try:
        creds, _ = google.auth.default(scopes=scopes)
    except DefaultCredentialsError:
        return None, "no Google credentials found"
    client_id = getattr(creds, "client_id", None)
    if (client_id and client_id != GCLOUD_CLI_CLIENT_ID
            and not getattr(creds, "quota_project_id", None)):
        return None, "ADC uses a client that needs a quota project for Sheets"
    try:
        creds.refresh(Request())
    except RefreshError as e:
        return None, f"Google login expired ({str(e)[:80]})"
    return creds, None


def get_credentials(scopes=None, *, auto_login: bool = True):
    scopes = scopes or READ_SCOPES
    sa = _service_account_creds(scopes)
    if sa is not None:
        return sa
    creds, problem = _adc_problem(scopes)
    if creds is not None:
        return creds
    if not auto_login:
        raise GoogleAuthError(problem)
    login(problem)
    creds, problem = _adc_problem(scopes)
    if creds is None:
        raise GoogleAuthError(f"Still not authorised after login: {problem}")
    return creds


def sheets_service(write: bool = False, scopes=None):
    from googleapiclient.discovery import build

    creds = get_credentials(scopes or (WRITE_SCOPES if write else READ_SCOPES))
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def drive_service(scopes=None):
    from googleapiclient.discovery import build

    creds = get_credentials(scopes or ["https://www.googleapis.com/auth/drive.readonly"])
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--login", action="store_true", help="force a fresh browser login")
    g.add_argument("--check", action="store_true", help="report only, never log in")
    args = ap.parse_args()
    try:
        if args.login:
            login("forced by --login")
        if args.check:
            _, problem = _adc_problem(READ_SCOPES)
            if problem:
                print(f"NOT OK: {problem}")
                return 1
        else:
            get_credentials(READ_SCOPES)
            get_credentials(WRITE_SCOPES)
    except GoogleAuthError as e:
        print(f"NOT OK: {e}")
        return 1
    print("OK: Google credentials are valid for Sheets read and write.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
