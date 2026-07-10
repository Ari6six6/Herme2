"""The verification pass grades in the air-gapped sandbox, never on the GPU box.

Two guarantees: the verifier's registry has no GPU-reaching tool (so it cannot
run — or "confirm" — the solution on a networked machine), and it runs code in
the VPS sandbox container via sandbox_shell instead.
"""

from hermes import agent
from hermes.llm import MockBackend


class FakeSandbox:
    """Stands in for the VPS sandbox host. Answers the docker calls sandbox_shell
    makes so a scripted verifier can 'run' the code; records every command."""

    host, port, user = "localhost", 0, ""

    def __init__(self):
        self.calls: list[str] = []

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        if "command -v docker" in command:
            return 0, "docker\n", ""  # runtime probe: docker is present
        if " exec " in command:
            return 0, "4\n", ""       # the code's output inside the container
        return 0, "", ""              # ps (absent) / run (started)


def _run(project, cfg, script, sandbox):
    return agent.run(project, "do it", cfg, MockBackend(script),
                     gpu=object(), sandbox=sandbox, env={},
                     confirm_fn=lambda *a, **k: True)


def test_verifier_cannot_reach_the_gpu_and_uses_the_sandbox(project, cfg):
    sandbox = FakeSandbox()
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/m.py", "content": "print(2+2)"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
        # verifier: first reaches for the GPU box (must be refused), then runs
        # the code in the air-gapped sandbox and rules.
        {"tool": "remote_shell", "args": {"command": "python m.py"}},
        {"tool": "sandbox_shell", "args": {"command": "python m.py"}},
        {"text": "ran it in the sandbox: 4. VERDICT: PASS"},
    ], sandbox)

    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    # the GPU tool was not available to the verifier
    assert "unknown tool 'remote_shell'" in transcript
    # and the actual run happened inside the container (docker exec), air-gapped
    assert any(" exec " in c for c in sandbox.calls)
    assert all("remote_shell" not in c for c in sandbox.calls)


def test_gpu_tools_are_stripped_from_the_verify_registry(project, cfg):
    from hermes.tools import build_registry

    cfg.set("gpu_shell", True)  # give the doer the GPU shell to strip
    reg = build_registry(project, cfg, lambda *a, **k: True)
    names_full = set(reg.names())
    names_verify = set(reg.without(agent.GPU_TOOLS).names())
    # the doer keeps them; the verifier does not
    assert {"remote_shell", "remote_read", "remote_write"} <= names_full
    assert not (agent.GPU_TOOLS & names_verify)
    # sandbox_shell survives the strip — the verifier's only way to run code
    assert "sandbox_shell" in names_verify
