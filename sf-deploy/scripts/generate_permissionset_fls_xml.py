"""Generate / merge Permission Set FLS XML from temp_updates.json.

TI-154 — primary Permission Set FLS generator. Both ``Deploy Active Sheet``
and ``Deploy All Sheets`` invoke this generator via
``.github/workflows/salesforce-sync-data-dictionary.yml``.

Reads `temp_updates.json` (emitted by the workflow's inline fetch_data.js),
groups field rows by permission set (any key prefixed with ``PM.``), and
upserts `force-app/main/default/permissionsets/<PermSet>.permissionset-meta.xml`.

Rules:

    RU | TRUE | ○  -> <readable>true</readable>  <editable>true</editable>
    R              -> <readable>true</readable>  <editable>false</editable>
    "" | - | × | FALSE | N/A -> remove any matching <fieldPermissions>
    other          -> row skipped, error logged to stderr (prev entry preserved)

Self-contained (WET) per ``.cursor/rules/wet-principle.mdc``: re-implements
XML helpers locally instead of sharing a module with other generators.
"""
from __future__ import annotations

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

INPUT_PATH = Path("temp_updates.json")
PERMSET_DIR = Path("force-app/main/default/permissionsets")

PERMSET_COL_PREFIX = "PM."

DEVELOPER_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,39}$")

YES_READ_WRITE = {"RU", "TRUE", "○"}
YES_READ_ONLY = {"R"}
NEGATIVES = {"", "-", "×", "FALSE", "N/A"}


def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def set_text(parent: ET.Element, tag: str, text: str) -> None:
    el = parent.find(qname(tag))
    if el is None:
        el = ET.SubElement(parent, qname(tag))
    el.text = str(text).strip()


def get_sort_key(el: ET.Element) -> tuple[str, str]:
    tag = el.tag.split("}")[-1]
    ident = (
        el.findtext(qname("field"))
        or el.findtext(qname("object"))
        or el.findtext(qname("name"))
        or el.findtext(qname("application"))
        or ""
    )
    return (tag, ident)


def remove_tag_recursively(element: ET.Element, tag_to_remove: str) -> None:
    for child in list(element):
        if child.tag == tag_to_remove:
            element.remove(child)
        else:
            remove_tag_recursively(child, tag_to_remove)


def load_rows(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[generate_permissionset_fls_xml] No {path} found; nothing to do.")
        return []
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return [row for row in data if isinstance(row, dict)]


def map_access(raw_value: str) -> tuple[bool, bool] | None:
    """Return (readable, editable) or None when the entry should be removed.

    Raises ValueError for unknown values so the caller can log & skip.
    """
    normalized = str(raw_value or "").strip().upper()
    if normalized in NEGATIVES:
        return None
    if normalized in YES_READ_WRITE:
        return (True, True)
    if normalized in YES_READ_ONLY:
        return (True, False)
    raise ValueError(f"Unknown FLS value: {raw_value!r}")


def group_by_permset(rows: list[dict]) -> dict[str, dict[str, str]]:
    """Return {permset_developer_name: {"Object.Field": raw_cell_value}}.

    Only rows with both Object API Name + Field API Name contribute.
    Skips object-meta rows (``_type == 'object_meta'``).
    """
    grouped: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("_type") == "object_meta":
            continue
        obj_api = str(row.get("Object API Name") or row.get("_SheetName") or "").strip()
        field_api = str(row.get("Field API Name") or "").strip()
        if not obj_api or not field_api:
            continue
        full_field = f"{obj_api}.{field_api}"
        for key, value in row.items():
            if not isinstance(key, str) or not key.startswith(PERMSET_COL_PREFIX):
                continue
            permset = key[len(PERMSET_COL_PREFIX):].strip()
            if not DEVELOPER_NAME_RE.match(permset):
                print(
                    f"[generate_permissionset_fls_xml] Skipping invalid permission set name: {permset!r}",
                    file=sys.stderr,
                )
                continue
            grouped.setdefault(permset, {})[full_field] = value
    return grouped


def _load_or_create_tree(path: Path, permset_name: str) -> ET.ElementTree:
    if path.exists():
        tree = ET.parse(path)
        root = tree.getroot()
        remove_tag_recursively(root, qname("viewAllFields"))
        return tree
    root = ET.Element(qname("PermissionSet"))
    set_text(root, "hasActivationRequired", "false")
    set_text(root, "isCustom", "true")
    set_text(root, "label", permset_name)
    return ET.ElementTree(root)


def upsert_permissionset_xml(
    permset_name: str,
    fields: dict[str, str],
    permset_dir: Path,
) -> Path:
    filepath = permset_dir / f"{permset_name}.permissionset-meta.xml"
    tree = _load_or_create_tree(filepath, permset_name)
    root = tree.getroot()

    for field_name, raw_value in fields.items():
        try:
            access = map_access(raw_value)
        except ValueError as exc:
            print(
                f"[generate_permissionset_fls_xml] {permset_name} :: {field_name} :: {exc}",
                file=sys.stderr,
            )
            continue

        for fp in list(root.findall(qname("fieldPermissions"))):
            if fp.findtext(qname("field")) == field_name:
                root.remove(fp)

        if access is None:
            continue

        readable, editable = access
        fp = ET.SubElement(root, qname("fieldPermissions"))
        set_text(fp, "editable", "true" if editable else "false")
        set_text(fp, "field", field_name)
        set_text(fp, "readable", "true" if readable else "false")

    children = list(root)
    for child in children:
        root.remove(child)
    children.sort(key=get_sort_key)
    for child in children:
        root.append(child)

    if hasattr(ET, "indent"):
        ET.indent(tree, space="    ")

    filepath.parent.mkdir(parents=True, exist_ok=True)
    with filepath.open("wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)
        f.write(b"\n")
    return filepath


def main() -> None:
    rows = load_rows(INPUT_PATH)
    if not rows:
        return

    grouped = group_by_permset(rows)
    if not grouped:
        print("[generate_permissionset_fls_xml] No PM.* columns detected; nothing to do.")
        return

    PERMSET_DIR.mkdir(parents=True, exist_ok=True)
    processed = 0
    for permset_name, fields in grouped.items():
        upsert_permissionset_xml(permset_name, fields, PERMSET_DIR)
        processed += 1

    print(f"FLS PermSet sync: {processed} permission sets processed.")


if __name__ == "__main__":
    main()
