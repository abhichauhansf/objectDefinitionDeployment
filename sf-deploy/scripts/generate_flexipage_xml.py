from __future__ import annotations
import argparse
import json
import re
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

def qname(tag: str) -> str:
    return f"{{{NS}}}{tag}"

def normalize(value) -> str:
    if value is None:
        return ""
    return str(value).strip()

def keyify(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize(value).lower())

def row_value(row: dict, aliases, default: str = "") -> str:
    for alias in aliases:
        alias_key = keyify(alias)
        for k, v in row.items():
            if keyify(k) == alias_key:
                val = normalize(v)
                if val != "":
                    return val
    return default

def row_to_model(row: dict) -> dict:
    api_name = str(row_value(row, ["Name", "fieldname", "API Name", "Component"]))
    item_type = str(row_value(row, ["ItemType", "Type", "Item Type"]))
    
    # If ItemType or Name contains "section", force the section API name.
    if "section" in item_type.lower() or "section" in api_name.lower():
        api_name = "flexipage:fieldSection"
        
    raw_page_name = str(row_value(row, ["_FlexiPageName"]))
    safe_page_name = raw_page_name.replace(" ", "_")

    return {
        "sheet_name": row_value(row, ["_SheetName"]),
        "flexipage_name": safe_page_name,
        "master_label": row_value(row, ["_MasterLabel"]),
        "object_name": row_value(row, ["Object"]),
        "region": row_value(row, ["Region"]),
        "component": api_name,
        "item_type": item_type,
        "order": row_value(row, ["Order"], default="1"),
        "property_name": row_value(row, ["PropertyName", "Property Name"]),
        "property_value": row_value(row, ["PropertyValue", "Property Value"]),
        "parent_field_api_name": row_value(row, ["Parent Field Api Name", "ParentFieldApiName"]),
        "related_list_api_name": row_value(row, ["Related List Api Name", "RelatedListApiName"]),
        "related_list_label": row_value(row, ["Related List Label", "RelatedListLabel"]),
        "related_list_field_aliases": row_value(row, ["Related List Field Aliases", "RelatedListFieldAliases", "Fields"]),
        "related_list_display_type": row_value(row, ["Related List Display Type", "RelatedListDisplayType", "Display Type"]),
        
        "sort_field_alias": row_value(row, ["Sort Field", "Sort Field Alias", "SortFieldAlias"]),
        "sort_field_order": row_value(row, ["Sort Order", "SortOrder", "Sort Field Order"]),

        "section_label": row_value(row, ["Section Name", "Section Label", "Name"]),
        "columns_count": row_value(row, ["Columns", "Column"]),
    }

def order_key(row: dict):
    raw = normalize(row.get("order", "1"))
    if re.fullmatch(r"\d+", raw):
        return (0, int(raw))
    return (1, raw)

def set_text(parent: ET.Element, tag: str, text: str):
    ET.SubElement(parent, qname(tag)).text = normalize(text)

def indent(elem: ET.Element, level: int = 0):
    pad = "\n" + "  " * level
    if len(elem):
        if not elem.text or not elem.text.strip():
            elem.text = pad + "  "
        for child in elem:
            indent(child, level + 1)
        if not elem[-1].tail or not elem[-1].tail.strip():
            elem[-1].tail = pad
    if level and (not elem.tail or not elem.tail.strip()):
        elem.tail = pad
def slugify(text: str) -> str:
    return re.sub(r'[^A-Z0-9_]', '', str(text).upper().replace(' ', '_'))
def build_flexipage_xml(flexipage_name: str, rows: list[dict]) -> tuple[ET.Element, dict]:
    root = ET.Element(qname("FlexiPage"))
    by_region = {}
    page_mapping = {}
    extra_facets = []
    
    for row in rows:
        reg = normalize(row.get("region", "main")).lower()
        if not row.get("component"): continue
        by_region.setdefault(reg, []).append(row)

    for region_name, region_rows in by_region.items():
        region_el = ET.SubElement(root, qname("flexiPageRegions"))
        components = {}
        
        for row in sorted(region_rows, key=order_key):
            comp_name = row.get("component")
            order_val = row.get("order")
            comp_key = f"{comp_name}_{order_val}"
            
            if comp_key not in components:
                components[comp_key] = {
                    "name": comp_name, 
                    "order": order_val, 
                    "properties": {},
                    "section_label": row.get("section_label", ""),
                    "columns_count": row.get("columns_count", "2")
                }
            
            props = components[comp_key]["properties"]
            if row.get("property_name"): props[row["property_name"]] = row["property_value"]
            if row.get("parent_field_api_name"): props["parentFieldApiName"] = row["parent_field_api_name"]
            if row.get("related_list_api_name"): props["relatedListApiName"] = row["related_list_api_name"]
            if row.get("related_list_label"): props["relatedListLabel"] = row["related_list_label"]
            if row.get("related_list_field_aliases"): props["relatedListFieldAliases"] = row["related_list_field_aliases"]
            if row.get("related_list_display_type"): props["relatedListDisplayType"] = row["related_list_display_type"]
            
            if row.get("sort_field_alias"): props["sortFieldAlias"] = row["sort_field_alias"]
            if row.get("sort_field_order"): props["sortFieldOrder"] = row["sort_field_order"]

        for comp_key, comp_data in components.items():
            item_inst = ET.SubElement(region_el, qname("itemInstances"))
            comp_inst = ET.SubElement(item_inst, qname("componentInstance"))
            
            # Nested facets: a facet inside a facet (section wrapper -> columns).
            if comp_data["name"] == "flexipage:fieldSection":
                sec_label = comp_data.get("section_label")
                if not sec_label:
                    sec_label = "SECTION"
                    
                col_str = comp_data.get("columns_count")
                try:
                    cols = int(col_str) if col_str else 2
                except ValueError:
                    cols = 2
                
                slug_base = slugify(sec_label)
                if not slug_base:
                    slug_base = "SECTION"
                
                slug = f"{slug_base}_{comp_data['order']}"
                wrapper_facet_id = f"{slug}_WRAPPER"
                
                # The "columns" property takes a single value (the wrapper facet id), not a valueList.
                col_prop = ET.SubElement(comp_inst, qname("componentInstanceProperties"))
                set_text(col_prop, "name", "columns")
                set_text(col_prop, "value", wrapper_facet_id)
                
                wrapper_reg = ET.Element(qname("flexiPageRegions"))
                
                if sec_label not in page_mapping:
                    page_mapping[sec_label] = {}

                for i in range(1, cols + 1):
                    col_facet_id = f"{slug}_COL{i}"
                    
                    # Inside the wrapper: a flexipage:column with a "body" property.
                    col_item = ET.SubElement(wrapper_reg, qname("itemInstances"))
                    col_comp = ET.SubElement(col_item, qname("componentInstance"))
                    
                    body_prop = ET.SubElement(col_comp, qname("componentInstanceProperties"))
                    set_text(body_prop, "name", "body")
                    set_text(body_prop, "value", col_facet_id)
                    
                    set_text(col_comp, "componentName", "flexipage:column")
                    # Keep the identifier unique to avoid clashes.
                    set_text(col_comp, "identifier", f"col_{slug}_{i}")
                    
                    page_mapping[sec_label][f"Column {i}"] = col_facet_id
                    
                    # Final empty facet that fields will be placed into later.
                    col_reg = ET.Element(qname("flexiPageRegions"))
                    set_text(col_reg, "name", col_facet_id)
                    set_text(col_reg, "type", "Facet")
                    extra_facets.append(col_reg)

                set_text(wrapper_reg, "name", wrapper_facet_id)
                set_text(wrapper_reg, "type", "Facet")
                extra_facets.append(wrapper_reg)

                lbl_prop = ET.SubElement(comp_inst, qname("componentInstanceProperties"))
                set_text(lbl_prop, "name", "label")
                set_text(lbl_prop, "value", sec_label)

            for p_name, p_val in comp_data["properties"].items():
                prop_el = ET.SubElement(comp_inst, qname("componentInstanceProperties"))
                set_text(prop_el, "name", p_name)
                
                if p_name in ["relatedListFieldAliases", "actionNames"]:
                    val_list_el = ET.SubElement(prop_el, qname("valueList"))
                    clean_val = str(p_val).replace('[', '').replace(']', '').replace('"', '').replace("'", "")
                    fields = [f.strip() for f in clean_val.split(',') if f.strip()]
                    for f in fields:
                        val_item_el = ET.SubElement(val_list_el, qname("valueListItems"))
                        set_text(val_item_el, "value", f)
                else:
                    set_text(prop_el, "value", str(p_val))

            set_text(comp_inst, "componentName", comp_data["name"])
            safe_name = str(comp_data['name']).replace(':', '_').replace('-', '_').replace(' ', '_')
            identifier = f"{safe_name}_{comp_data['order']}"
            set_text(comp_inst, "identifier", identifier)

        set_text(region_el, "name", region_name)
        set_text(region_el, "type", "Region")

    for fr in extra_facets:
        root.append(fr)

    master_lbl = rows[0].get("master_label", flexipage_name) if rows else flexipage_name
    object_name = rows[0].get("object_name", "Account") if rows else "Account"
    set_text(root, "masterLabel", master_lbl)
    set_text(root, "sobjectType", object_name)
    template = ET.SubElement(root, qname("template"))
    set_text(template, "name", "flexipage:recordHomeTemplateDesktop")
    set_text(root, "type", "RecordPage")
    
    return root, page_mapping

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="temp_updates.json")
    parser.add_argument("--out-dir", default="force-app/main/default/flexipages")
    # Mapping JSON output is no longer needed; the flexipage_event path only
    # generates scaffold FlexiPage XML under force-app/main/default/flexipages.
    # parser.add_argument("--mapping-dir", default="PartialMetadata/flexipage")
    args = parser.parse_args()
    
    input_path = Path(args.input)
    if not input_path.exists(): 
        return
        
    with input_path.open("r", encoding="utf-8") as f:
        rows_raw = json.load(f)
        
    rows = [row_to_model(r) for r in rows_raw if isinstance(r, dict)]
    by_page = {}
    
    for row in rows:
        page = normalize(row.get("flexipage_name", ""))
        if page: by_page.setdefault(page, []).append(row)
        
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    for page_name, page_rows in by_page.items():
        root, page_mapping = build_flexipage_xml(page_name, page_rows)
        indent(root)
        tree = ET.ElementTree(root)
        
        # Save the XML file.
        out_file = out_dir / f"{page_name}.flexipage-meta.xml"
        with out_file.open("wb") as f:
            f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            tree.write(f, encoding="utf-8", xml_declaration=False)
            f.write(b"\n")
        print(f"Generated: {out_file}")
        # Mapping JSON generation intentionally disabled. This used to write:
        #   PartialMetadata/flexipage/<ObjectName>/<PageName>.json
        # The current flexipage_event workflow only needs the scaffold XML above.
        # if page_mapping:
        #     obj_name = page_rows[0].get("object_name", "UnknownObject") if page_rows else "UnknownObject"
        #     obj_mapping_dir = mapping_dir / obj_name
        #     obj_mapping_dir.mkdir(parents=True, exist_ok=True)
        #     mapping_file = obj_mapping_dir / f"{page_name}.json"
        #     with mapping_file.open("w", encoding="utf-8") as f:
        #         json.dump(page_mapping, f, indent=4)
        #     print(f"Generated Mapping JSON: {mapping_file}")

if __name__ == "__main__":
    main()
