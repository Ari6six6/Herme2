"""Feature 10: wall-clock caps, independent of turn counts."""

import json

from hermes import agent, subagent
from hermes.llm import ChatResult, MockBackend, ToolCall


def run_agent(project, cfg, script, confirm=None):
    cfg.set("plan_build_tasks", False)
    cfg.set("referee_on_deadlock", False)
    backend = MockBackend(script)
    return agent.run(
        project, "do the thing", cfg, backend,
        gpu=None, env={}, confirm_fn=confirm or (lambda *a, **k: True),
    )


class FakeClock:
    """time.monotonic() stand-in: starts at 0, advances by `step` each call."""

    def __init__(self, step):
        self.step = step
        self.value = 0.0

    def __call__(self):
        v = self.value
        self.value += self.step
        return v


def test_run_stops_at_wall_clock_budget(project, cfg, monkeypatch):
    cfg.set("max_run_seconds", 15)
    cfg.set("max_turns", 40)  # turn cap deliberately generous — time should bind first
    monkeypatch.setattr(agent.time, "monotonic", FakeClock(step=10))
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(10)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    # cap aborts still get a real model-written summary, not the stub
    assert result.summary == "[mock] run done."
    # stopped well short of the generous turn cap
    assert result.turns < 40


def test_run_gets_one_time_wrapup_nudge_before_hard_stop(project, cfg, monkeypatch):
    cfg.set("max_run_seconds", 20)
    cfg.set("max_turns", 40)
    monkeypatch.setattr(agent.time, "monotonic", FakeClock(step=9))
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(10)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert "wall-clock time budget is spent" in transcript


def test_disabled_by_default_turn_cap_still_governs(project, cfg, monkeypatch):
    # max_run_seconds is 0 by default: even a "slow" fake clock must never cap it.
    monkeypatch.setattr(agent.time, "monotonic", FakeClock(step=10_000))
    cfg.set("max_turns", 2)
    script = [{"tool": "write_note", "args": {"text": f"n{i}"}} for i in range(5)]
    result = run_agent(project, cfg, script)
    assert result.aborted
    assert result.turns == 2


def _parent_ctx(project, cfg, backend, confirm=None, depth=0):
    from hermes.tools import build_registry
    from hermes.tools.base import ToolContext

    reg = build_registry(project, cfg, confirm or (lambda *a, **k: True))
    ctx = ToolContext(
        project=project, cfg=cfg, confirm=confirm or (lambda *a, **k: True),
        backend=backend, think_re=None, depth=depth,
    )
    ctx.registry = reg
    return ctx


class ScriptBackend:
    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, tool_choice=None):
        if self.turns:
            return self.turns.pop(0)()
        return ChatResult(content="(script exhausted)")


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


def test_delegate_child_reaped_by_wall_clock(project, cfg, monkeypatch):
    cfg.set("delegate_max_seconds", 15)
    cfg.set("delegate_max_turns", 40)  # generous turn cap — time should bind first
    monkeypatch.setattr(subagent.time, "monotonic", FakeClock(step=10))
    backend = ScriptBackend([
        _call("write_note", {"text": f"step {i}"}) for i in range(10)
    ])
    ctx = _parent_ctx(project, cfg, backend)
    out = subagent.run_child(ctx, "endless task", ["write_note"], cfg)
    assert "[sub-agent stopped: wall-clock budget 15s reached]" in out
