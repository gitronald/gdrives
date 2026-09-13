"""Local writes preserve existing files on failure and isolate scratch space."""

import stat
from pathlib import Path

import pytest

from gdrives.local import PRIVATE, atomic_output, umask_mode


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
