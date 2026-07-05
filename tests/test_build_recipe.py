from tests.conftest import FakeEndpoint

from hermes.tools import build_registry
from hermes.tools.base import ToolContext
from hermes.tools.builder import build_recipe, build_run


def _open_twin(project):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    return twin


# build_run execs inside the twin container via ctx.sandbox. The bring-up of a
# fresh container is: ensure_runtime probe(docker) · ps -a(not exists) · run -d ·
# exec mkdir · then the step exec. These helpers script that.
def _create_then(step_result):
    return [(0, "docker\n", ""), (0, "", ""), (0, "cid", ""), (0, "", ""), step_result]


def _ctx(project, cfg, sandbox):
    ctx = ToolContext(project=project, cfg=cfg, sandbox=sandbox)
    return ctx


def test_model_recipe_roundtrip(project):
    twin = _open_twin(project)
    twin.add_step("apt-get install -y nginx", note="web server")
    twin.add_step("systemctl start nginx")
    steps = twin.recipe()
    assert [s["cmd"] for s in steps] == ["apt-get install -y nginx", "systemctl start nginx"]
    assert steps[0]["note"] == "web server"


def test_build_run_records_successful_step(project, cfg):
    _open_twin(project)
    sb = FakeEndpoint(responses=_create_then((0, "unpacked", "")))
    out = build_run.fn({"command": "tar xzf wp.tgz", "note": "unpack"}, _ctx(project, cfg, sb))
    assert "recorded to recipe" in out
    assert project.twin().recipe()[0]["cmd"] == "tar xzf wp.tgz"
    # the step actually ran inside the container, with TWIN_PORT exported
    assert any("docker exec" in c and "TWIN_PORT" in c and "tar xzf wp.tgz" in c
               for c in sb.calls)


def test_build_run_does_not_record_failure(project, cfg):
    _open_twin(project)
    sb = FakeEndpoint(responses=_create_then((1, "", "No such file")))
    out = build_run.fn({"command": "make"}, _ctx(project, cfg, sb))
    assert "not recorded" in out
    assert project.twin().recipe() == []


def test_build_run_records_network_install_in_container(project, cfg):
    # The container has network, so installs/clones run INSIDE it and are captured
    # (no phone bounce — that was the GPU-box policy; the container is the sandbox).
    _open_twin(project)
    sb = FakeEndpoint(responses=_create_then((0, "Cloning into 'wp'...", "")))
    out = build_run.fn({"command": "git clone https://x/wp.git", "note": "pull app"},
                       _ctx(project, cfg, sb))
    assert "recorded to recipe" in out
    assert project.twin().recipe()[0]["cmd"] == "git clone https://x/wp.git"


def test_build_run_reuses_existing_container(project, cfg):
    _open_twin(project)
    # probe(docker) · ps -a(EXISTS) -> skip run/mkdir · step exec
    sb = FakeEndpoint(responses=[(0, "docker\n", ""),
                                 (0, "hermes-twin-testproj\n", ""),
                                 (0, "ok", "")])
    out = build_run.fn({"command": "echo hi"}, _ctx(project, cfg, sb))
    assert "recorded to recipe" in out
    assert not any("run -d" in c for c in sb.calls)  # didn't recreate the container


def test_build_run_needs_a_sandbox(project, cfg):
    _open_twin(project)
    out = build_run.fn({"command": "echo hi"}, _ctx(project, cfg, None))
    assert out.startswith("ERROR") and "sandbox" in out


def test_build_recipe_lists_steps(project, cfg):
    twin = _open_twin(project)
    twin.add_step("./configure", note="setup")
    twin.add_step("make install")
    out = build_recipe.fn({}, ToolContext(project=project, cfg=cfg))
    assert "2 step(s)" in out
    assert "./configure" in out and "# setup" in out
    assert "make install" in out


def test_build_recipe_empty(project, cfg):
    _open_twin(project)
    out = build_recipe.fn({}, ToolContext(project=project, cfg=cfg))
    assert "recipe empty" in out


def test_recipe_tools_register_while_open(project, cfg):
    yes = lambda *a, **k: True
    _open_twin(project)
    names = build_registry(project, cfg, yes).names()
    assert {"build_run", "build_recipe"} <= set(names)
