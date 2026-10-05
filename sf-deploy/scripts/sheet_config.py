#!/usr/bin/env python3
"""Canonical Google Sheet for this project.

CHANGE THE DEFAULT HERE when switching workbooks.
All fetch/write scripts import DEFAULT_SPREADSHEET_ID from this file.
CLI --spreadsheet-id / --sheet-id still overrides for a one-off run.

To switch sheets later, edit ONLY this file (then tell the agent). Rules and
skills point here; they do not store a second copy of the ID.
"""

DEFAULT_SPREADSHEET_ID = "1_TaxDe-Qxl8BAUmuZc01vUoxpBEPxJ4Opx4tEe8ulNQ"
DEFAULT_SPREADSHEET_TITLE = "Toray Object Definition Model Document V1.0"
DEFAULT_SPREADSHEET_URL = (
    "https://docs.google.com/spreadsheets/d/"
    + DEFAULT_SPREADSHEET_ID
    + "/edit"
)

SPREADSHEET_ID_HELP = (
    "Google Spreadsheet ID. Default is the project sheet defined in "
    "scripts/sheet_config.py. Pass a different ID only to override for one run."
)


def add_spreadsheet_id_arg(
    parser,
    *,
    dest: str = "spreadsheet_id",
    flag: str = "--spreadsheet-id",
) -> None:
    """Register --spreadsheet-id (or --sheet-id) with the project default."""
    parser.add_argument(
        flag,
        dest=dest,
        default=DEFAULT_SPREADSHEET_ID,
        help=SPREADSHEET_ID_HELP,
    )
