"""The agent's disposable exec sandbox — a container on the VPS, no network.

This is where agent code RUNS now: builds, tests, scripts, and the independent
verification re-run. It replaces the GPU box in that role. The GPU box goes back
to being only the model's host, reached as an inference endpoint — never a shell
the agent (or code it wrote) can reach the network from.

The container is `--network none`: the base image is pulled with the VPS's own
egress (the daemon pulls over the host, before the container exists), but once it
is running it has no interface at all, so nothing the agent executes can phone
out. That is the air-gap — enforced by the kernel, not a deny-list. The project
workspace is bind-mounted at /workspace so files the agent wrote on the phone are
there to run, and results land back in the same workspace with no transfer step.
"""

from __future__ import annotations

import re
import shlex

DEFAULT_IMAGE = "python:3.12-slim"


def container_name(project) -> str:
    """A stable, docker-safe container name per project, so the box is reused
    across turns and `sandbox reset` can find it."""
    raw = getattr(project, "name", "") or project.root.name or "hermes"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw).strip("-") or "hermes"
    return f"hermes-exec-{safe}"


def _run_container_cmd(name: str, runtime: str, workspace_host: str,
                       image: str) -> str:
    """Long-lived, network-less container with the project workspace mounted.
    `--network none` is the whole point: no egress the agent can reach."""
    return (
        f"{runtime} run -d --name {shlex.quote(name)} "
        f"--network none "
        f"-v {shlex.quote(workspace_host)}:/workspace -w /workspace "
        f"{shlex.quote(image)} sleep infinity"
    )


def _container_exists(ep, name: str, runtime: str) -> bool:
    rc, out, _ = ep.run(
        f"{runtime} ps -a --filter name=^{shlex.quote(name)}$ --format '{{{{.Names}}}}'"
    )
    return rc == 0 and name in (out or "")


def ensure_exec_container(ep, project, runtime: str = "",
                          image: str = DEFAULT_IMAGE) -> tuple[str, str]:
    """Make sure the air-gapped exec container is up; return (runtime, name).
    Raises SandboxError if the runtime or the container can't be brought up."""
    from hermes.sandbox.provision import SandboxError, ensure_runtime

    if not runtime:
        runtime = ensure_runtime(ep)
    name = container_name(project)
    if not _container_exists(ep, name, runtime):
        workspace_host = str(project.workspace_dir)
        rc, out, err = ep.run(
            _run_container_cmd(name, runtime, workspace_host, image), timeout=600
        )
        if rc != 0:
            raise SandboxError(
                f"could not start the exec sandbox: {(err or out).strip()[-300:]}")
    return runtime, name


def exec_in_sandbox(ep, name: str, cmd: str, runtime: str, cwd: str = "",
                    timeout: int = 120) -> tuple[int, str, str]:
    """Run one command inside the air-gapped container. `cwd` is relative to the
    mounted workspace (/workspace). Returns (rc, stdout, stderr)."""
    workdir = "/workspace"
    if cwd:
        # keep it inside the mount; a leading slash or .. can't escape /workspace
        rel = cwd.lstrip("/")
        workdir = f"/workspace/{rel}" if ".." not in rel.split("/") else "/workspace"
    return ep.run(
        f"{runtime} exec -w {shlex.quote(workdir)} {shlex.quote(name)} "
        f"sh -lc {shlex.quote(cmd)}",
        timeout=timeout,
    )


def stop(ep, project, runtime: str = "docker") -> None:
    """Tear the exec sandbox down on the VPS."""
    ep.run(f"{runtime} rm -f {shlex.quote(container_name(project))} 2>/dev/null || true")
