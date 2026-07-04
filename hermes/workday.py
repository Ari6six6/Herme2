"""The workday (feature 11): one operator prompt = one full day of the cast.

The chain of command, top to bottom: the operator hands down today's task; the
cast convenes at a MORNING BRIEFING over the mission, yesterday's debrief and
the task; the foreman cuts assignments from that discussion; each assigned
persona works its brief as a subagent child — real tools, the normal gates;
the cast convenes again at an EVENING DEBRIEF over the reports; the scribe
writes the day up — what happened, where the mission stands, what tomorrow
inherits. That debrief is both the reply the operator reads and the carryover
the NEXT day's briefing opens with, so days chain: task → work → debrief →
tomorrow's briefing. Optionally the day closes by banking what was learned
into skills (feature 3) — the self-improvement loop.

To the operator the CLI is unchanged: `run <task>` does all of this under the
hood when workday_enabled is on, and `hey <name>, ...` still pulls one persona
aside directly, skipping the day. A day is also a run: it gets a run dir, its
transcript carries every room and every worker, and the debrief becomes the
run summary future packages inherit.

The rooms (briefing/debrief) have no tools — deliberation only, no
confirm/taint surface. The workers dispatch through the same registry, the
same confirm and the same taint rail as any delegate child: the day changes
who does the work, never what the work is allowed to touch.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from hermes import hosts as hosts_mod
from hermes import package
from hermes import personas as personas_mod
from hermes import subagent
from hermes.llm import LLMTransportError
from hermes.tools import ToolRegistry, build_registry
from hermes.tools.base import ToolContext
from hermes.ui import cyan, dim, green, magenta, yellow

ASSIGNMENT_RE = re.compile(r"^ASSIGNMENT:\s*([A-Za-z0-9_-]+)\s*:\s*(.+)$", re.M)
HANDOFF_RE = re.compile(r"HANDOFF:\s*(ACCEPT|REWORK)(?:\s*:\s*(.+))?", re.I)
_SLUG_RE = re.compile(r"[^a-z0-9]+")
_ID_RE = re.compile(r"^(\d{4})-")
CAST_MAX = 4  # personas on shift — same cap as a council convening
MISSION_HEAD_CHARS = 2000


def days_dir(project) -> Path:
    return project.root / "days"


def _slug(text: str, max_len: int = 40) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (s[:max_len].rstrip("-")) or "day"


def _next_id(d: Path) -> int:
    last = 0
    if d.is_dir():
        for p in d.iterdir():
            m = _ID_RE.match(p.name)
            if m:
                last = max(last, int(m.group(1)))
    return last + 1


def latest_debrief(project) -> tuple[str, str] | None:
    """(filename, text) of the newest day's debrief — tomorrow's carryover."""
    d = days_dir(project)
    if not d.is_dir():
        return None
    debriefs = sorted(p for p in d.glob("*.md")
                      if not p.name.endswith(".log.md") and _ID_RE.match(p.name))
    if not debriefs:
        return None
    p = debriefs[-1]
    try:
        return p.name, p.read_text()
    except OSError:
        return None


def list_days(project) -> list[Path]:
    d = days_dir(project)
    if not d.is_dir():
        return []
    return sorted(p for p in d.glob("*.md")
                  if not p.name.endswith(".log.md") and _ID_RE.match(p.name))


def parse_assignments(text: str, cast, max_workers: int):
    """Cut the foreman's ASSIGNMENT lines into [(Persona, brief)]. Names not on
    shift are dropped; the list is capped. An empty result is the caller's
    fallback signal — the day must never die on a malformed dispatch."""
    catalog = {p.name: p for p in cast}
    out = []
    for name, brief in ASSIGNMENT_RE.findall(text or ""):
        p = personas_mod.resolve(catalog, name)
        if p is not None and brief.strip():
            out.append((p, brief.strip()))
        if len(out) >= max(1, int(max_workers)):
            break
    return out


def _fallback_worker(cast):
    """No parseable assignments — the whole task goes to one pair of hands.
    Prefer a persona with an unrestricted tool posture (the builder)."""
    for p in cast:
        if p.tools is None:
            return p
    return cast[0]


def _transcript_text(entries) -> str:
    return "\n\n".join(f"## round {rnd} — {name}\n\n{text}"
                       for rnd, name, text in entries)


def run_day(project, task, cfg, backend, gpu=None, env=None, confirm_fn=None,
            sandbox=None):
    """Run one full day and return a RunResult (the debrief is the final text)."""
    from hermes.agent import RunResult, _think_re, strip_think
    from hermes.models import resolve as resolve_model

    if confirm_fn is None:
        from hermes.confirm import confirm as confirm_fn
    env = env or {}
    spec = resolve_model(cfg)
    think_re = _think_re(spec.think_tags)

    run_id, run_dir = project.new_run()
    transcript_path = run_dir / "transcript.jsonl"

    def log(entry: dict):
        with transcript_path.open("a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    project.append_history(run_id, task)

    max_chars = cfg.get("persona_max_chars", 2000)
    catalog = personas_mod.load_all(project, max_chars)
    cast = [catalog[n] for n in sorted(catalog)][:CAST_MAX]
    roster = personas_mod.index(catalog={p.name: p for p in cast})
    room_budget = max(1000, int(cfg.get("council_transcript_chars", 24000)))
    clock = float(cfg.get("workday_max_seconds", 1800))
    start = time.monotonic()
    calls = 0  # every backend completion the day burns

    def _shown(content) -> str:
        return strip_think(content, think_re)

    def _convene(member_prompt: str, day_papers: str, rounds: int, role: str):
        """One room: round-robin, no tools. Returns the entries spoken; cuts
        to the caller on the day clock or a dead backend."""
        nonlocal calls
        entries: list[tuple[int, str, str]] = []
        for rnd in range(1, max(0, int(rounds)) + 1):
            for p in cast:
                if time.monotonic() - start > clock:
                    log({"role": role, "content": "(room cut short: day clock)"})
                    return entries
                system = package.render(member_prompt, {
                    "name": p.name, "voice": p.voice, "roster": roster,
                })
                so_far = package.truncate_keep_tail(
                    _transcript_text(entries), room_budget
                )
                user = (
                    day_papers + "\n\n# THE ROOM SO FAR\n"
                    + (so_far or "(nothing yet — you open the discussion)")
                    + f"\n\nYou speak now, {p.name}. React to the others; be brief."
                )
                try:
                    result = backend.chat([
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ])
                except LLMTransportError:
                    log({"role": role, "content": "(room cut short: backend unreachable)"})
                    return entries
                calls += 1
                text = _shown(result.content) or "(said nothing)"
                entries.append((rnd, p.name, text))
                log({"role": role, "name": p.name, "content": text})
                print(magenta(f"  [{role}·{p.name}] ") + dim(text.splitlines()[0][:110]))
        return entries

    # ---- morning briefing -------------------------------------------------
    mission = package.truncate_keep_head(
        project.read_mission().strip(), MISSION_HEAD_CHARS
    )
    carryover = latest_debrief(project)
    yesterday = carryover[1] if carryover else "(first day — no debrief yet)"
    papers = (
        "# MISSION (global, across days)\n" + (mission or "(empty)")
        + "\n\n# YESTERDAY'S DEBRIEF (carryover)\n"
        + package.truncate_keep_tail(yesterday, room_budget)
        + "\n\n# TODAY'S TASK (from the operator)\n" + task.strip()
    )
    print(dim(f"  morning briefing — {', '.join(p.name for p in cast)}"))
    briefing = _convene(package.briefing_member_prompt(), papers,
                        cfg.get("workday_briefing_rounds", 1), "briefing")

    # The foreman cuts assignments from the discussion. Fail open: a dead or
    # incoherent foreman means the whole task goes to one worker, never no day.
    assignments = []
    try:
        foreman_system = package.render(package.foreman_prompt(), {
            "roster": roster,
            "max_workers": str(cfg.get("workday_max_workers", 3)),
        })
        foreman_user = (papers + "\n\n# THE BRIEFING\n"
                        + package.truncate_keep_tail(_transcript_text(briefing),
                                                     room_budget))
        result = backend.chat([
            {"role": "system", "content": foreman_system},
            {"role": "user", "content": foreman_user},
        ])
        calls += 1
        assignments = parse_assignments(_shown(result.content), cast,
                                        cfg.get("workday_max_workers", 3))
    except LLMTransportError:
        pass
    if not assignments:
        worker = _fallback_worker(cast)
        assignments = [(worker, task.strip())]
        log({"role": "foreman", "content": f"(no parseable assignments — "
                                           f"whole task to {worker.name})"})
    for p, brief in assignments:
        log({"role": "assignment", "name": p.name, "content": brief})
        print(cyan(f"  assignment → {p.name}: ") + dim(brief[:100]))

    # ---- the work ----------------------------------------------------------
    host_records = hosts_mod.load_hosts()
    registry = build_registry(project, cfg, confirm_fn)
    ctx = ToolContext(
        project=project, cfg=cfg, gpu=gpu, sandbox=sandbox,
        hosts={n: hosts_mod.host_endpoint(r) for n, r in host_records.items()},
        confirm=confirm_fn, served_ctx=env.get("context_window", 0),
        backend=backend, think_re=think_re, depth=0,
    )
    ctx.registry = registry
    ctx._delegate_log = log

    # The watcher (the handoff supervisor): every worker that finishes reports
    # off to this persona, who asks the process question and may send the
    # report back. Fails open — no watcher on shift means reports file as-is.
    sup_name = str(cfg.get("workday_supervisor", "owl") or "").strip()
    sup = (personas_mod.resolve({p.name: p for p in cast}, sup_name)
           if sup_name else None)

    def _handoff_verdict(worker, brief, report):
        """One completion, no tools: the watcher reads the report and rules.
        Any failure — dead backend, no verdict line — accepts the report."""
        nonlocal calls
        system = package.render(package.supervisor_prompt(), {
            "name": sup.name, "voice": sup.voice,
        })
        user = (
            "# TODAY'S TASK\n" + task.strip()
            + f"\n\n# THE ASSIGNMENT ({worker.name})\n" + brief
            + "\n\n# THE WORKER'S REPORT (they just finished and reported off)\n"
            + package.truncate_keep_tail(report, room_budget)
        )
        try:
            result = backend.chat([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
        except LLMTransportError:
            return "ACCEPT", "(the watcher could not be reached — filed as-is)"
        calls += 1
        found = HANDOFF_RE.findall(_shown(result.content) or "")
        if not found:
            return "ACCEPT", "(no verdict from the watcher — filed as-is)"
        verdict, note = found[-1]  # last match wins, like every verdict rail
        return verdict.upper(), (note or "").strip()

    def _supervise(worker, brief, report, worker_brief):
        """The handoff loop: verdict, then at most workday_rework_rounds
        send-backs. Returns (final report, the trail for the record)."""
        events = []
        reworks_left = max(0, int(cfg.get("workday_rework_rounds", 1)))
        while True:
            verdict, note = _handoff_verdict(worker, brief, report)
            log({"role": "handoff", "name": worker.name,
                 "content": f"{sup.name}: {verdict}" + (f": {note}" if note else "")})
            if verdict != "REWORK":
                events.append(note or "accepted")
                print(dim(f"  handoff {worker.name} → {sup.name}: ")
                      + green("accepted"))
                break
            if reworks_left <= 0 or time.monotonic() - start > clock:
                events.append(f"sent back ({note}) — no rework left, filed as-is")
                print(dim(f"  handoff {worker.name} → {sup.name}: ")
                      + yellow("sent back, out of rework — filed as-is"))
                break
            reworks_left -= 1
            events.append(f"sent back: {note}")
            print(dim(f"  handoff {worker.name} → {sup.name}: ")
                  + yellow(f"sent back — {note[:80]}"))
            rework_brief = (
                worker_brief
                + "\n\nYour previous report:\n" + report
                + f"\n\nThe watcher ({sup.name}) sent it back: {note}\n"
                "Address exactly that objection and file a corrected conclusion."
            )
            report = subagent.run_child(ctx, rework_brief, [], cfg, log=log,
                                        persona=worker, max_turns=worker_turns)
            log({"role": "report", "name": worker.name, "content": report})
        return report, f"{sup.name}: " + "; ".join(events)

    reports: list[tuple[str, str, str, str]] = []  # (persona, brief, conclusion, trail)
    worker_turns = cfg.get("workday_worker_turns", 14)
    for p, brief in assignments:
        if time.monotonic() - start > clock:
            reports.append((p.name, brief, "(skipped — the day's clock ran out "
                                           "before this assignment started)", ""))
            continue
        worker_brief = (
            f"Today's operator task:\n{task.strip()}\n\n"
            f"Your assignment from the morning briefing:\n{brief}\n\n"
            "Mission context:\n" + (mission or "(empty)") + "\n\n"
            "Work the assignment with your tools. Your finish_run conclusion "
            "is read out at the evening debrief — make it factual."
        )
        print(dim(f"  {p.name} clocks in"))
        out = subagent.run_child(ctx, worker_brief, [], cfg, log=log,
                                 persona=p, max_turns=worker_turns)
        log({"role": "report", "name": p.name, "content": out})
        trail = ""
        if sup is not None and sup.name != p.name:  # no one referees their own work
            out, trail = _supervise(p, brief, out, worker_brief)
        reports.append((p.name, brief, out, trail))

    # ---- evening debrief ----------------------------------------------------
    reports_block = "\n\n".join(
        f"## {name} — assignment: {brief}\n\n{out}"
        + (f"\n\n[handoff — {trail}]" if trail else "")
        for name, brief, out, trail in reports
    )
    evening_papers = (
        "# TODAY'S TASK (from the operator)\n" + task.strip()
        + "\n\n# THE DAY'S REPORTS\n"
        + package.truncate_keep_tail(reports_block, room_budget)
    )
    print(dim("  evening debrief"))
    debrief_talk = _convene(package.debrief_member_prompt(), evening_papers,
                            cfg.get("workday_debrief_rounds", 1), "debrief")

    scribe_user = (
        "# MISSION (global, across days)\n" + (mission or "(empty)")
        + "\n\n" + evening_papers
        + "\n\n# THE DEBRIEF DISCUSSION\n"
        + (package.truncate_keep_tail(_transcript_text(debrief_talk),
                                      room_budget) or "(the room was skipped)")
    )
    try:
        result = backend.chat([
            {"role": "system", "content": package.debrief_scribe_prompt()},
            {"role": "user", "content": scribe_user},
        ])
        calls += 1
        debrief = _shown(result.content) or ""
    except LLMTransportError:
        debrief = ""
    if not debrief:
        # The operator is owed a record even with a dead scribe: the raw reports.
        debrief = ("(the scribe could not write the day up — raw reports "
                   "follow)\n\n" + reports_block)
    log({"role": "debrief-scribe", "content": debrief})

    # ---- write the day ------------------------------------------------------
    d = days_dir(project)
    d.mkdir(parents=True, exist_ok=True)
    base = f"{_next_id(d):04d}-{_slug(task)}"
    worked = ", ".join(f"{name} ({brief[:40]})" for name, brief, _, _ in reports)
    meta = (f"# Day {base} — {task.strip()}\n\n"
            f"Cast: {', '.join(p.name for p in cast)} · worked: {worked}\n")
    (d / f"{base}.md").write_text(meta + "\n" + debrief.rstrip() + "\n")
    day_log = (
        meta
        + "\n# MORNING BRIEFING\n\n" + (_transcript_text(briefing) or "(skipped)")
        + "\n\n# ASSIGNMENTS\n\n"
        + "\n".join(f"- {p.name}: {brief}" for p, brief in assignments)
        + "\n\n# REPORTS\n\n" + (reports_block or "(none)")
        + "\n\n# EVENING DEBRIEF\n\n"
        + (_transcript_text(debrief_talk) or "(skipped)")
        + "\n\n# DEBRIEF (as written)\n\n" + debrief.rstrip() + "\n"
    )
    (d / f"{base}.log.md").write_text(day_log)

    summary = package.truncate_keep_head(
        f"Workday {base}. Reports from: "
        + ", ".join(name for name, _, _, _ in reports) + ".\n\n" + debrief, 1200
    )
    (run_dir / "summary.md").write_text(summary + "\n")
    (run_dir / "final.md").write_text(debrief + "\n")

    print()
    print(debrief)
    print(green(f"\n[day {base} complete — {len(reports)} report(s), "
                f"{calls} completion(s)] ") + dim(str(d / f"{base}.md")))

    # ---- harvest: the day's lessons become skills ---------------------------
    if cfg.get("skills_enabled", False) and cfg.get("workday_skill_harvest", True):
        _harvest(project, cfg, backend, ctx, reports_block, log, think_re)

    return RunResult(run_id, summary, debrief, turns=calls)


def _harvest(project, cfg, backend, ctx, reports_block, log, think_re) -> None:
    """The self-improvement loop: a bounded pass with ONLY the skills tools,
    invited to bank what the day taught. Never touches the debrief."""
    from hermes.agent import _assistant_msg, strip_think
    from hermes.tools import skills as skills_tools

    reg = ToolRegistry()
    for t in skills_tools.TOOLS:
        reg.register(t)
    msgs = [{"role": "user", "content":
             package.harvest_prompt() + "\n\n# THE DAY'S REPORTS\n"
             + package.truncate_keep_tail(reports_block, 12000)}]
    for _ in range(max(1, int(cfg.get("skills_nudge_max_turns", 3)))):
        try:
            result = backend.chat(msgs, tools=reg.schemas())
        except LLMTransportError:
            return
        shown = strip_think(result.content, think_re)
        log({"role": "harvest", "content": result.content,
             "tool_calls": [{"name": tc.name, "arguments": tc.arguments}
                            for tc in result.tool_calls]})
        if shown:
            print(magenta("  [harvest] ") + dim(shown.splitlines()[0][:110]))
        if not result.tool_calls:
            return
        msgs.append(_assistant_msg(result))
        for tc in result.tool_calls:
            out = reg.dispatch(tc.name, tc.arguments, ctx)
            if tc.name == "write_skill" and not out.startswith(("ERROR", "DENIED")):
                print(green("  (lesson banked as a skill)"))
            log({"role": "harvest-tool", "name": tc.name, "content": out})
            msgs.append({"role": "tool", "tool_call_id": tc.id, "content": out})
