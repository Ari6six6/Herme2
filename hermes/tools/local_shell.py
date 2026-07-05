"""Local shell on the phone (Termux). Read-only commands (see
hermes.tools.readonly) run free, same tier as host_shell; anything not
positively classified read-only is operator-confirmed — this is still the
most privileged surface (the box the harness itself runs on), so the classifier
fails closed exactly as it does for managed hosts."""

from __future__ import annotations

import subprocess

from hermes.tools.base import obj_schema, tool
from hermes.tools.readonly import classify
from hermes.ui import dim


@tool(
    "local_shell",
    "Run a shell command on the operator's phone (Termux). Read-only commands "
    "run freely; anything else pauses for operator y/n. Use for: running "
    "scripts you wrote for the phone, installing Termux packages, anything "
    "local. Runs at the project root by default, so paths match the file "
    "tools: a file you wrote as `workspace/x.py` is run with `python "
    "workspace/x.py` (no `cd workspace` first). Pass `cwd` to start somewhere "
    "else.",
    obj_schema(
        {
            "command": {"type": "string", "description": "exact shell command"},
            "timeout": {"type": "integer", "description": "seconds, default 60"},
            "cwd": {"type": "string", "description": "working dir relative to project root (optional; default: project root)"},
        },
        ["command"],
    ),
)
def local_shell(args, ctx):
    command = args["command"]
    timeout = min(int(args.get("timeout", 60)), 600)
    cwd = ctx.project.root
    if args.get("cwd"):
        from hermes.paths import PathDenied, resolve_in

        try:
            cwd = resolve_in(ctx.project.root, args["cwd"])
        except PathDenied:
            return "DENIED: cwd outside the project directory."
    read_only, reason = classify(command)
    if read_only:
        print(dim(f"  [local] $ {command}"))
    elif not ctx.confirm("agent wants to run a LOCAL shell command on the phone:",
                         detail=f"  $ {command}\n  (cwd: {cwd}, timeout: {timeout}s)"
                                f"\n  (not classified read-only: {reason})"):
        return "DENIED by operator."
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return f"ERROR: command timed out after {timeout}s"
    out = (proc.stdout or "") + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
    return f"exit code {proc.returncode}\n{out.strip() or '(no output)'}"


TOOLS = [local_shell]
