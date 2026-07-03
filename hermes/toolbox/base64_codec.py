"""Toolbox: encode/decode base64 — binary-safe, tolerant on decode.

Code found online arrives base64-wrapped often enough (data: URIs, embedded
payloads, gist blobs, JWT segments) that eyeballing it doesn't work. This
decodes or encodes, taking input inline ('text') or from a project file
('src'), and returning the result or writing it to the workspace ('dest').
Decode tolerates url-safe alphabets, embedded whitespace, and missing padding.
"""

import base64
import binascii

TOOL = {
    "name": "base64_codec",
    "description": (
        "Encode or decode base64. 'action' is 'decode' or 'encode'. Input "
        "from inline 'text' or a project file 'src'. Result is returned "
        "(decoded bytes shown as UTF-8 text when valid), or written to a "
        "workspace path 'dest' for binary or large output. Decode tolerates "
        "url-safe alphabet, whitespace, and missing padding."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["decode", "encode"]},
            "text": {"type": "string", "description": "inline input"},
            "src": {"type": "string", "description": "project file to read as input"},
            "dest": {
                "type": "string",
                "description": "workspace path to write output to (binary-safe)",
            },
        },
        "required": ["action"],
    },
}

PREVIEW_LIMIT = 4000


def _read_input(args, ctx):
    """Return (data_bytes, error_str). Exactly one of text/src must be given."""
    from hermes.paths import PathDenied, resolve_in

    have = [k for k in ("text", "src") if args.get(k) is not None]
    if not have:
        return None, "ERROR: provide 'text' or 'src' as input."
    if len(have) > 1:
        return None, "ERROR: give only one of 'text' or 'src'."
    if args.get("src"):
        try:
            path = resolve_in(ctx.project.root, args["src"])
        except PathDenied as e:
            return None, f"DENIED: {e}"
        try:
            return path.read_bytes(), None
        except OSError as e:
            return None, f"ERROR: {e}"
    return str(args["text"]).encode("utf-8"), None


def _decode_tolerant(data: bytes) -> bytes:
    cleaned = bytes(c for c in data if not chr(c).isspace())
    cleaned = cleaned.replace(b"-", b"+").replace(b"_", b"/")
    pad = (-len(cleaned)) % 4
    cleaned += b"=" * pad
    return base64.b64decode(cleaned, validate=True)


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in

    action = args.get("action")
    if action not in ("decode", "encode"):
        return "ERROR: action must be 'decode' or 'encode'."

    data, err = _read_input(args, ctx)
    if err:
        return err

    if action == "encode":
        out_bytes = base64.b64encode(data)
    else:
        try:
            out_bytes = _decode_tolerant(data)
        except (binascii.Error, ValueError) as e:
            return f"ERROR: not valid base64: {e}"

    if args.get("dest"):
        try:
            dest = resolve_in(ctx.project.workspace_dir, args["dest"])
        except PathDenied:
            return "DENIED: dest must stay inside workspace/"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(out_bytes)
        rel = dest.relative_to(ctx.project.workspace_dir)
        return f"wrote {len(out_bytes)} bytes to workspace/{rel}"

    if action == "encode":
        text = out_bytes.decode("ascii")
    else:
        try:
            text = out_bytes.decode("utf-8")
        except UnicodeDecodeError:
            return (
                f"decoded {len(out_bytes)} bytes of binary (not UTF-8 text) — "
                "re-run with a 'dest' to save it to the workspace."
            )
    if len(text) > PREVIEW_LIMIT:
        return (
            f"{text[:PREVIEW_LIMIT]}\n[...{len(text) - PREVIEW_LIMIT} more chars — "
            "re-run with a 'dest' to capture the whole thing.]"
        )
    return text
