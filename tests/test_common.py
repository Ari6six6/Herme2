"""The shared tool guards in hermes.tools._common — one source of truth for the
"no GPU box" / "unknown host" errors and the twin lookup that several tool
modules used to hand-roll."""

from types import SimpleNamespace

from hermes.tools._common import host_or_error, need_gpu, twin_for
from hermes.twin.model import TwinModel


def test_need_gpu_reports_when_absent():
    assert need_gpu(SimpleNamespace(gpu=None)).startswith("ERROR: no GPU box")


def test_need_gpu_passes_when_attached():
    assert need_gpu(SimpleNamespace(gpu=object())) is None


def test_host_or_error_unknown_lists_known():
    ctx = SimpleNamespace(hosts={"web": object()})
    out = host_or_error(ctx, "db")
    assert isinstance(out, str)
    assert out.startswith("ERROR: no managed host 'db'")
    assert "web" in out  # the known hosts are surfaced


def test_host_or_error_returns_endpoint():
    ep = object()
    ctx = SimpleNamespace(hosts={"web": ep})
    assert host_or_error(ctx, "web") is ep


def test_twin_for_points_at_project_twin_dir(project):
    ctx = SimpleNamespace(project=project)
    twin = twin_for(ctx)
    assert isinstance(twin, TwinModel)
    assert twin.root == project.twin_dir
