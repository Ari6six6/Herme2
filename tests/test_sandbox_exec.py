"""The air-gapped VPS exec sandbox that replaces the GPU box as the agent's
workshop. The invariants that matter are security ones: the container has NO
network, the project workspace is mounted, and a hostile cwd can't escape it."""

from hermes.sandbox import exec as sbx


class FakeEp:
    """Records every command; scripted (rc, out, err) responses in call order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list[str] = []

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        return self.responses.pop(0) if self.responses else (0, "", "")


def test_container_is_network_none_with_workspace_mounted(project):
    ep = FakeEp([(0, "", "")])  # not-exists check -> start
    ep.responses = [(0, "", ""), (0, "", "")]  # ps (no match), run
    ep.responses = [(1, "", ""), (0, "cid", "")]  # ps says absent, run succeeds
    runtime, name = sbx.ensure_exec_container(ep, project, runtime="docker")
    run_cmd = ep.calls[-1]
    assert "--network none" in run_cmd           # the air-gap, kernel-enforced
    assert f"-v {project.workspace_dir}:/workspace" in run_cmd
    assert name == sbx.container_name(project)


def test_exec_runs_inside_the_container_not_the_host():
    ep = FakeEp([(0, "ran\n", "")])
    rc, out, _ = sbx.exec_in_sandbox(ep, "hermes-exec-p", "python3 recon.py",
                                     runtime="docker")
    assert rc == 0 and "ran" in out
    assert ep.calls[0].startswith("docker exec -w /workspace hermes-exec-p")
    assert "python3 recon.py" in ep.calls[0]


def test_hostile_cwd_cannot_escape_the_mount():
    ep = FakeEp([(0, "", "")])
    sbx.exec_in_sandbox(ep, "c", "ls", runtime="docker", cwd="../../etc")
    # traversal collapses back to the mount root, never /etc on the host
    assert "-w /workspace " in ep.calls[0]
    assert "/etc" not in ep.calls[0]


def test_subdir_cwd_stays_under_workspace():
    ep = FakeEp([(0, "", "")])
    sbx.exec_in_sandbox(ep, "c", "ls", runtime="docker", cwd="data/sub")
    assert "-w /workspace/data/sub " in ep.calls[0]
