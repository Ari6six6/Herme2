"""Toolbox: extract text from a PDF in the workspace — LOCAL, never fetches.

Pairs with `download_file`: pull a PDF onto the phone without inflating context
(it lands as bytes on disk, not in the model's window), then read it here. Like
`html_to_text`, this only ever touches a project file (`src`) — no URL, no
network — so it is a pure local transform that needs no seat on the taint rail.

PDF parsing has no stdlib path, so this uses `pypdf` (pure Python, installs on
aarch64/Termux). The import is optional: with pypdf absent the tool returns a
clear ERROR telling the operator what to install, rather than failing the run.
"""

TOOL = {
    "name": "pdf_text",
    "description": (
        "Extract text from a PDF file in the project (src). LOCAL only — reads "
        "a file already on disk (download_file it first), never a URL. Optional "
        "'pages' like '1' or '2-5' limits which pages. Optional 'dest' writes "
        "the text to a workspace path instead of returning it. Needs pypdf "
        "(pip install pypdf)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "src": {"type": "string", "description": "project path to a .pdf file"},
            "pages": {"type": "string", "description": "page or range, 1-based: '3' or '2-5'"},
            "dest": {"type": "string", "description": "workspace path to write the text to"},
        },
        "required": ["src"],
    },
}

PREVIEW_LIMIT = 6000


def _parse_pages(spec: str, total: int):
    """Return a 0-based list of page indices, or (None, error_str)."""
    spec = str(spec).strip()
    if "-" in spec:
        lo_s, _, hi_s = spec.partition("-")
        lo, hi = lo_s.strip(), hi_s.strip()
    else:
        lo = hi = spec
    if not (lo.isdigit() and hi.isdigit()):
        return None, f"ERROR: bad pages '{spec}' — use '3' or '2-5' (1-based)."
    lo_i, hi_i = int(lo), int(hi)
    if lo_i < 1 or hi_i < lo_i:
        return None, f"ERROR: bad page range '{spec}'."
    if lo_i > total:
        return None, f"ERROR: page {lo_i} out of range (PDF has {total} page(s))."
    hi_i = min(hi_i, total)
    return list(range(lo_i - 1, hi_i)), None


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in

    try:
        import pypdf
    except ImportError:
        return ("ERROR: pdf_text needs the 'pypdf' package. Ask the operator to "
                "install it: pip install pypdf")
    except Exception as e:
        # pypdf is present but its import blew up — e.g. a half-installed
        # optional crypto backend. Degrade cleanly instead of failing the run.
        return (f"ERROR: pypdf failed to import ({type(e).__name__}: {e}). "
                "Try reinstalling: pip install --force-reinstall pypdf")

    try:
        path = resolve_in(ctx.project.root, args["src"])
    except PathDenied as e:
        return f"DENIED: {e}"
    if not path.is_file():
        return f"ERROR: no such file: {args['src']}"

    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # try the empty owner password
            except Exception:
                return "ERROR: PDF is encrypted and needs a password."
        total = len(reader.pages)
    except Exception as e:
        return f"ERROR: could not read PDF ({type(e).__name__}: {e})."

    if total == 0:
        return "ERROR: PDF has no pages."

    if args.get("pages"):
        idx, err = _parse_pages(args["pages"], total)
        if err:
            return err
    else:
        idx = list(range(total))

    chunks = []
    for i in idx:
        try:
            chunks.append(reader.pages[i].extract_text() or "")
        except Exception as e:
            chunks.append(f"[page {i + 1}: extraction failed ({type(e).__name__})]")
    text = "\n\n".join(c.strip() for c in chunks).strip()

    if not text:
        return (f"no extractable text found in {args['src']} (it may be scanned "
                "images — this tool does not OCR).")

    if args.get("dest"):
        try:
            dest = resolve_in(ctx.project.workspace_dir, args["dest"])
        except PathDenied:
            return "DENIED: dest must stay inside workspace/"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text if text.endswith("\n") else text + "\n")
        rel = dest.relative_to(ctx.project.workspace_dir)
        return f"wrote {len(text)} chars from {len(idx)} page(s) to workspace/{rel}"

    header = f"{len(idx)} of {total} page(s):\n"
    if len(text) > PREVIEW_LIMIT:
        return (header + text[:PREVIEW_LIMIT] +
                f"\n[...{len(text) - PREVIEW_LIMIT} more chars — re-run with a "
                "'dest' to capture the whole thing.]")
    return header + text
