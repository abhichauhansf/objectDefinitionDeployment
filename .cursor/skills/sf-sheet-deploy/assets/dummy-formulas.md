# Dummy formulas (empty formula body is NOT a hard blocker)

`generate_xml.py` auto-injects a return-type-appropriate dummy so the field stays
deployable. Write the dummy back to col **H** (`設定値` — Type Specific Value;
NEVER col G, which is a description) via `write_attr_fixes.py` (gated) and note
it in AH. The client supplies the real formula later.

| Return type | Dummy formula |
|-------------|---------------|
| Text | `"TBD"` |
| Number / Currency / Percent | `0` |
| Date | `TODAY()` |
| DateTime | `NOW()` |
| Time | `TIMENOW()` |
| Checkbox | `false` |

Highlight updated formula rows **Column A only** (green).
