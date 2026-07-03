"""Directive reconciliation: treat standing instructions as state, not log.

The prompt history is append-only, so "never do X" (run 8) and "now do X"
(run 30) sit in the package with equal weight and the model can't tell which
one stands. This module distils the whole history into a `directives.md` that
resolves conflicts by recency, so the package can send the distillation plus
only the last few raw prompts instead of the entire log.

The reconciliation is a plain LLM side-call (no tools). It never raises — a
failed pass leaves the existing directives untouched and the run proceeds.
"""

from __future__ import annotations

from hermes.llm import LLMTransportError

RECENCY_HEADER_LINE = (
    "When instructions conflict, the more recent one wins. `directives.md` is the "
    "current authoritative state of standing instructions."
)


def _history_text(project) -> str:
    entries = project.all_prompts()
    return "\n".join(
        f"[{e.get('run', '?'):>4}] {e.get('text', '')}" for e in entries
    )


def reconcile(project, backend, cfg, think_re=None) -> str | None:
    """Rewrite `directives.md` from the full prompt history. Returns the new
    directives text, or None if the pass could not run (no history, backend
    down, empty output) — in which case any existing file is left as-is."""
    from hermes import package
    from hermes.agent import strip_think

    history = _history_text(project).strip()
    if not history:
        return None
    current = project.read_directives().strip() or "(none yet)"
    prompt = package.render(
        package.reconcile_prompt(), {"current": current, "history": history}
    )
    try:
        result = backend.chat([{"role": "user", "content": prompt}])
    except LLMTransportError:
        return None
    text = strip_think(result.content, think_re) if think_re else strip_think(
        result.content
    )
    text = (text or "").strip()
    if not text:
        return None
    project.write_directives(text)
    return text


def maybe_reconcile(project, backend, cfg, run_id: int, think_re=None) -> bool:
    """Trigger a reconciliation at the start of a run when it's due:
      - migration: an existing project has history but no directives.md yet, or
      - periodic: every `reconcile_every_runs` runs.
    Returns True if a pass ran. Gated by the caller on `directives_enabled`."""
    if not project.all_prompts():
        return False  # nothing to distil yet (first prompt lands after assemble)
    every = max(1, int(cfg.get("reconcile_every_runs", 10)))
    due = (not project.directives_path.exists()) or (run_id % every == 0)
    if not due:
        return False
    return reconcile(project, backend, cfg, think_re) is not None
