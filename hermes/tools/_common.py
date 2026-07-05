"""Small shared guards reused across tool modules.

These error strings are returned verbatim to the model, so keeping the check in
one place stops the wording from drifting between the tools that share it.
"""

from __future__ import annotations


def need_gpu(ctx) -> str | None:
    """An error string when no GPU box is attached, else None.

    Used by every tool that runs on the box (remote_*, transfer, replicate)."""
    if ctx.gpu is None:
        return "ERROR: no GPU box attached. Tell the operator to run `gpu attach`."
    return None


def host_or_error(ctx, name: str):
    """The SSHEndpoint for a managed host, or an error string when it's unknown."""
    ep = ctx.hosts.get(name)
    if ep is None:
        known = ", ".join(sorted(ctx.hosts)) or "(none)"
        return (f"ERROR: no managed host '{name}'. Known hosts: {known}. "
                f"The operator registers one with: host add <name> <ssh-string>")
    return ep


def twin_for(ctx):
    """The TwinModel for the current project (shared by the recon/build and
    sealed-twin tool modules)."""
    from hermes.twin.model import TwinModel
    return TwinModel.for_project(ctx.project)
