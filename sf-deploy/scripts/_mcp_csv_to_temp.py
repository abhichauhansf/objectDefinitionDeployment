#!/usr/bin/env python3
"""Build temp_updates.json from a live MCP CSV export page (Sheets ADC is broken).

Usage: python scripts/_mcp_csv_to_temp.py --csv-json <page.txt> --tab Receiving --out temp_updates.json
"""
from __future__ import annotations
import argparse, csv, io, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_sheet import parse_tab  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv-json", required=True)
    ap.add_argument("--tab", required=True)
    ap.add_argument("--out", default="temp_updates.json")
    args = ap.parse_args()

    payload = json.loads(Path(args.csv_json).read_text(encoding="utf-8"))
    grid = list(csv.reader(io.StringIO(payload["csv"])))
    rows = parse_tab(args.tab, grid)
    Path(args.out).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    n_obj = sum(1 for r in rows if r.get("_type") == "object_meta")
    print(f"Wrote {args.out}: {n_obj} object(s), {len(rows) - n_obj} field(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
