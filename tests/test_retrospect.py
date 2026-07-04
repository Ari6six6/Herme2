"""Feature 9 — Retrospection: per-run metrics recording (harness ground truth)
and the cross-run self-review pass that banks lessons as notes/skills."""

import json

from hermes import agent, retrospect
from hermes.llm import LLMTransportError, MockBackend


def run_agent(project, cfg, script, **kw):
    backend = MockBackend(script)
    return agent.run(
        project, "do the thing", cfg, backend,
        env={}, confirm_fn=lambda *a, **k: True, **kw,
    )


def read_metrics(project, run_id: int) -> dict:
    path = project.runs_dir / f"{run_id:04d}" / "metrics.json"
    return json.loads(path.read_text())


def fake_run(project, run_id, aborted=False, tool_errors=0, summary="Did: things"):
    """Fabricate a completed run on disk: metrics.json + summary.md."""
    run_dir = project.runs_dir / f"{run_id:04d}"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "metrics.json").write_text(json.dumps({
        "run": run_id, "turns": 5, "aborted": aborted,
        "tool_calls": 4, "tool_errors": tool_errors,
        "stall_nudges": 0, "phantom_bounces": 0,
        "verify_bounces": 0, "verify_failures": 0, "tainted_turns": 0,
        "tools": ["write_file"],
    }, indent=2))
    (run_dir / "summary.md").write_text(summary + "\n")


# -- metrics recording -------------------------------------------------------

def test_metrics_written_on_happy_path(project, cfg):
    result = run_agent(
        project, cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote out.txt"}},
        ],
    )
    assert not result.aborted
    m = read_metrics(project, 1)
    assert m["run"] == 1
    assert m["turns"] == result.turns
    assert m["aborted"] is False
    assert m["tool_calls"] == 2
    assert m["tool_errors"] == 0
    assert m["stall_nudges"] == 0
    assert "write_file" in m["tools"]


def test_metrics_count_errors_and_stalls(project, cfg):
    run_agent(
        project, cfg,
        [
            {"text": "I should call a tool."},  # prose only -> stall nudge
            {"tool": "no_such_tool", "args": {}},  # -> ERROR
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    m = read_metrics(project, 1)
    assert m["stall_nudges"] == 1
    assert m["tool_errors"] == 1


def test_metrics_written_on_aborted_run(project, cfg):
    cfg.set("max_turns", 2)
    result = run_agent(
        project, cfg,
        [
            {"tool": "write_note", "args": {"text": "a"}},
            {"tool": "write_note", "args": {"text": "b"}},
            {"tool": "write_note", "args": {"text": "c"}},
        ],
    )
    assert result.aborted
    m = read_metrics(project, 1)
    assert m["aborted"] is True
    assert m["turns"] == 2


def test_metrics_count_phantom_bounce(project, cfg):
    code_reply = "Here:\n\n```python\nprint('hi')\n```"
    run_agent(
        project, cfg,
        [
            {"tool": "finish_run", "args": {"summary": "wrote it"},
             "say": code_reply},
            {"tool": "write_file",
             "args": {"path": "workspace/x.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote x.py"}},
        ],
    )
    m = read_metrics(project, 1)
    assert m["phantom_bounces"] == 1


def test_recent_metrics_oldest_first_and_skips_gaps(project):
    fake_run(project, 1)
    # run 2 exists but predates metrics (no metrics.json)
    (project.runs_dir / "0002").mkdir()
    fake_run(project, 3)
    # run 4 has a corrupt metrics file
    (project.runs_dir / "0004").mkdir()
    (project.runs_dir / "0004" / "metrics.json").write_text("{not json")
    rows = project.recent_metrics(10)
    assert [m["run"] for m in rows] == [1, 3]


# -- the pass ----------------------------------------------------------------

def test_retrospect_needs_two_measured_runs(project, cfg):
    fake_run(project, 1)
    # backend=None: with <2 measured runs the pass must bail before any chat
    assert retrospect.retrospect(project, None, cfg) is False


def test_retrospect_banks_a_note(project, cfg):
    fake_run(project, 1, tool_errors=3)
    fake_run(project, 2, tool_errors=2)
    backend = MockBackend([
        {"tool": "write_note",
         "args": {"text": "remote_shell keeps failing on cold boxes — wait for ssh"}},
        {"text": "nothing else worth changing"},
    ])
    assert retrospect.retrospect(project, backend, cfg) is True
    assert "cold boxes" in project.read_notes()


def test_retrospect_registry_is_narrow(cfg):
    names = retrospect.build_registry(cfg).names()
    assert names == ["write_note"]  # skills off by default
    cfg.set("skills_enabled", True)
    names = retrospect.build_registry(cfg).names()
    assert names == ["load_skill", "write_note", "write_skill"]


def test_retrospect_writes_skill_when_skills_enabled(project, cfg, home):
    cfg.set("skills_enabled", True)
    fake_run(project, 1)
    fake_run(project, 2)
    backend = MockBackend([
        {"tool": "write_skill",
         "args": {"name": "gpu-warmup",
                  "content": "Wait for sshd before remote_shell.\nSteps: ..."}},
        {"text": "done"},
    ])
    assert retrospect.retrospect(project, backend, cfg) is True
    from hermes import skills as skills_mod
    assert skills_mod.get(project, "gpu-warmup") is not None


def test_retrospect_skill_tools_absent_when_skills_off(project, cfg):
    fake_run(project, 1)
    fake_run(project, 2)
    backend = MockBackend([
        {"tool": "write_skill",
         "args": {"name": "x", "content": "desc\nbody"}},
        {"text": "done"},
    ])
    # write_skill isn't registered -> ERROR -> nothing banked
    assert retrospect.retrospect(project, backend, cfg) is False
    assert not (project.skills_dir / "x.md").exists()


def test_retrospect_intercepts_finish_run(project, cfg):
    fake_run(project, 1)
    fake_run(project, 2)
    backend = MockBackend([
        {"tool": "finish_run", "args": {"summary": "hijack"}},
        {"text": "ok, stopping"},
    ])
    assert retrospect.retrospect(project, backend, cfg) is False
    # no run summary was created/overwritten by the pass
    assert (project.runs_dir / "0002" / "summary.md").read_text().strip() == \
        "Did: things"


def test_retrospect_turn_budget_bounds_the_pass(project, cfg):
    cfg.set("retrospect_max_turns", 2)
    fake_run(project, 1)
    fake_run(project, 2)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(10)]
    backend = MockBackend(script)
    assert retrospect.retrospect(project, backend, cfg) is True
    assert len(backend.script) == 8  # only 2 turns consumed


def test_retrospect_transport_error_is_noop(project, cfg):
    fake_run(project, 1)
    fake_run(project, 2)

    class DeadBackend:
        def chat(self, *a, **k):
            raise LLMTransportError("down")

    notes_before = project.read_notes()
    assert retrospect.retrospect(project, DeadBackend(), cfg) is False
    assert project.read_notes() == notes_before


def test_prompt_carries_the_measured_numbers(project, cfg):
    fake_run(project, 1, aborted=True, tool_errors=7)
    block = retrospect.metrics_block(project, 10)
    assert "run 0001" in block
    assert "aborted=yes" in block
    assert "tool_errors=7" in block


# -- the trigger -------------------------------------------------------------

def test_maybe_retrospect_stateless_modulo(project, cfg):
    fake_run(project, 1)
    fake_run(project, 2)
    cfg.set("retrospect_every_runs", 3)
    # not due: backend must never be reached (None would blow up if it were)
    assert retrospect.maybe_retrospect(project, None, cfg, run_id=2) is False
    # due: pass runs and banks
    backend = MockBackend([
        {"tool": "write_note", "args": {"text": "lesson"}},
        {"text": "done"},
    ])
    assert retrospect.maybe_retrospect(project, backend, cfg, run_id=3) is True


def test_agent_run_triggers_retrospection_end_to_end(project, cfg):
    cfg.set("retrospect_enabled", True)
    cfg.set("retrospect_every_runs", 1)
    # run 1: only one measured run afterwards -> pass bails quietly
    run_agent(project, cfg, [{"tool": "finish_run", "args": {"summary": "Did: a"}}])
    assert "lesson" not in project.read_notes()
    # run 2: two measured runs -> the pass fires and banks a note; the extra
    # script items are consumed by the retrospection turns after finish_run.
    run_agent(
        project, cfg,
        [
            {"tool": "finish_run", "args": {"summary": "Did: b"}},
            {"tool": "write_note", "args": {"text": "lesson: verify ssh first"}},
            {"text": "nothing else"},
        ],
    )
    assert "lesson: verify ssh first" in project.read_notes()
    transcript = (project.runs_dir / "0002" / "transcript.jsonl").read_text()
    assert "retrospect" in transcript


def test_retrospection_off_by_default(project, cfg):
    # leftover script items would be consumed if a pass fired
    backend = MockBackend([
        {"tool": "finish_run", "args": {"summary": "Did: a"}},
        {"tool": "write_note", "args": {"text": "should never land"}},
    ])
    agent.run(project, "go", cfg, backend, env={},
              confirm_fn=lambda *a, **k: True)
    assert "should never land" not in project.read_notes()
