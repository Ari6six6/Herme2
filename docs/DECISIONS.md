# Decisions

Running log of non-obvious calls made while evolving the harness: what, why,
and the alternative I passed on. Newest at the bottom of each feature.

## Global

- **No new dependencies.** Everything so far is Python stdlib plus the two deps
  the app already had (`httpx`, `prompt_toolkit`). Any new dependency gets a line
  here first.
- **Every feature behind a config flag in `config.py::DEFAULTS`.** Defaults off,
  except checkpointing and the directive header line (per the brief).

## Feature 1 — Directive reconciliation

- **`directives.md` is a distillation, the raw log is untouched.** History stays
  append-only on disk (nothing is deleted); only what the *package* sends changes.
  Alternative: rewrite/prune `history.jsonl`. Rejected — the raw log is the
  audit trail and the reconciliation input; destroying it would be irreversible
  and un-recoverable if a pass ever distils badly.
- **Two independent flags, not one.** `directive_header_rule` (on) adds the
  "recent instruction wins" line to the header; `directives_enabled` (off) turns
  on the machinery (reconciliation + swapping the full log for directives + last
  K). The brief lists the header line as on-by-default but the feature as
  off-by-default, so they can't be the same switch. The header line is true and
  useful even against the raw log, so it costs nothing to leave on.
- **Reconciliation is an LLM side-call with no tools.** It's one extra
  round-trip, only when `directives_enabled` is on, and only when due (migration
  or every N runs) — not every run. Justification for the token/latency cost:
  it *reduces* the steady-state package (a lean directives file replaces a
  ~1500-token history cap) and fixes the conflict bug that no amount of raw log
  can fix. Alternative: a local heuristic diff of prompts. Rejected — resolving
  "never X" vs "now X" needs language understanding, not string matching.
- **Trigger is stateless (`run_id % N == 0`) plus a migration check.** No extra
  state file to track "last reconciled run". Alternative: a `directives.state.json`
  marker. Rejected as more moving parts for no real gain; the modulo is
  predictable and the on-demand `directives reconcile` command covers urgency.
- **Migration runs on the first run of an old project** (`directives.md` missing
  but history present), so existing projects light up with zero manual steps.
- **The distilled directives reuse the history section's char budget** rather
  than adding a new `SECTION_SHARES` key. Keeps the budget math (and the
  off-by-default behaviour) byte-identical to before; the last-K raw prompts are
  tiny so their cap is more than enough.

## Feature 2 — Lazy compaction

- **A turn is `[assistant + its tool results]`, and only whole turns are folded.**
  Compaction removes complete turns and splices one summary message in their
  place, so every kept turn still has its assistant message paired with its
  tool-result messages — the request stays valid for the OpenAI wire format. A
  naive "drop the oldest N messages" would orphan `tool` messages from their
  `tool_calls` and 400 the endpoint.
- **The stable prefix is captured at loop start** (`len(messages)` after the
  package), not hard-coded to `messages[:2]`, so any pre-loop context stays out of
  the compactible region even if the assembled package grows more messages later.
- **Token estimate is chars/4, same as the package budget**, and includes the
  constant tool-schema bytes. It's an estimate, not a tokenizer — good enough to
  decide *when*, and it avoids adding a tokenizer dependency. Alternative: call
  the server's tokenizer endpoint. Rejected — a network round-trip per turn to
  decide whether to do another network round-trip is not worth it.
- **Trigger 50% / floor 25%** (owner's explicit call for a 60K window). An 80%
  trigger leaves too little headroom for the turn that tripped it; see USAGE.
- **Self-limiting, no thrash by construction.** After a compaction the region
  holds exactly `keep_last` turns, and the guard needs `> keep_last + 1` turns
  before it will act again — so a compaction can't immediately re-fire. No extra
  cooldown state needed.
- **A failed side-call is a no-op, not a failure.** If the summarizer can't be
  reached or returns empty, the conversation stays verbatim and the run keeps
  going. Losing compaction is a performance regression, never a correctness one.

## Feature 3 — Skills

- **Mirrors the toolbox catalog exactly.** Index of one-liners in the prompt,
  full body loaded on demand. This is the pattern the codebase already trusts
  for tokens; reusing it means the owner has one mental model, not two.
- **Skill name = filename stem; description = first non-empty line (`#` stripped).**
  Nano-friendly for both `# Heading`-style and plain first-line-description files.
  `load_skill` returns the whole file, so nothing is lost either way.
  Alternative: YAML front-matter. Rejected — adds a parse format and a dependency
  temptation for a file the owner edits by hand on a phone.
- **write_skill defaults to global scope.** The acceptance criterion (a skill
  from project A loadable in project B) only holds for globals, and "reusable"
  is the common case; `scope:"project"` is there for the local exception.
- **Project skill shadows a global of the same name.** The more specific
  procedure wins where it's defined, without touching the global.
- **The nudge can't corrupt the run.** It runs after the summary is fixed and
  intercepts `finish_run` instead of dispatching it, so `ctx.finish_summary`
  (the real handoff) is untouchable. It's the agent's private note-taking pass.
- **"Figuring-out" is a cheap heuristic** (error seen, or ≥8 turns, or forged a
  tool), not another model call. A false negative just means one skill unwritten;
  a false positive costs a couple of turns. Not worth an LLM classifier.

## Feature 4 — Subagent delegation

- **The child's tool set is drawn from the parent's built registry, by name.**
  That's the enforcement for "a child can never hold broader permissions than its
  parent": the parent registry already reflects the parent's permission context
  (live-touch, sealed-mode, registered hosts), and the child can only pick names
  out of it. Unknown names are dropped silently. Alternative: re-derive a registry
  for the child from config. Rejected — it could accidentally grant a tool the
  parent itself didn't have in this context.
- **Same `ctx.confirm`, same tool functions.** Permission tiers "apply
  identically" because the child literally calls the same tool bodies with the
  same confirm callback. No parallel permission path to keep in sync.
- **Depth is on `ToolContext`, checked in two places** (the delegate tool guard
  and the child-registry builder). Belt and suspenders: even if one path is
  bypassed, the other blocks a grandchild at the default cap of 1.
- **The child loop is a separate, smaller function, not `agent.run`.** `run` does
  package assembly, history append, run-dir writes, planner/verifier passes — all
  wrong for a stateless child. The child loop (`subagent.run_child`) reuses the
  shared helpers (`_assistant_msg`, `strip_think`, `dispatch`) but stays minimal.
  This is the "existing loop invoked recursively" in spirit without dragging the
  parent-only machinery along.
- **`backend`/`think_re` moved onto `ToolContext`** so a tool can run a model
  loop. Tools couldn't reach the LLM before; delegation is the first that needs
  to. Kept optional so nothing else is affected.
- **Cap-out returns a structured partial**, never an empty string — the parent
  gets "how far it got and why it stopped" so it can decide the next step instead
  of seeing a mysterious blank.

## Feature 5 — Prefix-cache-friendly ordering

- **Volatile status moves to the user message, not just later in the system
  prompt.** Putting it at the end of the system message would still break the
  cache for anything after it; putting it in the user message (after the
  slow-changing mission/directives) keeps the *entire* system prompt stable, which
  is the biggest single cacheable block.
- **`{{runtime_status}}` placeholder, filled per flag.** With the flag off the
  status renders inline exactly where it always was (existing behaviour, tests
  green); with it on the placeholder is empty and the status is emitted in the
  user message. One template, two orderings, no duplicated prompt text.
- **The date is the clearest offender**, but GPU status and host list can change
  mid-session too. All of them leave the header together.
- **`debug prefix` compares two packages with a deliberately changed status**, not
  two identical ones. Comparing identical packages would always report a 100%
  shared prefix and prove nothing; changing the volatile bits is what exposes
  whether they're actually isolated from the stable prefix.
- **Gated behind a flag (default off)** like the other opt-in features, even
  though it's a pure win with prefix caching on — the brief's default posture is
  off-until-flipped for everything but checkpointing and the header line.

## Feature 6 — Checkpointing

- **Copy, not git.** The brief allowed either; I chose copies. Projects are plain
  directories, not repos; a phone's git may be missing or in a weird state; and
  copy/restore has no failure modes to reason about. "Boring and reliable" was the
  explicit ask, and a safety net that can itself fail isn't one. Cost is a
  directory copy of small project state.
- **One snapshot per turn, before the first mutation.** Not per tool call (a turn
  with three writes shouldn't make three snapshots) and not after (that would
  capture the damage, not the escape hatch). Taken before the first file-mutating
  call so restore rewinds to just before the turn.
- **`runs/` and the store are excluded.** `runs/` is transcripts (not user
  content, and large); the store excludes itself to avoid recursion. This keeps
  snapshots cheap.
- **Restore is a true revert, not a merge.** Tracked entries are removed and
  copied back from the snapshot, so files created after the snapshot disappear.
  A merge would leave sideways artifacts behind, defeating the point.
- **On by default**, the only new feature that is (besides the header line), per
  the brief — it's pure safety.
- **Delegated child writes aren't separately checkpointed.** The child dispatches
  tools outside the parent's turn loop, so its writes don't trigger a snapshot;
  the parent's pre-turn snapshot before the `delegate` call still covers a revert.
  Noted as a known gap rather than threading checkpointing through the child loop.

## Feature 7 — Verification enforcement

- **Extends the existing verification, doesn't duplicate it.** The codebase
  already has `verify_code_runs` (the independent verifier pass), but that needs a
  GPU sandbox to re-run code. `verify_before_done` is the cheap, sandbox-free
  complement: it only checks that *an* execution tool ran this run, and nudges once
  if not. It slots into the finish chain before the sandbox pass, so with a GPU the
  agent self-verifies first, then the independent pass runs.
- **One-shot bounce, like the phantom gate.** A pure-edit or explain-only task
  legitimately has nothing to run; spending the single bounce and then accepting
  the finish avoids an infinite loop while still making the point once.
- **Trigger = file-mutating this run AND no execution tool used.** Reuses the same
  `FILE_MUTATING_TOOLS` set as checkpointing (writes to the project), and a new
  `EXECUTION_TOOLS` set (shells, http_request). A run that only read files or wrote
  a note isn't forced to execute anything.
- **Behind a flag (default off)** per the brief's default posture, even though
  it's low-cost — the owner opts in.

## Feature 8 — Taint tracking

- **No config flag.** The brief is explicit: this is the prompt-injection defense
  and it's not optional. It's a safety boundary, so it's always on. It's also
  self-quiet: it only prompts when a tainting tool actually ran, so an always-on
  rail costs nothing on runs that never touch the network.
- **Taint is tracked by producing-tool identity, at the harness level.** A tool in
  `TAINTING_TOOLS` returning non-error output marks the run's next turn tainted.
  Simpler and more robust than trying to tag substrings of content and chase them
  through the model's paraphrasing — the harness knows which results came from the
  network because it knows which tool produced them.
- **"Immediate inputs" = the previous turn's results, not the whole run.** Once
  tainted content is in context it technically lingers, but gating *every*
  subsequent turn forever would make the agent unusable after a single fetch. The
  brief's "immediate inputs" wording picks the practical, defensible line: the
  turn reacting to untrusted content is gated; taint clears when a turn pulls in
  no new untrusted input. The dangerous move — fetched content steering the very
  next action, including a follow-on fetch — is exactly what's caught.
- **One prompt per gated action, not two.** In a tainted turn the harness asks for
  approval, then dispatches with the tool's own `confirm` pre-satisfied, so a
  self-gating tool (local_shell, http POST) doesn't prompt twice for the same
  action the owner just approved. Deny → the tool never runs.
- **finish_run is exempt.** Ending a run isn't a privileged effect; gating it would
  add noise with no security value.
- **Extensible for the Docker/browser phase.** When sandboxed-runtime output tools
  arrive, adding their names to `TAINTING_TOOLS` extends the rail with no other
  change — the reason the set is a single named constant.
- **Per-domain read caching, added later.** Without it, any run that fetches more
  than once (search, then read a result; read a paginated API) re-prompts on
  every tainted turn even for the same trusted site, which trains the owner to
  reflexively hit "y" — the opposite of a safety rail. `ToolContext.approved_domains`
  remembers a domain once the owner approves a GET/HEAD `http_request` to it, and
  the taint gate skips the prompt for further reads of that domain for the rest of
  the run. Scoped narrowly on purpose: state-changing requests (POST etc.) always
  confirm regardless of domain, a new domain always confirms, and the cache is
  per-run (not persisted), so a stale approval from an earlier run can't be
  leveraged by a later prompt-injected page.

## Feature 9 — Self-build

- **A separate module and a separate base directory from the project tools.**
  `read_file`/`write_file`/etc. are scoped to `Project.root` on purpose —
  that's the directory the agent is *meant* to fill freely. Self-build is
  scoped to `hermes.paths.repo_root()`, a different tree entirely, so the two
  can't be confused by a path-escape bug in either: `resolve_in` still enforces
  "stay under base," it's just a different base.
- **A hardcoded denylist, not a config flag.** Everything else in this doc is a
  flag precisely so it's reversible and inspectable in `config.json`. Self-build
  protection is the one exception: if `PROTECTED` were a config key, an agent
  that can write files and (elsewhere) talk an unattended operator into a
  `config set` would have a path to loosen its own gate. Putting the list in
  source, and refusing to let self-build touch that source file, closes the
  loop — the only way to change it is a human editing Hermes by hand, which is
  already true of every other safety-relevant constant in the codebase.
- **What's on the denylist and why: the gates, not the surface area.**
  `confirm.py` (the y/n chokepoint itself), `config.py` (loads `auto_confirm`/
  `self_build_enabled`), `paths.py` (the escape check self-build itself relies
  on), `agent.py` (the frozensets that drive checkpointing, verification, and
  taint), `checkpoint.py`, `tools/base.py` and `tools/__init__.py` (the
  registry), `tools/local_shell.py`, and `self_build.py` itself. Everything
  else — tool modules, prompts, docs, tests — is editable once the operator
  opts in. The line is drawn at "can this file change whether the agent gets
  asked," not at "is this file important."
- **Reuses the `forge_tool` confirm pattern (diff instead of full source).**
  `forge_tool` already established the shape: show the operator what's about to
  load, let them view the full body, and don't proceed without an explicit yes.
  Self-build reuses `ctx.confirm(..., viewable=...)` the same way, but shows a
  unified diff rather than the whole file — self-edits are usually small
  changes to existing files, and a diff is what an operator actually needs to
  judge one.
- **A plain backup copy, not git, and not the project checkpoint store.**
  Same reasoning as feature 6: boring and reliable beats clever. The project
  checkpoint mechanism (`hermes/checkpoint.py`) is deliberately out of scope
  here too — it snapshots `Project.root`, and self-build never touches that
  directory. A timestamped copy in `repo_root()/.self_build_backups/` needs no
  git binary, no repo state assumptions (an operator might have installed
  Hermes without cloning it), and has exactly one failure mode: the disk is
  full, which every other file write in this codebase already lives with.
- **No auto-apply, no auto-restart.** A self-edit changes files on disk; the
  already-imported modules in the running process are untouched until the
  operator restarts Hermes. Making the harness restart itself mid-run to pick
  up a change to its own control loop is exactly the kind of "let it grade its
  own homework" move the README spends a whole section arguing against — the
  restart stays a deliberate, visible, operator-driven step.
- **Excluded from the recommended 60K settings.** Every other feature in that
  list is safe to leave on. This one changes the program the operator is
  trusting to gate everything else, so it's presented as a session you turn on
  and back off, not a standing default — the doc says so explicitly rather than
  leaving it to be inferred.

## Feature 10 — Time-boxed runs

- **A second axis, not a replacement for `max_turns`.** Turns and seconds
  measure different failure modes: a run that loops on a cheap, fast model can
  burn through 40 turns in under a minute (turns are the real limit); a run on
  a slow backend or a single turn stuck on a huge tool result can spend minutes
  on ONE turn (time is the real limit). Picking one axis to represent both
  would under-protect one of the two cases, so both caps exist and a run stops
  at whichever fires first.
- **`0` means off, same idiom as `max_model_len`.** Every other numeric safety
  knob in this codebase is a flag with a meaningful default; this one defaults
  to off because "how long is too long" depends entirely on what you're paying
  for compute and what the task is — there's no honest universal default the
  way `checkpointing: true` has one.
- **Checked at the top of the turn loop, not wrapped around `backend.chat`.**
  Killing a run mid-request would either need to cancel an in-flight HTTP call
  (backend-specific, fragile) or let it finish anyway (the timeout does
  nothing). Checking once per turn, before starting the next one, means the
  cap's granularity is "one more turn's worth of overshoot," which is fine —
  the goal is bounding a runaway *loop*, not preempting a single slow call.
- **One wrap-up nudge at 85%, not a countdown.** Mirrors the existing
  `turns == max_turns - 2` warning exactly, including reusing the same
  "wrap up, leave precise open items" wording (in a new `time_wrapup.md`
  template rather than the turn-worded `wrapup.md` — "2 turns remain" would be
  a lie in a time-based stop). A single nudge, deduped with a boolean flag, so
  a long-running compaction pass or a slow individual turn can't retrigger it.
- **A time-capped run still gets a real summary, not a stub.** The hard stop
  sets `aborted = True` and breaks the loop exactly like exhausting
  `max_turns` does; the existing post-loop logic (force a real handoff summary
  from the model, falling back to `_stub_summary` only if that also fails)
  already handles "aborted" uniformly, so no special-casing was needed here —
  the time cap is just another way to reach the same aborted state the turn
  cap already produces.
- **`delegate_max_seconds` lives beside `delegate_max_turns`, checked before
  each child turn.** Same reasoning as the parent loop, and the same
  `_cap_out` structured-partial return path other delegate stop conditions
  already use — a reaped child is indistinguishable, from the parent's point
  of view, from one that ran out of turns: both hand back "how far it got,"
  never a hang or an empty string.

## Feature 11 — Retrospection (recursive self-improvement)

- **Grounded in harness-recorded metrics, not self-report.** Every run writes
  `runs/NNNN/metrics.json` — turns, aborts, tool errors, stall/phantom/verify
  bounces, tainted turns — counted by the harness while it ran the loop. The
  reflection pass reasons over those numbers plus the summaries; it can't
  embellish what it didn't author. Same philosophy as verification: the doer
  doesn't grade its own homework, so the grader gets ground truth. Alternative:
  let the pass reread transcripts and judge for itself. Rejected — N transcripts
  don't fit a side-call budget, and a model grading its own prose is exactly the
  verification-theater failure the harness exists to prevent.
- **Metrics recording is unconditional** (like transcripts) — it's
  observability, not behaviour; a few hundred bytes per run, useful to the
  operator (`retrospect` lists them) even with the pass off. Only the *pass*
  is behind flags, per the default-off posture.
- **The write surface is the agent's own assets only.** `write_note` always;
  `load_skill`/`write_skill` only when `skills_enabled` — a skill written into
  a system that never indexes it would be a false improvement, so the pass's
  toolset shrinks to what actually recirculates into future packages. No
  shells, no network, no mission/persona/directives: self-improvement never
  touches operator files or the world. The recursion is real (notes and skills
  feed every future package) but the blast radius is two plain-text dirs.
- **The pass's confirm always denies.** Everything registered for it is free,
  but if a gated tool ever slips into its registry, an unattended reflection
  pass must fail closed, never self-approve.
- **Stateless trigger (`run_id % N`), like reconciliation** — no marker file to
  desync; `retrospect now` covers urgency. Needs ≥2 measured runs before it
  will run: one run has no pattern in it.
- **Fresh context, not the run's tail.** The skills nudge (feature 3) already
  reflects on one run in-context; the cross-run layer must see runs side by
  side, cheaply — compact metrics lines + summaries, one message.
- **A failed pass is a no-op** (transport error, nothing banked, budget
  exhausted) — losing a reflection is never a correctness problem for the run
  that hosted it, so it can never bounce or block a finish.

## Capabilities (breadth session)

Toolbox tools paired with a seed skill, one capability per commit. These are
library additions, not numbered features: they follow the toolbox precedent
(schema out of the prompt until equipped) rather than the config-flag pattern.

### `git_ops` — local git in the workspace

- **No config flag; the equip gate is the off-switch.** Every existing toolbox
  tool (`transfer`, `replicate`, `json_query`, …) ships flagless and inert until
  `equip_tool` loads it per project. A new library tool is an extension of that
  library, not a behaviour change to turn on, so adding one to `config.py`'s
  DEFAULTS would be a flag nothing else in the toolbox has. The house rule's
  "every feature behind a flag" targets loop/package behaviour; an opt-in-by-equip
  tool is already off by default by construction.
- **Local only — no clone/fetch/pull/push/remote.** The network git verbs are
  absent from the allowlist and return "unknown operation". Keeping the tool
  purely local means it never crosses the network, so it needs no place on the
  taint rail. A cloning tool is a *separate* tool that would ingest network
  content and therefore join `TAINTING_TOOLS` in `agent.py` — deferred as its own
  proposal precisely because it touches `agent.py`.
- **subcommand allowlist + argv list (shell=False).** git is invoked as a Python
  list, so there is no shell to inject into, and only the enumerated subcommands
  run. Rejected: a raw pass-through arg string — it would let `-c core.sshCommand=…`
  or `--upload-pack=…` turn an inspect tool into arbitrary execution.
- **Reads free, mutations confirmed.** status/log/diff/branch only inspect the
  workspace (like `read_file`/`list_files`), so they run without a prompt;
  init/add/commit go through `ctx.confirm` showing the exact git command. This
  matches the brief's "mutating subcommands gate through confirm" and the
  codebase's tier philosophy at once.
- **Inline commit identity (`-c user.name/email`).** A fresh box has no git
  identity, so a bare `git commit` fails. Passing the identity inline lets the
  agent commit out of the box without mutating global git config (which would be
  a side effect on the operator's box that outlives the run). Rejected: telling
  the agent to `git config --global` — that reaches outside the workspace.
- **Repo dir resolved inside the project (`resolve_in`).** The optional `repo`
  arg and `add`/`diff` `path` are path-checked, so an operation can't reach a git
  dir or stage a file outside the project. Same path-escape defense as the file
  tools.
