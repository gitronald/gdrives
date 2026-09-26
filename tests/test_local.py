"""Local writes preserve existing files on failure and isolate scratch space."""

import stat
from pathlib import Path

import pytest
from helpers import plant_scratch_symlink

from gdrives.local import (
    PRIVATE,
    atomic_output,
    escape_formula,
    printable,
    umask_mode,
    write_text,
)


def test_success_replaces_target(tmp_path):
    target = tmp_path / "out"
    target.write_bytes(b"old")
    with atomic_output(target) as stream:
        stream.write(b"new")
        assert target.read_bytes() == b"old"
    assert target.read_bytes() == b"new"
    assert list(tmp_path.iterdir()) == [target]


def test_default_mode_matches_a_direct_write(tmp_path):
    """Output the user opens keeps the permissions write_bytes would give it."""
    direct = tmp_path / "direct"
    direct.write_bytes(b"x")
    target = tmp_path / "atomic"
    with atomic_output(target) as stream:
        stream.write(b"x")
    assert stat.S_IMODE(target.stat().st_mode) == stat.S_IMODE(direct.stat().st_mode)
    assert stat.S_IMODE(target.stat().st_mode) == umask_mode()


def test_private_mode_is_owner_only(tmp_path):
    target = tmp_path / "token.json"
    with atomic_output(target, mode=PRIVATE) as stream:
        stream.write(b"secret")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize("mode", [0o600, 0o640, 0o444])
def test_replacing_a_file_keeps_its_mode(tmp_path, mode):
    """Overwriting keeps the old file's permissions, as a direct write would."""
    target = tmp_path / "report.docx"
    target.write_bytes(b"old")
    target.chmod(mode)
    with atomic_output(target) as stream:
        stream.write(b"new")
    assert target.read_bytes() == b"new"
    assert stat.S_IMODE(target.stat().st_mode) == mode


def test_private_mode_overrides_a_looser_existing_file(tmp_path):
    target = tmp_path / "token.json"
    target.write_bytes(b"old")
    target.chmod(0o644)
    with atomic_output(target, mode=PRIVATE) as stream:
        stream.write(b"secret")
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_scratch_file_never_follows_a_planted_symlink(tmp_path, monkeypatch):
    """A symlink at the scratch name is skipped, never written through."""
    victim = tmp_path / "victim"
    victim.write_bytes(b"keep")
    link = plant_scratch_symlink(monkeypatch, tmp_path, victim)
    target = tmp_path / "out"
    with atomic_output(target) as stream:
        stream.write(b"new")
    assert victim.read_bytes() == b"keep"
    assert link.is_symlink() and link.resolve() == victim
    assert target.read_bytes() == b"new"
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        ".gdrives-planted",
        "out",
        "victim",
    ]


def test_failed_write_preserves_target_and_cleans_up(tmp_path):
    target = tmp_path / "out"
    target.write_bytes(b"old")
    with pytest.raises(OSError, match="full"):
        with atomic_output(target) as stream:
            stream.write(b"partial")
            raise OSError("full")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_replace_cleans_up(tmp_path, monkeypatch):
    target = tmp_path / "out"
    target.write_bytes(b"old")

    def fail(self, destination):
        raise OSError("rename failed")

    monkeypatch.setattr(Path, "replace", fail)
    with pytest.raises(OSError, match="rename failed"):
        with atomic_output(target) as stream:
            stream.write(b"new")
    assert target.read_bytes() == b"old"
    assert list(tmp_path.iterdir()) == [target]


def test_overlapping_writers_have_independent_scratch_files(tmp_path):
    target = tmp_path / "out"
    with atomic_output(target) as first:
        first.write(b"first")
        with atomic_output(target) as second:
            second.write(b"second")
        assert target.read_bytes() == b"second"
    assert target.read_bytes() == b"first"
    assert list(tmp_path.iterdir()) == [target]


class TestWriteText:
    def test_creates_parents_and_writes_utf8(self, tmp_path):
        target = tmp_path / "a" / "b" / "notes.md"
        write_text(target, "café\n")
        assert target.read_bytes() == "café\n".encode()

    def test_line_endings_are_written_untranslated(self, tmp_path):
        """CSV rows keep their \\r\\n and plain text keeps \\n, on every platform."""
        target = tmp_path / "rows.csv"
        write_text(target, "a,b\r\nc,d\r\n")
        assert target.read_bytes() == b"a,b\r\nc,d\r\n"

    def test_failed_write_keeps_the_old_file(self, tmp_path, monkeypatch):
        target = tmp_path / "cache.json"
        target.write_text("old")

        def fail(self, destination):
            raise OSError("disk full")

        monkeypatch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="disk full"):
            write_text(target, "new")
        assert target.read_text() == "old"
        assert list(tmp_path.iterdir()) == [target]


class TestEscapeFormula:
    @pytest.mark.parametrize(
        "cell",
        ['=HYPERLINK("http://x","y")', "+1", "-2", "@SUM(A1)", "\t=1", "\r=1"],
    )
    def test_formula_starts_get_an_apostrophe(self, cell):
        assert escape_formula(cell) == "'" + cell

    @pytest.mark.parametrize("cell", ["", "report.pdf", "a=b", " =1", "'=1"])
    def test_other_cells_are_unchanged(self, cell):
        assert escape_formula(cell) == cell


class TestPrintable:
    def test_escape_sequences_are_shown_not_sent(self):
        name = "\x1b]52;c;ZXZpbA==\x07report\x1b[2J.pdf"
        assert printable(name) == "\\x1b]52;c;ZXZpbA==\\x07report\\x1b[2J.pdf"

    def test_c1_controls_and_del_are_escaped(self):
        assert printable("a\x9bb\x7fc\x85") == "a\\x9bb\\x7fc\\x85"

    def test_line_breaks_and_tabs_are_escaped(self):
        assert printable("a\nb\tc\r") == "a\\x0ab\\x09c\\x0d"

    def test_ordinary_names_are_unchanged(self):
        name = "Café notes (1)  — 日本.pdf"
        assert printable(name) == name
