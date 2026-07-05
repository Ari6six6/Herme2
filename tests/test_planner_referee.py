"""The build-mode planner (decompose before building) and referee (break a
builder/antithesis deadlock). Both are on by default in build mode and gated by
config. Driven against the scripted mock backend."""

from tests.conftest import serve_reference_twin

from hermes import agent
from hermes.llm import MockBackend
from hermes.twin.model import Exchange

SANDBOX = object()  # truthy stand-in for an attached sandbox


def _seal(project):
    twin = project.twin()
    twin.init(source="https://api.example.com", mission="reimplement /ping",
              win_condition="GET /ping returns pong")
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200,
                               response_body="pong", content_type="text/plain"))
    twin.seal()


def _run(project, cfg, script, gpu=None):
    with serve_reference_twin(project.twin_dir, cfg.get("twin_port", 8900)):
        return agent.run(project, "make /ping return pong", cfg, MockBackend(script),
                         gpu=gpu, env={}, confirm_fn=lambda *a, **k: True)


def _transcript(project, run="0001"):
    return (project.runs_dir / run / "transcript.jsonl").read_text()


# ---- planner ------------------------------------------------------------

def test_planner_runs_in_build_mode_and_logs_the_plan(project, cfg):
    _seal(project)
    result = _run(project, cfg, [
        {"text": "1. write app.py printing pong\n2. GET /ping == twin\n"
                 "DONE WHEN: /ping returns pong"},  # the planner pass
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ], gpu=None)
    assert not result.aborted
    tx = _transcript(project)
    assert '"role": "planner"' in tx
    assert "DONE WHEN" in tx  # the plan the planner produced was recorded


def test_planner_off_by_config(project, cfg):
    _seal(project)
    cfg.set("plan_build_tasks", False)
    result = _run(project, cfg, [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ], gpu=None)
    assert not result.aborted
    assert '"role": "planner"' not in _transcript(project)


def test_planner_does_not_run_without_a_sealed_twin(project, cfg):
    # No twin -> not build mode -> the planner stays out even though it's enabled.
    assert cfg.get("plan_build_tasks", True)
    result = _run(project, cfg, [
        {"tool": "write_file", "args": {"path": "workspace/x.py", "content": "1"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ], gpu=None)
    assert not result.aborted
    assert '"role": "planner"' not in _transcript(project)


# ---- referee ------------------------------------------------------------

def test_referee_overturns_antithesis_on_deadlock(project, cfg):
    # Two antithesis FAILs spend the verify rounds; the referee investigates and
    # overrules in the solution's favour, so the finish in flight is accepted.
    _seal(project)
    cfg.set("plan_build_tasks", False)
    result = _run(project, cfg, [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "v1"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 1
        {"text": "i think it mismatches. VERDICT: FAIL"},
        {"tool": "finish_run", "args": {"summary": "v2 still right"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 2
        {"text": "still no. VERDICT: FAIL"},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # referee investigates
        {"text": "i ran both: pong == pong; antithesis was wrong. VERDICT: PASS"},
    ], gpu=SANDBOX)
    assert not result.aborted
    assert result.summary == "v2 still right"
    assert '"role": "referee"' in _transcript(project)


def test_referee_upholds_antithesis_then_doer_fixes(project, cfg):
    _seal(project)
    cfg.set("plan_build_tasks", False)
    result = _run(project, cfg, [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('nope')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "v1"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 1
        {"text": "nope != pong. VERDICT: FAIL"},
        {"tool": "finish_run", "args": {"summary": "v2 insist"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 2
        {"text": "still nope. VERDICT: FAIL"},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # referee investigates
        {"text": "ran it: nope != pong; antithesis right. VERDICT: FAIL"},
        {"tool": "finish_run", "args": {"summary": "v3 fixed for real"}},  # accepted
    ], gpu=SANDBOX)
    assert not result.aborted
    assert result.summary == "v3 fixed for real"
    assert "A REFEREE was brought in" in _transcript(project)  # the referee nudge


def test_referee_off_accepts_after_rounds_spent(project, cfg):
    _seal(project)
    cfg.set("plan_build_tasks", False)
    cfg.set("referee_on_deadlock", False)
    result = _run(project, cfg, [
        {"tool": "write_file",
         "args": {"path": "workspace/app.py", "content": "print('pong')"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},
        {"tool": "finish_run", "args": {"summary": "v1"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 1
        {"text": "VERDICT: FAIL"},
        {"tool": "finish_run", "args": {"summary": "v2"}},
        {"tool": "twin_request", "args": {"path": "/ping"}},      # antithesis round 2
        {"text": "VERDICT: FAIL"},
        {"tool": "finish_run", "args": {"summary": "v3 accepted unverified"}},
    ], gpu=SANDBOX)
    assert not result.aborted
    assert result.summary == "v3 accepted unverified"
    assert '"role": "referee"' not in _transcript(project)
