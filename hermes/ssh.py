"""SSH plumbing via the openssh binary (no paramiko — Termux-friendly).

ControlMaster multiplexing keeps per-command round-trips fast on a phone
connection. The tunnel is a background `ssh -N -L` process. Endpoints are
generic — the GPU box and managed hosts share this machinery.
"""

from __future__ import annotations

import os
import re
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from hermes.config import hermes_home

_SSH_URL_RE = re.compile(r"^ssh://(?:(?P<user>[^@]+)@)?(?P<host>[^:/]+)(?::(?P<port>\d+))?/?$")
_SSH_CMD_RE = re.compile(
    r"ssh\s+(?:.*?-p\s*(?P<port>\d+)\s+)?.*?(?P<user>[A-Za-z0-9_.-]+)@(?P<host>[A-Za-z0-9_.-]+)"
    r"(?:\s+.*?-p\s*(?P<port2>\d+))?"
)


class SSHError(Exception):
    pass


def parse_ssh_string(text: str):
    """Accepts 'ssh://root@host:port' or a pasted 'ssh -p PORT root@host ...'."""
    text = text.strip()
    m = _SSH_URL_RE.match(text)
    if m:
        return (m["user"] or "root", m["host"], int(m["port"] or 22))
    m = _SSH_CMD_RE.search(text)
    if m:
        port = m["port"] or m["port2"] or "22"
        return (m["user"], m["host"], int(port))
    raise SSHError(f"could not parse SSH string: {text!r}")


def anchored_path(p: str, workspace: str) -> str:
    """Resolve a remote path against the workspace. Absolute and `~` paths
    pass through untouched; anything relative lands inside the workspace —
    so tool paths agree with remote_shell's default cwd instead of silently
    resolving against the SSH login dir."""
    p = p.strip()
    if p.startswith(("/", "~")):
        return p
    return f"{workspace.rstrip('/')}/{p}" if p else workspace


def shell_path(p: str) -> str:
    """Quote a remote path for safe shell interpolation while keeping a
    leading `~`/`~/` expandable. `~user` is not supported — it comes back
    quoted literally and the remote shell will say so."""
    if p == "~":
        return '"$HOME"'
    if p.startswith("~/"):
        return '"$HOME"/' + shlex.quote(p[2:])
    return shlex.quote(p)


@dataclass
class SSHEndpoint:
    host: str
    port: int = 22
    user: str = "root"
    remote_workspace: str = "~/hermes-workspace"
    net_isolation: bool = False  # kernel-level (unshare -n) verified on this box

    def base_args(self) -> list[str]:
        sockets = hermes_home() / "cm-sockets"
        sockets.mkdir(parents=True, exist_ok=True)
        return [
            "ssh",
            "-p", str(self.port),
            "-o", "ControlMaster=auto",
            "-o", f"ControlPath={sockets}/%r@%h-%p",
            "-o", "ControlPersist=600",
            "-o", "ServerAliveInterval=30",
            "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ConnectTimeout=15",
            f"{self.user}@{self.host}",
        ]

    def run(self, command: str, timeout: int = 120, stdin: str | None = None):
        """Returns (rc, stdout, stderr)."""
        try:
            proc = subprocess.run(
                self.base_args() + [command],
                capture_output=True,
                text=True,
                errors="replace",  # binary in stdout must not raise mid-run
                timeout=timeout,
                input=stdin,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "", "ssh binary not found — `pkg install openssh` on Termux"

    def run_out_to_file(self, command: str, out_path: Path, timeout: int = 600):
        """Run a command streaming its stdout (binary) into a local file.
        Returns (rc, stderr_text)."""
        try:
            with open(out_path, "wb") as f:
                proc = subprocess.run(
                    self.base_args() + [command],
                    stdout=f,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            return proc.returncode, proc.stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "ssh binary not found — `pkg install openssh` on Termux"

    def run_in_from_file(self, command: str, in_path: Path, timeout: int = 600):
        """Run a command streaming a local file (binary) into its stdin.
        Returns (rc, stderr_text)."""
        try:
            with open(in_path, "rb") as f:
                proc = subprocess.run(
                    self.base_args() + [command],
                    stdin=f,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=timeout,
                )
            return proc.returncode, proc.stderr.decode(errors="replace")
        except subprocess.TimeoutExpired:
            return 124, f"timed out after {timeout}s"
        except FileNotFoundError:
            return 127, "ssh binary not found — `pkg install openssh` on Termux"

    def check(self) -> bool:
        rc, out, _ = self.run("echo HERMES_OK", timeout=30)
        return rc == 0 and "HERMES_OK" in out

    def write_file(self, path: str, content: str):
        q = shell_path(path)
        return self.run(f'mkdir -p "$(dirname {q})" && cat > {q}', stdin=content)

    # -- tunnel --------------------------------------------------------------
    def tunnel_args(self, local_port: int, remote_port: int) -> list[str]:
        args = self.base_args()
        # A tunnel should be its own connection, not the multiplexed master.
        args[args.index("ControlMaster=auto")] = "ControlMaster=no"
        return args[:1] + [
            "-N",
            "-L", f"{local_port}:127.0.0.1:{remote_port}",
            "-o", "ExitOnForwardFailure=yes",
        ] + args[1:]

    def start_tunnel(self, local_port: int, remote_port: int) -> int:
        proc = subprocess.Popen(
            self.tunnel_args(local_port, remote_port),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return proc.pid

    def close_master(self) -> None:
        """Tear down the ControlMaster connection (otherwise ControlPersist
        keeps an ssh process around for 10 minutes after the last command)."""
        try:
            subprocess.run(
                self.base_args()[:1] + ["-O", "exit"] + self.base_args()[1:],
                capture_output=True,
                timeout=15,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass


def pid_alive(pid: int) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def kill_pid(pid: int) -> None:
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
