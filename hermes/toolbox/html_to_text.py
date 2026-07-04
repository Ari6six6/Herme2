"""Toolbox: turn HTML into readable plain text — LOCAL, never fetches.

`http_request` and `download_file` bring pages in; this makes them *readable*.
Raw HTML is token-thick noise for a small model — tags, scripts, styles,
inline CSS — and feeding it whole burns context and triggers the tool-error /
abort patterns the metrics track. This strips it down to the text a human would
read: scripts/styles dropped, block tags become line breaks, entities decoded.

By design it takes only a project file (`src`) or an inline string (`text`) —
NO url. It transforms bytes already on disk (or already fetched into context);
it is a pure local function, so it adds no network ingress and needs no seat on
the taint rail. Fetch with `http_request`/`download_file` first, then extract.
"""

import re
from html.parser import HTMLParser

TOOL = {
    "name": "html_to_text",
    "description": (
        "Extract readable plain text from HTML — drops <script>/<style>/tags "
        "so a page becomes text a small model can actually read. Input is a "
        "project file 'src' OR inline 'text' (never a URL — fetch first with "
        "http_request/download_file). Optional 'dest' writes the text to a "
        "workspace path instead of returning it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "project file (HTML) to read"},
            "text": {"type": "string", "description": "inline HTML string"},
            "dest": {"type": "string", "description": "workspace path to write the text to"},
        },
        "required": [],
    },
}

PREVIEW_LIMIT = 6000


class _TextExtractor(HTMLParser):
    """Flatten HTML to text: skip script/style content, turn block-level tags
    into line breaks, keep list items as `- ` bullets."""

    SKIP = {"script", "style", "noscript", "head", "template", "svg", "iframe"}
    BLOCK = {
        "p", "div", "section", "article", "header", "footer", "main", "aside",
        "nav", "ul", "ol", "table", "tr", "h1", "h2", "h3", "h4", "h5", "h6",
        "blockquote", "pre", "hr", "form", "figure", "figcaption", "br",
    }

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_startendtag(self, tag, attrs):
        if tag in ("br", "hr"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            if self._skip:
                self._skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def _normalize(text: str) -> str:
    """Collapse intra-line whitespace and runs of blank lines."""
    lines = [re.sub(r"[ \t\u00a0\u200b]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def to_text(html: str) -> str:
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:
        pass  # HTMLParser is best-effort; keep whatever it collected
    return _normalize("".join(p.parts))


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in

    have = [k for k in ("src", "text") if args.get(k)]
    if not have:
        return "ERROR: provide 'src' (a project file) or inline 'text'."
    if len(have) > 1:
        return "ERROR: give only one of 'src' or 'text'."

    if args.get("src"):
        try:
            path = resolve_in(ctx.project.root, args["src"])
        except PathDenied as e:
            return f"DENIED: {e}"
        try:
            raw = path.read_text(errors="replace")
        except OSError as e:
            return f"ERROR: {e}"
    else:
        raw = str(args["text"])

    text = to_text(raw)
    if not text:
        return "no readable text found in the HTML."

    if args.get("dest"):
        try:
            dest = resolve_in(ctx.project.workspace_dir, args["dest"])
        except PathDenied:
            return "DENIED: dest must stay inside workspace/"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text if text.endswith("\n") else text + "\n")
        rel = dest.relative_to(ctx.project.workspace_dir)
        return f"wrote {len(text)} chars of text to workspace/{rel}"

    if len(text) > PREVIEW_LIMIT:
        return (
            f"{text[:PREVIEW_LIMIT]}\n[...{len(text) - PREVIEW_LIMIT} more chars — "
            "re-run with a 'dest' to capture the whole thing.]"
        )
    return text
