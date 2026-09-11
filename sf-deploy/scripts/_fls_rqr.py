#!/usr/bin/env python3
"""
_fls_rqr.py — grant SalesFrontAdmin access for the new junction object
TI_Fnt_RecevingAndQuoteAndWorkRelation__c:

  * objectPermissions  — full CRUD + View/Modify All (matches the 14 existing
    object grants on this permission set).
  * fieldPermissions   — DeduplicateKey__c (Text) Read+Edit. The 2 Master-Detail
    fields (QuotationAndWork__c, Receiving__c) are implicitly visible/required and
    cannot carry fieldPermissions, so they are intentionally excluded.

Operates on the freshly-RETRIEVED SalesFrontAdmin file (append-only; existing
grants preserved). Preserves the file's existing schema (incl. <viewAllFields>
inside objectPermissions, valid at the project API version) and only re-sorts
top-level children so same-type elements stay contiguous.
"""
from __future__ import annotations
from pathlib import Path
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)
def q(t): return f"{{{NS}}}{t}"

OBJ = "TI_Fnt_RecevingAndQuoteAndWorkRelation__c"
FIELD_RW = [f"{OBJ}.DeduplicateKey__c"]          # editable Read+Edit
# Master-Detail parents: object access on the DETAIL requires at least Read on
# each MASTER. TI_Fnt_Receiving__c is already granted; TI_Logi_QuotationAndWork__c
# (another team's object) needs a least-privilege Read-only grant to satisfy the
# dependency.
PARENTS_READONLY = ["TI_Logi_QuotationAndWork__c"]
PS_FILE = Path("force-app/main/default/permissionsets/SalesFrontAdmin.permissionset-meta.xml")

tree = ET.parse(PS_FILE); root = tree.getroot()
have_obj = {op.findtext(q("object")) for op in root.findall(q("objectPermissions"))}

def add_obj_perm(obj, *, full):
    op = ET.SubElement(root, q("objectPermissions"))
    ET.SubElement(op, q("allowCreate")).text = "true" if full else "false"
    ET.SubElement(op, q("allowDelete")).text = "true" if full else "false"
    ET.SubElement(op, q("allowEdit")).text = "true" if full else "false"
    ET.SubElement(op, q("allowRead")).text = "true"
    ET.SubElement(op, q("modifyAllRecords")).text = "true" if full else "false"
    ET.SubElement(op, q("object")).text = obj
    ET.SubElement(op, q("viewAllFields")).text = "false"
    ET.SubElement(op, q("viewAllRecords")).text = "true" if full else "false"

# ---- objectPermissions: junction (full CRUD + View/Modify All) ----
added_obj = 0
if OBJ not in have_obj:
    add_obj_perm(OBJ, full=True); added_obj = 1

# ---- objectPermissions: MD parents (Read-only) to satisfy the dependency ----
added_parents = []
for p in PARENTS_READONLY:
    if p not in have_obj:
        add_obj_perm(p, full=False); added_parents.append(p)

# ---- fieldPermissions (Read+Edit) ----
have_fld = {fp.findtext(q("field")) for fp in root.findall(q("fieldPermissions"))}
added_fld = 0
for full in FIELD_RW:
    if full in have_fld:
        continue
    fp = ET.SubElement(root, q("fieldPermissions"))
    ET.SubElement(fp, q("editable")).text = "true"
    ET.SubElement(fp, q("field")).text = full
    ET.SubElement(fp, q("readable")).text = "true"
    added_fld += 1

# ---- re-sort top-level children so same-type elements stay contiguous ----
def sort_key(el):
    tag = el.tag.split("}")[-1]
    ident = (el.findtext(q("field")) or el.findtext(q("object")) or
             el.findtext(q("name")) or el.findtext(q("application")) or el.findtext(q("tab")) or "")
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
       '    <version>66.0</version>', '</Package>']
Path("manifest/fls_rqr.xml").write_text("\n".join(pkg) + "\n")

print(f"objectPermissions added: {added_obj} ({OBJ}) + parents(read-only): {added_parents}")
print(f"fieldPermissions added : {added_fld} -> {FIELD_RW if added_fld else '(already present)'}")
print(f"objectPermissions total: {len(root.findall(q('objectPermissions')))}")
print(f"fieldPermissions total : {len(root.findall(q('fieldPermissions')))}")
print("Manifest -> manifest/fls_rqr.xml")
