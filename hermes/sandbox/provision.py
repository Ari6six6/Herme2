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


def ensure_runtime(ep, on_event=None) -> str:
    """Return the container runtime name on the VPS, installing Docker if none is
    present. Raises SandboxError if it still isn't usable afterwards."""
    def emit(text):
        if on_event:
            on_event(text)

    runtime = probe_container_runtime(ep)
    if runtime:
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
    emit(f"{runtime} ready")
    return runtime
