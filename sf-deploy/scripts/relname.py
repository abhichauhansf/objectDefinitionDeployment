#!/usr/bin/env python3
"""Shared Lookup/Master-Detail relationshipName derivation.

Salesforce requires <relationshipName> on every relationship field. When the
sheet's col X is blank, we still have the lookup target in col H (設定値 /
referenceTo). Updated by Divakar N — 2026-10-05: when H is blank, col G is
that target. Derive X from the field API (minus __c), object-scoping the
name when the parent is a shared standard object so two children looking up
User/Account/… do not collide.

Used by validate_sheet.py (in-memory auto-fill, not an ERROR when H is set),
generate_xml.py (emit the derived name), and fill_relationship_name.py
(sheet write-back).
"""
from __future__ import annotations

import json
from pathlib import Path

# Salesforce CustomField.relationshipName max length (Metadata API).
MAX_RELATIONSHIP_NAME = 40

SHARED_PARENTS = frozenset({
    "User", "Account", "Contact", "Lead", "Group", "Case", "Opportunity",
    "Product2", "Pricebook2", "Asset", "Campaign", "Order", "Contract",
})

# Standard Salesforce objects we may attach custom fields to. Never emit a
# CustomObject for these and never append ``__c`` to their API names.
# Updated by Divakar N — 2026-09-23. Why: the roll-up test deploys Summary
# fields onto Account; prep_deploy used to rewrite Account → Account__c and
# generate_xml wrote a fake CustomObject, which Salesforce would reject.
STANDARD_OBJECTS = SHARED_PARENTS | frozenset({
    "OpportunityLineItem", "Task", "Event", "CampaignMember", "Quote",
    "QuoteLineItem", "OrderItem", "PricebookEntry",
})


def is_standard_object(obj_api: str) -> bool:
    """True when ``obj_api`` is a known standard entity (Account, Opportunity, …)."""
    api = (obj_api or "").strip()
    if not api or api.endswith("__c"):
        return False
    return api.split(".")[-1] in STANDARD_OBJECTS


def object_namespace_token(obj_api: str) -> str:
    """Fnt / Logi / Stc from a TI_* object API, else empty.

    Child relationship names are unique on the PARENT. TI_Fnt_TINETAccount and
    TI_Logi_TINETAccount share the component ``TINETAccount``, so a sheet name
    like ``CountryList_TINETAccount`` collides across those twins.
    """
    api = (obj_api or "").strip()
    for pfx, tok in (("TI_Fnt_", "Fnt"), ("TI_Logi_", "Logi"), ("TI_STC_", "Stc")):
        if api.startswith(pfx):
            return tok
    return ""


def object_component(obj_api: str) -> str:
    """Object API minus TI_* prefix and ``__c`` (e.g. TINETAccount)."""
    comp = (obj_api or "").strip()
    for pfx in ("TI_Fnt_", "TI_Logi_", "TI_STC_"):
        if comp.startswith(pfx):
            comp = comp[len(pfx):]
            break
    if comp.endswith("__c"):
        comp = comp[:-3]
    return comp


def qualify_relationship_name(rel_name: str, obj_api: str) -> str:
    """Insert Fnt/Logi/Stc into ``{parent}_{objectComponent}`` names.

    ``CountryList_TINETAccount`` on ``TI_Logi_TINETAccount__c`` becomes
    ``CountryList_LogiTINETAccount``. No-op when the token is already present
    or the name does not end with the object component.
    """
    name = (rel_name or "").strip()
    ns = object_namespace_token(obj_api)
    comp = object_component(obj_api)
    if not name or not ns or not comp:
        return name
    if ns + comp in name:
        return name
    if name.endswith(comp):
        qualified = f"{name[:-len(comp)]}{ns}{comp}"
        return qualified[:MAX_RELATIONSHIP_NAME]
    return name


def _obj_acronym(obj_api: str) -> str:
    """Short token for the owning object: acronym of capitals if >=3, else a
    trimmed component. e.g. ExportControlClassification -> 'ECC'."""
    comp = object_component(obj_api)
    caps = "".join(ch for ch in comp if ch.isupper())
    return caps if len(caps) >= 3 else comp[:12]


TAKEN_CACHE = Path(".build/relname_taken.json")


def bare_relationship_name(name: str) -> str:
    """Describe / RelationshipDomain report child names as ``Foo__r``; the
    CustomField metadata value is ``Foo``. Compare on the bare form."""
    n = (name or "").strip()
    return n[:-3] if n.lower().endswith("__r") else n


def save_taken(org: str, child_rels: dict[str, list[dict]]) -> None:
    """Persist the org's child relationships per parent (lowercased key) so
    generate_xml derives the same collision-free name the validator checked."""
    TAKEN_CACHE.parent.mkdir(exist_ok=True)
    json.dump({"org": org, "parents": child_rels},
              open(TAKEN_CACHE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def taken_names(parent: str, obj_api: str = "", field_api: str = "") -> set[str]:
    """Lowercased bare child-relationship names already on ``parent`` in the
    org, excluding the relationship owned by ``obj_api.field_api`` itself."""
    try:
        cache = json.load(open(TAKEN_CACHE, encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out = set()
    for cr in (cache.get("parents") or {}).get((parent or "").lower(), []):
        if cr.get("childSObject") == obj_api and cr.get("field") == field_api:
            continue
        out.add(bare_relationship_name(cr.get("relationshipName")).lower())
    return out


def derive_relationship_name(api: str, obj_api: str = "", parent: str = "",
                             taken: set[str] | None = None) -> str:
    """Return a legal relationshipName for a Lookup/MD field.

    `parent` is the col-H referenceTo (lookup object). Shared standard parents
    get an object-scoped name; everything else is field-API minus `__c`, unless
    that name is already used on the parent in the org (`taken`, default: the
    validator's cache), in which case it is object-scoped too.
    """
    api = (api or "").strip()
    if not api:
        return ""
    base = api[:-3] if api.endswith("__c") else api
    parent = (parent or "").strip()
    if taken is None:
        taken = taken_names(parent, obj_api, api)
    # strip namespace from referenceTo for the shared-parent test
    parent_core = parent[:-3] if parent.endswith("__c") else parent
    if not obj_api or (parent_core not in SHARED_PARENTS
                       and base[:MAX_RELATIONSHIP_NAME].lower() not in taken):
        return base[:MAX_RELATIONSHIP_NAME]
    comp = base[len("TI_Fnt_"):] if base.startswith("TI_Fnt_") else base
    scoped = f"{_obj_acronym(obj_api)}_{comp}"[:MAX_RELATIONSHIP_NAME]
    n = 2
    candidate = scoped
    while candidate.lower() in taken:
        suffix = str(n)
        candidate = scoped[:MAX_RELATIONSHIP_NAME - len(suffix)] + suffix
        n += 1
    return candidate
