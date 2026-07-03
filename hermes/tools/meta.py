"""Meta tools: memory, run completion, and the toolbox (inherit/forge).

Toolbox design: a library of ready-made tools ships with the app, but their
schemas are NOT loaded into every prompt. The agent lists the toolbox, equips
what it needs (persisted per project), or forges its own tool. Forged tools
are loaded only after the operator approves the source (per content hash).
"""

from __future__ import annotations

import json

from hermes.tools.base import obj_schema, tool


@tool(
    "write_note",
    "Append a short note to the project's notes.md — facts, decisions, "
    "reminders for your future self. Notes appear in every future package.",
    obj_schema({"text": {"type": "string"}}, ["text"]),
)
def write_note(args, ctx):
    ctx.project.append_note(args["text"])
    return "note saved."


@tool(
    "finish_run",
    "End the run with your summary for your future self. Structure: Did / "
    "Files touched / Decisions / Results / Open items. Under 200 words. "
    "Call this exactly once, as your last action.",
    obj_schema({"summary": {"type": "string"}}, ["summary"]),
)
def finish_run(args, ctx):
    ctx.finish_summary = str(args["summary"]).strip()
    return "summary recorded — run complete."


@tool(
    "list_toolbox",
    "List the tool library: ready-made tools you can equip, plus tools you "
    "forged earlier in this project, with their equipped/approved state.",
    obj_schema({}, []),
)
def list_toolbox(args, ctx):
    if ctx.registry is None:
        return "ERROR: registry unavailable"
    return ctx.registry.toolbox_listing(ctx)


@tool(
    "equip_tool",
    "Equip a tool from the toolbox library by name. It becomes callable on "
    "your NEXT turn and stays equipped for this project.",
    obj_schema({"name": {"type": "string"}}, ["name"]),
)
def equip_tool(args, ctx):
    if ctx.registry is None:
        return "ERROR: registry unavailable"
    return ctx.registry.equip(args["name"], ctx)


@tool(
    "forge_tool",
    "Create a new tool for yourself. Provide a python module that defines "
    "TOOL = {'name':..., 'description':..., 'parameters': <json schema>} and "
    "run(args, ctx) -> str. The operator reviews the source before it loads. "
    "It becomes callable on your NEXT turn. ctx gives you .project, .gpu, "
    ".cfg, .confirm — file/shell effects still go through the normal gates.",
    obj_schema(
        {
            "filename": {"type": "string", "description": "e.g. my_tool.py"},
            "source": {"type": "string", "description": "full python source"},
        },
        ["filename", "source"],
    ),
)
def forge_tool(args, ctx):
    if ctx.registry is None:
        return "ERROR: registry unavailable"
    return ctx.registry.forge(args["filename"], args["source"], ctx)


TOOLS = [write_note, finish_run, list_toolbox, equip_tool, forge_tool]
