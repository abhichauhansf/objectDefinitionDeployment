"""Generate / surgically update classic PageLayout XML from spreadsheet LO.* columns.

Mirrors generate_flexipage_partial.py for Lightning Pages, but targets classic
PageLayouts written to force-app/main/default/layouts/<Object>-<Label>.layout-meta.xml.

Spreadsheet contract (see docs/DATADICTIONARY_LAYOUT_DEPLOY.md):
    LO.<LayoutLabel>.SectionName    -> string label of the parent <layoutSections>
    LO.<LayoutLabel>.SectionColumn  -> 1-based int index into the section's
                                       <layoutColumns> children (1 = leftmost)
    LO.<LayoutLabel>.Order          -> 1-based int position of <layoutItems>
                                       within its column

Update policy (agreed with user on 2026-04-22):
- Surgical: remove every <layoutItems> whose <field> matches any field present
  in the sheet's update set, then insert that field's new <layoutItems> at the
  specified (SectionName, SectionColumn, Order) location.  Fields not present
  in the sheet (or with blank LO values) are left untouched.
- Section name MUST exist in the layout XML — we never auto-create sections
  because that requires choosing a <style> (OneColumn / TwoColumns / etc.) the
  sheet does not capture.  Missing sections produce a warning and the field
  is skipped.
- SectionColumn MAY exceed the section's current column count — we
  auto-insert empty <layoutColumns> elements until the requested column index
  exists.  This lets you push a 3-column section even if the source XML only
  has 2 columns today.
"""
from __future__ import annotations

from __future__ import annotations

import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)


def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"


# Detect Formula fields from the sheet's "Data Type" cell. Salesforce
# REJECTS layoutItems whose <field> is a formula / read-only system field
# unless <behavior>Readonly</behavior> is set, with errors like
# `Field:CustomerAddress__c must be Readonly`. We mirror the spellings
# accepted by `scripts/generate_xml.py::normalize_type` so behaviour stays
# in lock-step with the field-meta XML writer.
_FORMULA_DATA_TYPE_RE = re.compile(
    r"^\s*(?:formula|数式)\s*(?:[\s（(]|$)", re.IGNORECASE
)


def _is_formula_data_type(raw: str) -> bool:
    """True if the sheet's Data Type cell denotes a Formula return type.

    Matches `Formula Text`, `Formula Date/Time`, `Formula(Number)`,
    `数式（テキスト）`, etc.  We do NOT pull in `generate_xml.py`'s richer
    `normalize_type` because change-isolation requires the layout
    generator to stay self-contained — duplicating the lightweight regex
    is the safer trade-off.
    """
    return bool(_FORMULA_DATA_TYPE_RE.match(raw or ""))


def set_text(parent: ET.Element, tag: str, text: str) -> None:
    el = parent.find(qname(tag))
    if el is None:
        el = ET.SubElement(parent, qname(tag))
    el.text = str(text).strip() if text is not None else ""


def ensure_no_self_closing(root: ET.Element) -> None:
    """Force <tag></tag> instead of <tag /> for empty elements.

    Salesforce's metadata API accepts both, but git diffs against existing
    layouts (which Salesforce normally serializes with explicit close tags)
    become noisy if Python switches to self-closing form.
    """
    for elem in root.iter():
        if len(elem) == 0 and elem.text is None:
            elem.text = ""


def order_sort_key(entry):
    """Sort key for (field_api, props) entries by LO.<layout>.Order.

    Numeric Orders sort ascending first; blank/non-numeric values fall to
    the end while preserving sheet order via the original-index tiebreak.
    The caller supplies (idx, (field_api, props)) so that original spreadsheet
    order is preserved when Order values tie or are missing.
    """
    idx, (_field_api, props) = entry
    raw = str(props.get("Order", "")).strip()
    try:
        numeric = int(raw)
        return (0, numeric, idx)
    except (TypeError, ValueError):
        return (1, 0, idx)


def find_section_by_label(root: ET.Element, label: str) -> ET.Element | None:
    """Locate <layoutSections> whose <label> text matches `label` (trimmed)."""
    target = (label or "").strip()
    if not target:
        return None
    for section in root.findall(qname("layoutSections")):
        cur = (section.findtext(qname("label")) or "").strip()
        if cur == target:
            return section
    return None


def ensure_columns(section: ET.Element, required_count: int) -> list[ET.Element]:
    """Auto-expand <layoutColumns> until the section has `required_count` of them.

    Returns the (now guaranteed long enough) list of column elements in their
    document order.  Newly created columns are inserted directly after the
    last existing <layoutColumns> child so they precede any trailing tags
    such as <style>.
    """
    columns = section.findall(qname("layoutColumns"))
    while len(columns) < required_count:
        new_col = ET.Element(qname("layoutColumns"))
        # Insert after the last existing layoutColumns; otherwise after <label>.
        if columns:
            last = columns[-1]
            insert_idx = list(section).index(last) + 1
        else:
            label_el = section.find(qname("label"))
            insert_idx = (list(section).index(label_el) + 1) if label_el is not None else 0
        section.insert(insert_idx, new_col)
        columns = section.findall(qname("layoutColumns"))
    return columns


def remove_field_from_layout(root: ET.Element, field_api: str) -> None:
    """Remove every <layoutItems> child whose <field> equals field_api.

    why: surgical updates first remove the field from wherever it currently
    lives, then re-insert it at the sheet-specified position.  Without the
    global removal step, a field that moved sections would end up listed
    twice in the layout XML.
    """
    target = (field_api or "").strip()
    if not target:
        return
    for section in root.findall(qname("layoutSections")):
        for col in section.findall(qname("layoutColumns")):
            for item in list(col.findall(qname("layoutItems"))):
                if (item.findtext(qname("field")) or "").strip() == target:
                    col.remove(item)


FIELDLESS_RELATED_LISTS = {
    "RelatedActivityList",
    "RelatedHistoryList",
}


def sanitize_fieldless_related_lists(root: ET.Element) -> int:
    """Strip invalid <fields> entries under special related-list blocks.

    Salesforce Metadata API rejects custom field lists on
    activity/history related lists in many orgs with
    errors like:
      "Invalid field:TASK.SUBJECT in related list:RelatedActivityList"
      "Invalid field:TASK.SUBJECT in related list:RelatedHistoryList"
    Keep the related-list node itself, but remove any sibling <fields>.

    Returns the number of removed <fields> elements for logging.
    """
    removed = 0
    for related in root.findall(qname("relatedLists")):
        name = (related.findtext(qname("relatedList")) or "").strip()
        if name not in FIELDLESS_RELATED_LISTS:
            continue
        for field_el in list(related.findall(qname("fields"))):
            related.remove(field_el)
            removed += 1
    return removed


def build_layout_item(
    field_api: str,
    behavior: str | None = None,
    is_formula: bool = False,
) -> ET.Element:
    """Construct a <layoutItems> element with sensible defaults.

    When the sheet has a `LO.<name>.Behavior` column the caller passes its
    value as `behavior` (e.g. "Edit", "Readonly", "Required").  When the
    column is absent or the cell is blank, `behavior` is None and the
    function falls back to the auto-rules below.

    Special cases that ALWAYS force Readonly (Salesforce rejects deploys
    otherwise — `Field:<X> must be Readonly`):

    1. The standard `Name` field on objects with `<nameField type=AutoNumber>`
       (e.g. Sales_Deal__c uses `D-{00000}`).  Also safe for Text name
       fields because the layout edit semantics come from the object's
       nameField definition, not from layoutItems.behavior.
    2. Formula fields of any return type (Text / Number / Currency /
       Percent / Date / DateTime / Time / Checkbox).  Formula values are
       computed by Salesforce, so the field is never editable on a layout.
       Caller must pass `is_formula=True` based on the sheet's Data Type
       cell — see `_is_formula_data_type()`.
    """
    resolved_behavior = behavior
    if resolved_behavior is None:
        if field_api == "Name" or is_formula:
            resolved_behavior = "Readonly"
        else:
            resolved_behavior = "Edit"
    item = ET.Element(qname("layoutItems"))
    set_text(item, "behavior", resolved_behavior)
    set_text(item, "field", field_api)
    return item


def main() -> None:
    input_path = Path("temp_updates.json")
    if not input_path.exists():
        print("No temp_updates.json found.")
        return

    with input_path.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    layout_dir = Path("force-app/main/default/layouts")
    layout_dir.mkdir(parents=True, exist_ok=True)

    # layout_updates[obj_api][layout_label][field_api] = { "SectionName": ..., "SectionColumn": ..., "Order": ..., "Behavior": ... }
    layout_updates: dict[str, dict[str, dict[str, dict[str, str]]]] = {}

    # formula_fields[(obj_api, field_api)] = True for any row whose Data
    # Type cell denotes a Formula return type. Used to force
    # <behavior>Readonly</behavior> below.
    formula_fields: set[tuple[str, str]] = set()

    for row in rows:
        obj_api = (row.get("Object API Name") or row.get("_SheetName") or "").strip()
        field_api = (row.get("Field API Name") or "").strip()
        if not obj_api or not field_api:
            continue

        if _is_formula_data_type(str(row.get("Data Type") or "")):
            formula_fields.add((obj_api, field_api))

        for key, val in row.items():
            if not key.startswith("LO."):
                continue
            parts = key.split(".")
            if len(parts) < 3:
                continue
            # The layout label may itself contain dots (rare but legal in
            # Salesforce labels), so the property is always the LAST segment.
            prop_name = parts[-1]
            layout_label = ".".join(parts[1:-1])
            val_str = str(val).strip()
            if not val_str:
                continue
            layout_updates.setdefault(obj_api, {}).setdefault(
                layout_label, {}
            ).setdefault(field_api, {})[prop_name] = val_str

    if not layout_updates:
        print("No LO.* updates detected in temp_updates.json.")
        return

    for obj_api, layouts in layout_updates.items():
        for layout_label, fields in layouts.items():
            actual_filepath = layout_dir / f"{obj_api}-{layout_label}.layout-meta.xml"
            print(f"\n--- Processing {obj_api}-{layout_label} ---")

            if not actual_filepath.exists():
                # why: layout creation from scratch is out of scope (would
                # require synthesizing <layoutSections> with valid <style>
                # values).  We surface the missing layout so the user can
                # add it via Salesforce UI / sf project deploy.
                print(
                    f"⚠️  Layout file not found: {actual_filepath} — skipping. "
                    "Create the layout in Salesforce first, then re-run Push."
                )
                continue

            tree = ET.parse(actual_filepath)
            root = tree.getroot()
            removed_rel_fields = sanitize_fieldless_related_lists(root)
            if removed_rel_fields > 0:
                print(
                    f"  ℹ️  Removed {removed_rel_fields} invalid field(s) from activity/history related lists."
                )

            # --- 1. Group fields by their target (section, column) so we can
            #        sort by Order before insertion. -----------------------
            # Fields with a blank / non-numeric LO.Order are treated as
            # "not on this layout" — they are added to `fields_to_remove`
            # and stripped by the global surgical-removal step below.
            # SectionName / SectionColumn are ignored for these rows because
            # the removal semantics override placement: blanking Order is the
            # user's explicit "take this field off the layout" gesture.
            fields_by_location: dict[tuple[str, int], list[tuple[str, dict[str, str]]]] = {}
            unplaced: list[tuple[str, dict[str, str]]] = []
            fields_to_remove: set[str] = set()

            for field_api, props in fields.items():
                section_name = props.get("SectionName", "").strip()
                section_col_raw = props.get("SectionColumn", "").strip()
                order_raw = str(props.get("Order", "")).strip()
                try:
                    order_int = int(order_raw)
                    has_valid_order = order_int >= 1
                except (TypeError, ValueError):
                    has_valid_order = False

                if not has_valid_order:
                    fields_to_remove.add(field_api)
                    continue

                if not section_name or not section_col_raw:
                    unplaced.append((field_api, props))
                    continue
                try:
                    section_col = int(section_col_raw)
                except ValueError:
                    print(
                        f"  ⚠️  {field_api}: SectionColumn '{section_col_raw}' is not "
                        f"an integer — skipping."
                    )
                    continue
                if section_col < 1:
                    print(
                        f"  ⚠️  {field_api}: SectionColumn must be >= 1 (got {section_col}) — skipping."
                    )
                    continue
                fields_by_location.setdefault((section_name, section_col), []).append(
                    (field_api, props)
                )

            if fields_to_remove:
                names = ", ".join(sorted(fields_to_remove))
                print(
                    f"  ℹ️  Removing {len(fields_to_remove)} field(s) with blank "
                    f"LO.Order (not on this layout): {names}"
                )

            if unplaced:
                names = ", ".join(fa for fa, _ in unplaced)
                print(
                    f"  ℹ️  {len(unplaced)} field(s) have LO.Order but no "
                    f"SectionName+SectionColumn — left in place: {names}"
                )

            # --- 2. Sort each (section, column) bucket by Order so insertion
            #        emits the spreadsheet-specified display order. -------
            for loc in list(fields_by_location.keys()):
                entries = list(enumerate(fields_by_location[loc]))
                entries.sort(key=order_sort_key)
                fields_by_location[loc] = [pair for _, pair in entries]

            # --- 3. GLOBAL SURGICAL REMOVAL ----------------------------
            # Strip every layoutItems for the fields we are about to (re)place
            # so their old positions cannot survive in another section/column.
            # Blank-Order rows are unioned in so those fields are removed from
            # the layout XML without being re-inserted below.
            all_targets = {
                field_api
                for bucket in fields_by_location.values()
                for field_api, _ in bucket
            } | fields_to_remove
            for field_api in all_targets:
                remove_field_from_layout(root, field_api)

            # --- 4. INSERT each field at its specified location ------------
            inserted = 0
            for (section_name, section_col), bucket in fields_by_location.items():
                section = find_section_by_label(root, section_name)
                if section is None:
                    names = ", ".join(fa for fa, _ in bucket)
                    print(
                        f"  ⚠️  Section '{section_name}' not found in layout — "
                        f"skipping {len(bucket)} field(s): {names}"
                    )
                    continue

                # Auto-expand columns if the requested SectionColumn exceeds
                # the section's current width.  See module docstring.
                columns = ensure_columns(section, section_col)
                target_col = columns[section_col - 1]

                # Existing items in the column (after global removal above).
                # We honor the sheet's Order as a 1-based insert index but
                # cap it at len(existing)+1 so values like "999" simply
                # append rather than crash.
                for field_api, props in bucket:
                    raw_order = str(props.get("Order", "")).strip()
                    try:
                        order_int = int(raw_order)
                    except (TypeError, ValueError):
                        order_int = None

                    sheet_behavior = props.get("Behavior", "").strip() or None
                    new_item = build_layout_item(
                        field_api,
                        behavior=sheet_behavior,
                        is_formula=(obj_api, field_api) in formula_fields,
                    )
                    existing_items = target_col.findall(qname("layoutItems"))
                    if order_int is not None and order_int >= 1:
                        insert_idx = min(order_int - 1, len(existing_items))
                    else:
                        insert_idx = len(existing_items)

                    # Find the absolute position of the Nth layoutItems within
                    # target_col so siblings (rare but possible) are unaffected.
                    if existing_items and insert_idx < len(existing_items):
                        anchor = existing_items[insert_idx]
                        abs_idx = list(target_col).index(anchor)
                    else:
                        # Append after the last layoutItems, or at end if none.
                        if existing_items:
                            abs_idx = list(target_col).index(existing_items[-1]) + 1
                        else:
                            abs_idx = len(list(target_col))

                    target_col.insert(abs_idx, new_item)
                    inserted += 1
                    print(
                        f"  ✓ Placed {field_api} → "
                        f"'{section_name}' / col {section_col} / order {order_int or 'append'}"
                    )

            if inserted == 0 and not fields_to_remove:
                print(f"  ℹ️  No fields placed for {obj_api}-{layout_label}.")
                continue

            ensure_no_self_closing(root)

            with actual_filepath.open("wb") as f:
                f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                tree.write(f, encoding="utf-8", xml_declaration=False, short_empty_elements=False)
                f.write(b"\n")

            print(f"✓ Updated layout: {actual_filepath}")

    print("\nPageLayout XML generation complete.")


if __name__ == "__main__":
    main()
