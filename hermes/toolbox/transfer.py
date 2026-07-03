"""Toolbox: move files between the phone project and the GPU box (binary-safe).

This is THE bridge for the no-internet-on-GPU rule: download on the phone,
push to the box; compute on the box, pull results back. Bytes are streamed
over the SSH connection — no base64 inflation, nothing buffered in phone RAM.
"""

TOOL = {
    "name": "transfer",
    "description": (
        "Copy a file between the phone project and the GPU box. "
        "direction 'push' = phone->box, 'pull' = box->phone. Binary-safe."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["push", "pull"]},
            "local_path": {"type": "string", "description": "path inside the project"},
            "remote_path": {
                "type": "string",
                "description": "path on the GPU box (relative = inside the remote workspace)",
            },
        },
        "required": ["direction", "local_path", "remote_path"],
    },
}


def run(args, ctx):
    from hermes.paths import PathDenied, resolve_in
    from hermes.ssh import anchored_path, shell_path
    from hermes.tools._common import need_gpu

    err = need_gpu(ctx)
    if err:
        return err
    try:
        local = resolve_in(ctx.project.root, args["local_path"])
    except PathDenied:
        return "DENIED: local_path must stay inside the project."
    remote = anchored_path(args["remote_path"], ctx.gpu.remote_workspace)
    remote_q = shell_path(remote)

    if args["direction"] == "push":
        if not local.is_file():
            return f"ERROR: no such local file: {args['local_path']}"
        rc, err = ctx.gpu.run_in_from_file(
            f'mkdir -p "$(dirname {remote_q})" && cat > {remote_q}', local
        )
        if rc != 0:
            return f"ERROR: push failed: {err.strip()[-400:]}"
        return f"pushed {local.stat().st_size} bytes to {remote}"

    local.parent.mkdir(parents=True, exist_ok=True)
    rc, err = ctx.gpu.run_out_to_file(f"cat {remote_q}", local)
    if rc != 0:
        local.unlink(missing_ok=True)
        return f"ERROR: pull failed: {err.strip()[-400:]}"
    return f"pulled {local.stat().st_size} bytes to {args['local_path']}"
