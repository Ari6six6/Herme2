"""Personas (feature 9): a cast of named archetypes, each a voice + a capacity.

A persona is one markdown file. Its FIRST non-empty line is the capacity
one-liner (what this persona is good at — it doubles as the routing-menu
entry); a few optional `tools:` / `aliases:` / `max_turns:` header lines
follow; the rest of the file is the voice the persona speaks in. Three
scopes, most specific wins (same shadowing as skills):

  - builtin  hermes/personas/*.md        (the shipped starter cast)
  - global   ~/.hermes/personas/*.md     (yours, across projects)
  - project  <project>/personas/*.md     (local to one project)

A persona's tool list only ever NARROWS a registry that was already built
with the normal gates — it is a scoping convenience, not a security
boundary. Confirm prompts, the taint rail and host-tool tiers are untouched;
a persona can never reach a tool its run couldn't.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from hermes.config import hermes_home

PERSONA_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_HEADER_RE = re.compile(r"^(tools|aliases|max_turns)\s*:\s*(.+)$")
# `hey owl, do X` / `hey owl: do X` / `hey owl do X` — and the terse `@owl do X`.
_INVOKE_RES = (
    re.compile(r"^hey\s+([A-Za-z0-9_-]+)\s*[,:]\s*(.+)$", re.I | re.S),
    re.compile(r"^hey\s+([A-Za-z0-9_-]+)\s+(.+)$", re.I | re.S),
    re.compile(r"^@([A-Za-z0-9_-]+)\s+(.+)$", re.S),
)
PERSONA_VERDICT_RE = re.compile(r"PERSONA:\s*([A-Za-z0-9_-]+)", re.I)
ROUTER_REQUEST_CHARS = 1200


@dataclass
class Persona:
    name: str
    description: str  # the capacity one-liner (first non-empty line)
    voice: str  # body markdown — how this persona speaks and works
    tools: list[str] | None  # allowlist; None = no narrowing
    aliases: list[str] = field(default_factory=list)
    max_turns: int | None = None
    scope: str = "builtin"  # "builtin" | "global" | "project"
    path: Path | None = None


def builtin_dir() -> Path:
    return Path(__file__).parent / "personas"


def global_dir() -> Path:
    return hermes_home() / "personas"


def parse(name: str, text: str, scope: str, path: Path | None,
          max_chars: int = 2000) -> Persona:
    """First non-empty line = capacity one-liner; then consecutive known
    `key: value` header lines (an unknown key-looking line starts the body, so
    prose like `Note: ...` is never eaten); the remainder is the voice."""
    lines = text.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    description = lines[i].lstrip("#").strip() if i < len(lines) else "(no description)"
    i += 1
    tools: list[str] | None = None
    aliases: list[str] = []
    max_turns: int | None = None
    while i < len(lines):
        m = _HEADER_RE.match(lines[i].strip())
        if not m:
            break
        key, value = m.group(1), m.group(2).strip()
        if key == "tools":
            tools = [t.strip() for t in value.split(",") if t.strip()]
        elif key == "aliases":
            aliases = [a.strip() for a in value.split(",") if a.strip()]
        elif key == "max_turns":
            try:
                max_turns = max(1, int(value))
            except ValueError:
                pass  # malformed cap is ignored, not fatal
        i += 1
    voice = "\n".join(lines[i:]).strip()
    if len(voice) > max_chars:
        voice = voice[:max_chars] + "\n[persona truncated]"
    return Persona(name, description, voice, tools, aliases, max_turns, scope, path)


def _load_dir(d: Path, scope: str, max_chars: int) -> dict[str, Persona]:
    out: dict[str, Persona] = {}
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.md")):
        name = path.stem
        if not PERSONA_NAME_RE.match(name):
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        out[name] = parse(name, text, scope, path, max_chars)
    return out


def load_all(project=None, max_chars: int = 2000,
             record_chars: int = 0) -> dict[str, Persona]:
    """Every visible persona by name: builtin < global < project (the more
    specific file shadows the shipped one of the same name). With
    `record_chars` > 0 each persona's service record in this operation rides
    appended to its voice — identity accretes from what the character has
    actually done (feature 12)."""
    cast = _load_dir(builtin_dir(), "builtin", max_chars)
    cast.update(_load_dir(global_dir(), "global", max_chars))
    if project is not None:
        cast.update(_load_dir(project.personas_dir, "project", max_chars))
        if record_chars > 0:
            for p in cast.values():
                tail = record_tail(project, p.name, record_chars)
                if tail:
                    p.voice += (
                        "\n\n### Your service record — this operation, most "
                        "recent last\n" + tail
                    )
    return cast


def load_cast(project, cfg) -> dict[str, Persona]:
    """The catalog as a run should see it: voices truncated to the config
    budget, service records riding when the feature is on."""
    record_chars = (int(cfg.get("record_prompt_chars", 1200))
                    if cfg.get("service_records", False) else 0)
    return load_all(project, cfg.get("persona_max_chars", 2000), record_chars)


# ---- service records (feature 12): organic identity -------------------------
def records_dir(project) -> Path:
    return project.root / "records"


def record_path(project, name: str) -> Path:
    return records_dir(project) / f"{name}.md"


def append_record(project, name: str, entry: str,
                  keep_chars: int = 12000) -> None:
    """One line onto a character's service jacket. The file stays bounded:
    when it outgrows `keep_chars` the oldest days are forgotten first —
    accretion with decay, like any identity."""
    if not PERSONA_NAME_RE.match(name):
        return
    d = records_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = record_path(project, name)
    text = (path.read_text() if path.exists() else "")
    text = (text.rstrip() + "\n" if text.strip() else "") + entry.strip() + "\n"
    if len(text) > max(1000, keep_chars):
        cut = text[-keep_chars:]
        nl = cut.find("\n")
        text = "(older days forgotten)\n" + (cut[nl + 1:] if nl >= 0 else cut)
    path.write_text(text)


def record_tail(project, name: str, max_chars: int) -> str:
    path = record_path(project, name)
    if not path.exists():
        return ""
    try:
        text = path.read_text().strip()
    except OSError:
        return ""
    if len(text) > max_chars:
        cut = text[-max_chars:]
        nl = cut.find("\n")
        text = "(...)\n" + (cut[nl + 1:] if nl >= 0 else cut)
    return text


def resolve(catalog: dict[str, Persona], name: str) -> Persona | None:
    """Name-or-alias lookup, case-insensitive. A real name always beats another
    persona's alias; alias collisions fall to catalog (= precedence) order."""
    want = name.strip().lower()
    if not want:
        return None
    for n, p in catalog.items():
        if n.lower() == want:
            return p
    for p in catalog.values():
        if any(a.lower() == want for a in p.aliases):
            return p
    return None


def get(project, name: str, max_chars: int = 2000) -> Persona | None:
    return resolve(load_all(project, max_chars), name)


def index(project=None, catalog: dict[str, Persona] | None = None) -> str:
    """The one-liner roster: the routing menu and the delegate-facing cast list.
    '' when there are no personas."""
    cast = catalog if catalog is not None else load_all(project)
    if not cast:
        return ""
    lines = []
    for name in sorted(cast):
        p = cast[name]
        desc = " ".join(p.description.split())
        if len(desc) > 120:
            desc = desc[:117].rstrip() + "..."
        lines.append(f"- `{name}` — {desc}")
    return "\n".join(lines)


def parse_invocation(text: str, catalog: dict[str, Persona]):
    """Explicit invocation: `hey <name>, ...` or `@<name> ...`.

    Returns (persona, prompt, attempted_name):
      - a catalog hit  -> (Persona, the rest of the text, name)
      - a near-miss    -> (None, the ORIGINAL text untouched, the bad name)
      - no invocation  -> (None, the original text, None)
    A typo'd name never eats the operator's words — the whole prompt runs as
    the default agent and the caller may print a hint."""
    stripped = text.strip()
    for pattern in _INVOKE_RES:
        m = pattern.match(stripped)
        if not m:
            continue
        name, rest = m.group(1), m.group(2).strip()
        p = resolve(catalog, name)
        if p is not None and rest:
            return p, rest, name
        return None, text, name
    return None, text, None


def route(prompt: str, catalog: dict[str, Persona], backend, cfg,
          think_re=None) -> Persona | None:
    """Dynamic routing: one cheap dispatcher call picks the persona whose
    capacity fits the request. Fails OPEN — any error, an unparseable reply,
    `PERSONA: none` or an unknown name all mean the default agent runs. This
    function never raises and never blocks a run."""
    if not catalog:
        return None
    try:
        from hermes import package
        from hermes.agent import strip_think

        rendered = package.render(package.router_prompt(), {
            "roster": index(catalog=catalog),
            "request": prompt.strip()[:ROUTER_REQUEST_CHARS],
        })
        result = backend.chat([{"role": "user", "content": rendered}])
        text = strip_think(result.content, think_re) if think_re else strip_think(
            result.content
        )
        matches = PERSONA_VERDICT_RE.findall(text or "")
        if not matches:
            return None
        pick = matches[-1]  # last match wins — the model may echo the menu first
        if pick.lower() == "none":
            return None
        return resolve(catalog, pick)
    except Exception:
        return None


def filter_registry(registry, persona: Persona):
    """A registry narrowed to the persona's tool posture. Same silent-subset
    semantics as the delegate child registry: unknown names are dropped,
    `finish_run` always survives (a persona must be able to finish), and
    `delegate` survives if the run had it (a persona may still delegate).
    No narrowing when the persona declares no tools line."""
    if persona.tools is None:
        return registry
    from hermes.tools import ToolRegistry

    allowed = set(persona.tools) | {"finish_run", "delegate"}
    out = ToolRegistry()
    for name, t in registry._tools.items():
        if name in allowed:
            out.register(t)
    return out
