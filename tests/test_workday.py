"""Feature 11: the workday — briefing → foreman-cut assignments → persona
workers → debrief → the scribe's write-up, which is both the operator's reply
and the next day's carryover. Plus the harvest: lessons become skills."""

import json

import pytest

from hermes import personas as personas_mod
from hermes import skills as skills_mod
from hermes import workday
from hermes.llm import ChatResult, LLMTransportError, ToolCall


@pytest.fixture
def duo(monkeypatch, tmp_path):
    """A two-persona cast so day scripts stay small and deterministic.
    beta has no tools line — the unrestricted fallback worker."""
    d = tmp_path / "builtin-cast"
    d.mkdir()
    (d / "alpha.md").write_text(
        "does analysis and auditing\n"
        "tools: read_file, list_files, write_note\n\n"
        "You are Alpha, the analyst.\n")
    (d / "beta.md").write_text(
        "does building and fixing\n\nYou are Beta, the builder.\n")
    monkeypatch.setattr(personas_mod, "builtin_dir", lambda: d)
    return d


class ScriptBackend:
    """Pops one ChatResult factory per chat(); records every call."""

    def __init__(self, turns):
        self.turns = list(turns)
        self.calls = []

    def chat(self, messages, tools=None, tool_choice=None):
        self.calls.append(messages)
        if not self.turns:
            return ChatResult(content="(script exhausted)")
        item = self.turns.pop(0)
        if isinstance(item, Exception):
            raise item
        return item()


def _say(text):
    return lambda: ChatResult(content=text)


def _call(name, args):
    return lambda: ChatResult(content=None,
                              tool_calls=[ToolCall("c", name, json.dumps(args))])


FOREMAN = "The split:\nASSIGNMENT: alpha: audit the parser\nASSIGNMENT: beta: fix the parser"
DEBRIEF_DOC = ("## What happened\nAlpha audited, Beta fixed.\n\n"
               "## Mission status\nMoved.\n\n## Open items\nNone.\n\n"
               "## Tomorrow\nShip it.")


def _full_day_script():
    return [
        _say("alpha's morning view"),
        _say("beta's morning view"),
        _say(FOREMAN),
        _call("finish_run", {"summary": "ALPHA REPORT: two real bugs found"}),
        _call("finish_run", {"summary": "BETA REPORT: fixed and ran clean"}),
        _say("alpha's evening take"),
        _say("beta's evening take"),
        _say(DEBRIEF_DOC),
    ]


def _run_day(project, cfg, backend, task="sort out the parser"):
    return workday.run_day(project, task, cfg, backend,
                           confirm_fn=lambda *a, **k: True)


# ---- foreman parsing ------------------------------------------------------------
def test_parse_assignments_resolves_names_and_caps(project, cfg, duo):
    cast = list(personas_mod.load_all(project).values())
    text = ("ASSIGNMENT: alpha: look\nASSIGNMENT: minotaur: impossible\n"
            "ASSIGNMENT: beta: build\nASSIGNMENT: alpha: again")
    got = workday.parse_assignments(text, cast, max_workers=2)
    assert [(p.name, b) for p, b in got] == [("alpha", "look"), ("beta", "build")]


def test_parse_assignments_empty_on_garbage(project, cfg, duo):
    cast = list(personas_mod.load_all(project).values())
    assert workday.parse_assignments("no lines here", cast, 3) == []


# ---- one full day ---------------------------------------------------------------
def test_full_day_end_to_end(project, cfg, duo):
    backend = ScriptBackend(_full_day_script())
    result = _run_day(project, cfg, backend)

    # the debrief is the operator's reply and the run's inheritance
    assert "Alpha audited, Beta fixed." in result.final_text
    assert "Workday 0001" in result.summary
    run_dir = project.runs_dir / "0001"
    assert "Alpha audited" in (run_dir / "final.md").read_text()
    assert "Workday 0001" in (run_dir / "summary.md").read_text()

    # the day files: debrief + full log
    day = workday.days_dir(project) / "0001-sort-out-the-parser.md"
    log = workday.days_dir(project) / "0001-sort-out-the-parser.log.md"
    assert "## Mission status" in day.read_text()
    log_text = log.read_text()
    for piece in ("MORNING BRIEFING", "alpha's morning view", "ASSIGNMENTS",
                  "ALPHA REPORT", "BETA REPORT", "EVENING DEBRIEF",
                  "alpha's evening take", "DEBRIEF (as written)"):
        assert piece in log_text

    # the transcript carries every room
    lines = [json.loads(l) for l in
             (run_dir / "transcript.jsonl").read_text().splitlines()]
    roles = {e["role"] for e in lines}
    assert {"briefing", "assignment", "report", "debrief",
            "debrief-scribe"} <= roles

    # workers ran as persona children: sub-agent prompt + the persona voice
    worker_system = backend.calls[3][0]["content"]
    assert worker_system.startswith("You are a SUB-AGENT")
    assert "You are Alpha, the analyst." in worker_system
    # and the worker's brief was self-contained: task + assignment
    worker_user = backend.calls[3][1]["content"]
    assert "sort out the parser" in worker_user
    assert "audit the parser" in worker_user


def test_briefing_sees_mission_task_and_first_day_marker(project, cfg, duo):
    project.mission_path.write_text("Conquer the parser kingdom.")
    backend = ScriptBackend(_full_day_script())
    _run_day(project, cfg, backend)
    first_briefing = backend.calls[0][1]["content"]
    assert "Conquer the parser kingdom." in first_briefing
    assert "sort out the parser" in first_briefing
    assert "first day — no debrief yet" in first_briefing
    system = backend.calls[0][0]["content"]
    assert "MORNING BRIEFING" in system and "You are Alpha" in system


def test_debrief_carries_over_to_the_next_day(project, cfg, duo):
    _run_day(project, cfg, ScriptBackend(_full_day_script()))
    backend = ScriptBackend(_full_day_script())
    _run_day(project, cfg, backend, task="day two: polish it")
    first_briefing = backend.calls[0][1]["content"]
    assert "YESTERDAY'S DEBRIEF" in first_briefing
    assert "Alpha audited, Beta fixed." in first_briefing  # yesterday, verbatim
    assert (workday.days_dir(project) / "0002-day-two-polish-it.md").exists()


def test_foreman_garbage_falls_back_to_one_worker(project, cfg, duo):
    backend = ScriptBackend([
        _say("alpha's view"), _say("beta's view"),
        _say("I refuse to use the format"),  # foreman flops
        _call("finish_run", {"summary": "BETA REPORT: did the whole task"}),
        _say("alpha's take"), _say("beta's take"),
        _say(DEBRIEF_DOC),
    ])
    _run_day(project, cfg, backend)
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    # the unrestricted persona (beta) took the whole task
    assert "- beta: sort out the parser" in log
    assert "ALPHA REPORT" not in log


def test_day_clock_skips_workers_but_still_debriefs(project, cfg, duo, monkeypatch):
    ticks = iter([0.0, 0.0, 0.0])  # start + two briefing slots; then expired
    monkeypatch.setattr(workday.time, "monotonic", lambda: next(ticks, 1e9))
    backend = ScriptBackend([
        _say("alpha's view"), _say("beta's view"),
        _say(FOREMAN),
        # no worker turns — both get skipped by the clock
        _say(DEBRIEF_DOC),  # the scribe (the debrief rooms are cut by the clock)
    ])
    result = _run_day(project, cfg, backend)
    assert "Alpha audited, Beta fixed." in result.final_text
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert log.count("clock ran out") == 2  # both assignments skipped, on record


def test_dead_scribe_still_hands_over_raw_reports(project, cfg, duo):
    backend = ScriptBackend([
        _say("a"), _say("b"), _say(FOREMAN),
        _call("finish_run", {"summary": "ALPHA REPORT: found it"}),
        _call("finish_run", {"summary": "BETA REPORT: fixed it"}),
        _say("a2"), _say("b2"),
        LLMTransportError("scribe endpoint down"),
    ])
    result = _run_day(project, cfg, backend)
    assert "raw reports follow" in result.final_text
    assert "ALPHA REPORT: found it" in result.final_text
    assert (workday.days_dir(project) / "0001-sort-out-the-parser.md").exists()


def test_rooms_zero_rounds_are_skipped(project, cfg, duo):
    cfg.set("workday_briefing_rounds", 0)
    cfg.set("workday_debrief_rounds", 0)
    backend = ScriptBackend([
        _say(FOREMAN),
        _call("finish_run", {"summary": "ALPHA REPORT"}),
        _call("finish_run", {"summary": "BETA REPORT"}),
        _say(DEBRIEF_DOC),
    ])
    result = _run_day(project, cfg, backend)
    assert "Alpha audited" in result.final_text
    assert len(backend.calls) == 4  # no room chatter at all


# ---- the handoff: finished workers report off to the watcher ---------------------
FOREMAN_BETA = "The split:\nASSIGNMENT: beta: fix the parser"


def _supervised_day_script(handoff_turns):
    """A day where only beta works and alpha watches the handoff."""
    return ([_say("alpha's morning view"), _say("beta's morning view"),
             _say(FOREMAN_BETA),
             _call("finish_run", {"summary": "BETA v1: fixed it, ran clean"})]
            + handoff_turns
            + [_say("alpha's evening take"), _say("beta's evening take"),
               _say(DEBRIEF_DOC)])


def test_handoff_accept_goes_on_the_record(project, cfg, duo):
    cfg.set("workday_supervisor", "alpha")
    backend = ScriptBackend(_supervised_day_script(
        [_say("evidence is real.\nHANDOFF: ACCEPT")]))
    _run_day(project, cfg, backend)
    # the watcher was pinged with the worker's report, speaking as alpha
    sup_call = backend.calls[4]
    assert "You are Alpha, the analyst." in sup_call[0]["content"]
    assert "BETA v1" in sup_call[1]["content"]
    assert "fix the parser" in sup_call[1]["content"]
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert "[handoff — alpha: accepted]" in log
    lines = [json.loads(l) for l in
             (project.runs_dir / "0001" / "transcript.jsonl").read_text().splitlines()]
    assert any(e["role"] == "handoff" and "ACCEPT" in e["content"] for e in lines)


def test_handoff_rework_then_accept(project, cfg, duo):
    cfg.set("workday_supervisor", "alpha")
    backend = ScriptBackend([
        _say("a"), _say("b"), _say(FOREMAN_BETA),
        _call("finish_run", {"summary": "BETA v1: claims, nothing run"}),
        _say("HANDOFF: REWORK: actually run it and quote the output"),
        _call("finish_run", {"summary": "BETA v2: ran it, output quoted"}),
        _say("HANDOFF: ACCEPT"),
        _say("a2"), _say("b2"), _say(DEBRIEF_DOC),
    ])
    _run_day(project, cfg, backend)
    # the sent-back worker saw its old report and the watcher's objection
    rework_call = backend.calls[5]
    assert "BETA v1" in rework_call[1]["content"]
    assert "sent it back: actually run it" in rework_call[1]["content"]
    assert rework_call[0]["content"].startswith("You are a SUB-AGENT")
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert "BETA v2" in log  # the record carries the corrected report
    assert "[handoff — alpha: sent back: actually run it and quote the output; " \
           "accepted]" in log


def test_handoff_rework_cap_files_as_is(project, cfg, duo):
    cfg.set("workday_supervisor", "alpha")
    cfg.set("workday_rework_rounds", 0)
    backend = ScriptBackend(_supervised_day_script(
        [_say("HANDOFF: REWORK: not good enough")]))
    _run_day(project, cfg, backend)
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert "no rework left, filed as-is" in log
    assert "BETA v1" in log  # the report still made the record
    assert len(backend.calls) == 8  # exactly one worker run, no rework


def test_handoff_watcher_unreachable_fails_open(project, cfg, duo):
    cfg.set("workday_supervisor", "alpha")
    backend = ScriptBackend(_supervised_day_script(
        [LLMTransportError("watcher endpoint down")]))
    result = _run_day(project, cfg, backend)
    assert "Alpha audited, Beta fixed." in result.final_text
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert "could not be reached — filed as-is" in log


def test_handoff_no_verdict_fails_open(project, cfg, duo):
    cfg.set("workday_supervisor", "alpha")
    backend = ScriptBackend(_supervised_day_script(
        [_say("I have opinions but forget the format")]))
    _run_day(project, cfg, backend)
    log = (workday.days_dir(project) / "0001-sort-out-the-parser.log.md").read_text()
    assert "no verdict from the watcher — filed as-is" in log


def test_watcher_never_referees_own_work(project, cfg, duo):
    # foreman flops -> the whole task falls to beta, who IS the watcher:
    # no self-handoff may happen.
    cfg.set("workday_supervisor", "beta")
    backend = ScriptBackend([
        _say("a"), _say("b"), _say("no format here"),
        _call("finish_run", {"summary": "BETA REPORT: did the whole task"}),
        _say("a2"), _say("b2"), _say(DEBRIEF_DOC),
    ])
    _run_day(project, cfg, backend)
    assert len(backend.calls) == 7  # no handoff completion anywhere
    lines = [json.loads(l) for l in
             (project.runs_dir / "0001" / "transcript.jsonl").read_text().splitlines()]
    assert not any(e["role"] == "handoff" for e in lines)


# ---- the harvest: lessons become skills ------------------------------------------
def test_harvest_banks_a_skill_when_skills_are_on(project, cfg, duo):
    cfg.set("skills_enabled", True)
    backend = ScriptBackend(_full_day_script() + [
        _call("write_skill", {"name": "parser-audit",
                              "content": "how to audit the parser\n\nsteps here"}),
        _say("nothing more to bank"),
    ])
    _run_day(project, cfg, backend)
    sk = skills_mod.get(project, "parser-audit")
    assert sk is not None
    assert sk.description == "how to audit the parser"


def test_harvest_skipped_when_skills_off(project, cfg, duo):
    backend = ScriptBackend(_full_day_script())
    _run_day(project, cfg, backend)
    assert backend.turns == []  # exactly the day's calls, no harvest pass


def test_harvest_tools_are_skills_only(project, cfg, duo):
    cfg.set("skills_enabled", True)
    seen = {}

    class SpyBackend(ScriptBackend):
        def chat(self, messages, tools=None, tool_choice=None):
            if tools is not None:
                seen["tools"] = sorted(t["function"]["name"] for t in tools)
            return super().chat(messages, tools=tools, tool_choice=tool_choice)

    # worker calls pass tools too — the LAST tools seen are the harvest's
    backend = SpyBackend(_full_day_script() + [_say("nothing to bank")])
    _run_day(project, cfg, backend)
    assert seen["tools"] == ["load_skill", "write_skill"]


# ---- the record ------------------------------------------------------------------
def test_list_days_and_latest_debrief(project, cfg, duo):
    assert workday.list_days(project) == []
    assert workday.latest_debrief(project) is None
    _run_day(project, cfg, ScriptBackend(_full_day_script()))
    days = workday.list_days(project)
    assert [p.name for p in days] == ["0001-sort-out-the-parser.md"]
    name, text = workday.latest_debrief(project)
    assert name == "0001-sort-out-the-parser.md"
    assert "Alpha audited" in text
