import pytest

from hermes.paths import PathDenied, resolve_in


def test_inside_ok(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    assert resolve_in(base, "workspace/a.txt").name == "a.txt"
    assert resolve_in(base, ".") == base.resolve()


def test_dotdot_escape_denied(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    with pytest.raises(PathDenied):
        resolve_in(base, "../outside.txt")
    with pytest.raises(PathDenied):
        resolve_in(base, "a/../../outside.txt")


def test_absolute_outside_denied(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    with pytest.raises(PathDenied):
        resolve_in(base, "/etc/passwd")


def test_symlink_escape_denied(tmp_path):
    base = tmp_path / "proj"
    base.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (base / "link").symlink_to(outside)
    with pytest.raises(PathDenied):
        resolve_in(base, "link/file.txt")
