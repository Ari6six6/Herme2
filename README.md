# Hermes

A package-per-prompt agent shell you own end to end. **Hermes is the harness, not
the model** — it runs on a small always-on VPS you SSH into from your phone
(Termux), rents a GPU on [Vast.ai](https://vast.ai) on demand, and serves whatever
open model you point it at behind a vLLM/llama.cpp endpoint. The default it's tuned
for now is an uncensored **Qwen3.6-27B** that fits a single 24 GB card (see
[Models](#models)); the original **Hermes-4.3-36B** is still one pick in the
catalog.

It rents nothing you don't see and hides nothing. From the home box the agent
reaches:

- **the VPS itself** — where Hermes runs, every project lives, and containers run
  beside it, reached at `localhost`;
- **the GPU box** — the model's home and the agent's disposable Linux sandbox,
  rented on demand over SSH; internet from there is discouraged by design so raw
  egress stays off it;
- **your servers** (optional) — real machines you register with `host add`: reads
  run free, anything mutating asks you first.

### ▶ Start here

New, or setting up a box from scratch? **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)**
is the A-to-Z: fresh VPS → first run, in order, from a phone over SSH. The rest of
this README is reference.

---

## How it thinks: a package per prompt

Every prompt you send starts a **fresh model instance**. There's no rolling chat.
Instead the agent receives a *package* assembled from project state on disk:

```
SYSTEM  ── static header + persona + tool catalog + skills index (a stable prefix)
USER    ── # MISSION           mission.md, yours to edit
           # DIRECTIVES        distilled standing instructions (recency wins)   ┐ opt-in
           # PROMPT HISTORY     your prompts, or just the last few when ↑ is on  ┘
           # RUN SUMMARIES      short summaries the agent wrote about its runs
           # YOUR LAST REPLY    verbatim, so "do that" resolves
           # NOTES              facts the agent chose to remember
           # WORKSPACE          listing of its file area
           # CURRENT REQUEST    what you just typed
```

Every section has a hard budget that scales with the served context window, so the
prompt can never creep. Within a run the agent loops over native tool calls (the
model's own tool-call format) until it answers and files a `finish_run` summary its
future self inherits.

Everything is plain text on the VPS — `nano mission.md` is the long-term memory.
`persona.md` in `~/.hermes/` is appended to the (deliberately lean) system prompt.

---

## What it does

Beyond the core loop, a set of capabilities you turn on per project (all in one
config file; **full reference and recommended 60K settings in
[docs/USAGE.md](docs/USAGE.md)**):

| Capability | What it buys you | Default |
|---|---|---|
| **Directive reconciliation** | Distils your whole prompt history into a `directives.md` that resolves "never X → now X" conflicts by recency, instead of both sitting in context with equal weight. | off |
| **Lazy compaction** | Folds the middle of a long run into a summary (keeping exact commands/errors) so a 40-turn task stays inside the window. | off |
| **Skills** | The agent's own how-to notes — one file each, a one-line index in the prompt, full body loaded on demand. Global (cross-project) or per-project; it grows them after runs that took figuring-out. | off |
| **Subagent delegation** | `delegate(brief, tools)` runs a clean child agent that returns one conclusion — its spam never enters your context. Turns the context ceiling from hard to soft. | off |
| **Prefix-cache ordering** | Moves volatile bytes out of the header so the leading package is byte-identical across calls (cheaper on a caching server). `debug prefix` measures it. | off |
| **Checkpointing** | Snapshots the project before file-mutating turns; `checkpoint restore <id>` rewinds a run gone sideways. | **on** |
| **Verification enforcement** | Bounces a finish that changed files but never *ran* anything. | off |
| **Taint tracking** | Content pulled from the network marks the next turn untrusted — its tool calls all require your y/n, so a hostile page can't steer a privileged action. The prompt-injection rail. | **always on** |
| **Self-build** | Lets the agent read and edit **Hermes' own source**, not just the project — gated tighter than everything else: every write asks y/n with a diff, and a fixed set of files (the gates themselves) refuse edits no matter what. | off |
| **Time-boxed runs** | A wall-clock hard stop (`max_run_seconds`), independent of the turn count — the safety net that still bounds a run when `max_turns` is raised or removed for autopilot use. `delegate_max_seconds` does the same for one delegated child. | off |
| **Retrospection** | Every N runs, a fresh-context pass reviews harness-recorded per-run metrics (turns, aborts, errors, bounces — numbers the model can't embellish) plus its own summaries, and banks recurring lessons as notes/skills. The recursive self-improvement loop, grounded and bounded. | off |

Every toggle is reversible and ships with silent migration — flipping one back
gives you exactly the prior behaviour.

---

## The doer doesn't grade its own homework

A sandbox is only worth having if the model is forced to *listen* to it. Left
alone, a small model will write code, write a test that can't fail, run it, and
declare victory — verification theater. So Hermes stacks guards:

- **Real output on your screen.** Every tool's actual result — exit codes included
  — is echoed to your phone, so a fabricated "it passed" can't hide next to what
  the command really printed.
- **Phantom-finish bounce.** A finish that pasted code but wrote/ran nothing gets
  sent back to do it for real (`phantom_nudges`).
- **Independent verification.** When a run that wrote code tries to finish and a
  GPU box is attached, a separate pass — fresh context, skeptical prompt, the
  *same real sandbox* — re-runs the actual code and returns `VERDICT: PASS/FAIL`.
  A FAIL is ground truth that bounces the run back with the real error; it fails
  closed and is bounded (`verify_rounds`). Turn off with `verify_code_runs false`.
- **Verify-before-done** (opt-in): even without a GPU, a run that changed files
  but never executed anything is nudged once to actually run it.

The independent verification pass is the same weights wearing a different hat —
fresh context, a skeptical prompt, the same real sandbox — and a PASS is only
granted when it's backed by real executed evidence, never a read-only glance at
the code. A checker, invoked on a finish; never a standing overseer taxing every
turn.

---

## Build mode & the sandbox twin

Point Hermes at a web service and it stands up a **runnable local clone** — the
twin — then builds a solution against the twin instead of the live system, with
correctness measured against what the real service actually does. The twin is the
*reconstructed real software* (for a known open-source stack), running in a
container on the VPS; recorded request/response pairs are the ground truth. Once
the twin is **sealed** the agent has no path to the live target at all.

```
sandbox provision                          # container runtime on the VPS (once)
project build shop https://shop.example.com   # record the target, seed an OPEN twin
mission edit                               # the task
run build                                  # reconstruct: stand the real stack up, seal
build serve                                # boot the twin in a local container
run build the /products page to meet the mission   # build against the sealed twin
```

In build mode two more roles bracket the doer, same weights wearing different
hats: a **planner** lays out the checklist first, and on a builder/antithesis
deadlock a **referee** makes the binding call — but only on real executed evidence.

Herme builds against what it records. **Deeper reconnaissance of a target** (its
listening services, wider footprint, exposed files) is a *separate* program,
[`herme-recon`](https://github.com/Ari6six6/Hermes/tree/herme-recon), which you
invoke on demand through the recon skill — it's deliberately not baked into the
core.

---

## Models

`gpu serve` opens a picker — the mind behind Hermes is your choice, and it persists
as your default once picked:

| # | model | runtime | fits | notes |
|---|---|---|---|---|
| ★ | **Qwen3.6-27B** (HauhauCS Balanced, uncensored · Q5_K_P GGUF) | llama.cpp | single **24 GB** card | **the recommended default** — cheapest to run; community uncensored build |
| | **Qwen3.6-27B** (Alibaba, official · FP8) | vLLM | 32 GB card | official weights, same proven vLLM path as Hermes |
| | **Qwen3.6-40B** (DavidAU Opus-Deckard Heretic, uncensored · Q5_K_M GGUF) | llama.cpp | 32 GB+ / 2 GPUs | largest/most capable; verbose |
| | **Hermes-4.3-36B** (NousResearch · FP8) | vLLM | ≥44 GB | the original battle-tested default |

To make one your default: run `gpu serve`, pick its number once — the choice is
saved to config and the agent is told which weights are behind it.

The catalog lives in `hermes/models.py` — each row carries everything that differs
between models (weights, runtime, tool-call parser, VRAM floor, context tiers, the
identity the system prompt announces), so adding a model is a row, not a refactor.
Each model serves on its **native** runtime: FP8 safetensors (Qwen-official,
Hermes) run on vLLM; GGUF builds run on `llama-server` (built with CUDA on the box,
OpenAI-compatible, tool calls via the model's own chat template) rather than vLLM's
slower experimental GGUF path.

**Per-model build profiles.** What makes tool-calling reliable differs per model,
so each row also carries a tuned profile: its sampling (the quantized/uncensored
builds get `min_p` + a presence penalty; thinking models keep Qwen's published
reasoning sampler), a completion budget sized to its reasoning, how hard to bounce
prose-only turns, which reasoning tags to strip, a short tool-call discipline note,
and whether its runtime honours a forced `tool_choice` (vLLM does; llama.cpp under
`--jinja` doesn't, so the loop adapts). Picking a model at `gpu serve` applies its
profile.

> The GGUF paths (the two uncensored Qwens) need the CUDA **toolkit** on the box to
> build llama.cpp — rent a **CUDA-devel** image, not runtime-only. And the
> uncensored finetunes are community builds: sanity-check their tool-calling before
> trusting them with host writes.

---

## Running it

Full setup is [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md). The short of it:

**Install (on the VPS).** `sudo ./setup.sh [wg0.conf]` does the whole box — system
hardening, Docker, an optional fail-closed WireGuard killswitch, and Hermes itself.
Manual path on a plain Ubuntu box:

```sh
apt install -y python3-pip git docker.io   # docker runs local containers
git clone <repo> Hermes && cd Hermes && pip install -e .
hermes
```

**A session.**

```text
hermes
> config set vast_api_key <key>
> project new blog
> mission edit                  # tell the agent what this project is
> gpu attach                    # auto-discovers your Vast box (or paste an ssh string)
> gpu serve                     # picker → provisions the model, tunnels 8000, waits
> run fix the parser in workspace/scraper.py and test it on the box
> gpu down                      # stop the model; optionally PAUSE the box (keeps the disk)
> gpu up                        # later: resume the paused box, no re-download
```

`config set backend mock` exercises the whole loop with no GPU.

### GPU tiers

`gpu serve` reads `nvidia-smi` and adapts — context length scales with total VRAM.
For the default **Qwen (Q5 GGUF, ~19 GB weights)** a single 24 GB card runs it and
context tiers up to 128k as VRAM allows. For **Hermes (FP8 36B)**:

| total VRAM | context | example boxes |
|---|---|---|
| < 44 GB | refused | weights alone need ~37 GB |
| 44–56 GB | 16k (tight) | 1× 48 GB, 2× 24 GB |
| 56–96 GB | 32–64k | A100 80 GB, RTX 6000 Pro |
| 96–168 GB | 128–192k | H200 140 GB |
| 168+ GB | 256k | 2× RTX 6000 Pro |

Override either with `config set max_model_len <n>`. Hopper/Ada/Blackwell run FP8
natively; Ampere (A100/A40/3090) falls back to weight-only FP8 (Marlin) — works,
slower. Pre-Ampere unsupported. The package budgets shrink automatically on small
tiers.

---

## What the agent can do

| tool | runs on | gate |
|---|---|---|
| read/write/edit/list files | VPS, project dir | free inside the project |
| `local_shell` | VPS | **always asks you y/n** |
| `remote_shell`, `remote_read/write` | GPU box | free — it's the sandbox; network commands blocked |
| `host_shell`, `host_read/write` | **your servers** | reads free; anything mutating asks y/n |
| `http_request`, `web_search` | VPS | GET free; POST etc. ask you |
| `write_note`, `finish_run` | VPS | free |
| `list_toolbox` / `equip_tool` | — | library tools load on demand |
| `forge_tool` | VPS | you review the source before it loads |
| `load_skill` / `write_skill` | VPS | free (skills on); scoped to the skills dirs |
| `delegate` | VPS | free (delegation on); the child's tools are gated normally |
| `list_hermes_source` / `read_hermes_source` | VPS | free — read-only, scoped to Hermes' own codebase (off unless `self_build_enabled`) |
| `write_hermes_source` / `edit_hermes_source` | VPS | **always asks you y/n** with a diff; off unless `self_build_enabled`; a fixed denylist of safety-critical files refuses edits outright regardless |

The toolbox ships ready-made tools (`download_file`, `transfer`, `replicate`,
`todo`, `json_query`, `extract_code`, `base64_codec`) whose schemas don't bloat the
prompt until equipped. Forged tools are plain Python files in `<project>/tools/`,
loaded only after you approve the exact source (re-approval on any change). Host
tools only appear once you've registered a server.

### Safety model

- **Internet happens on the VPS, not the GPU box.** The box gets files pushed to it
  (`transfer`, `replicate`), not a connection out. Enforced by trust + a deny-list
  speed bump (plus `unshare -n` where the box allows), not a cage — a root agent
  can route around any in-box block, so the prompt is honest about it.
- **Two gate polarities.** The GPU box is disposable → deny-list speed bump
  (everything runs, known network commands blocked). Your servers are real → fails
  closed (only commands positively classified read-only run free; everything else
  and every write asks y/n).
- **Taint rail (always on).** Network-sourced content forces owner approval on the
  next turn's actions — untrusted data can't steer privileged tools. See
  [docs/USAGE.md](docs/USAGE.md) for the threat model.

---

## Layout

```
~/.hermes/            config.json (0600) · persona.md · gpu.json · hosts.json
~/.hermes/skills/     global skills (cross-project)
~/hermes-projects/<name>/
  mission.md          your standing orders (edit anytime)
  directives.md       distilled standing instructions (when enabled)
  notes.md            the agent's memory notes
  history.jsonl       your prompts
  workspace/          the agent's file area
  skills/             per-project skills
  tools/              forged tools + approval manifest
  twin/               the runtime-twin blueprint (build projects)
  runs/NNNN/          transcript.jsonl · summary.md · final.md · metrics.json per run
  .checkpoints/       project snapshots (checkpointing on by default)
```

## Docs

- **[docs/GETTING_STARTED.md](docs/GETTING_STARTED.md)** — A-to-Z: fresh VPS to first run.
- **[docs/USAGE.md](docs/USAGE.md)** — every config flag, token costs, recommended 60K settings, new commands.
- **[docs/runbook.md](docs/runbook.md)** — the tight build-a-twin runbook.
- **[docs/ARCHITECTURE_NOTES.md](docs/ARCHITECTURE_NOTES.md)** — how the package is assembled + measured token budget.
- **[docs/DECISIONS.md](docs/DECISIONS.md)** — why the newer features are built the way they are.

## Development

```sh
pip install -e ".[dev]"
python -m pytest tests/
```

Tests cover package assembly and budgets, path-escape defenses, the tool registry
(forging/approval), the full agent loop against a scripted mock backend, the GPU
tier planner, the read-only command classifier and host-tool gates, and every
evolved feature (directives, compaction, skills, delegation, prefix ordering,
checkpointing, verification, taint, retrospection).
