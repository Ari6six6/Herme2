import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

from hermes.config import Config
from hermes.project import Project


@contextmanager
def serve_reference_twin(model_dir, port):
    """Run the diff-only reference responder over a twin's recorded samples on a
    fixed port, so build-mode agent-loop tests (doer/antithesis/referee) can hit a
    live twin at the tunneled twin_local_port. Mirrors the real `build serve` from
    the agent's point of view; the production twin is a container on the VPS."""
    from hermes.twin import server as twin_server

    srv = twin_server.make_server(str(model_dir), port=port)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield
    finally:
        srv.shutdown()
        t.join(timeout=2)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    return tmp_path


@pytest.fixture
def cfg(home, tmp_path):
    c = Config.load()
    c.set("projects_dir", str(tmp_path / "projects"))
    return c


@pytest.fixture
def project(cfg):
    return Project.create(Path(cfg.get("projects_dir")), "testproj")


@pytest.fixture
def yes(monkeypatch):
    """Confirmation function that always approves."""
    return lambda *a, **k: True


@pytest.fixture
def no():
    return lambda *a, **k: False


@pytest.fixture
def never():
    """Confirmation function that must not be reached at all."""
    def _fail(*a, **k):
        raise AssertionError("confirm() was called for a supposedly free action")
    return _fail


class FakeEndpoint:
    """Scripted SSHEndpoint stand-in. `responses` is a list of (rc, out, err)
    consumed in call order; every command is recorded in `calls`."""

    def __init__(self, responses=None, file_payload=b"payload",
                 remote_workspace="~/hermes-workspace", net_isolation=False):
        self.responses = list(responses or [])
        self.file_payload = file_payload
        self.remote_workspace = remote_workspace
        self.net_isolation = net_isolation
        self.host, self.port, self.user = "fake.example", 22, "root"
        self.calls: list[str] = []
        self.writes: list[tuple] = []

    def _pop(self):
        return self.responses.pop(0) if self.responses else (0, "", "")

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        return self._pop()

    def run_out_to_file(self, command, out_path, timeout=600):
        self.calls.append(command)
        rc, _, err = self._pop()
        Path(out_path).write_bytes(self.file_payload)
        return rc, err

    def run_in_from_file(self, command, in_path, timeout=600):
        self.calls.append(command)
        self.last_stdin = Path(in_path).read_bytes()
        rc, _, err = self._pop()
        return rc, err

    def write_file(self, path, content):
        self.writes.append((path, content))
        return self._pop()


@pytest.fixture
def fake_gpu():
    return FakeEndpoint()
