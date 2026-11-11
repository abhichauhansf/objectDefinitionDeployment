#!/usr/bin/env python3
"""
run_lock.py — one pipeline run per working folder.

prep_deploy.py and deploy.py share temp_updates.json and .build/* (plan,
validation report, deploy log) relative to the current directory. Two runs in
the same folder overwrite each other's files mid-build. The lock is
.build/run.lock in the current directory, so work/<name> copies have their own.

A child process started by the lock holder inherits SF_DEPLOY_RUN_LOCK and
passes straight through. A lock whose PID is no longer alive is taken over.
"""
from __future__ import annotations

import atexit
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ENV_KEY = "SF_DEPLOY_RUN_LOCK"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def acquire(name: str) -> None:
    """Take the folder lock or exit 3 with the holder's details."""
    lock = (Path.cwd() / ".build" / "run.lock").resolve()
    if os.environ.get(ENV_KEY) == str(lock):
        return
    lock.parent.mkdir(parents=True, exist_ok=True)
    info = {"pid": os.getpid(), "script": name,
            "started": datetime.now().isoformat(timespec="seconds"),
            "argv": sys.argv[1:]}
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                held = json.loads(lock.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                held = {}
            pid = int(held.get("pid") or 0)
            if pid and _alive(pid):
                print(f"⛔ another pipeline run is using this folder ({Path.cwd()}):\n"
                      f"   {held.get('script')} pid={pid} since {held.get('started')} "
                      f"args={' '.join(held.get('argv') or [])}\n"
                      f"   Wait for it to finish, or run from a separate work/<name> copy.")
                sys.exit(3)
            print(f"ℹ️  removing stale run lock (pid {pid or '?'} is not running)")
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(info, f)
        os.environ[ENV_KEY] = str(lock)
        atexit.register(_release, lock, os.getpid())
        return
    print(f"⛔ could not take run lock {lock}")
    sys.exit(3)


def _release(lock: Path, pid: int) -> None:
    try:
        if int(json.loads(lock.read_text(encoding="utf-8")).get("pid") or 0) == pid:
            lock.unlink()
    except (OSError, ValueError):
        pass
