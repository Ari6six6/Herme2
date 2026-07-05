"""The sandbox: the box Hermes runs on.

Hermes lives on a persistent VPS — you SSH into it from your phone and drive the
REPL there. Containers run as **children on this same box**, reachable at
localhost, decoupled from the GPU (which is still rented on demand and reached
over SSH). There is no remote sandbox to register: the sandbox is here, and
`local_endpoint()` runs commands on it.
"""

from __future__ import annotations

from hermes.sandbox.local import LocalEndpoint


def local_endpoint() -> LocalEndpoint:
    """An executor for the local box."""
    return LocalEndpoint()


def probe_container_runtime(ep) -> str:
    """Which container runtime is installed, '' if none. Docker preferred (the
    common case on a plain Ubuntu box); podman accepted where it's the default."""
    rc, out, _ = ep.run(
        "command -v docker >/dev/null 2>&1 && echo docker || "
        "{ command -v podman >/dev/null 2>&1 && echo podman || echo none; }",
        timeout=30,
    )
    found = (out or "").strip().splitlines()[-1].strip() if rc == 0 and out.strip() else "none"
    return found if found in ("docker", "podman") else ""


def runtime_usable(ep, runtime: str) -> tuple[bool, str]:
    """Can the *current user* actually reach the runtime's daemon? A runtime can be
    installed (on PATH) yet unusable — the Hermes user isn't in the `docker` group,
    so the socket rejects it, or the daemon isn't running. `<runtime> ps` is a cheap
    round-trip that needs a live, permitted daemon connection. Returns (ok, detail)
    so the caller can surface the real reason instead of a generic guess."""
    if not runtime:
        return False, "no container runtime installed"
    rc, out, err = ep.run(f"{runtime} ps", timeout=30)
    return rc == 0, (err or out or "").strip()


def probe_kvm(ep) -> bool:
    """Does the box expose /dev/kvm? Gate for the future Firecracker microVM path
    — most cheap VPSes are themselves KVM guests with nested virt OFF, so this is
    usually False and we run plain containers instead."""
    rc, out, _ = ep.run("test -e /dev/kvm && echo KVM || echo NOKVM", timeout=20)
    return rc == 0 and "KVM" in out and "NOKVM" not in out


def capabilities(ep) -> dict:
    """Probe what isolation the box can offer, for `sandbox status`."""
    return {
        "runtime": probe_container_runtime(ep),
        "kvm": probe_kvm(ep),
    }
