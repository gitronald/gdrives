"""Tests for the ``render`` tab setting: reading a tab as the sheet displays it.

``unformatted``, the default, reads numbers and booleans as values, and
``formatted`` reads every cell as the sheet displays it. The fake service
returns a cell's displayed text for a formatted read, including the text of a
number format a test gives the cell (``FakeSheetGrid.display``).
"""

import pytest
from default_requests import (
    DEFAULT_CREATED_CALLS,
    DEFAULT_PULL_CALLS,
    DEFAULT_PUSH_CALLS,
    DEFAULT_SYNC_CALLS,
    default_created,
    default_pull,
    default_push,
    default_sync,
)


class TestDefaultRequests:
    """A run that does not set ``render`` sends the requests it sent before."""

    @pytest.mark.parametrize(
        ("scenario", "expected"),
        [
            (default_sync, DEFAULT_SYNC_CALLS),
            (default_created, DEFAULT_CREATED_CALLS),
            (default_pull, DEFAULT_PULL_CALLS),
            (default_push, DEFAULT_PUSH_CALLS),
        ],
    )
    def test_a_default_run_sends_the_recorded_requests(
        self, tmp_path, scenario, expected
    ):
        assert scenario(tmp_path).calls == expected
