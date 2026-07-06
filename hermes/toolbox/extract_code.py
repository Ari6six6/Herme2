"""Toolbox: pull just the code out of a web page or doc.

When the agent hunts a code solution online, `http_request`'s readable-text
mode works against it: it SKIPS `<script>` entirely and flattens `<pre>`/
`<code>` into the surrounding prose, losing newlines and indentation. The code
is exactly the part it needed.

This fetches a URL (on the VPS — the only place with internet), or takes raw
`text`, or reads a project file, and returns ONLY the code blocks — verbatim,
de-entitied — from HTML (`<pre>`, `<code>`, `<script>`) or markdown fences.
Optionally writes one block to the workspace.
"""

import html
import re
from html.parser import HTMLParser

TOOL = {
    "name": "extract_code",
    "description": (
        "Extract only the code from a web page or document — the part "
        "http_request drops (<script>) or mangles (<pre>/<code> flattened "
        "into prose). Source is one of: 'url' (fetched on the VPS), inline "
        "'text', or 'path' (a project file). Pulls HTML <pre>/<code>/<script> "
        "and markdown ``` fences, de-entitied and verbatim. Optional 'lang' "
        "filters by language hint; optional 'save' writes the chosen block "
        "(default 1) to a workspace path; 'index' picks which block to save."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "page to fetch on the VPS"},
            "text": {"type": "string", "description": "raw HTML/markdown to scan"},
            "path": {"type": "string", "description": "a project file to read"},
            "lang": {
                "type": "string",
                "description": "keep only blocks whose hint matches, e.g. 'js'",
            },
            "save": {
                "type": "string",
                "description": "workspace path to write the chosen block to",
            },
            "index": {
                "type": "integer",
                "description": "1-based block to save (default 1)",
            },
        },
        "required": [],
    },
}

UA = "Mozilla/5.0 (Linux; Android) HermesAgent/0.1"
MAX_FETCH_BYTES = 8 * 1024 * 1024

_CLASS_LANG_RE = re.compile(r"(?:language|lang|brush|highlight)[-:]([A-Za-z0-9+#]+)")
_FENCE_RE = re.compile(r"^[ \t]*(?:```+|~~~+)[ \t]*([^\n`~]*)\n(.*?)\n[ \t]*(?:```+|~~~+)", re.S | re.M)
_HTML_HINT_RE = re.compile(r"</[a-zA-Z]|<(?:pre|code|script|html|body|div)\b", re.I)


def _lang_from_attrs(attrs):
    for key, val in attrs:
        if not val:
            continue
        if key in ("class", "data-lang", "lang"):
            m = _CLASS_LANG_RE.search(val)
            if m:
                return m.group(1).lower()
            if key in ("data-lang", "lang"):
                return val.strip().lower()
    return None


class _CodeExtractor(HTMLParser):
    """Collect <pre>/<code>/<script> contents. Nesting (e.g. <pre><code>) is
    treated as one block so the same code isn't emitted twice."""

    CODE_TAGS = {"pre", "code", "script"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.blocks: list[tuple[str | None, str]] = []
        self._depth = 0
        self._buf: list[str] = []
        self._lang: str | None = None
        self._root_tag: str | None = None

    def handle_starttag(self, tag, attrs):
        if tag in self.CODE_TAGS:
            if self._depth == 0:
                self._buf = []
                self._lang = _lang_from_attrs(attrs)
                self._root_tag = tag
            elif self._lang is None:
                self._lang = _lang_from_attrs(attrs)
            self._depth += 1

    def handle_endtag(self, tag):
        if tag in self.CODE_TAGS and self._depth:
            self._depth -= 1
            if self._depth == 0:
                code = "".join(self._buf)
                # HTMLParser leaves <script> contents (CDATA) entity-encoded;
                # <pre>/<code> are already decoded by convert_charrefs.
                if self._root_tag == "script":
                    code = html.unescape(code)
                code = code.strip("\n")
                if code.strip():
                    self.blocks.append((self._lang, code))
                self._buf = []
                self._lang = None
                self._root_tag = None

    def handle_data(self, data):
        if self._depth:
            self._buf.append(data)


def _extract_html(html: str) -> list[tuple[str | None, str]]:
    parser = _CodeExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    return parser.blocks


def _extract_markdown(text: str) -> list[tuple[str | None, str]]:
    out = []
    for info, body in _FENCE_RE.findall(text):
        if body.strip():
            out.append((info.strip().lower() or None, body.strip("\n")))
    return out


def _matches_lang(block_lang, wanted):
    if not wanted:
        return True
    wanted = wanted.strip().lower()
    if not block_lang:
        return False
    return wanted in block_lang or block_lang in wanted


def run(args, ctx):
    import httpx

    from hermes.paths import PathDenied, resolve_in

    sources = [k for k in ("url", "text", "path") if args.get(k)]
    if not sources:
        return "ERROR: provide one of 'url', 'text', or 'path'."
    if len(sources) > 1:
        return f"ERROR: give exactly one source, got {sources}."

    if args.get("url"):
        url = args["url"]
        if not url.startswith(("http://", "https://")):
            return "ERROR: url must start with http:// or https://"
        try:
            resp = httpx.get(
                url, headers={"User-Agent": UA}, timeout=45, follow_redirects=True
            )
            resp.raise_for_status()
        except httpx.HTTPError as e:
            return f"ERROR: {type(e).__name__}: {e}"
        if len(resp.content) > MAX_FETCH_BYTES:
            return "ERROR: page exceeds 8MB — download_file it instead."
        raw = resp.text
    elif args.get("path"):
        try:
            path = resolve_in(ctx.project.root, args["path"])
        except PathDenied as e:
            return f"DENIED: {e}"
        try:
            raw = path.read_text(errors="replace")
        except OSError as e:
            return f"ERROR: {e}"
    else:
        raw = args["text"]

    if _HTML_HINT_RE.search(raw):
        blocks = _extract_html(raw) or _extract_markdown(raw)
    else:
        blocks = _extract_markdown(raw) or _extract_html(raw)

    if args.get("lang"):
        blocks = [b for b in blocks if _matches_lang(b[0], args["lang"])]

    if not blocks:
        hint = " matching lang" if args.get("lang") else ""
        return f"no code blocks{hint} found in the source."

    if args.get("save"):
        idx = int(args.get("index", 1))
        if not 1 <= idx <= len(blocks):
            return f"ERROR: index {idx} out of range (1..{len(blocks)})."
        try:
            dest = resolve_in(ctx.project.workspace_dir, args["save"])
        except PathDenied:
            return "DENIED: save path must stay inside workspace/"
        dest.parent.mkdir(parents=True, exist_ok=True)
        code = blocks[idx - 1][1]
        dest.write_text(code if code.endswith("\n") else code + "\n")
        rel = dest.relative_to(ctx.project.workspace_dir)
        return f"wrote block {idx} ({len(code)} chars) to workspace/{rel}"

    parts = [f"{len(blocks)} code block(s):"]
    for i, (lang, code) in enumerate(blocks, 1):
        tag = f" [{lang}]" if lang else ""
        parts.append(f"\n--- block {i}{tag} ({len(code)} chars) ---\n{code}")
    return "\n".join(parts)
