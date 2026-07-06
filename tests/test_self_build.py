import json

import pytest

from hermes.tools import build_registry, self_build
from hermes.tools.base import ToolContext


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A miniature stand-in for the real Hermes checkout, so self-build tests
    never touch the actual repo this test suite lives in."""
    (tmp_path / "hermes" / "tools").mkdir(parents=True)
    (tmp_path / "hermes" / "confirm.py").write_text("# the real gate\n")
    (tmp_path / "hermes" / "tools" / "widget.py").write_text("VALUE = 1\n")
    monkeypatch.setattr(self_build, "repo_root", lambda: tmp_path)
    return tmp_path


def _ctx(confirm):
    return ToolContext(project=None, cfg=None, confirm=confirm)


def test_list_and_read_are_free(fake_repo, never):
    ctx = _ctx(never)
    listing = self_build.list_hermes_source.fn({"path": "hermes"}, ctx)
    assert "confirm.py" in listing
    assert "[protected]" in listing
    assert "tools/ (1 entries)" in listing

    out = self_build.read_hermes_source.fn({"path": "hermes/tools/widget.py"}, ctx)
    assert "VALUE = 1" in out


def test_write_new_file_requires_confirm_and_backs_up_existing(fake_repo, yes):
    ctx = _ctx(yes)
    msg = self_build.write_hermes_source.fn(
        {"path": "hermes/tools/widget.py", "content": "VALUE = 2\n"}, ctx
    )
    assert "wrote" in msg
    assert (fake_repo / "hermes" / "tools" / "widget.py").read_text() == "VALUE = 2\n"
    backups = list((fake_repo / ".self_build_backups").glob("*widget.py"))
    assert len(backups) == 1
    assert backups[0].read_text() == "VALUE = 1\n"


def test_write_denied_by_operator_leaves_file_untouched(fake_repo, no):
    ctx = _ctx(no)
    msg = self_build.write_hermes_source.fn(
        {"path": "hermes/tools/widget.py", "content": "VALUE = 999\n"}, ctx
    )
    assert "DENIED" in msg
    assert (fake_repo / "hermes" / "tools" / "widget.py").read_text() == "VALUE = 1\n"


def test_protected_file_refuses_even_when_confirm_would_say_yes(fake_repo, never):
    ctx = _ctx(never)
    msg = self_build.write_hermes_source.fn(
        {"path": "hermes/confirm.py", "content": "def confirm(*a, **k): return True\n"},
        ctx,
    )
    assert "DENIED" in msg
    assert not (fake_repo / ".self_build_backups").exists()
    assert (fake_repo / "hermes" / "confirm.py").read_text() == "# the real gate\n"


def test_self_build_py_cannot_protect_itself_away(fake_repo, never):
    ctx = _ctx(never)
    # There's no self_build.py in the fake repo, but the denylist is checked by
    # relative path, not existence — a write attempting to create one is still refused.
    msg = self_build.write_hermes_source.fn(
        {"path": "hermes/tools/self_build.py", "content": "PROTECTED = frozenset()\n"},
        ctx,
    )
    assert "DENIED" in msg


def test_path_escape_denied(fake_repo, never):
    ctx = _ctx(never)
    msg = self_build.write_hermes_source.fn(
        {"path": "../outside.py", "content": "x = 1\n"}, ctx
    )
    assert "DENIED" in msg


def test_edit_requires_unique_match(fake_repo, yes):
    ctx = _ctx(yes)
    (fake_repo / "hermes" / "tools" / "dup.py").write_text("x = 1\nx = 1\n")
    out = self_build.edit_hermes_source.fn(
        {"path": "hermes/tools/dup.py", "old": "x = 1", "new": "x = 2"}, ctx
    )
    assert "occurs 2 times" in out


def test_edit_applies_and_backs_up(fake_repo, yes):
    ctx = _ctx(yes)
    msg = self_build.edit_hermes_source.fn(
        {"path": "hermes/tools/widget.py", "old": "VALUE = 1", "new": "VALUE = 42"}, ctx
    )
    assert "edited" in msg
    assert (fake_repo / "hermes" / "tools" / "widget.py").read_text() == "VALUE = 42\n"
    backups = list((fake_repo / ".self_build_backups").glob("*widget.py"))
    assert len(backups) == 1


def test_edit_protected_file_denied(fake_repo, never):
    ctx = _ctx(never)
    out = self_build.edit_hermes_source.fn(
        {"path": "hermes/confirm.py", "old": "# the real gate", "new": "# gutted"}, ctx
    )
    assert "DENIED" in out


def test_registry_gates_on_self_build_enabled(project, cfg, yes):
    registry = build_registry(project, cfg, yes)
    assert "write_hermes_source" not in registry.names()
    assert "read_hermes_source" not in registry.names()

    cfg.set("self_build_enabled", True)
    registry2 = build_registry(project, cfg, yes)
    for name in ("list_hermes_source", "read_hermes_source",
                 "write_hermes_source", "edit_hermes_source"):
        assert name in registry2.names()
