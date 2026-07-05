from hermes import package
from hermes.tools import build_registry
from hermes.tools.base import ToolContext
from hermes.tools.twin import twin_expand, twin_map, twin_request
from hermes.twin.model import Exchange


def _seal(project, exchanges, **manifest):
    twin = project.twin()
    twin.init(source="https://api.example.com", **manifest)
    for ex in exchanges:
        twin.add_exchange(ex)
    twin.seal()
    return twin


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


class _Resp:
    def __init__(self, status, text, ctype="application/json"):
        self.status_code = status
        self.text = text
        self.headers = {"content-type": ctype}


def test_twin_request_hits_the_live_twin(project, cfg, monkeypatch):
    _seal(project, [Exchange(method="GET", path="/users/1", status=200,
                             response_body='{"id":1}', content_type="application/json")])
    captured = {}

    def fake_request(method, url, **kw):
        captured["method"], captured["url"] = method, url
        return _Resp(200, '{"id":1}')

    monkeypatch.setattr("hermes.tools.twin.httpx.request", fake_request)
    out = twin_request.fn({"path": "/users/1"}, _ctx(project, cfg))
    assert "live runtime response" in out
    assert '{"id":1}' in out and "HTTP 200" in out
    assert captured["method"] == "GET"
    assert captured["url"].endswith("/users/1")
    assert "127.0.0.1:8900" in captured["url"]  # the tunneled twin port


def test_twin_request_unreachable_points_to_build_serve(project, cfg, monkeypatch):
    import httpx

    _seal(project, [Exchange(method="GET", path="/users/1", status=200, response_body="x")])

    def boom(*a, **k):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("hermes.tools.twin.httpx.request", boom)
    out = twin_request.fn({"path": "/users/2"}, _ctx(project, cfg))
    assert "could not reach the runtime twin" in out
    assert "build serve" in out


def test_twin_map_shows_routes_and_goal(project, cfg):
    _seal(project,
          [Exchange(method="GET", path="/users/1", status=200, response_body="a"),
           Exchange(method="GET", path="/users/2", status=200, response_body="b")],
          win_condition="parity on /users")
    out = twin_map.fn({}, _ctx(project, cfg))
    assert "parity on /users" in out
    assert "/users/{id}" in out


def test_twin_expand_grows_via_clone_layer(project, cfg, monkeypatch):
    _seal(project, [Exchange(method="GET", path="/users/1", status=200, response_body="a")])

    def fake_expand(twin, base_url, paths, **kw):
        from hermes.twin.model import Exchange as Ex
        twin.unseal()
        for p in paths:
            twin.add_exchange(Ex(method="GET", path=p, status=200,
                                 response_body="grown", source="expand"))
        twin.seal()
        return {"added": len(paths), "errors": 0}

    monkeypatch.setattr("hermes.twin.clone.expand", fake_expand)
    out = twin_expand.fn({"paths": ["/users/2"]}, _ctx(project, cfg))
    assert "learned 1" in out
    assert project.twin().respond("GET", "/users/2").response_body == "grown"


def test_twin_tools_register_only_when_sealed(project, cfg):
    yes = lambda *a, **k: True
    assert "twin_request" not in build_registry(project, cfg, yes).names()
    project.twin().init(source="https://api.example.com")  # open, not sealed
    assert "twin_request" not in build_registry(project, cfg, yes).names()
    _seal(project, [Exchange(method="GET", path="/", status=200, response_body="ok")])
    names = build_registry(project, cfg, yes).names()
    assert {"twin_request", "twin_map", "twin_stack"} <= set(names)


def test_sealed_build_has_no_live_reach_by_default(project, cfg):
    """Once sealed, `build_live_touch` defaults False: no way at all to reach
    the live target — not the general web tools, not the narrow twin ones."""
    yes = lambda *a, **k: True
    _seal(project, [Exchange(method="GET", path="/", status=200, response_body="ok")])
    names = build_registry(project, cfg, yes).names()
    assert "http_request" not in names
    assert "web_search" not in names
    assert "twin_expand" not in names
    assert "twin_reground" not in names


def test_build_live_touch_flag_restores_live_tools(project, cfg):
    yes = lambda *a, **k: True
    _seal(project, [Exchange(method="GET", path="/", status=200, response_body="ok")])
    cfg.set("build_live_touch", True)
    names = build_registry(project, cfg, yes).names()
    assert {"http_request", "web_search", "twin_expand", "twin_reground"} <= set(names)


def test_open_recon_phase_keeps_web_tools(project, cfg):
    """The OPEN recon/build phase ('pure scanning') is unaffected — it's the
    sealed build phase that gets cut off."""
    yes = lambda *a, **k: True
    project.twin().init(source="https://api.example.com")  # open, not sealed
    names = build_registry(project, cfg, yes).names()
    assert "http_request" in names


def test_build_mode_block_injected_when_sealed(project):
    assert package.build_mode_block(project) == ""  # no twin yet
    _seal(project, [Exchange(method="GET", path="/", status=200, response_body="ok")],
          mission="reimplement /users", win_condition="byte-match")
    block = package.build_mode_block(project)
    assert "RUNNING twin" in block
    assert "reimplement /users" in block
    assert "byte-match" in block


def test_build_mode_block_absent_until_sealed(project):
    project.twin().init(source="https://api.example.com")
    assert package.build_mode_block(project) == ""


def test_build_mode_reaches_system_prompt(project, cfg):
    _seal(project, [Exchange(method="GET", path="/", status=200, response_body="ok")],
          mission="reimplement /users", win_condition="byte-match")
    system = package.assemble(project, "go", {}, cfg)[0]["content"]
    assert "RUNNING twin" in system
    assert "reimplement /users" in system
