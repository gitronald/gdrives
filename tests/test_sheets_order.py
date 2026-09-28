"""Tests for gdrives.sheets.order: putting a keyed tab's rows in a given order.

The move planner is checked on plain lists, replaying its moves with the
index rule the live API follows (``destinationIndex`` counted before the row
is taken out), so it is tested apart from the fake. ``reorder_rows`` runs
against ``FakeSheetGrid``, whose ``moveDimension`` follows the same rule.
"""

import itertools
import random

import pytest
from googleapiclient.errors import HttpError
from helpers import FakeSheetGrid, http_error

from gdrives.sheets import (
    ReadBackError,
    ReorderResult,
    SheetChangedError,
    reorder_rows,
)
from gdrives.sheets.order import _Blank, _increasing, _plan_moves

READ = "values.get"
GRID = "spreadsheets.get"
STRUCTURE = "spreadsheets.batchUpdate"


def replay(rows, moves):
    """Apply ``moves`` to a copy of ``rows`` as the API applies them.

    Each move must change something: the API refuses a destination equal to
    the row's own index, and the one just past it moves nothing.
    """
    rows = list(rows)
    for move in moves:
        assert 0 <= move.start < len(rows)
        assert 0 <= move.destination <= len(rows)
        assert move.destination not in (move.start, move.start + 1)
        assert rows[move.start] == move.row
        row = rows.pop(move.start)
        rows.insert(
            move.destination - 1 if move.destination > move.start else move.destination,
            row,
        )
    return rows


def fewest(current, target):
    """The fewest single-row moves that leave every blank row unmoved.

    The rows left unmoved keep their order, so they are an increasing run of
    places in ``target`` that holds every blank. This finds the longest such
    run by a quadratic search, weighting a blank past every keyed row so the
    longest run takes them all, independently of the planner's own method.
    """
    place = {row: index for index, row in enumerate(target)}
    heavy = len(current) + 1
    weights = [heavy if isinstance(row, _Blank) else 1 for row in current]
    best: list[int] = []
    for i, row in enumerate(current):
        before = [best[j] for j in range(i) if place[current[j]] < place[row]]
        best.append(weights[i] + max(before, default=0))
    blanks = sum(isinstance(row, _Blank) for row in current)
    longest = max(best, default=0)
    assert longest >= blanks * heavy, "a run holding every blank always exists"
    return len(current) - blanks - (longest - blanks * heavy)


def lis(values):
    """The length of the longest strictly increasing run, by brute force."""
    for size in range(len(values), 0, -1):
        for picked in itertools.combinations(values, size):
            if list(picked) == sorted(set(picked)):
                return size
    return 0


def lay_out(slots, keys):
    """A block of ``slots`` places, blank where ``slots`` is False, keyed in order."""
    rows = iter(keys)
    return [next(rows) if filled else _Blank(at) for at, filled in enumerate(slots)]


def check(current, target):
    """The planner's moves reach ``target``, and are as few as can be."""
    moves = _plan_moves(current, target)
    assert replay(current, moves) == list(target)
    assert not any(isinstance(move.row, _Blank) for move in moves)
    assert len(moves) == fewest(current, target)
    return moves


class TestIncreasing:
    @pytest.mark.parametrize(
        "values",
        [[], [0], [3, 1, 2], [2, 1, 0], [0, 4, 1, 2, 5, 3], [5, 0, 6, 1, 7, 2]],
    )
    def test_a_longest_increasing_run(self, values):
        kept = sorted(_increasing(values))
        picked = [values[i] for i in kept]
        assert picked == sorted(picked)
        assert len(set(picked)) == len(picked) == lis(values)


class TestPlanMoves:
    def test_rows_in_order_make_no_move(self):
        assert check(list("abcd"), list("abcd")) == []

    def test_one_row_out_of_place_is_one_move(self):
        # An appended row that belongs at the top.
        (move,) = check(list("bcda"), list("abcd"))
        assert (move.row, move.start, move.destination) == ("a", 3, 0)

    def test_a_row_moved_down_names_one_past_its_place(self):
        (move,) = check(list("bacd"), list("acdb"))
        assert (move.row, move.start, move.destination) == ("b", 0, 4)

    def test_a_reversal_moves_every_row_but_one(self):
        assert len(check(list("abcde"), list("edcba"))) == 4

    def test_blank_rows_keep_their_places(self):
        current = lay_out([True, False, True, True, False, True], "dcba")
        target = lay_out([True, False, True, True, False, True], "abcd")
        moves = check(current, target)
        # a and d cross the blank rows; one of b and c stays between them.
        assert len(moves) == 3
        assert {"a", "d"} <= {move.row for move in moves}

    def test_a_row_that_must_cross_a_blank_row_is_moved(self):
        # Keeping y in place would take one move, and move the blank row.
        blank = _Blank(1)
        assert len(check(["x", blank, "y"], ["y", blank, "x"])) == 2

    def test_every_arrangement_of_up_to_six_rows(self):
        for size in range(7):
            for slots in itertools.product([True, False], repeat=size):
                keys = list(range(sum(slots)))
                target = lay_out(slots, keys)
                for shuffled in itertools.permutations(keys):
                    check(lay_out(slots, shuffled), target)

    @pytest.mark.parametrize("seed", range(200))
    def test_random_arrangements_with_blank_rows_mixed_in(self, seed):
        chance = random.Random(seed)
        size = chance.randint(0, 60)
        slots = [chance.random() > 0.2 for _ in range(size)]
        keys = list(range(sum(slots)))
        shuffled = list(keys)
        if chance.random() < 0.3:
            # Nearly in order: a few rows out of place, as appends leave it.
            for _ in range(chance.randint(1, 3)):
                shuffled.append(shuffled.pop(chance.randrange(len(shuffled) or 1)))
        else:
            chance.shuffle(shuffled)
        current = lay_out(slots, shuffled)
        moves = check(current, lay_out(slots, keys))
        if all(slots):
            # With no blank row, the fewest moves leave a longest run in place.
            places = [keys.index(row) for row in shuffled]
            assert len(moves) == len(keys) - len(_increasing(places))


HEADER = ["id", "name", "", "status"]
ROWS = [
    ["b", "Bo", "x", "open"],
    ["c", "Cy", "", "closed"],
    ["a", "Ada", "y", "open"],
]


def sheet(*rows, header=HEADER, **size):
    return FakeSheetGrid({"T": [header, *rows]}, **size)


def moves_sent(grid):
    """The ``moveDimension`` requests of the one structural batch sent."""
    (body,) = [kwargs["body"] for method, kwargs in grid.calls if method == STRUCTURE]
    return [request["moveDimension"] for request in body["requests"]]


@pytest.fixture
def no_sleep(monkeypatch):
    """Retries back off without waiting."""
    monkeypatch.setattr("gdrives.sheets.retry.time.sleep", lambda seconds: None)


class TestPreview:
    def test_a_preview_reads_once_and_writes_nothing(self):
        grid = sheet(*ROWS)
        result = reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"])
        assert result == ReorderResult(
            moves=1, moved=[("a",)], unchanged=False, applied=False
        )
        assert grid.methods == [READ]
        assert grid.values("T") == [HEADER, *ROWS]

    def test_a_tab_in_order_is_unchanged(self):
        grid = sheet(*ROWS)
        result = reorder_rows(grid, "S", "T", ["id"], ["b", "c", "a"], apply=True)
        assert result == ReorderResult(0, [], unchanged=True, applied=False)
        assert grid.methods == [READ]


class TestApply:
    def test_rows_move_whole_to_the_order(self):
        grid = sheet(*ROWS)
        grid.format("T", 4, 2)["bold"] = True  # row a's name
        result = reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert result == ReorderResult(1, [("a",)], unchanged=False, applied=True)
        # The unnamed column, never read into a record, moved with its row.
        assert grid.values("T") == [HEADER, ROWS[2], ROWS[0], ROWS[1]]
        assert grid.format("T", 2, 2) == {"bold": True}
        assert grid.format("T", 4, 2) == {}
        assert grid.methods == [READ, GRID, READ, STRUCTURE, READ]

    def test_moves_are_sent_in_the_api_s_coordinates(self):
        grid = sheet(*ROWS)
        reorder_rows(grid, "S", "T", ["id"], ["c", "a", "b"], apply=True)
        # b, the first data row (grid row 1), moves down past a to the end.
        assert moves_sent(grid) == [
            {
                "source": {
                    "sheetId": 0,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": 2,
                },
                "destinationIndex": 4,
            }
        ]
        assert [row[0] for row in grid.values("T")[1:]] == ["c", "a", "b"]

    def test_blank_rows_keep_their_places(self):
        grid = sheet(["c", "Cy"], [], ["a", "Ada"], ["d", "Di"], [], ["b", "Bo"])
        result = reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c", "d"], apply=True)
        assert result.applied
        assert [row[:1] for row in grid.values("T")[1:]] == [
            ["a"],
            [],
            ["b"],
            ["c"],
            [],
            ["d"],
        ]

    def test_a_composite_key_and_normalized_keys(self):
        header = ["year", "id", "v"]
        rows = [["2026", "2", "x"], ["2025", "1", "y"], ["2026", "1", "z"]]
        grid = sheet(*rows, header=header)
        order = [(" 2025", "1"), ("2026 ", "1"), ("2026", " 2 ")]
        result = reorder_rows(grid, "S", "T", ["year", "id"], order, apply=True)
        assert result.moved == [("2026", "2")]
        assert grid.values("T") == [header, rows[1], rows[2], rows[0]]

    def test_a_non_default_sheet_id(self):
        grid = FakeSheetGrid({"Other": [["x"]], "T": [["id"], ["b"], ["a"]]})
        reorder_rows(grid, "S", "T", ["id"], ["a", "b"], apply=True)
        assert moves_sent(grid)[0]["source"]["sheetId"] == 1
        assert grid.values("T") == [["id"], ["a"], ["b"]]
        assert grid.values("Other") == [["x"]]

    def test_a_failed_write_is_raised_and_not_retried(self, no_sleep):
        grid = sheet(*ROWS)
        grid.fail(STRUCTURE, http_error(503, "unavailable"))
        with pytest.raises(HttpError):
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert grid.methods.count(STRUCTURE) == 1
        assert grid.values("T") == [HEADER, *ROWS]

    def test_a_rate_limit_is_retried(self, no_sleep):
        grid = sheet(*ROWS)
        grid.fail(STRUCTURE, http_error(429, "rate limited"))
        result = reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert result.applied
        assert grid.methods.count(STRUCTURE) == 2


class TestRefusals:
    def test_no_key_is_refused_before_any_request(self):
        grid = sheet(*ROWS)
        with pytest.raises(ValueError, match="needs key columns"):
            reorder_rows(grid, "S", "T", [], ["a"])
        assert grid.calls == []

    @pytest.mark.parametrize(
        ("order", "message"),
        [
            (["a", "b"], r"rows the order does not name: \[\('c',\)\]"),
            (["a", "b", "c", "z"], r"keys the tab lacks: \[\('z',\)\]"),
            (["a", "b", "c", "a"], r"keys the order repeats: \[\('a',\)\]"),
            (
                ["a", "b", "c", ("d", "e")],
                r"keys that are not 1 cell\(s\) long: \[\('d', 'e'\)\]",
            ),
        ],
    )
    def test_an_order_that_does_not_fit_is_refused(self, order, message):
        grid = sheet(*ROWS)
        with pytest.raises(ValueError, match=message):
            reorder_rows(grid, "S", "T", ["id"], order, apply=True)
        assert grid.methods == [READ]

    def test_every_problem_is_listed_at_once(self):
        grid = sheet(["a", "1"], ["b", "2"], ["c", "3"], header=["id", "n"])
        with pytest.raises(ValueError) as raised:
            reorder_rows(grid, "S", "T", ["id", "n"], ["a", ("b", "2"), ("b", "2")])
        assert str(raised.value) == (
            "tab 'T': the order does not fit the tab: "
            "rows the order does not name: [('a', '1'), ('c', '3')]; "
            "keys the order repeats: [('b', '2')]; "
            "keys that are not 2 cell(s) long: [('a',)]"
        )

    def test_a_blank_or_repeated_key_on_the_tab_is_refused(self):
        grid = sheet(["a", "1"], ["", "2"], ["a", "3"], header=["id", "n"])
        with pytest.raises(ValueError, match=r"blank key \['id'\] in rows \[3\]"):
            reorder_rows(grid, "S", "T", ["id"], ["a"])

    def test_blank_keys_partial_takes_a_blank_component(self):
        grid = sheet(["a", ""], ["a", "1"], header=["id", "n"])
        result = reorder_rows(
            grid,
            "S",
            "T",
            ["id", "n"],
            [("a", "1"), ("a", "")],
            apply=True,
            blank_keys="partial",
        )
        assert result.moves == 1
        assert grid.values("T") == [["id", "n"], ["a", "1"], ["a"]]


class TestGuard:
    def test_a_tab_changed_since_the_preview_read_is_not_written(self):
        grid = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["b", "Bea"]], row=2), before=READ, occurrence=2
        )
        with pytest.raises(SheetChangedError, match=r"rows edited: \[\('b',\)\]"):
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert STRUCTURE not in grid.methods

    def test_a_row_moved_since_the_preview_read_is_not_written(self):
        grid = sheet(*ROWS)
        insert = {
            "insertDimension": {
                "range": {
                    "sheetId": 0,
                    "dimension": "ROWS",
                    "startIndex": 1,
                    "endIndex": 2,
                }
            }
        }
        grid.edit_externally(
            lambda g: g._batch_update(spreadsheetId="S", body={"requests": [insert]}),
            before=READ,
            occurrence=2,
        )
        with pytest.raises(SheetChangedError, match="rows moved"):
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert STRUCTURE not in grid.methods

    def test_a_reread_that_fails_to_parse_is_a_change(self):
        grid = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["a"]], row=2), before=READ, occurrence=2
        )
        with pytest.raises(SheetChangedError, match="duplicate key"):
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert STRUCTURE not in grid.methods


class TestReadBack:
    def test_a_row_that_no_longer_holds_its_cells(self):
        grid = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["a", "Al"]], row=2), before=READ, occurrence=3
        )
        with pytest.raises(ReadBackError) as raised:
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert str(raised.value) == (
            "tab 'T': the read-back does not match the moves: row ('a',) held "
            "{'id': 'a', 'name': 'Ada', 'status': 'open'}, now holds "
            "{'id': 'a', 'name': 'Al', 'status': 'open'}"
        )

    def test_rows_out_of_place_or_missing(self):
        grid = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["c", "Cy", "", "closed"], ["z"]], row=3),
            before=READ,
            occurrence=3,
        )
        with pytest.raises(ReadBackError) as raised:
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
        assert str(raised.value) == (
            "tab 'T': the read-back does not match the moves: "
            "rows [('b',)] not found; row ('c',) is in row 3, not 4"
        )

    def test_a_read_back_that_fails_to_parse(self):
        grid = sheet(*ROWS)
        grid.edit_externally(
            lambda g: g.write("T", [["b"]], row=2), before=READ, occurrence=3
        )
        with pytest.raises(ReadBackError, match="duplicate key"):
            reorder_rows(grid, "S", "T", ["id"], ["a", "b", "c"], apply=True)
