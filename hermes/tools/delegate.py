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
    "operator.",
    obj_schema(
        {
            "brief": {"type": "string",
                      "description": "the self-contained task; the child has no other context"},
            "allowed_tools": {
                "type": "array", "items": {"type": "string"},
                "description": "tool names the child may use (a subset of yours)",
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
    log = getattr(ctx, "_delegate_log", None)
    return subagent.run_child(ctx, brief, args.get("allowed_tools") or [], ctx.cfg,
                              log=log)


TOOLS = [delegate]
