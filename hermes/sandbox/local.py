"""Run commands on the local machine — the VPS Hermes lives on.

Hermes now runs *on* the sandbox box (you SSH into the VPS and run it there), so
containers run right beside it, reached at localhost. This endpoint gives callers
the same `(rc, out, err)` interface an SSHEndpoint has, but executes locally
instead of over SSH — no second hop, no tunnel.
"""

from __future__ import annotations

import subprocess


class LocalEndpoint:
    host = "localhost"
    port = 0
    user = ""

    def run(self, command: str, timeout: int = 120, stdin: str | None = None):
        """Returns (rc, stdout, stderr). Shell semantics match the SSH path so the
        same docker/heredoc commands work locally."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                input=stdin,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
