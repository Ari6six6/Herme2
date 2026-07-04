# HANDOVER — read this before touching anything

You are the successor instance. This file is your briefing: what this
version IS (against the base repo), the role model you build by, and the
frontier. The operator speaks in rambles; your job is to extract the 20%
of signal, bring the 80% of build, and never break the doctrine below.

## What this is

Hermes: a package-per-prompt agent harness for small self-hosted models
(vLLM/llama.cpp on a rented GPU, driven from a phone). **The harness is
not the model.** Every operator prompt rebuilds context from files on
disk; nothing survives a run except what is written down. On top of that
base, this branch (`claude/subagents-multi-persona-0paffv`) built the
multi-persona system the operator calls **the Nine** — read
`docs/THE_NINE.md` (the cosmology, ratified) and `docs/OPERATION.md`
(the protocol frame) before anything else.

## The ledger — this version vs. the base

The base repo ended at feature 8 (directives, compaction, skills,
delegation, prefix-cache, checkpoints, verification, taint). This branch
added, in order:

| # | Feature | Toggle | Where |
|---|---|---|---|
| 9 | Personas: catalog + `hey <name>` + seam A (top-level run as a persona) | `personas_enabled` | `hermes/personas.py`, `hermes/personas/*.md` |
| 9b | Dynamic routing (`PERSONA: <name>\|none` rail, fails open) | `personas_route` | `personas.route`, `prompts/router.md` |
| 9c | Seam B: `delegate(persona=…)` spawns a child AS a persona | — | `subagent.run_child`, `tools/delegate.py` |
| 10 | Council: clocked round-robin deliberation + scribe | `council_enabled` | `hermes/council.py` |
| 11 | Workday: one prompt = one day — briefing → dispatch → work → debrief → carryover | `workday_enabled` | `hermes/workday.py` |
| 11b | Handoff: finished workers report off (`HANDOFF: ACCEPT\|REWORK` rail) | `workday_supervisor` | `workday._supervise` |
| 11c | The Nine as runtime: Odin dispatches, Sveja delivers, Baldur's death = all-reported nightfall, Hawk's roster, domain-admin closing | `workday_room/courier/general` | `workday.py`, `prompts/{odin,courier,roster_call,domain_admin}.md` |
| 12 | Service records: organic identity — jackets in `records/<name>.md`, tail rides in every voice, oldest days forgotten first | `service_records` | `personas.append_record/load_cast` |
| 13 | Landmarks: rendezvous files, briefing must address, night sweeps to `.attended/` | `landmarks_enabled` | `hermes/landmarks.py`, `tools/landmark.py` |
| 14 | The strategy is the domain admin's: `mission.md` amendable at the close (`BEGIN/END STRATEGY`, fails closed) | `workday_amend_strategy` | `workday.py`, `prompts/strategy.md` |

Settled decisions you must NOT relitigate: no renames (project stays
project, `mission.md` keeps its filename but IS the strategy); the cast
is the Nine, same capacities, no tool lines on shipped sheets (the
operator's own sheets MAY narrow); the Hawk IS the general; assignment-
cutting is Odin's; nightfall = all workers reported (clock is backstop);
Sveja is a speaking part; domain admins don't die, they come and go —
the closing debrief IS the successor's briefing; the operator is the
Tenth (the Comet) — his prompt is an artifact passing through, not a
command line into the machinery.

## The role model — how everything here is built

1. **Every feature is a config toggle, default off**, in
   `config.py DEFAULTS` with a comment block, and the off-state is
   provably the prior behavior (there is a byte-identical system-prompt
   test — keep that invariant testable for anything you add).
2. **Everything is plain markdown on disk.** Personas, skills, records,
   landmarks, days, debriefs: one file each, first non-empty line = the
   one-liner, `nano` is the admin interface. No YAML, no DB, no deps
   beyond `httpx` + `prompt_toolkit`.
3. **Decisions travel on verdict rails**: one uppercase line, parsed
   with a regex, LAST match wins (`VERDICT:`, `PERSONA:`, `HANDOFF:`,
   `ASSIGNMENT:`, `STRATEGY:`). Never ask a small model for a schema on
   every reply; ask for one line only where a decision is consumed.
4. **Fail open for convenience, fail closed for writes.** A vacant
   office, a dead backend, a flopped format never kills a run — the
   step is skipped ON THE RECORD. But nothing rewrites an operator's
   file without an explicit marker block, and prior text goes on the
   record before any overwrite.
5. **Never bypass `ctx.confirm` or the taint rail.** Persona tool lists
   only NARROW an already-gated registry (scoping convenience, not a
   security boundary — the docstrings say so; keep saying so). Children
   dispatch through the same tool bodies and the same confirm.
6. **Budgets everywhere.** Every text that enters a prompt goes through
   `truncate_keep_head/tail` with a config budget. Rooms stay small
   (`workday_room`); rosters are one-liners; voices are capped.
7. **Tests are scripted backends, no network.** `ScriptBackend`/
   `MockBackend` patterns (see `tests/test_workday.py`); every fail-open
   path gets a test; count completions to prove vacant seats cost zero.
8. **Ship docs with code**: a feature section in `docs/USAGE.md` (flag
   table + recommended setting), a row in the README capability table,
   a mapping row in `docs/THE_NINE.md`. Design before big builds goes on
   the sheets (`OPERATION.md` / `THE_NINE.md`) — the operator ratifies,
   then you build. Characters are the OPERATOR'S to write; you draft
   only when he hands you the pattern and says so.
9. **The operator's voice is transcribed speech.** Extract intent, name
   your interpretation in one line, build, and always leave him a
   one-flip way back.

## How to extend (recipes)

- **A new character**: drop `<name>.md` into `~/.hermes/personas/` or
  `<project>/personas/` (shadows builtin). First line = capacity,
  optional `aliases:`; body = voice. Nothing else needed — records,
  rooms, dispatch, `hey <name>` all pick it up.
- **A new office** (a protocol seat like courier/general): resolve it
  with `workday._office("workday_<office>", "<default-name>")`, one
  prompt file + `package.<x>_prompt()` accessor, one completion, fail
  open on vacancy/transport, log a transcript role, print one dim line,
  add config key + tests + docs. Copy the roster-call block.
- **A new rail**: `X_RE = re.compile(r"X:\s*(...)")`, findall, take
  `[-1]`, fall back to the safe default on no match.

## The frontier — worth building next (operator-aligned)

1. **Landmark-driven owl** — the owl's full protocol slot: a briefing-
   side strategy check, and the settled question left open on the
   sheets: veto vs. recorded objection (`OPERATION.md` open point).
2. **Council members with read-only tools** — deliberation that can
   LOOK (via `run_child` with a read posture); noted as the planned
   extension in `council.py`.
3. **Records for delegate children** — seam-B persona children don't
   write jackets yet; workday workers and seam-A runs do.
4. **Per-persona sampling** — `ModelSpec` carries sampling; a sheet
   could too (the owl colder, Loki hotter). Plumb through `backend.chat`.
5. **Baldur as scheduler** — multi-mission days: the day holds several
   briefs, Baldur meters what can still start before dark.
6. **Loki's chain formalized** — route network-tainted content through
   a Loki child by protocol, so the adversary handles the outside.

## Verify anything you do

```sh
pip install -e ".[dev]" && python -m pytest tests/   # 389 pass today
# live smoke, no GPU:
#   config set backend mock · personas_enabled true · workday_enabled true
#   project new ops · run <anything> · days · landmark · personas
```

Full flags and the recommended 60K setup: `docs/USAGE.md`. The mind of
the operator: `docs/THE_NINE.md`. Trust the tests, keep the doctrine,
and hand your successor a better briefing than this one.
