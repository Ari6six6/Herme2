"""Feature 2: lazy compaction of the live within-prompt conversation."""

import json

from hermes import agent, compaction
from hermes.llm import ChatResult, ToolCall


class GoodSummarizer:
    """Faithful stand-in summarizer: shrinks the slice (like a real model) but
    copies error lines through verbatim, so we can prove exact errors survive."""

    def __init__(self):
        self.calls = 0

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls += 1
        slice_text = messages[-1]["content"]
        errs = [ln for ln in slice_text.splitlines() if "ERROR" in ln]
        return ChatResult(content="Compacted middle turns. Errors preserved:\n"
                          + "\n".join(errs))


def _msg_assistant(text, call_id, name, args):
    return {
        "role": "assistant",
        "content": text,
        "tool_calls": [
            {"id": call_id, "type": "function",
             "function": {"name": name, "arguments": json.dumps(args)}}
        ],
    }


def _tool_result(call_id, content):
    return {"role": "tool", "tool_call_id": call_id, "content": content}


ERR = "ERROR: not a file: workspace/missing.txt"


def _build(n_turns):
    msgs = [
        {"role": "system", "content": "HEADER-STABLE"},
        {"role": "user", "content": "PACKAGE-STABLE"},
    ]
    for i in range(n_turns):
        result = ERR if i == 0 else f"ok result {i} " + "x" * 300
        msgs.append(_msg_assistant(f"turn {i} " + "y" * 300, f"c{i}", "write_note",
                                   {"text": f"note {i}"}))
        msgs.append(_tool_result(f"c{i}", result))
    return msgs


def test_group_turns_splits_on_assistant():
    region = _build(3)[2:]
    turns = compaction.group_turns(region)
    assert len(turns) == 3
    assert all(t[0]["role"] == "assistant" for t in turns)


def test_estimate_tokens_counts_content_and_args():
    msgs = _build(2)
    t = compaction.estimate_tokens(msgs, schema_chars=0)
    assert t > 0


def test_no_compaction_when_disabled(cfg):
    msgs = _build(12)
    cfg.set("compaction_enabled", False)
    assert not compaction.maybe_compact(msgs, 2, GoodSummarizer(), cfg, 1000, 0)


def test_no_compaction_without_window(cfg):
    msgs = _build(12)
    cfg.set("compaction_enabled", True)
    assert not compaction.maybe_compact(msgs, 2, GoodSummarizer(), cfg, 0, 0)


def test_no_compaction_below_trigger(cfg):
    msgs = _build(12)
    cfg.set("compaction_enabled", True)
    # huge window -> way below trigger
    assert not compaction.maybe_compact(msgs, 2, GoodSummarizer(), cfg, 10_000_000, 0)


def test_compacts_keeps_header_and_last_m_and_preserves_error(cfg):
    cfg.set("compaction_enabled", True)
    cfg.set("compaction_keep_last_turns", 4)
    cfg.set("compaction_trigger_frac", 0.5)
    msgs = _build(12)
    before = compaction.estimate_tokens(msgs, 0)
    window = before  # trigger = 0.5*before, so we're over it
    ok = compaction.maybe_compact(msgs, 2, GoodSummarizer(), cfg, window, 0)
    assert ok
    # header untouched
    assert msgs[0]["content"] == "HEADER-STABLE"
    assert msgs[1]["content"] == "PACKAGE-STABLE"
    # one spliced marker then exactly the last 4 turns (8 messages) verbatim
    marker = msgs[2]
    assert marker["role"] == "user"
    assert "EARLIER CONVERSATION" in marker["content"]
    assert len(msgs) == 2 + 1 + 4 * 2
    # exact error from a compacted (early) turn survives in the summary
    assert ERR in marker["content"]
    # the last turn is still verbatim (turn 11), not folded into the summary
    assert any(m.get("content", "").startswith("ok result 11") for m in msgs[3:])
    # compaction bought runway: smaller than before
    assert compaction.estimate_tokens(msgs, 0) < before


def test_no_compaction_when_too_few_turns(cfg):
    cfg.set("compaction_enabled", True)
    cfg.set("compaction_keep_last_turns", 6)
    msgs = _build(5)  # <= keep_last + 1
    assert not compaction.maybe_compact(msgs, 2, GoodSummarizer(), cfg, 1, 0)


def test_side_call_failure_leaves_verbatim(cfg):
    from hermes.llm import LLMTransportError

    class DeadBackend:
        def chat(self, *a, **k):
            raise LLMTransportError("down")

    cfg.set("compaction_enabled", True)
    cfg.set("compaction_keep_last_turns", 4)
    msgs = _build(12)
    n_before = len(msgs)
    assert not compaction.maybe_compact(msgs, 2, DeadBackend(), cfg, 1, 0)
    assert len(msgs) == n_before  # untouched


# ---- integration through the real agent loop --------------------------------
class LongRunBackend:
    """Drives a long tool loop (error on turn 1, then padded successes) until the
    live conversation crosses the trigger, then finishes on the next turn. The
    compaction side-call (no tools) returns a real, shrinking summary that copies
    the error line through — like a faithful model."""

    def __init__(self):
        self.n = 0
        self.compacted = False

    def chat(self, messages, tools=None, tool_choice=None):
        if tools is None:  # the compaction summary side-call
            self.compacted = True
            errs = [ln for ln in messages[-1]["content"].splitlines()
                    if "missing.txt" in ln]
            return ChatResult(content="Compacted. Errors:\n" + "\n".join(errs))
        self.n += 1
        if self.n == 1:  # an error early, to be folded into the summary later
            return ChatResult(content=None, tool_calls=[
                ToolCall("t1", "read_file",
                         json.dumps({"path": "workspace/missing.txt"}))])
        if self.compacted:  # finish the turn right after the first compaction
            return ChatResult(content=None, tool_calls=[
                ToolCall("tf", "finish_run", json.dumps({"summary": "done"}))])
        return ChatResult(content="working " * 200, tool_calls=[
            ToolCall(f"t{self.n}", "write_note",
                     json.dumps({"text": "x" * 600}))])


def test_agent_loop_compacts_once_and_finishes(project, cfg):
    cfg.set("compaction_enabled", True)
    cfg.set("compaction_trigger_frac", 0.5)
    cfg.set("compaction_keep_last_turns", 4)
    cfg.set("max_turns", 40)
    cfg.set("plan_build_tasks", False)
    result = agent.run(
        project, "long task", cfg, LongRunBackend(),
        gpu=None, env={"context_window": 8000}, confirm_fn=lambda *a, **k: True,
    )
    assert not result.aborted
    assert result.summary == "done"
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    comp_lines = [l for l in transcript.splitlines()
                  if '"role": "compaction"' in l]
    assert len(comp_lines) == 1  # compacted exactly once
    # the exact error string from turn 1 survived into the compaction summary
    assert "workspace/missing.txt" in comp_lines[0]
