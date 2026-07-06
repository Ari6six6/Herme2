"""Path safety: keep agent file operations inside the project directory."""

from __future__ import annotations

from pathlib import Path


class PathDenied(Exception):
    pass


def resolve_in(base: Path, candidate: str) -> Path:
    """Resolve `candidate` (relative or absolute) and require it to live
    under `base` after symlink resolution. Raises PathDenied otherwise."""
    base = base.resolve()
    p = Path(candidate)
    if not p.is_absolute():
        p = base / p
    resolved = p.resolve()
    if resolved != base and not resolved.is_relative_to(base):
        raise PathDenied(f"path escapes the project directory: {candidate}")
    return resolved


def is_within(base: Path, candidate: str) -> bool:
    try:
        resolve_in(base, candidate)
        return True
    except PathDenied:
        return False


def repo_root() -> Path:
    """The Hermes source tree itself (hermes/paths.py -> hermes/ -> repo root).

    Used only by the self-build tools (hermes.tools.self_build), which are off
    by default and let the agent read/edit the harness's own code. This is NOT
    the project directory (see Project.root) — edits here only affect this
    installation's source (an editable `pip install -e .` checkout, normally
    the git repo)."""
    return Path(__file__).resolve().parent.parent
