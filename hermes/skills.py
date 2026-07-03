"""Skills: the agent's own how-to notes, kept as plain markdown files.

A skill is one markdown file whose FIRST non-empty line is a one-line
description and whose body is the full procedure (including the gotchas learned
the hard way). Two levels:

  - global   ~/.hermes/skills/*.md   (survive across every project)
  - project  <project>/skills/*.md   (local to one project; override globals
                                       of the same name)

Only the INDEX of one-liners rides in the context package (tens of tokens per
skill, like the toolbox catalog); `load_skill(name)` injects a full body on
demand. This mirrors the existing toolbox pattern deliberately.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hermes.config import hermes_home

SKILL_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


@dataclass
class Skill:
    name: str
    description: str
    body: str  # the full file text (description line + procedure)
    scope: str  # "global" | "project"
    path: Path


def global_skills_dir() -> Path:
    return hermes_home() / "skills"


def _describe(text: str) -> str:
    """One-line description = first non-empty line, leading '#'/spaces stripped."""
    for line in text.splitlines():
        s = line.lstrip("#").strip()
        if s:
            return s
    return "(no description)"


def _load_dir(d: Path, scope: str) -> dict[str, Skill]:
    out: dict[str, Skill] = {}
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.md")):
        name = path.stem
        if not SKILL_NAME_RE.match(name):
            continue
        try:
            body = path.read_text()
        except OSError:
            continue
        out[name] = Skill(name, _describe(body), body, scope, path)
    return out


def load_all(project) -> dict[str, Skill]:
    """Every visible skill by name. A project skill shadows a global one of the
    same name (the local, more-specific procedure wins)."""
    skills = _load_dir(global_skills_dir(), "global")
    skills.update(_load_dir(project.skills_dir, "project"))
    return skills


def get(project, name: str) -> Skill | None:
    return load_all(project).get(name)


def index(project) -> str:
    """The one-liner menu for the system prompt. '' when there are no skills."""
    skills = load_all(project)
    if not skills:
        return ""
    lines = []
    for name in sorted(skills):
        sk = skills[name]
        desc = " ".join(sk.description.split())
        if len(desc) > 100:
            desc = desc[:97].rstrip() + "..."
        tag = "" if sk.scope == "global" else " [project]"
        lines.append(f"- `{name}`{tag} — {desc}")
    return "\n".join(lines)


def write(project, name: str, content: str, scope: str = "global") -> Path:
    """Create or overwrite a skill file. Overwriting is the edit-in-place path —
    the whole self-improvement loop is the agent maintaining these."""
    if not SKILL_NAME_RE.match(name):
        raise ValueError("skill name must match [A-Za-z0-9_-]{1,40}")
    base = project.skills_dir if scope == "project" else global_skills_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / f"{name}.md"
    path.write_text(content.rstrip() + "\n")
    return path
