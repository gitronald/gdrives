"""Hooks a config names: ``module:function``, found when a run starts.

A tab's ``hooks`` (:attr:`~gdrives.sheets.config.TabConfig.hooks`) names the
functions that run as its ``validate``, ``check``, ``warn``, and
``transform``. Reading a config imports nothing: the names are checked by
form only. :func:`resolve_hooks` imports the modules and looks the names up,
and a run does so before its first request, listing every name that does not
resolve at once.

A module is looked for on ``sys.path`` alone, as ``import`` would find it:
installed in the environment, or on ``PYTHONPATH``. The config's directory is
not added, so a file beside the config shadows nothing and is not run.

A function found this way is wrapped: whatever it raises, and a return that
is not a list of messages (for ``transform``, not a list of rows), becomes a
ValueError naming the tab, the hook, and the name, so a run reports it as the
tab's error and goes on to the next tab. A hook given in code is not wrapped.
"""

import importlib
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

from gdrives.sheets.config import ConfigError, TabConfig, Target

#: A function a config names, as found; what it takes depends on its hook.
Hook = Callable[..., Any]

_A = TypeVar("_A")


def resolve_hooks(
    target: Target, tabs: Sequence[str] | None = None
) -> dict[str, dict[str, Hook]]:
    """Import and look up the hooks of ``target``'s tabs (or just ``tabs``).

    Returns each tab's hooks by title, then by hook, wrapped as the module
    docstring says; a tab with no ``hooks`` maps to an empty dict, and
    nothing is imported for it. Raises :class:`ConfigError` listing every
    name that does not resolve: a module that does not import, a name the
    module lacks, or a value that is not callable. Makes no request.
    """
    wanted = [target.tab(title) for title in tabs] if tabs is not None else target.tabs
    resolved: dict[str, dict[str, Hook]] = {}
    problems: list[str] = []
    for tab in wanted:
        hooks, found = _resolve_tab(tab)
        resolved[tab.title] = hooks
        problems.extend(f"tab {tab.title!r}: {problem}" for problem in found)
    if problems:
        raise ConfigError(f"target {target.name!r}", problems)
    return resolved


def tab_hooks(tab: TabConfig) -> dict[str, Hook]:
    """The hooks ``tab`` names, found and wrapped; empty for a tab with none.

    Raises :class:`ConfigError`, a ValueError, listing every name that does
    not resolve.
    """
    hooks, problems = _resolve_tab(tab)
    if problems:
        raise ConfigError(f"tab {tab.title!r}", problems)
    return hooks


def _resolve_tab(tab: TabConfig) -> tuple[dict[str, Hook], list[str]]:
    """``tab``'s hooks found and wrapped, and what kept a name from resolving."""
    hooks: dict[str, Hook] = {}
    problems: list[str] = []
    for hook, name in tab.hooks.items():
        try:
            found = _find(name)
        except ValueError as e:
            problems.append(f"hook {hook!r} {name!r}: {e}")
            continue
        hooks[hook] = _guarded(f"tab {tab.title!r}", hook, name, found)
    return hooks, problems


def _find(name: str) -> Hook:
    """The callable ``module:function`` names; ValueError saying why not."""
    module_name, function = name.split(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as e:  # import-time code may raise anything
        raise ValueError(
            f"cannot import {module_name!r}: {type(e).__name__}: {e}"
        ) from None
    if not hasattr(module, function):
        raise ValueError(f"module {module_name!r} has no {function!r}")
    found = getattr(module, function)
    if not callable(found):
        raise ValueError(f"{function!r} is not callable: it is {_shown(found)}")
    return found


def _guarded(where: str, hook: str, name: str, function: Hook) -> Hook:
    """``function`` with what it raises and a wrong return made ValueErrors."""
    label = f"{where}: hook {hook} {name!r}"

    def run(given: Any) -> list[Any]:
        try:
            result = function(given)
        except Exception as e:
            raise ValueError(f"{label} raised {type(e).__name__}: {e}") from e
        if hook == "transform":
            if isinstance(result, (str, bytes)) or not isinstance(result, Sequence):
                raise ValueError(
                    f"{label} returned a {type(result).__name__}, not a list of rows"
                )
            return list(result)
        # The return is named by its types alone: it may hold a tab's cells.
        what = f"a {type(result).__name__}"
        if isinstance(result, (list, tuple)):
            other = [item for item in result if not isinstance(item, str)]
            if not other:
                return list(result)
            what += f" holding a {type(other[0]).__name__}"
        raise ValueError(f"{label} returned {what}, not a list of messages")

    return run


def _shown(value: Any, limit: int = 60) -> str:
    """``value``'s repr, cut to ``limit`` characters."""
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _joined(
    first: Callable[[_A], list[str]] | None, second: Callable[[_A], list[str]] | None
) -> Callable[[_A], list[str]] | None:
    """A hook that returns ``first``'s messages, then ``second``'s."""
    if first is None or second is None:
        return first if second is None else second

    def both(given: _A) -> list[str]:
        return [*first(given), *second(given)]

    return both


def _chained(
    first: Callable[[_A], _A] | None, second: Callable[[_A], _A] | None
) -> Callable[[_A], _A] | None:
    """A transform that runs ``first``, then ``second`` on what it returns."""
    if first is None or second is None:
        return first if second is None else second

    def both(given: _A) -> _A:
        return second(first(given))

    return both
