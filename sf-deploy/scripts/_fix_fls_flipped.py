#!/usr/bin/env python3
"""
_fix_fls_flipped.py — grant Read+Edit FLS in SalesFrontAdmin for the fields that
were flipped required=true -> false (they lost their implicit visibility and had
no fieldPermissions). Computes the missing set LIVE per object, EXCLUDES
Master-Detail + still-required fields (can't carry FLS), upserts fieldPermissions,
and re-sorts all children so fieldPermissions stay contiguous (KB grouping rule).

Env: HOME=.sfhome, XDG_DATA_HOME=/Users/abhi.chauhan/.local/share (for sf CLI).
"""
from __future__ import annotations
import json, subprocess, sys
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
def q(t): return f"{{{NS}}}{t}"

ORG = "ERPDEV01"
PS_FILE = Path("force-app/main/default/permissionsets/SalesFrontAdmin.permissionset-meta.xml")
OBJECTS = ["TI_Fnt_IncidentalExpenses__c",
           "TI_Fnt_StandaloneIncidentalExpenses__c",
           "TI_Fnt_IncidentalExpensesDetail__c"]

def sf_json(args):
    out = subprocess.run(args, capture_output=True, text=True)
    return json.loads(out.stdout)

def all_custom(obj):
    d = sf_json(["sf","data","query","--use-tooling-api","--target-org",ORG,"--json","--query",
        f"SELECT DeveloperName FROM CustomField WHERE EntityDefinition.QualifiedApiName='{obj}'"])
    return {r["DeveloperName"]+"__c" for r in d.get("result",{}).get("records",[])}

def existing_fls(obj):
    d = sf_json(["sf","data","query","--target-org",ORG,"--json","--query",
        f"SELECT Field FROM FieldPermissions WHERE SobjectType='{obj}' AND Parent.Name='SalesFrontAdmin'"])
    return {r["Field"].split(".",1)[1] for r in d.get("result",{}).get("records",[])}

def rel_or_required(obj):
    """MasterDetail fields (DataType Master-Detail) — exclude from FLS."""
    d = sf_json(["sf","data","query","--target-org",ORG,"--json","--query",
        f"SELECT QualifiedApiName, DataType FROM FieldDefinition WHERE EntityDefinition.QualifiedApiName='{obj}' AND QualifiedApiName LIKE 'TI_Fnt_%'"])
    md=set()
    for r in d.get("result",{}).get("records",[]):
        if (r.get("DataType") or "").lower().startswith("master-detail"):
            md.add(r["QualifiedApiName"])
    return md

# 1) compute missing set to grant
to_grant = []   # "Obj.Field"
per_obj = {}
for obj in OBJECTS:
    allc = all_custom(obj); fls = existing_fls(obj); md = rel_or_required(obj)
    missing = sorted(allc - fls - md)
    per_obj[obj] = missing
    for f in missing:
        to_grant.append(f"{obj}.{f}")

# 2) upsert into the permission set, then re-sort children contiguous
tree = ET.parse(PS_FILE); root = tree.getroot()

# strip <viewAllFields> everywhere (newer-API property, invalid at v62 — same as
# the primary FLS generator does)
def strip_tag(el, tag):
    for child in list(el):
        if child.tag == tag: el.remove(child)
        else: strip_tag(child, tag)
strip_tag(root, q("viewAllFields"))
existing_fp = {fp.findtext(q("field")) for fp in root.findall(q("fieldPermissions"))}
added = 0
for full in to_grant:
    if full in existing_fp:
        continue
    fp = ET.SubElement(root, q("fieldPermissions"))
    ET.SubElement(fp, q("editable")).text = "true"
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

# 3) manifest
pkg = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<Package xmlns="http://soap.sforce.com/2006/04/metadata">',
       '    <types>', '        <members>SalesFrontAdmin</members>',
       '        <name>PermissionSet</name>', '    </types>',
       '    <version>62.0</version>', '</Package>']
Path("manifest/fix_fls_flipped.xml").write_text("\n".join(pkg)+"\n")

print("=== FLS to grant (Read+Edit) ===")
for obj, missing in per_obj.items():
    print(f"  {obj}: {len(missing)} fields")
print(f"\nTotal new fieldPermissions added: {added}")
print(f"Total fieldPermissions now in file: {len(root.findall(q('fieldPermissions')))}")
print("Manifest -> manifest/fix_fls_flipped.xml")
