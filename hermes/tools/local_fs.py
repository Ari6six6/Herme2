"""Local filesystem tools — run on the phone, scoped to the project dir.

Policy: anything inside the project directory is free. Reads outside it ask
the operator. Writes outside it are denied outright.
"""

from __future__ import annotations

from pathlib import Path

from hermes.paths import PathDenied, resolve_in
from hermes.tools.base import obj_schema, tool

MAX_READ_CHARS = 40000


def _resolve_read(args_path: str, ctx) -> Path:
    try:
        return resolve_in(ctx.project.root, args_path)
    except PathDenied:
        target = Path(args_path).expanduser()
        if ctx.confirm(f"agent wants to READ outside the project: {target}"):
            return target.resolve()
        raise PathDenied("DENIED by operator: read outside project")


@tool(
    "read_file",
    "Read a text file. Paths are relative to the project directory on the "
    "phone. Reading outside the project asks the operator first.",
    obj_schema(
        {
            "path": {"type": "string", "description": "file path"},
            "offset": {"type": "integer", "description": "start line (1-based, optional)"},
            "limit": {"type": "integer", "description": "max lines (optional)"},
        },
        ["path"],
    ),
)
def read_file(args, ctx):
    try:
        path = _resolve_read(args["path"], ctx)
    except PathDenied as e:
        return str(e)
    if not path.is_file():
        return f"ERROR: not a file: {args['path']}"
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return f"ERROR: {e}"
    lines = text.splitlines()
    offset = max(int(args.get("offset", 1)), 1)
    limit = int(args.get("limit", 0)) or len(lines)
    chunk = lines[offset - 1 : offset - 1 + limit]
    out = "\n".join(f"{i:>5} {line}" for i, line in enumerate(chunk, start=offset))
    if len(out) > MAX_READ_CHARS:
        out = out[:MAX_READ_CHARS] + (
            f"\n[...truncated: showing {MAX_READ_CHARS} of {len(out)} chars — "
            f"the file continues. Re-read with offset/limit.]"
        )
    return out or "(empty file)"


@tool(
    "write_file",
    "Create or overwrite a text file inside the project directory on the phone.",
    obj_schema(
        {
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        ["path", "content"],
    ),
)
def write_file(args, ctx):
    try:
        path = resolve_in(ctx.project.root, args["path"])
    except PathDenied:
        return "DENIED: writes outside the project directory are not allowed."
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args["content"])
    return f"wrote {len(args['content'])} chars to {path.relative_to(ctx.project.root)}"


@tool(
    "edit_file",
    "Replace an exact string in a project file. `old` must occur exactly once.",
    obj_schema(
        {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "exact text to replace"},
            "new": {"type": "string", "description": "replacement text"},
        },
        ["path", "old", "new"],
    ),
)
def edit_file(args, ctx):
    try:
        path = resolve_in(ctx.project.root, args["path"])
    except PathDenied:
        return "DENIED: writes outside the project directory are not allowed."
    if not path.is_file():
        return f"ERROR: not a file: {args['path']}"
    text = path.read_text()
    count = text.count(args["old"])
    if count == 0:
        return "ERROR: `old` string not found in file."
    if count > 1:
        return f"ERROR: `old` occurs {count} times — make it unique."
    path.write_text(text.replace(args["old"], args["new"], 1))
    return "edited."


@tool(
    "list_files",
    "List files under a directory in the project (default: project root).",
    obj_schema(
        {"path": {"type": "string", "description": "directory, default '.'"}},
        [],
    ),
)
def list_files(args, ctx):
    try:
        base = resolve_in(ctx.project.root, args.get("path", "."))
    except PathDenied as e:
        return f"DENIED: {e}"
    if not base.is_dir():
        return f"ERROR: not a directory: {args.get('path', '.')}"
    lines = []
    for p in sorted(base.iterdir()):
        rel = p.relative_to(ctx.project.root)
        if p.is_dir():
            n = sum(1 for _ in p.iterdir())
            lines.append(f"{rel}/ ({n} entries)")
        else:
            lines.append(f"{rel} ({p.stat().st_size}B)")
        if len(lines) >= 200:
            lines.append("[...truncated at 200 entries — list a narrower path.]")
            break
    return "\n".join(lines) or "(empty)"


TOOLS = [read_file, write_file, edit_file, list_files]
