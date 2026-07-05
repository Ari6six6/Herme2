"""The build-mode antithesis: diff the solution against the twin, and reject a
PASS that has no executed evidence (anti-collusion — same weights, no free pass)."""

from tests.conftest import serve_reference_twin

from hermes import agent
from hermes.llm import MockBackend
from hermes.twin.model import Exchange

SANDBOX = object()  # truthy stand-in for an attached sandbox, like the verify tests


def _seal_twin(project):
    twin = project.twin()
    twin.init(source="https://api.example.com", mission="reimplement /ping",
              win_condition="GET /ping returns pong")
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200,
                               response_body="pong", content_type="text/plain"))
    twin.seal()


def _run(project, cfg, script):
    cfg.set("plan_build_tasks", False)  # planner is exercised in test_planner_referee
    with serve_reference_twin(project.twin_dir, cfg.get("twin_port", 8900)):
        return agent.run(project, "build /ping", cfg, MockBackend(script),
                         gpu=None, sandbox=SANDBOX, env={}, confirm_fn=lambda *a, **k: True)


def test_antithesis_passes_when_outputs_match(project, cfg):
    _seal_twin(project)
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},     # doer checks (passes build gate)
        {"tool": "finish_run", "args": {"summary": "ping returns pong"}},
        # antithesis: actually queries the twin, then rules
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "solution prints pong; twin /ping -> pong. They match. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "ping returns pong"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "antithesis"' in transcript


def test_antithesis_pass_without_evidence_is_rejected(project, cfg):
    _seal_twin(project)
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "trust me"}},
        # antithesis just agrees, runs nothing -> anti-collusion override to FAIL
        {"text": "Looks correct to me. VERDICT: PASS"},
        # bounced back; doer finishes again, this time the antithesis really checks
        {"tool": "finish_run", "args": {"summary": "verified for real"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "ran it: pong, twin: pong. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "verified for real"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "VERDICT PASS rejected" in transcript  # the collusion guard fired


def test_antithesis_breaks_divergent_solution_then_doer_fixes(project, cfg):
    _seal_twin(project)
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/app.py", "content": "print('nope')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "done (wrong)"}},
        # antithesis runs the twin, finds divergence
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "solution prints nope but twin /ping -> pong. VERDICT: FAIL"},
        # bounced -> doer fixes and re-finishes
        {"tool": "edit_file", "args": {"path": "workspace/app.py", "old": "nope", "new": "pong"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "fixed: returns pong"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "now prints pong == twin pong. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "fixed: returns pong"
    assert (project.workspace_dir / "app.py").read_text() == "print('pong')"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "did NOT pass" in transcript  # the FAIL was fed back to the doer


def test_antithesis_pass_after_only_a_read_is_rejected(project, cfg):
    # A passive read is not executed evidence: the critic must run the solution
    # or query the twin. A VERDICT: PASS after only read_file is collusion
    # theater and must be rejected, exactly like a no-tool text PASS.
    _seal_twin(project)
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "trust me"}},
        # antithesis only READS the solution, runs/queries nothing, then rules PASS
        {"tool": "read_file", "args": {"path": "workspace/app.py"}},
        {"text": "I read it, looks right. VERDICT: PASS"},
        # bounced; doer re-finishes and this time the antithesis really queries
        {"tool": "finish_run", "args": {"summary": "verified for real"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"text": "ran it: pong, twin: pong. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "verified for real"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "VERDICT PASS rejected" in transcript  # the collusion guard fired


def test_non_build_verify_still_passes_on_text_verdict(project, cfg):
    # Outside build mode (no sealed twin), the anti-collusion evidence rule does
    # NOT apply — keep the existing verifier behavior (a text PASS is accepted).
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/m.py", "content": "print(2+2)"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
        {"text": "Ran python m.py, output 4. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "verifier"' in transcript  # plain verifier, not antithesis
