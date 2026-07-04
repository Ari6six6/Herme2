"""Feature 13: landmarks — the rendezvous between lifecycles. Marks stand on
the road until a briefing reads them; the room must address them; the night
archives what the day attended. Marks left during a day stand for tomorrow."""

import json

import pytest

from hermes import landmarks as landmarks_mod
from hermes import personas as personas_mod
from hermes import workday
from hermes.llm import ChatResult, ToolCall
from hermes.tools import build_registry
from hermes.tools.base import ToolContext


# ---- the road ---------------------------------------------------------------------
def test_leave_load_and_summary(project, cfg):
    landmarks_mod.leave(project, "flapping-tunnel",
                        "the tunnel flaps\n\nsaw it die twice at dawn")
    marks = landmarks_mod.load(project)
    assert [m.name for m in marks] == ["flapping-tunnel"]
    assert marks[0].summary == "the tunnel flaps"
    assert "twice at dawn" in marks[0].text


def test_same_name_overwrites_and_bad_names_refused(project, cfg):
    landmarks_mod.leave(project, "mark", "first")
    landmarks_mod.leave(project, "mark", "second")
    assert landmarks_mod.load(project)[0].text == "second"
    with pytest.raises(ValueError):
        landmarks_mod.leave(project, "no spaces!", "x")


def test_remove(project, cfg):
    landmarks_mod.leave(project, "mark", "x")
    assert landmarks_mod.remove(project, "mark")
    assert not landmarks_mod.remove(project, "mark")
    assert landmarks_mod.load(project) == []


def test_sweep_archives_under_the_day_and_clears_the_road(project, cfg):
    landmarks_mod.leave(project, "mark", "the message")
    marks = landmarks_mod.load(project)
    landmarks_mod.sweep(project, marks, "0007-some-day")
    assert landmarks_mod.load(project) == []
    archived = landmarks_mod.attended_dir(project) / "0007-some-day-mark.md"
    assert archived.read_text() == "the message\n"


# ---- the tool ---------------------------------------------------------------------
def test_tool_registered_only_when_enabled(project, cfg):
    assert "leave_landmark" not in build_registry(
        project, cfg, lambda *a, **k: True).names()
    cfg.set("landmarks_enabled", True)
    reg = build_registry(project, cfg, lambda *a, **k: True)
    assert "leave_landmark" in reg.names()
    ctx = ToolContext(project=project, cfg=cfg)
    out = reg.dispatch("leave_landmark", json.dumps(
        {"name": "found-a-lead", "text": "a lead I could not chase"}), ctx)
    assert "must address it" in out
    assert landmarks_mod.load(project)[0].name == "found-a-lead"
    out = reg.dispatch("leave_landmark", json.dumps(
        {"name": "bad name!", "text": "x"}), ctx)
    assert out.startswith("ERROR:")


# ---- the workday reads the road, the night clears it -------------------------------
class ScriptBackend:
    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls.append(messages)
        if not self.turns:
            return ChatResult(content="(script exhausted)")
        return self.turns.pop(0)()


def _say(text):
    return lambda: ChatResult(content=text)


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


@pytest.fixture
def duo(monkeypatch, tmp_path):
    d = tmp_path / "cast"
    d.mkdir()
    (d / "alpha.md").write_text("watches\n\nYou are Alpha.\n")
    (d / "beta.md").write_text("builds\n\nYou are Beta.\n")
    monkeypatch.setattr(personas_mod, "builtin_dir", lambda: d)
    return d


def _day_script():
    return [
        _say("a view"), _say("b view"),
        _say("ASSIGNMENT: beta: fix it"),
        _call("finish_run", {"summary": "BETA REPORT: fixed"}),
        _say("a take"), _say("b take"),
        _say("## What happened\nfixed."),
    ]


def test_briefing_reads_marks_and_the_night_clears_them(project, cfg, duo):
    cfg.set("landmarks_enabled", True)
    landmarks_mod.leave(project, "flapping-tunnel", "the tunnel flaps at dawn")
    backend = ScriptBackend(_day_script())
    workday.run_day(project, "the task", cfg, backend,
                    confirm_fn=lambda *a, **k: True)
    first_briefing = backend.calls[0][1]["content"]
    assert "LANDMARKS STANDING" in first_briefing
    assert "the tunnel flaps at dawn" in first_briefing
    # odin saw the road too (his papers include the marks)
    assert "the tunnel flaps at dawn" in backend.calls[2][1]["content"]
    # attended: on the day log, archived, and off the road
    log = (workday.days_dir(project) / "0001-the-task.log.md").read_text()
    assert "# LANDMARKS ATTENDED" in log
    assert "- flapping-tunnel: the tunnel flaps at dawn" in log
    assert landmarks_mod.load(project) == []
    assert (landmarks_mod.attended_dir(project)
            / "0001-the-task-flapping-tunnel.md").exists()
    lines = [json.loads(l) for l in
             (project.runs_dir / "0001" / "transcript.jsonl").read_text().splitlines()]
    assert any(e["role"] == "landmark" and e["name"] == "flapping-tunnel"
               for e in lines)


def test_mark_left_during_the_day_stands_for_tomorrow(project, cfg, duo):
    cfg.set("landmarks_enabled", True)
    landmarks_mod.leave(project, "old-mark", "seen by this day")
    backend = ScriptBackend([
        _say("a view"), _say("b view"),
        _say("ASSIGNMENT: beta: fix it"),
        # the worker leaves a mark mid-mission, then finishes
        _call("leave_landmark", {"name": "new-lead",
                                 "text": "a lead for tomorrow"}),
        _call("finish_run", {"summary": "BETA REPORT: fixed"}),
        _say("a take"), _say("b take"),
        _say("## What happened\nfixed."),
    ])
    workday.run_day(project, "the task", cfg, backend,
                    confirm_fn=lambda *a, **k: True)
    standing = landmarks_mod.load(project)
    assert [m.name for m in standing] == ["new-lead"]  # tomorrow's road
    # and the next day is bound to read it
    backend2 = ScriptBackend(_day_script())
    workday.run_day(project, "day two", cfg, backend2,
                    confirm_fn=lambda *a, **k: True)
    assert "a lead for tomorrow" in backend2.calls[0][1]["content"]
    assert landmarks_mod.load(project) == []


def test_clear_road_costs_nothing(project, cfg, duo):
    cfg.set("landmarks_enabled", True)
    backend = ScriptBackend(_day_script())
    workday.run_day(project, "the task", cfg, backend,
                    confirm_fn=lambda *a, **k: True)
    assert "LANDMARKS STANDING" not in backend.calls[0][1]["content"]
    log = (workday.days_dir(project) / "0001-the-task.log.md").read_text()
    assert "LANDMARKS ATTENDED" not in log
