---
id: 3
slug: sheets-conditional-formatting
status: done
branch: feature/sheets-conditional-formatting
created: 2026-09-08T12:12:39-07:00
concluded: 2026-09-11T13:59:06-07:00
pr: https://github.com/gitronald/gdrives/pull/26
---

# Read and write conditional format rules on a Sheet

## Plan

### Goal

Expose Google Sheets **conditional formatting** rules through `gdrives` — list the
rules on a spreadsheet, add one, and delete one. Plan
[001](../001-sheets-read-write/plan.md) deliberately scoped structural edits via
`spreadsheets.batchUpdate` (formatting, tabs, frozen rows) out; this plan takes the
first slice of that follow-up, limited to conditional format rules.

The motivating case: converting an uploaded `.xlsx` to a native Google Sheet does
not carry its conditional formatting across, so anyone migrating a workbook has to
re-create every rule by hand in the web UI. With this, the rules can be captured
from a source sheet and replayed onto the converted one as a scripted step.

### Scope

In scope:
- `list_conditional_rules` / `add_conditional_rule` / `delete_conditional_rule`
  helpers in `gdrives/sheets.py`.
- A custom-formula rule builder (the common case) plus pass-through of a raw rule
  dict for anything the builder does not cover.
- CLI commands `sheets-rules`, `sheets-add-rule`, `sheets-delete-rule`.
- Tests against the fake Sheets service in `tests/helpers.py`, mirroring
  `tests/test_sheets.py`.

Out of scope:
- The rest of `spreadsheets.batchUpdate`: cell formatting, tab add/delete/rename,
  frozen rows, data validation, protected ranges, banding.
- Color-scale (gradient) rules — single-color / boolean rules only. The API models
  them as `gradientRule` instead of `booleanRule`; `list` should still surface them
  verbatim, but the builder and the add command do not construct one.
- Any "copy all rules from sheet A to sheet B" convenience command. That is a
  caller-side loop over `list` + `add`, and belongs in the consumer until the
  primitives have settled.

### API surface

Conditional formatting is **not** part of `spreadsheets.values.*` — rules live on
the spreadsheet resource itself. So:

- **Reading** uses `spreadsheets().get(spreadsheetId=..., fields=...)` and pulls
  `sheets[].conditionalFormats[]`. Request a narrow `fields` mask
  (`sheets.properties(sheetId,title),sheets.conditionalFormats`) — the default
  response carries the whole grid and is enormous.
- **Writing** uses `spreadsheets().batchUpdate(...)` with
  `addConditionalFormatRule` / `deleteConditionalFormatRule` requests. Note this is
  `spreadsheets.batchUpdate`, a different method from the
  `spreadsheets.values.batchUpdate` already wrapped by `batch_update_values`
  (`sheets.py:99`) — keep the names distinct so the two are not confused.

Proposed helpers in `gdrives/sheets.py`:

```python
def list_conditional_rules(service, spreadsheet_id) -> list[dict[str, Any]]
    # -> [{"tab": "Sheet1", "sheet_id": 0, "index": 0, "rule": {...}}, ...]

def build_formula_rule(ranges, formula, *, bold=None, italic=None,
                       strikethrough=None, underline=None,
                       text_color=None, background=None) -> dict[str, Any]
    # -> {"ranges": [GridRange, ...],
    #     "booleanRule": {"condition": {"type": "CUSTOM_FORMULA",
    #                                   "values": [{"userEnteredValue": formula}]},
    #                     "format": {...}}}

def add_conditional_rule(service, spreadsheet_id, rule, *, index=0) -> dict[str, Any]

def delete_conditional_rule(service, spreadsheet_id, sheet_id, index) -> dict[str, Any]
```

Two details the builder has to get right:

- **A1 ranges must become `GridRange` objects.** The API takes
  `{sheetId, startRowIndex, endRowIndex, startColumnIndex, endColumnIndex}` with
  0-based, half-open indices — not the `"Sheet1!A2:AA"` string the rest of the
  module speaks. Add an `a1_to_grid_range(service, spreadsheet_id, range_)` that
  resolves the tab name to a `sheetId` and converts the corners, reusing
  `column_letter` (`sheets.py:169`) in reverse and `list_tabs` for the lookup.
  Omitting `endRowIndex` / `endColumnIndex` is how the API expresses an open-ended
  range (`A2:AA` with no row bound), so the converter must leave them unset rather
  than defaulting them.
- **Rules are an ordered list per tab, addressed by index.** `addConditionalFormatRule`
  takes an insertion `index` and `deleteConditionalFormatRule` a positional one, so
  deleting shifts everything after it. Document that, and have `list` return the
  index alongside each rule so a delete can be aimed.

Colors are `{"red": 0.0-1.0, "green": ..., "blue": ...}` floats, not hex. Accept hex
strings at the CLI boundary and convert, so `--text-color "#999999"` works.

### CLI

```bash
gdrives sheets-rules <sheet>                       # list rules, grouped by tab
gdrives sheets-rules <sheet> --json                # raw rule dicts, for replay
gdrives sheets-add-rule <sheet> --range "Sheet1!A2:AA" \
    --formula '=OR($F2="Rejected", $F2="Inactive")' \
    --strikethrough --text-color "#999999"
gdrives sheets-add-rule <sheet> --rule-json rules.json   # replay a captured rule
gdrives sheets-delete-rule <sheet> --tab Sheet1 --index 0 [--yes]
```

`sheets-rules` reads, so it keeps the default read-only scope. The two write
commands request `SHEETS_WRITE_SCOPES` like the existing `sheets-update` family, so
they reuse the `gdrives_token_rw.json` token with no new consent flow. Follow the
existing CLI conventions: the spreadsheet target accepts a URL, bare ID, or Drive
path (`cli.py:182`); the destructive command takes `--yes` like `sheets-clear`; the
`run_*` implementation lives in `sheets.py` and is imported inside the command body.

`--range` should be repeatable — one rule can cover several ranges.

### Tests

Extend `tests/helpers.py`'s fake Sheets service with a `conditionalFormats` payload
on the `get` response and a recorder for `batchUpdate` requests, then cover:

- `list_conditional_rules` on a sheet with rules on more than one tab, and on one
  with none (the key is absent, not empty — same shape trap as `values`).
- `a1_to_grid_range` for a bounded range, an open-ended one (`A2:AA`), a
  single-column range, and a quoted tab name with a space.
- `build_formula_rule` output matches the documented request shape, and that unset
  format options are omitted rather than sent as `None`.
- Hex-to-float color conversion, including a bad hex string erroring cleanly.
- `add`/`delete` issue the right `batchUpdate` request bodies.

### Docs

- README: a short **Conditional formatting** subsection under the Sheets commands.
- CHANGELOG `[Unreleased]`: the three new commands and the helper functions.

## Log

### 2026-09-11 — implementation

- Activated on `dev`; work on `feature/sheets-conditional-formatting` in a
  worktree; draft PR opened.
- `gdrives/sheets.py`: `list_conditional_rules`, `build_formula_rule`,
  `add_conditional_rule`, `delete_conditional_rule`, `a1_to_grid_range`, plus the
  supporting pieces the spec implied: `column_index` (inverse of `column_letter`),
  `split_a1`, `tab_sheet_ids` (title -> `sheetId`; `list_tabs` only returns titles,
  so it could not be reused for the lookup), `grid_range_to_a1` and `describe_rule`
  / `format_rules` (for the human `sheets-rules` view), `hex_to_color` /
  `color_to_hex`, `read_rule_json`, and `batch_update_spreadsheet` (named apart
  from `batch_update_values`, as planned).
- CLI: `sheets-rules [--json]`, `sheets-add-rule` (repeatable `--range`,
  `--formula`, `--bold/--italic/--strikethrough/--underline`, `--text-color`,
  `--background`, `--index`, or `--rule-json`), `sheets-delete-rule --index
  [--tab] [-y]`. `--tab` defaults to the first tab (matching `sheets-set`) rather
  than being required. The delete re-reads the rule list, shows the targeted rule
  in the prompt, and refuses an out-of-range index before sending anything.
  `--rule-json` accepts a bare rule or one `sheets-rules --json` entry.
- Tests: `tests/test_sheets_rules.py` (fake service gained
  `spreadsheets.batchUpdate`), CLI delegation tests, and two live tests in
  `tests/test_sheets_integration.py`; unit suite at 100% line+branch coverage,
  live sheets suite 12/12 passing.
- **Finding (spec correction):** omitting `endRowIndex` does express an
  open-ended range *in the request*, but the API does not store it that way — a
  rule added over `A2:C` lists back as `A2:C1000`, clamped to the tab's current
  row count. So a captured rule replays with a fixed extent. Documented in the
  `_cell_span` docstring and the README; the live test asserts the clamped shape.
- **Caveat for replay:** a captured rule's ranges keep their source `sheetId`, so
  `--rule-json` onto a different spreadsheet only works when a tab with that ID
  exists there. Remapping is left to the caller (in line with the out-of-scope
  "copy all rules" convenience).

### 2026-09-11 — live demo and live-test discoverability

- Exercised the three commands end to end through the CLI (the live tests call
  the helpers directly) on the live test spreadsheet. It now has three demo tabs
  with the same small roster: `no-rules` (data only; the spreadsheet's original
  first tab, which it must always keep), `rules-demo` (six rules, including two
  whole-row rules on a rows-only `2:1000` range, which the API stores clamped to
  the tab's width, `A2:Z1000`), and `rules-demo-replay` (rules captured with
  `sheets-rules --json`, `sheetId`-remapped, replayed with `--rule-json`, then one
  deleted with `sheets-delete-rule`). The live suites ignore these tabs.
- Found that live-test setup was discoverable only from the integration test
  modules' docstrings and the marker description: without the test IDs every live
  test skips, and a plain `uv run pytest` (as CI runs it) only counts the skips.
  Added a `pytest_terminal_summary` hook in `tests/conftest.py` that prints a
  "live integration tests skipped" note with each reason and a pointer to the
  README. It runs `trylast`, so it lands after the coverage table next to the
  final counts, and it is silent when the live tests run or are deselected.
- README gained a **Development** section (unit vs. live commands, and a
  step-by-step for the service account, APIs, throwaway sheet/doc shared as
  Editor, and the `.env` IDs); CHANGELOG `[Unreleased]` notes both.

### 2026-09-11 — close

- Commit `e31d960` (review fixes). Check gate (ruff check, ruff format --check,
  pyrefly, pytest) green: 555 passed, 100% line + branch coverage.

#### Review follow-up

`/code-review` at medium (correctness + reuse/simplification finders, per-file
verifiers): 5 candidates, 5 confirmed, 0 rejected; the gap sweep added none.
All five actioned, each with a test:

- **Ranges on more than one tab were not rejected.** `sheets-add-rule` with
  `--range` on two tabs sent mixed `sheetId`s and got an opaque API 400.
  `build_formula_rule` now raises (an omitted `sheetId` counts as 0) —
  `test_ranges_on_two_tabs_raise`, `test_omitted_sheet_id_is_the_first_tab`,
  `test_ranges_on_two_tabs_refuse_without_writing`.
- **No-format-option check ran after an API round trip**, contradicting the
  `run_add_rule` docstring. Now checked before resolving the source —
  `test_no_format_fails_before_any_call`.
- **`run_delete_rule` read the spreadsheet twice** (`tab_sheet_ids`, then
  `list_conditional_rules`). Now one read via `_rule_tabs`, shared by `_tab_ids`
  and `_flatten_rules` — `test_yes_deletes_on_named_tab` asserts one read, one write.
- **`delete_conditional_rule` accepted a negative index**, unlike
  `add_conditional_rule`. Now raises — `test_delete_negative_index_raises`.
- **Duplicated test helper.** `patch_service` in `test_sheets_rules.py` copied
  `TestRunWrite._patch_service`; both now use `patch_sheets_service` in
  `tests/helpers.py`.

No conscious no-ops.

## Retrospective

- The spec held up: the helper set, CLI shape, and scopes landed as planned. The
  one correction came from the live API, not review — open-ended ranges are
  stored clamped to the tab's size, which only a live test could reveal.
- Filling in `--tab`'s default (first tab, like `sheets-set`) and having the
  delete re-read the rule it targets before prompting were the right calls; both
  kept the destructive command honest about what it was about to remove.
- Review found no wrong results, but did find validation gaps at the library
  boundary: a constraint the README stated (one tab per rule) was enforced by
  nobody but the API. Worth checking every documented rule constraint for a
  matching guard while writing the helper, not after.
- 100% coverage did not catch the redundant read or the late validation —
  tests asserted outcomes, not call counts or call order. Asserting the exact
  API call sequence in `run_*` tests is cheap and catches both.
- Live-test discoverability (the skip notice and README setup steps) was
  unplanned scope, but it pays off for any future plan that relies on live tests.
