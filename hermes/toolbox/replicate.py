"""Toolbox: clone files from a managed host into the GPU box workspace.

For the opt-in on-card path (needs the GPU shell): pull the broken thing off the
real server, rebuild it on the disposable GPU box, iterate freely there, then
apply the verified fix back with host_write / host_shell. The VPS relays the
bytes — the GPU box never gets a network path to your servers. (For routine work
you don't need the card, experiment in the air-gapped VPS sandbox instead.)
"""

import shlex
import tempfile
from pathlib import Path, PurePosixPath

MAX_BYTES = 200 * 1024 * 1024
DEFAULT_EXCLUDES = [".git", "node_modules", "__pycache__", ".venv"]

TOOL = {
    "name": "replicate",
    "description": (
        "Copy a file or directory FROM a managed host INTO the GPU box "
        "workspace (relayed through the VPS; needs the GPU shell on) so you can "
        "experiment on a copy instead of the live server. Reads on the host are free. "
        "Max 200MB; directories skip .git/node_modules/__pycache__/.venv "
        "unless you pass excludes=[]"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "host": {"type": "string", "description": "registered host name"},
            "src": {"type": "string", "description": "absolute path on the host"},
            "dest": {
                "type": "string",
                "description": "name inside the GPU workspace (default: basename of src)",
            },
            "excludes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "tar exclude patterns for directories (optional)",
            },
        },
        "required": ["host", "src"],
    },
}


def run(args, ctx):
    from hermes.ssh import shell_path
    from hermes.tools._common import host_or_error, need_gpu

    err = need_gpu(ctx)
    if err:
        return err
    ep = host_or_error(ctx, args["host"])
    if isinstance(ep, str):
        return ep

    src = args["src"].rstrip("/") or "/"
    src_q = shell_path(src)

    # Size check first — fail closed if we can't measure.
    rc, out, err = ep.run(f"du -sb {src_q}", timeout=120)
    mult = 1
    if rc != 0:  # busybox du has no -b; -k reports KiB
        rc, out, err = ep.run(f"du -sk {src_q}", timeout=120)
        mult = 1024
    if rc != 0:
        return f"ERROR: cannot stat {src} on '{args['host']}': {err.strip()[-300:]}"
    try:
        size = int(out.split()[0]) * mult
    except (ValueError, IndexError):
        return f"ERROR: could not determine size of {src} (du said: {out[:200]!r})"
    if size > MAX_BYTES:
        return (f"ERROR: {src} is ~{size // (1024 * 1024)}MB — over the "
                f"{MAX_BYTES // (1024 * 1024)}MB replicate cap. Narrow src or "
                f"add excludes.")

    rc, out, _ = ep.run(f"test -d {src_q} && echo DIR || echo FILE", timeout=60)
    if rc != 0:
        return f"ERROR: cannot inspect {src} on '{args['host']}'"
    is_dir = "DIR" in out

    name = PurePosixPath(src).name
    dest = (args.get("dest") or name).strip("/")
    dst = f"{ctx.gpu.remote_workspace}/{dest}"

    tmp_dir = Path(tempfile.gettempdir())
    with tempfile.NamedTemporaryFile(dir=tmp_dir, prefix="hermes-replicate-",
                                     delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if is_dir:
            excludes = args.get("excludes")
            if excludes is None:
                excludes = DEFAULT_EXCLUDES
            excl = " ".join(f"--exclude={shlex.quote(e)}" for e in excludes)
            parent = str(PurePosixPath(src).parent)
            pull = (f"tar -C {shell_path(parent)} -czf - {excl} "
                    f"{shlex.quote(name)}")
            rc, err = ep.run_out_to_file(pull, tmp_path)
            if rc != 0:
                return f"ERROR: tar on '{args['host']}' failed: {err.strip()[-300:]}"
            ctx.gpu.run(f"mkdir -p {shell_path(dst)}", timeout=60)
            rc, err = ctx.gpu.run_in_from_file(
                f"tar -C {shell_path(dst)} -xzf - --strip-components=1", tmp_path
            )
            if rc != 0:
                return f"ERROR: unpack on the GPU box failed: {err.strip()[-300:]}"
        else:
            rc, err = ep.run_out_to_file(f"cat {src_q}", tmp_path)
            if rc != 0:
                return f"ERROR: read on '{args['host']}' failed: {err.strip()[-300:]}"
            dst_q = shell_path(dst)
            rc, err = ctx.gpu.run_in_from_file(
                f'mkdir -p "$(dirname {dst_q})" && cat > {dst_q}', tmp_path
            )
            if rc != 0:
                return f"ERROR: write on the GPU box failed: {err.strip()[-300:]}"
        moved = tmp_path.stat().st_size
    finally:
        tmp_path.unlink(missing_ok=True)

    kind = "directory" if is_dir else "file"
    return (f"replicated {kind} {src} from host '{args['host']}' -> {dst} on "
            f"the GPU box ({moved} bytes transferred). Inspect it with "
            f"remote_shell, e.g. ls {dest}")
