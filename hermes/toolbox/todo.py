"""Toolbox: a tiny per-project todo list for multi-run tasks."""

import json

TOOL = {
    "name": "todo",
    "description": (
        "Track multi-run work: action 'add' (text), 'done' (id), 'list'. "
        "Stored in the project; survives between runs."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["add", "done", "list"]},
            "text": {"type": "string"},
            "id": {"type": "integer"},
        },
        "required": ["action"],
    },
}


def _path(ctx):
    return ctx.project.root / "todo.json"


def run(args, ctx):
    path = _path(ctx)
    items = json.loads(path.read_text()) if path.exists() else []
    action = args["action"]
    if action == "add":
        if not args.get("text"):
            return "ERROR: 'text' required for add"
        items.append({"id": (max((i["id"] for i in items), default=0) + 1),
                      "text": args["text"], "done": False})
        path.write_text(json.dumps(items, indent=2))
        return f"added #{items[-1]['id']}"
    if action == "done":
        for item in items:
            if item["id"] == args.get("id"):
                item["done"] = True
                path.write_text(json.dumps(items, indent=2))
                return f"#{item['id']} done"
        return f"ERROR: no item #{args.get('id')}"
    lines = [f"[{'x' if i['done'] else ' '}] #{i['id']} {i['text']}" for i in items]
    return "\n".join(lines) or "(no todos)"
