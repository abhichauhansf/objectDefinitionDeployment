#!/usr/bin/env python3
"""
_fix_fls_dealdetail.py — grant FLS in SalesFrontAdmin for TI_Fnt_DealDetail__c
custom fields that currently have NONE. Classifies field types from the
RETRIEVED field-meta XML (FLS-independent — FieldDefinition is FLS-gated and
under-reports here), so Master-Detail / formula / required fields are handled
correctly. Read-only for formula/summary/autonumber; editable for the rest;
Master-Detail + required excluded (can't carry fieldPermissions).
"""
from __future__ import annotations
import json, subprocess, glob, os
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
def q(t): return f"{{{NS}}}{t}"

ORG = "ERPDEV01"
OBJ = "TI_Fnt_DealDetail__c"
PS_FILE = Path("force-app/main/default/permissionsets/SalesFrontAdmin.permissionset-meta.xml")
FIELD_DIR = Path(f"force-app/main/default/objects/{OBJ}/fields")

def sf_json(args):
    return json.loads(subprocess.run(args, capture_output=True, text=True).stdout)

# 1) classify each retrieved field
editable, readonly, excluded = [], [], []
for fp in sorted(glob.glob(str(FIELD_DIR / "*.field-meta.xml"))):
    root = ET.parse(fp).getroot()
    name = os.path.basename(fp).replace(".field-meta.xml", "")
    ftype = (root.findtext(q("type")) or "").strip()
    is_formula = root.find(q("formula")) is not None
    required = (root.findtext(q("required")) or "").strip().lower() == "true"
    if ftype == "MasterDetail" or required:
        excluded.append((name, ftype, "required" if required else "MasterDetail"))
    elif is_formula or ftype in ("Summary", "AutoNumber"):
        readonly.append(name)
    else:
        editable.append(name)

# 2) existing FLS for this object in SalesFrontAdmin (FieldPermissions is FLS-independent)
d = sf_json(["sf","data","query","--target-org",ORG,"--json","--query",
    f"SELECT Field FROM FieldPermissions WHERE SobjectType='{OBJ}' AND Parent.Name='SalesFrontAdmin'"])
existing = {r["Field"].split(".",1)[1] for r in d.get("result",{}).get("records",[])}

grant = [(f, True) for f in editable if f not in existing] + \
        [(f, False) for f in readonly if f not in existing]  # (field, editable?)

# 3) upsert into permission set
tree = ET.parse(PS_FILE); root = tree.getroot()
def strip_tag(el, tag):
    for c in list(el):
        if c.tag == tag: el.remove(c)
        else: strip_tag(c, tag)
strip_tag(root, q("viewAllFields"))
present = {fp.findtext(q("field")) for fp in root.findall(q("fieldPermissions"))}
added = 0
for f, ed in grant:
    full = f"{OBJ}.{f}"
    if full in present: continue
    fp = ET.SubElement(root, q("fieldPermissions"))
    ET.SubElement(fp, q("editable")).text = "true" if ed else "false"
    ET.SubElement(fp, q("field")).text = full
    ET.SubElement(fp, q("readable")).text = "true"
    added += 1

def sort_key(el):
    tag = el.tag.split("}")[-1]
    ident = (el.findtext(q("field")) or el.findtext(q("object")) or
             el.findtext(q("name")) or el.findtext(q("application")) or "")
    return (tag, ident)
children = list(root)
for c in children: root.remove(c)
children.sort(key=sort_key)
for c in children: root.append(c)
if hasattr(ET, "indent"): ET.indent(tree, space="    ")
with PS_FILE.open("wb") as f:
    f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
    tree.write(f, encoding="utf-8", xml_declaration=False)
    f.write(b"\n")

pkg = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<Package xmlns="http://soap.sforce.com/2006/04/metadata">',
       '    <types>', '        <members>SalesFrontAdmin</members>',
       '        <name>PermissionSet</name>', '    </types>',
       '    <version>62.0</version>', '</Package>']
Path("manifest/fix_fls_dealdetail.xml").write_text("\n".join(pkg)+"\n")

print(f"=== {OBJ} FLS classification (49 custom fields) ===")
print(f"  editable (Read+Edit): {len(editable)}")
print(f"  read-only (formula/summary/autonum): {len(readonly)}")
print(f"  excluded (MasterDetail/required): {len(excluded)} -> {excluded}")
print(f"  already had FLS: {len(existing)}")
print(f"\nNEW fieldPermissions added: {added}")
print(f"Total fieldPermissions in file now: {len(root.findall(q('fieldPermissions')))}")
print("Manifest -> manifest/fix_fls_dealdetail.xml")
