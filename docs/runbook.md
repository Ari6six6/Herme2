# Build a twin — live runbook

The short version of taking a target from a URL to a sealed twin you can build
against.

## 0. Prereqs (once per session)

```
gpu attach            # pick/point at your Vast box
gpu serve             # bring up vLLM (first time downloads the model)
gpu status            # confirm: vllm endpoint UP
```

If `run` later says "vLLM endpoint not reachable", this is what's missing.

## 1. Create the build project

```
project build shop https://shop.example.com
```

Runs on the phone: clones the target's reachable surface (read-only) into an
**open** twin, fingerprints the stack, and equips the file-transfer tools. Expect
a few seconds of `GET … -> 200` lines and a `stack:` summary.

```
mission edit          # describe the actual task (the build's win = match the target, baked in)
```

## 2. Reconstruct + refine the twin

```
run build
```

One refinement pass: the agent reopens the twin, uses `twin_diff` to compare the
live target against the twin, reconstructs the real stack via `build_run` in the
twin's own build container (installs are allowed there — it has network; steps
it gets working are captured into a replayable recipe), records ground-truth
samples, and seals when it's satisfied. **Run it again for another pass** — each
one tightens the match. Watch for:

- `build_run` output — installs/clones (apt, pip, npm, git clone) run in the
  twin's build container (expected; it's the one container with network).
- `Keep this one on the phone …` — a raw `curl`/`wget` was bounced; that's by
  design, the agent should pull it on the phone and `transfer` it.
- `twin_diff: N match, M drifted, K missing` — the score; goal is all-match.

Once sealed, `sandbox_shell` is the only execution tool left and it's
`--network none` — nothing needing a download can happen after this point.
Anything the solution will need at runtime has to already be in the recipe.

Inspect anytime: `build show` (state, samples, stack), `build recipe` is shown to
the agent via `build_recipe`.

If the agent doesn't seal, the twin stays open — just `run build` again, or
`build seal` to freeze it manually.

## 3. Serve the twin + do the work

```
build serve           # run the recorded-sample twin on the box's localhost:8900
run build the /products page to meet the mission
```

In the build phase the agent has `twin_request` (ground truth), and an independent
**antithesis** re-runs the solution against the twin and rejects any "it works"
that wasn't actually proven (anti-collusion). A plain `run <anything>` operates
against the sealed twin; `run build` goes back to refining it.

## Good to know

- **Two runtimes, don't conflate them.** `twin_request` / `build serve` serve the
  *recorded ground-truth samples* — that's what parity is judged against. The
  reconstructed real stack (apache/php/… the builder stands up via the recipe) is
  how it *captures* accurate samples and gives the solution a real environment.
- **Cost is agent turns.** The from-scratch reconstruction is the expensive pass;
  the recipe makes later passes cheap. The box bills the whole time it's attached —
  `gpu down` when done.
- **Resources:** the twin/stack is RAM/CPU/disk, never VRAM (that's the model's).
  It stays alive between runs until you stop it or the box.
- **This is the first live outing.** Everything is unit-tested, but the prompts
  haven't met the 36B model on a real target yet — expect to tune
  `prompts/recon_build.md` and `prompts/build_mode.md` from what it actually does.
