"""The sync config file: which tabs of which spreadsheet sync with which files.

``gdrives-sheets.json`` names one or more **targets**, each a spreadsheet with
the tabs to keep in step with local files::

    {
      "roster": {
        "spreadsheet": "https://docs.google.com/spreadsheets/d/<spreadsheet-id>",
        "base": "sheets-base/roster",
        "tabs": {
          "Members": {"mode": "sync", "local": "data/members.csv",
                      "key": ["member_id"]},
          "Summary": {"mode": "push", "local": "output/summary.csv"}
        }
      }
    }

:func:`load_config` finds the file from the working directory upward (or takes
a path), resolves every relative path in it against the file's own
directory, and checks all of it before anything touches the network. Every
problem found is collected and raised together as one :class:`ConfigError`,
each naming its target and tab, so a single run shows everything to fix. The
``spreadsheet`` value is kept as written; the caller resolves it.
"""

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gdrives.local import safe_filename
from gdrives.sheets.cells import BLANK_KEYS, COLUMN_TYPES, ColumnSchema
from gdrives.sheets.files import NEWLINES
from gdrives.sheets.stores import FileStore, JsonEntryStore, Store
from gdrives.sheets.structure import _rgb
from gdrives.sheets.values import RAW, RENDERS, USER_ENTERED

#: The config file's name, looked for in the working directory and its parents.
CONFIG_NAME = "gdrives-sheets.json"

#: The sync modes: ``sync`` merges both ways, ``pull`` and ``push`` replace.
MODES = frozenset({"sync", "pull", "push"})

#: How a sync tab with no base yet is started; ``--adopt`` is a flag instead.
BOOTSTRAPS = frozenset({"local"})

#: What a sync does with a sheet value that fails the schema: ``refuse``
#: writes nothing for the tab, and ``hold`` keeps that value out and goes on.
ON_INVALID = frozenset({"refuse", "hold"})

#: The ``valueInputOption`` a target writes with.
INPUT_OPTIONS = frozenset({RAW, USER_ENTERED})

#: The local file formats, by lower-cased extension.
LOCAL_EXTENSIONS = frozenset({".csv", ".tsv", ".json"})

#: The hooks a config may name, each as ``module:function``
#: (:mod:`gdrives.sheets.hooks`).
HOOKS = frozenset({"validate", "check", "warn", "transform"})

# The cache directory gdrives already uses, commonly ignored by version
# control; a base belongs where it is committed.
_CACHE_DIR = ".gdrives"

_TARGET_FIELDS = frozenset(
    {"spreadsheet", "base", "base_file", "input_option", "tabs", "hooks"}
)
_TAB_FIELDS = frozenset(
    {
        "mode",
        "local",
        "key",
        "columns",
        "exclude",
        "local_owned",
        "sheet_owned",
        "owns_rows",
        "schema",
        "bootstrap",
        "insert_above",
        "widths",
        "bom",
        "newline",
        "blank_keys",
        "on_invalid",
        "render",
        "clear_links",
        "sheet_id",
        "entry",
        "link_urls",
        "strict_schema",
        "hooks",
    }
)
_SCHEMA_FIELDS = frozenset({"type", "required", "allowed"})
# Fields that only mean something to a merge, so only to a sync tab.
_SYNC_ONLY = (
    "local_owned",
    "sheet_owned",
    "owns_rows",
    "bootstrap",
    "insert_above",
    "on_invalid",
)


class ConfigError(ValueError):
    """The config file is missing, unreadable, or invalid.

    ``problems`` lists every problem found; the message lists them too.
    """

    def __init__(self, source: str, problems: Sequence[str]) -> None:
        self.source = source
        self.problems = list(problems)
        lines = "\n".join(f"  - {problem}" for problem in self.problems)
        count = len(self.problems)
        super().__init__(
            f"{source}: {count} problem{'s' if count != 1 else ''}:\n{lines}"
        )


@dataclass(frozen=True)
class TabConfig:
    """One tab of a target, and the local side it is kept in step with.

    ``local`` is the local file, absolute (resolved against the config
    file's directory). A tab loaded from a config always has one. A tab built
    in code may give a ``store`` instead, and then ``local`` is None unless
    given too; :attr:`local_store` is what a run reads and writes. A tab
    with neither is refused.
    ``columns`` is the projection, or None for every column of the local
    side. ``exclude`` names columns a pull tab leaves out, so their values
    never reach the local file or a report; it applies only to a pull tab,
    and contradicts ``columns``. ``insert_above`` maps its one column to the
    values it matches.
    ``newline`` names the line ending (``"lf"`` or ``"crlf"``) a delimited
    local file is written with, and with it the tab's base. ``blank_keys``
    is ``"refuse"`` or ``"partial"``, as for
    :func:`~gdrives.sheets.cells.index_rows`. ``on_invalid`` is ``"refuse"``
    or ``"hold"``: what a sync does with a sheet value that fails ``schema``.
    ``render`` is ``"unformatted"`` or ``"formatted"``: how every read of the
    tab reads its cells (:func:`~gdrives.sheets.table.read_tab`).
    ``clear_links`` leaves the cells a sync or a push writes with no link,
    where the Sheets API links a URL when it is written. ``sheet_id`` names
    the tab by its ``sheetId``, which a rename leaves as it is: the tab is
    then found by it, and ``title`` is what reports and the base file call
    the tab. ``entry`` names an entry of ``local``, a ``.json`` file
    holding several: the tab's local side is then that entry
    (:class:`~gdrives.sheets.stores.JsonEntryStore`). ``link_urls`` is a
    ``#rrggbb`` colour: after a write, each URL cell the run wrote is given a
    link to its own text in that colour, not underlined
    (:func:`~gdrives.sheets.structure.set_url_links`). It contradicts
    ``clear_links``.
    ``strict_schema`` makes it a problem for a column of either side, less one
    a run is dropping, to have no ``schema`` entry: a carried local column and
    a sheet column outside the projection are checked too, not just the
    projection. With it, ``schema`` may also name a column outside
    ``columns``, which is refused otherwise.
    ``hooks`` maps a hook (:data:`HOOKS`) to the ``module:function`` that
    runs as it, only named here: nothing is imported until a run starts
    (:func:`~gdrives.sheets.hooks.resolve_hooks`). A push tab takes no
    ``transform``.
    """

    title: str
    local: Path | None = None
    mode: str = "sync"
    key: tuple[str, ...] = ()
    columns: tuple[str, ...] | None = None
    exclude: tuple[str, ...] = ()
    local_owned: tuple[str, ...] = ()
    sheet_owned: tuple[str, ...] = ()
    owns_rows: bool = False
    schema: Mapping[str, ColumnSchema] = field(default_factory=dict)
    bootstrap: str = "local"
    insert_above: Mapping[str, tuple[Any, ...]] | None = None
    widths: Mapping[str, int] = field(default_factory=dict)
    bom: bool = False
    newline: str = "lf"
    blank_keys: str = "refuse"
    on_invalid: str = "refuse"
    render: str = "unformatted"
    clear_links: bool = False
    sheet_id: int | None = None
    strict_schema: bool = False
    store: Store | None = None
    entry: str | None = None
    link_urls: str | None = None
    hooks: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.local is None and self.store is None:
            raise ValueError(
                f"tab {self.title!r}: give 'local', a file path, or 'store'"
            )
        if self.exclude and self.columns is not None:
            raise ValueError(
                f"tab {self.title!r}: 'exclude' and 'columns' contradict each other"
            )
        if self.entry is not None and (
            self.local is None or self.local.suffix.lower() != ".json"
        ):
            raise ValueError(f"tab {self.title!r}: 'entry' needs a .json 'local'")
        if self.link_urls is not None:
            if self.clear_links:
                raise ValueError(
                    f"tab {self.title!r}: 'link_urls' and 'clear_links' "
                    "contradict each other"
                )
            _rgb(self.link_urls)
        hook_problems = _hook_problems(self.hooks, self.mode)
        if hook_problems:
            raise ValueError(f"tab {self.title!r}: " + "; ".join(hook_problems))

    @property
    def types(self) -> dict[str, str]:
        """Each schema column's declared type, for writing a JSON file."""
        return {column: spec.type for column, spec in self.schema.items()}

    @property
    def local_store(self) -> Store:
        """The store of the local side: ``store``, or the file at ``local``.

        The file is read and written with the tab's ``types``, ``bom``, and
        ``newline``; with an ``entry``, it is that entry of the file, typed by
        ``types``.
        """
        if self.store is not None:
            return self.store
        assert self.local is not None  # __post_init__ refused a tab with neither
        if self.entry is not None:
            return JsonEntryStore(self.local, self.entry, types=self.types)
        return FileStore(
            self.local, types=self.types, bom=self.bom, newline=self.newline
        )


@dataclass(frozen=True)
class Target:
    """One spreadsheet and its tabs, in config order.

    ``spreadsheet`` is the URL, file ID, or Drive path as written in the
    config. ``base`` is the absolute directory holding the base snapshots,
    one CSV per tab, or None for a target built in code whose every sync tab
    has an entry in ``base_stores``: pull and push tabs never read a base, so
    a target of only those needs neither ``base`` nor ``base_stores``.
    ``base_stores`` maps a tab's title to the store that holds its base
    instead, for a base kept somewhere else; ``base`` is unused for a tab it
    names. A config's ``base_file`` becomes one
    :class:`~gdrives.sheets.stores.JsonEntryStore` here for each sync tab,
    the entry named by the tab's title and typed by its schema. A config
    always sets ``base``, to a default directory when none is given.
    """

    name: str
    spreadsheet: str
    base: Path | None = None
    tabs: tuple[TabConfig, ...] = ()
    input_option: str = RAW
    base_stores: Mapping[str, Store] = field(default_factory=dict)

    def tab(self, title: str) -> TabConfig:
        """The tab titled ``title``, raising ValueError naming the others."""
        for tab in self.tabs:
            if tab.title == title:
                return tab
        raise ValueError(
            f"target {self.name!r} has no tab {title!r}; "
            f"tabs: {[tab.title for tab in self.tabs]}"
        )

    def base_path(self, tab: TabConfig) -> Path:
        """The base snapshot file of ``tab``: one CSV per tab, named by title.

        Raises ValueError when ``base`` is None: a target built in code with
        no ``base`` directory and no entry for ``tab`` in ``base_stores``.
        """
        if self.base is None:
            raise ValueError(
                f"target {self.name!r} has no base directory, and tab "
                f"{tab.title!r} has no entry in base_stores"
            )
        return self.base / f"{safe_filename(tab.title)}.csv"

    def base_store(self, tab: TabConfig) -> Store:
        """The store of ``tab``'s base: its entry in ``base_stores``, or the file.

        The file is the one at :meth:`base_path`, written with the tab's
        ``newline``; :meth:`base_path` is reached, and can raise, only for a
        tab with no entry in ``base_stores``.
        """
        if tab.title in self.base_stores:
            return self.base_stores[tab.title]
        return FileStore(self.base_path(tab), newline=tab.newline)


@dataclass(frozen=True)
class Config:
    """A loaded config file: its path and its targets, in file order."""

    path: Path
    targets: Mapping[str, Target]

    def target(self, name: str) -> Target:
        """The target called ``name``, raising ValueError naming the others."""
        if name not in self.targets:
            raise ValueError(
                f"{self.path}: no target {name!r}; targets: {list(self.targets)}"
            )
        return self.targets[name]


def find_config(start: Path | None = None) -> Path:
    """Return the nearest ``gdrives-sheets.json`` in ``start`` or a parent.

    ``start`` defaults to the working directory, as for the ``.env`` file.
    Raises :class:`ConfigError` when no directory up to the root has one.
    """
    here = (start if start is not None else Path.cwd()).absolute()
    for directory in (here, *here.parents):
        candidate = directory / CONFIG_NAME
        if candidate.is_file():
            return candidate
    raise ConfigError(str(here), [f"no {CONFIG_NAME} here or in any parent directory"])


def load_config(path: str | Path | None = None) -> Config:
    """Read and check a config file; ``path`` defaults to :func:`find_config`.

    Relative paths inside the file resolve against the file's directory. Raises
    :class:`ConfigError` listing every problem at once.
    """
    found = Path(path).absolute() if path is not None else find_config()
    try:
        data = json.loads(found.read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise ConfigError(str(found), [f"cannot read the file: {e}"]) from None
    except json.JSONDecodeError as e:
        raise ConfigError(str(found), [f"not valid JSON: {e}"]) from None
    return parse_config(data, found)


def parse_config(data: Any, path: Path) -> Config:
    """Check the parsed JSON ``data`` of the config file at ``path``.

    Pure: nothing is read. Relative paths resolve against ``path``'s
    directory. Raises :class:`ConfigError` listing every problem at once.
    """
    checker = _Checker(path.parent)
    targets: dict[str, Target] = {}
    if not isinstance(data, dict):
        checker.problems.append("expected a JSON object of targets")
    elif not data:
        checker.problems.append("names no targets")
    else:
        for name, raw in data.items():
            target = checker.target(name, raw)
            if target is not None:
                targets[name] = target
        checker.collisions(targets.values())
    if checker.problems:
        raise ConfigError(str(path), checker.problems)
    return Config(path=path, targets=targets)


# -- checking --


def _with_defaults(tab: TabConfig, defaults: Mapping[str, str]) -> TabConfig:
    """``tab`` with its target's ``hooks`` under its own, hook by hook.

    A push tab is not given the target's ``transform``.
    """
    given = {
        hook: name
        for hook, name in defaults.items()
        if not (hook == "transform" and tab.mode == "push")
    }
    return replace(tab, hooks={**given, **tab.hooks})


def _is_names(value: Any) -> bool:
    """True for a list of non-blank strings."""
    return isinstance(value, list) and all(
        isinstance(item, str) and item.strip() for item in value
    )


def _is_scalar(value: Any) -> bool:
    """True for a value that can be one cell: a string, number, or boolean."""
    return isinstance(value, (str, int, float, bool))


def _is_hook_name(value: Any) -> bool:
    """True for a string of the form ``module:function``, dotted module allowed."""
    if not isinstance(value, str) or value.count(":") != 1:
        return False
    module, function = value.split(":")
    return function.isidentifier() and all(
        part.isidentifier() for part in module.split(".")
    )


def _hook_problems(hooks: Mapping[str, Any], mode: str) -> list[str]:
    """What is wrong with a tab's ``hooks`` of the given ``mode``, by form alone."""
    found: list[str] = []
    unknown = sorted(set(hooks) - set(HOOKS))
    if unknown:
        found.append(f"'hooks' names unknown hook(s) {unknown}; hooks: {sorted(HOOKS)}")
    for hook, name in hooks.items():
        if hook in HOOKS and not _is_hook_name(name):
            found.append(f"hook {hook!r} must be 'module:function', not {name!r}")
    if mode == "push" and "transform" in hooks:
        found.append("hook 'transform' applies only to pull and sync tabs")
    return found


def _repeated(names: Sequence[str]) -> list[str]:
    return sorted({name for name in names if list(names).count(name) > 1})


class _Checker:
    """Collects every problem in a config while building its dataclasses."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.problems: list[str] = []

    def _path(self, text: str) -> Path:
        """``text`` as an absolute path, relative ones against the config's folder.

        Normalized (``..`` folded) without resolving links, so two spellings of
        one file compare equal.
        """
        return Path(os.path.normpath(self.root / Path(text).expanduser()))

    def target(self, name: str, raw: Any) -> Target | None:
        where = f"target {name!r}"
        problems = self.problems
        start = len(problems)
        if not name.strip():
            problems.append("a target has a blank name")
        if not isinstance(raw, dict):
            problems.append(f"{where}: expected an object")
            return None
        unknown = sorted(set(raw) - _TARGET_FIELDS)
        if unknown:
            problems.append(f"{where}: unknown field(s) {unknown}")

        spreadsheet = raw.get("spreadsheet")
        if not isinstance(spreadsheet, str) or not spreadsheet.strip():
            problems.append(f"{where}: 'spreadsheet' must be a URL, file ID, or path")

        base_text = raw.get("base", f"sheets-base/{safe_filename(name)}")
        base = self.root
        if not isinstance(base_text, str) or not base_text.strip():
            problems.append(f"{where}: 'base' must be a directory path")
        else:
            base = self._path(base_text)
            if _CACHE_DIR in base.parts:
                problems.append(
                    f"{where}: 'base' {base_text!r} is inside a {_CACHE_DIR} "
                    "directory, which is a cache; keep the base where it is "
                    "committed"
                )
        base_file = self._base_file(where, raw)
        defaults = self._hooks(where, raw, "sync")

        input_option = raw.get("input_option", RAW)
        if input_option not in INPUT_OPTIONS:
            problems.append(
                f"{where}: 'input_option' must be one of {sorted(INPUT_OPTIONS)}, "
                f"not {input_option!r}"
            )

        tabs: list[TabConfig] = []
        raw_tabs = raw.get("tabs")
        if not isinstance(raw_tabs, dict) or not raw_tabs:
            problems.append(
                f"{where}: 'tabs' must be an object naming one or more tabs"
            )
        else:
            for title, raw_tab in raw_tabs.items():
                tab = self.tab(where, title, raw_tab)
                if tab is None:
                    continue
                if defaults:
                    tab = _with_defaults(tab, defaults)
                named = [t.title for t in tabs if t.sheet_id == tab.sheet_id]
                if tab.sheet_id is not None and named:
                    problems.append(
                        f"{where}, tab {title!r}: 'sheet_id' {tab.sheet_id} is "
                        f"tab {named[0]!r} too"
                    )
                tabs.append(tab)
                if tab.mode == "sync" and input_option == USER_ENTERED:
                    problems.append(
                        f"{where}, tab {title!r}: a sync tab writes with RAW input; "
                        f"USER_ENTERED rewrites values, so they would never "
                        f"read back as written"
                    )
        if len(problems) > start:
            return None
        base_stores: dict[str, Store] = {}
        if base_file is not None:
            base_stores = {
                tab.title: JsonEntryStore(base_file, tab.title, types=tab.types)
                for tab in tabs
                if tab.mode == "sync"
            }
        return Target(
            name=name,
            spreadsheet=str(spreadsheet),
            base=base,
            tabs=tuple(tabs),
            input_option=str(input_option),
            base_stores=base_stores,
        )

    def _base_file(self, where: str, raw: Mapping[str, Any]) -> Path | None:
        """The target's ``base_file``, or None when it has none or it is refused."""
        if "base_file" not in raw:
            return None
        text = raw["base_file"]
        if "base" in raw:
            self.problems.append(
                f"{where}: 'base' and 'base_file' contradict each other"
            )
        if not isinstance(text, str) or not text.strip():
            self.problems.append(f"{where}: 'base_file' must be a .json file path")
            return None
        path = self._path(text)
        if path.suffix.lower() != ".json":
            self.problems.append(f"{where}: 'base_file' {text!r} must end in .json")
            return None
        if _CACHE_DIR in path.parts:
            self.problems.append(
                f"{where}: 'base_file' {text!r} is inside a {_CACHE_DIR} "
                "directory, which is a cache; keep the base where it is "
                "committed"
            )
        return path

    def collisions(self, targets: Iterable[Target]) -> None:
        """Refuse a file, or an entry of one, that two tabs would write.

        Checked across the whole config. A sync or pull tab writes its local
        side, and a sync tab its base; a push tab only reads its local side,
        so push tabs may share one. A writer is keyed by its path and its
        entry (None for a whole file): two entries of one file may be written
        by two tabs, and the same entry, or a whole file and an entry of it,
        may not. Paths are compared case-folded, since on a case-insensitive
        filesystem ``Notes.csv`` and ``notes.csv`` are one file. Only a
        :class:`FileStore` or a :class:`JsonEntryStore` is checked: a caller
        that gives a tab a store of its own owns this check.
        """
        writers: dict[str, dict[str | None, list[str]]] = {}
        paths: dict[str, Path] = {}
        for target in targets:
            for tab in target.tabs:
                where = f"target {target.name!r}, tab {tab.title!r}"
                stores: list[tuple[Store, str]] = []
                if tab.mode != "push":
                    stores.append((tab.local_store, f"{where} (local file)"))
                # A target built in code may have no base for the tab: that is
                # the run's to refuse, and there is no file to collide here.
                based = target.base is not None or tab.title in target.base_stores
                if tab.mode == "sync" and based:
                    stores.append((target.base_store(tab), f"{where} (base)"))
                for store, role in stores:
                    if isinstance(store, FileStore):
                        path, entry = store.path, None
                    elif isinstance(store, JsonEntryStore):
                        path, entry = store.path, store.entry
                    else:
                        continue
                    folded = str(path).casefold()
                    writers.setdefault(folded, {}).setdefault(entry, []).append(role)
                    paths.setdefault(folded, path)
        for folded, entries in writers.items():
            whole = entries.pop(None, [])
            if whole and entries:
                roles = whole + [role for named in entries.values() for role in named]
                self.problems.append(
                    f"{paths[folded]} would be written whole and by entry: "
                    + "; ".join(roles)
                )
                continue
            if len(whole) > 1:
                self.problems.append(
                    f"{paths[folded]} would be written by more than one tab: "
                    + "; ".join(whole)
                )
            for entry, roles in entries.items():
                if len(roles) > 1:
                    self.problems.append(
                        f"{paths[folded]} [{entry}] would be written by more than "
                        "one tab: " + "; ".join(roles)
                    )

    def tab(self, target: str, title: str, raw: Any) -> TabConfig | None:
        where = f"{target}, tab {title!r}"
        problems = self.problems
        start = len(problems)
        if not title.strip():
            problems.append(f"{target}: a tab has a blank title")
        if not isinstance(raw, dict):
            problems.append(f"{where}: expected an object")
            return None
        unknown = sorted(set(raw) - _TAB_FIELDS)
        if unknown:
            problems.append(f"{where}: unknown field(s) {unknown}")

        mode = raw.get("mode", "sync")
        if mode not in MODES:
            problems.append(
                f"{where}: 'mode' must be one of {sorted(MODES)}, not {mode!r}"
            )
        elif mode != "sync":
            given = [name for name in _SYNC_ONLY if name in raw]
            if given:
                problems.append(f"{where}: {given} apply only to a sync tab")
            if mode == "pull" and "widths" in raw:
                problems.append(f"{where}: 'widths' do not apply to a pull tab")
            if mode == "pull" and "clear_links" in raw:
                problems.append(f"{where}: 'clear_links' does not apply to a pull tab")
            if mode == "pull" and "link_urls" in raw:
                problems.append(f"{where}: 'link_urls' does not apply to a pull tab")
        if mode != "pull" and "exclude" in raw:
            problems.append(f"{where}: 'exclude' applies only to a pull tab")

        local = self._local(where, raw)
        entry = self._entry(where, raw, local)
        bom = raw.get("bom", False)
        if not isinstance(bom, bool):
            problems.append(f"{where}: 'bom' must be true or false")
        elif bom and local is not None and local.suffix.lower() == ".json":
            problems.append(f"{where}: 'bom' applies only to a .csv or .tsv file")
        newline = raw.get("newline", "lf")
        if not isinstance(newline, str) or newline not in NEWLINES:
            problems.append(
                f"{where}: 'newline' must be one of {sorted(NEWLINES)}, not {newline!r}"
            )
        elif newline != "lf" and local is not None and local.suffix.lower() == ".json":
            problems.append(f"{where}: 'newline' applies only to a .csv or .tsv file")

        key = self._names(where, raw, "key")
        if mode == "sync" and not key:
            problems.append(f"{where}: a sync tab needs a 'key' of one or more columns")
        blank_keys = raw.get("blank_keys", "refuse")
        if not isinstance(blank_keys, str) or blank_keys not in BLANK_KEYS:
            problems.append(
                f"{where}: 'blank_keys' must be one of {sorted(BLANK_KEYS)}, "
                f"not {blank_keys!r}"
            )
        columns = self._columns(where, raw)
        exclude = self._names(where, raw, "exclude")
        if exclude and columns is not None:
            problems.append(f"{where}: 'exclude' and 'columns' contradict each other")
        local_owned = self._names(where, raw, "local_owned")
        sheet_owned = self._names(where, raw, "sheet_owned")
        self._ownership(where, key, columns, local_owned, sheet_owned)

        owns_rows = raw.get("owns_rows", False)
        if not isinstance(owns_rows, bool):
            problems.append(f"{where}: 'owns_rows' must be true or false")
        bootstrap = raw.get("bootstrap", "local")
        if bootstrap not in BOOTSTRAPS:
            problems.append(
                f"{where}: 'bootstrap' must be one of {sorted(BOOTSTRAPS)}, not "
                f"{bootstrap!r} (--adopt is a flag, not a config value)"
            )
        sheet_id = raw.get("sheet_id")
        # bool is an int subclass, and True is not a sheetId.
        if sheet_id is not None and (
            isinstance(sheet_id, bool) or not isinstance(sheet_id, int) or sheet_id < 0
        ):
            problems.append(
                f"{where}: 'sheet_id' must be a whole number, the tab's sheetId, "
                f"not {sheet_id!r}"
            )
        clear_links = raw.get("clear_links", False)
        if not isinstance(clear_links, bool):
            problems.append(f"{where}: 'clear_links' must be true or false")
        link_urls = self._link_urls(where, raw)
        hooks = self._hooks(where, raw, str(mode))
        if link_urls is not None and clear_links is True:
            problems.append(
                f"{where}: 'link_urls' and 'clear_links' contradict each other"
            )
        on_invalid = raw.get("on_invalid", "refuse")
        if not isinstance(on_invalid, str) or on_invalid not in ON_INVALID:
            problems.append(
                f"{where}: 'on_invalid' must be one of {sorted(ON_INVALID)}, "
                f"not {on_invalid!r}"
            )
        render = raw.get("render", "unformatted")
        if not isinstance(render, str) or render not in RENDERS:
            problems.append(
                f"{where}: 'render' must be one of {sorted(RENDERS)}, not {render!r}"
            )
        strict_schema = raw.get("strict_schema", False)
        if not isinstance(strict_schema, bool):
            problems.append(f"{where}: 'strict_schema' must be true or false")
            strict_schema = False
        schema = self._schema(where, raw.get("schema", {}), columns, strict_schema)
        insert_above = self._insert_above(where, raw.get("insert_above"), columns)
        widths = self._widths(where, raw.get("widths", {}), columns)
        for what, names in (
            ("key", key),
            ("schema", list(schema)),
            ("widths", list(widths)),
        ):
            excluded = sorted(set(names) & set(exclude))
            if excluded:
                problems.append(
                    f"{where}: 'exclude' names {what} column(s) {excluded}, which "
                    "would be read anyway"
                )

        if len(problems) > start or local is None:
            return None
        return TabConfig(
            title=title,
            local=local,
            mode=str(mode),
            key=tuple(key),
            columns=tuple(columns) if columns is not None else None,
            exclude=tuple(exclude),
            local_owned=tuple(local_owned),
            sheet_owned=tuple(sheet_owned),
            owns_rows=bool(owns_rows),
            schema=schema,
            bootstrap=str(bootstrap),
            insert_above=insert_above,
            widths=widths,
            bom=bool(bom),
            newline=str(newline),
            blank_keys=str(blank_keys),
            on_invalid=str(on_invalid),
            render=str(render),
            clear_links=bool(clear_links),
            sheet_id=sheet_id,
            entry=entry,
            link_urls=link_urls,
            strict_schema=bool(strict_schema),
            hooks=hooks,
        )

    def _hooks(self, where: str, raw: Mapping[str, Any], mode: str) -> dict[str, str]:
        """The ``hooks`` of a tab or a target, checked by form; empty when refused.

        A target's hooks are checked as a sync tab's, since a sync tab takes
        every hook.
        """
        if "hooks" not in raw:
            return {}
        given = raw["hooks"]
        if not isinstance(given, dict) or not given:
            self.problems.append(
                f"{where}: 'hooks' must be an object naming one or more of "
                f"{sorted(HOOKS)}"
            )
            return {}
        found = _hook_problems(given, mode)
        self.problems.extend(f"{where}: {problem}" for problem in found)
        return {} if found else {str(hook): str(name) for hook, name in given.items()}

    def _link_urls(self, where: str, raw: Mapping[str, Any]) -> str | None:
        """The colour of the tab's ``link_urls``; None when absent or refused."""
        if "link_urls" not in raw:
            return None
        given = raw["link_urls"]
        if not isinstance(given, dict) or set(given) != {"color"}:
            self.problems.append(
                f"{where}: 'link_urls' must be an object with one field, 'color'"
            )
            return None
        color = given["color"]
        try:
            _rgb(color)
        except ValueError:
            self.problems.append(
                f"{where}: 'link_urls' color must be '#rrggbb', not {color!r}"
            )
            return None
        return str(color)

    def _entry(
        self, where: str, raw: Mapping[str, Any], local: Path | None
    ) -> str | None:
        """The tab's ``entry``, or None when it has none or it is refused."""
        if "entry" not in raw:
            return None
        entry = raw["entry"]
        if not isinstance(entry, str) or not entry.strip():
            self.problems.append(f"{where}: 'entry' must be a non-blank string")
            return None
        if local is not None and local.suffix.lower() != ".json":
            self.problems.append(f"{where}: 'entry' needs a .json 'local'")
            return None
        return entry

    def _local(self, where: str, raw: Mapping[str, Any]) -> Path | None:
        text = raw.get("local")
        if not isinstance(text, str) or not text.strip():
            self.problems.append(f"{where}: 'local' must be a file path")
            return None
        path = self._path(text)
        if path.suffix.lower() not in LOCAL_EXTENSIONS:
            self.problems.append(
                f"{where}: 'local' {text!r} must end in one of "
                f"{sorted(LOCAL_EXTENSIONS)}"
            )
            return None
        return path

    def _columns(self, where: str, raw: Mapping[str, Any]) -> list[str] | None:
        """The projection, or None when absent (or too malformed to check against)."""
        if "columns" not in raw:
            return None
        if raw["columns"] == [] or not _is_names(raw["columns"]):
            self.problems.append(
                f"{where}: 'columns' must be a list of one or more column names"
            )
            return None
        return self._names(where, raw, "columns")

    def _names(self, where: str, raw: Mapping[str, Any], name: str) -> list[str]:
        """The list of column names in field ``name``, empty when absent or bad."""
        value = raw.get(name, [])
        if not _is_names(value):
            self.problems.append(f"{where}: {name!r} must be a list of column names")
            return []
        repeated = _repeated(value)
        if repeated:
            self.problems.append(f"{where}: {name!r} repeats {repeated}")
        return list(value)

    def _ownership(
        self,
        where: str,
        key: Sequence[str],
        columns: Sequence[str] | None,
        local_owned: Sequence[str],
        sheet_owned: Sequence[str],
    ) -> None:
        """Refuse key or owned columns outside the projection, or overlapping."""
        self._outside(where, "key", key, columns)
        self._outside(where, "local_owned", local_owned, columns)
        self._outside(where, "sheet_owned", sheet_owned, columns)
        for name, names in (("local_owned", local_owned), ("sheet_owned", sheet_owned)):
            keyed = [c for c in names if c in key]
            if keyed:
                self.problems.append(f"{where}: key column(s) {keyed} cannot be {name}")
        both = sorted(set(local_owned) & set(sheet_owned))
        if both:
            self.problems.append(
                f"{where}: column(s) {both} are both local_owned and sheet_owned"
            )

    def _outside(
        self, where: str, what: str, names: Sequence[str], columns: Sequence[str] | None
    ) -> None:
        if columns is None:
            return
        outside = [c for c in names if c not in columns]
        if outside:
            self.problems.append(
                f"{where}: {what} column(s) {outside} not in 'columns'"
            )

    def _schema(
        self,
        where: str,
        raw: Any,
        columns: Sequence[str] | None,
        strict_schema: bool = False,
    ) -> dict[str, ColumnSchema]:
        problems = self.problems
        if not isinstance(raw, dict):
            problems.append(f"{where}: 'schema' must be an object of columns")
            return {}
        schema: dict[str, ColumnSchema] = {}
        for column, spec in raw.items():
            at = f"{where}: schema {column!r}"
            if not column.strip():
                problems.append(f"{where}: 'schema' names a blank column")
                continue
            if not isinstance(spec, dict):
                problems.append(f"{at}: expected an object")
                continue
            unknown = sorted(set(spec) - _SCHEMA_FIELDS)
            if unknown:
                problems.append(f"{at}: unknown field(s) {unknown}")
            type_ = spec.get("type", "str")
            required = spec.get("required", False)
            allowed = spec.get("allowed")
            ok = not unknown
            if type_ not in COLUMN_TYPES:
                problems.append(
                    f"{at}: 'type' must be one of {sorted(COLUMN_TYPES)}, not {type_!r}"
                )
                ok = False
            if not isinstance(required, bool):
                problems.append(f"{at}: 'required' must be true or false")
                ok = False
            if allowed is not None and not (
                isinstance(allowed, list)
                and allowed
                and all(_is_scalar(value) for value in allowed)
            ):
                problems.append(f"{at}: 'allowed' must be a list of one or more values")
                ok = False
            if ok:
                schema[column] = ColumnSchema(
                    type=str(type_),
                    required=bool(required),
                    allowed=tuple(allowed) if allowed is not None else None,
                )
        if not strict_schema:
            self._outside(where, "schema", list(raw), columns)
        return schema

    def _insert_above(
        self, where: str, raw: Any, columns: Sequence[str] | None
    ) -> dict[str, tuple[Any, ...]] | None:
        if raw is None:
            return None
        bad = f"{where}: 'insert_above' must be one {{column: value or [values]}} pair"
        if not isinstance(raw, dict) or len(raw) != 1:
            self.problems.append(bad)
            return None
        ((column, given),) = raw.items()
        values = given if isinstance(given, list) else [given]
        if (
            not column.strip()
            or not values
            or not all(_is_scalar(value) for value in values)
        ):
            self.problems.append(bad)
            return None
        self._outside(where, "insert_above", [column], columns)
        return {column: tuple(values)}

    def _widths(
        self, where: str, raw: Any, columns: Sequence[str] | None
    ) -> dict[str, int]:
        if not isinstance(raw, dict):
            self.problems.append(f"{where}: 'widths' must be an object of columns")
            return {}
        widths: dict[str, int] = {}
        for column, width in raw.items():
            # bool is an int subclass, and True is not a width.
            if (
                not column.strip()
                or isinstance(width, bool)
                or not isinstance(width, int)
                or width < 1
            ):
                self.problems.append(
                    f"{where}: width of {column!r} must be a whole number of "
                    f"pixels, at least 1, not {width!r}"
                )
                continue
            widths[column] = width
        self._outside(where, "widths", list(raw), columns)
        return widths
