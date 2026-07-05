"""Run the twin on the sandbox host: a contained Linux box running the real
reconstructed software.

`build serve` stands the twin up inside a **container** on the VPS sandbox host,
listening on `127.0.0.1:<port>` of the VPS (then tunneled to the phone), so the
solution the agent writes — and its tests — hit a faithful, *isolated*, live clone
of the target instead of the real service.

The twin is the **real software**, reconstructed from a blueprint that lives on the
phone: the project's `twin/recipe.jsonl` (the ordered, captured reconstruction
steps) plus the recon manifest. `build serve` boots a fresh container from a base
image and replays the recipe steps *inside it* (each step is a `docker exec`), so
the whole stack — packages, runtime, app — is installed and launched in a
contained system. Change VPSes or wipe the box and the same blueprint respins the
runtime twin; no agent turns, no reinventing the wheel.

There is no recorded-response fallback: a twin is the real running software or it
is nothing. With no recipe yet, `build serve` says so and points you at `run
build` to derive the reconstruction.
"""

from __future__ import annotations

import re
import shlex
import time
from pathlib import Path

from hermes.twin.model import TwinModel

REMOTE_SUBDIR = "twin-runtime"
DEFAULT_BASE_IMAGE = "ubuntu:22.04"


def serve_log_path(model: TwinModel) -> Path:
    """Where the last `build serve` transcript is written, on the phone — so a
    failed respin is debuggable without the box."""
    return model.root / "serve.log"


def _write_serve_log(model: TwinModel, lines: list[str]) -> None:
    try:
        model.root.mkdir(parents=True, exist_ok=True)
        header = time.strftime("%Y-%m-%d %H:%M:%S") + "  build serve\n"
        serve_log_path(model).write_text(header + "\n".join(lines) + "\n")
    except OSError:
        pass


def container_name(model: TwinModel) -> str:
    """A stable, docker-safe container name per project (the twin dir's parent is
    the project dir). Keeps respins idempotent and lets `stop` find it."""
    raw = model.root.parent.name or "hermes"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "-", raw).strip("-") or "hermes"
    return f"hermes-twin-{safe}"


# A network-free liveness probe against the published port, in stdlib python
# (always on the VPS). Exit 0 means something is listening on the host side of
# the container's port mapping.
_HEALTHCHECK = (
    "python3 - <<'PY'\n"
    "import socket,sys\n"
    "s=socket.socket(); s.settimeout(2)\n"
    "sys.exit(0 if s.connect_ex(('127.0.0.1',{port}))==0 else 1)\n"
    "PY"
)


def _alive(ep, port: int) -> bool:
    rc, _, _ = ep.run(_HEALTHCHECK.format(port=int(port)))
    return rc == 0


def _container_exists(ep, name: str, runtime: str) -> bool:
    rc, out, _ = ep.run(
        f"{runtime} ps -a --filter name=^{shlex.quote(name)}$ --format '{{{{.Names}}}}'"
    )
    return rc == 0 and name in (out or "")


def _run_container_cmd(name: str, port: int, base_image: str, runtime: str) -> str:
    """The long-lived container is the contained Linux box; we exec the recipe
    into it. Publish the port only on this box's loopback, never the public net."""
    return (
        f"{runtime} run -d --name {shlex.quote(name)} "
        f"-p 127.0.0.1:{int(port)}:{int(port)} "
        f"-e TWIN_PORT={int(port)} -w /twin {shlex.quote(base_image)} sleep infinity"
    )


def exec_step(ep, name: str, cmd: str, port: int, runtime: str,
              timeout: int = 600):
    """Run one command inside the twin container, with TWIN_PORT exported and the
    cwd at /twin. Returns (rc, stdout, stderr)."""
    return ep.run(
        f"{runtime} exec -e TWIN_PORT={int(port)} {shlex.quote(name)} "
        f"sh -lc {shlex.quote(cmd)}",
        timeout=timeout,
    )


def ensure_build_container(ep, model: TwinModel, port: int,
                           base_image: str = DEFAULT_BASE_IMAGE,
                           runtime: str = "") -> tuple[str, str]:
    """Make sure the long-lived twin container exists; return (runtime, name).
    This is the contained box the builder reconstructs the stack *inside* during
    the build phase — build_run execs each step here and captures it to the
    recipe, and `build serve` later replays that recipe into a fresh container.
    Raises SandboxError if the runtime or the container can't be brought up."""
    from hermes.sandbox.provision import SandboxError, ensure_runtime

    if not runtime:
        runtime = ensure_runtime(ep)
    name = container_name(model)
    if not _container_exists(ep, name, runtime):
        rc, out, err = ep.run(_run_container_cmd(name, port, base_image, runtime))
        if rc != 0:
            raise SandboxError(
                f"could not start the build container: {(err or out).strip()[-300:]}")
        ep.run(f"{runtime} exec {shlex.quote(name)} sh -lc 'mkdir -p /twin'")
    return runtime, name


def deploy(ep, model: TwinModel, port: int, on_event=None,
           step_timeout: int = 1800, clean: bool = False,
           base_image: str = DEFAULT_BASE_IMAGE, runtime: str = "") -> dict:
    """Bring the twin up inside a container on the sandbox host, listening on
    127.0.0.1:<port> of the VPS. Replays the blueprint recipe *inside* the
    container. With clean=True, tears down any existing container first for a
    fresh respin. Returns {"ok", "port", "container", "log", "source",
    "log_path"} (or {"ok": False, "error", ...})."""
    def emit(text):
        if on_event:
            on_event(text)

    log_path = str(serve_log_path(model))

    if not runtime:
        from hermes.sandbox.provision import SandboxError, ensure_runtime
        try:
            runtime = ensure_runtime(ep, on_event=emit)
        except SandboxError as e:
            _write_serve_log(model, [f"# {e}"])
            return {"ok": False, "error": str(e), "source": "container",
                    "log_path": log_path}

    recipe = model.recipe()
    if not recipe:
        msg = ("no reconstruction recipe yet — a twin is the real running software, "
               "not a recording. Derive the build with `run build`: the builder "
               "captures each working step into the blueprint (build_run), then "
               "`build serve` replays it into a container here.")
        _write_serve_log(model, ["# " + msg])
        return {"ok": False, "error": msg, "source": "container", "log_path": log_path}

    return _serve_container(ep, model, port, recipe, emit, step_timeout, clean,
                            base_image, runtime, log_path)


def _serve_container(ep, model, port, recipe, emit, step_timeout, clean,
                     base_image, runtime, log_path) -> dict:
    """Boot a fresh container and replay the captured reconstruction steps inside
    it, with a transcript written to the phone for debugging."""
    name = container_name(model)
    transcript: list[str] = []

    def done(extra: dict) -> dict:
        _write_serve_log(model, transcript)
        base = {"container": name, "source": "container", "log_path": log_path}
        base.update(extra)
        return base

    if not clean and _container_exists(ep, name, runtime) and _alive(ep, port):
        emit(f"already serving on :{port} (container {name})")
        transcript.append(f"# already up on :{port}")
        return done({"ok": True, "port": port, "log": "already up"})

    # Fresh respin: drop any old container so the recipe replays clean.
    ep.run(f"{runtime} rm -f {shlex.quote(name)} 2>/dev/null || true")
    if clean:
        emit("clean respin — removed any existing container")

    # A long-lived container is the contained Linux box; we exec the recipe into
    # it. Publish the port only on this box's loopback, never the public net.
    run_cmd = _run_container_cmd(name, port, base_image, runtime)
    rc, out, err = ep.run(run_cmd, timeout=step_timeout)
    transcript.append(f"$ {run_cmd}\n[rc {rc}] {(err or out).strip()[-300:]}".rstrip())
    if rc != 0:
        return done({"ok": False,
                     "error": f"could not start the container: {(err or out).strip()[-300:]}"})
    ep.run(f"{runtime} exec {shlex.quote(name)} sh -lc 'mkdir -p /twin'")
    emit(f"container {name} up from {base_image} — replaying {len(recipe)} step(s)")

    for i, step in enumerate(recipe, 1):
        cmd = step.get("cmd", "")
        note = step.get("note") or cmd
        # Each step runs in /twin inside the container, with TWIN_PORT exported.
        rc, out, err = exec_step(ep, name, cmd, port, runtime, step_timeout)
        tail = (err or out or "").strip()[-500:]
        transcript.append(f"$ exec: {cmd}\n[rc {rc}] {tail}".rstrip())
        if rc != 0:
            return done({"ok": False,
                         "error": f"recipe step {i}/{len(recipe)} failed "
                                  f"({note[:60]}): {tail[-300:]}"})
        emit(f"step {i}/{len(recipe)} ok: {note[:60]}")

    ep.run("sleep 1")  # let a just-launched daemon bind the port
    up = _alive(ep, port)
    transcript.append(f"# health check :{port} -> {'listening' if up else 'NOT listening'}")
    if up:
        emit(f"runtime twin up on :{port} (container {name})")
        return done({"ok": True, "port": port,
                     "log": f"recipe replayed in {name}, listening on :{port}"})
    return done({"ok": False,
                 "error": f"recipe replayed but nothing is listening on :{port}. The "
                          "serving step must bind 0.0.0.0:$TWIN_PORT (not 127.0.0.1, "
                          "or the published port can't reach it) and run in the "
                          "background (nohup ... &). See the serve log."})


def stop(ep, model: TwinModel, runtime: str = "docker") -> None:
    """Tear the twin container down on the sandbox host."""
    ep.run(f"{runtime} rm -f {shlex.quote(container_name(model))} 2>/dev/null || true")
