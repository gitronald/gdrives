"""Conditional format rules (``spreadsheets.get`` / ``spreadsheets.batchUpdate``).

Conditional format rules live on the spreadsheet resource, not in
``spreadsheets.values``, so they are read with ``spreadsheets.get`` and written
with ``spreadsheets.batchUpdate``, and address cells by ``GridRange`` (numeric
``sheetId`` + 0-based, half-open indices) rather than A1 strings.

Each tab holds an ordered list of rules addressed by position: the first rule
that matches a cell wins, and both inserting at an index and deleting one shift
every rule after it. So list returns each rule's index alongside it, and a
delete is aimed by (tab, index) from a fresh list.
"""

import json
import string
from typing import Any

from gdrives.files import Service
from gdrives.sheets.a1 import grid_range_to_a1
from gdrives.sheets.values import batch_update_spreadsheet


def hex_to_color(value: str) -> dict[str, float]:
    """Convert ``#RRGGBB`` (or ``RRGGBB`` / ``#RGB``) to a Sheets Color (0-1 floats)."""
    digits = value.strip().removeprefix("#")
    if len(digits) == 3:
        digits = "".join(c * 2 for c in digits)
    if len(digits) != 6 or any(c not in string.hexdigits for c in digits):
        raise ValueError(f"color must be a hex string like '#999999', got {value!r}")
    red, green, blue = (int(digits[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return {"red": red, "green": green, "blue": blue}


def color_to_hex(color: dict[str, Any]) -> str:
    """Convert a Sheets Color to ``#rrggbb``; channels the API omits count as 0."""
    return "#" + "".join(
        f"{round(color.get(c, 0) * 255):02x}" for c in ("red", "green", "blue")
    )


def build_formula_rule(
    ranges: list[dict[str, Any]],
    formula: str,
    *,
    bold: bool | None = None,
    italic: bool | None = None,
    strikethrough: bool | None = None,
    underline: bool | None = None,
    text_color: dict[str, float] | None = None,
    background: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build a custom-formula ``ConditionalFormatRule`` over ``ranges`` (GridRanges).

    Only the format options given are sent, so an unset one keeps the cell's own
    formatting rather than being forced off. Colors are Sheets Color dicts (see
    :func:`hex_to_color`). Raises when there are no ranges, ranges on more than one
    tab (the API requires a rule's ranges to share a ``sheetId``), or no format
    options.
    """
    if not ranges:
        raise ValueError("a rule needs at least one range")
    if len({r.get("sheetId", 0) for r in ranges}) > 1:
        raise ValueError("a rule's ranges must all be on one tab")
    flags = {
        "bold": bold,
        "italic": italic,
        "strikethrough": strikethrough,
        "underline": underline,
    }
    text_format: dict[str, Any] = {k: v for k, v in flags.items() if v is not None}
    if text_color is not None:
        text_format["foregroundColor"] = text_color
    fmt: dict[str, Any] = {}
    if text_format:
        fmt["textFormat"] = text_format
    if background is not None:
        fmt["backgroundColor"] = background
    if not fmt:
        raise ValueError("a rule needs at least one format option")
    return {
        "ranges": list(ranges),
        "booleanRule": {
            "condition": {
                "type": "CUSTOM_FORMULA",
                "values": [{"userEnteredValue": formula}],
            },
            "format": fmt,
        },
    }


def _rule_tabs(service: Service, spreadsheet_id: str) -> list[dict[str, Any]]:
    """Read every tab's properties and conditional formats in one ``get``."""
    result = (
        service.spreadsheets()
        .get(
            spreadsheetId=spreadsheet_id,
            # A narrow mask: the default response carries the whole grid.
            fields="sheets(properties(sheetId,title),conditionalFormats)",
        )
        .execute()
    )
    return result.get("sheets", [])


def list_conditional_rules(
    service: Service, spreadsheet_id: str
) -> list[dict[str, Any]]:
    """Return every conditional format rule as ``{tab, sheet_id, index, rule}``.

    Rules come back in tab order, then rule order; ``index`` is the rule's
    position on its tab (what :func:`delete_conditional_rule` takes). Color-scale
    (``gradientRule``) rules are returned verbatim alongside boolean ones.
    """
    return _flatten_rules(_rule_tabs(service, spreadsheet_id))


def _flatten_rules(tabs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten :func:`_rule_tabs` output to ``{tab, sheet_id, index, rule}`` entries."""
    rules = []
    for sheet in tabs:
        props = sheet["properties"]
        # Like "values", the key is absent (not empty) on a tab with no rules.
        for index, rule in enumerate(sheet.get("conditionalFormats", [])):
            rules.append(
                {
                    "tab": props["title"],
                    "sheet_id": props.get("sheetId", 0),
                    "index": index,
                    "rule": rule,
                }
            )
    return rules


def add_conditional_rule(
    service: Service, spreadsheet_id: str, rule: dict[str, Any], *, index: int = 0
) -> dict[str, Any]:
    """Insert ``rule`` at ``index`` in its tab's rules (0 = first, highest priority).

    The tab is the one named by the rule's ranges' ``sheetId``; rules at or after
    ``index`` shift down by one.
    """
    if index < 0:
        raise ValueError(f"rule index must be non-negative, got {index}")
    return batch_update_spreadsheet(
        service,
        spreadsheet_id,
        [{"addConditionalFormatRule": {"rule": rule, "index": index}}],
    )


def delete_conditional_rule(
    service: Service, spreadsheet_id: str, sheet_id: int, index: int
) -> dict[str, Any]:
    """Delete the rule at ``index`` on tab ``sheet_id``; later rules shift up by one."""
    if index < 0:
        raise ValueError(f"rule index must be non-negative, got {index}")
    return batch_update_spreadsheet(
        service,
        spreadsheet_id,
        [{"deleteConditionalFormatRule": {"sheetId": sheet_id, "index": index}}],
    )


def read_rule_json(path: str) -> dict[str, Any]:
    """Read one rule from a JSON file for replay with :func:`add_conditional_rule`.

    Accepts a bare ``ConditionalFormatRule`` or one entry of ``sheets-rules
    --json`` output (unwrapping its ``rule``). Its ranges keep their ``sheetId``,
    so they must name a tab that exists on the target spreadsheet. A UTF-8
    byte-order mark, which some Windows editors write, is ignored.
    """
    with open(path, encoding="utf-8-sig") as f:
        data = json.load(f)
    if isinstance(data, dict) and "rule" in data:
        data = data["rule"]
    if not isinstance(data, dict) or not (
        "booleanRule" in data or "gradientRule" in data
    ):
        raise ValueError(
            f"{path}: expected one conditional format rule "
            "(an object with booleanRule or gradientRule, or one sheets-rules "
            "--json entry)"
        )
    return data


def _format_summary(fmt: dict[str, Any]) -> str:
    """Summarize a rule's CellFormat: flags plus text/fill colors as hex."""
    text = fmt.get("textFormat", {})
    parts = [
        flag
        for flag in ("bold", "italic", "strikethrough", "underline")
        if text.get(flag)
    ]
    fg = text.get(
        "foregroundColor", text.get("foregroundColorStyle", {}).get("rgbColor")
    )
    if fg is not None:
        parts.append(f"text {color_to_hex(fg)}")
    bg = fmt.get("backgroundColor", fmt.get("backgroundColorStyle", {}).get("rgbColor"))
    if bg is not None:
        parts.append(f"fill {color_to_hex(bg)}")
    return ", ".join(parts) or "no format"


def describe_rule(rule: dict[str, Any]) -> str:
    """Render a rule as one line: ranges, condition, and format summary."""
    ranges = ", ".join(
        grid_range_to_a1(r) or "(whole tab)" for r in rule.get("ranges", [])
    )
    if "gradientRule" in rule:
        return f"{ranges}  color scale"
    boolean = rule.get("booleanRule", {})
    condition = boolean.get("condition", {})
    kind = condition.get("type", "")
    # A custom formula speaks for itself; other types (TEXT_CONTAINS, ...) need it.
    parts = [] if kind == "CUSTOM_FORMULA" else [kind]
    parts += [
        v.get("userEnteredValue", v.get("relativeDate", ""))
        for v in condition.get("values", [])
    ]
    return (
        f"{ranges}  {' '.join(parts)}  [{_format_summary(boolean.get('format', {}))}]"
    )


def format_rules(rules: list[dict[str, Any]]) -> str:
    """Render :func:`list_conditional_rules` output by tab, one rule per line."""
    lines: list[str] = []
    tab = None
    for entry in rules:
        if entry["tab"] != tab:
            tab = entry["tab"]
            lines.append(f"{tab} (sheetId {entry['sheet_id']})")
        lines.append(f"  [{entry['index']}] {describe_rule(entry['rule'])}")
    return "\n".join(lines)
