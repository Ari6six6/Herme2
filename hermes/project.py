"""Projects: the unit of memory.

A project is a directory holding everything the agent knows between runs:
mission.md, notes.md, history.jsonl (user prompts only), run summaries,
forged tools, and a workspace. The app's code is never inside a project.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

DEFAULT_MISSION = """\
# Mission

(Describe what this project is about. The agent reads this at the start of
every run — keep it current. Edit freely.)
"""

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


class ProjectError(Exception):
    pass


class Project:
    def __init__(self, root: Path):
        self.root = root.resolve()

    # -- layout ----------------------------------------------------------
    @property
    def name(self) -> str:
        return self.root.name

    @property
    def mission_path(self) -> Path:
        return self.root / "mission.md"

    @property
    def notes_path(self) -> Path:
        return self.root / "notes.md"

    @property
    def directives_path(self) -> Path:
        return self.root / "directives.md"

    @property
    def history_path(self) -> Path:
        return self.root / "history.jsonl"

    @property
    def tools_dir(self) -> Path:
        return self.root / "tools"

    @property
    def workspace_dir(self) -> Path:
        return self.root / "workspace"

    @property
    def skills_dir(self) -> Path:
        return self.root / "skills"

    @property
    def personas_dir(self) -> Path:
        return self.root / "personas"

    @property
    def runs_dir(self) -> Path:
        return self.root / "runs"

    @property
    def equipped_path(self) -> Path:
        return self.tools_dir / ".equipped.json"

    @property
    def approved_path(self) -> Path:
        return self.tools_dir / ".approved.json"

    def ensure_layout(self) -> None:
        for d in (self.root, self.tools_dir, self.workspace_dir, self.runs_dir):
            d.mkdir(parents=True, exist_ok=True)
        if not self.mission_path.exists():
            self.mission_path.write_text(DEFAULT_MISSION)
        if not self.notes_path.exists():
            self.notes_path.write_text("")
        if not self.history_path.exists():
            self.history_path.write_text("")

    # -- lifecycle ---------------------------------------------------------
    @staticmethod
    def create(projects_dir: Path, name: str) -> "Project":
        if not _NAME_RE.match(name):
            raise ProjectError(
                "project names: letters, digits, '-' and '_' only (max 40 chars)"
            )
        root = projects_dir / name
        if root.exists():
            raise ProjectError(f"project '{name}' already exists")
        p = Project(root)
        p.ensure_layout()
        return p

    @staticmethod
    def load(projects_dir: Path, name: str) -> "Project":
        root = projects_dir / name
        if not root.is_dir():
            raise ProjectError(f"no such project: {name}")
        p = Project(root)
        p.ensure_layout()
        return p

    @staticmethod
    def list_names(projects_dir: Path) -> list[str]:
        if not projects_dir.is_dir():
            return []
        return sorted(d.name for d in projects_dir.iterdir() if d.is_dir())

    # -- history (user prompts only) ---------------------------------------
    def append_history(self, run_id: int, text: str) -> None:
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M"), "run": run_id, "text": text}
        with self.history_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")

    def recent_prompts(self, n: int) -> list[dict]:
        if not self.history_path.exists():
            return []
        lines = self.history_path.read_text().splitlines()
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def all_prompts(self) -> list[dict]:
        """The full prompt log, oldest first. Used only by the directive
        reconciliation pass — the package never sends this whole thing."""
        if not self.history_path.exists():
            return []
        out = []
        for line in self.history_path.read_text().splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    # -- runs ----------------------------------------------------------------
    def next_run_id(self) -> int:
        ids = [
            int(d.name)
            for d in self.runs_dir.iterdir()
            if d.is_dir() and d.name.isdigit()
        ] if self.runs_dir.exists() else []
        return max(ids, default=0) + 1

    def new_run(self) -> tuple[int, Path]:
        run_id = self.next_run_id()
        run_dir = self.runs_dir / f"{run_id:04d}"
        run_dir.mkdir(parents=True)
        return run_id, run_dir

    def last_final_reply(self) -> tuple[int, str] | None:
        """The agent's most recent final answer, verbatim — so the next run
        understands references to 'what you just said'."""
        if not self.runs_dir.exists():
            return None
        dirs = sorted(
            (d for d in self.runs_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: int(d.name),
            reverse=True,
        )
        for d in dirs:
            final = d / "final.md"
            if final.exists():
                text = final.read_text().strip()
                if text:
                    return int(d.name), text
        return None

    def recent_summaries(self, k: int) -> list[tuple[int, str]]:
        if not self.runs_dir.exists():
            return []
        dirs = sorted(
            (d for d in self.runs_dir.iterdir() if d.is_dir() and d.name.isdigit()),
            key=lambda d: int(d.name),
        )
        out = []
        for d in dirs[-k:]:
            summary = d / "summary.md"
            if summary.exists():
                out.append((int(d.name), summary.read_text().strip()))
        return out

    # -- mission / notes ----------------------------------------------------
    def read_mission(self) -> str:
        return self.mission_path.read_text() if self.mission_path.exists() else ""

    def read_notes(self) -> str:
        return self.notes_path.read_text() if self.notes_path.exists() else ""

    def read_directives(self) -> str:
        return self.directives_path.read_text() if self.directives_path.exists() else ""

    def write_directives(self, text: str) -> None:
        self.directives_path.write_text(text.rstrip() + "\n")

    def append_note(self, text: str) -> None:
        stamp = time.strftime("%Y-%m-%d %H:%M")
        with self.notes_path.open("a") as f:
            f.write(f"- [{stamp}] {text.strip()}\n")

    # -- workspace ------------------------------------------------------------
    def workspace_listing(self, max_entries: int = 60) -> str:
        lines: list[str] = []
        try:
            entries = sorted(self.workspace_dir.rglob("*"))
        except OSError:
            return "(workspace unreadable)"
        for p in entries:
            if len(lines) >= max_entries:
                lines.append(f"... ({len(entries) - max_entries} more entries)")
                break
            rel = p.relative_to(self.workspace_dir)
            if p.is_dir():
                lines.append(f"{rel}/")
            else:
                try:
                    lines.append(f"{rel} ({p.stat().st_size}B)")
                except OSError:
                    lines.append(str(rel))
        return "\n".join(lines) if lines else "(empty)"

    # -- equipped toolbox tools ------------------------------------------------
    def equipped_tools(self) -> list[str]:
        if not self.equipped_path.exists():
            return []
        try:
            return list(json.loads(self.equipped_path.read_text()))
        except (json.JSONDecodeError, OSError):
            return []

    def equip_tool(self, name: str) -> None:
        names = self.equipped_tools()
        if name not in names:
            names.append(name)
            self.tools_dir.mkdir(parents=True, exist_ok=True)
            self.equipped_path.write_text(json.dumps(names, indent=2))

    # -- forged tool approval (content hashes) ----------------------------------
    def approved_hashes(self) -> dict:
        if not self.approved_path.exists():
            return {}
        try:
            return json.loads(self.approved_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {}

    def approve_hash(self, filename: str, digest: str) -> None:
        hashes = self.approved_hashes()
        hashes[filename] = digest
        self.tools_dir.mkdir(parents=True, exist_ok=True)
        self.approved_path.write_text(json.dumps(hashes, indent=2))
