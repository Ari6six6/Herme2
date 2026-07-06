"""Package assembly: turn project state + a new prompt into [system, user].

This is a pure function of inputs, with a hard per-section budget so the
prompt can never creep — the failure mode of the previous app. Budgets scale
down automatically when the served context window is small.
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path

from hermes.config import Config, read_persona
from hermes.project import Project

APPROX_CHARS_PER_TOKEN = 4
PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def _template(name: str) -> str:
    """Read a prompt template once and cache it. These files are static for the
    life of the process, yet several are read on every agent turn — caching keeps
    that off the hot path without changing the text."""
    return (PROMPTS_DIR / name).read_text()


# Fraction of the total package budget given to each section.
SECTION_SHARES = {
    "mission": 0.20,
    "history": 0.15,
    "summaries": 0.30,
    "last_reply": 0.10,
    "notes": 0.15,
    "workspace": 0.10,
}


_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")


def render(template: str, variables: dict) -> str:
    """Substitute {{key}} placeholders in a single pass. Substituted values are
    NOT rescanned, so a value that itself contains a {{...}} sequence (e.g. a
    project named "{{date}}") can't bleed into a later placeholder. Unknown
    placeholders are left untouched."""
    return _PLACEHOLDER_RE.sub(
        lambda m: str(variables[m.group(1)]) if m.group(1) in variables else m.group(0),
        template,
    )


def truncate_keep_tail(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return "[...truncated...]\n" + text[-max_chars:]


def truncate_keep_head(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[...truncated...]"


def package_budget_chars(cfg: Config, context_window: int) -> int:
    budget_tokens = cfg.get("package_budget_tokens", 10000)
    if context_window:
        # Leave room for tool schemas, the system prompt, the in-run tool
        # loop, and the model's output.
        budget_tokens = min(budget_tokens, int(context_window * 0.30))
    return max(budget_tokens, 1500) * APPROX_CHARS_PER_TOKEN


def runtime_status_text(env: dict) -> str:
    """The volatile runtime status: date, GPU, managed hosts, context window.
    These bytes change between calls, so under prefix-cache ordering they move
    out of the early header and ride late in the user message instead."""
    ctx = env.get("context_window") or 0
    window = f"~{ctx} tokens" if ctx else "unknown (assume modest)"
    return (
        f"Date: {time.strftime('%Y-%m-%d')} · GPU: {env.get('gpu_status', 'not attached')}\n"
        f"Managed hosts: {env.get('managed_hosts', 'none')}\n"
        f"Context window: {window} — plan your reading and output accordingly."
    )


def build_system_prompt(project: Project, env: dict, cfg: Config | None = None) -> str:
    from hermes.tools import toolbox_catalog

    template = _template("system.md")
    # Prefix-cache ordering (feature 5): keep the volatile status out of the
    # stable header so the header + persona + tools + skills index are a
    # byte-identical prefix across calls. Off by default (status stays inline).
    prefix_order = cfg is not None and cfg.get("prefix_cache_order", False)
    runtime_status = "" if prefix_order else "\n" + runtime_status_text(env)
    variables = {
        "model_identity": env.get("model_identity", "Hermes (NousResearch Hermes-4.3-36B)"),
        "project_name": project.name,
        "project_dir": str(project.root),
        "remote_workspace": env.get("remote_workspace", "~/hermes-workspace"),
        "runtime_status": runtime_status,
        "toolbox_catalog": toolbox_catalog(),
    }
    system = render(template, variables)
    if cfg is None or cfg.get("directive_header_rule", True):
        from hermes.directives import RECENCY_HEADER_LINE
        system += "\n\n" + RECENCY_HEADER_LINE
    if cfg is not None and cfg.get("verify_before_done", False):
        system += (
            "\n\nVerification rule: never report a task done until you have "
            "EXECUTED a verification step this run — run the script, run the "
            "tests, or hit the endpoint — and seen the real output. Written but "
            "not run is not done; say \"written, not yet run\" and then run it."
        )
    if cfg is not None and cfg.get("skills_enabled", False):
        from hermes import skills as skills_mod
        idx = skills_mod.index(project)
        if idx:
            system += (
                "\n\n## Skills — load the full procedure before you need it\n\n"
                "Each line is a skill you can pull in full with `load_skill(name)`. "
                "When one covers the task at hand, load it first — it holds the "
                "gotchas. After a task that took real figuring-out, capture what you "
                "learned with `write_skill` (or update an existing skill).\n\n" + idx
            )
    phase = recon_build_block(project) or build_mode_block(project, env)
    if phase:
        system += "\n\n" + phase
    guidance = (env.get("model_tool_guidance") or "").strip()
    if guidance:
        # Model-specific tool-calling discipline. Empty for the baseline model,
        # so its system prompt is byte-for-byte what it always was.
        system += "\n\n## Operating notes for this model\n\n" + guidance
    persona = read_persona().strip()
    if persona:
        system += "\n\n## Persona\n\n" + persona
    return system


def _stack_line(manifest: dict) -> str:
    stack = manifest.get("stack") or {}
    from hermes.twin.recon import StackReport
    return StackReport(**stack).summary() if stack else "(not fingerprinted yet)"


def recon_build_block(project: Project) -> str:
    """When a twin exists but isn't sealed yet, this is the builder phase: the
    agent's job is to reconstruct the target's real webserver into the twin and
    seal it. Returns "" when there's no open twin."""
    try:
        twin = project.twin()
    except Exception:
        return ""
    if not twin.exists() or twin.is_sealed():
        return ""
    manifest = twin.read_manifest()
    return render(_template("recon_build.md"), {
        "source": manifest.get("source", "(unknown)"),
        "exchange_count": manifest.get("exchange_count", len(twin.exchanges())),
        "stack": _stack_line(manifest),
    })


def build_mode_block(project: Project, env: dict | None = None) -> str:
    """When a sealed twin exists, tell the agent plainly: it is building against a
    faithful, SAFE copy of the target — a safe execution environment — not the
    live system. This is what unlocks it to work freely without tripping the
    don't-touch-live-servers reflex."""
    try:
        twin = project.twin()
    except Exception:
        return ""
    if not twin.is_sealed():
        return ""
    env = env or {}
    manifest = twin.read_manifest()
    twin_url = f"http://127.0.0.1:{env.get('twin_port', 8900)}"
    live_touch = env.get("build_live_touch", False)
    network_note = (
        "Live-touch tools (`twin_expand`, `twin_reground`) and general web access "
        "are available this run, so you technically CAN reach the live target — "
        "don't, unless the operator explicitly asked you to."
        if live_touch else
        "This run has NO path to the live target at all: no `http_request`, no "
        "`web_search`, no `twin_expand`/`twin_reground` are even registered. "
        "Everything reachable from here is the twin. If the twin is missing "
        "something you need, say so in your summary — the operator grows it with "
        "`run build`."
    )
    return render(_template("build_mode.md"), {
        "source": manifest.get("source", "(unknown)"),
        "exchange_count": manifest.get("exchange_count", 0),
        "stack": _stack_line(manifest),
        "mission": manifest.get("mission") or "(set the mission with `mission edit`)",
        "win_condition": manifest.get("win_condition")
        or "your solution is correct and behaves like the twin on every input you check",
        "twin_url": twin_url,
        "network_note": network_note,
    })


def assemble(project: Project, prompt: str, env: dict, cfg: Config) -> list[dict]:
    """Build the two-message package. `env` carries gpu_status,
    remote_workspace and context_window (0 if unknown)."""
    total_chars = package_budget_chars(cfg, env.get("context_window") or 0)
    budget = {k: int(total_chars * share) for k, share in SECTION_SHARES.items()}

    mission = truncate_keep_head(project.read_mission().strip(), budget["mission"])

    # Directive reconciliation (feature 1): when on, the distilled directives.md
    # is the authoritative standing-instruction channel and only the last K raw
    # prompts ride along. When off, the full recent prompt log is sent as before.
    directives_on = cfg.get("directives_enabled", False)
    if directives_on:
        directives = truncate_keep_head(
            project.read_directives().strip(), budget["history"]
        )
        history_entries = project.recent_prompts(cfg.get("directives_recent_k", 5))
    else:
        directives = ""
        history_entries = project.recent_prompts(cfg.get("history_max_prompts", 30))
    history_lines = [
        f"[{e.get('run', '?'):>4}] {e.get('text', '')}" for e in history_entries
    ]
    history = truncate_keep_tail("\n".join(history_lines), budget["history"])

    summary_entries = project.recent_summaries(cfg.get("summaries_max", 8))
    summary_blocks = [
        f"## Run {run_id:04d}\n{text}" for run_id, text in summary_entries
    ]
    summaries = truncate_keep_tail("\n\n".join(summary_blocks), budget["summaries"])

    last = project.last_final_reply()
    if last:
        last_run, last_text = last
        last_reply_block = (
            f"# YOUR LAST REPLY (run {last_run:04d}, verbatim — the operator may "
            "refer to it)\n" + truncate_keep_tail(last_text, budget["last_reply"])
        )
    else:
        last_reply_block = "# YOUR LAST REPLY\n(none yet)"

    notes = truncate_keep_tail(project.read_notes().strip(), budget["notes"])
    workspace = truncate_keep_head(project.workspace_listing(), budget["workspace"])

    if directives_on:
        history_header = (
            "# RECENT PROMPTS (operator, last few verbatim — the full history is "
            "distilled into DIRECTIVES above; recency wins there)"
        )
    else:
        history_header = "# PROMPT HISTORY (operator, oldest first)"

    sections = ["# MISSION\n" + (mission or "(empty)")]
    if directives_on:
        sections.append(
            "# DIRECTIVES (authoritative standing instructions — obey these; "
            "when they conflict with an old prompt, these win)\n"
            + (directives or "(none yet — none distilled)")
        )
    # Under prefix-cache ordering the volatile runtime status was pulled out of
    # the stable header; re-add it here, after the slow-changing mission and
    # directives and before the already-volatile history/summaries tail.
    if cfg.get("prefix_cache_order", False):
        sections.append("# RUNTIME STATUS\n" + runtime_status_text(env))
    sections += [
        history_header + "\n" + (history or "(none yet)"),
        "# RUN SUMMARIES (your own past runs)\n" + (summaries or "(none yet)"),
        last_reply_block,
        "# NOTES (your own)\n" + (notes or "(none)"),
        "# WORKSPACE\n" + workspace,
        "# CURRENT REQUEST\n" + prompt.strip(),
    ]
    user = "\n\n".join(sections)

    return [
        {"role": "system", "content": build_system_prompt(project, env, cfg)},
        {"role": "user", "content": user},
    ]


def reconcile_prompt() -> str:
    return _template("reconcile.md")


def compact_prompt() -> str:
    return _template("compact.md")


def skills_nudge() -> str:
    return _template("skills_nudge.md").strip()


def subagent_prompt() -> str:
    return _template("subagent.md")


def verify_before_done_nudge() -> str:
    return _template("verify_before_done.md").strip()


def summary_nudge() -> str:
    return _template("summary.md").strip()


def wrapup_warning() -> str:
    return _template("wrapup.md").strip()


def time_wrapup_warning() -> str:
    return _template("time_wrapup.md").strip()


def phantom_nudge() -> str:
    return _template("phantom.md").strip()


def build_proof_nudge() -> str:
    return _template("build_proof.md").strip()


def verifier_prompt() -> str:
    return _template("verifier.md").strip()


def antithesis_prompt() -> str:
    return _template("antithesis.md").strip()


def planner_prompt() -> str:
    return _template("planner.md")


def planner_request(project, request: str) -> str:
    """The planner brief: mission + winning condition + the operator's request."""
    manifest = {}
    try:
        manifest = project.twin().read_manifest()
    except Exception:
        pass
    mission = manifest.get("mission") or "(no explicit mission set)"
    win = manifest.get("win_condition") or "behave like the twin on every input checked"
    return (
        f"Mission: {mission}\n"
        f"Winning condition: {win}\n\n"
        "Operator request:\n"
        f"{request.strip()}\n\n"
        "Produce the execution checklist."
    )


def plan_brief(plan: str) -> str:
    return (
        "# PLAN (from the planner — execute against this, in order; the "
        "antithesis will check each point)\n" + plan.strip()
    )


def referee_prompt() -> str:
    return _template("referee.md")


def referee_request(request: str, files: list[str], antithesis_report: str) -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "(none reported)"
    return (
        "The builder was asked:\n\n"
        f"{request.strip()}\n\n"
        "Files the builder wrote or changed:\n"
        f"{file_list}\n\n"
        "The antithesis's latest report (it says the solution does NOT pass):\n"
        f"{(antithesis_report or '(no report)').strip()}\n\n"
        "Investigate against the twin yourself and make the final, binding call."
    )


def referee_failed(reason: str) -> str:
    return (
        "A REFEREE was brought in because you and the independent checker kept "
        "disagreeing, and with fresh eyes on the real sandbox it ruled against "
        "the current solution:\n\n"
        f"{reason.strip()}\n\n"
        "This is the binding call. Fix the real problem it identified, verify it "
        "yourself by running the code and the twin, and only then finish."
    )


def antithesis_request(project, request: str, files: list[str]) -> str:
    """The antithesis brief: break this solution against the twin."""
    file_list = "\n".join(f"- {f}" for f in files) if files else "(none reported)"
    manifest = {}
    try:
        manifest = project.twin().read_manifest()
    except Exception:
        pass
    mission = manifest.get("mission") or request.strip()
    win = manifest.get("win_condition") or "(no explicit winning condition set)"
    return (
        "A build agent was asked:\n\n"
        f"{request.strip()}\n\n"
        f"Mission: {mission}\n"
        f"Winning condition: {win}\n\n"
        "It claims to have met the winning condition, writing/changing these files:\n"
        f"{file_list}\n\n"
        "Break it against the twin now: run the real solution and the twin on real "
        "inputs, compare their actual outputs, then give your VERDICT."
    )


def verifier_request(request: str, files: list[str]) -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "(none reported)"
    return (
        "The agent was asked:\n\n"
        f"{request.strip()}\n\n"
        "It claims to have finished, writing/changing these files:\n"
        f"{file_list}\n\n"
        "Verify it independently in the sandbox now, then give your VERDICT."
    )


def verify_failed(report: str) -> str:
    return (
        "An INDEPENDENT verification pass ran your code in the real sandbox and "
        "it did NOT pass:\n\n"
        f"{report.strip()}\n\n"
        "That is ground truth from the sandbox, not an opinion, and it overrides "
        "your own conclusion. Do not finish again until you have fixed the real "
        "problem: read the failure, change the code (`edit_file` / `write_file`), "
        "run the actual program yourself and read its real output, and only then "
        "`finish_run`."
    )


def self_build_verifier_prompt() -> str:
    return _template("self_build_verifier.md").strip()


def self_build_verifier_request(request: str, files: list[str]) -> str:
    file_list = "\n".join(f"- {f}" for f in files) if files else "(none reported)"
    return (
        "The agent was asked:\n\n"
        f"{request.strip()}\n\n"
        "It edited HERMES' OWN SOURCE — these files:\n"
        f"{file_list}\n\n"
        "Re-run Hermes' test suite on the VPS with local_shell now "
        "(`python -m pytest tests/ -q`), read the real result, then give your "
        "VERDICT. The source lives on the VPS, not the sandbox."
    )


def self_build_verify_failed(report: str) -> str:
    return (
        "An INDEPENDENT verification pass re-ran Hermes' OWN test suite after "
        "your self-edit and it did NOT pass:\n\n"
        f"{report.strip()}\n\n"
        "That is ground truth from the test run, not an opinion. A self-edit "
        "that reddens Hermes' tests is not done and it overrides your own "
        "conclusion. Read the failure, fix the source (`edit_hermes_source` / "
        "`write_hermes_source`), re-run `python -m pytest tests/` yourself and "
        "read the real output, and only then `finish_run`."
    )


def stall_nudge(repeated: bool = False) -> str:
    text = _template("stall.md").strip()
    if repeated:
        text += (
            "\n\nYou have now sent essentially the same message twice without "
            "acting. Stop announcing and make the tool call NOW."
        )
    return text
