"""The Hermes REPL — short commands for a phone keyboard.

  run <text>        talk to the agent (alias: r)
  project ...       new/use/list (alias: p)
  gpu ...           attach/serve/status/tunnel/up/down (alias: g)
  mission/notes/history/summaries/tools/config/persona/help/quit
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from hermes import __version__, agent
from hermes import hosts as hosts_mod
from hermes.config import Config, hermes_home, persona_path
from hermes.gpu import (
    endpoint_from_state,
    load_gpu_state,
    probe_net_isolation,
    save_gpu_state,
)
from hermes.llm import make_backend
from hermes.project import Project, ProjectError
from hermes.sandbox import capabilities as sandbox_capabilities, local_endpoint
from hermes.ssh import SSHEndpoint, SSHError, kill_pid, parse_ssh_string, pid_alive
from hermes.ui import bold, cyan, dim, green, magenta, red, yellow

BANNER = f"{bold(magenta('hermes'))} {dim('v' + __version__)} — type {cyan('help')}"


# ---------------------------------------------------------------- helpers
def _projects_dir(cfg) -> Path:
    return Path(cfg.get("projects_dir")).expanduser()


def _current_project(cfg) -> Project | None:
    name = cfg.get("current_project")
    if not name:
        return None
    try:
        return Project.load(_projects_dir(cfg), name)
    except ProjectError:
        return None


def _probe_vllm(cfg) -> bool:
    try:
        url = f"http://127.0.0.1:{cfg.get('local_port', 8000)}/v1/models"
        return httpx.get(url, timeout=4).status_code == 200
    except httpx.HTTPError:
        return False


def _ensure_tunnel(cfg, state) -> None:
    """Best effort: restart the tunnel if the pid died."""
    ep = endpoint_from_state(state)
    if ep is None:
        return
    if pid_alive(state.get("tunnel_pid", 0)) and _probe_vllm(cfg):
        return
    if state.get("tunnel_pid"):
        kill_pid(state["tunnel_pid"])
    pid = ep.start_tunnel(cfg.get("local_port", 8000), cfg.get("gpu_port", 8000))
    state["tunnel_pid"] = pid
    save_gpu_state(state)


def _gpu_status_line(cfg, state) -> str:
    if not state.get("host"):
        return "not attached"
    up = "vllm:up" if _probe_vllm(cfg) else "vllm:DOWN"
    ctx = state.get("served_ctx")
    return f"{state['host']}:{state['port']} ({up}{f', ctx {ctx}' if ctx else ''})"


def _sandbox_status_line() -> str:
    caps = sandbox_capabilities(local_endpoint())
    if not caps["runtime"]:
        return "local — no container runtime yet (`sandbox provision` installs one)"
    bits = [caps["runtime"]] + (["kvm"] if caps["kvm"] else [])
    return "local (" + ", ".join(bits) + ")"


def _edit_file(path: Path) -> None:
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(path)])


def _pick_model(cfg):
    """Let the operator choose which model to serve, defaulting to the one the
    config already points at. Persists the choice so `run` serves the same
    identity. Returns the chosen ModelSpec, or None if cancelled."""
    from hermes.models import model_list, resolve

    specs = model_list()
    current = resolve(cfg)
    default_idx = next((i for i, s in enumerate(specs) if s.key == current.key), 0)
    print(dim("which model?"))
    for i, s in enumerate(specs):
        tag = green("ready") if s.ready else yellow("experimental")
        here = cyan(" ← current") if s.key == current.key else ""
        print(f"  {cyan(f'[{i + 1}]')} {s.label} [{tag}]{here}")
    try:
        raw = input(f"model [{default_idx + 1}]? ").strip()
    except EOFError:
        raw = ""
    if not raw:
        spec = specs[default_idx]
    else:
        try:
            spec = specs[int(raw) - 1]
            if int(raw) < 1:
                raise IndexError
        except (ValueError, IndexError):
            print(yellow("not a listed choice — cancelled"))
            return None
    # The served name is what the OpenAI client (llm.py) sends; keep it in sync.
    cfg.set("model_id", spec.key)
    cfg.set("model", spec.served_name)
    cfg.set("quantization", spec.quantization)
    # Apply this model's tuned build — sampling, completion budget, stall
    # tolerance — so the agent loop and client serve its optimized profile, not
    # the previous model's. (The Hermes profile equals the app defaults.)
    for key, value in spec.runtime_config().items():
        cfg.set(key, value)
    cfg.save()
    return spec


# ---------------------------------------------------------------- commands
def cmd_run(cfg, args: str) -> None:
    if not args.strip():
        print(dim("usage: run <prompt>"))
        return
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project") + dim(" — `project new <name>` or `project use <name>`"))
        return
    state = load_gpu_state()
    gpu = endpoint_from_state(state)
    sandbox = local_endpoint()  # this box (the VPS Hermes runs on)
    if cfg.get("backend") != "mock":
        if state.get("host"):
            _ensure_tunnel(cfg, state)
        if not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(" — `gpu attach` + `gpu serve` first "
                  "(or `config set backend mock` for a dry run)."))
            return
    from hermes.models import resolve
    spec = resolve(cfg)
    env = {
        "gpu_status": _gpu_status_line(cfg, state),
        "sandbox_status": _sandbox_status_line(),
        "remote_workspace": state.get("remote_workspace", "~/hermes-workspace"),
        "context_window": state.get("served_ctx", 0),
        "model_identity": spec.identity,
        "model_tool_guidance": spec.tool_guidance,
    }
    prompt = args.strip()
    backend = make_backend(cfg)
    # Workday (feature 11): the same `run` becomes a full day of the cast —
    # briefing, assigned work, debrief. An explicit `hey <name>` is a direct
    # order to one persona and skips the day.
    if cfg.get("personas_enabled", False) and cfg.get("workday_enabled", False):
        from hermes import personas as personas_mod
        from hermes import workday
        catalog = personas_mod.load_cast(project, cfg)
        p, rest, attempted = personas_mod.parse_invocation(prompt, catalog)
        if p is not None:
            print(dim(f"(speaking as {p.name} — pulled aside, no workday)"))
            agent.run(project, rest, cfg, backend, gpu=gpu, env=env,
                      sandbox=sandbox, persona=p)
            return
        if attempted:
            print(dim(f"(no persona named '{attempted}' — the day takes the "
                      "whole prompt; `personas` lists the cast)"))
        workday.run_day(project, prompt, cfg, backend, gpu=gpu, env=env,
                        sandbox=sandbox)
        return
    persona, prompt = _pick_persona(cfg, project, prompt, backend, spec)
    agent.run(project, prompt, cfg, backend, gpu=gpu, env=env, sandbox=sandbox,
              persona=persona)


def _pick_persona(cfg, project, prompt, backend, spec):
    """Who speaks this run (feature 9). Precedence: explicit `hey <name>` /
    `@<name>` > dynamic routing > the persona_default. A typo'd name never
    eats the prompt, and a failed router never blocks the run — both fall
    back to the default agent."""
    if not cfg.get("personas_enabled", False):
        return None, prompt
    from hermes import personas as personas_mod
    catalog = personas_mod.load_cast(project, cfg)
    persona, prompt, attempted = personas_mod.parse_invocation(prompt, catalog)
    if persona is not None:
        print(dim(f"(speaking as {persona.name})"))
        return persona, prompt
    if attempted:
        print(dim(f"(no persona named '{attempted}' — running as default; "
                  "`personas` lists the cast)"))
        return None, prompt
    if cfg.get("personas_route", False):
        think_re = agent._think_re(spec.think_tags)
        persona = personas_mod.route(prompt, catalog, backend, cfg, think_re)
        if persona is not None:
            print(dim(f"(routed to {persona.name} — say `hey <name>` to pick "
                      "yourself)"))
            return persona, prompt
    default = (cfg.get("persona_default") or "").strip()
    if default:
        persona = personas_mod.resolve(catalog, default)
        if persona is not None:
            return persona, prompt
        print(yellow(f"persona_default '{default}' not found — running as default"))
    return None, prompt


def cmd_project(cfg, args: str) -> None:
    parts = args.split()
    sub = parts[0] if parts else "list"
    pdir = _projects_dir(cfg)
    if sub == "new" and len(parts) > 1:
        try:
            Project.create(pdir, parts[1])
        except ProjectError as e:
            print(red(e))
            return
        cfg.set("current_project", parts[1])
        cfg.save()
        print(green(f"project '{parts[1]}' created and selected.") + dim(" Edit its mission: `mission edit`"))
    elif sub == "use" and len(parts) > 1:
        try:
            Project.load(pdir, parts[1])
        except ProjectError as e:
            print(red(e))
            return
        cfg.set("current_project", parts[1])
        cfg.save()
        print(green(f"switched to '{parts[1]}'"))
    else:
        current = cfg.get("current_project")
        names = Project.list_names(pdir)
        if not names:
            print(dim("(no projects yet — `project new <name>`)"))
        for n in names:
            print(green("* ") + bold(n) if n == current else "  " + n)


def cmd_gpu(cfg, args: str) -> None:
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "status"
    state = load_gpu_state()

    if sub == "attach":
        if len(parts) > 1:
            try:
                user, host, port = parse_ssh_string(parts[1])
            except SSHError as e:
                print(red(e))
                return
            instance_id = None
        else:
            from hermes.gpu.vast import VastError, running_instances
            try:
                instances = running_instances(cfg.get("vast_api_key", ""))
            except VastError as e:
                print(red(e) + dim("\n(fallback: paste it — `gpu attach ssh -p PORT root@HOST`)"))
                return
            if not instances:
                print(yellow("no running Vast.ai instances found."))
                return
            if len(instances) > 1:
                for i, inst in enumerate(instances):
                    print(f"  {cyan(f'[{i}]')} id={inst['id']} {inst['num_gpus']}x{inst['gpu_name']} ${inst['dph']:.2f}/hr")
                try:
                    pick = int(input("which? "))
                    inst = instances[pick]
                except (ValueError, IndexError, EOFError):
                    print(yellow("cancelled"))
                    return
            else:
                inst = instances[0]
            user, host, port = "root", inst["ssh_host"], int(inst["ssh_port"])
            instance_id = inst["id"]
        ep = SSHEndpoint(host=host, port=port, user=user)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        if not ep.check():
            print(red("ssh check failed — is your key registered with Vast.ai?"))
            return
        ep.run(f"mkdir -p {ep.remote_workspace}")
        isolated = probe_net_isolation(ep)
        print("network isolation: " + (
            green("kernel-level (unshare)") if isolated
            else yellow("regex deny-list only (unshare unavailable in this container)")
        ))
        if state.get("tunnel_pid"):  # don't orphan a tunnel to the old box
            kill_pid(state["tunnel_pid"])
        state = {
            "instance_id": instance_id,
            "host": host, "port": port, "user": user,
            "remote_workspace": ep.remote_workspace,
            "net_isolation": isolated,
            "tunnel_pid": 0, "served_ctx": 0,
        }
        save_gpu_state(state)
        print(green("attached.") + dim(" Next: `gpu serve`"))

    elif sub == "serve":
        from hermes.gpu import provision
        ep = endpoint_from_state(state)
        if ep is None:
            print(yellow("not attached — `gpu attach` first"))
            return
        if "net_isolation" not in state:  # attached with an older version
            state["net_isolation"] = probe_net_isolation(ep)
            save_gpu_state(state)
            ep = endpoint_from_state(state)
        spec = _pick_model(cfg)
        if spec is None:
            print(yellow("cancelled"))
            return
        try:
            gpus = provision.detect_gpus(ep)
            plan = provision.plan_serve(gpus, cfg, spec)
        except provision.ProvisionError as e:
            print(red(f"cannot serve: {e}"))
            return
        print(f"model: {cyan(spec.label)}")
        print(f"GPUs: {cyan(', '.join(plan.gpu_names))} — {plan.total_vram_gb}GB total")
        if spec.server == "vllm":
            detail = f"vLLM · tp={plan.tensor_parallel}, util={plan.gpu_memory_utilization}"
        else:
            detail = f"llama.cpp · {plan.tensor_parallel} GPU(s)"
        print(f"plan: {detail}, context={plan.max_model_len}")
        for note in plan.notes:
            print(yellow(f"note: {note}"))
        try:
            provision.launch(ep, cfg, plan, spec)
        except provision.ProvisionError as e:
            print(red(f"launch failed: {e}"))
            return
        _ensure_tunnel(cfg, state)
        print(dim(f"waiting for the model to come up ({spec.weights_note})..."))
        if provision.wait_ready(ep, cfg):
            state["served_ctx"] = plan.max_model_len
            save_gpu_state(state)
            print(green(f"ready — {spec.label} is listening (context {plan.max_model_len}).")
                  + dim(" Try: run hello"))
        else:
            print(red("timed out.") + dim(" Inspect with: gpu status / `remote tail -n 50 ~/vllm.log`"))

    elif sub == "status":
        if not state.get("host"):
            print(yellow("not attached"))
            return
        box = f"{state['user']}@{state['host']}:{state['port']}"
        print(f"box: {cyan(box)}"
              + (dim(f" (vast id {state['instance_id']})") if state.get("instance_id") else ""))
        print(f"tunnel: pid {state.get('tunnel_pid')} "
              + (green("alive") if pid_alive(state.get("tunnel_pid", 0)) else red("dead")))
        print("vllm endpoint: " + (green("UP") if _probe_vllm(cfg) else red("down")))
        ep = endpoint_from_state(state)
        rc, out, _ = ep.run(
            "nvidia-smi --query-gpu=name,memory.used,memory.total --format=csv,noheader",
            timeout=20,
        )
        if rc == 0:
            print(out.strip())

    elif sub == "tunnel":
        _ensure_tunnel(cfg, state)
        print("tunnel " + (green("up") if _probe_vllm(cfg)
                           else yellow("started (endpoint not answering yet)")))

    elif sub in ("up", "resume"):
        iid = state.get("instance_id")
        if not iid or not cfg.get("vast_api_key"):
            print(yellow("no paused Vast instance to resume")
                  + dim(" — `gpu attach` to a running box instead"))
            return
        from hermes.gpu.vast import VastError, get_instance, start_instance
        try:
            start_instance(cfg.get("vast_api_key"), iid)
        except VastError as e:
            print(red(e))
            return
        print(dim(f"resuming Vast instance {iid} — waiting for it to boot..."))
        inst = None
        for _ in range(40):  # ~2 minutes
            try:
                inst = get_instance(cfg.get("vast_api_key"), iid)
            except VastError:
                inst = None
            if inst and inst.get("status") == "running" and inst.get("ssh_host"):
                break
            time.sleep(3)
        else:
            print(red("instance didn't come back up in time")
                  + dim(" — try `gpu up` again, or check the Vast console"))
            return
        # SSH host/port can change across a stop/start — always re-read them.
        user, host, port = "root", inst["ssh_host"], int(inst["ssh_port"])
        ep = SSHEndpoint(host=host, port=port, user=user)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        if not ep.check():
            print(red("ssh check failed after resume")
                  + dim(" — the box may still be booting; try `gpu up` again shortly"))
            return
        ep.run(f"mkdir -p {ep.remote_workspace}")
        isolated = probe_net_isolation(ep)
        if state.get("tunnel_pid"):  # the old tunnel points at the pre-pause host
            kill_pid(state["tunnel_pid"])
        state.update({
            "host": host, "port": port, "user": user,
            "remote_workspace": ep.remote_workspace,
            "net_isolation": isolated, "tunnel_pid": 0, "served_ctx": 0,
        })
        save_gpu_state(state)
        print(green("resumed.") + dim(" The disk persisted, so `gpu serve` skips the "
              "weight download / llama.cpp rebuild. Next: `gpu serve`"))

    elif sub == "down":
        ep = endpoint_from_state(state)
        if ep:
            ep.run("kill $(cat ~/vllm.pid) 2>/dev/null; rm -f ~/vllm.pid")
            print(green("vLLM stopped."))
        if state.get("tunnel_pid"):
            kill_pid(state["tunnel_pid"])
            state["tunnel_pid"] = 0
        if ep:
            ep.close_master()  # don't leave the multiplexed ssh around
        if state.get("instance_id") and cfg.get("vast_api_key"):
            answer = input(
                f"pause Vast instance {state['instance_id']}? stops billing but keeps "
                "the disk, so `gpu up` resumes fast (weights + build intact) [y/N] "
            )
            if answer.strip().lower() == "y":
                from hermes.gpu.vast import VastError, stop_instance
                try:
                    stop_instance(cfg.get("vast_api_key"), state["instance_id"])
                    print(green("instance paused.")
                          + dim(" Resume later with `gpu up`. (To stop paying for the "
                                "disk too, destroy it in the Vast console.)"))
                except VastError as e:
                    print(red(e))
        state["served_ctx"] = 0
        save_gpu_state(state)
    else:
        print(dim("usage: gpu attach [sshstr] | serve | status | tunnel | up | down"))


def cmd_sandbox(cfg, args: str) -> None:
    """The local sandbox: this box (the VPS Hermes runs on). Nothing to register —
    `status` shows what it can isolate with, `provision` installs the container
    runtime."""
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "status"
    ep = local_endpoint()

    if sub == "status":
        caps = sandbox_capabilities(ep)
        print("container runtime: " + (
            cyan(caps["runtime"]) if caps["runtime"]
            else yellow("none yet — `sandbox provision`")
        ))
        print("kvm (microVM-capable): " + (
            green("yes") if caps["kvm"]
            else dim("no — running plain containers (expected on a cheap VPS)")
        ))

    elif sub == "provision":
        from hermes.sandbox.provision import SandboxError, ensure_runtime
        try:
            rt = ensure_runtime(ep, on_event=lambda t: print(dim("  " + t)))
            print(green(f"{rt} ready."))
        except SandboxError as e:
            print(red(e))

    else:
        print(dim("usage: sandbox status | provision"))


def cmd_host(cfg, args: str) -> None:
    parts = args.split()
    sub = parts[0] if parts else "list"
    hosts = hosts_mod.load_hosts()

    if sub == "add" and len(parts) >= 3:
        name = parts[1]
        if not hosts_mod.HOST_NAME_RE.match(name):
            print(red("host name must match [A-Za-z0-9_-]{1,32}"))
            return
        # ssh:// form leaves room for a trailing note; a pasted `ssh -p ...`
        # command consumes the whole rest of the line.
        if parts[2].startswith("ssh://"):
            sshstr, note = parts[2], " ".join(parts[3:])
        else:
            sshstr, note = " ".join(parts[2:]), ""
        try:
            user, host, port = parse_ssh_string(sshstr)
        except SSHError as e:
            print(red(e))
            return
        ep = SSHEndpoint(host=host, port=port, user=user)
        print(dim(f"checking ssh {user}@{host}:{port} ..."))
        if not ep.check():
            print(yellow("warning: ssh check failed — saving anyway (server may be down)"))
        hosts[name] = {"host": host, "port": port, "user": user, "note": note}
        hosts_mod.save_hosts(hosts)
        print(green(f"host '{name}' registered.") + dim(" The agent reaches it with "
              "host_shell/host_read/host_write (reads free, writes ask you)."))

    elif sub == "rm" and len(parts) == 2:
        if hosts.pop(parts[1], None) is None:
            print(red(f"no such host: {parts[1]}"))
            return
        hosts_mod.save_hosts(hosts)
        print(green(f"host '{parts[1]}' removed."))

    elif sub == "list" or not parts:
        if not hosts:
            print(dim("(no managed hosts — `host add <name> ssh://user@host[:port]`)"))
        for name, rec in sorted(hosts.items()):
            note = dim(f"  {rec['note']}") if rec.get("note") else ""
            print(f"  {cyan(name)}  {rec.get('user', 'root')}@{rec['host']}:{rec.get('port', 22)}{note}")
    else:
        print(dim("usage: host add <name> <ssh-string> [note] | list | rm <name>"))


def cmd_directives(cfg, args: str) -> None:
    """Standing instructions distilled from the prompt history (feature 1).
      directives            show directives.md
      directives edit       nano it yourself
      directives reconcile  force a reconciliation pass now
    """
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    sub = args.strip()
    if sub == "edit":
        _edit_file(project.directives_path)
        return
    if sub == "reconcile":
        if not cfg.get("directives_enabled", False):
            print(yellow("directives are off") + dim(" — `config set directives_enabled true` first"))
            return
        if cfg.get("backend") != "mock" and not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(" — `gpu serve` first"))
            return
        from hermes import directives as directives_mod
        from hermes.models import resolve
        spec = resolve(cfg)
        think_re = agent._think_re(spec.think_tags)
        print(dim("reconciling standing instructions from the full history..."))
        text = directives_mod.reconcile(project, make_backend(cfg), cfg, think_re)
        if text is None:
            print(yellow("nothing to reconcile (no history, or the pass failed)."))
        else:
            print(green("directives.md rewritten:\n") + text)
        return
    print(project.read_directives() or dim("(no directives yet — `directives reconcile`"
                                            " or enable `directives_enabled`)"))


def _common_prefix_len(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def cmd_debug(cfg, args: str) -> None:
    """Diagnostics. `debug prefix` assembles two consecutive packages (with a
    changed runtime status between them) and reports the shared byte prefix — so
    prefix-cache efficiency is measurable, not assumed (feature 5)."""
    from hermes import package
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    sub = (args.split() or ["prefix"])[0]
    if sub != "prefix":
        print(dim("usage: debug prefix"))
        return
    # Two calls that differ only in volatile parts: a changed request and a
    # changed GPU status / host set (the bytes prefix ordering is meant to move).
    env_a = {"gpu_status": "1.2.3.4:8000 (vllm:up)", "managed_hosts": "none",
             "context_window": cfg.get("package_budget_tokens", 10000)}
    env_b = {"gpu_status": "9.9.9.9:8000 (vllm:DOWN)", "managed_hosts": "web=root@9.9.9.9:22",
             "context_window": cfg.get("package_budget_tokens", 10000)}
    msgs_a = package.assemble(project, "probe request one", env_a, cfg)
    msgs_b = package.assemble(project, "a different probe request two", env_b, cfg)
    system_a, system_b = msgs_a[0]["content"], msgs_b[0]["content"]
    text_a = system_a + "\n\x1e\n" + msgs_a[1]["content"]
    text_b = system_b + "\n\x1e\n" + msgs_b[1]["content"]
    shared = _common_prefix_len(text_a, text_b)
    sys_shared = _common_prefix_len(system_a, system_b)
    approx = shared // package.APPROX_CHARS_PER_TOKEN
    print(f"prefix-cache ordering: {cyan('ON' if cfg.get('prefix_cache_order') else 'OFF')}")
    print(f"system prompt: {len(system_a)} chars; identical across the two calls: "
          + (green('yes') if sys_shared == len(system_a) == len(system_b) else
             red(f'no (diverges at char {sys_shared})')))
    print(f"shared package prefix: {cyan(str(shared))} chars (~{approx} tokens)")
    if sys_shared == len(system_a) == len(system_b):
        print(green("  → the full stable header (header + persona + tools + skills "
                    "index) is a byte-identical prefix — cache-friendly."))
    else:
        print(yellow("  → volatile bytes sit inside the header; turn on "
                     "`prefix_cache_order` to move them out."))


def cmd_skills(cfg, args: str) -> None:
    """The agent's reusable how-to notes (feature 3).
      skills               list the index (global + this project)
      skills show <name>   print a skill's full body
      skills edit <name>   nano a skill (creates a global one if new)
    """
    from hermes import skills as skills_mod
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""
    if sub == "show" and name:
        sk = skills_mod.get(project, name)
        print(sk.body.rstrip() if sk else red(f"no such skill: {name}"))
    elif sub == "edit" and name:
        sk = skills_mod.get(project, name)
        if sk is not None:
            _edit_file(sk.path)
        else:
            if not skills_mod.SKILL_NAME_RE.match(name):
                print(red("skill name must match [A-Za-z0-9_-]{1,40}"))
                return
            skills_mod.global_skills_dir().mkdir(parents=True, exist_ok=True)
            path = skills_mod.global_skills_dir() / f"{name}.md"
            if not path.exists():
                path.write_text(f"one-line description of {name}\n\n(procedure)\n")
            _edit_file(path)
    else:
        idx = skills_mod.index(project)
        print(idx or dim("(no skills yet — the agent writes them with write_skill, "
                         "or `skills edit <name>`)"))


def cmd_personas(cfg, args: str) -> None:
    """The cast of named archetypes (feature 9).
      personas               list the roster (builtin + global + this project)
      personas show <name>   print a persona's full file
      personas edit <name>   $EDITOR it (a new name scaffolds a global file)
      personas use <name>    adopt one as the default voice · `use off` clears
    """
    from hermes import personas as personas_mod
    project = _current_project(cfg)
    max_chars = cfg.get("persona_max_chars", 2000)
    catalog = personas_mod.load_all(project, max_chars)
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""
    if sub == "show" and name:
        p = personas_mod.resolve(catalog, name)
        if p is None:
            print(red(f"no such persona: {name}"))
        else:
            print(p.path.read_text().rstrip() if p.path else p.voice)
    elif sub == "edit" and name:
        p = personas_mod.resolve(catalog, name)
        if p is not None and p.scope != "builtin":
            _edit_file(p.path)
            return
        if not personas_mod.PERSONA_NAME_RE.match(name):
            print(red("persona name must match [A-Za-z0-9_-]{1,40}"))
            return
        # A new name — or shadowing a shipped builtin — edits a global file.
        personas_mod.global_dir().mkdir(parents=True, exist_ok=True)
        path = personas_mod.global_dir() / f"{name}.md"
        if not path.exists():
            seed = p.path.read_text() if p is not None and p.path else (
                f"one-line capacity of {name}\n"
                "tools: read_file, list_files, write_note\n\n"
                f"You are {name}. (the voice)\n"
            )
            path.write_text(seed)
        _edit_file(path)
    elif sub == "use" and name:
        if name.lower() in ("off", "none", ""):
            cfg.set("persona_default", "")
            cfg.save()
            print(green("default persona cleared") + dim(" — back to ~/.hermes/persona.md"))
            return
        p = personas_mod.resolve(catalog, name)
        if p is None:
            print(red(f"no such persona: {name}"))
            return
        cfg.set("persona_default", p.name)
        cfg.save()
        print(green(f"default persona: {p.name}"))
    else:
        if not cfg.get("personas_enabled", False):
            print(yellow("personas are off") + dim(" — `config set personas_enabled true`"))
        default = (cfg.get("persona_default") or "").strip()
        for n in sorted(catalog):
            p = catalog[n]
            tag = "" if p.scope == "builtin" else f" [{p.scope}]"
            mark = green("* ") if n == default else "  "
            print(f"{mark}{cyan(n)}{dim(tag)} — {p.description[:100]}")
        if not catalog:
            print(dim("(no personas — the shipped cast seems missing)"))


def cmd_days(cfg, args: str) -> None:
    """The workday record (feature 11).
      days            list the day log (newest last)
      days show <id>  print a day's debrief (NNNN prefix is enough)
      days log <id>   print the full day log — briefing, assignments, reports
    """
    from hermes import workday
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    entries = workday.list_days(project)
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    name = parts[1].strip() if len(parts) > 1 else ""
    if sub in ("show", "log") and name:
        match = next((p for p in entries if p.name.startswith(name)), None)
        if match is None:
            print(red(f"no such day: {name}") + dim(" — `days` lists them"))
            return
        if sub == "log":
            log_path = match.with_name(match.stem + ".log.md")
            print(log_path.read_text().rstrip() if log_path.exists()
                  else dim("(no log for that day)"))
        else:
            print(match.read_text().rstrip())
    else:
        if not entries:
            print(dim("(no days yet — with workday_enabled on, every `run` is one)"))
        for p in entries:
            first = p.read_text().splitlines()[0].lstrip("# ")
            print(f"  {cyan(p.stem)}  {dim(first[:100])}")


def cmd_landmark(cfg, args: str) -> None:
    """Landmarks (feature 13): marks on the road between lifecycles.
      landmark                    list what stands on the road
      landmark <name> <text...>   leave one — the next day's briefing must address it
      landmark rm <name>          clear one yourself
    """
    from hermes import landmarks as landmarks_mod
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    parts = args.split(maxsplit=1)
    if not parts:
        marks = landmarks_mod.load(project)
        if not marks:
            print(dim("(the road is clear — `landmark <name> <text>` leaves one)"))
        for m in marks:
            print(f"  {cyan(m.name)}  {dim(m.summary[:100])}")
        if not cfg.get("landmarks_enabled", False):
            print(dim("(landmarks are off at runtime — "
                      "`config set landmarks_enabled true` makes the briefing read them)"))
        return
    if parts[0] == "rm" and len(parts) > 1:
        name = parts[1].strip()
        if landmarks_mod.remove(project, name):
            print(green(f"landmark '{name}' cleared."))
        else:
            print(red(f"no such landmark: {name}"))
        return
    if len(parts) < 2 or not parts[1].strip():
        print(dim("usage: landmark <name> <text>  ·  landmark rm <name>  ·  landmark"))
        return
    try:
        path = landmarks_mod.leave(project, parts[0], parts[1].strip())
    except ValueError as e:
        print(red(str(e)))
        return
    print(green(f"landmark '{parts[0]}' stands") + dim(f" at {path} — the next "
                "day's briefing must address it"))


def cmd_council(cfg, args: str) -> None:
    """Council mode (feature 10): the cast deliberates, the scribe writes.
      council <topic>              the loaded cast (up to 4) takes the topic
      council <topic> owl,smith    explicit members — comma-separated last word
    """
    if not cfg.get("council_enabled", False):
        print(yellow("council is off") + dim(" — `config set council_enabled true`"))
        return
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project") + dim(" — `project new <name>` or `project use <name>`"))
        return
    args = args.strip()
    if not args:
        print(dim("usage: council <topic> [names,comma,separated]"))
        return
    from hermes import personas as personas_mod
    catalog = personas_mod.load_all(project, cfg.get("persona_max_chars", 2000))
    if not catalog:
        print(red("no personas to convene") + dim(" — the shipped cast seems missing"))
        return
    topic, members = args, None
    head, _, last = args.rpartition(" ")
    if head and "," in last:
        picks = [personas_mod.resolve(catalog, n)
                 for n in last.split(",") if n.strip()]
        if picks and all(p is not None for p in picks):
            topic = head.strip()
            members = list(dict.fromkeys(picks))  # dedupe, keep order
    if members is None:
        members = [catalog[n] for n in sorted(catalog)][:4]
    if cfg.get("backend") != "mock":
        state = load_gpu_state()
        if state.get("host"):
            _ensure_tunnel(cfg, state)
        if not _probe_vllm(cfg):
            print(red("vLLM endpoint not reachable") + dim(" — `gpu attach` + `gpu serve` first "
                  "(or `config set backend mock` for a dry run)."))
            return
    from hermes.models import resolve
    spec = resolve(cfg)
    think_re = agent._think_re(spec.think_tags)
    backend = make_backend(cfg)
    from hermes import council as council_mod
    print(dim(f"convening: {', '.join(p.name for p in members)} — "
              f"{cfg.get('council_rounds', 2)} round(s), "
              f"{cfg.get('council_max_seconds', 600)}s clock"))
    council_mod.council(project, topic, members, cfg, backend, think_re)


def cmd_checkpoint(cfg, args: str) -> None:
    """Project snapshots taken before file-mutating turns (feature 6).
      checkpoint(s)              list snapshots (newest last)
      checkpoint restore <id>    revert the project to a snapshot
    """
    from hermes import checkpoint
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    parts = args.split(maxsplit=1)
    sub = parts[0] if parts else "list"
    if sub == "restore" and len(parts) > 1:
        cid = parts[1].strip()
        snaps = {c["id"] for c in checkpoint.list_checkpoints(project)}
        if cid not in snaps:
            print(red(f"no such checkpoint: {cid}") + dim(" — `checkpoint` to list"))
            return
        from hermes.confirm import confirm
        if not confirm(f"revert project '{project.name}' to checkpoint {cid}?",
                       detail=dim("  overwrites workspace/tools/skills/notes/etc. "
                                  "with the snapshot")):
            print(dim("cancelled."))
            return
        if checkpoint.restore(project, cid):
            print(green(f"reverted to {cid}."))
        else:
            print(red("restore failed."))
    else:
        snaps = checkpoint.list_checkpoints(project)
        if not snaps:
            print(dim("(no checkpoints yet — they're taken before file-mutating turns)"))
        for c in snaps:
            label = dim(f"  {c['label']}") if c.get("label") else ""
            print(f"  {cyan(c['id'])}  {dim(c.get('ts', ''))}{label}")


def cmd_config(cfg, args: str) -> None:
    args = args.strip()
    # accept both `config key value` and `config set key value` / `config get key`
    first, _, rest = args.partition(" ")
    if first in ("set", "get"):
        args = rest.strip()
    parts = args.split(maxsplit=1)
    if len(parts) == 2:
        cfg.set(parts[0], parts[1])
        cfg.save()
        print(f"{parts[0]} = {cfg.get(parts[0])}")
    elif len(parts) == 1 and parts[0]:
        print(json.dumps(cfg.get(parts[0]), indent=2))
    else:
        redacted = dict(cfg.data)
        if redacted.get("vast_api_key"):
            redacted["vast_api_key"] = "***"
        print(json.dumps(redacted, indent=2))


def cmd_info(cfg, what: str, args: str) -> None:
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    if what == "mission":
        if args.strip() == "edit":
            _edit_file(project.mission_path)
        else:
            print(project.read_mission())
    elif what == "notes":
        print(project.read_notes() or dim("(no notes)"))
    elif what == "history":
        n = int(args) if args.strip().isdigit() else 20
        for e in project.recent_prompts(n):
            head = f"[{e.get('run', '?'):>4}] {e.get('ts', '')}"
            print(f"{dim(head)}  {e.get('text', '')[:120]}")
    elif what == "summaries":
        n = int(args) if args.strip().isdigit() else 3
        for run_id, text in project.recent_summaries(n):
            print(f"{cyan(f'--- run {run_id:04d} ---')}\n{text}\n")


def cmd_tools(cfg) -> None:
    from hermes.confirm import confirm
    from hermes.tools import build_registry
    project = _current_project(cfg)
    if project is None:
        print(yellow("no current project"))
        return
    registry = build_registry(project, cfg, confirm)
    for name in registry.names():
        t = registry._tools[name]
        print(f"  {cyan(name)} {dim(f'[{t.origin}]')}")
    print("\nlibrary (equip via the agent's list_toolbox/equip_tool):")
    for name, t in registry.library_tools().items():
        print(f"  {cyan(name)}: {t.description[:90]}")


HELP = f"""\
{cyan('run')} <text>            start an agent run {dim('(alias: r)')}
{cyan('project')} new|use|list  manage projects {dim('(alias: p)')}
{cyan('mission')} [edit]        show/edit the project mission
{cyan('notes')} / {cyan('history')} [n] / {cyan('summaries')} [n]
{cyan('directives')} [edit|reconcile]  standing instructions distilled from history
{cyan('skills')} [show|edit <name>]  the agent's reusable how-to notes
{cyan('personas')} [show|edit|use <name>]  the cast of archetypes ({cyan('run hey <name>, ...')} invokes one)
{cyan('council')} <topic> [names]  the cast deliberates in a clocked circle; the scribe writes the outcome
{cyan('days')} [show|log <id>]   the workday record — each `run` is a briefing→work→debrief day when on
{cyan('landmark')} [<name> <text>|rm <name>]  marks on the road — the next briefing must address them
{cyan('checkpoint')} [restore <id>]  project snapshots before file-mutating turns
{cyan('tools')}                 list the agent's tools
{cyan('gpu')} attach [sshstr] | serve | status | tunnel | down   {dim('(alias: g)')}
{cyan('host')} add <name> <sshstr> [note] | list | rm <name>     your real servers
{cyan('sandbox')} status | provision                            the local box Hermes runs on
{cyan('persona')} edit          edit the persona appended to the system prompt
{cyan('debug')} prefix          measure the prefix-cache-shared bytes across two packages
{cyan('config')} [key [value]]  view/set configuration
{cyan('quit')}                  exit
"""


def dispatch(cfg, line: str) -> bool:
    """Returns False to exit the REPL."""
    line = line.strip()
    if not line:
        return True
    cmd, _, rest = line.partition(" ")
    cmd = {"r": "run", "p": "project", "g": "gpu", "exit": "quit", "q": "quit"}.get(cmd, cmd)
    if cmd == "quit":
        return False
    elif cmd == "help":
        print(HELP)
    elif cmd == "run":
        cmd_run(cfg, rest)
    elif cmd == "project":
        cmd_project(cfg, rest)
    elif cmd == "gpu":
        cmd_gpu(cfg, rest)
    elif cmd == "host":
        cmd_host(cfg, rest)
    elif cmd == "sandbox":
        cmd_sandbox(cfg, rest)
    elif cmd == "config":
        cmd_config(cfg, rest)
    elif cmd == "directives":
        cmd_directives(cfg, rest)
    elif cmd == "skills":
        cmd_skills(cfg, rest)
    elif cmd == "personas":
        cmd_personas(cfg, rest)
    elif cmd == "council":
        cmd_council(cfg, rest)
    elif cmd in ("days", "day"):
        cmd_days(cfg, rest)
    elif cmd in ("landmark", "landmarks"):
        cmd_landmark(cfg, rest)
    elif cmd == "debug":
        cmd_debug(cfg, rest)
    elif cmd in ("checkpoint", "checkpoints"):
        cmd_checkpoint(cfg, rest)
    elif cmd in ("mission", "notes", "history", "summaries"):
        cmd_info(cfg, cmd, rest)
    elif cmd == "tools":
        cmd_tools(cfg)
    elif cmd == "persona":
        _edit_file(persona_path())
    else:
        print(red(f"unknown command: {cmd}") + dim(" (try `help`)"))
    return True


def main() -> None:
    cfg = Config.load()
    cfg.save()  # materialize defaults + persona on first start
    hermes_home().mkdir(parents=True, exist_ok=True)
    print(BANNER)
    project = cfg.get("current_project") or "-"
    print(f"project: {cyan(project)} {dim('·')} backend: {cyan(cfg.get('backend'))}")

    session = None
    ansi = None
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import ANSI as ansi
        from prompt_toolkit.history import FileHistory
        session = PromptSession(history=FileHistory(str(hermes_home() / "repl_history")))
    except Exception:
        pass

    while True:
        proj = cfg.get("current_project") or "-"
        prompt_text = f"{magenta('hermes')}({cyan(proj)})> "
        try:
            line = session.prompt(ansi(prompt_text)) if session else input(prompt_text)
        except (EOFError, KeyboardInterrupt):
            print()
            break
        try:
            if not dispatch(cfg, line):
                break
        except Exception as e:  # the REPL must survive anything
            print(red(f"error: {type(e).__name__}: {e}"))
    print(dim("bye."))


if __name__ == "__main__":
    main()
