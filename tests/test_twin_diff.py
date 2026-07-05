from hermes.tools.base import ToolContext
from hermes.tools.builder import twin_diff
from hermes.twin import clone as clone_mod
from hermes.twin.model import Exchange


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


def _open_twin(project, exchanges):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    for ex in exchanges:
        twin.add_exchange(ex)
    return twin


def test_twin_diff_all_match(project, cfg, monkeypatch):
    _open_twin(project, [
        Exchange(method="GET", path="/a", status=200, response_body="A"),
        Exchange(method="GET", path="/b", status=200, response_body="B"),
    ])
    def fake(m, u, h=None, b=None, t=45):
        return 200, {}, "A" if u.endswith("/a") else "B"

    monkeypatch.setattr(clone_mod, "_httpx_fetch", fake)
    out = twin_diff.fn({}, _ctx(project, cfg))
    assert "ALL MATCH" in out
    assert "2 match" in out


def test_twin_diff_flags_drift_and_missing(project, cfg, monkeypatch):
    _open_twin(project, [
        Exchange(method="GET", path="/ok", status=200, response_body="same"),
        Exchange(method="GET", path="/stale", status=200, response_body="old"),
    ])

    def fake(m, u, h=None, b=None, t=45):
        if u.endswith("/ok"):
            return 200, {}, "same"
        if u.endswith("/stale"):
            return 200, {}, "new"      # drifted
        return 200, {}, "x"

    monkeypatch.setattr(clone_mod, "_httpx_fetch", fake)
    out = twin_diff.fn({"paths": ["/ok", "/stale", "/brandnew"]}, _ctx(project, cfg))
    assert "1 match" in out
    assert "/stale" in out and "drifted" in out
    assert "/brandnew" in out and "missing" in out
    assert "divergence(s) to close" in out


def test_twin_diff_needs_samples(project, cfg):
    project.twin().init(source="https://api.example.com")
    out = twin_diff.fn({}, _ctx(project, cfg))
    assert "no paths to diff" in out


def test_twin_diff_survives_fetch_error(project, cfg, monkeypatch):
    _open_twin(project, [Exchange(method="GET", path="/a", status=200, response_body="A")])

    def boom(*a, **k):
        raise RuntimeError("dns")

    monkeypatch.setattr(clone_mod, "_httpx_fetch", boom)
    out = twin_diff.fn({}, _ctx(project, cfg))
    assert "error" in out and "/a" in out
