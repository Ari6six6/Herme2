"""Compatibility shim — the SSH plumbing moved to hermes.ssh once managed
hosts started sharing it with the GPU box."""

from hermes.ssh import (  # noqa: F401
    SSHEndpoint,
    SSHError,
    kill_pid,
    parse_ssh_string,
    pid_alive,
    shell_path,
)
