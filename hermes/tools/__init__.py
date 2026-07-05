"""ToolRegistry: builtins + equipped toolbox tools + approved forged tools.

dispatch() never raises — every failure mode comes back as a string the
model can read and adapt to.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import traceback
from pathlib import Path

from hermes.tools.base import Tool, ToolContext
from hermes.ui import yellow

TOOLBOX_DIR = Path(__file__).parent.parent / "toolbox"
_FILENAME_RE = re.compile(r"^[A-Za-z0-9_]{1,40}\.py$")


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    # -- registration ------------------------------------------------------
    def register(self, t: Tool, override: bool = False) -> None:
        if t.name in self._tools and not override:
            raise ValueError(f"tool name collision: {t.name}")
        self._tools[t.name] = t

    def names(self) -> list[str]:
        return sorted(self._tools)

    def schemas(self) -> list[dict]:
        return [t.schema() for t in self._tools.values()]

    def without(self, names) -> "ToolRegistry":
        """A shallow clone with `names` removed. Used to hand the verification
        pass a toolset that has no path to the GPU box, so grading runs only in
        the air-gapped sandbox — the doer can still hold those tools."""
        drop = set(names)
        clone = ToolRegistry()
        clone._tools = {n: t for n, t in self._tools.items() if n not in drop}
        return clone

    # -- dispatch ----------------------------------------------------------
    def dispatch(self, name: str, arguments: str, ctx: ToolContext) -> str:
        t = self._tools.get(name)
        if t is None:
            return f"ERROR: unknown tool '{name}'. Available: {', '.join(self.names())}"
        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                raise ValueError("arguments must be a JSON object")
        except (json.JSONDecodeError, ValueError) as e:
            return f"ERROR: invalid arguments for {name}: {e}"
        try:
            result = t.fn(args, ctx)
        except KeyError as e:
            return f"ERROR: missing required argument for {name}: {e}"
        except Exception as e:
            tb = traceback.format_exc(limit=3)
            return f"ERROR: {type(e).__name__}: {e}\n{tb[-1000:]}"
        result = str(result)
        cap = ctx.cfg.get("max_tool_result_chars", 8000)
        if len(result) > cap:
            result = result[:cap] + (
                f"\n[...tool output truncated: showing {cap} of {len(result)} "
                f"chars — what you see above is INCOMPLETE. Re-fetch in smaller "
                f"pieces (offset/limit, head/tail, grep).]"
            )
        return result

    # -- toolbox library (shipped, trusted) ----------------------------------
    def library_tools(self) -> dict[str, Tool]:
        out = {}
        if TOOLBOX_DIR.is_dir():
            for path in sorted(TOOLBOX_DIR.glob("*.py")):
                t = _load_module_tool(path, origin="toolbox")
                if isinstance(t, Tool):
                    out[t.name] = t
        return out

    def toolbox_listing(self, ctx: ToolContext) -> str:
        equipped = set(ctx.project.equipped_tools())
        lines = ["LIBRARY (equip by name):"]
        lib = self.library_tools()
        if not lib:
            lines.append("  (empty)")
        for name, t in lib.items():
            mark = "equipped" if name in equipped or name in self._tools else "available"
            lines.append(f"  - {name} [{mark}]: {t.description[:120]}")
        lines.append("FORGED (this project):")
        forged = sorted(ctx.project.tools_dir.glob("*.py"))
        if not forged:
            lines.append("  (none)")
        approved = ctx.project.approved_hashes()
        for path in forged:
            state = "loaded" if _stem_loaded(self, path) else (
                "approved" if approved.get(path.name) == _digest(path.read_text()) else "unapproved"
            )
            lines.append(f"  - {path.name} [{state}]")
        return "\n".join(lines)

    def equip(self, name: str, ctx: ToolContext) -> str:
        if name in self._tools:
            return f"'{name}' is already equipped."
        t = self.library_tools().get(name)
        if t is None:
            return f"ERROR: no such library tool '{name}'. Use list_toolbox."
        self.register(t)
        ctx.project.equip_tool(name)
        return f"equipped '{name}' — callable from your next turn."

    def forge(self, filename: str, source: str, ctx: ToolContext) -> str:
        if not _FILENAME_RE.match(filename):
            return "ERROR: filename must match [A-Za-z0-9_]+.py"
        path = ctx.project.tools_dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
        t = _load_module_tool(path, origin="forged")
        if not isinstance(t, Tool):
            return f"ERROR: tool failed to load: {t}"
        if t.name in self._tools:
            return f"ERROR: tool name '{t.name}' already exists — pick another name."
        if not ctx.confirm(
            f"agent forged a new tool '{t.name}' ({filename}) — load it?",
            detail=f"  {t.description[:200]}",
            viewable=source,
        ):
            return "DENIED by operator. The file remains at tools/" + filename + \
                " — you may revise it and forge again."
        ctx.project.approve_hash(filename, _digest(source))
        self.register(t)
        return f"forged and loaded '{t.name}' — callable from your next turn."


def _digest(source: str) -> str:
    return hashlib.sha256(source.encode()).hexdigest()


def _stem_loaded(registry: ToolRegistry, path: Path) -> bool:
    return any(
        t.origin == "forged" and getattr(t, "_source_file", "") == path.name
        for t in registry._tools.values()
    )


def _load_module_tool(path: Path, origin: str):
    """Load a TOOL/run module file into a Tool. Returns Tool or error str."""
    try:
        spec = importlib.util.spec_from_file_location(
            f"hermes_dyn_{origin}_{path.stem}", path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        meta = getattr(mod, "TOOL", None)
        run = getattr(mod, "run", None)
        if not isinstance(meta, dict) or not callable(run):
            return "module must define TOOL dict and run(args, ctx)"
        t = Tool(
            name=str(meta["name"]),
            description=str(meta.get("description", "")),
            parameters=meta.get("parameters", {"type": "object", "properties": {}}),
            fn=run,
            origin=origin,
        )
        t._source_file = path.name
        return t
    except Exception as e:
        return f"{type(e).__name__}: {e}"


def toolbox_catalog() -> str:
    """A name + one-line summary for every shipped toolbox tool, for the
    system prompt. Schemas stay out of the prompt (equip loads those), but the
    agent must always SEE the menu — otherwise it concludes it lacks a tool it
    actually has."""
    lib = ToolRegistry().library_tools()
    if not lib:
        return "(toolbox is empty)"
    lines = []
    for name in sorted(lib):
        summary = " ".join(lib[name].description.split())
        if len(summary) > 110:
            summary = summary[:107].rstrip() + "..."
        lines.append(f"- `{name}` — {summary}")
    return "\n".join(lines)


def build_registry(project, cfg, confirm_fn) -> ToolRegistry:
    from hermes import hosts as hosts_mod
    from hermes.tools import local_fs, local_shell, meta, remote, sandbox_tools, web
    from hermes.twin.model import TwinModel

    registry = ToolRegistry()

    # The seal is the phase boundary, and (by default) the network boundary too.
    # OPEN (recon/build, "pure scanning") and plain non-twin projects can reach
    # the live world. Once SEALED — the phase where the agent writes/runs code
    # against the twin — live reach is cut off completely unless the operator
    # explicitly turns it back on (`config set build_live_touch true`): no
    # http_request/web_search, no twin_expand/twin_reground. `run` then has no
    # path to anything but the twin at all, by construction, not by prompt.
    twin_model = TwinModel(project.twin_dir)
    sealed = twin_model.is_sealed()
    live_touch = (not sealed) or cfg.get("build_live_touch", False)

    for module in (local_fs, local_shell, sandbox_tools, meta):
        for t in module.TOOLS:
            registry.register(t)
    # The GPU box is the model's host, not a shell the agent runs code from:
    # code runs in the air-gapped sandbox container (sandbox_shell). The GPU
    # shell (remote_*) is off by default — the operator opens it with
    # `config set gpu_shell true` when a task genuinely needs to compute on the
    # card. Even then it stays network-isolated unless `allow_gpu_network` is
    # also set (that flag governs the box's egress, not whether the shell exists).
    if cfg.get("gpu_shell", False):
        for t in remote.TOOLS:
            registry.register(t)
    if live_touch:
        for t in web.TOOLS:
            registry.register(t)

    # Skills (feature 3): load_skill/write_skill, only when the owner turns the
    # system on. The index of one-liners lives in the system prompt.
    if cfg.get("skills_enabled", False):
        from hermes.tools import skills as skills_tools

        for t in skills_tools.TOOLS:
            registry.register(t)

    # Delegation (feature 4): the delegate tool, only when enabled. A child's
    # tool set is drawn from this same registry, so it can never be broader.
    if cfg.get("delegate_enabled", False):
        from hermes.tools import delegate as delegate_tools

        for t in delegate_tools.TOOLS:
            registry.register(t)

    # Host tools only exist when the operator has registered a server —
    # no schema bloat for setups that never use them.
    if hosts_mod.load_hosts():
        from hermes.tools import hosts as hosts_tools

        for t in hosts_tools.TOOLS:
            registry.register(t)

    # While OPEN we're in the builder phase: the agent gets the builder tools to
    # record the target and reconstruct it. Once SEALED, the build phase begins:
    # the frozen twin to work against, plus (only if live_touch) the narrow,
    # read-only tools that can still reach the live target. (Deeper target
    # reconnaissance is not a built-in tool; it lives in the external herme-recon
    # program, reached via the recon skill through local_shell.)
    if twin_model.exists() and not sealed:
        from hermes.tools import builder as builder_tools

        for t in builder_tools.TOOLS:
            registry.register(t)
    elif sealed:
        from hermes.tools import twin as twin_tools

        for t in twin_tools.ALWAYS_TOOLS:
            registry.register(t)
        if live_touch:
            for t in twin_tools.LIVE_TOUCH_TOOLS:
                registry.register(t)

    # Equipped library tools (shipped with the app — trusted).
    lib = registry.library_tools()
    for name in project.equipped_tools():
        t = lib.get(name)
        if t and t.name not in registry._tools:
            registry.register(t)

    # Forged tools: load silently if the content hash was approved before;
    # otherwise ask once (with view-source option).
    approved = project.approved_hashes()
    for path in sorted(project.tools_dir.glob("*.py")):
        source = path.read_text()
        digest = _digest(source)
        t = _load_module_tool(path, origin="forged")
        if not isinstance(t, Tool):
            print(yellow(f"warning: forged tool {path.name} disabled ({t})"))
            continue
        if t.name in registry._tools:
            print(yellow(f"warning: forged tool {path.name} collides with '{t.name}' — skipped"))
            continue
        if approved.get(path.name) == digest or confirm_fn(
            f"load forged tool '{t.name}' from {path.name}?", viewable=source
        ):
            project.approve_hash(path.name, digest)
            registry.register(t)
    return registry
