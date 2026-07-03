"""The delegate tool: hand a focused sub-task to a clean child worker.

The child runs the same loop recursively with a minimal context and a subset of
this agent's tools, and returns one conclusion. The whole cost to this agent is
the brief plus that conclusion — the child's intermediate steps never enter this
context. See hermes/subagent.py for the mechanics and the permission/depth rules.
"""

from __future__ import annotations

from hermes.tools.base import obj_schema, tool


@tool(
    "delegate",
    "Delegate a focused sub-task to a clean child agent and get back a single "
    "conclusion. The child starts fresh (no project memory) with only the tools "
    "you name in `allowed_tools`, runs on its own, and returns its findings — its "
    "intermediate work never touches your context. Use this to keep big, "
    "spammy sub-tasks (wide searches, multi-file surveys) out of your window. "
    "The child can never do more than you can, and gated tools still ask the "
    "operator. When personas are on you may spawn the child AS a named persona "
    "from the roster: it works in that voice and capacity, with that persona's "
    "tool posture unless you name `allowed_tools` yourself.",
    obj_schema(
        {
            "brief": {"type": "string",
                      "description": "the self-contained task; the child has no other context"},
            "allowed_tools": {
                "type": "array", "items": {"type": "string"},
                "description": "tool names the child may use (a subset of yours)",
            },
            "persona": {
                "type": "string",
                "description": "optional: a persona name from the roster the "
                               "child runs as",
            },
        },
        ["brief"],
    ),
)
def delegate(args, ctx):
    if not ctx.cfg.get("delegate_enabled", False):
        return "ERROR: delegation is disabled (config set delegate_enabled true)."
    depth = ctx.depth or 0
    if depth >= int(ctx.cfg.get("delegate_max_depth", 1)):
        return (f"ERROR: delegation depth cap reached (depth {depth}); this child "
                "may not spawn its own children.")
    from hermes import subagent
    brief = str(args.get("brief") or "").strip()
    if not brief:
        return "ERROR: delegate needs a `brief`."
    persona = None
    pname = str(args.get("persona") or "").strip()
    if pname:
        if not ctx.cfg.get("personas_enabled", False):
            return ("ERROR: personas are disabled "
                    "(config set personas_enabled true).")
        from hermes import personas as personas_mod
        max_chars = ctx.cfg.get("persona_max_chars", 2000)
        catalog = personas_mod.load_all(ctx.project, max_chars)
        persona = personas_mod.resolve(catalog, pname)
        if persona is None:
            return (f"ERROR: no such persona '{pname}'. "
                    f"Available: {', '.join(sorted(catalog))}")
    log = getattr(ctx, "_delegate_log", None)
    return subagent.run_child(ctx, brief, args.get("allowed_tools") or [], ctx.cfg,
                              log=log, persona=persona)


TOOLS = [delegate]
