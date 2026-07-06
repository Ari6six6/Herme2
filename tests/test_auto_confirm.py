"""Unattended mode: `auto_confirm` approves the y/n gate so a run left alone
doesn't stall forever on a prompt nobody is there to answer."""

import hermes.confirm as confirm_mod
from hermes import agent
from hermes.llm import MockBackend


def test_auto_confirm_bypasses_the_gate_without_prompting(project, cfg, monkeypatch):
    # If the interactive confirm is ever reached under auto_confirm, that's the
    # bug — it would block on input(). Make calling it a hard failure.
    def _boom(*a, **k):
        raise AssertionError("interactive confirm was reached under auto_confirm")

    monkeypatch.setattr(confirm_mod, "confirm", _boom)
    cfg.set("auto_confirm", True)

    # No confirm_fn passed → agent.run would default to the interactive one;
    # auto_confirm must replace it. local_shell is a gated action.
    r = agent.run(
        project, "do it", cfg,
        MockBackend([
            {"tool": "local_shell", "args": {"command": "echo hi"}},
            {"tool": "finish_run", "args": {"summary": "ran unattended"}},
        ]),
        gpu=None, sandbox=None, env={},
    )
    assert not r.aborted
    assert r.summary == "ran unattended"


def test_gate_still_denies_when_auto_confirm_off(project, cfg):
    # Default: the gate holds. A confirm_fn that says no makes local_shell DENIED.
    r = agent.run(
        project, "do it", cfg,
        MockBackend([
            {"tool": "local_shell", "args": {"command": "echo hi"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        gpu=None, sandbox=None, env={},
        confirm_fn=lambda *a, **k: False,
    )
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "DENIED by operator" in transcript
