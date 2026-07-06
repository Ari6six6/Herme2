"""Recursive self-improvement is held to the same loops as ordinary code.

A self-edit (write/edit_hermes_source) is the highest-stakes write in the system
— the agent changing the harness that gates it — so it must not slip past the
guards that grade project code. These tests pin the wiring: phantom-finish and
verify-before-done reach self-edits, and an independent pass re-runs Hermes' OWN
test suite on the VPS (not the air-gapped sandbox, where the source doesn't live)
before a self-edit is allowed to finish.
"""

import pytest

from hermes import agent
from hermes.llm import MockBackend
from hermes.tools import self_build


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """Point self-build at a throwaway checkout so agent-loop tests never write
    into the real repo this suite lives in."""
    root = tmp_path / "fakehermes"
    (root / "hermes" / "tools").mkdir(parents=True)
    (root / "tests").mkdir()
    monkeypatch.setattr(self_build, "repo_root", lambda: root)
    return root


def _run(project, cfg, script, sandbox=None):
    cfg.set("self_build_enabled", True)  # put the self-build tools in reach
    cfg.set("plan_build_tasks", False)
    return agent.run(project, "harden the widget", cfg, MockBackend(script),
                     gpu=None, sandbox=sandbox, env={},
                     confirm_fn=lambda *a, **k: True)


def test_self_build_tools_are_wired_into_the_loops():
    # phantom-finish (pasted code, wrote/ran nothing) already covers self-edits
    assert agent.SELF_BUILD_TOOLS <= agent.PRODUCTIVE_TOOLS
    # they get their OWN verification pass (re-run the suite on the VPS), not the
    # sandbox verifier — the source isn't in the sandbox container
    assert not (agent.SELF_BUILD_TOOLS & agent.CODE_WRITE_TOOLS)
    # and deliberately NOT a project-checkpoint trigger: self-build snapshots via
    # its own .self_build_backups, and a project checkpoint is the wrong directory
    assert not (agent.SELF_BUILD_TOOLS & agent.FILE_MUTATING_TOOLS)


def test_self_edit_verified_by_rerunning_the_suite(fake_repo, project, cfg):
    result = _run(project, cfg, [
        {"tool": "write_hermes_source",
         "args": {"path": "hermes/tools/widget.py", "content": "VALUE = 2\n"}},
        {"tool": "finish_run", "args": {"summary": "edited widget"}},
        # self-build verifier: re-runs Hermes' suite on the VPS, then rules PASS
        {"tool": "local_shell", "args": {"command": "echo '1 passed'"}},
        {"text": "ran python -m pytest tests/: 1 passed. VERDICT: PASS"},
    ])
    assert not result.aborted
    assert result.summary == "edited widget"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "self-build-verifier" in transcript  # the pass really ran


def test_self_edit_bounced_when_the_suite_reddens(fake_repo, project, cfg):
    cfg.set("verify_rounds", 1)  # one bounce, then the budget is spent
    result = _run(project, cfg, [
        {"tool": "write_hermes_source",
         "args": {"path": "hermes/tools/widget.py", "content": "VALUE = bad\n"}},
        {"tool": "finish_run", "args": {"summary": "edited (broken)"}},
        # verifier runs the suite and it FAILS -> the finish is bounced
        {"tool": "local_shell", "args": {"command": "echo '1 failed'"}},
        {"text": "pytest shows 1 failed. VERDICT: FAIL"},
        # doer, sent back to fix it, repairs the source and re-finishes
        {"tool": "edit_hermes_source",
         "args": {"path": "hermes/tools/widget.py", "old": "bad", "new": "3"}},
        {"tool": "finish_run", "args": {"summary": "fixed it"}},
    ])
    assert not result.aborted
    assert result.summary == "fixed it"
    assert (fake_repo / "hermes" / "tools" / "widget.py").read_text() == "VALUE = 3\n"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "reddens Hermes' tests" in transcript  # the FAIL nudge landed


def test_verify_before_done_covers_self_edits(fake_repo, project, cfg):
    cfg.set("verify_before_done", True)
    cfg.set("verify_code_runs", False)  # isolate the cheap nudge from the pass
    result = _run(project, cfg, [
        {"tool": "write_hermes_source",
         "args": {"path": "hermes/tools/widget.py", "content": "VALUE = 2\n"}},
        {"tool": "finish_run", "args": {"summary": "edited, not tested"}},
        # bounced (changed source, ran nothing) -> runs the suite, then finishes
        {"tool": "local_shell", "args": {"command": "echo ok"}},
        {"tool": "finish_run", "args": {"summary": "ran the tests"}},
    ])
    assert not result.aborted
    assert result.summary == "ran the tests"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "never actually ran anything" in transcript


def test_no_self_build_verification_when_switched_off(fake_repo, project, cfg):
    cfg.set("verify_code_runs", False)  # the switch the pass shares with project code
    result = _run(project, cfg, [
        {"tool": "write_hermes_source",
         "args": {"path": "hermes/tools/widget.py", "content": "VALUE = 2\n"}},
        {"tool": "finish_run", "args": {"summary": "edited, trusting myself"}},
    ])
    assert not result.aborted
    assert result.summary == "edited, trusting myself"
    assert result.turns == 2  # no verifier pass consumed extra turns
