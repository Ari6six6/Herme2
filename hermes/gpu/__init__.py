"""GPU box state: ~/.hermes/gpu.json plus helpers to rebuild an endpoint."""

from __future__ import annotations

import json
import os

from hermes.config import hermes_home


def gpu_state_path():
    return hermes_home() / "gpu.json"


def load_gpu_state() -> dict:
    path = gpu_state_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_gpu_state(state: dict) -> None:
    hermes_home().mkdir(parents=True, exist_ok=True)
    gpu_state_path().write_text(json.dumps(state, indent=2) + "\n")
    os.chmod(gpu_state_path(), 0o600)


def probe_net_isolation(ep) -> bool:
    """Can the box drop a command's network at the kernel level? Probes the
    exact wrap shape remote_shell will use."""
    rc, out, _ = ep.run("unshare -n -- sh -c 'echo NETOK'", timeout=20)
    return rc == 0 and "NETOK" in out


def endpoint_from_state(state: dict):
    from hermes.ssh import SSHEndpoint

    if not state.get("host"):
        return None
    return SSHEndpoint(
        host=state["host"],
        port=int(state.get("port", 22)),
        user=state.get("user", "root"),
        remote_workspace=state.get("remote_workspace", "~/hermes-workspace"),
        net_isolation=bool(state.get("net_isolation", False)),
    )
