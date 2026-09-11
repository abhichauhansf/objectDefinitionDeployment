#!/usr/bin/env python3
"""One-off local patch for the 4 Shipping/Receiving objects (NO sheet/org write).

- fills 25 curated Lookup API names (blank-API rows we agreed to name),
- fills relationshipName (= API minus __c) + relationshipLabel (= Field Label)
  on every deployable Lookup missing it,
- DROPS rows referencing the non-existent TI_Fnt_BusinessPartner__c (parked, 10),
- DROPS the duplicate 2nd TI_Fnt_LineItem__c on ReceivingDetail (keep first),
- DROPS the 12 non-field noise rows (blank label + blank type).
"""
import json
import sys

SRC = ".build/ship_recv_deploy.json"
OUT = ".build/ship_recv_deploy.json"
# referenceTo targets that do NOT exist in the org (only their *Master twin does).
# BusinessPartner was un-parked after the client repointed those rows to
# TI_Fnt_BusinessPartnerMaster__c (live in the sheet). PaymentTerms still missing.
PARKED_REFS = {"TI_Fnt_PaymentTerms__c"}
# short object codes used to make child-relationship names unique per parent
OBJ_CODE = {
    "TI_Fnt_Shipping__c": "Ship",
    "TI_Fnt_ShippingDetail__c": "ShipDtl",
    "TI_Fnt_Receiving__c": "Recv",
    "TI_Fnt_ReceivingDetail__c": "RecvDtl",
}

# curated names: object short -> {label: [api,...]} (list handles duplicate labels)
CURATED = {
    "TI_Fnt_Shipping__c": {
        "受注先 名称": ["TI_Fnt_SoldToParty__c"],
        "支払人": ["TI_Fnt_Payer__c"],
        "Accountee": ["TI_Fnt_Accountee__c"],
        "Consignee": ["TI_Fnt_Consignee__c"],
        "Notify Party": ["TI_Fnt_NotifyParty__c"],
        "エンドユーザ": ["TI_Fnt_EndUser__c"],
        "Ship To": ["TI_Fnt_ShipTo__c"],
        "受注先 国コード": ["TI_Fnt_SoldToCountry__c"],
        "支払人 国コード": ["TI_Fnt_PayerCountry__c"],
        "経由地": ["TI_Fnt_TransitPoint__c"],
        "Accountee 国コード": ["TI_Fnt_AccounteeCountry__c"],
        "Consignee 国コード": ["TI_Fnt_ConsigneeCountry__c"],
        "Notify Party 国コード": ["TI_Fnt_NotifyPartyCountry__c"],
        "エンドユーザ 国コード": ["TI_Fnt_EndUserCountry__c"],
        "Ship To 国コード": ["TI_Fnt_ShipToCountry__c"],
        "輸出前保管場所": ["TI_Fnt_PreExportStorageLoc__c"],
        "船社": ["TI_Fnt_ShippingCompany__c"],
        "FORWARDER": ["TI_Fnt_Forwarder__c"],
        "PLACE OF RECEIPT": ["TI_Fnt_PlaceOfReceipt__c"],
        "PORT OF LOADING": ["TI_Fnt_PortOfLoading__c"],
        "PORT OF DISCHARGING": ["TI_Fnt_PortOfDischarging__c"],
        "PLACE OF DELIVERY": ["TI_Fnt_PlaceOfDelivery__c"],
        "営業員": ["TI_Fnt_SalesRep__c"],
        "経由地2": ["TI_Fnt_TransitPoint2__c"],
        "経由地3": ["TI_Fnt_TransitPoint3__c"],
        "Booking依頼No": ["TI_Fnt_BookingRequestNo__c"],
    },
    "TI_Fnt_ShippingDetail__c": {
        "輸出・出庫管理": ["TI_Fnt_ShipmentMgmt__c"],
        "成約明細": ["TI_Fnt_DealDetail__c"],
        "基本数量単位": ["TI_Fnt_BaseQuantityUnit__c"],
    },
    "TI_Fnt_Receiving__c": {
        "取引先銀行": ["TI_Fnt_PartnerBank__c", "TI_Fnt_PartnerBank2__c"],
        "SHIPPER": ["TI_Fnt_Shipper__c"],
    },
    "TI_Fnt_ReceivingDetail__c": {
        "輸入・入庫管理": ["TI_Fnt_ReceiptMgmt__c"],
    },
}


def norm(s):
    return str(s or "").strip()


def main():
    rows = json.load(open(SRC, encoding="utf-8"))
    # per-object queues (copy of curated lists so we can pop)
    queues = {o: {k: list(v) for k, v in d.items()} for o, d in CURATED.items()}
    out = []
    named, relfilled, dropped = [], [], []
    seen_lineitem = set()

    for r in rows:
        if r.get("_type") == "object_meta":
            out.append(r)
            continue
        obj = norm(r.get("Object API Name"))
        label = norm(r.get("Field Label"))
        typ = norm(r.get("Data Type"))
        api = norm(r.get("Field API Name"))
        ref = norm(r.get("Reference To")) or norm(r.get("Type Specific Value"))

        # DROP: parked references whose target object does not exist in the org
        if typ == "Lookup" and ref in PARKED_REFS:
            dropped.append((obj, label, api or "(blank)", f"parked: referenceTo {ref} missing in org"))
            continue
        # DROP: noise non-field rows (blank label + blank type)
        if not label and not typ:
            dropped.append((obj, "(no label)", api or "(blank)", "noise: record-type/rule helper"))
            continue
        # DROP: duplicate 2nd TI_Fnt_LineItem__c on ReceivingDetail
        if obj == "TI_Fnt_ReceivingDetail__c" and api == "TI_Fnt_LineItem__c":
            if "x" in seen_lineitem:
                dropped.append((obj, label, api, "duplicate: 2nd LineItem row skipped"))
                continue
            seen_lineitem.add("x")

        # NAME: blank-API rows we curated
        if not api and obj in queues and label in queues[obj] and queues[obj][label]:
            api = queues[obj][label].pop(0)
            r["Field API Name"] = api
            named.append((obj, label, api, ref))

        # RELNAME: deployable Lookup missing relationshipName. Child-relationship
        # names must be UNIQUE on the parent across ALL child objects, so qualify
        # with a short object code (e.g. Ship_PlaceOfDelivery) to avoid collisions
        # with the same role on sibling objects / other objects already in the org.
        if typ == "Lookup" and api.endswith("__c"):
            if not norm(r.get("Relationship Name")):
                comp = api[:-3]
                if comp.startswith("TI_Fnt_"):
                    comp = comp[len("TI_Fnt_"):]
                rel = f"{OBJ_CODE.get(obj, 'X')}_{comp}"
                r["Relationship Name"] = rel
                relfilled.append((obj, api, rel))
            if not norm(r.get("Relationship Label")):
                r["Relationship Label"] = label or api[:-3]

        out.append(r)

    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    def sh(o):
        return o.replace("TI_Fnt_", "").replace("__c", "")

    print(f"NAMED ({len(named)}):")
    for o, l, a, ref in named:
        print(f"  [{sh(o):16s}] {l:22s} -> {a:30s} (ref {ref})")
    print(f"\nRELNAME FILLED ({len(relfilled)}):")
    for o, a, rn in relfilled:
        print(f"  [{sh(o):16s}] {a:32s} -> rel {rn}")
    print(f"\nDROPPED ({len(dropped)}):")
    for o, l, a, why in dropped:
        print(f"  [{sh(o):16s}] {l:22s} {a:22s} | {why}")
    # leftover unresolved curated (safety)
    leftover = [(o, k) for o, d in queues.items() for k, v in d.items() if v]
    if leftover:
        print("\n⚠️  UNUSED curated names (label not matched):")
        for o, k in leftover:
            print(f"     [{sh(o)}] {k}")
    print(f"\nwrote {OUT}: {len(out)} rows")


if __name__ == "__main__":
    sys.exit(main())
