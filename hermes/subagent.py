"""Subagent delegation: the existing turn loop, invoked recursively with a
minimal context.

A child gets the brief, a subset of the parent's tools, and a stripped header —
no persona, no mission, no history, no summaries. It runs its own bounded loop
and returns a single conclusion. Its intermediate tool spam lives in the child's
local message list and dies when the call returns, so the parent's context grows
by only the brief plus the returned summary. That's what turns the context
ceiling from a hard limit into a soft one.

Permissions: the child dispatches through the SAME tool functions and the SAME
`confirm`, so gated tools still stop for the owner. Its tool set is a subset of
the parent's already-built registry, so a child can never hold broader
permissions than its parent. Recursion depth is capped (default 1).
"""

from __future__ import annotations

from hermes import package
from hermes.llm import LLMTransportError
from hermes.tools import ToolRegistry
from hermes.tools.base import ToolContext
from hermes.ui import cyan, dim, magenta


def _child_registry(parent_ctx, allowed_tools, depth, max_depth, cfg):
    """A registry that is a strict subset of the parent's. Unknown or
    unpermitted names are silently dropped — a child cannot widen its reach."""
    parent = parent_ctx.registry
    child = ToolRegistry()
    for name in dict.fromkeys(allowed_tools or []):  # dedupe, keep order
        t = parent._tools.get(name)
        if t is not None and name not in ("finish_run", "delegate"):
            child.register(t)
    # Always able to finish; delegate only if a grandchild is still within depth.
    if "finish_run" in parent._tools:
        child.register(parent._tools["finish_run"])
    if (cfg.get("delegate_enabled", False) and depth < max_depth
            and "delegate" in parent._tools):
        child.register(parent._tools["delegate"])
    return child


def _cap_out(last_text, tool_names, max_turns, reason) -> str:
    used = ", ".join(dict.fromkeys(tool_names)) or "none"
    tail = (last_text or "").strip()
    return (
        f"[sub-agent stopped: {reason}]\n"
        f"Turn cap: {max_turns}. Tools used: {used}.\n"
        f"How far it got: {tail[:800] if tail else '(no output produced)'}\n"
        "Treat this as partial — the parent should decide the next step."
    )


def run_child(parent_ctx: ToolContext, brief: str, allowed_tools, cfg,
              log=None, persona=None) -> str:
    """Run one delegated child loop and return its single conclusion string.
    With `persona` (feature 9) the child speaks and works as that persona: its
    voice rides after the subagent prompt, its tool posture is the default
    allowed set, and its turn cap tightens the child's own."""
    from hermes.agent import _assistant_msg, strip_think

    backend = parent_ctx.backend
    if backend is None:
        return "ERROR: no backend available for delegation."
    think_re = parent_ctx.think_re
    depth = (parent_ctx.depth or 0) + 1
    max_depth = int(cfg.get("delegate_max_depth", 1))
    max_turns = max(1, int(cfg.get("delegate_max_turns", 20)))
    if persona is not None:
        if not allowed_tools:
            allowed_tools = list(persona.tools or [])
        if persona.max_turns:
            max_turns = max(1, min(max_turns, persona.max_turns))

    child_reg = _child_registry(parent_ctx, allowed_tools, depth, max_depth, cfg)
    child_ctx = ToolContext(
        project=parent_ctx.project, cfg=cfg, gpu=parent_ctx.gpu,
        sandbox=parent_ctx.sandbox, hosts=parent_ctx.hosts,
        confirm=parent_ctx.confirm, served_ctx=parent_ctx.served_ctx,
        backend=backend, think_re=think_re, depth=depth,
    )
    child_ctx.registry = child_reg
    child_ctx._delegate_log = log  # nested delegation logs into the same transcript

    tool_list = ", ".join(child_reg.names())
    system = package.render(package.subagent_prompt(), {"tools": tool_list})
    if persona is not None:
        # APPENDED after the subagent prompt, never prepended — the child is
        # still a sub-agent first (and callers key on that prompt's first line).
        system += (
            f"\n\n## Persona — {persona.name}\n\n{persona.voice}\n\n"
            "Work the brief as this persona: its voice, its capacity. The "
            "sub-agent rules above still apply."
        )
    msgs = [{"role": "system", "content": system},
            {"role": "user", "content": brief.strip()}]
    if log:
        who = f" persona={persona.name}" if persona is not None else ""
        log({"role": "delegate",
             "content": f"depth={depth}{who} tools=[{tool_list}]\n{brief[:500]}"})

    tool_names: list[str] = []
    last_text = ""
    for _ in range(max_turns):
        try:
            result = backend.chat(msgs, tools=child_reg.schemas())
        except LLMTransportError:
            return _cap_out(last_text, tool_names, max_turns, "backend unreachable")
        shown = strip_think(result.content, think_re) if think_re else strip_think(
            result.content
        )
        if shown:
            last_text = shown
            print(magenta("  [child] ") + dim(shown.splitlines()[0][:120]))
        if not result.tool_calls:
            # A child that stops without finishing still owes a conclusion.
            if last_text:
                return last_text
            continue
        msgs.append(_assistant_msg(result))
        for tc in result.tool_calls:
            tool_names.append(tc.name)
            out = child_reg.dispatch(tc.name, tc.arguments, child_ctx)
            if tc.name != "finish_run":
                print(dim("    [child] → ") + cyan(tc.name))
            if log:
                log({"role": "delegate-tool", "name": tc.name, "content": out})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
        if child_ctx.finish_summary is not None:
            return child_ctx.finish_summary or _cap_out(
                last_text, tool_names, max_turns, "finished with an empty summary"
            )
    return _cap_out(last_text, tool_names, max_turns, "turn cap reached")
