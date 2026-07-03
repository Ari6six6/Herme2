"""Skill tools: load a skill's full body on demand, and write/update one.

The index of one-liners is already in the system prompt (like the toolbox
catalog); these tools are how the agent pulls a full procedure when it needs it
and how it captures what it learned. Both are auto-run: reading a skill is free,
and a skill is a plain note file scoped to the skills dir — no shell, no reach.
"""

from __future__ import annotations

from hermes import skills as skills_mod
from hermes.tools.base import obj_schema, tool


@tool(
    "load_skill",
    "Load the full body of a skill by name (from the index in your system "
    "prompt). Returns the whole procedure, including the gotchas. Use this "
    "before a task the index says a skill covers.",
    obj_schema({"name": {"type": "string"}}, ["name"]),
)
def load_skill(args, ctx):
    sk = skills_mod.get(ctx.project, args["name"])
    if sk is None:
        idx = skills_mod.index(ctx.project) or "(no skills yet)"
        return f"ERROR: no skill '{args['name']}'. Known skills:\n{idx}"
    return f"# SKILL: {sk.name} ({sk.scope})\n\n{sk.body.strip()}"


@tool(
    "write_skill",
    "Create or update a skill — a reusable how-to you can load later and in "
    "other projects. The FIRST line of `content` is the one-line description; "
    "the rest is the full procedure (include the gotchas you hit). Writing over "
    "an existing name edits it in place. scope: 'global' (default, cross-project) "
    "or 'project' (this project only).",
    obj_schema(
        {
            "name": {"type": "string", "description": "short id, [A-Za-z0-9_-]"},
            "content": {"type": "string",
                        "description": "first line = description; rest = procedure"},
            "scope": {"type": "string", "description": "'global' (default) or 'project'"},
        },
        ["name", "content"],
    ),
)
def write_skill(args, ctx):
    scope = "project" if str(args.get("scope", "")).lower() == "project" else "global"
    existed = skills_mod.get(ctx.project, args["name"]) is not None
    try:
        path = skills_mod.write(ctx.project, args["name"], args["content"], scope)
    except ValueError as e:
        return f"ERROR: {e}"
    verb = "updated" if existed else "created"
    return f"{verb} {scope} skill '{args['name']}' at {path}."


TOOLS = [load_skill, write_skill]
