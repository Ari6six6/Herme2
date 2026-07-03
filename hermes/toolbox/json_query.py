"""Toolbox: inspect a JSON file without reading the whole thing into context."""

import json

TOOL = {
    "name": "json_query",
    "description": (
        "Read a JSON file in the project and extract a value by dotted path "
        "(e.g. 'results.0.name'). Without a path, shows the top-level shape."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "file path in the project"},
            "query": {"type": "string", "description": "dotted path, optional"},
        },
        "required": ["path"],
    },
}


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in

    try:
        path = resolve_in(ctx.project.root, args["path"])
    except PathDenied as e:
        return f"DENIED: {e}"
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        return f"ERROR: {e}"
    node = data
    if args.get("query"):
        for part in args["query"].split("."):
            try:
                node = node[int(part)] if isinstance(node, list) else node[part]
            except (KeyError, IndexError, ValueError, TypeError):
                return f"ERROR: query failed at '{part}'"
    if isinstance(node, dict):
        shape = {k: type(v).__name__ for k, v in list(node.items())[:40]}
        preview = json.dumps(node, indent=2)[:2000]
        return f"keys: {json.dumps(shape, indent=2)}\npreview:\n{preview}"
    if isinstance(node, list):
        return f"list of {len(node)} items\npreview:\n{json.dumps(node[:5], indent=2)[:2000]}"
    return json.dumps(node)[:4000]
