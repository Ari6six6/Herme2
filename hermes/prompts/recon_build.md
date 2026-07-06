## Reconstruction — make the twin BECOME the webserver

Target: **{{source}}**. This run you turn the recorded responses into a *running
clone* of this webserver inside the sandbox box: the real software stack, stood up
and serving — **not** a recording of its pages. The twin is currently OPEN
({{exchange_count}} sample(s)); you finish by sealing it once your reconstruction
answers like the live target.

### What we've recorded so far
- **Stack:** {{stack}}

`twin_stack` / `twin_map` / `build_recipe` show the detail. This is what you
rebuild — the actual server, runtime and app, at the detected versions. If you
need a deeper read of the target (listening services, its wider footprint), that
lives in the separate `herme-recon` tool — see the recon skill — not in here.

### Reconstruct the real thing, not a mock

This is **reconstruction, not imitation.** A known open-source stack (WordPress,
Drupal, Joomla, Django, Rails, ...) is public and downloadable — stand up the
GENUINE software so the twin literally *is* the system, top to bottom:

1. **Install the real software inside the twin container** with `build_run` — it
   runs the step *in the container*, which has network, so `apt` / `pip` / `npm`
   / `git clone` at the detected version all work right there. No transfer
   needed.
2. **Stand the stack up with `build_run`** — web server, language runtime, the
   app, and the database/cache/services it depends on. Each successful step is
   captured into the **recipe** — the portable blueprint that lives on the
   VPS. `build serve` boots a fresh container and replays that recipe inside
   it to respin the whole runtime server without re-deriving anything, so make
   every step **idempotent and self-contained**, in order. Read the real build
   output and iterate — don't assume.
   - **Wire it like the target.** Bring up the supporting services (database,
     cache, queue) on the ports the app expects and connect them, so the clone
     behaves like the real system end to end — that accurate wiring is what makes
     it a safe place to test.
   - **The web server must bind `0.0.0.0:$TWIN_PORT`** (build serve exports
     `$TWIN_PORT` and publishes it from the container — binding `127.0.0.1` inside
     the container would be unreachable) and the final serving step must run in
     the background (`nohup ... &` / a daemon), so replaying the recipe leaves the
     server listening rather than blocking.
3. **Rebuild from real source when you have it.** If the operator has provided the
   target's actual source or config, or the stack is open-source and downloadable,
   rebuild from that REAL code and config rather than guessing the app's internals
   — it's the highest-fidelity path to an identical server.
4. **Recreate the routes you've recorded.** The endpoints in the recorded samples
   should exist on your reconstruction; wire the routes, content and config so they
   do.

### Differential — prove the reconstruction equals the target

- Capture ground truth from the live target with **`http_request`** and record the
  key responses with **`twin_record`**, so this pass and the antithesis can check
  the reconstruction against what the real target actually returned.
- **`twin_diff`** compares the live target against the twin's samples and shows
  where you match, where you've drifted, and where you're missing data. Drive it
  toward all-match — that gap is your work this pass.
- The twin is the real running software or it is nothing — there is no
  recorded-response stand-in. For a genuinely opaque/bespoke service you can't
  reconstruct, reproduce its observable behavior as a real running stub (a small
  app that serves the recorded responses) rather than pretending a recording is
  the twin.

### Prove it, then seal

Before sealing, show real evidence the reconstruction behaves like the target —
quote actual responses from your running stack beside the live ones, not a claim.
Make sure the **recipe** captures how it was built (`build_recipe`), so it's
reproducible on a fresh box. Then **`twin_seal`** to freeze this pass, and
`finish_run` with what the twin now is and which gaps you closed. Each time you're
run with "build" you get another pass to tighten the match — only seal a pass that
genuinely improved fidelity.
