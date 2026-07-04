"""The leave_landmark tool: a worker's note at the rendezvous point.

A landmark is how a short life speaks to a longer one: the mark stands on
the road until a morning briefing reads it, and the room is bound to address
it. Scoped to the project's landmarks dir — a note file, no shell, no reach.
"""

from __future__ import annotations

from hermes import landmarks as landmarks_mod
from hermes.tools.base import obj_schema, tool


@tool(
    "leave_landmark",
    "Leave a landmark: a note at the rendezvous point that the NEXT day's "
    "briefing is bound to read and address. Use it when you find something "
    "outside your brief that tomorrow must not miss — a lead you couldn't "
    "chase, a danger you saw, a question above your station. First line of "
    "`text` is the one-line summary. Same name overwrites — the road holds "
    "one mark per name.",
    obj_schema(
        {
            "name": {"type": "string", "description": "short id, [A-Za-z0-9_-]"},
            "text": {"type": "string",
                     "description": "first line = summary; rest = the message"},
        },
        ["name", "text"],
    ),
)
def leave_landmark(args, ctx):
    try:
        path = landmarks_mod.leave(ctx.project, args["name"], args["text"])
    except ValueError as e:
        return f"ERROR: {e}"
    return (f"landmark '{args['name']}' stands at {path} — the next briefing "
            "must address it.")


TOOLS = [leave_landmark]
