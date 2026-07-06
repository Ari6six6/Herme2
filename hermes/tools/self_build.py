"""Self-build tools: let the agent read and edit Hermes' OWN source tree.

Off by default (`self_build_enabled`). This is a different, much narrower gate
than the project file tools: those are scoped to `<project>/`, a directory the
agent is expected to fill freely. This module is scoped to the Hermes
installation itself (`hermes.paths.repo_root()`) — the code that defines every
other gate in the system.

That difference means one extra rule the project tools don't need: a fixed
denylist (`PROTECTED`) of files this module refuses to write or edit no matter
what the operator has configured, because they ARE the gates — the
confirmation chokepoint, the config loader, the path-safety check, the run
loop's own safety bookkeeping, and this file. A doer that can rewrite its own
gates isn't gated anymore; deny beats allow, and it isn't negotiable from
inside a tool call. Anything else in the tree is fair game once the operator
opts in, but every write still pauses for an explicit y/n with a real diff,
same as `forge_tool`, and a timestamped backup is kept before every change so
a bad self-edit is one copy away from undone.

Edits here take effect only the next time the operator restarts Hermes —
the running process already has the old code imported.
"""

from __future__ import annotations

import difflib
import time

from hermes.paths import PathDenied, repo_root, resolve_in
from hermes.tools.base import obj_schema, tool

MAX_READ_CHARS = 40000

# Files that ARE the permission system. self_build tools refuse to touch these
# even when self_build_enabled is on — editing them from inside a tool call
# would let the agent quietly loosen its own gates. Change these only by hand,
# outside the agent.
PROTECTED = frozenset({
    "hermes/confirm.py",
    "hermes/config.py",
    "hermes/paths.py",
    "hermes/agent.py",
    "hermes/checkpoint.py",
    "hermes/tools/base.py",
    "hermes/tools/__init__.py",
    "hermes/tools/local_shell.py",
    "hermes/tools/self_build.py",
})


def _rel(path) -> str:
    return path.relative_to(repo_root()).as_posix()


def _resolve(args_path: str):
    """Resolve a path inside the Hermes source tree. Returns (path, error)."""
    try:
        path = resolve_in(repo_root(), args_path)
    except PathDenied as e:
        return None, f"DENIED: {e}"
    return path, None


def _protected_denial(path) -> str | None:
    rel = _rel(path)
    if rel in PROTECTED:
        return (f"DENIED: '{rel}' is one of the files that define Hermes' own "
                f"safety gates — self-build refuses to touch it regardless of "
                f"config. Ask the operator to change it by hand.")
    return None


def _backup(path) -> str:
    """Copy the current file into repo_root()/.self_build_backups/ before a
    change lands, so a bad self-edit is one copy away from undone."""
    if not path.is_file():
        return ""
    store = repo_root() / ".self_build_backups"
    store.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = store / f"{stamp}-{path.name}"
    n = 1
    while dest.exists():
        n += 1
        dest = store / f"{stamp}-{n}-{path.name}"
    dest.write_text(path.read_text(errors="replace"))
    return str(dest.relative_to(repo_root()))


@tool(
    "list_hermes_source",
    "List files under a directory in Hermes' OWN source tree (default: repo "
    "root). Read-only, always free — use it to find where something lives "
    "before reading or editing it.",
    obj_schema({"path": {"type": "string", "description": "directory, default '.'"}}, []),
)
def list_hermes_source(args, ctx):
    path, err = _resolve(args.get("path", "."))
    if err:
        return err
    if not path.is_dir():
        return f"ERROR: not a directory: {args.get('path', '.')}"
    lines = []
    for p in sorted(path.iterdir()):
        if p.name in (".git", ".self_build_backups", "__pycache__"):
            continue
        rel = _rel(p)
        if p.is_dir():
            n = sum(1 for c in p.iterdir() if c.name not in (".git", "__pycache__"))
            lines.append(f"{rel}/ ({n} entries)")
        else:
            mark = " [protected]" if rel in PROTECTED else ""
            lines.append(f"{rel} ({p.stat().st_size}B){mark}")
        if len(lines) >= 200:
            lines.append("[...truncated at 200 entries — list a narrower path.]")
            break
    return "\n".join(lines) or "(empty)"


@tool(
    "read_hermes_source",
    "Read a file from Hermes' OWN source tree (paths relative to the repo "
    "root, e.g. 'hermes/agent.py'). Read-only, always free.",
    obj_schema(
        {
            "path": {"type": "string"},
            "offset": {"type": "integer", "description": "start line (1-based, optional)"},
            "limit": {"type": "integer", "description": "max lines (optional)"},
        },
        ["path"],
    ),
)
def read_hermes_source(args, ctx):
    path, err = _resolve(args["path"])
    if err:
        return err
    if not path.is_file():
        return f"ERROR: not a file: {args['path']}"
    text = path.read_text(errors="replace")
    lines = text.splitlines()
    offset = max(int(args.get("offset", 1)), 1)
    limit = int(args.get("limit", 0)) or len(lines)
    chunk = lines[offset - 1: offset - 1 + limit]
    mark = " [protected — refuses writes]" if _rel(path) in PROTECTED else ""
    out = f"# {_rel(path)}{mark}\n" + "\n".join(
        f"{i:>5} {line}" for i, line in enumerate(chunk, start=offset)
    )
    if len(out) > MAX_READ_CHARS:
        out = out[:MAX_READ_CHARS] + (
            f"\n[...truncated: showing {MAX_READ_CHARS} of {len(out)} chars — "
            f"the file continues. Re-read with offset/limit.]"
        )
    return out


@tool(
    "write_hermes_source",
    "Create or overwrite a file in Hermes' OWN source tree. Always pauses for "
    "the operator's y/n with the diff visible. A fixed set of safety-critical "
    "files refuse this outright (see list_hermes_source's [protected] marks). "
    "Takes effect only after the operator restarts Hermes.",
    obj_schema({"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
)
def write_hermes_source(args, ctx):
    path, err = _resolve(args["path"])
    if err:
        return err
    denial = _protected_denial(path)
    if denial:
        return denial
    old = path.read_text(errors="replace") if path.is_file() else ""
    new = str(args["content"])
    diff = "".join(difflib.unified_diff(
        old.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"a/{_rel(path)}", tofile=f"b/{_rel(path)}",
    )) or "(new file, no prior content)"
    if not ctx.confirm(
        f"agent wants to write HERMES' OWN SOURCE: {_rel(path)}",
        detail="This changes the harness itself, not the project. It takes "
               "effect after you restart Hermes.",
        viewable=diff,
    ):
        return "DENIED by operator."
    backup = _backup(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new)
    note = f" (backup: {backup})" if backup else ""
    return f"wrote {len(new)} chars to {_rel(path)}{note}. Restart Hermes for it to take effect; run the tests before you tell the operator it's safe."


@tool(
    "edit_hermes_source",
    "Replace an exact string in a file in Hermes' OWN source tree. `old` must "
    "occur exactly once. Always pauses for the operator's y/n with the diff "
    "visible. A fixed set of safety-critical files refuse this outright. "
    "Takes effect only after the operator restarts Hermes.",
    obj_schema(
        {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "exact text to replace"},
            "new": {"type": "string", "description": "replacement text"},
        },
        ["path", "old", "new"],
    ),
)
def edit_hermes_source(args, ctx):
    path, err = _resolve(args["path"])
    if err:
        return err
    denial = _protected_denial(path)
    if denial:
        return denial
    if not path.is_file():
        return f"ERROR: not a file: {args['path']}"
    old_text = path.read_text(errors="replace")
    count = old_text.count(args["old"])
    if count == 0:
        return "ERROR: `old` string not found in file."
    if count > 1:
        return f"ERROR: `old` occurs {count} times — make it unique."
    new_text = old_text.replace(args["old"], args["new"], 1)
    diff = "".join(difflib.unified_diff(
        old_text.splitlines(keepends=True), new_text.splitlines(keepends=True),
        fromfile=f"a/{_rel(path)}", tofile=f"b/{_rel(path)}",
    ))
    if not ctx.confirm(
        f"agent wants to edit HERMES' OWN SOURCE: {_rel(path)}",
        detail="This changes the harness itself, not the project. It takes "
               "effect after you restart Hermes.",
        viewable=diff,
    ):
        return "DENIED by operator."
    backup = _backup(path)
    path.write_text(new_text)
    note = f" (backup: {backup})" if backup else ""
    return f"edited {_rel(path)}{note}. Restart Hermes for it to take effect; run the tests before you tell the operator it's safe."


TOOLS = [list_hermes_source, read_hermes_source, write_hermes_source, edit_hermes_source]
