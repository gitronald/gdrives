"""Shared pytest fixtures and hooks for gdrives tests."""

from collections import Counter
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def mock_service():
    """Return a MagicMock Drive API service."""
    return MagicMock()


@pytest.hookimpl(trylast=True)  # after pytest-cov's table, next to the final counts
def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    """Note live integration tests that skipped for lack of configuration.

    They skip rather than fail when no test sheet/doc or service account is set,
    so CI and new contributors stay green, but a plain run only counts skips.
    This names each reason once so the gap is visible without ``-rs``, and says
    nothing when the live tests ran or were deselected.
    """
    reasons: Counter[str] = Counter()
    for report in terminalreporter.stats.get("skipped", []):
        if "integration" in report.keywords and isinstance(report.longrepr, tuple):
            reasons[report.longrepr[2].removeprefix("Skipped: ")] += 1
    if not reasons:
        return
    terminalreporter.write_sep("-", "live integration tests skipped", yellow=True)
    for reason, count in reasons.items():
        terminalreporter.write_line(f"{count} skipped: {reason}")
    terminalreporter.write_line(
        'To run them, see "Live integration tests" under Development in README.md.'
    )
