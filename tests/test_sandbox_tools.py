"""sandbox_shell's network-failure hint: the container is --network none, so a
command that tried to reach the network fails the same way every time, not
intermittently. The tool result should say so plainly instead of leaving the
model to read a doomed retry as 'slow'."""

from hermes.tools.base import ToolContext
from hermes.tools.sandbox_tools import sandbox_shell


class FakeSandbox:
    host, port, user = "localhost", 0, ""

    def __init__(self, exec_output, exec_rc=1):
        self.exec_output = exec_output
        self.exec_rc = exec_rc
        self.calls: list[str] = []

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        if "command -v docker" in command:
            return 0, "docker\n", ""
        if " exec " in command:
            return self.exec_rc, "", self.exec_output
        return 0, "", ""  # ps (absent) / run (started)


def _ctx(project, cfg, sandbox):
    return ToolContext(project=project, cfg=cfg, sandbox=sandbox)


def test_network_failure_gets_a_clear_no_network_hint(project, cfg):
    sandbox = FakeSandbox("Temporary failure in name resolution")
    out = sandbox_shell.fn({"command": "pip install requests"}, _ctx(project, cfg, sandbox))
    assert "exit code 1" in out
    assert "--network none" in out
    assert "will not succeed on retry" in out


def test_unrelated_failure_gets_no_network_hint(project, cfg):
    sandbox = FakeSandbox("SyntaxError: invalid syntax")
    out = sandbox_shell.fn({"command": "python broken.py"}, _ctx(project, cfg, sandbox))
    assert "exit code 1" in out
    assert "--network none" not in out


def test_success_gets_no_hint_even_if_output_mentions_network_words(project, cfg):
    sandbox = FakeSandbox("Network is unreachable", exec_rc=0)
    out = sandbox_shell.fn({"command": "echo test"}, _ctx(project, cfg, sandbox))
    assert "exit code 0" in out
    assert "--network none" not in out
