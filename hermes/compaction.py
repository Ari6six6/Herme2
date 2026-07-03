"""Lazy compaction of the live, within-prompt conversation.

`summary.md` (the durable per-run handoff) is untouched — that's the project
record. This is different: during a single long tool loop the `messages` list
grows verbatim, and on a ~60K window a 40-turn run can crowd out the room the
current turn needs. When the live conversation crosses a trigger threshold we
fire a side LLM call that summarizes the MIDDLE turns — preserving decisions,
files, exact commands, and exact error strings — and splice that in place of
them, keeping the stable header and the last M turns verbatim.

Everything here is off unless `compaction_enabled` and a known context window;
a failed side-call leaves the conversation verbatim and the run continues.
"""

from __future__ import annotations

import json

from hermes.llm import LLMTransportError

APPROX_CHARS_PER_TOKEN = 4


def estimate_tokens(messages: list[dict], schema_chars: int = 0) -> int:
    """Rough token estimate for the whole request: message text + tool-call
    arguments + the (constant) tool schemas. Same 4-chars/token yardstick the
    package budget uses — good enough to decide when to compact."""
    chars = schema_chars
    for m in messages:
        chars += len(str(m.get("content") or ""))
        for tc in m.get("tool_calls") or []:
            fn = tc.get("function", tc)
            chars += len(str(fn.get("name", ""))) + len(str(fn.get("arguments", "")))
    return chars // APPROX_CHARS_PER_TOKEN


def group_turns(region: list[dict]) -> list[list[dict]]:
    """Group a slice of the conversation into turns. A turn starts at each
    assistant message and includes the tool results (and any nudge) that follow
    it, so a turn is always a self-contained, API-valid unit."""
    turns: list[list[dict]] = []
    cur: list[dict] = []
    for m in region:
        if m.get("role") == "assistant" and cur:
            turns.append(cur)
            cur = []
        cur.append(m)
    if cur:
        turns.append(cur)
    return turns


def _render_slice(turns: list[list[dict]]) -> str:
    """Flatten the middle turns into readable text for the summarizer, keeping
    exact commands, arguments, and error strings intact."""
    out: list[str] = []
    for t in turns:
        for m in t:
            role = m.get("role")
            content = str(m.get("content") or "").strip()
            if role == "assistant":
                if content:
                    out.append(f"[assistant] {content}")
                for tc in m.get("tool_calls") or []:
                    fn = tc.get("function", tc)
                    out.append(f"[tool_call] {fn.get('name')}({fn.get('arguments')})")
            elif role == "tool":
                out.append(f"[tool_result] {content}")
            elif role == "user":
                out.append(f"[nudge] {content}")
    return "\n".join(out)


def maybe_compact(messages, stable_prefix, backend, cfg, window, schema_chars,
                  think_re=None, log=None) -> bool:
    """Compact the live conversation in place when it's over the trigger. Returns
    True if a compaction happened. `messages` is mutated (spliced) on success."""
    if not cfg.get("compaction_enabled", False) or not window:
        return False
    trigger = int(window * cfg.get("compaction_trigger_frac", 0.5))
    if estimate_tokens(messages, schema_chars) < trigger:
        return False

    keep_last = max(1, int(cfg.get("compaction_keep_last_turns", 6)))
    region = messages[stable_prefix:]
    turns = group_turns(region)
    if len(turns) <= keep_last + 1:
        return False  # nothing meaningful to fold away yet

    middle = turns[:-keep_last]
    kept = [m for t in turns[-keep_last:] for m in t]
    summary = _summarize(_render_slice(middle), backend, cfg, think_re)
    if not summary:
        return False  # side-call failed — stay verbatim, keep running

    marker = {
        "role": "user",
        "content": (
            "# EARLIER CONVERSATION (compacted to save context — decisions, "
            "files, exact commands, and error messages preserved below; the last "
            f"{keep_last} turns remain verbatim after this)\n\n" + summary
        ),
    }
    messages[stable_prefix:] = [marker] + kept
    if log:
        log({"role": "compaction", "content": summary})
    return True


def _summarize(slice_text: str, backend, cfg, think_re=None) -> str | None:
    from hermes import package
    from hermes.agent import strip_think

    if not slice_text.strip():
        return None
    prompt = package.render(package.compact_prompt(), {"slice": slice_text})
    try:
        result = backend.chat([{"role": "user", "content": prompt}])
    except LLMTransportError:
        return None
    text = strip_think(result.content, think_re) if think_re else strip_think(
        result.content
    )
    return (text or "").strip() or None
