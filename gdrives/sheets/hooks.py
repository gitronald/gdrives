"""Code a config names as ``module:name``, found when a run starts.

A tab's ``hooks`` (:attr:`~gdrives.sheets.config.TabConfig.hooks`) names the
functions that run as its ``validate``, ``check``, ``warn``, and
``transform``, and its ``schema`` may name its columns' schema
(:attr:`~gdrives.sheets.config.TabConfig.schema_ref`), with a key after it
for an entry of a registry (``module:attribute[key]``). Reading a config
imports nothing: the names are checked by form only. :func:`resolve_target`
imports the modules and looks the names up, and a run does so before its
first request, listing every name that does not resolve, and every problem
of a schema found, at once. :func:`resolve_hooks` does the same for the hooks
alone.

A module is looked for on ``sys.path`` alone, as ``import`` would find it:
installed in the environment, or on ``PYTHONPATH``. The config's directory is
not added, so a file beside the config shadows nothing and is not run.

A function found this way is wrapped: whatever it raises, and a return that
is not a list of messages (for ``transform``, not a list of rows), becomes a
ValueError naming the tab, the hook, and the name, so a run reports it as the
tab's error and goes on to the next tab. A hook given in code is not wrapped.
"""

import importlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any, TypeVar

from gdrives.sheets.config import (
    ConfigError,
    TabConfig,
    Target,
    _checked_schema,
    schema_ref_parts,
)

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


def resolve_tab(tab: TabConfig) -> TabConfig:
    """``tab`` with the schema its ``schema_ref`` names, and its hooks checked.

    The module is imported and the attribute looked up: a mapping of column
    name to :class:`~gdrives.sheets.cells.ColumnSchema`, or a function that
    is given the tab's title and returns one. A reference with a key,
    ``module:attribute[key]``, names an entry of a registry: the attribute is
    a mapping of key to schema, or a function given the key, so tabs of
    different titles share one schema. The schema is then checked as
    a config's ``schema`` object is at load, against the tab's ``columns``,
    ``strict_schema``, and ``exclude``. Returns the tab with ``schema``
    filled and ``schema_ref`` None; a tab with no ``schema_ref`` is returned
    as it is. The tab's hook names are found in the same pass, so one
    :class:`ConfigError`, a ValueError, lists every name that does not
    resolve and every problem of the schema. Makes no request.
    """
    resolved, problems = _resolve(tab)
    if problems:
        raise ConfigError(f"tab {tab.title!r}", problems)
    return resolved


def resolve_target(target: Target, tabs: Sequence[str] | None = None) -> Target:
    """``target`` with its tabs (or just ``tabs``) resolved, as by :func:`resolve_tab`.

    One pass over the tabs asked for: every hook name and schema reference
    that does not resolve, and every problem of a schema found, is listed in
    one :class:`ConfigError` naming the target, each problem naming its tab.
    Returns the target with those tabs' schemas filled, the others as they
    were. Makes no request, and imports nothing for a tab with no hooks and
    no ``schema_ref``.
    """
    return _resolved(target, tabs, _resolve)


def resolve_schemas(target: Target, tabs: Sequence[str] | None = None) -> Target:
    """:func:`resolve_target` for the schema references alone.

    For a caller that reads the schemas and runs nothing else, such as a
    documentation export: it imports the modules the ``schema_ref`` of the
    tabs asked for name, and neither imports nor checks their hooks.
    """
    return _resolved(target, tabs, _resolve_schema)


def _resolved(
    target: Target,
    tabs: Sequence[str] | None,
    resolve: Callable[[TabConfig], tuple[TabConfig, list[str]]],
) -> Target:
    """``target`` with each tab asked for through ``resolve``, or a ConfigError."""
    chosen = [target.tab(title) for title in tabs] if tabs is not None else target.tabs
    found: list[TabConfig] = []
    problems: list[str] = []
    for tab in target.tabs:
        if not any(tab is wanted for wanted in chosen):
            found.append(tab)
            continue
        resolved, tab_problems = resolve(tab)
        found.append(resolved)
        problems.extend(f"tab {tab.title!r}: {problem}" for problem in tab_problems)
    if problems:
        raise ConfigError(f"target {target.name!r}", problems)
    return replace(target, tabs=tuple(found))


def _resolve(tab: TabConfig) -> tuple[TabConfig, list[str]]:
    """``tab`` with its schema resolved, and what kept its schema or hooks from it."""
    resolved, problems = _resolve_schema(tab)
    _, found = _resolve_tab(tab)
    return resolved, [*problems, *found]


def _resolve_schema(tab: TabConfig) -> tuple[TabConfig, list[str]]:
    """``tab`` with the schema ``schema_ref`` names, or ``tab`` and the problems."""
    if tab.schema_ref is None:
        return tab, []
    parts = schema_ref_parts(tab.schema_ref)
    assert parts is not None  # the tab checked its form
    name, key = parts
    where = f"schema {tab.schema_ref!r}"
    wanted = "a mapping of column name to ColumnSchema"
    try:
        found = _attribute(name)
    except ValueError as e:
        return tab, [f"{where}: {e}"]
    value: Any
    if callable(found) and not isinstance(found, Mapping):
        try:
            # A function is given the key it is asked for, else the title.
            value = found(tab.title if key is None else key)
        except Exception as e:  # a caller's code may raise anything
            return tab, [f"{where} raised {type(e).__name__}: {e}"]
        if not isinstance(value, Mapping):
            return tab, [f"{where} returned a {type(value).__name__}, not {wanted}"]
    elif not isinstance(found, Mapping):
        registry = "" if key is None else " for each key"
        return tab, [
            f"{where} is a {type(found).__name__}, not {wanted}{registry} or a "
            "function that returns one"
        ]
    elif key is None:
        value = found
    elif key not in found:
        return tab, [f"{where}: {name.split(':')[1]!r} has no entry {key!r}"]
    else:
        value = found[key]
        if not isinstance(value, Mapping):
            return tab, [
                f"{where}: entry {key!r} is a {type(value).__name__}, not {wanted}"
            ]
    schema, problems = _checked_schema(where, value, tab)
    if problems:
        return tab, problems
    return replace(tab, schema=schema, schema_ref=None), []


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


def _attribute(name: str) -> Any:
    """What ``module:attribute`` names; ValueError saying why it cannot be found."""
    module_name, attribute = name.split(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as e:  # import-time code may raise anything
        raise ValueError(
            f"cannot import {module_name!r}: {type(e).__name__}: {e}"
        ) from None
    if not hasattr(module, attribute):
        raise ValueError(f"module {module_name!r} has no {attribute!r}")
    return getattr(module, attribute)


def _find(name: str) -> Hook:
    """The callable ``module:function`` names; ValueError saying why not."""
    function = name.split(":")[1]
    found = _attribute(name)
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
