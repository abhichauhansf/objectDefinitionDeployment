"""Backward-compatible entrypoint for classic layout generation.

The layout-only GitHub workflow historically invokes `generate_layout_xml.py`.
The implementation now lives in `generate_layout_partial.py`, so this shim
keeps existing workflow references stable.
"""
from __future__ import annotations

from generate_layout_partial import main


if __name__ == "__main__":
    main()
