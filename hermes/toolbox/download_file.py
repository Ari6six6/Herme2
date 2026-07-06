"""Toolbox: download a file from the web ON THE VPS into the workspace."""

import httpx

TOOL = {
    "name": "download_file",
    "description": (
        "Download a URL (from the VPS — the only place with internet) into "
        "the project workspace. Binary-safe. Max 100MB."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "dest": {"type": "string", "description": "path under workspace/, e.g. data.zip"},
        },
        "required": ["url", "dest"],
    },
}

MAX_BYTES = 100 * 1024 * 1024


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in

    try:
        dest = resolve_in(ctx.project.workspace_dir, args["dest"])
    except PathDenied:
        return "DENIED: dest must stay inside workspace/"
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    try:
        with httpx.stream("GET", args["url"], timeout=120, follow_redirects=True) as r:
            r.raise_for_status()
            with dest.open("wb") as f:
                for chunk in r.iter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        return "ERROR: file exceeds 100MB cap"
                    f.write(chunk)
    except httpx.HTTPError as e:
        return f"ERROR: {type(e).__name__}: {e}"
    return f"downloaded {total} bytes to workspace/{dest.relative_to(ctx.project.workspace_dir)}"
