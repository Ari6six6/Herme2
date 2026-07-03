from tests.conftest import FakeEndpoint

from hermes import sandbox
from hermes.sandbox import provision
from hermes.sandbox.local import LocalEndpoint


def test_local_endpoint_runs_locally():
    rc, out, _ = sandbox.local_endpoint().run("echo HERMES_OK")
    assert rc == 0 and "HERMES_OK" in out


def test_local_endpoint_reports_nonzero():
    rc, _, _ = LocalEndpoint().run("exit 3")
    assert rc == 3


def test_probe_container_runtime_detects_docker():
    ep = FakeEndpoint(responses=[(0, "docker\n", "")])
    assert sandbox.probe_container_runtime(ep) == "docker"


def test_probe_container_runtime_detects_podman():
    ep = FakeEndpoint(responses=[(0, "podman\n", "")])
    assert sandbox.probe_container_runtime(ep) == "podman"


def test_probe_container_runtime_none():
    ep = FakeEndpoint(responses=[(0, "none\n", "")])
    assert sandbox.probe_container_runtime(ep) == ""


def test_probe_kvm_true_false():
    assert sandbox.probe_kvm(FakeEndpoint(responses=[(0, "KVM\n", "")])) is True
    assert sandbox.probe_kvm(FakeEndpoint(responses=[(0, "NOKVM\n", "")])) is False


def test_capabilities_bundles_probes():
    ep = FakeEndpoint(responses=[(0, "docker\n", ""), (0, "NOKVM\n", "")])
    caps = sandbox.capabilities(ep)
    assert caps == {"runtime": "docker", "kvm": False}


def test_ensure_runtime_returns_existing():
    ep = FakeEndpoint(responses=[(0, "docker\n", "")])
    assert provision.ensure_runtime(ep) == "docker"
    assert not any("apt-get" in c for c in ep.calls)


def test_ensure_runtime_installs_when_missing():
    # probe(none), install(ok), re-probe(docker)
    ep = FakeEndpoint(responses=[(0, "none\n", ""), (0, "", ""), (0, "docker\n", "")])
    assert provision.ensure_runtime(ep) == "docker"
    assert any("apt-get install" in c and "docker.io" in c for c in ep.calls)


def test_ensure_runtime_raises_when_install_fails():
    ep = FakeEndpoint(responses=[(0, "none\n", ""), (1, "", "no space left")])
    try:
        provision.ensure_runtime(ep)
        assert False, "expected SandboxError"
    except provision.SandboxError as e:
        assert "no space left" in str(e)
