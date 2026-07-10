"""Feature 4: subagent delegation."""

import json

from hermes import agent, subagent
from hermes.llm import ChatResult, MockBackend, ToolCall
from hermes.tools import build_registry
from hermes.tools.base import ToolContext


def _parent_ctx(project, cfg, backend, confirm=None, depth=0):
    cfg.set("delegate_enabled", True)
    reg = build_registry(project, cfg, confirm or (lambda *a, **k: True))
    ctx = ToolContext(
        project=project, cfg=cfg, confirm=confirm or (lambda *a, **k: True),
        backend=backend, think_re=None, depth=depth,
    )
    ctx.registry = reg
    return ctx


class ScriptBackend:
    """Emits a fixed sequence of turns; each item is a ChatResult factory."""

    def __init__(self, turns):
        self.turns = list(turns)

    def chat(self, messages, tools=None, tool_choice=None):
        if self.turns:
            return self.turns.pop(0)()
        return ChatResult(content="(script exhausted)")


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


def _say(text):
    return lambda: ChatResult(content=text)


def test_child_runs_subset_and_returns_summary(project, cfg):
    # Child writes a file then finishes; only the summary comes back.
    backend = ScriptBackend([
        _call("write_file", {"path": "workspace/child.txt", "content": "hi"}),
        _call("finish_run", {"summary": "wrote child.txt, all good"}),
    ])
    ctx = _parent_ctx(project, cfg, backend)
    out = subagent.run_child(ctx, "write a file", ["write_file"], cfg)
    assert out == "wrote child.txt, all good"
    assert (project.workspace_dir / "child.txt").read_text() == "hi"


def test_child_cannot_exceed_parent_tools(project, cfg):
    # Ask for a tool the parent doesn't have -> silently dropped; child registry
    # is a strict subset. local_shell IS a parent tool, so it's grantable; a
    # made-up name is not.
    backend = ScriptBackend([_call("finish_run", {"summary": "done"})])
    ctx = _parent_ctx(project, cfg, backend)
    reg = subagent._child_registry(ctx, ["write_file", "not_a_real_tool"],
                                    depth=1, max_depth=1, cfg=cfg)
    assert "write_file" in reg.names()
    assert "not_a_real_tool" not in reg.names()
    assert "finish_run" in reg.names()


def test_child_gated_tool_still_asks_operator(project, cfg):
    # local_shell is owner-confirmed. A DENY inside the child must be honoured.
    calls = {"n": 0}

    def deny(*a, **k):
        calls["n"] += 1
        return False

    backend = ScriptBackend([
        _call("local_shell", {"command": "echo hi"}),  # will be DENIED
        _call("finish_run", {"summary": "operator blocked the shell"}),
    ])
    ctx = _parent_ctx(project, cfg, backend, confirm=deny)
    out = subagent.run_child(ctx, "run a shell cmd", ["local_shell"], cfg)
    assert calls["n"] == 1  # the confirm flow fired inside the child
    assert out == "operator blocked the shell"


def test_depth_cap_blocks_grandchildren(project, cfg):
    # A child (depth 1) with default max_depth 1 must not get a delegate tool.
    backend = ScriptBackend([_call("finish_run", {"summary": "done"})])
    ctx = _parent_ctx(project, cfg, backend)
    reg = subagent._child_registry(ctx, ["delegate"], depth=1, max_depth=1, cfg=cfg)
    assert "delegate" not in reg.names()


def test_delegate_tool_depth_guard(project, cfg):
    backend = ScriptBackend([])
    ctx = _parent_ctx(project, cfg, backend, depth=1)  # already at the cap
    out = ctx.registry.dispatch("delegate", json.dumps({"brief": "x"}), ctx)
    assert out.startswith("ERROR: delegation depth cap")


def test_cap_out_returns_structured_progress(project, cfg):
    cfg.set("delegate_max_turns", 2)
    # Child never finishes: two working turns, then the cap.
    backend = ScriptBackend([
        _call("write_note", {"text": "step 1"}),
        _call("write_note", {"text": "step 2"}),
    ])
    ctx = _parent_ctx(project, cfg, backend)
    out = subagent.run_child(ctx, "endless task", ["write_note"], cfg)
    assert "[sub-agent stopped: turn cap reached]" in out
    assert "write_note" in out


def test_delegate_disabled_returns_error(project, cfg):
    reg = build_registry(project, cfg, lambda *a, **k: True)  # delegate_enabled off
    assert "delegate" not in reg.names()


# ---- end to end: parent context grows by only brief + summary ----------------
class ParentWithDelegate:
    """Parent delegates once, then finishes. The child's own turns are served by
    the same backend (interleaved), but must NOT appear in the parent's messages."""

    def __init__(self):
        self.n = 0

    def chat(self, messages, tools=None, tool_choice=None):
        self.n += 1
        # Distinguish parent vs child by the system prompt (child = subagent.md).
        is_child = messages[0]["content"].startswith("You are a SUB-AGENT")
        if is_child:
            if self.n_child_step == 0:
                self.n_child_step = 1
                return ChatResult(content=None, tool_calls=[
                    ToolCall("cc", "write_note", json.dumps({"text": "SPAMMY CHILD DETAIL"}))])
            return ChatResult(content=None, tool_calls=[
                ToolCall("cf", "finish_run",
                         json.dumps({"summary": "CHILD CONCLUSION: found 3 places"}))])
        # parent
        if self.n == 1:
            self.n_child_step = 0
            return ChatResult(content=None, tool_calls=[
                ToolCall("pd", "delegate", json.dumps({
                    "brief": "search the repo", "allowed_tools": ["write_note"]}))])
        return ChatResult(content=None, tool_calls=[
            ToolCall("pf", "finish_run", json.dumps({"summary": "parent done"}))])


def test_parent_context_grows_by_only_brief_and_summary(project, cfg):
    cfg.set("delegate_enabled", True)
    result = agent.run(project, "do it", cfg, ParentWithDelegate(),
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True)
    assert not result.aborted
    assert result.summary == "parent done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    # the child's conclusion reached the parent as the delegate tool result...
    assert "CHILD CONCLUSION: found 3 places" in transcript
    # ...but the child's spammy intermediate step is only in the child's own
    # (logged) trace, never spliced into the parent's message list. Assert the
    # parent's delegate tool RESULT is the conclusion, not the spam.
    lines = [json.loads(l) for l in transcript.splitlines()]
    delegate_results = [
        e for e in lines
        if e.get("role") == "tool" and "CHILD CONCLUSION" in e.get("content", "")
    ]
    assert delegate_results  # parent saw the conclusion as a tool result
    parent_tool_spam = [
        e for e in lines
        if e.get("role") == "tool" and "SPAMMY CHILD DETAIL" in e.get("content", "")
    ]
    assert not parent_tool_spam  # child's note result never entered parent context
