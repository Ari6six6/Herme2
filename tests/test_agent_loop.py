import json

from hermes import agent
from hermes.llm import MockBackend


def run_agent(project, cfg, script, confirm=None, gpu=None):
    # The planner/referee are exercised in test_planner_referee; keep these
    # loop-mechanics tests free of the extra build-mode passes.
    cfg.set("plan_build_tasks", False)
    cfg.set("referee_on_deadlock", False)
    backend = MockBackend(script)
    return agent.run(
        project,
        "do the thing",
        cfg,
        backend,
        gpu=gpu,
        env={},
        confirm_fn=confirm or (lambda *a, **k: True),
    )


def test_happy_path_with_finish_run(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote out.txt"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote out.txt"
    assert (project.workspace_dir / "out.txt").read_text() == "hello"
    assert (project.runs_dir / "0001" / "summary.md").read_text().strip() == \
        "Did: wrote out.txt"
    assert (project.runs_dir / "0001" / "transcript.jsonl").exists()
    # prompt landed in history
    assert project.recent_prompts(5)[-1]["text"] == "do the thing"


def test_forced_summary_when_model_forgets(project, cfg):
    cfg.set("stall_nudges", 0)  # legacy path: prose is accepted as final immediately
    result = run_agent(project, cfg, [{"text": "all done, bye"}])
    assert not result.aborted
    assert result.summary == "[mock] run done."  # MockBackend obeys forced finish_run
    assert result.final_text == "all done, bye"


def test_stall_nudge_gets_model_to_act(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"text": "I should write out.txt with hello."},  # narrates, no tool call
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote out.txt"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote out.txt"
    assert (project.workspace_dir / "out.txt").read_text() == "hello"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "prose and no tool call" in transcript  # the nudge landed


def test_stall_nudge_flags_repetition(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"text": "I should write the file."},
            {"text": "I should write   the file."},  # same thing, modulo whitespace
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    assert not result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "same message twice" in transcript


def test_stall_nudges_exhausted_accepts_prose(project, cfg):
    result = run_agent(
        project,
        cfg,
        [{"text": "thinking..."}, {"text": "still thinking..."}, {"text": "the answer"}],
    )
    assert not result.aborted
    assert result.final_text == "the answer"  # third prose turn accepted as final
    assert result.summary == "[mock] run done."  # forced finish_run backstop


CODE_REPLY = "Here's the scraper:\n\n```python\nimport requests\nprint('hi')\n```"


def test_phantom_finish_bounced_then_does_real_work(project, cfg):
    # Model pastes code and tries to finish without ever writing a file.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "finish_run", "args": {"summary": "wrote scraper.py"},
             "say": CODE_REPLY},
            # bounced -> now it actually writes the file and finishes for real
            {"tool": "write_file",
             "args": {"path": "workspace/scraper.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "Did: wrote scraper.py"}},
        ],
    )
    assert not result.aborted
    assert result.summary == "Did: wrote scraper.py"
    assert (project.workspace_dir / "scraper.py").read_text() == "print('hi')"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Nobody runs the code in a chat reply" in transcript  # the nudge landed


def test_phantom_finish_allowed_when_file_was_written(project, cfg):
    # Code in the answer is fine when a file was actually written this run.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/scraper.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "done"}, "say": CODE_REPLY},
        ],
    )
    assert not result.aborted
    assert result.summary == "done"  # not bounced
    assert result.turns == 2


def test_phantom_finish_bounce_budget_does_not_loop(project, cfg):
    # If the model insists on finishing with only code (e.g. an explain-only
    # request), the single bounce is spent and prose is accepted — no loop.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "finish_run", "args": {"summary": "example"}, "say": CODE_REPLY},
            {"tool": "finish_run", "args": {"summary": "example, as asked"},
             "say": CODE_REPLY},
        ],
    )
    assert not result.aborted
    assert result.summary == "example, as asked"
    assert result.turns == 2


def test_phantom_guard_ignores_prose_without_code(project, cfg):
    # A normal prose answer with no code fence finishes immediately.
    result = run_agent(
        project,
        cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "I checked the logs; nginx is fine."}],
    )
    assert not result.aborted
    assert result.turns == 1


def test_turn_cap_forces_handoff_summary(project, cfg):
    cfg.set("max_turns", 2)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    assert result.turns == 2
    # cap aborts still get a real model-written summary, not the stub
    assert result.summary == "[mock] run done."


def test_stub_summary_when_backend_dies(project, cfg):
    class DeadBackend:
        def chat(self, *a, **k):
            from hermes.llm import LLMTransportError
            raise LLMTransportError("vLLM unreachable")

    result = agent.run(project, "do the thing", cfg, DeadBackend(),
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True)
    assert result.aborted
    assert "[auto-stub" in result.summary  # no extra LLM call when transport is down


def test_wrapup_warning_near_turn_cap(project, cfg):
    cfg.set("max_turns", 4)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "Only 2 turns remain" in transcript


def test_final_reply_persisted_verbatim(project, cfg):
    from hermes import package
    result = run_agent(
        project,
        cfg,
        [{"tool": "finish_run", "args": {"summary": "done"},
          "say": "Two options: (a) rsync nightly, (b) btrfs snapshots. I lean (b)."}],
    )
    assert result.final_text.startswith("Two options")
    assert (project.runs_dir / "0001" / "final.md").read_text().startswith("Two options")
    # the next run's package carries it verbatim
    user = package.assemble(project, "do option b", {}, cfg)[1]["content"]
    assert "# YOUR LAST REPLY (run 0001" in user
    assert "btrfs snapshots. I lean (b)." in user


def test_circuit_breaker_on_consecutive_errors(project, cfg):
    script = [
        {"tool": "read_file", "args": {"path": "workspace/missing.txt"}}
        for _ in range(5)
    ]
    result = run_agent(project, cfg, script)
    assert result.aborted
    assert result.turns == 3  # breaker trips after 3 consecutive ERROR results


def test_denied_local_shell_feeds_back(project, cfg):
    script = [
        {"tool": "local_shell", "args": {"command": "rm -rf /"}},
        {"tool": "finish_run", "args": {"summary": "operator said no"}},
    ]
    result = run_agent(project, cfg, script, confirm=lambda *a, **k: False)
    assert result.summary == "operator said no"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "DENIED by operator" in transcript


def test_remote_tools_without_gpu(project, cfg):
    script = [
        {"tool": "remote_shell", "args": {"command": "ls"}},
        {"tool": "finish_run", "args": {"summary": "no gpu"}},
    ]
    result = run_agent(project, cfg, script)
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "no GPU box attached" in transcript
    assert result.summary == "no gpu"


def test_tool_output_echoed_to_operator(project, cfg, capsys):
    # The operator must see the real tool result, not just the model's prose.
    run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/a.txt", "content": "hi"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
    )
    out = capsys.readouterr().out
    assert "wrote 2 chars to workspace/a.txt" in out  # real result on screen
    assert "summary recorded" not in out  # finish_run's result stays quiet


def test_echo_result_truncates_long_output(capsys):
    agent._echo_result("\n".join(f"line{i}" for i in range(50)))
    out = capsys.readouterr().out
    assert "line0" in out
    assert "line7" in out
    assert "line8" not in out  # capped at 8 lines
    assert "more line(s)" in out


def test_echo_result_skips_empty(capsys):
    agent._echo_result("   ")
    assert capsys.readouterr().out == ""


SANDBOX = object()  # a non-None stand-in for an attached GPU box


def test_verification_runs_only_with_a_sandbox(project, cfg):
    # No GPU attached -> no verifier pass, the doer's finish stands as before.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "x=1"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
        gpu=None,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier" not in transcript


def test_verification_passes_lets_run_finish(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "print(2+2)"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            # verifier pass (same backend, next script items):
            {"text": "Ran python m.py, output 4. VERDICT: PASS"},
        ],
        gpu=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "verifier"' in transcript


def test_verification_fail_bounces_then_doer_fixes(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "import nope"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            {"text": "Ran it, ModuleNotFoundError: nope. VERDICT: FAIL"},  # round 1
            # bounced back to the doer:
            {"tool": "edit_file",
             "args": {"path": "workspace/m.py", "old": "import nope", "new": "x=1"}},
            {"tool": "finish_run", "args": {"summary": "fixed it"}},
            {"text": "Ran it, no error. VERDICT: PASS"},  # round 2
        ],
        gpu=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "fixed it"
    assert (project.workspace_dir / "m.py").read_text() == "x=1"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "did NOT pass" in transcript  # the failure was fed back


def test_verification_budget_stops_relooping(project, cfg):
    cfg.set("verify_rounds", 1)
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "import nope"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            {"text": "VERDICT: FAIL still broken"},  # round 1, budget now 0
            # doer re-finishes; no budget left -> accepted without another pass
            {"tool": "finish_run", "args": {"summary": "second attempt"}},
        ],
        gpu=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "second attempt"


def test_verifier_can_use_tools_before_verdict(project, cfg):
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/m.py", "content": "print('hi')"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
            # verifier reads the file, then rules:
            {"tool": "read_file", "args": {"path": "workspace/m.py"}},
            {"text": "Saw print('hi'); ran it. VERDICT: PASS"},
        ],
        gpu=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier-tool" in transcript


def test_no_verification_for_non_code_runs(project, cfg):
    # A run that wrote no code files (just a note) isn't verified.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_note", "args": {"text": "nginx looked fine"}},
            {"tool": "finish_run", "args": {"summary": "checked, all good"}},
        ],
        gpu=SANDBOX,
    )
    assert not result.aborted
    assert result.summary == "checked, all good"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "verifier" not in transcript


def _seal_twin(project):
    from hermes.twin.model import Exchange
    twin = project.twin()
    twin.init(source="https://example.com", mission="reimpl", win_condition="match")
    twin.add_exchange(Exchange(method="GET", path="/ping", status=200,
                               response_body="pong", content_type="text/plain"))
    twin.seal()


def test_build_proof_gate_bounces_finish_without_twin_check(project, cfg):
    # Build mode: changed code, declared done, never queried the twin -> bounced.
    _seal_twin(project)
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "print('pong')"}},
            {"tool": "finish_run", "args": {"summary": "it works, trust me"}},
            # bounced -> now it actually checks against the twin, then finishes
            {"tool": "twin_request", "args": {"path": "/ping"}},
            {"tool": "finish_run", "args": {"summary": "proved: my output == twin's"}},
        ],
        gpu=None,  # isolate the build-proof gate from the sandbox verifier
    )
    assert not result.aborted
    assert result.summary == "proved: my output == twin's"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "you do not get to make" in transcript  # the gate's nudge landed


def test_build_proof_gate_passes_when_twin_queried(project, cfg):
    _seal_twin(project)
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "print('pong')"}},
            {"tool": "twin_request", "args": {"path": "/ping"}},
            {"tool": "finish_run", "args": {"summary": "checked against twin"}},
        ],
        gpu=None,
    )
    assert not result.aborted
    assert result.summary == "checked against twin"
    assert result.turns == 3  # not bounced
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "you do not get to make" not in transcript


def test_build_proof_gate_inactive_without_sealed_twin(project, cfg):
    # An open twin is still the recon phase — no build-proof gate.
    project.twin().init(source="https://example.com")
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/app.py", "content": "x=1"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ],
        gpu=None,
    )
    assert not result.aborted
    assert result.turns == 2  # finished without a bounce


def test_think_blocks_stripped():
    assert agent.strip_think("<think>secret</think>answer") == "answer"
    assert agent.strip_think("<seed:think>x</seed:think>ok") == "ok"
    assert agent.strip_think(None) == ""


def test_empty_finish_summary_falls_back_to_real_handoff(project, cfg):
    # finish_run with a whitespace-only summary used to slip past the
    # never-lose-the-handoff fallback (it guarded on `is None`, but the summary
    # stripped to ""). The run should still produce a non-empty summary.
    result = run_agent(
        project,
        cfg,
        [
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hi"}},
            {"tool": "finish_run", "args": {"summary": "   \n\t "}},
        ],
    )
    assert result.summary.strip() != ""
    assert (project.runs_dir / "0001" / "summary.md").read_text().strip() != ""
