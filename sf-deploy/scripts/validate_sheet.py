#!/usr/bin/env python3
"""
validate_sheet.py — Pre-deployment validation for Data Dictionary rows.

Reads `temp_updates.json` (produced by fetch_sheet.py) and validates every
field row against Salesforce metadata rules BEFORE any XML/package is built.
Emits a detailed execution log (PASS / FAIL per check + diagnostics) and a
machine-readable report. Exit code 0 = all clear, 1 = blocking ERROR(s).

Rule sources (reconciled + refined — see docs/FIELD_TYPE_VALIDATION_RULES.md,
DATADICTIONARY_VALIDATION.md, GOOGLE_SHEET_STRUCTURE.md):
  - Semicolon `;` is the picklist value delimiter.
  - Checkbox: defaultValue (TRUE/FALSE) required.
  - MultiselectPicklist: visibleLines required.
  - Lookup/MasterDetail: a field already in the org keeps the org's
    relationshipName (Divakar N, 2026-10-06; read live with --target-org).
    For a NEW field, relationshipName (col X) is auto-filled from the field
    API when col X is blank AND the lookup object is known. The lookup object
    is col H (設定値); col G is never read (Divakar N, 2026-10-08). Only
    blank-X PLUS blank H is a hard blocker. A filled relationshipName longer
    than 40 characters is a hard ERROR (Salesforce Metadata API max).
  - Blank custom-field API name (col D) is NOT an ERROR (Divakar N, 2026-09-21):
    emit INFO `field.api.generate` and name+write-back instead of parking.
  - Formula Number/Currency/Percent: precision + scale required (SF Metadata API),
    derived from col F ("12, 3") — T/U are not client-required.
  - Number/Currency/Percent size lives in col F (サイズ); T/U are derived.
  - unique/externalId only for Text, Email, Phone, Url, Number, Currency, Percent.
  - Rollup summary: col H (設定値) holds Operation:COUNT / Child:… / Field:…
    / Condition:… (**no space after the colon**). Blank H is a hard blocker.
    Updated by Divakar N — 2026-09-23: previously required non-existent
    Summary Foreign Key / Summary Operation columns, so every roll-up failed
    or deployed as empty ``<type>Summary</type>``.

Usage:
  python scripts/validate_sheet.py [--in temp_updates.json] [--json report.json] [--strict]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from relname import (  # noqa: E402
    MAX_RELATIONSHIP_NAME,
    ORG_RELNAME_CACHE,
    TAKEN_CACHE,
    bare_relationship_name,
    derive_relationship_name,
    fetch_org_relationship_names,
    is_standard_object,
    org_relationship_name,
    qualify_relationship_name,
    save_org_relationship_names,
    save_taken,
)
from rollup_h import apply_rollup_h  # noqa: E402  # Updated by Divakar N — 2026-09-23
from picklist_api import org_picklist_values, split_entry, split_values  # noqa: E402
from numeric_size import apply_numeric_size, is_numeric_sheet_type, numeric_size_mode_banner  # noqa: E402
# --------------------------------------------------------------------------- #
# Rule table  (data_type -> required / optional / forbidden / constraints)
# Keys reference temp_updates.json field keys.
# --------------------------------------------------------------------------- #
G = "Type Specific Value"  # polymorphic Column H (picklist / referenceTo / formula / displayFormat)

RULES: dict[str, dict] = {
    "Text":                {"required": ["Length"], "optional": ["Unique", "External ID"], "forbidden": ["Precision", "Scale", "Visible Lines", "Mask Character"], "len": ("Length", 1, 255)},
    "TextArea":            {"required": [], "optional": ["Length"], "forbidden": ["Precision", "Scale", "Visible Lines", "Mask Character", "Unique", "External ID"]},
    "LongTextArea":        {"required": ["Length", "Visible Lines"], "optional": [], "forbidden": ["Precision", "Scale", "Mask Character", "Unique", "External ID"], "len": ("Length", 256, 131072), "vl": ("Visible Lines", 1, 50)},
    "Html":                {"required": ["Length", "Visible Lines"], "optional": [], "forbidden": ["Precision", "Scale", "Mask Character", "Unique", "External ID"], "len": ("Length", 256, 131072), "vl": ("Visible Lines", 10, 50)},
    "EncryptedText":       {"required": ["Length", "Mask Character"], "optional": [], "forbidden": ["Precision", "Scale", "Visible Lines"], "len": ("Length", 1, 175)},
    # Length (col F) is the client-facing numeric size ("12, 3"); Precision/Scale
    # are derived in-memory. Keep them required AFTER apply_numeric_size so a
    # blank F AND blank T/U still fails. Do NOT forbid Length.
    "Number":              {"required": ["Precision", "Scale"], "optional": ["Unique", "External ID"], "forbidden": ["Visible Lines", "Mask Character"], "num": True},
    "Currency":            {"required": ["Precision", "Scale"], "optional": ["Unique", "External ID"], "forbidden": ["Visible Lines", "Mask Character"], "num": True},
    "Percent":             {"required": ["Precision", "Scale"], "optional": ["Unique", "External ID"], "forbidden": ["Visible Lines", "Mask Character"], "num": True},
    "Picklist":            {"required": [G], "optional": ["Global Value Set", "Restricted"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", "Unique", "External ID"], "picklist": True},
    "MultiselectPicklist": {"required": [G, "Visible Lines"], "optional": ["Global Value Set", "Restricted"], "forbidden": ["Length", "Precision", "Scale", "Mask Character", "Unique", "External ID"], "picklist": True, "vl": ("Visible Lines", 1, 50)},
    "Lookup":              {"required": [G], "optional": ["Relationship Label", "Relationship Name", "Delete Constraint"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character"], "ref": True},
    "MasterDetail":        {"required": [G], "optional": ["Relationship Label", "Relationship Name"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character"], "ref": True},
    "AutoNumber":          {"required": [G], "optional": [], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", "Unique"]},
    "Summary":             {"required": [G], "optional": [], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", "Unique", "External ID"], "summary": True},  # Updated by Divakar N — 2026-09-23: required is col H, not phantom Summary columns
    "Checkbox":            {"required": [], "optional": ["Default Value"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G, "Unique", "External ID"], "checkbox": True},
    "Date":                {"required": [], "optional": [], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G, "Unique", "External ID"]},
    "DateTime":            {"required": [], "optional": [], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G, "Unique", "External ID"]},
    "Time":                {"required": [], "optional": [], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G, "Unique", "External ID"]},
    "Email":               {"required": [], "optional": ["Unique", "External ID"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G]},
    "Phone":               {"required": [], "optional": ["Unique", "External ID"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G]},
    "Url":                 {"required": [], "optional": ["Unique", "External ID"], "forbidden": ["Length", "Precision", "Scale", "Visible Lines", "Mask Character", G]},
    "Location":            {"required": ["Scale"], "optional": [], "forbidden": ["Length", "Visible Lines", "Mask Character", "Unique", "External ID"]},
}

# Formula return types → underlying rule (numeric ones require precision+scale).
FORMULA_TYPES = {
    "Formula Checkbox": [G], "Formula Text": [G], "Formula Date": [G],
    "Formula Date/Time": [G], "Formula DateTime": [G], "Formula Time": [G],
    "Formula Number": [G, "Precision", "Scale"],
    "Formula Currency": [G, "Precision", "Scale"],
    "Formula Percent": [G, "Precision", "Scale"],
}
FORMULA_NUMERIC = {"Formula Number", "Formula Currency", "Formula Percent"}

UNIQUE_EXTID_TYPES = {"Text", "Email", "Phone", "Url", "Number", "Currency", "Percent"}
API_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
# First relationship hop of a formula path (`X__r.Field__c`); later hops belong to the parent.
FORMULA_FIRST_HOP_RE = re.compile(r"(?<![\w.])([A-Za-z][A-Za-z0-9_]*?)__r\.")
FORMULA_BARE_FIELD_RE = re.compile(r"(?<![\w.$])([A-Za-z][A-Za-z0-9_]*__c)\b(?!\.)")
FORMULA_LOOKUP_DOT_RE = re.compile(r"(?<![\w.])([A-Za-z][A-Za-z0-9_]*__c)\.[A-Za-z]")
FORMULA_PARENT_FIELD_RE = re.compile(
    r"(?<![\w.])([A-Za-z][A-Za-z0-9_]*?)__r\.([A-Za-z][A-Za-z0-9_]*)(?![\w.])")
PICKLIST_SAFE_FN_RE = re.compile(
    r"\b(TEXT|ISPICKVAL|CASE|ISBLANK|ISNULL|INCLUDES|PRIORVALUE|ISCHANGED)\s*\(\s*$",
    re.IGNORECASE)
# Lowercased picklist / multipicklist field APIs per lowercased parent object,
# filled by query_parent_child_relationships() in --target-org mode.
PARENT_PICKLISTS: dict[str, set[str]] = {}
KNOWN_STANDARD = {
    "Id", "Name", "OwnerId", "CreatedById", "CreatedDate", "LastModifiedById",
    "LastModifiedDate", "SystemModstamp", "IsDeleted", "LastActivityDate",
    "LastViewedDate", "LastReferencedDate", "RecordTypeId", "RecordType",
    "CurrencyIsoCode",
}
# Updated by Divakar N — 2026-10-07. Why: blank English is filled by the
# prep_deploy translation step (JA → en_US), so on a raw sheet it is pending
# work, not a blocker. prep_deploy passes --require-en after enrichment so a
# deploy still never ships a blank en_US label.
REQUIRE_EN = False
TRUTHY = {"true", "y", "yes", "1", "○", "〇"}
BOOLEANISH = TRUTHY | {"false", "n", "no", "0", "×", "-", ""}


def _te():
    try:
        from translate_enrich import LABEL_MAX_LEN, invalid_english, object_en_for_org, norm as _n
    except ImportError:
        sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
        from translate_enrich import LABEL_MAX_LEN, invalid_english, object_en_for_org, norm as _n
    return LABEL_MAX_LEN, invalid_english, object_en_for_org, _n


def _validate_object_header_en(rep: Report, obj: str, obj_ja: str, obj_en: str,
                               name_ja: str, name_en: str) -> None:
    """Object English is not rewritten on the sheet. Over-limit header EN → Name EN in org."""
    max_len, _, object_en_for_org, _n = _te()
    deploy_en, via = object_en_for_org(obj_en, name_en)
    if via == "name_en":
        if not _n(deploy_en):
            msg = (f"object English exceeds Salesforce label limit "
                   f"({len(_n(obj_en))}>{max_len}) and Name EN is blank")
            if REQUIRE_EN:
                rep.error(obj, "-", "translation.en", msg)
            else:
                rep.info(obj, "-", "translation.pending",
                         f"{msg} — Name EN will be filled by the prep_deploy translation step")
            return
        # Updated by Divakar N — 2026-10-13. Why: on CompanyInfoTransactionDetail
        # both labels were 48 chars; the translation file then carried the
        # Japanese master as English caseValues with no startsWith and the
        # dry-run failed ("StartsWith is required for language ENGLISH").
        if len(_n(deploy_en)) > max_len:
            rep.warn(obj, "-", "translation.object_none",
                     f"object English and Name EN both exceed {max_len} chars; unless "
                     f"DeepL fits Name EN, the object gets NO English label in the org "
                     f"(fields still translate) — shorten Name EN on the sheet to fix")
        _validate_translation(rep, obj, "-", name_ja, deploy_en, kind="object")
        rep.warn(obj, "-", "translation.en",
                 f"header object English is {len(_n(obj_en))} chars (limit {max_len}); "
                 f"org will use Name EN {deploy_en!r}; header cells not overwritten")
        return
    _validate_translation(rep, obj, "-", obj_ja, obj_en, kind="object")


def _validate_translation(rep: Report, obj: str, loc: str, ja: str, en: str, *, kind: str) -> None:
    """Blank English is pending translation unless REQUIRE_EN; invalid English always blocks."""
    _, invalid_english, _, _n = _te()
    ja_n, en_n = _n(ja), _n(en)
    if not ja_n:
        # object/name JA blank is already covered by sheet structure; field.label covers fields.
        if kind != "field":
            rep.error(obj, loc, "translation.ja", f"{kind} Japanese source label is blank")
        return
    if not en_n:
        if REQUIRE_EN:
            rep.error(obj, loc, "translation.en",
                      f"{kind} English label is blank — cannot deploy without en_US")
        else:
            rep.info(obj, loc, "translation.pending",
                     f"{kind} English label is blank — not a blocker; filled by the "
                     f"prep_deploy translation step (JA → en_US)")
        return
    reason = invalid_english(en_n, ja=ja_n, api=loc if loc != "-" else obj)
    if reason.startswith("English exceeds Salesforce label limit"):
        # Updated by Divakar N — 2026-10-07. Why: length is not a blocker; the
        # prep_deploy translation step re-translates it with DeepL to fit.
        rep.warn(obj, loc, "translation.length",
                 f"{kind} English is too long: {reason} — prep_deploy re-translates "
                 f"it with DeepL; if it still does not fit, this label's English is "
                 f"not deployed")
    elif reason:
        rep.error(obj, loc, "translation.en", f"{kind} English is invalid: {reason}")


def truthy(v) -> bool:
    return str(v or "").strip().lower() in TRUTHY


def nonblank(v) -> bool:
    return bool(str(v or "").strip())


class Report:
    def __init__(self):
        self.items: list[dict] = []
        self.counts = defaultdict(int)

    def add(self, sev, obj, field, check, msg):
        self.items.append({"severity": sev, "object": obj, "field": field, "check": check, "message": msg})
        self.counts[sev] += 1

    def error(self, *a): self.add("ERROR", *a)
    def warn(self, *a):  self.add("WARN", *a)
    def info(self, *a):  self.add("INFO", *a)


def normalize_type(raw: str):
    t = str(raw or "").strip()
    if t in FORMULA_TYPES:
        return t, True
    # normalize a few aliases: Boolean is the sheet's spelling for Checkbox, and
    # Text Area / Rich Text Area / Rollup summary are the type-dropdown spellings
    # of TextArea / Html / Summary. Keyed like generate_xml._key (no spaces/case).
    aliases = {"formuladatetime": "Formula Date/Time", "boolean": "Checkbox",
               "textarea": "TextArea", "richtextarea": "Html",
               "rollupsummary": "Summary", "roll-upsummary": "Summary",  # Updated by Divakar N — 2026-09-23
               "autonumber": "AutoNumber"}
    k = t.lower().replace(" ", "")
    if k in aliases:
        return aliases[k], False
    # Updated by Divakar N — 2026-10-21. Why: TINETAccount row 'CheckBox' was
    # rejected as an unknown type although generate_xml keys types case-insensitively.
    by_lower = {name.lower(): name for name in RULES}
    return by_lower.get(k, t), False


def validate(
    rows: list[dict],
    rep: Report,
    org_objects: set[str] | None = None,
    org_child_rels: dict[str, list[dict]] | None = None,
    org_relnames_failed: bool = False,
    org_picklists: dict | None = None,
    org_field_types: dict | None = None,
) -> None:
    # object set present in this deploy (for dependency hints)
    deploy_objects = {r.get("Object API Name", "").strip()
                      for r in rows if r.get("_type") == "object_meta"}
    deploy_objects |= {r.get("Object API Name", "").strip()
                       for r in rows if r.get("Field API Name", "").strip()}
    # case-insensitive view of what's in THIS deploy set (SF API names are
    # case-insensitive, so parents created in this same batch count as present).
    deploy_objects_lc = {o.lower() for o in deploy_objects if o}

    seen: dict[tuple, int] = defaultdict(int)
    # Count Lookup + MasterDetail rows per object to enforce the 40 custom-
    # relationship-per-object hard limit (see KB 2026-08-20).
    rel_count: dict[str, int] = defaultdict(int)

    # Lookup/MasterDetail field APIs per object, for the formula first-hop check.
    rel_fields: dict[str, set[str]] = defaultdict(set)
    rel_targets: dict[tuple[str, str], str] = {}
    batch_relnames: dict[tuple[str, str], tuple[str, str]] = {}
    obj_field_apis: dict[str, set[str]] = defaultdict(set)
    for r in rows:
        if r.get("_type") == "object_meta":
            continue
        obj_field_apis[(r.get("Object API Name", "").strip()
                        or r.get("_SheetName", "").strip())].add(
            r.get("Field API Name", "").strip().lower())
        _t = (normalize_type(r.get("Data Type", "").strip()) or ("",))[0]
        if _t in ("Lookup", "MasterDetail"):
            _obj = (r.get("Object API Name", "").strip()
                    or r.get("_SheetName", "").strip())
            _fapi = r.get("Field API Name", "").strip().lower()
            rel_fields[_obj].add(_fapi)
            rel_targets[(_obj, _fapi)] = str(r.get(G) or "").strip().lower()

    for r in rows:
        if r.get("_type") == "object_meta":
            obj = r.get("Object API Name", "").strip()
            if not obj:
                rep.error(r.get("Object Label", "?"), "-", "object.api", "Object API Name is blank")
            elif is_standard_object(obj):
                # Updated by Divakar N — 2026-09-23. Why: custom fields (incl.
                # Rollup summary) can land on standard objects such as Account.
                # Requiring __c here parked the Account roll-up test as ERROR.
                pass
            elif not (API_NAME_RE.match(obj) and obj.endswith("__c")):
                rep.error(obj, "-", "object.api", f"Object API '{obj}' invalid (must match API name regex and end __c)")
            # Updated by Divakar N — 2026-11-05. Why: ERPPJ-1150 User tab has a
            # blank Name label and an object EN copied from another tab; a
            # standard object's own labels are Salesforce-owned and never deployed.
            if obj and is_standard_object(obj):
                continue
            obj_en = r.get("Object Label (EN)", "")
            name_en = r.get("Name Field Label (EN)", "")
            # Object EN is the header-row Field Label (EN). If it exceeds 40
            # chars, the org package uses Name EN; header cells are not rewritten.
            if nonblank(r.get("Object Label", "")):
                _validate_object_header_en(
                    rep, obj, r.get("Object Label", ""), obj_en,
                    r.get("Name Field Label", ""), name_en)
            _validate_translation(rep, obj, "Name", r.get("Name Field Label", ""),
                                  name_en, kind="name")
            continue

        obj = r.get("Object API Name", "").strip() or r.get("_SheetName", "").strip()
        fapi = r.get("Field API Name", "").strip()
        label = r.get("Field Label", "").strip()
        raw_type = r.get("Data Type", "").strip()
        loc = fapi or label or "(row)"

        # Standard fields are never packaged. Identify them before schema
        # checks so a blank Data Type on RecordType / OwnerId / etc. is not
        # a blocker (the DD sheet often leaves type empty on system rows).
        is_standard = bool(fapi) and (
            fapi in KNOWN_STANDARD or not fapi.endswith("__c")
        )

        # ---- schema: required trio -------------------------------------- #
        if not label:
            rep.error(obj, loc, "field.label", "Field Label (label) is missing")
        if not raw_type and not is_standard:
            rep.error(obj, loc, "field.type", "Data Type (type) is missing")
        if not fapi:
            # Updated by Divakar N — 2026-09-21. Blank API is NOT a blocker.
            # Why: the client ships labels/types first; GDC generates TI_Fnt_
            # names and writes them back to col D. ERROR here parked whole new
            # tabs (TradeTermsAppDetail). Remaining type checks still run.
            rep.info(obj, loc, "field.api.generate",
                     f"Field API Name (fullName) is blank for '{label}' — not a "
                     f"blocker; generate TI_Fnt_<Component>__c and write back to col D")
        else:
            if not fapi.endswith("__c"):
                if fapi in KNOWN_STANDARD:
                    # Known Salesforce-owned names — not an error. INFO so the
                    # log stays honest without looking like an API-name problem.
                    rep.info(obj, loc, "field.api",
                             f"'{fapi}' is a standard field — not tool-deployed")
                else:
                    rep.warn(obj, loc, "field.api",
                             f"'{fapi}' has no __c suffix — treated as standard field, not deployed")
            core = fapi[:-3] if fapi.endswith("__c") else fapi
            if not API_NAME_RE.match(core) or "__" in core:
                rep.error(obj, loc, "field.api", f"'{fapi}' is not a valid Salesforce API name")
            if len(core) > 40:
                rep.error(obj, loc, "field.api", f"'{fapi}' exceeds 40 chars")
            # duplicate detection (per object)
            seen[(obj, fapi)] += 1
            if seen[(obj, fapi)] == 2:
                rep.error(obj, fapi, "field.duplicate", f"Duplicate field API '{fapi}' in object '{obj}'")

        if fapi.endswith("__c"):
            _validate_translation(rep, obj, fapi, label, r.get("Field Label (EN)", ""), kind="field")

        # Standard fields (no __c) are never deployed by generate_xml, so their
        # field-definition columns (Length, referenceTo, Precision, etc.) are
        # irrelevant. Skip the rest to avoid spurious required.column errors on
        # sheet-provided system rows (OwnerId, CreatedDate, LastModifiedDate…).
        if fapi and not fapi.endswith("__c"):
            continue

        if not raw_type:
            continue

        sf_type, is_formula = normalize_type(raw_type)

        ref_col = "col H"
        # Updated by Divakar N — 2026-10-08. Why: blank col H reads the labels
        # from col G; the generated Label:ApiName list must be written to H.
        if r.get("_picklistSource") == "G":
            rep.warn(obj, loc, "picklist.source",
                     "col H blank — picklist labels read from col G; generate API "
                     "names (picklist_api.py) and write Label:ApiName to col H")

        # ---- existing field: the org owns relationshipName --------------- #
        # Updated by Divakar N — 2026-10-06. Why: inventing col X for a field
        # already in the org can fail the deploy or rename a child
        # relationship other code depends on.
        org_reln = (org_relationship_name(obj, fapi)
                    if sf_type in ("Lookup", "MasterDetail", "Hierarchy") else None)
        if org_reln:
            sheet_reln = str(r.get("Relationship Name") or "").strip()
            if not sheet_reln:
                rep.info(obj, loc, "relationshipname.org",
                         f"col X blank; field exists in org → using org "
                         f"relationshipName='{org_reln}' (write back to col X)")
            elif sheet_reln != org_reln:
                rep.warn(obj, loc, "relationshipname.org",
                         f"col X '{sheet_reln}' differs from org relationshipName "
                         f"'{org_reln}' — keeping the org value (write back to col X)")
            r["Relationship Name"] = org_reln
            r["_relname_from_org"] = org_reln

        # ---- blank col X: auto-fill from the lookup object --------------- #
        # NEW fields only (existing fields were resolved from the org above).
        # If relationshipName is blank but H (or G, when H is blank) has a
        # valid referenceTo, derive X from the field API (object-scoped for
        # shared parents). This is NOT a blocker. Blank X + blank H + blank G
        # remains an ERROR (caught in the ref block below).
        if sf_type in ("Lookup", "MasterDetail", "Hierarchy") and not nonblank(r.get("Relationship Name")):
            href = str(r.get(G) or r.get("Reference To") or "").strip()
            if org_relnames_failed and fapi.endswith("__c"):
                rep.error(obj, loc, "relationshipname.org",
                          "col X blank and the org's relationshipName could not be "
                          "read — not deriving one (the field may already exist)")
            elif href and fapi.endswith("__c"):
                derived = derive_relationship_name(fapi, obj, href)
                if derived:
                    r["Relationship Name"] = derived
                    r["_relname_autofilled"] = derived
                    rep.info(obj, loc, "relationshipname.autofill",
                             f"col X blank; {ref_col} has lookup object '{href}' → "
                             f"auto-filled relationshipName='{derived}' (write back to sheet)")

        # ---- unknown type ----------------------------------------------- #
        if is_formula:
            required = FORMULA_TYPES[raw_type]
            rule = {"required": required, "forbidden": [] if raw_type in FORMULA_NUMERIC
                    else ["Precision", "Scale"], "optional": []}
        elif sf_type in RULES:
            rule = RULES[sf_type]
        elif raw_type.lower() == "address":
            # Updated by Divakar N — 2026-10-29. Why: a compound Address field
            # is created in Setup; the generator skips it, so only an existing
            # org Address field may stay on the sheet.
            org_t = (org_field_types or {}).get((obj, fapi), "")
            if org_t.lower() == "address":
                rep.info(obj, loc, "field.type.address",
                         "Address field already in the org — not tool-deployed")
            elif org_field_types is None:
                rep.error(obj, loc, "field.type.address",
                          "Address field: run with --target-org to confirm it exists in the org")
            else:
                rep.error(obj, loc, "field.type.address",
                          f"Address field not in the org (org type '{org_t or 'none'}') — "
                          "create it in Setup; the generator cannot build Address fields")
            continue
        else:
            rep.error(obj, loc, "field.type", f"Unknown/unsupported Data Type '{raw_type}'")
            continue

        # Number/Currency/Percent (+ formula): derive Precision/Scale from col F
        # ("12, 3" → precision 15, scale 3). T/U are not client-required.
        # Updated by Divakar N.
        num_size_failed = False
        if is_numeric_sheet_type(raw_type) or (
                is_formula and sf_type in ("Number", "Currency", "Percent")):
            st = apply_numeric_size(r)
            if st.get("ok") is False:
                rep.error(obj, loc, "numeric.size", st["error"])
                num_size_failed = True

        # ---- required columns ------------------------------------------- #
        for col in rule.get("required", []):
            if not nonblank(r.get(col)):
                # Empty FORMULA body is not a hard blocker — generate_xml injects a
                # return-type-appropriate dummy (see sf-deploy-delta-and-blockers).
                if is_formula and col == G:
                    rep.warn(obj, loc, "formula.autofill",
                             f"{raw_type}: formula body (Column H) is empty — generator "
                             f"injects a dummy; real formula supplied later")
                    continue
                # Updated by Divakar N — 2026-09-23.
                # Why: unlike empty formulas, a blank roll-up H cannot be dummy-filled
                # (operation/child/field are a business decision). Hard blocker.
                if sf_type == "Summary" and col == G:
                    rep.error(obj, loc, "summary.parse",
                              "Rollup summary col H (設定値) is empty — fill "
                              "Operation:COUNT / Child:… (no space after colon; never col G)")
                    continue
                if col == G and rule.get("picklist") and (
                        nonblank(r.get("Global Value Set")) or r.get("_gvsUnresolved")):
                    continue
                # LongTextArea/Html visibleLines blank — generator defaults to 3 / 25.
                if col == "Visible Lines" and sf_type in ("LongTextArea", "Html"):
                    vl_default = "25" if sf_type == "Html" else "3"
                    rep.warn(obj, loc, "visiblelines.default",
                             f"{raw_type}: 'Visible Lines' empty — generator defaults to {vl_default}")
                    continue
                # Precision/Scale empty after apply_numeric_size already reported.
                if num_size_failed and col in ("Precision", "Scale"):
                    continue
                rep.error(obj, loc, "required.column",
                          f"{raw_type}: required column '{col}' is empty")

        # ---- formula body hygiene --------------------------------------- #
        if is_formula:
            fbody = str(r.get(G) or "").strip()
            if "__c__r" in fbody:
                rep.error(obj, loc, "formula.rel",
                          "formula uses '__c__r' — relationship is FieldApi__r "
                          "(no extra __c before __r)")
            # Updated by Divakar N — 2026-10-12. Why: BookingRequestDetail
            # formulas used TI_Fnt_SDS__r while the lookup was named
            # TI_Fnt_OfficialItemNameEN__c → "Field TI_Fnt_SDS__r does not exist".
            # ERPPJ-1097: SpecificBulkLicense wrote TI_Fnt_Buyer__c.Field__c →
            # "Field TI_Fnt_Buyer__c does not exist".
            for m in FORMULA_LOOKUP_DOT_RE.finditer(fbody):
                if m.group(1).lower() in rel_fields.get(obj, set()):
                    rep.error(obj, loc, "formula.rel.dot",
                              f"formula uses '{m.group(1)}.' — a lookup is traversed "
                              f"as '{m.group(1)[:-3]}__r.'")
            # ERPPJ-1116: BankMaster formula body was just `CountryList__c` (an
            # object name) → "Field CountryList__c does not exist".
            for tok in sorted(set(FORMULA_BARE_FIELD_RE.findall(fbody))):
                if tok.lower() not in obj_field_apis.get(obj, set()):
                    rep.error(obj, loc, "formula.field",
                              f"formula references '{tok}', which is not a field "
                              f"on {obj} in the sheet")
            for hop in sorted(set(FORMULA_FIRST_HOP_RE.findall(fbody))):
                if f"{hop}__c".lower() not in rel_fields.get(obj, set()):
                    rep.error(obj, loc, "formula.rel.field",
                              f"formula uses '{hop}__r.' but this object has no "
                              f"Lookup/MasterDetail field '{hop}__c' — rename the "
                              f"lookup or fix the path")
            # Updated by Divakar N — 2026-10-12. Why: QuoteCostLineItem text
            # formulas returned TI_Fnt_CostItemCode__r.ExpensesItem*Category__c
            # bare; those parent fields are picklists, so the dry-run failed
            # with "Picklist fields are only supported in certain functions".
            for m in FORMULA_PARENT_FIELD_RE.finditer(fbody):
                hop, pfield = m.group(1), m.group(2)
                parent = rel_targets.get((obj, f"{hop}__c".lower()), "")
                if pfield.lower() not in PARENT_PICKLISTS.get(parent, set()):
                    continue
                if PICKLIST_SAFE_FN_RE.search(fbody[:m.start()]):
                    continue
                rep.error(obj, loc, "formula.picklist",
                          f"formula reads picklist '{parent}.{pfield}' via "
                          f"'{hop}__r' without TEXT()/ISPICKVAL()/CASE() — wrap it, "
                          f"e.g. TEXT({hop}__r.{pfield})")
            ph = fbody.strip().strip('"').strip("'")
            ret = raw_type[7:].strip() if raw_type.lower().startswith("formula") else sf_type
            if ph in {"TDB", "TBD"} and ret.lower() not in {"text", ""}:
                rep.warn(obj, loc, "formula.placeholder",
                         f"{raw_type} body {fbody!r} is not valid for return type "
                         f"{ret}; generator injects a type dummy")

        # ---- AutoNumber display format ---------------------------------- #
        if sf_type == "AutoNumber" and not is_formula and nonblank(r.get(G)):
            fmt = str(r.get(G)).strip()
            m = re.search(r"\{(\d+)\}", fmt)
            if not m:
                rep.error(obj, loc, "autonumber.format",
                          f"display format {fmt!r} has no {{0…}} number token")
            elif not re.fullmatch(r"(?:[^{}]|\{(?:YYYY|YY|MM|DD)\})*\{\d{1,10}\}"
                                  r"(?:[^{}]|\{(?:YYYY|YY|MM|DD)\})*", fmt):
                rep.error(obj, loc, "autonumber.format",
                          f"display format {fmt!r} must have exactly one {{0…}} number token "
                          "of 1-10 digits; other braces may only be {YYYY} {YY} {MM} {DD}")
            elif len(fmt) > 30:
                rep.error(obj, loc, "autonumber.format",
                          f"display format {fmt!r} is {len(fmt)} characters; 30 is the maximum")
            elif set(m.group(1)) != {"0"}:
                rep.info(obj, loc, "autonumber.start",
                         f"display format {fmt!r}: deployed as "
                         f"{{{'0' * len(m.group(1))}}} starting at {int(m.group(1)) or 1}")

        # ---- forbidden columns ------------------------------------------ #
        for col in rule.get("forbidden", []):
            if nonblank(r.get(col)):
                rep.warn(obj, loc, "forbidden.column",
                         f"{raw_type}: column '{col}'='{r.get(col)}' should be blank for this type")

        # ---- numeric constraints ---------------------------------------- #
        if (rule.get("num") or raw_type in FORMULA_NUMERIC) and not num_size_failed:
            _check_int(rep, obj, loc, "Precision", r.get("Precision"), 1, 18)
            _check_int(rep, obj, loc, "Scale", r.get("Scale"), 0, 17)
            p, s = _to_int(r.get("Precision")), _to_int(r.get("Scale"))
            if p is not None and s is not None and s >= p:
                rep.error(obj, loc, "constraint",
                          f"scale ({s}) must be < precision ({p}) — Salesforce rejects "
                          f"'Precision must be greater than scale'")
        if "len" in rule:
            col, lo, hi = rule["len"]
            _check_int(rep, obj, loc, col, r.get(col), lo, hi)
        if "vl" in rule:
            col, lo, hi = rule["vl"]
            if nonblank(r.get(col)):
                _check_int(rep, obj, loc, col, r.get(col), lo, hi)

        # Salesforce TextArea is always 255 chars; Metadata API rejects <length>.
        if sf_type == "TextArea" and nonblank(r.get("Length")):
            rep.info(obj, loc, "textarea.length",
                     "TextArea length is fixed at 255 by Salesforce; generator omits <length>")

        # ---- unique / externalId gate ----------------------------------- #
        for col in ("Unique", "External ID"):
            if truthy(r.get(col)) and sf_type not in UNIQUE_EXTID_TYPES:
                rep.error(obj, loc, "unique.gate",
                          f"'{col}'=true not allowed for type '{sf_type}'")

        # ---- required-flag applicability -------------------------------- #
        NO_REQUIRED = {"Checkbox", "AutoNumber", "Summary", "LongTextArea",
                       "Html", "Location", "MultiselectPicklist", "MasterDetail"}
        if truthy(r.get("Required")):
            if sf_type in NO_REQUIRED:
                rep.info(obj, loc, "required.gate",
                         f"'Required'=true is invalid for type '{sf_type}'; the "
                         f"generator drops it (enforce via page layout / validation rule)")
            elif sf_type == "Lookup" and not str(r.get("Delete Constraint") or "").strip():
                ref = str(r.get(G) or r.get("Reference To") or "").strip()
                if ref in {"User", "Group", "RecordType", "Profile"}:
                    # Updated by Divakar N — 2026-10-27. Why: ERPPJ-1116 — the
                    # client asked for this to show on the sheet (AH), not only
                    # in the console; INFO is never written back.
                    report = rep.warn if fapi.endswith("__c") else rep.info
                    report(obj, loc, "required.lookup",
                           f"Lookup to '{ref}' cannot be required in Salesforce "
                           f"(only 'clear the value' is allowed on delete, and that "
                           f"cannot be combined with required). Deployed as optional — "
                           f"remove ○ from 'required' and enforce with a validation rule")
                else:
                    rep.info(obj, loc, "required.lookup",
                             "Required Lookup — generator adds "
                             "<deleteConstraint>Restrict</deleteConstraint> "
                             "(Salesforce requires a delete constraint for required lookups)")

        # ---- boolean-ish sanity ----------------------------------------- #
        for col in ("Required", "Unique", "External ID", "Track History", "Restricted"):
            v = str(r.get(col) or "").strip().lower()
            if v and v not in BOOLEANISH:
                rep.warn(obj, loc, "boolean", f"'{col}'='{r.get(col)}' is not a boolean-ish value")

        # ---- checkbox default ------------------------------------------- #
        if rule.get("checkbox"):
            dv = str(r.get("Default Value") or "").strip().lower()
            if dv and dv not in {"true", "false"}:
                rep.error(obj, loc, "checkbox.default",
                          f"Checkbox Default Value must be TRUE/FALSE (got '{r.get('Default Value')}')")
            elif not dv:
                # Empty is fine: generate_xml emits <defaultValue>false</defaultValue>.
                rep.info(obj, loc, "checkbox.default",
                         "Checkbox has no Default Value; generator defaults it to false")

        # ---- picklist values -------------------------------------------- #
        if rule.get("picklist"):
            gval = str(r.get(G) or "").strip()
            gvs = str(r.get("Global Value Set") or "").strip()
            if r.get("_gvsUnresolved"):
                rep.error(obj, loc, "picklist.gvs",
                          f"Col H refers to global picklist '{r['_gvsUnresolved']}', which the "
                          "tool cannot map to a GlobalValueSet API name — add it to "
                          "GVS_BY_LABEL in fetch_sheet.py or write the API name")
            if not gvs and gval:
                # split exactly like generate_xml (picklist_api.split_entry), so
                # the guards see what will actually be parsed.
                norm = gval.translate({0xFF1B: ord(";")})
                if "；" in gval:
                    rep.warn(obj, loc, "picklist.fullwidth",
                             "Full-width '；' detected in picklist values; "
                             "read as ';' at generate time")
                if "," in norm and ";" not in norm and "\n" not in norm:
                    rep.warn(obj, loc, "picklist.delim",
                             "Picklist values look comma-separated; delimiter must be ';' "
                             "or a line break")
                entries = split_values(gval)
                if not entries:
                    rep.error(obj, loc, "picklist.values", "Picklist has no usable values")
                seen_api: set[str] = set()
                seen_label: set[str] = set()
                for e in entries:
                    # count colons that are NOT escaped as "\:"; a full-width '：'
                    # only separates when there is no half-width ':'
                    unescaped = e.replace("\\:", "")
                    if ":" not in unescaped:
                        unescaped = unescaped.replace("：", ":")
                    ncolon = unescaped.count(":")
                    if ncolon >= 2:
                        rep.warn(obj, loc, "picklist.colon",
                                 f"Value '{e}' has multiple ':' — only the first splits "
                                 "Label:ApiName; escape a literal label colon as '\\:'")
                    if ncolon >= 1:
                        label, api = split_entry(e)
                        api = api or ""
                        if not api:
                            rep.error(obj, loc, "picklist.api_empty",
                                      f"Value '{e}' has an empty ApiName after ':'")
                        # Updated by Divakar N — 2026-10-12. Why: QuoteLineItemBreakdown
                        # row 23 had 'SeaBaseFreight:海上BASE FREIGHT(入力用)'; it would
                        # deploy English labels with Japanese API names.
                        elif API_NAME_RE.match(label) and not api.isascii():
                            rep.error(obj, loc, "picklist.reversed",
                                      f"Value '{e}' looks reversed (ApiName:Label): the "
                                      f"label would be '{label}' and the API name '{api}'. "
                                      f"Write it as '{api}:{label}'")
                    else:
                        label = api = e.replace("\\:", ":")
                        if not api.isascii():
                            rep.warn(obj, loc, "picklist.nonascii_api",
                                     f"Value '{label}' has no ApiName; its stored value "
                                     f"becomes non-ASCII '{api}'. Use 'Label:ApiName' for an ASCII code.")
                    key = api.lower()
                    if key in seen_api:
                        rep.error(obj, loc, "picklist.dup",
                                  f"Duplicate picklist ApiName '{api}'")
                    seen_api.add(key)
                    # Salesforce also rejects duplicate value LABELS ("Duplicate label: …").
                    lkey = label.strip().lower()
                    if lkey in seen_label:
                        rep.error(obj, loc, "picklist.dup_label",
                                  f"Duplicate picklist label '{label}' (ApiName '{api}') — "
                                  "Salesforce requires unique value labels")
                    seen_label.add(lkey)
                    # Updated by Divakar N — 2026-10-21. Why: ERPPJ-1113 Class2
                    # dry-run Duplicate label — sheet renamed live values (AAA→AA4).
                    if org_picklists is not None:
                        org_api = (org_picklists.get((obj, loc)) or {}).get(lkey)
                        if org_api and api and org_api != api:
                            rep.error(
                                obj, loc, "picklist.org_api",
                                f"label '{label}' already exists in the org as "
                                f"ApiName '{org_api}' (sheet '{api}'). Salesforce "
                                f"cannot rename picklist APIs and rejects Duplicate "
                                f"label — write the org ApiName back to col H")

        # ---- dependency: reference target ------------------------------- #
        if rule.get("ref"):
            # Count this relationship toward the object's 40-relationship budget.
            rel_count[obj] += 1
            ref = str(r.get(G) or r.get("Reference To") or "").strip()
            if not ref:
                # Updated by Divakar N — 2026-10-08. Col G is never read.
                rep.error(obj, loc, "dependency.ref",
                          f"{raw_type}: referenceTo is empty in col H (設定値)")
            elif not API_NAME_RE.match(ref):
                rep.error(obj, loc, "dependency.ref",
                          f"{raw_type}: referenceTo '{ref}' is not a valid API name "
                          f"(placeholder like 要確認?)")
            elif ref.endswith("__c") and ref not in deploy_objects:
                # Live org-existence check when --target-org was supplied. SF API
                # names are case-insensitive, so compare lowercased. A referenceTo
                # that is neither in this deploy batch nor in the org is a hard
                # blocker (this is exactly how TI_Fnt_BusinessPartner__c /
                # TI_Fnt_PaymentTerms__c slipped past validation — see KB 2026-08-26).
                if org_objects is not None:
                    if ref.lower() not in org_objects and ref.lower() not in deploy_objects_lc:
                        twin = ref[:-3] + "Master__c"  # common shorthand: X__c vs XMaster__c
                        hint = (f" — did you mean '{twin}'?"
                                if twin.lower() in org_objects else "")
                        rep.error(obj, loc, "dependency.ref.org",
                                  f"referenceTo '{ref}' does NOT exist in the target org "
                                  f"and is not created in this deploy{hint}")
                else:
                    rep.info(obj, loc, "dependency.ref",
                             f"referenceTo '{ref}' is a custom object not in this deploy set — ensure it exists in the target org")
            # relationshipName: auto-filled above when H has the lookup
            # object. Only flag when X and H are both blank (cannot derive).
            if sf_type in ("Lookup", "MasterDetail") and not nonblank(r.get("Relationship Name")):
                if not ref:
                    rep.error(obj, loc, "dependency.relationshipname",
                              f"{sf_type}: Relationship Name (col X) is blank AND "
                              f"col H has no lookup object — cannot auto-fill")
                elif not fapi.endswith("__c"):
                    # Updated by Divakar N — API still blank: not a blocker.
                    # relationshipName is filled after the API name is generated.
                    pass
                else:
                    rep.error(obj, loc, "dependency.relationshipname",
                              f"{sf_type}: Relationship Name is blank and could "
                              f"not be derived from {ref_col} '{ref}'")
            reln = str(r.get("Relationship Name") or "").strip()
            if (
                reln
                and not r.get("_relname_from_org")
                and fapi.startswith("TI_Fnt_")
                and obj.startswith("TI_Logi_")
            ):
                qualified = qualify_relationship_name(reln, obj)
                if qualified != reln:
                    r["Relationship Name"] = qualified
                    r["_relname_qualified"] = qualified
                    rep.info(
                        obj, loc, "relationshipname.qualify",
                        f"relationshipName '{reln}' collides with the TI_Fnt_ twin "
                        f"on parent '{ref}' → '{qualified}' (write back to col X)",
                    )
                    reln = qualified
            if reln and len(reln) > MAX_RELATIONSHIP_NAME:
                rep.error(obj, loc, "relationshipname.length",
                          f"{sf_type}: Relationship Name '{reln}' is {len(reln)} "
                          f"chars (Salesforce max {MAX_RELATIONSHIP_NAME})")
            if reln and ref:
                key = (ref.lower(), reln.lower())
                prev = batch_relnames.get(key)
                if prev and prev != (obj, fapi):
                    rep.error(obj, loc, "relationshipname.batchdup",
                              f"child relationship '{reln}' on {ref} is also used by "
                              f"{prev[0]}.{prev[1]} in this deploy")
                batch_relnames.setdefault(key, (obj, fapi))
            # Live: child relationship names are unique on the PARENT. A name
            # already used by another object (e.g. TI_Fnt_TINETAccount vs
            # TI_Logi_TINETAccount) fails deploy.
            if reln and org_child_rels and ref:
                for cr in org_child_rels.get(ref.lower(), []):
                    if cr.get("relationshipName", "").lower() != reln.lower():
                        continue
                    child_obj = (cr.get("childSObject") or "").strip()
                    child_field = (cr.get("field") or "").strip()
                    same_field = (
                        child_obj == obj and child_field == fapi
                    )
                    if same_field:
                        continue
                    suggested = qualify_relationship_name(reln, obj)
                    extra = (
                        f" — use '{suggested}'"
                        if suggested and suggested != reln
                        else ""
                    )
                    rep.error(
                        obj, loc, "relationshipname.collision",
                        f"child relationship '{reln}' already exists on {ref} "
                        f"(from {child_obj}.{child_field}){extra}",
                    )
                    break

        # ---- deleteConstraint (Lookup only) ----------------------------- #
        dc_raw = str(r.get("Delete Constraint") or "").strip()
        if dc_raw:
            dc = dc_raw.lower()
            if dc not in {"setnull", "restrict", "cascade", "cascadedelete"}:
                rep.error(obj, loc, "deleteconstraint.value",
                          f"Delete Constraint '{dc_raw}' invalid (SetNull / Restrict / Cascade)")
            elif sf_type != "Lookup":
                rep.warn(obj, loc, "deleteconstraint.type",
                         f"Delete Constraint set on non-Lookup type '{sf_type}' — ignored "
                         f"(only Lookup uses deleteConstraint)")
            else:
                ref = str(r.get(G) or r.get("Reference To") or "").strip()
                if dc in {"restrict", "cascade", "cascadedelete"} and \
                        ref in {"User", "Group", "RecordType", "Profile"}:
                    rep.error(obj, loc, "deleteconstraint.ref",
                              f"Delete Constraint '{dc_raw}' is not allowed on a Lookup to "
                              f"'{ref}' (only SetNull) — use SetNull or leave blank")
                if dc == "setnull" and truthy(r.get("Required")):
                    rep.info(obj, loc, "deleteconstraint.required",
                             "SetNull lookup cannot be required — generator drops 'required'")

        # ---- summary (col H Operation:COUNT / Child:…; no space after colon) ---- #
        # Updated by Divakar N — 2026-09-23.
        # Why: parse H so SUM without Field / bad operators / prose-in-H fail
        # at STEP 0 instead of deploying a type-only Summary field.
        if rule.get("summary"):
            parsed = apply_rollup_h(r) or r.get("_rollup") or {}
            hval = str(r.get(G) or "").strip()
            if not hval:
                # Blank H already reported as summary.parse in required-column.
                pass
            else:
                for err in parsed.get("errors") or []:
                    if "empty" in err and not hval:
                        continue
                    check = "summary.parse"
                    if err.startswith("missing Operation") or "Operation '" in err:
                        check = "summary.op"
                    elif err.startswith("missing Child") or "Child must" in err:
                        check = "summary.child"
                    elif "Field:" in err or "Field must" in err:
                        check = "summary.field"
                    elif "Condition" in err or "filter field" in err or "valueField" in err:
                        check = "summary.filter"
                    elif "max" in err.lower():
                        check = "summary.filter.limit"
                    rep.error(obj, loc, check, err)
                for warn in parsed.get("warnings") or []:
                    rep.warn(obj, loc, "summary.field", warn)

    # ---- per-object 40 custom-relationship limit (post-scan) ------------- #
    # Salesforce hard cap: an object may have at most 40 custom relationships
    # (Lookup + MasterDetail). Only counts fields IN THIS DEPLOY SET; the deploy
    # is delta-only so this is the count being ADDED. Existing org relationships
    # are not included here — when redeploying onto a populated object, subtract
    # already-present relationships from the 40 budget before trusting this.
    for obj, n in sorted(rel_count.items()):
        if n > 40:
            rep.error(obj, "-", "relationship.limit",
                      f"{n} custom relationships (Lookup+MasterDetail) in this deploy "
                      f"set exceeds the 40-per-object hard limit — park or defer "
                      f"{n - 40} relationship field(s)")
        elif n > 35:
            rep.warn(obj, "-", "relationship.limit",
                     f"{n} custom relationships approaching the 40-per-object limit "
                     f"(only counts this deploy set; org may already hold some)")


def collect_reference_targets(rows: list[dict], include_standard: bool = False) -> set[str]:
    """Distinct referenceTo values used by Lookup/MasterDetail rows: custom
    (__c) only, or every parent (User, Account, …) with ``include_standard``."""
    refs: set[str] = set()
    for r in rows:
        if r.get("_type") == "object_meta":
            continue
        raw_type = str(r.get("Data Type") or "").strip()
        if raw_type not in ("Lookup", "MasterDetail"):
            continue
        ref = str(r.get(G) or r.get("Reference To") or "").strip()
        if not API_NAME_RE.match(ref):
            continue
        if ref.endswith("__c") or include_standard:
            refs.add(ref)
    return refs


def org_custom_field_types(objs: list[str], org: str) -> dict[tuple[str, str], str]:
    """{(obj, field): type} for custom fields already in the org (readMetadata)."""
    from translate_enrich import load_token, read_metadata
    tok = load_token(org)
    recs = read_metadata("CustomObject", objs, tok["accessToken"],
                         tok["instanceUrl"].rstrip("/"), tok["apiVersion"])
    out: dict[tuple[str, str], str] = {}
    for rec, _raw in recs:
        obj = (rec.findtext("fullName") or "").strip()
        for f in rec.iter("fields"):
            out[(obj, (f.findtext("fullName") or "").strip())] = (f.findtext("type") or "").strip()
    return out


def query_org_objects(target_org: str, ref_names: set[str]) -> set[str]:
    """Return the lowercased set of the given object API names that EXIST in the
    org (Tooling-independent EntityDefinition). Also includes the *Master twins so
    a 'did you mean' hint can be offered. Case-insensitive."""
    want = set(ref_names)
    # include the Master twin of each ref so we can suggest it if the base is absent
    want |= {r[:-3] + "Master__c" for r in ref_names if r.endswith("__c")}
    if not want:
        return set()
    present: set[str] = set()
    names = sorted(want)
    # chunk to keep the IN() clause reasonable
    for i in range(0, len(names), 190):
        chunk = names[i:i + 190]
        inlist = "','".join(chunk)
        q = (f"SELECT QualifiedApiName FROM EntityDefinition "
             f"WHERE QualifiedApiName IN ('{inlist}')")
        try:
            cp = subprocess.run(
                ["sf", "data", "query", "--target-org", target_org, "--json", "--query", q],
                capture_output=True, text=True, timeout=120)
            data = json.loads(cp.stdout or "{}")
            if data.get("status") not in (0, None):
                msg = data.get("message") or data.get("name") or "unknown"
                print(f"⚠️  org referenceTo check failed ({msg}) — skipping org existence "
                      f"validation for this run.")
                return None  # signal: could not check (do not fail-closed)
            for rec in data.get("result", {}).get("records", []) or []:
                present.add(str(rec.get("QualifiedApiName", "")).lower())
        except Exception as e:
            print(f"⚠️  org referenceTo check failed ({e}) — skipping org existence "
                  f"validation for this run.")
            return None  # signal: could not check (do not fail-closed)
    return present


def query_parent_child_relationships(
    target_org: str, parents: set[str]
) -> dict[str, list[dict]]:
    """childRelationships on each lookup parent (sobject describe).

    Used to catch relationshipName collisions that are unique on the PARENT,
    not on the child object (TI_Fnt_TINETAccount vs TI_Logi_TINETAccount).
    """
    out: dict[str, list[dict]] = {}
    for parent in sorted(p for p in parents if p):
        try:
            cp = subprocess.run(
                [
                    "sf", "sobject", "describe",
                    "--sobject", parent,
                    "--target-org", target_org,
                    "--json",
                ],
                capture_output=True, text=True, timeout=120,
            )
            data = json.loads(cp.stdout or "{}")
            result = data.get("result") or data
            rels = []
            for cr in result.get("childRelationships") or []:
                rn = bare_relationship_name(str(cr.get("relationshipName") or ""))
                if not rn:
                    continue
                rels.append({
                    "relationshipName": rn,
                    "childSObject": str(cr.get("childSObject") or "").strip(),
                    "field": str(cr.get("field") or "").strip(),
                })
            out[parent.lower()] = rels
        except Exception as e:
            print(f"⚠️  child-relationship describe failed for {parent} ({e})")
    return out


def query_parent_picklists(target_org: str, parents: set[str]) -> None:
    """Fill PARENT_PICKLISTS from Tooling FieldDefinition (sobject describe
    omits fields the running user has no FLS on)."""
    names = sorted(p for p in parents if p)
    for i in range(0, len(names), 100):
        inlist = "','".join(names[i:i + 100])
        q = (f"SELECT EntityDefinition.QualifiedApiName, QualifiedApiName FROM FieldDefinition "
             f"WHERE EntityDefinition.QualifiedApiName IN ('{inlist}') "
             f"AND DataType LIKE 'Picklist%'")
        try:
            cp = subprocess.run(
                ["sf", "data", "query", "--use-tooling-api", "--target-org", target_org,
                 "--json", "--query", q],
                capture_output=True, text=True, timeout=120)
            for rec in (json.loads(cp.stdout or "{}").get("result") or {}).get("records") or []:
                ent = str((rec.get("EntityDefinition") or {}).get("QualifiedApiName") or "").lower()
                PARENT_PICKLISTS.setdefault(ent, set()).add(
                    str(rec.get("QualifiedApiName") or "").lower())
        except Exception as e:
            print(f"⚠️  parent picklist lookup failed ({e}) — formula.picklist not checked")


def _to_int(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _check_int(rep, obj, loc, col, v, lo, hi):
    if not nonblank(v):
        return
    n = _to_int(v)
    if n is None:
        rep.error(obj, loc, "constraint", f"'{col}'='{v}' must be an integer")
    elif not (lo <= n <= hi):
        rep.error(obj, loc, "constraint", f"'{col}'={n} out of range [{lo}, {hi}]")


def print_log(rep: Report, total_rows: int) -> None:
    order = {"ERROR": 0, "WARN": 1, "INFO": 2}
    icon = {"ERROR": "❌", "WARN": "⚠️ ", "INFO": "ℹ️ "}
    print("=" * 72)
    print("  PRE-DEPLOYMENT VALIDATION LOG")
    print("=" * 72)
    for it in sorted(rep.items, key=lambda x: (x["object"], order[x["severity"]])):
        print(f"{icon[it['severity']]} [{it['object']}::{it['field']}] "
              f"({it['check']}) {it['message']}")
    if not rep.items:
        print("  (no issues found)")
    print("-" * 72)
    e, w, i = rep.counts["ERROR"], rep.counts["WARN"], rep.counts["INFO"]
    status = "FAIL" if e else "PASS"
    print(f"  ROWS: {total_rows}   ERRORS: {e}   WARNINGS: {w}   INFO: {i}")
    print(f"  RESULT: {status}" + ("  — deployment BLOCKED" if e else "  — safe to build package"))
    print("=" * 72)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate DD rows before packaging")
    ap.add_argument("--in", dest="inp", default="temp_updates.json")
    ap.add_argument("--json", dest="json_out", default="")
    ap.add_argument("--strict", action="store_true", help="treat WARN as blocking too")
    ap.add_argument("--target-org", default="",
                    help="if set, live-verify every Lookup/MasterDetail referenceTo "
                         "custom object EXISTS in this org (ERROR on missing, with a "
                         "'…Master__c?' hint). Offline when omitted.")
    ap.add_argument("--require-en", action="store_true",
                    help="blank English labels are ERRORs (deploy gate, after the "
                         "translation step). Default: INFO translation.pending.")
    args = ap.parse_args()
    global REQUIRE_EN
    REQUIRE_EN = args.require_en

    numeric_size_mode_banner()

    try:
        rows = json.load(open(args.inp, encoding="utf-8"))
    except FileNotFoundError:
        print(f"❌ input '{args.inp}' not found — run fetch_sheet.py first.")
        return 1

    org_objects = None
    org_child_rels = None
    org_relnames_failed = False
    org_picklists = None
    org_field_types = None
    ORG_RELNAME_CACHE.unlink(missing_ok=True)
    if args.target_org:
        own_objects = {str(r.get("Object API Name") or "").strip() for r in rows}
        print(f"🔎 reading org relationshipName for existing fields on "
              f"{len(own_objects)} object(s)…")
        org_rels = fetch_org_relationship_names(args.target_org, own_objects)
        if org_rels is None:
            org_relnames_failed = True
        else:
            save_org_relationship_names(args.target_org, org_rels)
        refs = collect_reference_targets(rows)
        if refs:
            print(f"🔎 checking {len(refs)} distinct referenceTo target(s) live "
                  f"against org '{args.target_org}'…")
            org_objects = query_org_objects(args.target_org, refs)
            query_parent_picklists(args.target_org, refs)
        # Child relationship names are unique on standard parents (User) too.
        parents = collect_reference_targets(rows, include_standard=True)
        if parents:
            print(f"🔎 checking child relationship names on {len(parents)} "
                  f"parent object(s)…")
            org_child_rels = query_parent_child_relationships(args.target_org, parents)
            save_taken(args.target_org, org_child_rels)
        print(f"🔎 reading org picklist APIs for {len(own_objects)} object(s)…")
        org_picklists = org_picklist_values(sorted(o for o in own_objects if o),
                                           args.target_org)
        if any(str(r.get("Data Type") or "").strip().lower() == "address" for r in rows):
            org_field_types = org_custom_field_types(
                sorted(o for o in own_objects if o), args.target_org)
    else:
        TAKEN_CACHE.unlink(missing_ok=True)

    rep = Report()
    validate(rows, rep, org_objects=org_objects, org_child_rels=org_child_rels,
             org_relnames_failed=org_relnames_failed, org_picklists=org_picklists,
             org_field_types=org_field_types)
    print_log(rep, len(rows))

    if args.json_out:
        json.dump({"counts": dict(rep.counts), "items": rep.items},
                  open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"  report written: {args.json_out}")

    blocking = rep.counts["ERROR"] + (rep.counts["WARN"] if args.strict else 0)
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
