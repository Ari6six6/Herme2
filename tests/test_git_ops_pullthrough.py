"""Proof that the git_ops capability closes the self-improvement loop.

A natural prompt, with the skills system on and the shipped `git_ops` seed
skill in place, drives: load_skill -> equip_tool -> git_ops runs -> finish_run.
The transcript and metrics.json are the evidence — the same artifacts the
operator (and the retrospection pass) read as ground truth.

This is a *wiring* proof (a scripted MockBackend): it shows the harness routes
the skill+tool pull-through correctly, not that a live model would choose it.
"""

import json
from pathlib import Path

from hermes import agent
from hermes.llm import MockBackend

SEED_SKILL = Path(__file__).resolve().parents[1] / "skills" / "git_ops.md"


def _install_seed_skill(project):
    project.skills_dir.mkdir(parents=True, exist_ok=True)
    (project.skills_dir / "git_ops.md").write_text(SEED_SKILL.read_text())


def test_git_ops_skill_pulls_through_to_a_commit(project, cfg):
    cfg.set("skills_enabled", True)  # index the skill + register load_skill
    _install_seed_skill(project)
    # Something in the workspace to actually commit.
    (project.workspace_dir / "notes.txt").write_text("first pass\n")

    backend = MockBackend([
        {"tool": "load_skill", "args": {"name": "git_ops"}},
        {"tool": "equip_tool", "args": {"name": "git_ops"}},
        {"tool": "git_ops", "args": {"operation": "init"}},
        {"tool": "git_ops", "args": {"operation": "add"}},
        {"tool": "git_ops",
         "args": {"operation": "commit", "message": "snapshot the workspace"}},
        {"tool": "finish_run",
         "args": {"summary": "Did: initialised git and committed the workspace."}},
    ])
    result = agent.run(
        project,
        "start tracking my work in git and commit what's there now",
        cfg, backend, gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )

    assert not result.aborted
    assert "committed the workspace" in result.summary

    # The tool really ran: a git repo with our commit now exists in the workspace.
    assert (project.workspace_dir / ".git").is_dir()

    run_dir = project.runs_dir / "0001"
    transcript = run_dir.read_text() if run_dir.is_file() else \
        (run_dir / "transcript.jsonl").read_text()
    # The pull-through, in order: loaded the skill, equipped, ran the tool.
    assert "load_skill" in transcript
    assert "equip_tool" in transcript
    assert "git_ops" in transcript
    assert "git commit ok" in transcript  # the real tool result, echoed into the log

    # metrics.json — harness ground truth — records a clean run that used git_ops.
    metrics = json.loads((run_dir / "metrics.json").read_text())
    assert metrics["aborted"] is False
    assert metrics["tool_errors"] == 0
    assert "git_ops" in metrics["tools"]
    assert "load_skill" in metrics["tools"]
    assert "equip_tool" in metrics["tools"]


def test_denied_commit_does_not_abort_the_run(project, cfg):
    # If the operator declines the commit, the tool feeds back a DENIED string;
    # the run continues and finishes cleanly (no crash, no abort).
    cfg.set("skills_enabled", True)
    _install_seed_skill(project)
    (project.workspace_dir / "notes.txt").write_text("x\n")

    calls = {"n": 0}

    def confirm(*a, **k):
        calls["n"] += 1
        return False  # decline every gated git op

    backend = MockBackend([
        {"tool": "equip_tool", "args": {"name": "git_ops"}},
        {"tool": "git_ops", "args": {"operation": "init"}},
        {"tool": "finish_run", "args": {"summary": "operator declined git init"}},
    ])
    result = agent.run(
        project, "set up git", cfg, backend, gpu=None, env={}, confirm_fn=confirm,
    )
    assert not result.aborted
    assert calls["n"] >= 1
    assert not (project.workspace_dir / ".git").exists()
