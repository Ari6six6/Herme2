"""Retrospection: recursive self-improvement across runs (feature 9).

The skills nudge (feature 3) reflects on ONE run while it's still in context.
This is the cross-run layer: every N runs a fresh-context side-pass reads the
harness-recorded per-run metrics (ground truth — turns, aborts, tool errors,
bounces; numbers the harness counted, which the model can't embellish) plus
its own run summaries, looks for recurring friction, and banks what it
concludes as notes and skills — the same assets that feed every future
package, which is what makes the improvement recursive.

The write surface is deliberately the agent's own assets only: write_note
always; load_skill/write_skill when the skills system is on. No shells, no
network, no mission/persona/directives — those are the operator's. The pass
never raises; a failed pass is a no-op and the run's result stands.
"""

from __future__ import annotations

from hermes.llm import LLMTransportError


def build_registry(cfg):
    """The pass's own registry: only tools whose output recirculates into
    future packages. A skill written while skills are OFF would never surface
    (no index in the prompt, no load_skill), so the skill tools only register
    when the system that makes them visible is on."""
    from hermes.tools import ToolRegistry
    from hermes.tools.meta import write_note

    registry = ToolRegistry()
    registry.register(write_note)
    if cfg.get("skills_enabled", False):
        from hermes.tools import skills as skills_tools

        for t in skills_tools.TOOLS:
            registry.register(t)
    return registry


def metrics_block(project, window: int) -> str:
    """The recent metrics as compact fixed-shape lines, oldest first."""
    lines = []
    for m in project.recent_metrics(window):
        lines.append(
            f"run {m.get('run', 0):04d}: turns={m.get('turns', '?')}"
            f" aborted={'yes' if m.get('aborted') else 'no'}"
            f" tool_errors={m.get('tool_errors', 0)}"
            f" stall_nudges={m.get('stall_nudges', 0)}"
            f" phantom_bounces={m.get('phantom_bounces', 0)}"
            f" verify_bounces={m.get('verify_bounces', 0)}"
            f" verify_failures={m.get('verify_failures', 0)}"
            f" tainted_turns={m.get('tainted_turns', 0)}"
        )
    return "\n".join(lines)


def retrospect(project, backend, cfg, think_re=None, log=None) -> bool:
    """One bounded self-review pass over the last `retrospect_window` runs.
    Returns True when the pass banked something (a note or a skill)."""
    from hermes import package
    from hermes import skills as skills_mod
    from hermes.agent import _assistant_msg, strip_think
    from hermes.tools.base import ToolContext
    from hermes.ui import dim, magenta

    window = max(1, int(cfg.get("retrospect_window", 10)))
    if len(project.recent_metrics(window)) < 2:
        return False  # one run has no pattern in it
    summaries = "\n\n".join(
        f"## Run {rid:04d}\n{text}"
        for rid, text in project.recent_summaries(window)
    )
    prompt = package.render(package.retrospect_prompt(), {
        "metrics": metrics_block(project, window),
        "summaries": summaries or "(none)",
        "skills_index": skills_mod.index(project) or "(none)",
        "notes": package.truncate_keep_tail(project.read_notes().strip(), 2000)
        or "(none)",
    })
    registry = build_registry(cfg)
    # An unattended reflection pass must never approve anything on the
    # operator's behalf: everything registered here is free, and if a gated
    # tool ever slipped in, this confirm fails closed.
    ctx = ToolContext(project=project, cfg=cfg, confirm=lambda *a, **k: False)
    ctx.registry = registry
    msgs = [{"role": "user", "content": prompt}]
    if log:
        log({"role": "retrospect", "content": prompt[:4000]})
    banked = False
    for _ in range(max(1, int(cfg.get("retrospect_max_turns", 4)))):
        try:
            result = backend.chat(msgs, tools=registry.schemas())
        except LLMTransportError:
            return banked
        shown = strip_think(result.content, think_re) if think_re \
            else strip_think(result.content)
        if log:
            log({"role": "retrospect", "content": result.content,
                 "tool_calls": [{"name": tc.name, "arguments": tc.arguments}
                                for tc in result.tool_calls]})
        if shown:
            print(magenta("  [retrospect] ") + dim(shown.splitlines()[0][:120]))
        if not result.tool_calls:
            return banked
        msgs.append(_assistant_msg(result))
        for tc in result.tool_calls:
            if tc.name == "finish_run":
                out = ("Not here — this is a reflection pass, not a run. Stop "
                       "calling tools when you're done.")
            else:
                out = registry.dispatch(tc.name, tc.arguments, ctx)
                if tc.name in ("write_note", "write_skill") and \
                        not out.startswith(("ERROR", "DENIED")):
                    banked = True
                    print(magenta("  (retrospect banked a "
                                  f"{'note' if tc.name == 'write_note' else 'skill'})"))
            if log:
                log({"role": "retrospect-tool", "name": tc.name, "content": out})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
    return banked


def maybe_retrospect(project, backend, cfg, run_id: int, think_re=None,
                     log=None) -> bool:
    """Trigger a pass at the end of a run when it's due — every
    `retrospect_every_runs` runs, stateless like directive reconciliation.
    Gated by the caller on `retrospect_enabled`. Returns True if the pass
    banked anything."""
    every = max(1, int(cfg.get("retrospect_every_runs", 5)))
    if run_id % every != 0:
        return False
    return retrospect(project, backend, cfg, think_re=think_re, log=log)
