"""Feature 12: service records — organic identity. What a character does
accretes into who it is: one line per run/day onto its jacket, the tail
riding in its voice everywhere it speaks."""

import json

from hermes import agent
from hermes import personas as personas_mod
from hermes.llm import ChatResult, ToolCall


class ScriptBackend:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls.append(messages)
        if not self.turns:
            return ChatResult(content="(script exhausted)")
        return self.turns.pop(0)()


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


def _say(text):
    return lambda: ChatResult(content=text)


# ---- the jacket file ------------------------------------------------------------
def test_append_and_tail(project, cfg):
    personas_mod.append_record(project, "owl", "- day 0001: watched 2 handoffs")
    personas_mod.append_record(project, "owl", "- day 0002: watched 1 handoff")
    text = personas_mod.record_path(project, "owl").read_text()
    assert text == ("- day 0001: watched 2 handoffs\n"
                    "- day 0002: watched 1 handoff\n")
    tail = personas_mod.record_tail(project, "owl", 40)
    assert "day 0002" in tail
    assert tail.startswith("(...)")  # cut at a line boundary, marked


def test_jacket_forgets_oldest_days_first(project, cfg):
    for i in range(200):
        personas_mod.append_record(project, "tor",
                                   f"- day {i:04d}: swung the hammer " + "x" * 60,
                                   keep_chars=2000)
    text = personas_mod.record_path(project, "tor").read_text()
    assert len(text) < 2200
    assert text.startswith("(older days forgotten)")
    assert "day 0199" in text  # the newest survives
    assert "day 0001:" not in text  # the oldest is gone


def test_bad_names_never_write(project, cfg):
    personas_mod.append_record(project, "no good!", "- entry")
    assert not personas_mod.records_dir(project).exists()


# ---- the record rides in the voice ------------------------------------------------
def test_load_cast_appends_record_tail_when_enabled(project, cfg):
    personas_mod.append_record(project, "owl", "- day 0001: sent one back")
    cast = personas_mod.load_cast(project, cfg)
    assert "service record" not in cast["owl"].voice.lower()  # off by default
    cfg.set("service_records", True)
    cast = personas_mod.load_cast(project, cfg)
    assert "Your service record" in cast["owl"].voice
    assert "- day 0001: sent one back" in cast["owl"].voice
    # a character with no record speaks with its plain voice
    assert "service record" not in cast["tor"].voice.lower()


def test_record_tail_in_voice_is_budgeted(project, cfg):
    cfg.set("service_records", True)
    cfg.set("record_prompt_chars", 200)
    for i in range(50):
        personas_mod.append_record(project, "owl", f"- day {i:04d}: " + "w" * 40)
    voice = personas_mod.load_cast(project, cfg)["owl"].voice
    record_part = voice.split("Your service record")[1]
    assert len(record_part) < 400
    assert "day 0049" in record_part  # most recent last, and it survives


# ---- writers ----------------------------------------------------------------------
def test_direct_persona_run_writes_the_jacket(project, cfg):
    cfg.set("personas_enabled", True)
    cfg.set("service_records", True)
    p = personas_mod.get(project, "owl")
    backend = ScriptBackend([
        _call("finish_run", {"summary": "audited the tunnel; it flaps on DNS"}),
    ])
    agent.run(project, "hey owl why does the tunnel flap", cfg, backend,
              gpu=None, env={}, confirm_fn=lambda *a, **k: True, persona=p)
    text = personas_mod.record_path(project, "owl").read_text()
    assert "- run 0001:" in text
    assert "audited the tunnel; it flaps on DNS" in text


def test_direct_run_without_the_toggle_writes_nothing(project, cfg):
    cfg.set("personas_enabled", True)
    p = personas_mod.get(project, "owl")
    backend = ScriptBackend([_call("finish_run", {"summary": "done"})])
    agent.run(project, "x", cfg, backend, gpu=None, env={},
              confirm_fn=lambda *a, **k: True, persona=p)
    assert not personas_mod.records_dir(project).exists()


# ---- the workday shapes its cast ---------------------------------------------------
def test_workday_writes_worker_and_office_jackets(project, cfg, monkeypatch,
                                                  tmp_path):
    from hermes import workday
    d = tmp_path / "cast"
    d.mkdir()
    (d / "alpha.md").write_text("watches\n\nYou are Alpha.\n")
    (d / "beta.md").write_text("builds\n\nYou are Beta.\n")
    monkeypatch.setattr(personas_mod, "builtin_dir", lambda: d)
    cfg.set("service_records", True)
    cfg.set("workday_supervisor", "alpha")
    backend = ScriptBackend([
        _say("a view"), _say("b view"),
        _say("ASSIGNMENT: beta: fix the parser"),
        _call("finish_run", {"summary": "BETA v1: claims only"}),
        _say("HANDOFF: REWORK: run it"),
        _call("finish_run", {"summary": "BETA v2: ran it clean"}),
        _say("HANDOFF: ACCEPT"),
        _say("a take"), _say("b take"),
        _say("## What happened\nfixed."),
    ])
    workday.run_day(project, "sort the parser", cfg, backend,
                    confirm_fn=lambda *a, **k: True)
    beta = personas_mod.record_path(project, "beta").read_text()
    assert 'sent to "fix the parser"' in beta
    assert "BETA v2: ran it clean" in beta
    assert "[sent back by the watcher]" in beta
    alpha = personas_mod.record_path(project, "alpha").read_text()
    assert "watched 1 handoff(s), sent 1 back" in alpha


def test_yesterday_shapes_todays_voice(project, cfg, monkeypatch, tmp_path):
    # The full loop of organic identity: day 1's work rides in the SAME
    # character's system prompt on day 2.
    from hermes import workday
    d = tmp_path / "cast"
    d.mkdir()
    (d / "alpha.md").write_text("watches\n\nYou are Alpha.\n")
    (d / "beta.md").write_text("builds\n\nYou are Beta.\n")
    monkeypatch.setattr(personas_mod, "builtin_dir", lambda: d)
    cfg.set("service_records", True)

    def day_script():
        return [
            _say("a"), _say("b"),
            _say("ASSIGNMENT: beta: fix the parser"),
            _call("finish_run", {"summary": "BETA REPORT: fixed for good"}),
            _say("a2"), _say("b2"),
            _say("## What happened\nfixed."),
        ]

    workday.run_day(project, "day one", cfg, ScriptBackend(day_script()),
                    confirm_fn=lambda *a, **k: True)
    backend = ScriptBackend(day_script())
    workday.run_day(project, "day two", cfg, backend,
                    confirm_fn=lambda *a, **k: True)
    worker_system = backend.calls[3][0]["content"]  # beta's day-2 child prompt
    assert "Your service record" in worker_system
    assert "BETA REPORT: fixed for good" in worker_system
