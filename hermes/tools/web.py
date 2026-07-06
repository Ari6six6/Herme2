"""Web tools — these run ON THE VPS, by design.

The operator's hard rule: all internet goes through the VPS, never the GPU
box. GET/HEAD are free; anything that changes state on the web (POST etc.)
is operator-confirmed.
"""

from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from hermes.tools.base import obj_schema, tool

MAX_BODY_CHARS = 20000
UA = "Mozilla/5.0 (Linux; Android) HermesAgent/0.1"


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "template"}

    def __init__(self):
        super().__init__()
        self.chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if not self._skip_depth and data.strip():
            self.chunks.append(data.strip())


def html_to_text(html: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(html)
    except Exception:
        pass
    return re.sub(r"\n{3,}", "\n\n", "\n".join(parser.chunks))


@tool(
    "http_request",
    "Make an HTTP request FROM THE VPS. GET/HEAD run freely; other methods "
    "(POST, PUT, ...) ask the operator first. HTML responses are converted "
    "to readable text unless raw=true.",
    obj_schema(
        {
            "url": {"type": "string"},
            "method": {"type": "string", "description": "GET (default), POST, ..."},
            "headers": {"type": "object", "description": "extra headers (optional)"},
            "body": {"type": "string", "description": "request body (optional)"},
            "raw": {"type": "boolean", "description": "return raw body, default false"},
        },
        ["url"],
    ),
)
def http_request(args, ctx):
    url = args["url"]
    method = (args.get("method") or "GET").upper()
    if not url.startswith(("http://", "https://")):
        return "ERROR: url must start with http:// or https://"
    if method not in ("GET", "HEAD"):
        detail = f"  {method} {url}"
        if args.get("body"):
            detail += f"\n  body: {str(args['body'])[:500]}"
        if not ctx.confirm("agent wants to make a state-changing web request:", detail=detail):
            return "DENIED by operator."
    headers = {"User-Agent": UA}
    if isinstance(args.get("headers"), dict):
        headers.update({str(k): str(v) for k, v in args["headers"].items()})
    try:
        resp = httpx.request(
            method,
            url,
            headers=headers,
            content=args.get("body"),
            timeout=45,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return f"ERROR: {type(e).__name__}: {e}"
    ctype = resp.headers.get("content-type", "")
    body = resp.text
    if "html" in ctype and not args.get("raw"):
        body = html_to_text(body)
    if len(body) > MAX_BODY_CHARS:
        body = body[:MAX_BODY_CHARS] + (
            f"\n[...truncated: showing {MAX_BODY_CHARS} of {len(body)} chars — "
            f"the page continues beyond this point.]"
        )
    return f"HTTP {resp.status_code} {ctype}\nfinal url: {resp.url}\n\n{body}"


_RESULT_RE = re.compile(
    r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.S,
)
_SNIPPET_RE = re.compile(
    r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.S
)


def _clean_url(href: str) -> str:
    # DDG wraps results: //duckduckgo.com/l/?uddg=<encoded>&...
    if "uddg=" in href:
        qs = parse_qs(urlparse(href).query)
        if qs.get("uddg"):
            return unquote(qs["uddg"][0])
    return href


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html).strip()


@tool(
    "web_search",
    "Search the web (DuckDuckGo) FROM THE VPS. Returns titles, urls and "
    "snippets. Follow up with http_request to read a page.",
    obj_schema(
        {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "description": "default 6, max 10"},
        },
        ["query"],
    ),
)
def web_search(args, ctx):
    query = args["query"]
    limit = min(int(args.get("max_results", 6)), 10)
    try:
        resp = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": UA},
            timeout=30,
            follow_redirects=True,
        )
    except httpx.HTTPError as e:
        return f"ERROR: {type(e).__name__}: {e}"
    links = _RESULT_RE.findall(resp.text)[:limit]
    snippets = [_strip_tags(s) for s in _SNIPPET_RE.findall(resp.text)[:limit]]
    if not links:
        return f"no results (HTTP {resp.status_code}) for: {query}"
    lines = []
    for i, (href, title) in enumerate(links):
        snippet = snippets[i] if i < len(snippets) else ""
        lines.append(f"{i + 1}. {_strip_tags(title)}\n   {_clean_url(href)}\n   {snippet}")
    return "\n".join(lines)


TOOLS = [http_request, web_search]
