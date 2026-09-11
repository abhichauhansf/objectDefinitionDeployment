#!/usr/bin/env python3
"""
_fix_batch1.py — NON-DESTRUCTIVE fix batch (one-off).

1) Flip required=true -> required=false on already-deployed fields of
   IncidentalExpenses / IncidentalExpensesDetail / StandaloneIncidentalExpenses
   (DealDetail has none). Minimal-diff: edits the org-retrieved field-meta files
   in force-app, touching ONLY the <required> element.
2) Generate 3 in-place Lookup->MasterDetail conversion field files
   (DeliveryDestination.TI_Fnt_Receiving__c, DocumentDestination.TI_Fnt_Shipping__c,
    ShipoutMovein.TI_Fnt_Shipping__c) — same referenceTo, empty objects -> safe.
3) Emit manifest/.build fix_batch1_package.xml listing exactly the touched members.
"""
import sys, os, re, glob
import xml.etree.ElementTree as ET
sys.path.insert(0, "scripts")
import generate_xml as g

OBJ_DIR = "force-app/main/default/objects"
REQ_OBJECTS = ["TI_Fnt_IncidentalExpenses__c",
               "TI_Fnt_IncidentalExpensesDetail__c",
               "TI_Fnt_StandaloneIncidentalExpenses__c",
               "TI_Fnt_DealDetail__c"]

flipped = {}   # obj -> [field,...]
for obj in REQ_OBJECTS:
    fdir = f"{OBJ_DIR}/{obj}/fields"
    flipped[obj] = []
    for fx in sorted(glob.glob(f"{fdir}/*.field-meta.xml")):
        txt = open(fx, encoding="utf-8").read()
        if re.search(r"<required>\s*true\s*</required>", txt):
            new = re.sub(r"<required>\s*true\s*</required>",
                         "<required>false</required>", txt)
            open(fx, "w", encoding="utf-8").write(new)
            flipped[obj].append(os.path.basename(fx).replace(".field-meta.xml", ""))

# ---- 2) generate the 3 MasterDetail conversion field files ----------------
CONVERSIONS = [
    ("TI_Fnt_DeliveryDestination__c", "TI_Fnt_Receiving__c", "TI_Fnt_Receiving__c", "DED_Receiving", "輸入・入庫管理"),
    ("TI_Fnt_DocumentDestination__c", "TI_Fnt_Shipping__c",  "TI_Fnt_Shipping__c",  "DCD_Shipping",  "輸出・出庫管理"),
    ("TI_Fnt_ShipoutMovein__c",       "TI_Fnt_Shipping__c",  "TI_Fnt_Shipping__c",  "SOM_Shipping",  "輸出・出庫管理"),
]
md_members = []
for obj, fld, ref, relname, label in CONVERSIONS:
    row = {
        "Field API Name": fld, "Data Type": "MasterDetail", "Field Label": label,
        "Reference To": ref, "Relationship Name": relname, "Relationship Label": label,
        "Required": "",
    }
    root = g.build_field_xml(row)
    assert root is not None, f"failed to build MD field {obj}.{fld}"
    if hasattr(ET, "indent"):
        ET.indent(root, space="    ")
    fdir = f"{OBJ_DIR}/{obj}/fields"
    os.makedirs(fdir, exist_ok=True)
    fp = f"{fdir}/{fld}.field-meta.xml"
    tree = ET.ElementTree(root)
    with open(fp, "wb") as f:
        f.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
        tree.write(f, encoding="utf-8", xml_declaration=False)
        f.write(b"\n")
    md_members.append(f"{obj}.{fld}")

# ---- 3) manifest ----------------------------------------------------------
members = []
for obj, flds in flipped.items():
    for fl in flds:
        members.append(f"{obj}.{fl}")
members += md_members
members = sorted(set(members))

pkg = ['<?xml version="1.0" encoding="UTF-8"?>',
       '<Package xmlns="http://soap.sforce.com/2006/04/metadata">',
       '    <types>']
for m in members:
    pkg.append(f"        <members>{m}</members>")
pkg += ['        <name>CustomField</name>', '    </types>',
        '    <version>62.0</version>', '</Package>']
os.makedirs("manifest", exist_ok=True)
open("manifest/fix_batch1_package.xml", "w", encoding="utf-8").write("\n".join(pkg) + "\n")

print("=== required=true -> false flips ===")
for obj, flds in flipped.items():
    print(f"  {obj}: {len(flds)} flipped")
print(f"\n=== MasterDetail conversions ({len(md_members)}) ===")
for m in md_members:
    print(f"  {m}")
tot = sum(len(v) for v in flipped.values()) + len(md_members)
print(f"\nTotal CustomField members in manifest: {len(members)}  (flips {tot-len(md_members)} + MD {len(md_members)})")
print("Manifest -> manifest/fix_batch1_package.xml")
