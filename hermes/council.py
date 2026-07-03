"""Council mode (feature 10): the cast deliberating in a closed circle.

Round-robin over the chosen personas. Each speaking slot is one
package-per-prompt completion — the persona's voice + the roster + the
council rules as the system message, the topic + the transcript so far
(tail-truncated to a hard budget) as the user message. No tools: a council
deliberates, it doesn't act, so there is no confirm/taint surface at all.

Bounded twice: `council_rounds` full passes over the cast, and a wall clock
(`council_max_seconds`) checked before every slot. However the deliberation
ends — rounds done, clock expired, backend unreachable, Ctrl-C — the scribe
always writes the outcome from whatever transcript exists. Outputs land in
<project>/council/ (an outcome doc + the full transcript); the council never
touches workspace/.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from hermes import package
from hermes.llm import LLMTransportError
from hermes.ui import cyan, dim, magenta

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^(\d{4})-")


def _slug(topic: str, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("-", topic.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "council"


def _next_id(council_dir: Path) -> int:
    last = 0
    if council_dir.is_dir():
        for p in council_dir.iterdir():
            m = _ID_RE.match(p.name)
            if m:
                last = max(last, int(m.group(1)))
    return last + 1


def _transcript_text(entries) -> str:
    return "\n\n".join(f"## round {rnd} — {name}\n\n{text}"
                       for rnd, name, text in entries)


def council(project, topic: str, cast, cfg, backend, think_re=None):
    """Run one deliberation and return (outcome_path, transcript_path)."""
    from hermes.agent import strip_think

    roster = ""
    from hermes import personas as personas_mod
    roster = personas_mod.index(catalog={p.name: p for p in cast})

    rounds = max(1, int(cfg.get("council_rounds", 2)))
    clock = float(cfg.get("council_max_seconds", 600))
    budget = max(1000, int(cfg.get("council_transcript_chars", 24000)))
    start = time.monotonic()

    entries: list[tuple[int, str, str]] = []  # (round, speaker, text)
    stopped = ""  # why the deliberation ended early, if it did

    def _shown(content) -> str:
        return (strip_think(content, think_re) if think_re
                else strip_think(content))

    try:
        for rnd in range(1, rounds + 1):
            for p in cast:
                if time.monotonic() - start > clock:
                    stopped = "wall clock expired"
                    raise StopIteration
                system = package.render(package.council_member_prompt(), {
                    "name": p.name, "voice": p.voice, "roster": roster,
                })
                so_far = package.truncate_keep_tail(
                    _transcript_text(entries), budget
                )
                user = (
                    f"Topic:\n{topic.strip()}\n\n"
                    "Transcript so far:\n"
                    f"{so_far or '(nothing yet — you open the discussion)'}\n\n"
                    f"You speak now, {p.name}. React to the others; be brief."
                )
                result = backend.chat([
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ])
                text = _shown(result.content) or "(said nothing)"
                entries.append((rnd, p.name, text))
                print(magenta(f"  [{p.name}] ") + dim(text.splitlines()[0][:120]))
    except StopIteration:
        pass
    except LLMTransportError:
        stopped = "backend unreachable"
    except KeyboardInterrupt:
        stopped = "interrupted by the operator"

    transcript = _transcript_text(entries)
    if stopped:
        print(dim(f"  (deliberation cut short: {stopped} — the scribe writes "
                  "from the partial transcript)"))

    # The scribe always writes — a cut-short council still owes an outcome.
    scribe_user = (
        f"Topic:\n{topic.strip()}\n\n"
        + (f"NOTE: the deliberation was cut short ({stopped}).\n\n" if stopped else "")
        + "Transcript:\n"
        + (package.truncate_keep_tail(transcript, budget) or "(empty)")
    )
    try:
        result = backend.chat([
            {"role": "system", "content": package.council_scribe_prompt()},
            {"role": "user", "content": scribe_user},
        ])
        outcome = _shown(result.content) or "(the scribe wrote nothing)"
    except (LLMTransportError, KeyboardInterrupt):
        outcome = ("(the scribe could not reach the backend — the raw "
                   "transcript is the only record)")

    council_dir = project.root / "council"
    council_dir.mkdir(parents=True, exist_ok=True)
    base = f"{_next_id(council_dir):04d}-{_slug(topic)}"
    header = f"# Council {base} — {topic.strip()}\n"
    members = ", ".join(p.name for p in cast)
    meta = f"{header}\nMembers: {members} · rounds asked: {rounds}" + (
        f" · ended early: {stopped}\n" if stopped else "\n"
    )
    outcome_path = council_dir / f"{base}.md"
    transcript_path = council_dir / f"{base}.transcript.md"
    outcome_path.write_text(meta + "\n" + outcome.rstrip() + "\n")
    transcript_path.write_text(meta + "\n" + (transcript.rstrip() or "(empty)") + "\n")
    print(cyan(f"  council outcome → {outcome_path}"))
    return outcome_path, transcript_path
