"""Feature 7: verification enforcement."""

from hermes import agent, package
from hermes.llm import MockBackend


def _run(project, cfg, script, gpu=None):
    cfg.set("plan_build_tasks", False)
    cfg.set("verify_code_runs", False)  # isolate from the sandbox verifier
    return agent.run(project, "do it", cfg, MockBackend(script),
                     gpu=gpu, env={}, confirm_fn=lambda *a, **k: True)


def test_header_rule_present_only_when_enabled(project, cfg):
    cfg.set("verify_before_done", False)
    off = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "Verification rule" not in off
    cfg.set("verify_before_done", True)
    on = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "Verification rule" in on
    assert "EXECUTED a verification step" in on


def test_finish_bounced_when_files_changed_but_nothing_run(project, cfg):
    cfg.set("verify_before_done", True)
    result = _run(
        project, cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "print(1)"}},
            {"tool": "finish_run", "args": {"summary": "wrote it, should work"}},
            # bounced -> now it actually runs something, then finishes
            {"tool": "local_shell", "args": {"command": "python workspace/app.py"}},
            {"tool": "finish_run", "args": {"summary": "ran it, printed 1"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "ran it, printed 1"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "never actually ran anything" in transcript  # the nudge landed


def test_no_bounce_when_something_was_executed(project, cfg):
    cfg.set("verify_before_done", True)
    result = _run(
        project, cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "print(1)"}},
            {"tool": "local_shell", "args": {"command": "python workspace/app.py"}},
            {"tool": "finish_run", "args": {"summary": "ran it"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "ran it"
    assert result.turns == 3  # not bounced


def test_bounce_is_one_shot(project, cfg):
    # If the agent insists on finishing without running (e.g. a pure edit task),
    # the single bounce is spent and prose is accepted — no infinite loop.
    cfg.set("verify_before_done", True)
    result = _run(
        project, cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/a.txt", "content": "notes"}},
            {"tool": "finish_run", "args": {"summary": "first"}},
            {"tool": "finish_run", "args": {"summary": "second, still not running"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "second, still not running"


def test_no_bounce_for_non_mutating_run(project, cfg):
    cfg.set("verify_before_done", True)
    result = _run(
        project, cfg,
        [
            {"tool": "write_note", "args": {"text": "checked, fine"}},
            {"tool": "finish_run", "args": {"summary": "looked at it"}},
        ],
    )
    assert not result.aborted
    assert result.turns == 2  # a read/note-only run isn't forced to execute


def test_no_enforcement_when_flag_off(project, cfg):
    cfg.set("verify_before_done", False)
    result = _run(
        project, cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "print(1)"}},
            {"tool": "finish_run", "args": {"summary": "done, not run"}},
        ],
    )
    assert result.summary == "done, not run"
    assert result.turns == 2  # no enforcement when the flag is off
