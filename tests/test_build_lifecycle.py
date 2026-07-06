"""End-to-end: walk a build project through both phases and the seal between them,
proving the registry/prompt/gates compose across the boundary."""

from tests.conftest import serve_reference_twin

from hermes import agent, package
from hermes.llm import MockBackend

SANDBOX = object()
yes = lambda *a, **k: True


def _run(project, cfg, prompt, script, sandbox):
    cfg.set("plan_build_tasks", False)  # planner is exercised in test_planner_referee
    with serve_reference_twin(project.twin_dir, cfg.get("twin_port", 8900)):
        return agent.run(project, prompt, cfg, MockBackend(script), gpu=None, sandbox=sandbox, env={},
                         confirm_fn=yes)


def test_attach_target_makes_any_project_build_capable(project, cfg, monkeypatch):
    # A plain `project new` project starts with no twin. `_attach_target` (behind
    # `build init <url>` / `run build <url>`) points it at a target, so build/twin
    # work is no longer locked to projects created with `project build`.
    from hermes import cli

    # Tests have no network — stub the clone/fingerprint step.
    monkeypatch.setattr(cli, "_clone_target",
                        lambda cfg, twin, url, seal=False: {"exchanges": 2})
    assert not project.twin().exists()

    report = cli._attach_target(cfg, project, "https://api.example.com")

    twin = project.twin()
    assert twin.exists()                          # now a build-capable project
    assert twin.source == "https://api.example.com"
    assert report["exchanges"] == 2
    # The file-moving tools the builder needs were equipped up front.
    assert {"download_file", "transfer"} <= set(project.equipped_tools())


def test_recon_to_build_full_lifecycle(project, cfg):
    twin = project.twin()
    twin.init(source="https://api.example.com", mode="url",
              mission="make /ping return pong", win_condition="GET /ping -> pong")

    # While OPEN the system prompt is the recon/build brief, and builder tools exist.
    assert "BECOME the webserver" in package.assemble(project, "x", {}, cfg)[0]["content"]

    # --- Phase 1: recon/builder records a real sample and seals the twin ---
    r1 = _run(project, cfg, "build the twin", [
        {"tool": "twin_record",
         "args": {"path": "/ping", "status": 200, "response_body": "pong",
                  "content_type": "text/plain"}},
        {"tool": "twin_seal", "args": {}},
        {"tool": "finish_run", "args": {"summary": "twin sealed: /ping -> pong"}},
    ], sandbox=None)
    assert not r1.aborted
    assert project.twin().is_sealed()

    # The seal flips the phase: prompt becomes build mode, build tools appear.
    assert "RUNNING twin" in package.assemble(project, "x", {}, cfg)[0]["content"]

    # --- Phase 2: thesis builds, checks the twin, antithesis verifies ---
    r2 = _run(project, cfg, "make /ping return pong", [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},   # satisfies build-proof gate
        {"tool": "finish_run", "args": {"summary": "/ping returns pong"}},
        # antithesis runs the twin and confirms parity with executed evidence
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "solution prints pong; twin /ping -> pong. match. VERDICT: PASS"},
    ], sandbox=SANDBOX)
    assert not r2.aborted
    assert r2.summary == "/ping returns pong"
    transcript = (project.runs_dir / "0002" / "transcript.jsonl").read_text()
    assert '"role": "antithesis"' in transcript


def test_build_phase_rejects_unproven_claim_end_to_end(project, cfg):
    # A thesis that writes code and tries to bail without checking the twin is
    # stopped by the build-proof gate, even before the antithesis would run.
    twin = project.twin()
    twin.init(source="https://api.example.com", mode="url")
    from hermes.twin.model import Exchange
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200, response_body="pong"))
    twin.seal()

    r = _run(project, cfg, "do it", [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('nope')"}},
        {"tool": "finish_run", "args": {"summary": "trust me bro"}},   # bounced
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "checked it"}},
    ], sandbox=None)  # no sandbox -> isolate the build-proof gate from the antithesis
    assert not r.aborted
    assert r.summary == "checked it"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "you do not get to make" in transcript  # build-proof nudge fired
