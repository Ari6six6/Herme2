from tests.conftest import FakeEndpoint

from hermes.twin import deploy
from hermes.twin.model import Exchange


def _twin_with_recipe(project):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    twin.add_step("apt-get install -y nginx", "install nginx")
    twin.add_step("nohup nginx -g 'daemon off;' &", "serve on 0.0.0.0:$TWIN_PORT")
    twin.add_exchange(Exchange(method="GET", path="/", status=200, response_body="hi"))
    twin.seal()
    return twin


def _twin_no_recipe(project):
    twin = project.twin()
    twin.init(source="https://api.example.com")
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200, response_body="pong"))
    twin.seal()
    return twin


def test_deploy_boots_container_and_replays_recipe(project):
    twin = _twin_with_recipe(project)
    # ps-a(not exists), rm -f, run -d, exec mkdir, step1, step2, sleep, healthcheck(up)
    ep = FakeEndpoint(responses=[
        (0, "", ""), (0, "", ""), (0, "cid", ""), (0, "", ""),
        (0, "", ""), (0, "", ""), (0, "", ""), (0, "", ""),
    ])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert report["ok"] and report["source"] == "container"
    assert report["container"] == "hermes-twin-testproj"
    assert any("docker run -d" in c and "-p 127.0.0.1:8900:8900" in c for c in ep.calls)
    assert any("docker exec" in c and "nginx" in c for c in ep.calls)
    assert any("TWIN_PORT=8900" in c for c in ep.calls)


def test_deploy_short_circuits_when_already_up(project):
    twin = _twin_with_recipe(project)
    # ps-a(exists), healthcheck(up) -> already up, no rebuild
    ep = FakeEndpoint(responses=[(0, "hermes-twin-testproj\n", ""), (0, "", "")])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert report["ok"] and "already up" in report["log"]
    assert not any("docker run -d" in c for c in ep.calls)


def test_deploy_fails_on_bad_recipe_step(project):
    twin = _twin_with_recipe(project)
    # ps-a(no), rm, run -d(ok), exec mkdir(ok), step1 fails
    ep = FakeEndpoint(responses=[
        (0, "", ""), (0, "", ""), (0, "cid", ""), (0, "", ""),
        (1, "", "package not found"),
    ])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert not report["ok"]
    assert "recipe step 1" in report["error"]
    log = deploy.serve_log_path(twin).read_text()
    assert "package not found" in log and "apt-get install -y nginx" in log


def test_deploy_fails_when_container_wont_start(project):
    twin = _twin_with_recipe(project)
    # ps-a(no), rm, run -d FAILS
    ep = FakeEndpoint(responses=[(0, "", ""), (0, "", ""), (1, "", "no such image")])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert not report["ok"]
    assert "could not start the container" in report["error"]


def test_deploy_clean_skips_precheck_and_rebuilds(project):
    twin = _twin_with_recipe(project)
    # clean: rm, run -d, exec mkdir, step1, step2, sleep, healthcheck(up)
    ep = FakeEndpoint(responses=[(0, "", "")] * 6 + [(0, "", "")])
    report = deploy.deploy(ep, twin, 8900, runtime="docker", clean=True)
    assert report["ok"] and report["source"] == "container"
    assert any("docker rm -f" in c for c in ep.calls)
    assert any("docker run -d" in c for c in ep.calls)
    # no precheck ps -a on the clean path
    assert not any("ps -a" in c for c in ep.calls)


def test_deploy_reports_when_nothing_listens(project):
    twin = _twin_with_recipe(project)
    # everything ok but final healthcheck reports down
    ep = FakeEndpoint(responses=[
        (0, "", ""), (0, "", ""), (0, "cid", ""), (0, "", ""),
        (0, "", ""), (0, "", ""), (0, "", ""), (1, "", ""),
    ])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert not report["ok"]
    assert "nothing is listening" in report["error"]
    assert "0.0.0.0:$TWIN_PORT" in report["error"]  # the fix hint


def test_deploy_no_recipe_refuses_with_guidance(project):
    twin = _twin_no_recipe(project)
    ep = FakeEndpoint()
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert not report["ok"] and report["source"] == "container"
    assert "no reconstruction recipe" in report["error"]
    assert "run build" in report["error"]
    # never a recorded-response fallback
    assert not any("docker run" in c for c in ep.calls)


def test_deploy_installs_runtime_when_not_passed(project):
    twin = _twin_with_recipe(project)
    # ensure_runtime probe(docker present), then the normal boot sequence
    ep = FakeEndpoint(responses=[
        (0, "docker\n", ""),                                    # probe_container_runtime
        (0, "", ""), (0, "", ""), (0, "cid", ""), (0, "", ""),  # ps-a, rm, run, mkdir
        (0, "", ""), (0, "", ""), (0, "", ""), (0, "", ""),     # step1, step2, sleep, health
    ])
    report = deploy.deploy(ep, twin, 8900)
    assert report["ok"]


def test_serve_log_written_on_success(project):
    twin = _twin_with_recipe(project)
    ep = FakeEndpoint(responses=[
        (0, "", ""), (0, "", ""), (0, "cid", ""), (0, "", ""),
        (0, "", ""), (0, "", ""), (0, "", ""), (0, "", ""),
    ])
    report = deploy.deploy(ep, twin, 8900, runtime="docker")
    assert report["ok"]
    log = deploy.serve_log_path(twin).read_text()
    assert "health check :8900 -> listening" in log


def test_stop_removes_named_container(project):
    twin = _twin_with_recipe(project)
    ep = FakeEndpoint()
    deploy.stop(ep, twin)
    assert any("docker rm -f" in c and "hermes-twin-testproj" in c for c in ep.calls)
