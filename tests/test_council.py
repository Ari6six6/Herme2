"""Feature 10: council mode — a bounded round-robin deliberation whose scribe
always writes an outcome, however the loop ends."""

import re

from hermes import council as council_mod
from hermes import personas as personas_mod
from hermes.llm import ChatResult, LLMTransportError

THINK_RE = re.compile(r"<think>.*?</think>\s*", re.S)


class SayBackend:
    """Answers each chat() with the next scripted text; records every call."""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls.append(messages)
        item = self.texts.pop(0) if self.texts else "(dry)"
        if isinstance(item, Exception):
            raise item
        return ChatResult(content=item)


def _cast(project, names=("owl", "tor")):
    catalog = personas_mod.load_all(project)
    return [catalog[n] for n in names]


def _speaker(call) -> str:
    m = re.search(r"You speak now, ([A-Za-z0-9_-]+)\.", call[1]["content"])
    return m.group(1) if m else "(scribe)"


def test_round_robin_order_and_rounds_cap(project, cfg):
    cfg.set("council_rounds", 2)
    backend = SayBackend(["a1", "b1", "a2", "b2", "OUTCOME DOC"])
    outcome, transcript = council_mod.council(
        project, "should we rewrite the parser?", _cast(project), cfg, backend,
        THINK_RE)
    # 2 members x 2 rounds + 1 scribe call, in strict round-robin order
    assert [_speaker(c) for c in backend.calls] == [
        "owl", "tor", "owl", "tor", "(scribe)"]
    body = transcript.read_text()
    assert body.index("## round 1 — owl") < body.index("## round 1 — tor") \
        < body.index("## round 2 — owl")
    assert "OUTCOME DOC" in outcome.read_text()


def test_each_speaker_sees_the_transcript_so_far(project, cfg):
    cfg.set("council_rounds", 1)
    backend = SayBackend(["the owl's point", "the smith's reply", "done"])
    council_mod.council(project, "topic", _cast(project), cfg, backend, THINK_RE)
    first_user = backend.calls[0][1]["content"]
    second_user = backend.calls[1][1]["content"]
    assert "you open the discussion" in first_user
    assert "the owl's point" in second_user  # smith reacts to what was said


def test_member_prompt_carries_voice_and_roster(project, cfg):
    cfg.set("council_rounds", 1)
    backend = SayBackend(["x", "y", "z"])
    council_mod.council(project, "topic", _cast(project), cfg, backend, THINK_RE)
    system = backend.calls[0][0]["content"]
    assert "You are owl" in system or "## Persona — owl" in system
    assert "You are the Owl" in system  # the voice body
    assert "`tor`" in system  # the roster
    assert "{{" not in system


def test_wall_clock_cuts_to_the_scribe(project, cfg, monkeypatch):
    cfg.set("council_rounds", 5)
    cfg.set("council_max_seconds", 100)
    ticks = iter([0.0, 0.0, 1e9])  # start, slot 1 ok, slot 2 expired
    monkeypatch.setattr(council_mod.time, "monotonic",
                        lambda: next(ticks, 1e9))
    backend = SayBackend(["only the owl spoke", "SCRIBE OUT"])
    outcome, transcript = council_mod.council(
        project, "topic", _cast(project), cfg, backend, THINK_RE)
    assert len(backend.calls) == 2  # one slot + the scribe, nothing more
    assert "only the owl spoke" in transcript.read_text()
    text = outcome.read_text()
    assert "SCRIBE OUT" in text
    assert "wall clock expired" in text
    # the scribe was told the deliberation was cut short
    assert "cut short" in backend.calls[-1][1]["content"]


def test_transport_error_still_yields_a_scribe_outcome(project, cfg):
    cfg.set("council_rounds", 1)
    backend = SayBackend(["the owl spoke",
                          LLMTransportError("endpoint down"),
                          "PARTIAL OUTCOME"])
    outcome, transcript = council_mod.council(
        project, "topic", _cast(project), cfg, backend, THINK_RE)
    assert "the owl spoke" in transcript.read_text()
    assert "PARTIAL OUTCOME" in outcome.read_text()
    assert "backend unreachable" in outcome.read_text()


def test_scribe_failure_still_writes_files(project, cfg):
    cfg.set("council_rounds", 1)
    backend = SayBackend(["a", "b", LLMTransportError("down at the end")])
    outcome, transcript = council_mod.council(
        project, "topic", _cast(project), cfg, backend, THINK_RE)
    assert outcome.exists() and transcript.exists()
    assert "could not reach the backend" in outcome.read_text()


def test_transcript_fed_to_speakers_is_tail_truncated(project, cfg):
    cfg.set("council_rounds", 1)
    cfg.set("council_transcript_chars", 1000)
    backend = SayBackend(["EARLY " + "LONGWINDED " * 400 + "RECENT_TAIL", "y", "z"])
    council_mod.council(project, "topic", _cast(project), cfg, backend, THINK_RE)
    second_user = backend.calls[1][1]["content"]
    assert "[...truncated...]" in second_user
    assert "RECENT_TAIL" in second_user  # the tail survives
    assert "EARLY LONGWINDED" not in second_user


def test_think_tags_stripped_from_speeches(project, cfg):
    cfg.set("council_rounds", 1)
    backend = SayBackend(["<think>secret chain</think>the public point",
                          "y", "z"])
    _, transcript = council_mod.council(
        project, "topic", _cast(project), cfg, backend, THINK_RE)
    body = transcript.read_text()
    assert "the public point" in body
    assert "secret chain" not in body


def test_council_files_are_numbered_and_never_touch_workspace(project, cfg):
    cfg.set("council_rounds", 1)
    for _ in range(2):
        backend = SayBackend(["a", "b", "out"])
        council_mod.council(project, "same topic!", _cast(project), cfg,
                            backend, THINK_RE)
    names = sorted(p.name for p in (project.root / "council").iterdir())
    assert names == [
        "0001-same-topic.md", "0001-same-topic.transcript.md",
        "0002-same-topic.md", "0002-same-topic.transcript.md",
    ]
    assert list(project.workspace_dir.iterdir()) == []
