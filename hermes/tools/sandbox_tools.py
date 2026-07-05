"""The agent's exec sandbox tool — runs code in the air-gapped VPS container.

This is the replacement for `remote_shell` as the place code runs. The GPU box
is going back to being only the model's host; agent-run code executes here, in a
`--network none` container on the VPS, so nothing it does can reach the network.
The project workspace is bind-mounted, so files written with `write_file`
(project-relative `workspace/...`) are already present — no transfer step.
"""

from __future__ import annotations

import re

from hermes.tools.base import obj_schema, tool
from hermes.ui import dim


def _need_sandbox(ctx):
    if ctx.sandbox is None:
        return "ERROR: no sandbox host available. Hermes runs on the VPS; the "\
               "sandbox is this box. Check `sandbox status`."
    return None


# Signatures a failed command leaves behind when it tried to reach the network
# from inside the `--network none` container — there is no route out, ever, so
# these aren't transient/slow, they're permanent for this container. Naming
# them explicitly stops the model from reading a doomed retry as "flaky" and
# burning turns on it.
_NETWORK_FAILURE_RE = re.compile(
    r"Temporary failure in name resolution"
    r"|Network is unreachable"
    r"|Could not resolve host"
    r"|Connection refused"
    r"|No route to host"
    r"|Failed to connect to"
    r"|Could not connect to",
    re.I,
)
_NETWORK_HINT = (
    "\n\nNOTE: that failure pattern means the command tried to reach the "
    "network. This container is `--network none` — no route out exists, ever; "
    "this will not succeed on retry and it is not slow, it never connects. "
    "Anything needing installs/downloads has to happen in the twin build "
    "container (`build_run`) before the twin is sealed — it's too late for that "
    "here."
)


@tool(
    "sandbox_shell",
    "Run a shell command in the air-gapped sandbox container on the VPS — your "
    "workshop for running code, tests, and builds. It has NO network (nothing "
    "you run can reach out), and the project workspace is mounted at the cwd, so "
    "a file you wrote as `workspace/x.py` runs as `python x.py`. Paths are "
    "relative to the workspace.",
    obj_schema(
        {
            "command": {"type": "string", "description": "exact shell command"},
            "timeout": {"type": "integer", "description": "seconds, default 120"},
            "cwd": {"type": "string", "description": "working dir under the workspace (optional)"},
        },
        ["command"],
    ),
)
def sandbox_shell(args, ctx):
    err = _need_sandbox(ctx)
    if err:
        return err
    from hermes.sandbox import exec as sbx
    from hermes.sandbox.provision import SandboxError

    image = ctx.cfg.get("sandbox_image", sbx.DEFAULT_IMAGE)
    try:
        runtime, name = sbx.ensure_exec_container(ctx.sandbox, ctx.project, image=image)
    except SandboxError as e:
        return f"ERROR: sandbox unavailable — {e}"
    command = args["command"]
    timeout = min(int(args.get("timeout", 120)), 1800)
    print(dim(f"  [sandbox] $ {command}"))
    rc, out, errout = sbx.exec_in_sandbox(
        ctx.sandbox, name, command, runtime, cwd=args.get("cwd", ""), timeout=timeout
    )
    body = (out or "") + (("\n[stderr]\n" + errout) if errout else "")
    result = f"exit code {rc}\n{body.strip() or '(no output)'}"
    if rc != 0 and _NETWORK_FAILURE_RE.search(body):
        result += _NETWORK_HINT
    return result


TOOLS = [sandbox_shell]
