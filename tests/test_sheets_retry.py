"""Tests for gdrives.sheets.retry and its wiring into the values wrappers.

``with_retry`` takes its sleep and its jitter as parameters, so these tests
record the delays instead of waiting and see the same jitter every run. The
wrappers use the defaults, so their tests patch ``time.sleep`` instead.
"""

import time

import pytest
from helpers import FakeSheetsService, http_error

from gdrives.sheets import (
    IDEMPOTENT_STATUSES,
    RATE_LIMIT_STATUSES,
    append_values,
    batch_update_spreadsheet,
    batch_update_values,
    clear_values,
    list_tabs,
    pull_many,
    pull_values,
    tab_sheet_ids,
    update_values,
    with_retry,
)


class Flaky:
    """A call that raises each queued error in turn, then returns ``result``."""

    def __init__(self, errors: list[Exception], result: str = "ok") -> None:
        self.errors = errors
        self.result = result
        self.calls = 0

    def __call__(self) -> str:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


def run(call, **kwargs):
    """Run ``with_retry`` with recorded, zero-length sleeps and fixed jitter."""
    delays: list[float] = []
    kwargs.setdefault("jitter", lambda: 0.25)
    result = with_retry(call, sleep=delays.append, **kwargs)
    return result, delays


class TestStatusSets:
    def test_idempotent_set(self):
        assert IDEMPOTENT_STATUSES == {429, 500, 502, 503, 504}

    def test_rate_limit_set(self):
        assert RATE_LIMIT_STATUSES == {429}


class TestWithRetry:
    def test_success_makes_one_call_and_never_sleeps(self):
        call = Flaky([])
        assert run(call) == ("ok", [])
        assert call.calls == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    def test_retries_each_idempotent_status(self, status):
        call = Flaky([http_error(status, "transient")])
        assert run(call) == ("ok", [1.25])
        assert call.calls == 2

    def test_backoff_doubles_with_jitter(self):
        call = Flaky([http_error(503, "unavailable")] * 3)
        result, delays = run(call)
        assert result == "ok"
        # base_delay * (2**n + jitter) for n = 0, 1, 2.
        assert delays == [1.25, 2.25, 4.25]

    def test_delay_is_capped(self):
        call = Flaky([http_error(429, "rate")] * 3)
        _, delays = run(call, base_delay=10.0, max_delay=15.0)
        assert delays == [12.5, 15.0, 15.0]

    def test_gives_up_after_attempts_and_raises_the_last_error(self):
        errors = [http_error(500, f"fail {n}") for n in range(3)]
        last = errors[-1]
        call = Flaky(errors)
        delays: list[float] = []
        with pytest.raises(type(last)) as raised:
            with_retry(call, attempts=3, sleep=delays.append, jitter=lambda: 0.0)
        assert raised.value is last
        assert call.calls == 3
        assert delays == [1.0, 2.0]  # no sleep after the final attempt

    def test_status_outside_the_set_is_not_retried(self):
        call = Flaky([http_error(403, "forbidden")])
        delays: list[float] = []
        with pytest.raises(Exception, match="forbidden"):
            with_retry(call, sleep=delays.append)
        assert call.calls == 1
        assert delays == []

    def test_rate_limit_set_does_not_retry_a_5xx(self):
        call = Flaky([http_error(503, "unavailable")])
        with pytest.raises(Exception, match="unavailable"):
            with_retry(call, statuses=RATE_LIMIT_STATUSES, sleep=lambda s: None)
        assert call.calls == 1

    def test_rate_limit_set_retries_a_429(self):
        call = Flaky([http_error(429, "rate")])
        assert run(call, statuses=RATE_LIMIT_STATUSES) == ("ok", [1.25])

    def test_other_exceptions_propagate_at_once(self):
        call = Flaky([ConnectionResetError("reset")])
        with pytest.raises(ConnectionResetError):
            with_retry(call, sleep=lambda s: None)
        assert call.calls == 1

    def test_single_attempt_never_retries(self):
        call = Flaky([http_error(503, "unavailable")])
        with pytest.raises(Exception, match="unavailable"):
            with_retry(call, attempts=1, sleep=lambda s: None)
        assert call.calls == 1

    def test_zero_attempts_is_refused(self):
        call = Flaky([])
        with pytest.raises(ValueError, match="attempts must be at least 1"):
            with_retry(call, attempts=0)
        assert call.calls == 0

    def test_defaults_are_looked_up_at_call_time(self, monkeypatch):
        # Patching time.sleep and random.random reaches with_retry's defaults,
        # which is how the wrappers' tests avoid waiting.
        delays: list[float] = []
        monkeypatch.setattr(time, "sleep", delays.append)
        monkeypatch.setattr("random.random", lambda: 0.5)
        call = Flaky([http_error(502, "bad gateway")])
        assert with_retry(call) == "ok"
        assert delays == [1.5]


# -- wiring into the values wrappers --


@pytest.fixture
def no_wait(monkeypatch):
    """Record the wrappers' backoff sleeps instead of waiting them out."""
    delays: list[float] = []
    monkeypatch.setattr(time, "sleep", delays.append)
    return delays


IDEMPOTENT_CALLS = [
    ("get", "values.get", lambda svc: pull_values(svc, "sid", "A1")),
    ("batchGet", "values.batchGet", lambda svc: pull_many(svc, "sid", ["A1"])),
    ("update", "values.update", lambda svc: update_values(svc, "sid", "A1", [["x"]])),
    ("clear", "values.clear", lambda svc: clear_values(svc, "sid", "A1")),
    (
        "batchUpdate",
        "values.batchUpdate",
        lambda svc: batch_update_values(svc, "sid", [("A1", [["x"]])]),
    ),
    ("meta", "spreadsheets.get", lambda svc: list_tabs(svc, "sid")),
    ("meta", "spreadsheets.get", lambda svc: tab_sheet_ids(svc, "sid")),
]

ADDING_CALLS = [
    ("append", "values.append", lambda svc: append_values(svc, "sid", "A1", [["x"]])),
    (
        "spreadsheetBatchUpdate",
        "spreadsheets.batchUpdate",
        lambda svc: batch_update_spreadsheet(svc, "sid", [{"addSheet": {}}]),
    ),
]


class TestWrappersRetry:
    @pytest.mark.parametrize(("key", "method", "invoke"), IDEMPOTENT_CALLS)
    def test_idempotent_call_retries_a_5xx(self, no_wait, key, method, invoke):
        svc = FakeSheetsService(**{key: [http_error(503, "unavailable"), {}]})
        invoke(svc)
        assert [m for m, _ in svc.calls] == [method, method]
        assert len(no_wait) == 1

    @pytest.mark.parametrize(("key", "method", "invoke"), ADDING_CALLS)
    def test_adding_call_does_not_retry_a_5xx(self, no_wait, key, method, invoke):
        svc = FakeSheetsService(**{key: [http_error(503, "unavailable"), {}]})
        with pytest.raises(Exception, match="unavailable"):
            invoke(svc)
        assert [m for m, _ in svc.calls] == [method]
        assert no_wait == []

    @pytest.mark.parametrize(("key", "method", "invoke"), ADDING_CALLS)
    def test_adding_call_retries_a_429(self, no_wait, key, method, invoke):
        svc = FakeSheetsService(**{key: [http_error(429, "rate"), {}]})
        invoke(svc)
        assert [m for m, _ in svc.calls] == [method, method]
        assert len(no_wait) == 1

    def test_retried_read_returns_the_successful_response(self, no_wait):
        svc = FakeSheetsService(get=[http_error(500, "boom"), {"values": [["a"]]}])
        assert pull_values(svc, "sid", "A1") == [["a"]]

    def test_client_error_propagates_without_waiting(self, no_wait):
        svc = FakeSheetsService(get=http_error(404, "not found"))
        with pytest.raises(Exception, match="not found"):
            pull_values(svc, "sid", "A1")
        assert len(svc.calls) == 1
        assert no_wait == []
