"""Tools for managed hosts — the operator's real servers, reached from the
phone over SSH.

These are NOT sandboxes, so the gate has the opposite polarity to the GPU
box: it fails CLOSED. Only commands positively classified as read-only run
free (see hermes.tools.readonly); everything else — and every file write —
pauses for operator y/n.
"""

from __future__ import annotations

from hermes.ssh import shell_path
from hermes.tools._common import host_or_error as _get
from hermes.tools.base import obj_schema, tool
from hermes.tools.readonly import classify
from hermes.ui import dim


@tool(
    "host_shell",
    "Run a shell command on a managed host (one of the operator's real "
    "servers). Read-only commands run freely; anything that could change the "
    "server pauses for operator y/n. This is NOT a sandbox — be deliberate.",
    obj_schema(
        {
            "host": {"type": "string", "description": "registered host name"},
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds, default 60"},
            "cwd": {"type": "string", "description": "remote working dir (optional)"},
        },
        ["host", "command"],
    ),
)
def host_shell(args, ctx):
    ep = _get(ctx, args["host"])
    if isinstance(ep, str):
        return ep
    command = args["command"]
    timeout = min(int(args.get("timeout", 60)), 600)
    read_only, reason = classify(command)
    if read_only:
        print(dim(f"  [host:{args['host']}] $ {command}"))
    elif not ctx.confirm(
        f"agent wants to run a command on managed host '{args['host']}' "
        f"({ep.user}@{ep.host}):",
        detail=f"  $ {command}\n  (not classified read-only: {reason})",
    ):
        return ("DENIED by operator. If you only need to inspect the server, "
                "use a read-only command instead.")
    if args.get("cwd"):
        command = f"cd {shell_path(args['cwd'])} && ({command})"
    rc, out, errout = ep.run(command, timeout=timeout)
    body = (out or "") + (("\n[stderr]\n" + errout) if errout else "")
    return f"exit code {rc}\n{body.strip() or '(no output)'}"


@tool(
    "host_read",
    "Read a text file from a managed host. Reads are free.",
    obj_schema(
        {"host": {"type": "string"}, "path": {"type": "string"}},
        ["host", "path"],
    ),
)
def host_read(args, ctx):
    ep = _get(ctx, args["host"])
    if isinstance(ep, str):
        return ep
    rc, out, errout = ep.run(f"cat {shell_path(args['path'])}", timeout=60)
    if rc != 0:
        return f"ERROR: {errout.strip() or 'read failed'}"
    return out


@tool(
    "host_write",
    "Write a text file on a managed host. ALWAYS asks the operator first — "
    "this changes a real server.",
    obj_schema(
        {
            "host": {"type": "string"},
            "path": {"type": "string"},
            "content": {"type": "string"},
        },
        ["host", "path", "content"],
    ),
)
def host_write(args, ctx):
    ep = _get(ctx, args["host"])
    if isinstance(ep, str):
        return ep
    content = args["content"]
    preview = "\n".join("    " + line for line in content.splitlines()[:5])
    if not ctx.confirm(
        f"agent wants to WRITE a file on managed host '{args['host']}' "
        f"({ep.user}@{ep.host}):",
        detail=f"  {args['path']} ({len(content)} chars)\n{preview}",
        viewable=content,
    ):
        return "DENIED by operator."
    rc, _, errout = ep.write_file(args["path"], content)
    if rc != 0:
        return f"ERROR: {errout.strip() or 'write failed'}"
    return f"wrote {len(content)} chars to {args['path']} on '{args['host']}'"


TOOLS = [host_shell, host_read, host_write]
