# Hermes — Architecture Notes (Phase 0 review)

Written before any feature work, from reading the code (not the brief). Where
the code disagrees with how the system was described to me, **the code wins** and
I've flagged it. Token numbers are measured, not guessed — see the last section
for how.

## What Hermes is

A package-per-prompt agent shell. It runs from a phone (Termux) over SSH; the LLM
is served remotely on a rented GPU box behind an OpenAI-compatible endpoint
(vLLM, or llama.cpp for GGUF models). One operator prompt = one *fresh* model
instance. There is no conversation memory between prompts — everything the agent
knows on the next prompt is whatever got written to disk and re-assembled into
the package.

Entry point: `hermes/cli.py::main` → a REPL. `run <text>` → `cmd_run` →
`agent.run(...)`.

## The pieces and how they connect

```
cli.py (REPL)
  └─ cmd_run → agent.run(project, prompt, cfg, backend, gpu, env, sandbox)
        ├─ package.assemble(project, prompt, env, cfg) → [system, user]   # the "context package"
        ├─ build_registry(project, cfg, confirm) → ToolRegistry            # tools + schemas
        └─ turn loop: backend.chat(messages, tools=schemas) → tool calls → dispatch → repeat
              ├─ finish_run sets ctx.finish_summary → loop ends
              ├─ nudges: stall / phantom / verify-before-done
              └─ on a code finish (GPU attached): an independent verifier pass re-runs the code
        └─ writes runs/NNNN/{summary.md, final.md, transcript.jsonl}
```

### Projects — the unit of memory (`hermes/project.py`)

A project is a directory under `~/hermes-projects/<name>/`:

```
mission.md          owner-edited; the standing description of the project
notes.md            agent-appended facts/decisions (write_note)
history.jsonl       every operator prompt, verbatim, append-only  ← the bug lives here
runs/NNNN/
    summary.md      the agent's own handoff summary for that run
    final.md        the agent's final prose reply, verbatim
    transcript.jsonl full turn-by-turn log (not re-injected)
tools/              forged tools + .equipped.json + .approved.json
workspace/          the agent's file area (real work lands here)
```

Key methods: `append_history`, `recent_prompts(n)`, `recent_summaries(k)`,
`last_final_reply`, `read_mission`, `read_notes`/`append_note`,
`workspace_listing`. Runs are numbered by `next_run_id()` (max existing +1).

### Persona (`hermes/config.py`)

Global, not per-project: `~/.hermes/persona.md`, read by `read_persona()` and
**capped at 2000 chars** (~500 tokens). Default persona is 3 lines (~37 tokens).
Appended to the system prompt under `## Persona`.

### The context package (`hermes/package.py::assemble`)

Returns exactly **two messages**: `[system, user]`. This is the single most
important function to understand.

**system message** (`build_system_prompt`): renders `prompts/system.md` with
`{{placeholders}}`, then appends, in order: model-specific tool guidance (empty
for Hermes), and the persona.

**user message**: seven fixed sections joined by blank lines, in this order:

1. `# MISSION` — `mission.md`, head-truncated
2. `# PROMPT HISTORY` — `recent_prompts(history_max_prompts=30)`, tail-truncated  ← **the append-only log**
3. `# RUN SUMMARIES` — `recent_summaries(summaries_max=8)`, tail-truncated
4. `# YOUR LAST REPLY` — `last_final_reply()`, verbatim
5. `# NOTES` — `notes.md`, tail-truncated
6. `# WORKSPACE` — `workspace_listing()`, head-truncated
7. `# CURRENT REQUEST` — the new prompt

Every section has a hard char budget (`SECTION_SHARES` fractions of a total
budget). The total budget: `min(package_budget_tokens=10000, 30% of context
window)` tokens × 4 chars/token, floored at 1500 tokens. On a 60K window that's
`min(10000, 18000) = 10000` tokens = 40000 chars.

Truncation is head-keep or tail-keep per section — nothing is dropped from disk,
only what's *sent* is trimmed.

### Tools (`hermes/tools/`)

`build_registry` assembles a `ToolRegistry` per run. Tools are `Tool`
dataclasses (name, description, hand-written JSON-schema, `fn(args, ctx) -> str`).
`dispatch()` never raises — every failure comes back as a string starting with
`ERROR:` or `DENIED`.

Three origins:
- **builtin** — always loaded: `read_file`, `write_file`, `edit_file`,
  `list_files`, `local_shell`, `remote_shell/read/write`, `write_note`,
  `finish_run`, `list_toolbox`, `equip_tool`, `forge_tool`, and (when live reach
  is allowed) `http_request`, `web_search`.
- **toolbox** — shipped library in `hermes/toolbox/*.py`. **Schemas are NOT in
  the package.** Only a one-line catalog (name + description) appears in the
  system prompt; `equip_tool` loads the full schema on demand and persists the
  choice per project. This is the pattern the **skills system should mirror.**
- **forged** — the agent writes new tools into `tools/*.py`; they persist and
  load in future runs after operator approval (per content hash).

The full builtin **tool schemas are sent on every `backend.chat` call** as the
OpenAI `tools=` array — separate from the package budget. Measured ~1600 tokens
for the 15 default builtins.

### Permission tiers (`hermes/confirm.py` + per-tool)

There is **no declarative tier field** on tools — the tier is baked into each
tool's body as an explicit `ctx.confirm(...)` call or the absence of one:

- **Auto-run (no prompt):** in-project `read_file`/`write_file`/`edit_file`/
  `list_files`; `http_request` GET/HEAD; `web_search`; `remote_*` (the GPU box is
  the agent's sandbox); `write_note`; the meta tools.
- **Owner-confirmed:** `local_shell` (always); `http_request` non-GET/HEAD;
  `forge_tool`; host writes; reading outside the project dir.
- **Denied outright:** writes outside the project dir.

`confirm()` is the single chokepoint — prints the action, waits y/n/v (v = view
source for forged tools). This matters for two upcoming features: **delegation**
(the child must go through the same `confirm`) and **taint tracking** (tainted
turns must force the confirm path even for normally-auto tools).

### The turn loop (`hermes/agent.py::run`)

- `max_turns` default **40** (not ~60 — see discrepancies).
- Each turn: `backend.chat` → strip `<think>` → log → if no tool calls, *nudge*
  (stall) up to `stall_nudges` times before accepting prose as final → else
  dispatch every tool call, append results.
- Guards before accepting a `finish_run`:
  - **phantom** — code fence in the answer but no productive tool ran → bounce.
  - **verify-before-done** (opt-in) — files changed but nothing was executed this
    run → one nudge to actually run it.
  - **verification** — if a code-writing tool ran and a GPU sandbox is attached,
    an independent `verifier` pass re-runs the code and returns
    `VERDICT: PASS/FAIL`, bounded by `verify_rounds`.
- Circuit breaker: 3 consecutive tool errors aborts.
- Always ends by writing `summary.md` (model-written, forced, or stubbed) and
  `final.md`.

The whole message list (`messages`) grows verbatim across the loop and is never
compacted mid-run — this is exactly what **lazy compaction** (feature 2) targets.

## Measured token costs (60K served window)

Measured by assembling real packages; ~4 chars/token (the app's own
`APPROX_CHARS_PER_TOKEN`). Approximate but consistent with what the app budgets.

| Piece | Tokens | Notes |
|---|---:|---|
| **Static system prompt total** | **~2150** | header + env line + toolbox catalog + persona |
| — system.md body (header) | ~1885 | the "~2K header" from the brief — accurate |
| — toolbox catalog (one-liners) | ~222 | 7 toolbox tools, name + description each |
| — persona (default) | ~37 | capped at 500 tokens (2000 chars) |
| **Builtin tool schemas** (sent as `tools=`) | **~1600** | 15 tools; separate from package budget |
| Prompt history | ~22 / prompt | append-only; capped 30 prompts / 15% budget (~1500t) |
| Run summaries | ~108 / run | capped 8 runs / 30% budget (~3000t) |
| Mission (typical) | ~40–200 | 20% budget cap (~2000t) |
| **Full package, mature project** | **~3800** user + ~2150 system ≈ **~5400** | + ~1600 schemas ≈ **~7000 sent/call** |

**Static block the brief asks me to keep lean** (header + persona + tools +
skills index + directives): currently header ~1885 + toolbox catalog ~222 +
persona ~37 + tool schemas ~1600 = **~3750 tokens**. That leaves comfortable room
(~2–4K) for a directives block and a skills index before the 6–8K ceiling the
brief sets. Budget targets for the new features derive from this.

### Budget implications for the features

- Directives (feature 1): replacing the full history log (capped ~1500t) with
  `directives.md` + last K≈5 raw prompts (~110t + distilled directives). A lean
  directives file (target <400t) is a net *reduction* in the package.
- Skills index (feature 3): mirror the toolbox catalog — ~20–30 tokens/skill.
  10 skills ≈ 250–300t, under the 500t acceptance bar.
- Lazy compaction (feature 2): the live loop, not the static block. With
  ~7000t of fixed overhead per call and a 60K window, a trigger at ~45–50%
  (~27–30K) leaves ~30K headroom for the running turn; a floor at ~20–25%
  (~12–15K) buys a long runway.

## Discrepancies between the brief and the code (code is truth)

1. **"summarized into a `summary.md`"** — there is no single `summary.md`. Each
   *run* writes its own `runs/NNNN/summary.md`; the package injects the last
   `summaries_max=8`. The durable record is per-run, not one growing file.
2. **"capped around 60 turns per prompt"** — the default is `max_turns=40`
   (config). Configurable.
3. **"tool list with descriptions" in the package** — only *builtins* ship full
   schemas (via the `tools=` array, not the package). The *toolbox* ships as
   one-line summaries in the system prompt and loads schemas on `equip`. So a
   two-tier tool disclosure already exists — the skills system should copy it.
4. **Header "~2K tokens"** — accurate (~1885t body, ~2150t with catalog+persona).
5. **Permission tiers** are implicit (per-tool `ctx.confirm` calls), not a
   declarative registry. Taint tracking and delegation must work *with* that.
6. **Prefix caching is currently broken by design.** `system.md` line 29 embeds
   `Date: {{date}}` and `GPU: {{gpu_status}}` (and line 30 `Managed hosts`) high
   in the otherwise-stable header. Those bytes change between calls, so a
   vLLM-style prefix cache can't reuse anything past them. Feature 5 addresses
   this directly.
7. More loop machinery exists than the brief describes: an independent verifier
   pass, verification rounds, and phantom/stall nudges. Verification enforcement
   (feature 7) partially exists already (`verify_code_runs`) — feature 7 should
   extend, not duplicate it.

## Conventions to respect

- Pure Python + stdlib bias; only `httpx` and `prompt_toolkit` as deps.
- Config: one file (`~/.hermes/config.json`), dotted-key `cfg.get/set`, defaults
  in `config.py::DEFAULTS`. Every new flag goes there.
- Tests: pytest, fixtures in `tests/conftest.py` (`project`, `cfg`, `yes`/`no`/
  `never` confirm fns, `MockBackend` scripted LLM). New features get tests in the
  same style. Baseline: **348 tests pass** before any change.
- `package.assemble` is a pure function of inputs — keep it that way.
- Migrations must be automatic and silent (`ensure_layout` is the hook point).
- Tool results never raise; they return `ERROR:`/`DENIED` strings.
