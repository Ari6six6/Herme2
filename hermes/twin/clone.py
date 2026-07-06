"""The clone engine: point it at a URL, it builds a comprehensive model.

This is the ONE component that touches the live target, and it is operator-driven
(a CLI action on the VPS), never an agent tool — the agent never decides to go
poke a live service. It is benign by construction: read-only methods only, a hard
cap on requests, a polite delay. It gathers as much as it responsibly can — the
API spec if the service publishes one, common discovery endpoints, and a
same-origin crawl of whatever it finds — then seals the model. The crawl follows
pages and endpoints, not static assets (images, fonts, media, stylesheets,
bundles): we want to know what apps and services run, not what the page looks
like, and the stack fingerprint never reads those bodies.

`fetch` is injected so the gathering logic is fully testable without a network.
"""

from __future__ import annotations

import json
import re
import time
from urllib.parse import urljoin, urlsplit

from hermes.twin.model import Exchange, TwinModel

DISCOVERY_PATHS = (
    "/openapi.json", "/swagger.json", "/api/openapi.json", "/v1/openapi.json",
    "/.well-known/openapi.json", "/swagger/v1/swagger.json",
    "/robots.txt", "/sitemap.xml", "/health", "/healthz", "/version", "/api",
)
SPEC_PATHS = DISCOVERY_PATHS[:6]
SAFE_METHODS = ("GET", "HEAD")
UA = "Mozilla/5.0 (Linux; Android) HermesAgent/0.1 (benign clone)"

_HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_URLISH_RE = re.compile(r'"(/[A-Za-z0-9_\-./]+)"')

# Static assets — images, fonts, media, stylesheets, bundles, archives. We're
# after the stack and the endpoints (what apps and services run), not the page's
# appearance. The fingerprint only reads headers, cookies, paths and HTML/JSON
# bodies, so following these wastes requests and bloats the twin with binary
# bodies that nothing downstream uses. The crawl skips them.
_ASSET_EXTS = frozenset((
    # images
    "png", "jpg", "jpeg", "gif", "webp", "svg", "ico", "bmp", "avif", "tiff",
    # fonts
    "woff", "woff2", "ttf", "otf", "eot",
    # audio / video
    "mp4", "webm", "ogg", "mp3", "wav", "avi", "mov", "m4a", "flac",
    # styles / client bundles / sourcemaps
    "css", "js", "mjs", "map",
    # downloadable blobs
    "pdf", "zip", "gz", "tgz", "tar", "rar", "7z", "dmg", "exe", "bin",
))


def _is_asset(path: str) -> bool:
    """True for paths that point at a static asset rather than a page/endpoint."""
    last = urlsplit(path).path.rsplit("/", 1)[-1]
    ext = last.rsplit(".", 1)
    return len(ext) == 2 and ext[1].lower() in _ASSET_EXTS

# Response headers worth keeping: enough to fingerprint the stack, not the noise.
_KEEP_HEADERS = ("content-type", "content-length", "etag", "server",
                 "x-powered-by", "x-aspnet-version", "x-generator", "set-cookie",
                 "via")


def _keep(resp_headers: dict) -> dict:
    return {k: v for k, v in resp_headers.items() if k.lower() in _KEEP_HEADERS}


def _httpx_fetch(method, url, headers=None, body=None, timeout=45):
    """Real network read, on the VPS. Returns (status, headers, text)."""
    import httpx

    hdrs = {"User-Agent": UA}
    if isinstance(headers, dict):
        hdrs.update({str(k): str(v) for k, v in headers.items()})
    resp = httpx.request(method, url, headers=hdrs, content=body,
                         timeout=timeout, follow_redirects=True)
    return resp.status_code, dict(resp.headers), resp.text


def _same_origin(base, candidate):
    b, c = urlsplit(base), urlsplit(urljoin(base, candidate))
    if c.scheme not in ("http", "https"):
        return None
    if (c.netloc or b.netloc) != b.netloc:
        return None
    return c.path + (f"?{c.query}" if c.query else "")


def _spec_get_paths(spec):
    """Pull GET paths with no required path params out of an OpenAPI doc, plus
    examples for parameterized paths when the spec provides them."""
    out = []
    paths = spec.get("paths", {}) if isinstance(spec, dict) else {}
    for raw_path, methods in paths.items():
        if not isinstance(methods, dict) or "get" not in methods:
            continue
        params = methods["get"].get("parameters", []) or []
        path_params = [p for p in params if isinstance(p, dict) and p.get("in") == "path"]
        filled = raw_path
        ok = True
        for p in path_params:
            example = p.get("example")
            if example is None:
                example = (p.get("schema") or {}).get("example")
            if example is None:
                ok = False
                break
            filled = filled.replace("{" + str(p.get("name")) + "}", str(example))
        if ok and "{" not in filled:
            out.append(filled)
    return out


def _links(base_url, status, ctype, text):
    """Same-origin paths worth following from a response."""
    found = []
    if "html" in ctype:
        found = _HREF_RE.findall(text)
    elif "json" in ctype or text.lstrip()[:1] in ("{", "["):
        found = _URLISH_RE.findall(text)
    out = []
    for href in found:
        norm = _same_origin(base_url, href)
        if norm and not _is_asset(norm):
            out.append(norm)
    return out


def clone(model: TwinModel, base_url: str, *, seeds=None, fetch=_httpx_fetch,
          max_exchanges: int = 200, delay: float = 0.5, max_depth: int = 2,
          include_discovery: bool = True, seal: bool = True, on_event=None) -> dict:
    """Gather the target into `model`. `model` must be init'd and open. With
    seal=True the model is frozen at the end; with seal=False it stays open for
    the recon/builder agent to refine and seal itself. `on_event` is a progress
    hook."""
    def emit(kind, text):
        if on_event:
            on_event(kind, text)

    root = base_url if base_url.endswith("/") else base_url + "/"
    queued = []  # (path, depth)
    enqueued = set()

    def enqueue(path, depth):
        norm = path if path.startswith("/") else "/" + path
        if norm not in enqueued:
            enqueued.add(norm)
            queued.append((norm, depth))

    enqueue("/", 0)
    for s in (seeds or []):
        enqueue(s, 0)
    if include_discovery:
        for p in DISCOVERY_PATHS:
            enqueue(p, 1)

    recorded = errors = 0
    i = 0
    while queued and recorded < max_exchanges:
        path, depth = queued.pop(0)
        url = urljoin(root, path.lstrip("/"))
        if i > 0 and delay:
            time.sleep(delay)
        i += 1
        try:
            status, resp_headers, text = fetch("GET", url, None)
        except Exception as e:
            errors += 1
            emit("error", f"GET {path}: {type(e).__name__}: {e}")
            continue
        ctype = str(resp_headers.get("content-type", ""))
        model.add_exchange(Exchange(
            method="GET", path=urlsplit(path).path or "/",
            query=urlsplit(path).query, status=status, content_type=ctype,
            response_headers=_keep(resp_headers),
            response_body=text,
            source="spec" if path in SPEC_PATHS else "crawl",
        ))
        recorded += 1
        emit("exchange", f"GET {path} -> {status} ({len(text)}B)")

        # If this was an API spec, mine it for the whole documented surface.
        if path in SPEC_PATHS and "json" in ctype:
            try:
                spec = json.loads(text)
            except json.JSONDecodeError:
                spec = None
            if isinstance(spec, dict) and spec.get("paths"):
                model.store_spec(spec)
                added = 0
                for sp in _spec_get_paths(spec):
                    enqueue(sp, 1)
                    added += 1
                emit("spec", f"API spec found — enqueued {added} documented path(s)")

        if depth < max_depth:
            for link in _links(base_url, status, ctype, text):
                enqueue(link, depth + 1)

    # Fingerprint the stack so the builder knows whether to mirror behavior or
    # reconstruct the real software.
    from hermes.twin.recon import fingerprint

    stack = fingerprint(model.exchanges())
    model.store_stack(stack.to_dict())
    emit("stack", stack.summary())

    if seal:
        model.seal()
    report = {"recorded": recorded, "errors": errors,
              "exchanges": len(model.exchanges()), "stack": stack.to_dict(),
              "sealed": seal}
    state = "sealed" if seal else "seeded (open for the builder)"
    emit("done", f"twin {state}: {report['exchanges']} exchange(s), {errors} error(s)")
    return report


def expand(model: TwinModel, base_url: str, paths, *, fetch=_httpx_fetch,
           delay: float = 0.3, on_event=None) -> dict:
    """Grow a sealed model to cover specific misses — the benign clone layer the
    agent reaches for when the twin lacks a case. Read-only GETs, then re-seal."""
    def emit(kind, text):
        if on_event:
            on_event(kind, text)

    was_sealed = model.is_sealed()
    if was_sealed:
        model.unseal()
    root = base_url if base_url.endswith("/") else base_url + "/"
    added = errors = 0
    for j, path in enumerate(paths):
        norm = path if path.startswith("/") else "/" + path
        if j > 0 and delay:
            time.sleep(delay)
        try:
            status, resp_headers, text = fetch("GET", urljoin(root, norm.lstrip("/")), None)
        except Exception as e:
            errors += 1
            emit("error", f"GET {norm}: {type(e).__name__}: {e}")
            continue
        model.add_exchange(Exchange(
            method="GET", path=urlsplit(norm).path or "/", query=urlsplit(norm).query,
            status=status, content_type=str(resp_headers.get("content-type", "")),
            response_headers=_keep(resp_headers), response_body=text, source="expand",
        ))
        added += 1
        emit("exchange", f"GET {norm} -> {status} ({len(text)}B)")
    if was_sealed:
        model.seal()
    return {"added": added, "errors": errors}


def reground(model: TwinModel, base_url: str, path: str, *, method: str = "GET",
             query: str = "", body: str | None = None, fetch=None) -> dict:
    """Re-check one request against the live target and correct the twin if its
    stored sample has drifted. This is the live-accuracy loop: keep the twin equal
    to the truth for the cases a build actually leans on, on demand. Read-only.

    Returns {"status": "accurate" | "corrected" | "added" | "error", ...}.
    """
    fetch = fetch or _httpx_fetch
    method = (method or "GET").upper()
    if method not in ("GET", "HEAD"):
        return {"status": "error", "detail": f"{method} is not a read-only check"}
    old = model.respond(method, path, query, body)
    root = base_url if base_url.endswith("/") else base_url + "/"
    url = urljoin(root, path.lstrip("/"))
    if query:
        url = f"{url}?{query}"
    try:
        status, resp_headers, text = fetch(method, url, None)
    except Exception as e:
        return {"status": "error", "detail": f"{type(e).__name__}: {e}"}

    new_ex = Exchange(
        method=method, path=urlsplit(path).path or "/", query=urlsplit(path).query or query,
        status=status, content_type=str(resp_headers.get("content-type", "")),
        response_headers=_keep(resp_headers), response_body=text,
        request_body=body, source="reground",
    )
    drifted = old is not None and (
        old.status != status or (old.response_body or "") != (text or "")
    )

    was_sealed = model.is_sealed()
    if old is None:
        if was_sealed:
            model.unseal()
        model.add_exchange(new_ex)
        if was_sealed:
            model.seal()
        return {"status": "added", "new": (status, text)}
    if not drifted:
        return {"status": "accurate", "value": (old.status, len(old.response_body or ""))}
    if was_sealed:
        model.unseal()
    model.upsert_exchange(new_ex)
    if was_sealed:
        model.seal()
    return {"status": "corrected",
            "old": (old.status, old.response_body or ""),
            "new": (status, text)}
