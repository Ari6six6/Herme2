# Getting Started — A to Z

Zero to your first real run. Written for the way you actually run this: a phone
with Termux, SSH'd into a small always-on VPS.

If you only read one thing: the ordered command list in **§12 (The whole thing on
one screen)** is the whole path. Everything above it explains each step.

---

## 0. The mental model (read this once)

Four machines, each with one job:

```
   YOUR PHONE ──ssh──> THE VPS ──ssh tunnel──> THE GPU BOX (rented, on-demand)
   (Termux,            (always-on, cheap;      (Vast.ai; holds the model,
    the terminal)       Hermes + all projects   the agent's compute sandbox)
                        live here)

   ...plus YOUR SERVERS (optional): real machines you register; reads free,
   writes ask you first.
```

- The **phone** is just the keyboard. Nothing runs there.
- The **VPS** is home. Hermes runs here; every project, note, skill, and
  checkpoint lives here. It's cheap and always on.
- The **GPU box** is rented from Vast.ai *only while you're working*, holds the
  LLM, and doubles as the agent's disposable Linux sandbox. You bring it up
  (`gpu serve`) and take it down (`gpu down`) each session.

**How memory works:** every prompt starts a *fresh* model instance. There's no
rolling chat. What the agent knows is assembled each time from files on the VPS:
your `mission.md`, your prompt history (distilled into `directives.md`), the
agent's own run summaries and notes, and its skills. You edit those files with
`nano`; that *is* the long-term memory. (Full detail: `README.md` → "The stateful
machine".)

---

## 1. Before you begin

You need:

1. **A VPS** — a plain Ubuntu 22.04/24.04 box, ~4–8 GB RAM. Any cheap provider.
   This is the one thing that costs money continuously (a few dollars a month).
2. **Termux on your phone** (or any SSH client) — to reach the VPS.
3. **A Vast.ai account + API key** — for renting GPUs on demand. Get the key
   from the Vast.ai console. *(Optional until you want a real model; you can do
   everything else with `backend mock` first — see §7.)*
4. **Optional: a WireGuard `wg0.conf`** — if you want all the box's outbound
   traffic to ride a VPN with a fail-closed killswitch. Recommended but not
   required; without it the setup script locks the box to SSH-only egress until
   you add one.

---

## 2. Stand up the VPS (one-shot)

SSH into the fresh VPS from Termux as your normal user (not root):

```sh
ssh you@your-vps-ip
```

Clone the repo and run the bootstrap. It's one script; it hardens the box,
installs Docker + Python + Hermes, and (if you give it a `wg0.conf`) sets up the
WireGuard killswitch:

```sh
git clone <this-repo-url> Hermes && cd Hermes
# with a VPN config (recommended):
sudo ./setup.sh /path/to/wg0.conf
# or without one (box will be SSH-only-egress until you add a VPN later):
sudo ./setup.sh
```

What it does, briefly: system update, installs (python, docker, wireguard,
fail2ban), base hardening, the fail-closed killswitch (all egress via the VPN;
SSH stays alive on the real NIC via a routing trick), then installs Hermes into a
venv and puts `hermes` on your `PATH`. It arms a 5-minute auto-revert while
touching the firewall, so a mistake can't lock you out — if you get dropped,
wait 5 minutes and reconnect. (Full explanation lives in the header of
`setup.sh`.)

> **Important:** with the killswitch on, Hermes's own outbound (the Vast SSH
> tunnel/API) only works while the VPN is **up**. That's the killswitch doing its
> job, not a bug.

Then **log out and back in** — that activates your Docker group membership and
the updated `PATH`:

```sh
exit
ssh you@your-vps-ip
```

Verify the killswitch later if you set up the VPN:

```sh
wg-quick down wg0 && curl --max-time 5 https://1.1.1.1   # must FAIL (no leak)
wg-quick up   wg0 && curl -s https://api.ipify.org        # shows the VPN IP
```

*(No VPS provisioning script for you? The manual path is in `README.md` →
"Install": `apt install -y python3-pip git docker.io && pip install -e .`)*

---

## 3. First launch

```sh
hermes
```

First run creates your home at `~/.hermes/` (`config.json` at `0600`, a default
`persona.md`). You'll get a REPL prompt like `hermes(-)>`. The `-` means no
project is selected yet.

Set your Vast key and edit who the agent is:

```
config set vast_api_key <your-key>
persona edit          # opens nano; keep it short — a few lines of who Hermes is
```

`type help` any time to see every command.

---

## 4. The evolved features (all on by default)

Out of the box every evolved capability is on — the full-power setup **is** the
default. Nothing to flip. For reference, this is what's running:

```
directives_enabled true      # standing instructions that resolve conflicts by recency
compaction_enabled true      # keep long runs inside the context window
skills_enabled true          # reusable how-to notes the agent grows
skills_nudge true
delegate_enabled true        # offload big sub-tasks to a clean child agent
prefix_cache_order true      # cheaper calls when the server caches prefixes
verify_before_done true      # never report done without running it
```

Also on: `checkpointing` (auto-snapshots before file changes) and the directive
header rule. Always on, no switch: taint tracking (the prompt-injection rail).
**What each flag does and its token cost are in `docs/USAGE.md`** — that's the
reference. Every one is reversible: `config set <flag> false` and behaviour is
exactly what it was, and anything you've set in `~/.hermes/config.json` always
wins over the defaults.

---

## 5. Your first project

A project is an isolated space with its own mission, memory, workspace, and
tools.

```
project new blog
mission edit          # tell the agent what this project is about; save + exit
```

The mission is the standing description the agent reads at the start of every
run. Keep it current — it's the highest-signal thing in its context. You can
`nano` it any time, or from the REPL with `mission edit`.

---

## 6. Rent and serve a GPU

1. In the **Vast.ai console**, rent a box that fits the model. For the
   recommended default — the uncensored **Qwen3.6-27B (Q5 GGUF)** — a single
   **24 GB** card runs it. **Rent a CUDA-devel image** (it has to build
   llama.cpp; runtime-only images can't). If you'd rather run Hermes-4.3-36B
   (FP8), you want **≥44 GB VRAM** and any image works — see `README.md` → "GPU
   tiers" for the full table.
2. Back in the Hermes REPL:

```
gpu attach            # auto-discovers your running Vast box via the API
                      # (or paste it: gpu attach ssh -p PORT root@HOST)
gpu serve             # opens a model picker, provisions the runtime, tunnels
                      # port 8000, waits until the model answers
gpu status            # confirm: "vllm endpoint: UP"
```

In the picker, choose the **Qwen3.6-27B (HauhauCS, Q5 GGUF)** — that's the
recommended default; your choice persists, so you only pick it once.

`gpu serve` reads `nvidia-smi`, picks a context tier for the VRAM it finds,
launches llama.cpp for GGUF (or vLLM for FP8), and tunnels it to the VPS's
localhost. The first serve downloads the weights (~19 GB for the Qwen default,
~37 GB for Hermes) and — for the GGUF models — builds llama.cpp with CUDA on the
box; that's a one-time cost per box, and a paused-and-resumed box keeps it.

> If `run` later says **"vLLM endpoint not reachable"**, this step is what's
> missing or the tunnel died — `gpu status`, then `gpu tunnel` or re-`gpu serve`.

---

## 7. Your first run

```
run summarize what this project is for and list what you'd need from me to start
```

You'll watch the agent work turn by turn: each tool call is printed (`→ tool(...)`)
and its **real output is echoed to your screen** — exit codes and all — so a
fabricated "it worked" can't hide next to what actually printed. When it's done
it prints a plain-prose answer and files a summary for its future self.

Things you'll see the new features do:

- Before it writes a file, a **checkpoint** is taken silently (revert with
  `checkpoint restore <id>`).
- If it changed files but never *ran* anything, it gets bounced once to verify
  (feature 7).
- If it fetches a web page, the **next** action needs your y/n — that's the
  taint rail stopping a hostile page from steering it (feature 8).

**No GPU yet?** You can exercise the entire loop with a fake model:

```
config set backend mock
run hello
config set backend openai   # switch back when you have a GPU
```

---

## 8. Add your real servers (optional)

Register a machine and the agent gains `host_shell` / `host_read` / `host_write`
for it — reads run free, anything that could change the server stops for your y/n:

```
host add web ssh://root@203.0.113.7 my blog server
run why is nginx returning 502s on web? check the logs and config
```

For risky surgery the agent can `replicate` files from a host into the GPU
sandbox, reproduce and fix there, and only then ask you to apply the verified
change back. Nothing mutates a real server without your approval.

---

## 9. Daily driving

A normal working session:

```
ssh you@your-vps-ip
hermes
project use blog            # pick up where you left off
gpu attach && gpu serve     # bring the model up (or `gpu up` to resume a paused box)
run <whatever you need>
...
gpu down                    # stop vLLM; optionally PAUSE the Vast box (keeps the disk)
```

Handy commands as you go:

- `directives` — see the distilled standing instructions; `directives edit` to
  hand-tune them, `directives reconcile` to rebuild from history now.
- `skills` — list the agent's how-to notes; `skills edit <name>` to nano one.
- `checkpoint` — list snapshots; `checkpoint restore <id>` to rewind a run that
  went sideways.
- `history` / `summaries` / `notes` — see what the agent remembers.
- `debug prefix` — confirm prefix-cache friendliness after `prefix_cache_order`.

---

## 10. Cost control & teardown

- The **VPS** bills continuously (cheap). Leave it on — it's home.
- The **GPU box** bills the whole time it's attached. `gpu down` at the end of a
  session and choose **pause** when prompted: it stops billing for compute but
  keeps the disk (weights, built llama.cpp), so `gpu up` later resumes fast with
  no re-download. To stop paying for the disk too, destroy the instance in the
  Vast console.

---

## 11. When something's wrong

| Symptom | Fix |
|---|---|
| `vLLM endpoint not reachable` | `gpu status`; if the tunnel is dead, `gpu tunnel`; if the model isn't up, re-`gpu serve`. |
| `no GPU box attached` | `gpu attach` (then `gpu serve`). |
| Hermes's outbound hangs | If you set up the VPN, the killswitch blocks egress when the tunnel is down — `wg-quick up wg0`. |
| A run went wrong | `checkpoint` to list, `checkpoint restore <id>` to rewind the project files. |
| Got SSH-dropped during `setup.sh` | Wait 5 minutes — the dead-man's-switch auto-reverts the firewall — then reconnect. |
| Model is narrating instead of acting | Expected occasionally on small models; it gets bounced. Persistent? Try a different model at `gpu serve`. |

---

## 12. The whole thing on one screen

```sh
# --- VPS, once ---
ssh you@your-vps-ip
git clone <repo> Hermes && cd Hermes
sudo ./setup.sh /path/to/wg0.conf     # or: sudo ./setup.sh   (no VPN)
exit && ssh you@your-vps-ip           # log back in for docker group + PATH

# --- Hermes, once ---
hermes
config set vast_api_key <key>
persona edit
# (the evolved features are all on by default — see docs/USAGE.md to tune)

# --- each session ---
project new blog          # or: project use blog
mission edit
gpu attach && gpu serve   # or: gpu up   (resume a paused box)
run <your task>
gpu down                  # pause the box when prompted
```

## Where to go next

- `docs/USAGE.md` — every config flag, what it costs in tokens, recommended 60K
  settings, and the new commands.
- `docs/ARCHITECTURE_NOTES.md` — how the context package is assembled and the
  measured token budget, if you want to understand the internals.
- `docs/DECISIONS.md` — why the newer features are built the way they are.
- `README.md` — the model catalog, GPU tiers, and the tool/permission table.
