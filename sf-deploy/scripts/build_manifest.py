#!/usr/bin/env python3
"""
build_manifest.py — Generate a Salesforce deploy manifest (package.xml) and,
optionally, a destructiveChanges.xml, from the local `force-app` source tree.

The legacy CI pipeline deployed via `--source-dir` file lists and never built
a manifest. This script produces a proper, dependency-ordered `package.xml`
so deployments are explicit, reviewable, and reproducible.

Metadata types discovered under force-app/main/default/:
  - CustomObject      objects/<Api>/<Api>.object-meta.xml         -> member <Api>
  - CustomField       objects/<Api>/fields/<Field>.field-meta.xml -> member <Api>.<Field>
  - CustomObjectTranslation  objectTranslations/<Obj>-<lang>/  -> member <Obj>-<lang>
  - Layout            layouts/<file>.layout-meta.xml              -> member <file>
  - FlexiPage         flexipages/<file>.flexipage-meta.xml        -> member <file>
  - PermissionSet     permissionsets/<file>.permissionset-meta.xml-> member <file>
  - RecordType        objects/<Api>/recordTypes/<RT>.recordType-meta.xml -> <Api>.<RT>

Usage:
  python scripts/build_manifest.py \
      [--source-root force-app/main/default] \
      [--out manifest/package.xml] \
      [--api-version 60.0] \                       # default: from sfdx-project.json
      [--only Sales_Deal__c,Other__c] \            # restrict to these objects
      [--destroy deletions.json --destroy-out manifest/destructiveChanges.xml]

`deletions.json` shape (for destructive changes):
  { "CustomField": ["Obj__c.Field__c"], "CustomObject": ["Old__c"] }
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote  # noqa: F401  (kept for parity with URL-ish inputs)
import xml.sax.saxutils as sx

# Ordered so parents deploy before children (objects before fields/record types,
# permissionsets/layouts/flexipages after the objects they reference).
TYPE_ORDER = [
    "CustomObject",
    "CustomField",
    "CustomObjectTranslation",
    "RecordType",
    "Layout",
    "FlexiPage",
    "PermissionSet",
]


def default_api_version(root: Path) -> str:
    proj = root / "sfdx-project.json"
    try:
        data = json.loads(proj.read_text())
        v = str(data.get("sourceApiVersion", "")).strip()
        if v:
            return v
    except Exception:
        pass
    return "60.0"


def discover(source_root: Path, only: set[str] | None) -> dict[str, list[str]]:
    members: dict[str, set[str]] = defaultdict(set)
    objects_dir = source_root / "objects"

    if objects_dir.is_dir():
        for obj_dir in sorted(p for p in objects_dir.iterdir() if p.is_dir()):
            obj = obj_dir.name
            if only and obj not in only:
                continue
            if (obj_dir / f"{obj}.object-meta.xml").exists():
                members["CustomObject"].add(obj)
            fields_dir = obj_dir / "fields"
            if fields_dir.is_dir():
                for f in sorted(fields_dir.glob("*.field-meta.xml")):
                    members["CustomField"].add(f"{obj}.{f.name[:-len('.field-meta.xml')]}")
            rt_dir = obj_dir / "recordTypes"
            if rt_dir.is_dir():
                for f in sorted(rt_dir.glob("*.recordType-meta.xml")):
                    members["RecordType"].add(f"{obj}.{f.name[:-len('.recordType-meta.xml')]}")

    # CustomObjectTranslation: folder objectTranslations/<Obj>-<lang>/
    ot_dir = source_root / "objectTranslations"
    if ot_dir.is_dir():
        for folder in sorted(p for p in ot_dir.iterdir() if p.is_dir()):
            member = folder.name  # e.g. TI_Fnt_Deal__c-en_US
            obj = member.rsplit("-", 1)[0]
            if only and obj not in only:
                continue
            meta = folder / f"{member}.objectTranslation-meta.xml"
            if meta.exists():
                members["CustomObjectTranslation"].add(member)

    simple = {
        "Layout": ("layouts", ".layout-meta.xml"),
        "FlexiPage": ("flexipages", ".flexipage-meta.xml"),
        "PermissionSet": ("permissionsets", ".permissionset-meta.xml"),
    }
    for mtype, (folder, suffix) in simple.items():
        d = source_root / folder
        if not d.is_dir():
            continue
        # When --only restricts the deploy to specific object(s), non-object
        # metadata must NOT ride along:
        #   * Layout members are "Object-Label" → filter by the object prefix.
        #   * FlexiPage / PermissionSet have no object prefix in their file name
        #     and are deployed by their OWN gated manifests (post-deploy FlexiPage
        #     gate, FLS/permission-set deploy). Including them here would silently
        #     redeploy unrelated pages/permission sets on every object push, so
        #     skip them entirely under --only. (Deploy them explicitly when needed.)
        if only and mtype in ("FlexiPage", "PermissionSet"):
            continue
        for f in sorted(d.glob(f"*{suffix}")):
            name = f.name[: -len(suffix)]
            # Layout members are Object-Label; filter by object when --only given
            if only and mtype == "Layout":
                obj = name.split("-", 1)[0]
                if obj not in only:
                    continue
            members[mtype].add(name)

    return {k: sorted(v) for k, v in members.items()}


def render_package(members: dict[str, list[str]], api_version: str) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    for mtype in TYPE_ORDER:
        vals = members.get(mtype)
        if not vals:
            continue
        lines.append("    <types>")
        for m in vals:
            lines.append(f"        <members>{sx.escape(m)}</members>")
        lines.append(f"        <name>{mtype}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{api_version}</version>")
    lines.append("</Package>")
    return "\n".join(lines) + "\n"


def render_destructive(deletions: dict[str, list[str]], api_version: str) -> str:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<Package xmlns="http://soap.sforce.com/2006/04/metadata">']
    for mtype in TYPE_ORDER:
        vals = sorted(set(deletions.get(mtype, [])))
        if not vals:
            continue
        lines.append("    <types>")
        for m in vals:
            lines.append(f"        <members>{sx.escape(m)}</members>")
        lines.append(f"        <name>{mtype}</name>")
        lines.append("    </types>")
    lines.append(f"    <version>{api_version}</version>")
    lines.append("</Package>")
    return "\n".join(lines) + "\n"


def object_of(mtype: str, member: str) -> str:
    """The object a member belongs to, so splits never separate an object from
    its own fields and translations."""
    if mtype == "CustomField" or mtype == "RecordType":
        return member.split(".", 1)[0]
    if mtype == "CustomObjectTranslation":
        return member.rsplit("-", 1)[0]
    if mtype == "Layout":
        return member.split("-", 1)[0]
    return member


class PackageCapError(ValueError):
    """An unsplittable dependency closure exceeds ``max_components``."""

    def __init__(self, message: str, *, object_api: str = "", size: int = 0,
                 cap: int = 0):
        super().__init__(message)
        self.object_api = object_api
        self.size = size
        self.cap = cap


def _group_size(group: dict[str, list]) -> int:
    return sum(len(v) for v in group.values())


def _member_keys(group: dict[str, list]) -> set[tuple[str, str]]:
    return {(t, m) for t, vals in group.items() for m in vals}


def _sorted_group(group: dict[str, list[str]]) -> dict[str, list[str]]:
    return {k: sorted(v) for k, v in sorted(
        ((t, vals) for t, vals in group.items() if vals),
        key=lambda kv: TYPE_ORDER.index(kv[0]) if kv[0] in TYPE_ORDER else 99)}


def _closure_error(group: dict[str, list[str]], size: int, cap: int) -> PackageCapError:
    objs = sorted({object_of(t, m) for t, vals in group.items() for m in vals})
    obj = objs[0] if len(objs) == 1 else ",".join(objs)
    bits = [f"{len(v)} {t}" for t, v in _sorted_group(group).items()]
    msg = (
        f"Object {obj} dependency closure is {size} component(s) "
        f"({', '.join(bits)}) which exceeds max_components={cap}. "
        "Check-only validates each package against the unchanged org, so a new "
        "object cannot be split from its fields or translations, and a "
        "CustomObjectTranslation cannot be split from the new fields it "
        "references. Raise --max-components to at least "
        f"{size} (Metadata API cap is 10000), or reduce the object's new members."
    )
    return PackageCapError(msg, object_api=obj, size=size, cap=cap)


def infer_atomic_groups(members: dict[str, list[str]]) -> list[dict[str, list[str]]]:
    """Conservative closures when the planner did not pass explicit groups.

    Any object that carries CustomObject or CustomObjectTranslation is treated
    as unsplittable (safe for check-only). Field-only groups may split.
    """
    by_object: dict[str, dict[str, list[str]]] = {}
    for mtype in TYPE_ORDER:
        for m in members.get(mtype, []):
            by_object.setdefault(object_of(mtype, m), {}).setdefault(mtype, []).append(m)
    groups: list[dict[str, list[str]]] = []
    for obj in sorted(by_object):
        g = by_object[obj]
        if g.get("CustomObject") or g.get("CustomObjectTranslation"):
            groups.append(g)
    return groups


def split_object_group(group: dict[str, list[str]], max_components: int
                       ) -> list[dict[str, list[str]]]:
    """Split a *dependency-free* object group under the component cap.

    CustomField lists may be chunked. This must NOT be used for a new object's
    CustomObject+fields+translation closure — those are atomic.
    """
    if max_components <= 0:
        return [group]
    sequence: list[tuple[str, str]] = []
    seen: set[str] = set()
    for mtype in TYPE_ORDER:
        for m in group.get(mtype) or []:
            sequence.append((mtype, m))
        seen.add(mtype)
    for mtype, vals in group.items():
        if mtype in seen:
            continue
        for m in vals:
            sequence.append((mtype, m))
    if not sequence:
        return []

    packages: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    count = 0
    for mtype, member in sequence:
        if count >= max_components:
            packages.append(current)
            current, count = {}, 0
        current.setdefault(mtype, []).append(member)
        count += 1
    if current:
        packages.append(current)
    return packages


def split_members(members: dict[str, list[str]], max_components: int,
                  atomic_groups: list[dict[str, list[str]]] | None = None
                  ) -> list[dict[str, list[str]]]:
    """Split a member set into deterministic packages under the component cap.

    ``atomic_groups`` are dependency closures that must stay in one package
    because check-only dry-runs each package against the *unchanged* org:
    a new object's CustomObject+fields+translation, and a translation plus
    every new field it references. Closures larger than the cap fail instead
    of producing independently invalid packages. Only leftover field groups
    (no such dependency) are split.
    """
    total = sum(len(v) for v in members.values())
    if not total:
        return []
    if max_components <= 0 or total <= max_components:
        return [members]

    if atomic_groups is None:
        atomic_groups = infer_atomic_groups(members)

    units: list[dict[str, list[str]]] = []
    taken: set[tuple[str, str]] = set()
    for raw in atomic_groups:
        group = {t: list(vals) for t, vals in raw.items() if vals}
        size = _group_size(group)
        if not size:
            continue
        if size > max_components:
            raise _closure_error(group, size, max_components)
        units.append(group)
        taken |= _member_keys(group)

    remainder: dict[str, list[str]] = {}
    for mtype, vals in members.items():
        left = [m for m in vals if (mtype, m) not in taken]
        if left:
            remainder[mtype] = left

    by_object: dict[str, dict[str, list[str]]] = {}
    for mtype in TYPE_ORDER:
        for m in remainder.get(mtype, []):
            by_object.setdefault(object_of(mtype, m), {}).setdefault(mtype, []).append(m)
    for obj in sorted(by_object):
        units.extend(split_object_group(by_object[obj], max_components))

    packages: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    count = 0

    def flush() -> None:
        nonlocal current, count
        if current:
            packages.append(current)
        current, count = {}, 0

    for unit in units:
        size = _group_size(unit)
        if size > max_components:
            raise _closure_error(unit, size, max_components)
        if count and count + size > max_components:
            flush()
        for mtype, vals in unit.items():
            current.setdefault(mtype, []).extend(vals)
        count += size
    flush()
    return [_sorted_group(p) for p in packages]


def plan_parts(members: dict[str, list[str]], max_components: int,
               stem: str = "package", suffix: str = ".xml",
               atomic_groups: list[dict[str, list[str]]] | None = None
               ) -> list[dict]:
    """The manifest INDEX: every package file this member set becomes.

    The planner stores this in the plan so the deploy command knows there are
    N packages and deploys every one of them in order. Deploying only
    `package.xml` when the plan split into parts silently drops components.
    """
    packages = split_members(members, max_components, atomic_groups=atomic_groups)
    if not packages:
        return []
    if len(packages) == 1:
        return [{"file": f"{stem}{suffix}", "members": packages[0],
                 "components": sum(len(v) for v in packages[0].values())}]
    return [{"file": f"{stem}.part{i}{suffix}", "members": part,
             "components": sum(len(v) for v in part.values())}
            for i, part in enumerate(packages, 1)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build package.xml / destructiveChanges.xml")
    ap.add_argument("--plan", default="",
                    help="deploy_plan.json — take the members from the PLAN "
                         "instead of scanning the source tree (authoritative)")
    ap.add_argument("--max-components", type=int, default=9000,
                    help="component cap per package (Metadata API allows 10000); "
                         "larger plans split deterministically into package.partN.xml")
    ap.add_argument("--source-root", default="force-app/main/default")
    ap.add_argument("--out", default="manifest/package.xml")
    ap.add_argument("--api-version", default="")
    ap.add_argument("--only", default="", help="comma-separated object API names to restrict to")
    ap.add_argument("--destroy", default="", help="deletions.json path")
    ap.add_argument("--destroy-out", default="manifest/destructiveChanges.xml")
    ap.add_argument("--project-root", default=".")
    args = ap.parse_args(argv)

    proj_root = Path(args.project_root)
    api_version = args.api_version or default_api_version(proj_root)
    source_root = Path(args.source_root)
    only = {o.strip() for o in args.only.split(",") if o.strip()} or None

    parts: list[dict] | None = None
    if args.plan:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        members = {k: sorted(v) for k, v in (plan.get("manifestMembers") or {}).items()}
        # The plan already decided how the members split; honour that index
        # verbatim so the files on disk match what the deploy will iterate.
        parts = plan.get("manifestParts") or None
        source = f"plan {args.plan}"
    else:
        if not source_root.is_dir():
            print(f"❌ source root '{source_root}' not found — run the generators first.")
            return 1
        members = discover(source_root, only)
        source = f"source scan {source_root}"

    total = sum(len(v) for v in members.values())
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    if parts is None:
        parts = plan_parts(members, args.max_components, out.stem, out.suffix)
    written = []
    for part in parts:
        p = out.with_name(Path(part["file"]).name)
        p.write_text(render_package(part["members"], api_version), encoding="utf-8")
        written.append(p)
    if not parts:                       # empty delta: still emit a valid manifest
        out.write_text(render_package({}, api_version), encoding="utf-8")
        written = [out]
    # Stale parts from a previous, larger run would otherwise be deployed again.
    keep = {p.name for p in written}
    for old in out.parent.glob(f"{out.stem}.part*{out.suffix}"):
        if old.name not in keep:
            old.unlink()
    packages = [p["members"] for p in parts]

    print(f"📦 package.xml  ->  {', '.join(str(p) for p in written)}   "
          f"(api {api_version}, from {source})")
    for mtype in TYPE_ORDER:
        if members.get(mtype):
            print(f"     {mtype:26} {len(members[mtype])}")
    if len(packages) > 1:
        print(f"   ⚠️  {total} components exceed --max-components "
              f"{args.max_components}: split into {len(packages)} package(s); "
              f"deploy them in order.")
    if total == 0:
        print("   ⚠️  no members — nothing to deploy (empty delta)."
              if args.plan else "   ⚠️  no metadata discovered under source root.")

    if args.destroy:
        try:
            deletions = json.loads(Path(args.destroy).read_text())
        except Exception as e:
            print(f"❌ could not read deletions file '{args.destroy}': {e}")
            return 1
        dout = Path(args.destroy_out)
        dout.parent.mkdir(parents=True, exist_ok=True)
        dout.write_text(render_destructive(deletions, api_version), encoding="utf-8")
        d_total = sum(len(v) for v in deletions.values())
        print(f"🗑️  destructiveChanges.xml  ->  {dout}   ({d_total} member(s))")
        print("     NOTE: pair with an empty package.xml for a delete-only deploy,")
        print("           or place alongside package.xml for an additive+destructive deploy.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
