"""replicate: managed host -> phone relay -> GPU sandbox."""

from conftest import FakeEndpoint

from hermes.toolbox import replicate
from hermes.tools.base import ToolContext


def _ctx(project, cfg, host_ep, gpu_ep):
    return ToolContext(project=project, cfg=cfg, gpu=gpu_ep,
                       hosts={"web": host_ep} if host_ep else {})


def test_directory_flow(project, cfg, home):
    host = FakeEndpoint([
        (0, "4096\t/srv/app", ""),   # du -sb
        (0, "DIR", ""),              # test -d
        (0, "", ""),                 # tar out
    ], file_payload=b"tarball-bytes")
    gpu = FakeEndpoint([(0, "", ""), (0, "", "")])  # mkdir, tar in
    out = replicate.run({"host": "web", "src": "/srv/app"},
                        _ctx(project, cfg, host, gpu))
    assert out.startswith("replicated directory /srv/app")
    tar_pull = host.calls[2]
    assert tar_pull.startswith("tar -C /srv -czf -")
    assert "--exclude=.git" in tar_pull and "--exclude=node_modules" in tar_pull
    assert gpu.calls[0] == 'mkdir -p "$HOME"/hermes-workspace/app'
    assert "tar -C" in gpu.calls[1] and "-xzf -" in gpu.calls[1]
    assert gpu.last_stdin == b"tarball-bytes"


def test_file_flow(project, cfg, home):
    host = FakeEndpoint([
        (0, "120 /etc/nginx/nginx.conf", ""),  # du -sb
        (0, "FILE", ""),                       # test -d
        (0, "", ""),                           # cat out
    ], file_payload=b"server {}")
    gpu = FakeEndpoint([(0, "", "")])  # mkdir + cat in one call
    out = replicate.run(
        {"host": "web", "src": "/etc/nginx/nginx.conf", "dest": "repro/nginx.conf"},
        _ctx(project, cfg, host, gpu),
    )
    assert out.startswith("replicated file /etc/nginx/nginx.conf")
    assert "cat /etc/nginx/nginx.conf" in host.calls[2]
    assert "cat > " in gpu.calls[0]
    assert gpu.last_stdin == b"server {}"


def test_over_cap_aborts_before_any_transfer(project, cfg, home):
    host = FakeEndpoint([(0, f"{300 * 1024 * 1024} /srv/big", "")])
    gpu = FakeEndpoint()
    out = replicate.run({"host": "web", "src": "/srv/big"},
                        _ctx(project, cfg, host, gpu))
    assert out.startswith("ERROR") and "cap" in out
    assert len(host.calls) == 1  # only the du
    assert gpu.calls == []


def test_excludes_override(project, cfg, home):
    host = FakeEndpoint([(0, "10 /srv/app", ""), (0, "DIR", ""), (0, "", "")])
    gpu = FakeEndpoint()
    replicate.run({"host": "web", "src": "/srv/app", "excludes": []},
                  _ctx(project, cfg, host, gpu))
    assert "--exclude" not in host.calls[2]


def test_guards(project, cfg, home):
    assert replicate.run({"host": "web", "src": "/x"},
                         _ctx(project, cfg, FakeEndpoint(), None)).startswith(
        "ERROR: no GPU box")
    assert replicate.run({"host": "db", "src": "/x"},
                         _ctx(project, cfg, None, FakeEndpoint())).startswith(
        "ERROR: no managed host 'db'")
