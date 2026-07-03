"""Managed hosts: real servers the operator registered for the agent to
take care of — ~/.hermes/hosts.json, name -> {host, port, user, note}.

These are NOT sandboxes. The tools in hermes.tools.hosts gate everything
mutating behind operator confirmation; only the GPU box runs free.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from hermes.config import hermes_home
from hermes.ssh import SSHEndpoint

HOST_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def hosts_path() -> Path:
    return hermes_home() / "hosts.json"


def load_hosts() -> dict[str, dict]:
    path = hosts_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def save_hosts(hosts: dict[str, dict]) -> None:
    hermes_home().mkdir(parents=True, exist_ok=True)
    hosts_path().write_text(json.dumps(hosts, indent=2) + "\n")
    os.chmod(hosts_path(), 0o600)


def host_endpoint(rec: dict) -> SSHEndpoint:
    return SSHEndpoint(
        host=rec["host"],
        port=int(rec.get("port", 22)),
        user=rec.get("user", "root"),
    )


def hosts_env_line(hosts: dict[str, dict]) -> str:
    """One line for the system prompt: which servers exist and how to name them."""
    if not hosts:
        return "none"
    parts = []
    for name, rec in sorted(hosts.items()):
        entry = f"{name}={rec.get('user', 'root')}@{rec['host']}:{rec.get('port', 22)}"
        if rec.get("note"):
            entry += f" ({rec['note']})"
        parts.append(entry)
    return "; ".join(parts)
