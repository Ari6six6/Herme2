from hermes import package
from hermes.tools import build_registry
from hermes.tools.base import ToolContext
from hermes.tools.builder import twin_clone, twin_record, twin_seal
from hermes.twin.model import Exchange


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg)


def _open_twin(project, source="https://api.example.com"):
    twin = project.twin()
    twin.init(source=source)
    return twin


def test_twin_record_adds_sample(project, cfg):
    _open_twin(project)
    out = twin_record.fn(
        {"path": "/users/1", "status": 200, "response_body": '{"id":1}',
         "content_type": "application/json"},
        _ctx(project, cfg))
    assert "twin now has 1 sample" in out
    assert project.twin().respond("GET", "/users/1").response_body == '{"id":1}'


def test_twin_record_refuses_when_sealed(project, cfg):
    twin = _open_twin(project)
    twin.add_exchange(Exchange(method="GET", path="/", status=200, response_body="x"))
    twin.seal()
    out = twin_record.fn({"path": "/a", "status": 200, "response_body": "y"}, _ctx(project, cfg))
    assert out.startswith("ERROR") and "sealed" in out


def test_twin_seal_requires_samples(project, cfg):
    _open_twin(project)
    out = twin_seal.fn({}, _ctx(project, cfg))
    assert out.startswith("ERROR")
    assert not project.twin().is_sealed()


def test_twin_seal_freezes_and_opens_build_phase(project, cfg):
    twin = _open_twin(project)
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200, response_body="pong"))
    out = twin_seal.fn({}, _ctx(project, cfg))
    assert "sealed" in out and "build phase" in out
    assert project.twin().is_sealed()


def test_twin_clone_tool_seeds_open(project, cfg, monkeypatch):
    from hermes.twin import clone as clone_mod
    monkeypatch.setattr(clone_mod.time, "sleep", lambda *_: None)
    monkeypatch.setattr(clone_mod, "_httpx_fetch",
                        lambda m, u, h=None, b=None, t=45: (200, {"content-type": "text/html"}, "hi"))
    _open_twin(project)
    out = twin_clone.fn({"seeds": ["/api"]}, _ctx(project, cfg))
    assert "twin now has" in out
    assert not project.twin().is_sealed()  # stays open after a tool clone


def test_builder_tools_register_only_while_open(project, cfg):
    yes = lambda *a, **k: True
    assert "twin_seal" not in build_registry(project, cfg, yes).names()
    twin = _open_twin(project)
    names = build_registry(project, cfg, yes).names()
    # builder tools present while open; build-phase twin tools absent. (Target
    # recon lives in the external herme-recon program, not as a built-in tool.)
    assert {"twin_record", "twin_clone", "twin_seal"} <= set(names)
    assert not any(n.startswith("recon_") for n in names)
    assert "twin_request" not in names
    twin.add_exchange(Exchange(method="GET", path="/", status=200, response_body="x"))
    twin.seal()
    names = build_registry(project, cfg, yes).names()
    assert "twin_seal" not in names         # builder tools gone after seal
    assert "twin_request" in names          # build-phase tools now present


def test_recon_build_block_injected_while_open(project, cfg):
    assert package.recon_build_block(project) == ""  # no twin
    twin = _open_twin(project)
    twin.add_exchange(Exchange(method="GET", path="/", status=200, response_body="x"))
    block = package.recon_build_block(project)
    assert "BECOME the webserver" in block
    assert "https://api.example.com" in block
    # reaches the system prompt, and is the reconstruction framing (not build mode)
    system = package.assemble(project, "go", {}, cfg)[0]["content"]
    assert "Reconstruction — make the twin" in system
    # once sealed, the block flips to build mode
    twin.seal()
    assert package.recon_build_block(project) == ""
    assert "RUNNING twin" in package.assemble(project, "go", {}, cfg)[0]["content"]
