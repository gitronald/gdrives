"""Tests for gdrives.sheets.merge: the pure three-way merge by row key.

The case tests cover each row of the cell and row tables, each ownership rule,
carried columns, keys, and ``prefer`` one at a time. ``TestExhaustive`` then
enumerates every base, local, and sheet state over a tiny domain and checks the
invariants a sync depends on by applying each plan to simulated sides.
"""

import copy
import itertools
from dataclasses import FrozenInstanceError

import pytest

from gdrives.sheets import (
    OVERRIDE_REASONS,
    ROW_FLAGS,
    SIDES,
    Cell,
    MergePlan,
    NewRow,
    Override,
    RowFlag,
    merge,
)

KEY = ["id"]
COLUMNS = ["id", "name", "notes"]


def row(id_, name="", notes="", **extra):
    """One record in the ``COLUMNS`` projection, plus any carried cells."""
    return {"id": id_, "name": name, "notes": notes, **extra}


def run(base, local, sheet, **options):
    """Merge over the default key and projection."""
    return merge(base, local, sheet, KEY, COLUMNS, **options)


def cell(column, base, local, sheet, id_="1"):
    return Cell(key=(id_,), column=column, base=base, local=local, sheet=sheet)


def override(column, base, local, sheet, kept, reason, id_="1"):
    return Override(
        key=(id_,),
        column=column,
        base=base,
        local=local,
        sheet=sheet,
        kept=kept,
        reason=reason,
    )


class TestValidation:
    def test_empty_key_is_refused(self):
        with pytest.raises(ValueError, match="no key columns"):
            merge([], [], [], [], COLUMNS)

    def test_repeated_column_is_refused(self):
        with pytest.raises(ValueError, match=r"column\(s\) \['name'\] named twice"):
            merge([], [], [], KEY, ["id", "name", "name"])

    def test_key_outside_columns_is_refused(self):
        with pytest.raises(ValueError, match=r"key column\(s\) \['id'\] not in"):
            merge([], [], [], KEY, ["name"])

    @pytest.mark.parametrize("name", ["local_owned", "sheet_owned"])
    def test_owned_key_column_is_refused(self, name):
        with pytest.raises(
            ValueError, match=rf"key column\(s\) \['id'\] cannot be {name}"
        ):
            run([], [], [], **{name: {"id"}})

    @pytest.mark.parametrize("name", ["local_owned", "sheet_owned"])
    def test_owned_column_outside_projection_is_refused(self, name):
        with pytest.raises(
            ValueError, match=rf"{name} column\(s\) \['extra'\] not in columns"
        ):
            run([], [], [], **{name: {"extra"}})

    def test_overlapping_ownership_is_refused(self):
        with pytest.raises(
            ValueError,
            match=r"column\(s\) \['name'\] both local_owned and sheet_owned",
        ):
            run([], [], [], local_owned={"name"}, sheet_owned={"name", "notes"})

    def test_unknown_prefer_is_refused(self):
        with pytest.raises(ValueError, match="prefer must be one of"):
            run([], [], [], prefer="both")

    @pytest.mark.parametrize(
        ("side", "position"), [("base", 0), ("local", 1), ("sheet", 2)]
    )
    def test_duplicate_key_names_the_side(self, side, position):
        sides = [[], [], []]
        sides[position] = [row("1"), row(" 1 ")]
        with pytest.raises(ValueError, match=rf"^{side}: duplicate key \('1',\)"):
            run(*sides)

    @pytest.mark.parametrize(
        ("side", "position"), [("base", 0), ("local", 1), ("sheet", 2)]
    )
    def test_blank_key_names_the_side(self, side, position):
        sides = [[], [], []]
        sides[position] = [row("1"), {"name": "no id"}]
        with pytest.raises(ValueError, match=rf"^{side}: blank key"):
            run(*sides)


class TestCells:
    """The four cell cases, for a row on both sides and in the base."""

    def test_in_sync_does_nothing_and_moves_the_base(self):
        # Both sides made the same edit: nothing to write, base catches up.
        plan = run([row("1", "a")], [row("1", "b")], [row("1", "b")])
        assert (plan.pushes, plan.fold_cells, plan.conflicts) == ([], [], [])
        assert plan.new_base == [row("1", "b")]
        assert plan.new_local == [row("1", "b")]

    def test_local_edit_is_pushed(self):
        plan = run([row("1", "a")], [row("1", "b")], [row("1", "a")])
        assert plan.pushes == [cell("name", "a", "b", "a")]
        assert plan.fold_cells == []
        assert plan.new_local == [row("1", "b")]
        assert plan.new_base == [row("1", "b")]

    def test_sheet_edit_is_folded(self):
        plan = run([row("1", "a")], [row("1", "a")], [row("1", "c")])
        assert plan.fold_cells == [cell("name", "a", "a", "c")]
        assert plan.pushes == []
        assert plan.new_local == [row("1", "c")]
        assert plan.new_base == [row("1", "c")]

    def test_conflict_writes_neither_side_and_keeps_the_base(self):
        plan = run([row("1", "a")], [row("1", "b")], [row("1", "c")])
        assert plan.conflicts == [cell("name", "a", "b", "c")]
        assert (plan.pushes, plan.fold_cells, plan.overrides) == ([], [], [])
        assert plan.new_local == [row("1", "b")]
        assert plan.new_base == [row("1", "a")]
        assert plan.needs_attention

    def test_blank_is_a_value_like_any_other(self):
        # Clearing a cell locally is an edit, pushed as a blank.
        plan = run([row("1", "a")], [row("1", "")], [row("1", "a")])
        assert plan.pushes == [cell("name", "a", "", "a")]

    def test_missing_cell_counts_as_blank(self):
        plan = run([{"id": "1"}], [{"id": "1", "name": "b"}], [{"id": "1"}])
        assert plan.pushes == [cell("name", "", "b", "")]
        assert plan.fold_cells == []
        assert plan.new_base == [row("1", "b")]

    def test_entries_follow_local_row_then_column_order(self):
        plan = run(
            [row("1", "a", "a"), row("2", "a", "a")],
            [row("2", "b", "b"), row("1", "b", "b")],
            [row("1", "a", "a"), row("2", "a", "a")],
        )
        assert [(p.key, p.column) for p in plan.pushes] == [
            (("2",), "name"),
            (("2",), "notes"),
            (("1",), "name"),
            (("1",), "notes"),
        ]


class TestRowNotInBase:
    """A row on both sides but not in the base merges against blank cells."""

    def test_cell_filled_on_one_side_moves_to_the_other(self):
        plan = run([], [row("1", "a", "")], [row("1", "", "n")])
        assert plan.pushes == [cell("name", "", "a", "")]
        assert plan.fold_cells == [cell("notes", "", "", "n")]
        assert plan.new_local == [row("1", "a", "n")]
        assert plan.new_base == [row("1", "a", "n")]

    def test_cell_filled_differently_on_both_is_a_conflict(self):
        plan = run([], [row("1", "a")], [row("1", "b")])
        assert plan.conflicts == [cell("name", "", "a", "b")]
        assert plan.new_base == [row("1", "", "")]

    def test_matching_cells_are_in_sync(self):
        plan = run([], [row("1", "a")], [row("1", "a")])
        assert (plan.pushes, plan.fold_cells, plan.conflicts) == ([], [], [])
        assert plan.new_base == [row("1", "a")]


class TestRows:
    """The four row cases, read against the base."""

    def test_new_local_row_is_appended(self):
        plan = run([], [row("1", "a", "n")], [])
        assert plan.appends == [NewRow(key=("1",), values=row("1", "a", "n"))]
        assert plan.new_local == [row("1", "a", "n")]
        assert plan.new_base == [row("1", "a", "n")]
        assert not plan.needs_attention

    def test_appended_row_holds_projection_cells_in_order(self):
        plan = run([], [{"notes": "n", "extra": "x", "id": "1"}], [])
        assert list(plan.appends[0].values.items()) == [
            ("id", "1"),
            ("name", ""),
            ("notes", "n"),
        ]

    def test_local_row_gone_from_the_sheet_is_flagged_and_kept(self):
        plan = run([row("1", "a")], [row("1", "b", extra="x")], [])
        assert plan.row_flags == [RowFlag(key=("1",), flag="remote_deleted")]
        assert (plan.appends, plan.pushes) == ([], [])
        assert plan.new_local == [row("1", "b", extra="x")]
        # The base row stays, so the flag repeats on the next run.
        assert plan.new_base == [row("1", "a")]
        assert plan.needs_attention

    def test_new_sheet_row_is_folded(self):
        plan = run([], [row("1", "a", extra="x")], [row("1", "a"), row("2", "b")])
        assert plan.fold_rows == [NewRow(key=("2",), values=row("2", "b"))]
        assert plan.new_local == [row("1", "a", extra="x"), row("2", "b", extra="")]
        assert plan.new_base == [row("1", "a"), row("2", "b")]

    def test_folded_row_drops_sheet_columns_outside_the_projection(self):
        plan = run([], [], [{**row("2", "b"), "sheet_only": "s"}])
        assert plan.fold_rows == [NewRow(key=("2",), values=row("2", "b"))]
        assert plan.new_local == [row("2", "b")]

    def test_sheet_row_gone_locally_is_flagged_not_folded(self):
        plan = run([row("1", "a")], [], [row("1", "c")])
        assert plan.row_flags == [RowFlag(key=("1",), flag="local_deleted")]
        assert (plan.fold_rows, plan.new_local) == ([], [])
        assert plan.new_base == [row("1", "a")]

    def test_row_gone_from_both_sides_leaves_the_base_quietly(self):
        plan = run([row("1", "a")], [], [])
        assert plan.row_flags == []
        assert plan.new_base == []

    def test_base_order_is_local_then_folded_then_locally_deleted(self):
        # Locally deleted rows go last, so the next run, where the folded
        # rows are local, builds the base in the same order.
        plan = run(
            [row("3")],
            [row("1")],
            [row("4"), row("1"), row("3", "c"), row("2")],
        )
        assert [r["id"] for r in plan.new_local] == ["1", "4", "2"]
        assert [r["id"] for r in plan.new_base] == ["1", "4", "2", "3"]
        assert [r.key for r in plan.fold_rows] == [("4",), ("2",)]

    def test_no_row_is_ever_removed(self):
        plan = run(
            [row("1"), row("2")],
            [row("1"), row("3")],
            [row("2"), row("4")],
        )
        assert [r["id"] for r in plan.new_local] == ["1", "3", "4"]
        assert plan.row_flags == [
            RowFlag(key=("1",), flag="remote_deleted"),
            RowFlag(key=("2",), flag="local_deleted"),
        ]


class TestOwnsRows:
    def test_new_sheet_row_is_flagged_remote_added(self):
        plan = run([], [row("1")], [row("1"), row("2", "b")], owns_rows=True)
        assert plan.row_flags == [RowFlag(key=("2",), flag="remote_added")]
        assert plan.fold_rows == []
        assert plan.new_local == [row("1")]
        assert plan.new_base == [row("1")]  # no base row, so the flag repeats
        assert plan.needs_attention

    def test_new_local_row_is_still_appended(self):
        plan = run([], [row("1", "a")], [], owns_rows=True)
        assert plan.appends == [NewRow(key=("1",), values=row("1", "a"))]

    def test_deleted_rows_are_still_flagged(self):
        plan = run([row("1"), row("2")], [row("1")], [row("2")], owns_rows=True)
        assert [f.flag for f in plan.row_flags] == ["remote_deleted", "local_deleted"]


class TestLocalOwned:
    def test_sheet_edit_is_overwritten_and_reported(self):
        plan = run(
            [row("1", "a")], [row("1", "a")], [row("1", "c")], local_owned={"name"}
        )
        assert plan.pushes == [cell("name", "a", "a", "c")]
        assert plan.fold_cells == []
        assert plan.overrides == [
            override("name", "a", "a", "c", "local", "local_owned")
        ]
        assert plan.new_local == [row("1", "a")]
        assert plan.new_base == [row("1", "a")]

    def test_both_edited_pushes_local_without_a_conflict(self):
        plan = run(
            [row("1", "a")], [row("1", "b")], [row("1", "c")], local_owned={"name"}
        )
        assert plan.conflicts == []
        assert plan.pushes == [cell("name", "a", "b", "c")]
        assert plan.overrides == [
            override("name", "a", "b", "c", "local", "local_owned")
        ]
        assert not plan.needs_attention

    def test_local_edit_alone_is_a_plain_push(self):
        plan = run(
            [row("1", "a")], [row("1", "b")], [row("1", "a")], local_owned={"name"}
        )
        assert plan.pushes == [cell("name", "a", "b", "a")]
        assert plan.overrides == []

    def test_in_sync_cell_needs_nothing(self):
        plan = run(
            [row("1", "a")], [row("1", "b")], [row("1", "b")], local_owned={"name"}
        )
        assert (plan.pushes, plan.overrides) == ([], [])

    def test_other_columns_keep_the_cell_rule(self):
        plan = run(
            [row("1", "a", "a")],
            [row("1", "a", "a")],
            [row("1", "a", "c")],
            local_owned={"name"},
        )
        assert plan.fold_cells == [cell("notes", "a", "a", "c")]

    def test_new_sheet_row_folds_its_local_owned_cells(self):
        # The row came from the sheet; there is no local value to keep.
        plan = run([], [], [row("2", "b")], local_owned={"name"})
        assert plan.fold_rows == [NewRow(key=("2",), values=row("2", "b"))]
        assert plan.overrides == []


class TestSheetOwned:
    def test_local_edit_is_replaced_and_reported(self):
        plan = run(
            [row("1", "a")], [row("1", "b")], [row("1", "a")], sheet_owned={"name"}
        )
        assert plan.fold_cells == [cell("name", "a", "b", "a")]
        assert plan.pushes == []
        assert plan.overrides == [
            override("name", "a", "b", "a", "sheet", "sheet_owned")
        ]
        assert plan.new_local == [row("1", "a")]
        assert plan.new_base == [row("1", "a")]

    def test_both_edited_folds_sheet_without_a_conflict(self):
        plan = run(
            [row("1", "a")], [row("1", "b")], [row("1", "c")], sheet_owned={"name"}
        )
        assert plan.conflicts == []
        assert plan.fold_cells == [cell("name", "a", "b", "c")]
        assert plan.overrides == [
            override("name", "a", "b", "c", "sheet", "sheet_owned")
        ]

    def test_sheet_edit_alone_is_a_plain_fold(self):
        plan = run(
            [row("1", "a")], [row("1", "a")], [row("1", "c")], sheet_owned={"name"}
        )
        assert plan.fold_cells == [cell("name", "a", "a", "c")]
        assert plan.overrides == []

    def test_new_local_row_is_appended_with_sheet_owned_cells_blank(self):
        plan = run([], [row("1", "a", "mine", extra="x")], [], sheet_owned={"notes"})
        assert plan.appends == [NewRow(key=("1",), values=row("1", "a", ""))]
        assert plan.new_local == [row("1", "a", "", extra="x")]
        assert plan.new_base == [row("1", "a", "")]
        assert plan.overrides == [
            override("notes", "", "mine", "", "sheet", "sheet_owned")
        ]

    def test_blank_sheet_owned_cell_on_a_new_row_is_not_an_override(self):
        plan = run([], [row("1", "a")], [], sheet_owned={"notes"})
        assert plan.overrides == []
        assert plan.appends == [NewRow(key=("1",), values=row("1", "a", ""))]

    def test_missing_sheet_owned_cell_on_a_new_row_is_written_blank_locally(self):
        plan = run([], [{"id": "1", "name": "a"}], [], sheet_owned={"notes"})
        assert plan.new_local == [row("1", "a", "")]


class TestPrefer:
    @pytest.mark.parametrize("prefer", sorted(SIDES))
    def test_conflict_resolves_toward_the_named_side(self, prefer):
        plan = run([row("1", "a")], [row("1", "b")], [row("1", "c")], prefer=prefer)
        conflict = cell("name", "a", "b", "c")
        assert plan.conflicts == []
        assert plan.overrides == [override("name", "a", "b", "c", prefer, "prefer")]
        if prefer == "local":
            assert (plan.pushes, plan.fold_cells) == ([conflict], [])
            assert plan.new_local == plan.new_base == [row("1", "b")]
        else:
            assert (plan.pushes, plan.fold_cells) == ([], [conflict])
            assert plan.new_local == plan.new_base == [row("1", "c")]
        assert not plan.needs_attention

    @pytest.mark.parametrize("prefer", sorted(SIDES))
    def test_non_conflicts_are_unaffected(self, prefer):
        plan = run(
            [row("1", "a", "a")],
            [row("1", "b", "a")],
            [row("1", "a", "c")],
            prefer=prefer,
        )
        assert plan.pushes == [cell("name", "a", "b", "a")]
        assert plan.fold_cells == [cell("notes", "a", "a", "c")]
        assert plan.overrides == []

    @pytest.mark.parametrize("prefer", sorted(SIDES))
    def test_row_flags_are_unaffected(self, prefer):
        plan = run([row("1"), row("2")], [row("1")], [row("2")], prefer=prefer)
        assert [f.flag for f in plan.row_flags] == ["remote_deleted", "local_deleted"]


class TestKeys:
    def test_key_whitespace_matches_and_each_side_keeps_its_text(self):
        plan = run([row("A 1", "a")], [row(" A 1", "a")], [row("A  1 ", "c")])
        assert plan.fold_cells == [cell("name", "a", "a", "c", id_="A 1")]
        # Key cells are never pushed, folded, or reported.
        assert (plan.pushes, plan.conflicts, plan.overrides) == ([], [], [])
        assert plan.new_local == [row(" A 1", "c")]
        assert plan.new_base == [row(" A 1", "c")]

    def test_composite_key(self):
        plan = merge(
            [{"a": "1", "b": "x", "v": "0"}],
            [{"a": "1", "b": "x", "v": "1"}, {"a": "1", "b": "y", "v": "2"}],
            [{"a": "1", "b": "x", "v": "0"}],
            ["a", "b"],
            ["a", "b", "v"],
        )
        assert plan.pushes == [
            Cell(key=("1", "x"), column="v", base="0", local="1", sheet="0")
        ]
        assert plan.appends == [
            NewRow(key=("1", "y"), values={"a": "1", "b": "y", "v": "2"})
        ]

    def test_plan_entries_name_rows_by_normalized_key(self):
        plan = run([], [row("  x  y ")], [])
        assert plan.appends[0].key == ("x y",)
        assert plan.appends[0].values["id"] == "  x  y "


class TestCarried:
    def test_carried_cells_pass_through_byte_identical(self):
        local = [row("1", "a", extra=" keep  me ", more="")]
        plan = run([row("1", "a")], local, [row("1", "c")])
        assert plan.new_local == [row("1", "c", extra=" keep  me ", more="")]

    def test_carried_columns_never_reach_the_sheet_or_base(self):
        plan = run([], [row("1", "a", extra="x")], [])
        assert "extra" not in plan.appends[0].values
        assert plan.new_base == [row("1", "a")]

    def test_carried_cells_are_never_compared(self):
        # A base or sheet cell under a carried name is ignored.
        plan = run(
            [row("1", extra="old")], [row("1", extra="x")], [row("1", extra="y")]
        )
        assert (plan.pushes, plan.fold_cells, plan.conflicts) == ([], [], [])

    def test_folded_row_gets_every_carried_column_blank(self):
        plan = run([], [row("1", a="1"), row("2", b="2")], [row("3", "c")])
        assert plan.new_local[-1] == row("3", "c", a="", b="")


class TestPlan:
    def test_entries_and_plan_are_frozen(self):
        plan = run([], [], [])
        for obj, name in [
            (plan, "pushes"),
            (cell("name", "", "", ""), "base"),
            (override("name", "", "", "", "local", "prefer"), "kept"),
            (NewRow(key=("1",), values={}), "key"),
            (RowFlag(key=("1",), flag="local_deleted"), "flag"),
        ]:
            with pytest.raises(FrozenInstanceError):
                setattr(obj, name, None)

    def test_empty_merge_is_an_empty_plan(self):
        assert run([], [], []) == MergePlan()
        assert not MergePlan().needs_attention

    def test_inputs_are_not_mutated(self):
        base = [row("1", "a"), row("2"), row("4")]
        local = [row("1", "b", "x", extra="e"), row("3", "n", "m"), row("4")]
        sheet = [row("1", "a", "y"), row("2", "z"), row("5", "s")]
        snapshot = copy.deepcopy((base, local, sheet))
        plan = run(base, local, sheet, sheet_owned={"notes"})
        assert (base, local, sheet) == snapshot
        # The plan holds its own dicts, so changing it cannot reach the inputs.
        for record in [*plan.new_local, *plan.new_base]:
            record["id"] = "changed"
        for new in [*plan.appends, *plan.fold_rows]:
            new.values["id"] = "changed"
        assert (base, local, sheet) == snapshot

    def test_constants(self):
        assert SIDES == {"local", "sheet"}
        assert OVERRIDE_REASONS == {"local_owned", "sheet_owned", "prefer"}
        assert ROW_FLAGS == {"remote_deleted", "local_deleted", "remote_added"}


# -- exhaustive invariants --

_VALUES = ("", "a", "b")
# Every ownership setting, and prefer, over a projection whose only cell
# column is "name"; the two-column tests add a split ownership too.
_SETTINGS = [
    {},
    {"owns_rows": True},
    {"local_owned": {"name"}},
    {"sheet_owned": {"name"}},
    {"prefer": "local"},
    {"prefer": "sheet"},
]
_SPLIT = {"local_owned": {"name"}, "sheet_owned": {"notes"}}


def _states(columns):
    """Every state of one row on one side: absent, or each mix of cell values."""
    return [None, *itertools.product(_VALUES, repeat=len(columns))]


def _side(ids, states, columns, **extra):
    """Build one side's records from each row's state."""
    return [
        {"id": id_, **dict(zip(columns, state, strict=True)), **extra}
        for id_, state in zip(ids, states, strict=True)
        if state is not None
    ]


def _apply_to_sheet(sheet, plan):
    """The sheet after the plan's pushes and appends, as apply would leave it."""
    rows = {r["id"]: dict(r) for r in sheet}
    for push in plan.pushes:
        rows[push.key[0]][push.column] = push.local
    for new in plan.appends:
        rows[new.key[0]] = dict(new.values)
    return list(rows.values())


def _check(base, local, sheet, columns, options):
    snapshot = copy.deepcopy((base, local, sheet))
    plan = merge(base, local, sheet, KEY, columns, **options)
    assert (base, local, sheet) == snapshot

    new_sheet = _apply_to_sheet(sheet, plan)
    new_local = plan.new_local
    by_id = {
        name: {r["id"]: r for r in rows}
        for name, rows in [
            ("sheet", new_sheet),
            ("local", new_local),
            ("base", plan.new_base),
        ]
    }

    # No row is ever removed, and local rows keep their order at the front.
    assert {r["id"] for r in sheet} <= set(by_id["sheet"])
    assert [r["id"] for r in new_local[: len(local)]] == [r["id"] for r in local]
    # Carried cells of existing local rows pass through unchanged.
    for before, after in zip(local, new_local, strict=False):
        assert after["carried"] == before["carried"]
    assert all(set(r) == {"id", *columns} for r in plan.new_base)

    # Every settled cell holds one value on the sheet, locally, and in the base.
    flagged = {f.key[0] for f in plan.row_flags}
    conflicted = {(c.key[0], c.column) for c in plan.conflicts}
    ids = set(by_id["local"]) | set(by_id["sheet"])
    for id_ in ids - flagged:
        for column in columns:
            if (id_, column) in conflicted:
                continue
            seen = {by_id[name][id_].get(column, "") for name in by_id}
            assert len(seen) == 1, (id_, column, seen)

    # A flagged row changes nothing on either side.
    old_local = {r["id"]: r for r in local}
    old_sheet = {r["id"]: r for r in sheet}
    for id_ in flagged:
        assert by_id["local"].get(id_) == old_local.get(id_)
        assert by_id["sheet"].get(id_) == old_sheet.get(id_)

    # Every flag and override carries a documented value.
    for flag in plan.row_flags:
        assert flag.flag in ROW_FLAGS
    for entry in plan.overrides:
        assert entry.kept in SIDES
        assert entry.reason in OVERRIDE_REASONS

    # Merging again from the result settles: nothing moves, the rest repeats.
    again = merge(plan.new_base, new_local, new_sheet, KEY, columns, **options)
    assert (again.pushes, again.appends) == ([], [])
    assert (again.fold_cells, again.fold_rows, again.overrides) == ([], [], [])
    assert again.conflicts == plan.conflicts
    assert again.row_flags == plan.row_flags
    assert again.new_local == new_local
    assert again.new_base == plan.new_base


class TestExhaustive:
    """Every base, local, and sheet state over a tiny domain, each setting."""

    @pytest.mark.parametrize("options", [*_SETTINGS, _SPLIT])
    def test_one_row_two_columns(self, options):
        columns = ["name", "notes"]
        states = _states(columns)
        for b, loc, r in itertools.product(states, repeat=3):
            base = _side(["1"], [b], columns)
            local = _side(["1"], [loc], columns, carried="c")
            sheet = _side(["1"], [r], columns)
            _check(base, local, sheet, ["id", *columns], options)

    @pytest.mark.parametrize("options", _SETTINGS)
    def test_two_rows_one_column(self, options):
        columns = ["name"]
        states = _states(columns)
        per_row = list(itertools.product(states, repeat=3))
        for first, second in itertools.product(per_row, repeat=2):
            base, local, sheet = (
                _side(["1", "2"], [first[i], second[i]], columns) for i in range(3)
            )
            local = [{**r, "carried": "c"} for r in local]
            _check(base, local, sheet, ["id", *columns], options)
