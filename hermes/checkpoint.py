"""Checkpointing: snapshot the project before a turn mutates files, so a long
run that goes sideways at turn 40 is one revert, not forensics.

Design choice: a lightweight copy, not git. Projects are plain directories (not
repos), a phone has no guarantee of a working git, and copy/restore has no
failure modes to reason about — it stays boring and reliable, which is the whole
point of a safety net. We snapshot the project's own state (mission, notes,
directives, history, workspace, tools, skills) and skip the bulky, separately
managed `runs/` dir (transcripts) and the checkpoint store itself.

On by default. Each snapshot is taken *before* the first file-mutating tool call
of a turn, so restoring one rewinds to just before that turn's changes.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

CHECKPOINT_DIRNAME = ".checkpoints"
EXCLUDE = {CHECKPOINT_DIRNAME, "runs"}


def _store(project) -> Path:
    return project.root / CHECKPOINT_DIRNAME


def _tracked_entries(project) -> list[Path]:
    return [p for p in sorted(project.root.iterdir()) if p.name not in EXCLUDE]


def _copy_entry(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def create(project, label: str = "", max_keep: int = 20) -> str:
    """Snapshot the tracked project state. Returns the checkpoint id."""
    store = _store(project)
    store.mkdir(parents=True, exist_ok=True)
    cid = time.strftime("%Y%m%d-%H%M%S")
    # Disambiguate rapid-fire snapshots within the same second.
    base, n = cid, 1
    while (store / cid).exists():
        n += 1
        cid = f"{base}-{n}"
    snap = store / cid
    snap.mkdir(parents=True)
    for entry in _tracked_entries(project):
        _copy_entry(entry, snap / entry.name)
    (snap / ".meta.json").write_text(json.dumps({
        "id": cid, "ts": time.strftime("%Y-%m-%d %H:%M:%S"), "label": label,
    }))
    _prune(project, max_keep)
    return cid


def _meta(snap: Path) -> dict:
    try:
        return json.loads((snap / ".meta.json").read_text())
    except (OSError, json.JSONDecodeError):
        return {"id": snap.name, "ts": "?", "label": ""}


def list_checkpoints(project) -> list[dict]:
    store = _store(project)
    if not store.is_dir():
        return []
    snaps = sorted((d for d in store.iterdir() if d.is_dir()), key=lambda d: d.name)
    return [_meta(s) for s in snaps]


def _prune(project, max_keep: int) -> None:
    store = _store(project)
    snaps = sorted((d for d in store.iterdir() if d.is_dir()), key=lambda d: d.name)
    for old in snaps[:-max_keep] if max_keep > 0 else []:
        shutil.rmtree(old, ignore_errors=True)


def restore(project, cid: str) -> bool:
    """Revert the tracked project state to checkpoint `cid`. Entries created
    after the snapshot are removed; snapshotted entries are copied back."""
    snap = _store(project) / cid
    if not snap.is_dir():
        return False
    snapshotted = {p.name for p in snap.iterdir() if p.name != ".meta.json"}
    # Remove current tracked entries so the restore is a true revert, not a merge.
    for entry in _tracked_entries(project):
        if entry.is_dir():
            shutil.rmtree(entry, ignore_errors=True)
        else:
            entry.unlink(missing_ok=True)
    for name in snapshotted:
        _copy_entry(snap / name, project.root / name)
    return True
