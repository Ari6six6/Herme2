"""Builder tools: stand up the twin during the recon/build phase.

These are the recon/builder agent's hands for assembling the twin — record
ground-truth samples of how the target responds, pull a batch at once, and seal
the twin when it's proven accurate. They register only while the twin is still
OPEN; sealing ends the phase and hands off to the build agents.
"""

from __future__ import annotations

from hermes.tools._common import twin_for as _twin
from hermes.tools.base import obj_schema, tool
from hermes.twin.model import Exchange


@tool(
    "twin_record",
    "Record one ground-truth sample into the twin: a real request and the real "
    "response the target gave for it (fetch it with http_request first). These "
    "samples are what later runs prove their solution against.",
    obj_schema(
        {
            "path": {"type": "string", "description": "request path, e.g. /users/1"},
            "status": {"type": "integer", "description": "real HTTP status, e.g. 200"},
            "response_body": {"type": "string", "description": "the real response body"},
            "method": {"type": "string", "description": "GET (default), POST, ..."},
            "query": {"type": "string", "description": "query string (optional)"},
            "request_body": {"type": "string", "description": "request body (optional)"},
            "content_type": {"type": "string", "description": "e.g. application/json"},
        },
        ["path", "status", "response_body"],
    ),
)
def twin_record(args, ctx):
    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    if twin.is_sealed():
        return "ERROR: the twin is sealed. Recon/build is over for this twin."
    twin.add_exchange(Exchange(
        method=(args.get("method") or "GET").upper(),
        path=args["path"], query=args.get("query", ""),
        status=int(args["status"]), response_body=str(args["response_body"]),
        request_body=args.get("request_body"),
        content_type=str(args.get("content_type", "")), source="builder",
    ))
    return f"recorded {(args.get('method') or 'GET').upper()} {args['path']} -> {args['status']}. " \
           f"twin now has {len(twin.exchanges())} sample(s)."


@tool(
    "twin_clone",
    "Pull a batch of ground-truth samples from the target at once: crawl it from "
    "the source URL (and any seed paths you give) and record what it returns. Read "
    "requests only. Use it to bootstrap or widen the twin quickly; refine with "
    "twin_record / http_request.",
    obj_schema(
        {"seeds": {"type": "array", "items": {"type": "string"},
                   "description": "extra paths to start from, e.g. ['/api', '/users']"}},
        [],
    ),
)
def twin_clone(args, ctx):
    from hermes.twin import clone as clone_mod

    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    if twin.is_sealed():
        return "ERROR: the twin is sealed."
    seeds = [str(s) for s in (args.get("seeds") or []) if str(s).strip()]
    report = clone_mod.clone(
        twin, twin.source, seeds=seeds or None, seal=False,
        max_exchanges=ctx.cfg.get("twin_clone_max", 200),
        delay=ctx.cfg.get("twin_clone_delay", 0.5),
        max_depth=ctx.cfg.get("twin_clone_depth", 2),
    )
    return (f"cloned {report['recorded']} request(s) ({report['errors']} error(s)); "
            f"twin now has {report['exchanges']} sample(s). Stack: "
            f"{report['stack'].get('product') or report['stack'].get('kind', 'unknown')}.")


@tool(
    "twin_diff",
    "Differential check: fetch the LIVE target for the paths the twin knows (or "
    "paths you name) and compare to the twin's current samples. Reports where the "
    "twin matches, where it has drifted, and where it's missing data — the gap to "
    "close this pass. Drive it to all-match before sealing.",
    obj_schema(
        {"paths": {"type": "array", "items": {"type": "string"},
                   "description": "paths to diff (default: every path the twin knows)"}},
        [],
    ),
)
def twin_diff(args, ctx):
    from urllib.parse import urljoin

    from hermes.twin import clone as clone_mod

    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    given = [str(p) for p in (args.get("paths") or []) if str(p).strip()]
    paths = given or sorted({ex.path for ex in twin.exchanges() if ex.method == "GET"})
    if not paths:
        return "no paths to diff yet — record or clone some samples first."
    root = twin.source if twin.source.endswith("/") else twin.source + "/"
    cap = ctx.cfg.get("twin_clone_max", 200)
    matched, drifted, missing, errors = [], [], [], []
    for path in paths[:cap]:
        recorded = twin.respond("GET", path)
        try:
            status, _, text = clone_mod._httpx_fetch("GET", urljoin(root, path.lstrip("/")), None)
        except Exception as e:
            errors.append(f"  {path}: live fetch failed ({type(e).__name__})")
            continue
        if recorded is None:
            missing.append(f"  {path}: live={status} ({len(text)}B), not in twin — record it")
        elif recorded.status == status and (recorded.response_body or "") == (text or ""):
            matched.append(path)
        else:
            drifted.append(f"  {path}: twin={recorded.status}/{len(recorded.response_body or '')}B "
                           f"live={status}/{len(text)}B — re-record")
    div = len(drifted) + len(missing)
    head = (f"twin_diff: {len(matched)} match, {len(drifted)} drifted, "
            f"{len(missing)} missing, {len(errors)} error(s). "
            + ("ALL MATCH — the twin tracks the target." if div == 0 and not errors
               else f"{div} divergence(s) to close."))
    body = "\n".join(drifted + missing + errors)
    return head + ("\n" + body if body else "")


@tool(
    "build_run",
    "Run a reconstruction step INSIDE the twin container AND capture it into the "
    "twin's recipe when it succeeds — so a later pass or a fresh box can rebuild "
    "the stack by replaying the recipe instead of you re-deriving every step (that "
    "derivation is the expensive part). The container has network, so installs and "
    "clones (apt, pip, npm, git clone, ...) run here. Make the final serving step "
    "bind 0.0.0.0:$TWIN_PORT and run in the background (nohup ... &).",
    obj_schema(
        {"command": {"type": "string"},
         "note": {"type": "string", "description": "what this step does (optional)"},
         "timeout": {"type": "integer", "description": "seconds (optional)"}},
        ["command"],
    ),
)
def build_run(args, ctx):
    from hermes.sandbox.provision import SandboxError
    from hermes.twin import deploy

    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    if twin.is_sealed():
        return "ERROR: the twin is sealed — building is over for this twin."
    if ctx.sandbox is None:
        return "ERROR: no sandbox executor available to run the build container."
    port = ctx.cfg.get("twin_port", 8900)
    base = ctx.cfg.get("twin_base_image", deploy.DEFAULT_BASE_IMAGE)
    try:
        runtime, name = deploy.ensure_build_container(ctx.sandbox, twin, port, base)
    except SandboxError as e:
        return f"ERROR: {e}"
    timeout = min(int(args.get("timeout") or 600), 1800)
    rc, out, err = deploy.exec_step(ctx.sandbox, name, args["command"], port, runtime, timeout)
    body = ((out or "") + (("\n[stderr]\n" + err) if err else "")).strip() or "(no output)"
    if rc == 0:
        twin.add_step(args["command"], str(args.get("note", "")))
        return f"[recorded to recipe — {len(twin.recipe())} step(s)] exit 0\n{body}"
    return f"[not recorded — exit {rc}, the step didn't cleanly succeed]\n{body}"


@tool(
    "build_recipe",
    "Show the reconstruction recipe captured so far: the ordered steps that stand "
    "up the twin. On a fresh box, replay these to restore the stack quickly "
    "instead of figuring them out again.",
    obj_schema({}, []),
)
def build_recipe(args, ctx):
    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    steps = twin.recipe()
    if not steps:
        return "recipe empty — capture steps with build_run as you reconstruct the stack."
    lines = [f"reconstruction recipe ({len(steps)} step(s)):"]
    for i, s in enumerate(steps, 1):
        note = f"   # {s['note']}" if s.get("note") else ""
        lines.append(f"  {i}. {s['cmd']}{note}")
    return "\n".join(lines)


@tool(
    "twin_seal",
    "Seal the twin: freeze it and open the build phase. Do this ONLY once you've "
    "verified the twin behaves like the target — an inaccurate twin poisons "
    "everything built on it. Needs at least one recorded sample.",
    obj_schema({}, []),
)
def twin_seal(args, ctx):
    twin = _twin(ctx)
    if not twin.exists():
        return "ERROR: no twin for this project."
    if twin.is_sealed():
        return "the twin is already sealed."
    if not twin.exchanges():
        return ("ERROR: the twin has no samples yet — record real responses with "
                "twin_record or twin_clone before sealing.")
    twin.seal()
    return (f"twin sealed with {len(twin.exchanges())} sample(s). The build phase "
            "is open — the next run builds the solution against this frozen twin.")


TOOLS = [twin_record, twin_clone, twin_diff, build_run, build_recipe, twin_seal]
