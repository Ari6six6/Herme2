"""Provision the sandbox host: make sure a container runtime is on the VPS.

On-demand and idempotent, like hermes.gpu.provision installs vLLM/llama.cpp: a
plain Ubuntu box probably has neither Docker nor Podman on first attach, so we
install Docker from the distro repos (good enough for local container work; we
don't need the upstream Docker CE channel). Re-running is a no-op once the
runtime is present.

The sandbox host is allowed network egress to do this — it has to pull base
images and packages. That's the deliberate policy difference from the GPU box:
the sandbox is a general workspace, so it installs freely.
"""

from __future__ import annotations

from hermes.sandbox import probe_container_runtime
from hermes.ui import dim


class SandboxError(Exception):
    pass


# Distro Docker is enough for local container work and is one apt away on a
# plain Ubuntu/Debian box. Start + enable so it survives a reboot of the VPS.
_INSTALL_DOCKER = (
    "set -e; "
    "export DEBIAN_FRONTEND=noninteractive; "
    "apt-get update -qq; "
    "apt-get install -y -qq docker.io; "
    "systemctl enable --now docker 2>/dev/null || service docker start 2>/dev/null || true"
)


def _require_usable(ep, runtime: str) -> None:
    """A *present* runtime is not a *usable* one. If the Hermes user can't reach the
    daemon, `build serve` would otherwise die deep inside a reconstruction with a
    cryptic 'permission denied ... docker.sock' — and the agent falls back to the
    GPU box. Catch it here, at provision time, with the actual fix instead of a
    guess."""
    from hermes.sandbox import runtime_usable

    ok, detail = runtime_usable(ep, runtime)
    if ok:
        return
    low = detail.lower()
    if "permission denied" in low or "docker.sock" in low or "/run/docker" in low:
        raise SandboxError(
            f"{runtime} is installed but this user can't reach its daemon "
            "(permission denied on the socket). Add yourself to the `docker` group "
            "and start a fresh login shell, then re-run `sandbox provision`:\n"
            '    sudo usermod -aG docker "$(id -un)" && newgrp docker\n'
            f"(or run hermes as root). [daemon said: {detail[-160:]}]"
        )
    if "cannot connect" in low or "daemon running" in low or "refused" in low:
        raise SandboxError(
            f"{runtime} is installed but its daemon isn't running. Start it and "
            "re-run `sandbox provision`:\n    sudo systemctl start docker\n"
            f"[daemon said: {detail[-160:]}]"
        )
    raise SandboxError(f"{runtime} is installed but not usable: {detail[-200:]}")


def ensure_runtime(ep, on_event=None) -> str:
    """Return the container runtime name on the VPS, installing Docker if none is
    present. Raises SandboxError if it still isn't usable afterwards — installed but
    unreachable (wrong group / dead daemon) counts as not usable."""
    def emit(text):
        if on_event:
            on_event(text)

    runtime = probe_container_runtime(ep)
    if runtime:
        _require_usable(ep, runtime)  # present isn't enough — it must be reachable
        return runtime

    emit("no container runtime found — installing docker.io (first time only)")
    print(dim("installing Docker on the sandbox host (first time can take a minute)..."))
    rc, _, err = ep.run(_INSTALL_DOCKER, timeout=900)
    if rc != 0:
        raise SandboxError(f"failed to install a container runtime: {err.strip()[-600:]}")

    runtime = probe_container_runtime(ep)
    if not runtime:
        raise SandboxError(
            "installed docker.io but no runtime is callable — check the VPS "
            "(is the docker daemon running? `systemctl status docker`)"
        )
    _require_usable(ep, runtime)
    emit(f"{runtime} ready")
    return runtime
