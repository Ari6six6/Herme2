"""Twin tools: the agent's window onto the runtime twin of the target.

The twin is the **real reconstructed software** running in a container on this
box (the VPS Hermes lives on) — a faithful, SAFE, live clone of the target, not
the live service itself. `twin_request` hits that running clone at localhost and
returns whatever it really does. The recorded request/response samples are kept
only as *ground truth*
to prove the running twin matches the target (twin_map shows that surface;
twin_reground re-checks one against the live target).

These tools only register when a sealed twin exists for the project.
"""

from __future__ import annotations

import httpx

from hermes.tools._common import twin_for as _twin
from hermes.tools.base import obj_schema, tool


def _twin_base_url(ctx) -> str:
    return f"http://127.0.0.1:{ctx.cfg.get('twin_port', 8900)}"


@tool(
    "twin_request",
    "Send a request to the runtime twin — the REAL reconstructed software running "
    "in the sandbox, a faithful SAFE clone of the target (never the live service). "
    "Returns exactly what the running twin does. Build your solution to match it.",
    obj_schema(
        {
            "path": {"type": "string", "description": "request path, e.g. /users/1"},
            "method": {"type": "string", "description": "GET (default), POST, ..."},
            "query": {"type": "string", "description": "query string (optional)"},
            "body": {"type": "string", "description": "request body (optional)"},
        },
        ["path"],
    ),
)
def twin_request(args, ctx):
    method = (args.get("method") or "GET").upper()
    path = args["path"]
    if not path.startswith("/"):
        path = "/" + path
    query = (args.get("query") or "").lstrip("?")
    url = _twin_base_url(ctx) + path + (f"?{query}" if query else "")
    body = args.get("body")
    try:
        resp = httpx.request(method, url, content=body, timeout=20)
    except httpx.HTTPError as e:
        return (
            f"ERROR: could not reach the runtime twin at {_twin_base_url(ctx)} "
            f"[{type(e).__name__}: {e}]. The twin is the real software running in "
            "the sandbox — ask the operator to `build serve` it (and `sandbox add` "
            "a VPS first if none is attached)."
        )
    ctype = resp.headers.get("content-type", "")
    return (
        f"[twin: live runtime response]\nHTTP {resp.status_code} {ctype}\n"
        f"request: {method} {path}{('?' + query) if query else ''}\n\n{resp.text}"
    )


@tool(
    "twin_map",
    "Show the surface the twin covers: the route map (method + templated path + "
    "how many examples), whether an API spec was captured, plus the mission and "
    "winning condition for this build.",
    obj_schema({}, []),
)
def twin_map(args, ctx):
    twin = _twin(ctx)
    if not twin.is_sealed():
        return "ERROR: no sealed twin for this project."
    lines = [twin.summary(), "", "route map (method · template · examples):"]
    for method, template, n in twin.route_map():
        lines.append(f"  {method} {template}  ({n})")
    return "\n".join(lines)


@tool(
    "twin_stack",
    "Show the fingerprinted web stack of the target (server, runtime, "
    "framework/CMS and version), read from the recorded responses. This is what "
    "the twin reconstructs.",
    obj_schema({}, []),
)
def twin_stack(args, ctx):
    twin = _twin(ctx)
    if not twin.is_sealed():
        return "ERROR: no sealed twin for this project."
    stack = twin.stack
    if not stack:
        return "no stack fingerprint recorded."
    lines = []
    from hermes.twin.recon import StackReport
    report = StackReport(**stack)
    lines.append(report.summary())
    if report.signals:
        lines.append("signals: " + ", ".join(report.signals))
    return "\n".join(lines)


@tool(
    "twin_expand",
    "Grow the twin to cover requests it's missing. Give the paths you need; the "
    "clone step fetches them from the target (on the phone, never from here) and "
    "folds them into the twin. Use this when twin_request returns a MISS for "
    "something you need.",
    obj_schema(
        {"paths": {"type": "array", "items": {"type": "string"},
                   "description": "paths to learn, e.g. ['/users/2', '/users/3']"}},
        ["paths"],
    ),
)
def twin_expand(args, ctx):
    from hermes.twin import clone as clone_mod

    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    paths = [str(p) for p in (args.get("paths") or []) if str(p).strip()]
    if not paths:
        return "ERROR: give at least one path to learn."
    report = clone_mod.expand(twin, twin.source, paths)
    return (f"clone layer learned {report['added']} request(s) "
            f"({report['errors']} error(s)); twin now has {len(twin.exchanges())} "
            "exchange(s). Re-run twin_request for the paths you needed.")


@tool(
    "twin_reground",
    "Re-check one request against the real target and correct the twin if it has "
    "drifted. Reach for this when a mismatch might be the twin's fault rather than "
    "your solution's: it fetches the live response for that exact request, compares "
    "it to the twin's stored sample, and updates the twin if they differ — so you "
    "always build against the truth, and you learn whether a mismatch is on you or "
    "on the twin.",
    obj_schema(
        {
            "path": {"type": "string", "description": "request path, e.g. /users/1"},
            "method": {"type": "string", "description": "GET (default) or HEAD"},
            "query": {"type": "string", "description": "query string (optional)"},
            "body": {"type": "string", "description": "request body (optional)"},
        },
        ["path"],
    ),
)
def twin_reground(args, ctx):
    from hermes.twin import clone as clone_mod

    twin = _twin(ctx)
    if not twin.is_sealed():
        return "ERROR: no sealed twin for this project."
    r = clone_mod.reground(
        twin, twin.source, args["path"],
        method=(args.get("method") or "GET"),
        query=args.get("query", ""), body=args.get("body"),
    )
    status = r["status"]
    if status == "accurate":
        return ("the twin matches the live target for this request — the twin is "
                "accurate here, so a mismatch is in your solution, not the twin.")
    if status == "corrected":
        return (f"the twin had drifted — corrected it to the live value.\n"
                f"old: HTTP {r['old'][0]}  {r['old'][1][:300]}\n"
                f"new: HTTP {r['new'][0]}  {r['new'][1][:300]}\n"
                "re-check your solution against the new value.")
    if status == "added":
        return f"the twin had no sample for this — added the live one (HTTP {r['new'][0]})."
    return f"ERROR: could not reach the target to re-ground: {r.get('detail')}"


# Never touch the live target — always registered once sealed.
ALWAYS_TOOLS = [twin_request, twin_map, twin_stack]
# Do reach the live target (read-only, GET/HEAD only). Gated behind
# `build_live_touch` in build_registry() — off by default, so a sealed build
# has zero path to the live target unless the operator turns it back on.
LIVE_TOUCH_TOOLS = [twin_expand, twin_reground]
TOOLS = ALWAYS_TOOLS + LIVE_TOUCH_TOOLS
