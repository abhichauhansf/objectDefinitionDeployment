#!/usr/bin/env python3
"""
_fix_batch2.py — DESTRUCTIVE recreate of the 2 self-lookup fields as
MasterDetail->parent (referenceTo changed self->parent, which SF cannot do
in place). Generates:
  * the 2 new MasterDetail field-meta files (same API name),
  * manifest/fix_recreate_destructive.xml (pre-destructive: delete the 2 old fields),
  * manifest/fix_recreate_package.xml     (create: the 2 new MD fields).
No org writes here — prep only.
"""
import sys, os
import xml.etree.ElementTree as ET
sys.path.insert(0, "scripts")
import generate_xml as g

OBJ_DIR = "force-app/main/default/objects"
RECREATE = [
    ("TI_Fnt_ShippingDetail__c", "TI_Fnt_ShipmentMgmt__c", "TI_Fnt_Shipping__c",  "ShipDtl_ShipmentMgmt", "輸出・出庫管理"),
    ("TI_Fnt_ReceivingDetail__c", "TI_Fnt_ReceiptMgmt__c", "TI_Fnt_Receiving__c", "RecvDtl_ReceiptMgmt",  "輸入・入庫管理"),
]
members = []
for obj, fld, ref, relname, label in RECREATE:
    row = {"Field API Name": fld, "Data Type": "MasterDetail", "Field Label": label,
           "Reference To": ref, "Relationship Name": relname, "Relationship Label": label,
           "Required": ""}
    root = g.build_field_xml(row)
    assert root is not None
    if hasattr(ET, "indent"):
        ET.indent(root, space="    ")
    fdir = f"{OBJ_DIR}/{obj}/fields"; os.makedirs(fdir, exist_ok=True)
    with open(f"{fdir}/{fld}.field-meta.xml", "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        ET.ElementTree(root).write(f, encoding="utf-8", xml_declaration=False)
        f.write(b"\n")
    members.append(f"{obj}.{fld}")

def pkg(ms):
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<Package xmlns="http://soap.sforce.com/2006/04/metadata">', '    <types>']
    for m in ms: out.append(f"        <members>{m}</members>")
    out += ['        <name>CustomField</name>', '    </types>',
            '    <version>62.0</version>', '</Package>']
    return "\n".join(out) + "\n"

os.makedirs("manifest", exist_ok=True)
open("manifest/fix_recreate_destructive.xml", "w").write(pkg(members))
# empty package for the destructive-only phase is not used; create-package below
open("manifest/fix_recreate_package.xml", "w").write(pkg(members))
print("generated MD field files + manifests for:", members)
print(" destructive: manifest/fix_recreate_destructive.xml")
print(" create/pkg : manifest/fix_recreate_package.xml")
