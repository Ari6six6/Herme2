"""Landmarks (feature 13): the rendezvous between lifecycles.

Characters with different lifecycles cannot call each other — a short life
and a long life are rarely awake at the same moment — so they meet at marks.
A landmark is one markdown file in <project>/landmarks/: its first non-empty
line is the one-line summary, the rest is the message. Anyone can leave one —
the operator from the REPL (`landmark <name> <text>`), a worker mid-mission
with the `leave_landmark` tool (a dying child's note to tomorrow) — and the
protocol is the obligation on the other side: every morning briefing reads
the standing landmarks into the day's papers, and the room MUST address
them. A landmark seen by a day is attended by it: after the close it is
archived into landmarks/.attended/ (stamped with the day) and cleared from
the road. Marks left DURING a day stand for the next one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

LANDMARK_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
LANDMARK_MAX_CHARS = 4000  # one mark's budget in the briefing papers


@dataclass
class Landmark:
    name: str
    text: str
    path: Path

    @property
    def summary(self) -> str:
        for line in self.text.splitlines():
            s = line.lstrip("#").strip()
            if s:
                return s
        return "(empty)"


def landmarks_dir(project) -> Path:
    return project.root / "landmarks"


def attended_dir(project) -> Path:
    return landmarks_dir(project) / ".attended"


def load(project) -> list[Landmark]:
    """Every standing landmark, oldest first — the order they were left."""
    d = landmarks_dir(project)
    if not d.is_dir():
        return []
    out = []
    for path in sorted(d.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name)):
        if not LANDMARK_NAME_RE.match(path.stem):
            continue
        try:
            text = path.read_text().strip()
        except OSError:
            continue
        if len(text) > LANDMARK_MAX_CHARS:
            text = text[:LANDMARK_MAX_CHARS] + "\n[landmark truncated]"
        out.append(Landmark(path.stem, text, path))
    return out


def leave(project, name: str, text: str) -> Path:
    """Plant a mark on the road. Overwriting a standing mark of the same name
    is the edit path — the road holds one mark per name."""
    if not LANDMARK_NAME_RE.match(name):
        raise ValueError("landmark name must match [A-Za-z0-9_-]{1,40}")
    d = landmarks_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.md"
    path.write_text(text.strip() + "\n")
    return path


def remove(project, name: str) -> bool:
    path = landmarks_dir(project) / f"{name}.md"
    if LANDMARK_NAME_RE.match(name) and path.exists():
        path.unlink()
        return True
    return False


def sweep(project, marks: list[Landmark], day_base: str) -> None:
    """The night clears the road: marks the day attended are archived under
    the day's name and removed. Marks left during the day are not touched —
    they stand for tomorrow."""
    if not marks:
        return
    archive = attended_dir(project)
    archive.mkdir(parents=True, exist_ok=True)
    for m in marks:
        try:
            (archive / f"{day_base}-{m.name}.md").write_text(m.text.rstrip() + "\n")
            if m.path.exists():
                m.path.unlink()
        except OSError:
            continue  # a stuck mark stands another day; never kill the night


def papers_block(marks: list[Landmark]) -> str:
    """The briefing-papers section. '' when the road is clear."""
    if not marks:
        return ""
    parts = [f"## {m.name}\n{m.text}" for m in marks]
    return (
        "\n\n# LANDMARKS STANDING (left on the road for this day — the room "
        "must address each one)\n\n" + "\n\n".join(parts)
    )
