"""transfer: VPS project <-> GPU box, streamed and binary-safe."""

from conftest import FakeEndpoint

from hermes.toolbox import transfer
from hermes.tools.base import ToolContext


def _ctx(project, cfg, gpu):
    return ToolContext(project=project, cfg=cfg, gpu=gpu)


def test_push_streams_binary(project, cfg, home):
    payload = bytes(range(256)) * 3
    src = project.workspace_dir / "model.bin"
    src.write_bytes(payload)
    gpu = FakeEndpoint([(0, "", "")])
    out = transfer.run(
        {"direction": "push", "local_path": "workspace/model.bin",
         "remote_path": "~/hermes-workspace/model.bin"},
        _ctx(project, cfg, gpu),
    )
    assert out == f'pushed {len(payload)} bytes to ~/hermes-workspace/model.bin'
    assert gpu.last_stdin == payload
    assert 'cat > "$HOME"/hermes-workspace/model.bin' in gpu.calls[0]
    assert gpu.calls[0].startswith('mkdir -p "$(dirname ')


def test_push_quotes_awkward_remote_path(project, cfg, home):
    (project.workspace_dir / "a.txt").write_text("x")
    gpu = FakeEndpoint([(0, "", "")])
    transfer.run(
        {"direction": "push", "local_path": "workspace/a.txt",
         "remote_path": "/data/my results/a.txt"},
        _ctx(project, cfg, gpu),
    )
    assert "cat > '/data/my results/a.txt'" in gpu.calls[0]


def test_push_relative_remote_path_anchors_to_workspace(project, cfg, home):
    (project.workspace_dir / "a.txt").write_text("x")
    gpu = FakeEndpoint([(0, "", "")])
    out = transfer.run(
        {"direction": "push", "local_path": "workspace/a.txt",
         "remote_path": "a.txt"},
        _ctx(project, cfg, gpu),
    )
    assert 'cat > "$HOME"/hermes-workspace/a.txt' in gpu.calls[0]
    assert out == "pushed 1 bytes to ~/hermes-workspace/a.txt"


def test_pull_relative_remote_path_anchors_to_workspace(project, cfg, home):
    gpu = FakeEndpoint([(0, "", "")], file_payload=b"y")
    transfer.run(
        {"direction": "pull", "local_path": "workspace/out.bin",
         "remote_path": "out.bin"},
        _ctx(project, cfg, gpu),
    )
    assert gpu.calls[0] == 'cat "$HOME"/hermes-workspace/out.bin'


def test_push_missing_local_file(project, cfg, home):
    gpu = FakeEndpoint()
    out = transfer.run(
        {"direction": "push", "local_path": "workspace/nope.bin",
         "remote_path": "/tmp/x"},
        _ctx(project, cfg, gpu),
    )
    assert out.startswith("ERROR: no such local file")
    assert gpu.calls == []


def test_pull_streams_binary(project, cfg, home):
    payload = b"\x00\xff" * 100
    gpu = FakeEndpoint([(0, "", "")], file_payload=payload)
    out = transfer.run(
        {"direction": "pull", "local_path": "workspace/results/out.bin",
         "remote_path": "~/hermes-workspace/out.bin"},
        _ctx(project, cfg, gpu),
    )
    assert out == f"pulled {len(payload)} bytes to workspace/results/out.bin"
    assert gpu.calls[0] == 'cat "$HOME"/hermes-workspace/out.bin'
    assert (project.workspace_dir / "results/out.bin").read_bytes() == payload


def test_pull_failure_leaves_no_partial_file(project, cfg, home):
    gpu = FakeEndpoint([(1, "", "cat: /tmp/gone: No such file or directory")])
    out = transfer.run(
        {"direction": "pull", "local_path": "workspace/gone.bin",
         "remote_path": "/tmp/gone"},
        _ctx(project, cfg, gpu),
    )
    assert out.startswith("ERROR: pull failed")
    assert "No such file" in out
    assert not (project.workspace_dir / "gone.bin").exists()


def test_guards(project, cfg, home):
    assert transfer.run(
        {"direction": "push", "local_path": "a", "remote_path": "b"},
        _ctx(project, cfg, None),
    ).startswith("ERROR: no GPU box")
    assert transfer.run(
        {"direction": "push", "local_path": "../../etc/passwd", "remote_path": "b"},
        _ctx(project, cfg, FakeEndpoint()),
    ).startswith("DENIED")
