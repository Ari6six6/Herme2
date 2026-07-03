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
    from hermes.tools import local_fs, local_shell, meta, remote, web

    registry = ToolRegistry()

    for module in (local_fs, local_shell, remote, meta, web):
        for t in module.TOOLS:
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
