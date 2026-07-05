"""The on-demand live-accuracy loop: re-check a request against the target and
correct the twin if its stored sample drifted."""

from hermes.tools.base import ToolContext
from hermes.tools.twin import twin_reground
from hermes.twin import clone as clone_mod
from hermes.twin.clone import reground
from hermes.twin.model import Exchange


def _seal(project, exchanges):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    for ex in exchanges:
        twin.add_exchange(ex)
    twin.seal()
    return twin


def test_upsert_replaces_same_key_only(project):
    twin = project.twin()
    twin.init(source="x")
    twin.add_exchange(Exchange(method="GET", path="/a", status=200, response_body="old"))
    twin.add_exchange(Exchange(method="GET", path="/b", status=200, response_body="keep"))
    twin.upsert_exchange(Exchange(method="GET", path="/a", status=200, response_body="new"))
    assert twin.respond("GET", "/a").response_body == "new"
    assert twin.respond("GET", "/b").response_body == "keep"
    assert len(twin.exchanges()) == 2


def test_reground_accurate_leaves_twin_unchanged(project):
    twin = _seal(project, [Exchange(method="GET", path="/v", status=200,
                                    response_body="same", content_type="text/plain")])
    fetch = lambda m, u, h=None, b=None, t=45: (200, {"content-type": "text/plain"}, "same")
    r = reground(twin, "https://api.example.com", "/v", fetch=fetch)
    assert r["status"] == "accurate"
    assert twin.is_sealed()
    assert twin.respond("GET", "/v").response_body == "same"


def test_reground_corrects_drifted_sample(project):
    twin = _seal(project, [Exchange(method="GET", path="/v", status=200,
                                    response_body="stale", content_type="text/plain")])
    fetch = lambda m, u, h=None, b=None, t=45: (200, {"content-type": "text/plain"}, "fresh")
    r = reground(twin, "https://api.example.com", "/v", fetch=fetch)
    assert r["status"] == "corrected"
    assert r["old"][1] == "stale" and r["new"][1] == "fresh"
    assert twin.is_sealed()                       # re-sealed after the fix
    assert twin.respond("GET", "/v").response_body == "fresh"  # truth, not the stale value
    assert len(twin.exchanges()) == 1             # replaced, not duplicated


def test_reground_adds_when_missing(project):
    twin = _seal(project, [Exchange(method="GET", path="/known", status=200,
                                    response_body="x")])
    fetch = lambda m, u, h=None, b=None, t=45: (201, {"content-type": "text/plain"}, "new!")
    r = reground(twin, "https://api.example.com", "/fresh", fetch=fetch)
    assert r["status"] == "added"
    assert twin.respond("GET", "/fresh").response_body == "new!"


def test_reground_rejects_non_read_method(project):
    twin = _seal(project, [Exchange(method="GET", path="/v", status=200, response_body="x")])
    r = reground(twin, "https://api.example.com", "/v", method="POST")
    assert r["status"] == "error"


def test_reground_handles_unreachable_target(project):
    twin = _seal(project, [Exchange(method="GET", path="/v", status=200, response_body="x")])

    def boom(*a, **k):
        raise RuntimeError("connection refused")

    r = reground(twin, "https://api.example.com", "/v", fetch=boom)
    assert r["status"] == "error"
    assert twin.respond("GET", "/v").response_body == "x"  # unchanged


def test_twin_reground_tool_reports_drift(project, cfg, monkeypatch):
    _seal(project, [Exchange(method="GET", path="/v", status=200, response_body="stale",
                             content_type="text/plain")])
    monkeypatch.setattr(clone_mod, "_httpx_fetch",
                        lambda m, u, h=None, b=None, t=45: (200, {"content-type": "text/plain"}, "fresh"))
    out = twin_reground.fn({"path": "/v"}, ToolContext(project=project, cfg=cfg))
    assert "drifted" in out and "fresh" in out


def test_twin_reground_tool_confirms_accuracy(project, cfg, monkeypatch):
    _seal(project, [Exchange(method="GET", path="/v", status=200, response_body="same",
                             content_type="text/plain")])
    monkeypatch.setattr(clone_mod, "_httpx_fetch",
                        lambda m, u, h=None, b=None, t=45: (200, {"content-type": "text/plain"}, "same"))
    out = twin_reground.fn({"path": "/v"}, ToolContext(project=project, cfg=cfg))
    assert "mismatch is in your solution" in out
