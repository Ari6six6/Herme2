"""Tools that execute on the rented GPU box over SSH — opt-in on-card compute.

The GPU box is the model's host, not the agent's workshop; routine code runs in
the air-gapped VPS sandbox (`sandbox_shell`). These tools exist only when the
operator turns the GPU shell on for genuine on-card work. When they do, shell and
file ops there run without confirmation (commands are echoed to the screen), and
installing and building software on the box is fine, so package managers and
source pulls reach the network. What stays on the VPS is raw egress and anything
that talks to the target — bounced back so every such byte is visible to the
operator. The network split is a deny-list speed bump paired with a kernel net
namespace (when available), not a cage: a root agent can route around it, so it is
paired with an honest ask in the prompt rather than a false claim of impossibility.
"""

from __future__ import annotations

import re
import shlex

from hermes.ssh import anchored_path, shell_path
from hermes.tools._common import need_gpu
from hermes.tools.base import obj_schema, tool
from hermes.ui import dim

# Installing/building software on the box is fine — these keep their network.
PROVISION_RE = re.compile(
    r"(?:^|[;&|]\s*|\bsudo\s+)"
    r"(apt(?:-get)?\s+(?:install|update|upgrade|build-dep)|aptitude\s+install|"
    r"dpkg\s+-i|apk\s+(?:add|update)|yum\s+(?:install|update)|dnf\s+install|"
    r"zypper\s+(?:in|install)|pacman\s+-S|snap\s+install|"
    r"pip3?\s+(?:install|download)|pipx\s+install|uv\s+(?:pip\s+install|add|sync)|"
    r"poetry\s+(?:add|install)|conda\s+(?:install|create)|mamba\s+(?:install|create)|"
    r"npm\s+(?:install|i|ci|add)|yarn\s+(?:add|install)|pnpm\s+(?:add|install|i)|"
    r"gem\s+install|bundle\s+install|"
    r"cargo\s+(?:install|build|fetch|add)|go\s+(?:get|install|mod|build|download)|"
    r"composer\s+(?:install|require|update)|mvn\b|gradle\b|mix\s+deps|"
    r"git\s+(?:clone|pull|fetch|submodule))\b"
)

# Raw egress / transfer / probe — keep these on the VPS, where egress is visible.
EGRESS_RE = re.compile(
    r"(?:^|[;&|]\s*|\bsudo\s+)"
    r"(curl|wget|aria2c|axel|scp|sftp|rsync|telnet|ncat|netcat|nc|socat|ping|"
    r"ssh|gdown|huggingface-cli|hf\s+download|git\s+(?:push|remote))\b"
)

NETWORK_DENIED = (
    "Keep this one on the VPS. Installing and building software on the box is "
    "fine — let the package managers pull what they need — but raw downloads and "
    "transfers (and anything that talks to the target) go through the VPS, where "
    "every byte is visible to your operator. Grab the file on the VPS "
    "(download_file / http_request), push it with the `transfer` toolbox tool, and "
    "use http_request / web_search for reading the web. remote_write works for "
    "small text files."
)


@tool(
    "remote_shell",
    "Run a shell command on the GPU box (Linux, root) — opt-in on-card compute "
    "(CUDA work, heavy jobs on the card). Routine code runs in the air-gapped VPS "
    "sandbox, not here. Default cwd is the remote workspace. Installing and "
    "building software here is fine (apt, pip, npm, git clone, ...). Keep raw "
    "downloads/transfers and anything that talks to the target on the VPS.",
    obj_schema(
        {
            "command": {"type": "string"},
            "timeout": {"type": "integer", "description": "seconds, default 120"},
            "cwd": {"type": "string", "description": "remote working dir (optional)"},
        },
        ["command"],
    ),
)
def remote_shell(args, ctx):
    err = need_gpu(ctx)
    if err:
        return err
    command = args["command"]
    inner = f"({command})"
    if not ctx.cfg.get("allow_gpu_network", False):
        is_provision = bool(PROVISION_RE.search(command))
        if EGRESS_RE.search(command) and not is_provision:
            return NETWORK_DENIED
        # Provisioning keeps the network it needs; everything else loses it at
        # the kernel level when the box supports namespaces (so target traffic
        # and exfil stay on the VPS).
        if not is_provision and ctx.gpu.net_isolation:
            inner = f"unshare -n -- sh -c {shlex.quote(command)}"
    timeout = min(int(args.get("timeout", 120)), 1800)
    cwd = shell_path(anchored_path(args.get("cwd") or "", ctx.gpu.remote_workspace))
    print(dim(f"  [gpu] $ {command}"))
    rc, out, errout = ctx.gpu.run(f"cd {cwd} && {inner}", timeout=timeout)
    body = (out or "") + (("\n[stderr]\n" + errout) if errout else "")
    return f"exit code {rc}\n{body.strip() or '(no output)'}"


@tool(
    "remote_read",
    "Read a text file from the GPU box. Relative paths resolve inside the "
    "remote workspace. Text only — to move binary files or anything large, "
    "equip the `transfer` toolbox tool.",
    obj_schema({"path": {"type": "string"}}, ["path"]),
)
def remote_read(args, ctx):
    err = need_gpu(ctx)
    if err:
        return err
    path = anchored_path(args["path"], ctx.gpu.remote_workspace)
    rc, out, errout = ctx.gpu.run(f"cat {shell_path(path)}", timeout=60)
    if rc != 0:
        return f"ERROR: {errout.strip() or 'read failed'}"
    return out


@tool(
    "remote_write",
    "Write a text file on the GPU box. Relative paths resolve inside the "
    "remote workspace. Text only — to move binary files or anything large, "
    "equip the `transfer` toolbox tool.",
    obj_schema(
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
)
def remote_write(args, ctx):
    err = need_gpu(ctx)
    if err:
        return err
    path = anchored_path(args["path"], ctx.gpu.remote_workspace)
    rc, _, errout = ctx.gpu.write_file(path, args["content"])
    if rc != 0:
        return f"ERROR: {errout.strip() or 'write failed'}"
    return f"wrote {len(args['content'])} chars to {path} on the GPU box"


TOOLS = [remote_shell, remote_read, remote_write]
