#!/usr/bin/env python3
"""
extend_type_dropdown.py — add values to the column-E (データ型 / type) dropdown on
the Format tab and every object tab.

The type dropdown is an inline ONE_OF_LIST validation stored separately on each
tab, so new values must be written per tab. Only ranges that ALREADY carry the
dropdown are rewritten, which preserves each tab's validation range, strictness
and input message. Existing values keep their order; new ones are appended.

Dry-run by default (gated sheet write — preview, confirm, then --apply):

  python scripts/extend_type_dropdown.py --spreadsheet-id <ID> \
      --values "Rollup summary,Text Area,Rich Text Area,Time" [--apply]
"""
from __future__ import annotations

import argparse
import sys

sys.path.insert(0, "scripts")
from write_back import get_write_service  # noqa: E402
from sheet_config import add_spreadsheet_id_arg  # noqa: E402

TYPE_COL = 4  # column E, 0-based
FIRST_ROW = 10  # row 11, 0-based


def main() -> int:
    ap = argparse.ArgumentParser()
    add_spreadsheet_id_arg(ap)
    ap.add_argument("--values", required=True, help="comma-separated values to add")
    ap.add_argument("--tabs", help="comma-separated tab names (default: every tab that has the dropdown)")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    new_values = [v.strip() for v in args.values.split(",") if v.strip()]
    wanted = {t.strip() for t in args.tabs.split(",")} if args.tabs else None

    svc = get_write_service()
    meta = svc.spreadsheets().get(spreadsheetId=args.spreadsheet_id,
                                  includeGridData=False).execute()
    titles = [s["properties"]["title"] for s in meta["sheets"]]

    requests, report = [], []
    for title in titles:
        if wanted is not None and title not in wanted:
            continue
        grid = svc.spreadsheets().get(
            spreadsheetId=args.spreadsheet_id, ranges=[f"'{title}'!E1:E"],
            includeGridData=True,
            fields="sheets(properties(sheetId),data(rowData(values(dataValidation))))",
        ).execute()["sheets"][0]
        gid = grid["properties"]["sheetId"]
        rows = grid["data"][0].get("rowData", [])

        # collect contiguous runs sharing one ONE_OF_LIST rule
        runs: list[tuple[int, int, dict]] = []
        for i, row in enumerate(rows):
            dv = ((row.get("values") or [{}])[0]).get("dataValidation")
            if not dv or dv.get("condition", {}).get("type") != "ONE_OF_LIST":
                continue
            if runs and runs[-1][1] == i and runs[-1][2] == dv:
                runs[-1] = (runs[-1][0], i + 1, dv)
            else:
                runs.append((i, i + 1, dv))

        for start, end, dv in runs:
            if start < FIRST_ROW:
                continue
            cond = dv["condition"]
            values = [v["userEnteredValue"] for v in cond["values"]]
            missing = [v for v in new_values if v not in values]
            if not missing:
                continue
            rule = dict(dv)
            rule["condition"] = dict(cond, values=[{"userEnteredValue": v}
                                                   for v in values + missing])
            requests.append({"setDataValidation": {
                "range": {"sheetId": gid, "startRowIndex": start, "endRowIndex": end,
                          "startColumnIndex": TYPE_COL, "endColumnIndex": TYPE_COL + 1},
                "rule": rule,
            }})
            report.append(f"  {title}!E{start + 1}:E{end} — {len(values)} → "
                          f"{len(values) + len(missing)} values (+{', '.join(missing)})")

    print(f"adding {new_values} to the type dropdown")
    print("\n".join(report) if report else "  nothing to change (all tabs already have the values)")
    print(f"tabs affected: {len(report)}")

    if not requests:
        return 0
    if not args.apply:
        print("DRY RUN — nothing applied. Re-run with --apply.")
        return 0

    svc.spreadsheets().batchUpdate(spreadsheetId=args.spreadsheet_id,
                                   body={"requests": requests}).execute()
    print(f"APPLIED to {len(requests)} ranges.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
