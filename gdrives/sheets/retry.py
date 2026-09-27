"""Retry Sheets API calls with exponential backoff and jitter.

Which failures are worth retrying depends on whether the call is safe to repeat.
A read, or a write that overwrites a fixed range with fixed values, lands in
the same state however many times it runs, so any transient failure (a rate
limit or a 5xx) is retried. A call that adds something (an appended row, an
inserted row or column, a new rule) may already have been applied when the
server answered 5xx, and repeating it would apply it twice, so only a 429,
which the API returns before doing anything, is retried.

A wait is silent unless someone asks: ``on_retry`` is called before each one,
and :func:`retry_notices` sets that callback for every call inside a block,
the wrappers' own included.
"""

import random
import time
from collections.abc import Callable, Collection, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

from googleapiclient.errors import HttpError

_T = TypeVar("_T")

#: Statuses retried for an idempotent call: reads and range overwrites.
IDEMPOTENT_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Statuses retried for a call that is not safe to repeat: the rate limit only.
RATE_LIMIT_STATUSES = frozenset({429})


@dataclass(frozen=True)
class RetryNotice:
    """One wait of :func:`with_retry`: why, how long, and which attempt is next.

    ``status`` is the status the API returned, ``delay`` the seconds about to
    be waited, ``attempt`` the 1-based attempt that follows the wait, and
    ``attempts`` the most that will be made.
    """

    status: int
    delay: float
    attempt: int
    attempts: int


#: A callback told of each wait before it starts.
OnRetry = Callable[[RetryNotice], None]

# The callback of the enclosing retry_notices block, if any. A context
# variable, so it does not reach another thread or outlive its block.
_notices: ContextVar[OnRetry | None] = ContextVar("gdrives_retry_notices", default=None)


@contextmanager
def retry_notices(callback: OnRetry) -> Iterator[None]:
    """Tell ``callback`` of every wait of a :func:`with_retry` inside the block.

    The value wrappers call :func:`with_retry` themselves, so a caller cannot
    pass them ``on_retry``; this sets it for every call in the block that was
    given none. Blocks nest, the inner one winning, and the callback is unset
    when its block ends.
    """
    token = _notices.set(callback)
    try:
        yield
    finally:
        _notices.reset(token)


def with_retry(
    call: Callable[[], _T],
    *,
    statuses: Collection[int] = IDEMPOTENT_STATUSES,
    attempts: int = 5,
    base_delay: float = 1.0,
    max_delay: float = 32.0,
    sleep: Callable[[float], None] | None = None,
    jitter: Callable[[], float] | None = None,
    on_retry: OnRetry | None = None,
) -> _T:
    """Run ``call``, retrying an ``HttpError`` whose status is in ``statuses``.

    Makes up to ``attempts`` calls in all. Before retry ``n`` (0-based) it
    waits ``base_delay * 2**n`` seconds plus up to one ``base_delay`` of jitter,
    capped at ``max_delay``, so clients that failed together do not retry in
    step. Any other error, and the last retryable one, propagates unchanged.

    ``sleep`` and ``jitter`` (a source of floats in ``[0, 1)``) default to
    :func:`time.sleep` and :func:`random.random`, looked up at call time; tests
    pass their own so they never wait and always see the same delays.

    ``on_retry`` is called with a :class:`RetryNotice` before each wait, and
    defaults to the callback of the enclosing :func:`retry_notices` block.
    With neither, a wait is silent.
    """
    if attempts < 1:
        raise ValueError(f"attempts must be at least 1, got {attempts}")
    pause = time.sleep if sleep is None else sleep
    draw = random.random if jitter is None else jitter
    notify = on_retry if on_retry is not None else _notices.get()
    for attempt in range(attempts - 1):
        try:
            return call()
        except HttpError as e:
            if e.resp.status not in statuses:
                raise
            status = e.resp.status
        delay = min(max_delay, base_delay * (2**attempt + draw()))
        if notify is not None:
            notify(RetryNotice(status, delay, attempt + 2, attempts))
        pause(delay)
    return call()
