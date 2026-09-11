from __future__ import annotations
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

# Standard audit / system fields that Salesforce REJECTS as
# <fieldInstance><fieldItem>Record.<Field></fieldItem></fieldInstance> on a
# Lightning Record Page. Adding them produces a deploy error like:
#   "Something went wrong. We couldn't retrieve or load the information on
#    the field: Record.CreatedDate."
# These fields can only be surfaced via the standard Highlights Panel /
# recordDetails components, NEVER as a fieldInstance.
# We strip them from the generated FlexiPage XML so the sheet can still
# carry them as field-dictionary rows without breaking deploys.
FLEXIPAGE_DISALLOWED_FIELDS = frozenset({
    "Id",
    "CreatedDate",
    "CreatedById",
    "LastModifiedDate",
    "LastModifiedById",
    "SystemModstamp",
    "IsDeleted",
    "LastViewedDate",
    "LastReferencedDate",
    "LastActivityDate",
})

def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"

def set_text(parent: ET.Element, tag: str, text: str):
    el = parent.find(qname(tag))
    if el is None:
        el = ET.SubElement(parent, qname(tag))
    el.text = str(text).strip() if text is not None else ""

def ensure_no_self_closing(root: ET.Element):
    """Safeguard: Forces Python to write <value></value> instead of <value />"""
    for elem in root.iter():
        if len(elem) == 0 and elem.text is None:
            elem.text = ""

def scrub_invalid_tabset_properties(root: ET.Element):
    """Remove the 'label' componentInstanceProperty from every flexipage:tabset.

    Salesforce requires the label to be a translated token (@@@...@@@) or absent.
    A plain string such as 'Tabs' cannot be resolved by the validator and is treated
    as a null property, causing deployment to fail with:
      'Invalid null property [label] in component [flexipage:tabset]'

    The property is safe to omit — Salesforce uses its own default label when absent.
    """
    for comp in root.iter(qname("componentInstance")):
        if comp.findtext(qname("componentName")) == "flexipage:tabset":
            for prop in list(comp.findall(qname("componentInstanceProperties"))):
                if prop.findtext(qname("name")) == "label":
                    comp.remove(prop)

def load_columnuid_mapping(obj_api: str, flexi_name: str) -> dict | None:
    """Load ColumnUID Mapping JSON for a flexipage.
    
    Returns a dict mapping ColumnUID to (section_name, column_name), or None if not found.
    """
    mapping_path = Path("PartialMetadata/flexipage") / obj_api / f"{flexi_name}.json"
    if not mapping_path.exists():
        return None
    
    try:
        with mapping_path.open("r", encoding="utf-8") as f:
            mapping_json = json.load(f)
        
        # Flatten the structure: { "Section": { "Column": "UID" } } → { "UID": ("Section", "Column") }
        uid_map = {}
        for section, columns in mapping_json.items():
            if isinstance(columns, dict):
                for col_name, col_uid in columns.items():
                    uid_map[str(col_uid)] = (section, col_name)
        
        return uid_map
    except Exception as e:
        print(f"Warning: Could not load ColumnUID mapping for {obj_api}/{flexi_name}: {e}")
        return None


def validate_column_uid(col_uid: str, uid_map: dict | None, obj_api: str, flexi_name: str, field_api: str) -> bool:
    """Validate that a ColumnUID exists in the mapping.
    
    Returns True if valid or no mapping exists, False if invalid UID.
    """
    if uid_map is None:
        return True  # No mapping to validate against
    
    if col_uid not in uid_map:
        section, column = uid_map.get(col_uid, (None, None))
        print(f"⚠️  Warning: ColumnUID '{col_uid}' not found in mapping for {obj_api}/{flexi_name} (field: {field_api})")
        return False
    
    section, column = uid_map[col_uid]
    print(f"✓ Validated: {field_api} → {section} / {column} (UID: {col_uid})")
    return True

def normalize_column_uid_display(value: str) -> str:
    """Return the deployable ColumnUID from a sheet display value.

    Pull writes readable values such as "購買情報 (Facet-...)" so humans can
    identify the Lightning Page section. The XML region name in parentheses
    remains the authoritative placement key for Push.
    """
    text = str(value or "").strip()
    match = re.search(r"\(([^()]*)\)\s*$", text)
    if match:
        inner = match.group(1).strip()
        if inner:
            return inner
    return text

def build_field_identifier(field_name: str) -> str:
    ident = re.sub(r"[^a-zA-Z0-9]", "", field_name or "")
    if not ident:
        ident = "Field"
    return f"Record{ident}Field"

def order_sort_key(entry):
    """Sort key for (field_name, props) entries by LP.<flexipage>.Order.

    Numeric Orders sort ascending first; blank/non-numeric values fall to the
    end while preserving insertion order via a monotonically increasing tiebreak.
    The caller supplies (idx, (field_name, props)) so that original spreadsheet
    order is preserved when Order values tie or are missing.
    """
    idx, (field_name, props) = entry
    raw = str(props.get("Order", "")).strip()
    try:
        numeric = int(raw)
        return (0, numeric, idx)
    except (TypeError, ValueError):
        return (1, 0, idx)

def reserve_unique_identifier(base_identifier: str, used_identifiers: set[str]) -> str:
    """Reserve a unique identifier name and return it."""
    candidate = base_identifier
    index = 2
    while candidate in used_identifiers:
        candidate = f"{base_identifier}{index}"
        index += 1
    used_identifiers.add(candidate)
    return candidate

def collect_used_field_identifiers(root: ET.Element) -> set[str]:
    used = set()
    for fi in root.iter(qname("fieldInstance")):
        identifier = (fi.findtext(qname("identifier")) or "").strip()
        if identifier:
            used.add(identifier)
    return used

def enforce_unique_field_identifiers(root: ET.Element):
    """Guarantee unique <fieldInstance><identifier> values across the entire flexipage."""
    used_identifiers = set()
    for fi in root.iter(qname("fieldInstance")):
        ident_el = fi.find(qname("identifier"))
        if ident_el is None:
            ident_el = ET.SubElement(fi, qname("identifier"))
        current = (ident_el.text or "").strip()
        if not current:
            field_item = (fi.findtext(qname("fieldItem")) or "").strip()
            field_name = field_item[7:] if field_item.startswith("Record.") else field_item
            current = build_field_identifier(field_name)
        unique = reserve_unique_identifier(current, used_identifiers)
        if unique != current:
            print(f"  ⚠️  Duplicate field identifier '{current}' renamed to '{unique}'")
        ident_el.text = unique


def main():
    input_path = Path("temp_updates.json")
    if not input_path.exists():
        print("No temp_updates.json found.")
        return

    with input_path.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    flexi_dir = Path("force-app/main/default/flexipages")
    flexi_dir.mkdir(parents=True, exist_ok=True)
    partial_dir_base = Path("PartialMetadata/flexipage")
    
    flexi_updates = {}
    
    for row in rows:
        obj_api = row.get("Object API Name", row.get("_SheetName", "")).strip()
        field_api = row.get("Field API Name", "").strip()
        if not obj_api or not field_api: continue
        
        for key, val in row.items():
            if key.startswith("LP."):
                parts = key.split(".")
                if len(parts) < 3: continue
                
                flexi_name = parts[1]
                prop_name = ".".join(parts[2:])
                val_str = str(val).strip()
                
                if obj_api not in flexi_updates: flexi_updates[obj_api] = {}
                if flexi_name not in flexi_updates[obj_api]: flexi_updates[obj_api][flexi_name] = {}
                if field_api not in flexi_updates[obj_api][flexi_name]: flexi_updates[obj_api][flexi_name][field_api] = {}
                    
                if val_str:
                    if prop_name == "ColumnUID":
                        val_str = normalize_column_uid_display(val_str)
                    flexi_updates[obj_api][flexi_name][field_api][prop_name] = val_str
                    
                    # Validate ColumnUID if this property is ColumnUID
                    if prop_name == "ColumnUID":
                        uid_map = load_columnuid_mapping(obj_api, flexi_name)
                        validate_column_uid(val_str, uid_map, obj_api, flexi_name, field_api)

    for obj_api, flexipages in flexi_updates.items():
        for flexi_name, fields in flexipages.items():
            
            # Load the ColumnUID mapping for validation and tracking
            uid_map = load_columnuid_mapping(obj_api, flexi_name)
            
            print(f"\n--- Processing {obj_api}/{flexi_name} ---")
            if uid_map:
                print(f"✓ ColumnUID mapping found with {len(uid_map)} defined columns")
            else:
                print(f"⚠️  No ColumnUID mapping found (optional)")
            
            # --- 1. Generate Partial XML in /PartialMetadata ---
            partial_obj_dir = partial_dir_base / obj_api
            partial_obj_dir.mkdir(parents=True, exist_ok=True)
            partial_filepath = partial_obj_dir / f"{flexi_name}.xml"
            
            partial_root = ET.Element(qname("FlexiPage"))
            
            fields_by_col = {}
            # Fields present in the sheet whose LP.Order is blank / non-numeric
            # are treated as "not on this flexipage" — they are added to the
            # removal set so the surgical-removal step below strips any existing
            # <itemInstances> for them, and we never re-insert them.
            fields_to_remove: set[str] = set()

            disallowed_present: set[str] = set()

            for field_name, props in fields.items():
                # Salesforce rejects audit/system fields as fieldInstances on
                # a Lightning Record Page. Strip them whether the sheet has
                # an Order or not — they must NEVER appear as <fieldItem>
                # Record.<Field>, even if a user accidentally typed an Order.
                if field_name in FLEXIPAGE_DISALLOWED_FIELDS:
                    fields_to_remove.add(field_name)
                    disallowed_present.add(field_name)
                    continue

                col_uid = props.get("ColumnUID")
                raw_order = str(props.get("Order", "")).strip()
                try:
                    order_int = int(raw_order)
                    has_valid_order = order_int >= 1
                except (TypeError, ValueError):
                    has_valid_order = False

                if not col_uid or not has_valid_order:
                    fields_to_remove.add(field_name)
                    continue

                fields_by_col.setdefault(col_uid, []).append((field_name, props))

            if disallowed_present:
                names = ", ".join(sorted(disallowed_present))
                print(
                    f"  ⚠️  Skipping {len(disallowed_present)} audit/system "
                    f"field(s) that Salesforce does not allow as FlexiPage "
                    f"fieldInstances: {names}"
                )

            if fields_to_remove:
                names = ", ".join(sorted(fields_to_remove))
                print(
                    f"  ℹ️  Removing {len(fields_to_remove)} field(s) with blank "
                    f"LP.Order (not on this flexipage): {names}"
                )

            # Sort each column's fields by LP.<flexipage>.Order so both the partial
            # metadata reference and the surgical insertion below emit field
            # instances in the author-specified display order.
            for col_uid in list(fields_by_col.keys()):
                entries = list(enumerate(fields_by_col[col_uid]))
                entries.sort(key=order_sort_key)
                fields_by_col[col_uid] = [pair for _, pair in entries]

            for col_uid, col_fields in fields_by_col.items():
                region = ET.SubElement(partial_root, qname("flexiPageRegions"))
                
                for field_name, props in col_fields:
                    inst = ET.SubElement(region, qname("itemInstances"))
                    fi = ET.SubElement(inst, qname("fieldInstance"))
                    
                    ui_behavior = props.get("fieldInstanceProperties.uiBehavior")
                    if ui_behavior and ui_behavior.lower() in ["none", "readonly", "required"]:
                        fip = ET.SubElement(fi, qname("fieldInstanceProperties"))
                        set_text(fip, "name", "uiBehavior")
                        set_text(fip, "value", ui_behavior.lower())
                        
                    set_text(fi, "fieldItem", f"Record.{field_name}")
                    ident = re.sub(r'[^a-zA-Z0-9]', '', field_name)
                    set_text(fi, "identifier", f"Record{ident}Field")
                    
                    vis_rule = props.get("visibilityRule")
                    if vis_rule:
                        try:
                            vis_root = ET.fromstring(f"<visibilityRule>{vis_rule}</visibilityRule>")
                            fi.append(vis_root)
                        except ET.ParseError:
                            pass
                
                set_text(region, "name", col_uid)
                set_text(region, "type", "Region")
            
            ensure_no_self_closing(partial_root)
            partial_tree = ET.ElementTree(partial_root)
            
            with partial_filepath.open("wb") as f:
                f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                partial_tree.write(f, encoding="utf-8", xml_declaration=False, short_empty_elements=False)
                f.write(b"\n")
            
            print(f"✓ Generated partial metadata: {partial_filepath}")
            
            # --- 2. Update Actual XML in force-app (if exists) ---
            actual_filepath = flexi_dir / f"{flexi_name}.flexipage-meta.xml"
            if not actual_filepath.exists():
                print(f"ℹ️  Flexipage metadata not found at {actual_filepath} - skipping update")
                continue
                
            print(f"✓ Updating flexipage metadata: {actual_filepath}")
            actual_tree = ET.parse(actual_filepath)
            actual_root = actual_tree.getroot()

            # Collect all fields being updated across all ColumnUID regions.
            # We remove these globally so old copies in other regions cannot survive
            # and create duplicate identifiers during insertion.  The set is
            # unioned with `fields_to_remove` (blank-Order rows) so those fields
            # are stripped from the flexipage XML without being re-inserted.
            all_update_targets = {
                f"Record.{field_name}"
                for col_fields in fields_by_col.values()
                for field_name, _ in col_fields
            } | {f"Record.{name}" for name in fields_to_remove}

            # 1) GLOBAL SURGICAL REMOVAL: remove updated field instances from every region.
            for reg in actual_root.findall(qname("flexiPageRegions")):
                for child in list(reg.findall(qname("itemInstances"))):
                    fi = child.find(qname("fieldInstance"))
                    if fi is not None and fi.findtext(qname("fieldItem")) in all_update_targets:
                        reg.remove(child)

            # Track existing identifiers after global removal.
            used_identifiers = collect_used_field_identifiers(actual_root)
            
            for col_uid, col_fields in fields_by_col.items():
                region = None
                for reg in actual_root.findall(qname("flexiPageRegions")):
                    if reg.findtext(qname("name")) == col_uid:
                        region = reg
                        break
                
                if region is None:
                    print(f"  ⚠️  Region '{col_uid}' not found in flexipage XML")
                    continue
                
                # 2. Build the NEW item instances
                new_item_instances = []
                for field_name, props in col_fields:
                    inst = ET.Element(qname("itemInstances"))
                    fi = ET.SubElement(inst, qname("fieldInstance"))
                    
                    ui_behavior = props.get("fieldInstanceProperties.uiBehavior")
                    if ui_behavior and ui_behavior.lower() in ["none", "readonly", "required"]:
                        fip = ET.SubElement(fi, qname("fieldInstanceProperties"))
                        set_text(fip, "name", "uiBehavior")
                        set_text(fip, "value", ui_behavior.lower())
                        
                    set_text(fi, "fieldItem", f"Record.{field_name}")
                    base_identifier = build_field_identifier(field_name)
                    set_text(
                        fi,
                        "identifier",
                        reserve_unique_identifier(base_identifier, used_identifiers),
                    )
                    
                    vis_rule = props.get("visibilityRule")
                    if vis_rule:
                        try:
                            vis_root = ET.fromstring(f"<visibilityRule>{vis_rule}</visibilityRule>")
                            fi.append(vis_root)
                        except ET.ParseError:
                            pass
                    new_item_instances.append(inst)
                
                # 3. Find safe insertion index (before mode/name/prependable/type)
                insert_idx = len(region)
                for i, child in enumerate(list(region)):
                    if child.tag in [qname("mode"), qname("name"), qname("prependable"), qname("type")]:
                        insert_idx = i
                        break
                        
                # 4. SURGICAL INSERTION
                for new_inst in new_item_instances:
                    new_inst.tail = "\n        "
                    region.insert(insert_idx, new_inst)
                    insert_idx += 1
                    print(f"  ✓ Updated field in region: {col_uid}")

            # Final safety net for any pre-existing or newly introduced duplicates.
            enforce_unique_field_identifiers(actual_root)
            
            # Remove unresolvable label properties from tabsets, then prevent self-closing tags
            scrub_invalid_tabset_properties(actual_root)
            ensure_no_self_closing(actual_root)
            
            with actual_filepath.open("wb") as f:
                f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
                actual_tree.write(f, encoding="utf-8", xml_declaration=False, short_empty_elements=False)
                f.write(b"\n")
                
    print("FlexiPage XML generation complete.")

if __name__ == "__main__":
    main()
