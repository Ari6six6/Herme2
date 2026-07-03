"""Tool primitives: the Tool dataclass, @tool decorator, and ToolContext.

A tool is fn(args: dict, ctx: ToolContext) -> str. Schemas are explicit
JSON-schema dicts — they are part of the model contract and are hand-tuned,
not introspected.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[[dict, "ToolContext"], str]
    origin: str = "builtin"  # builtin | toolbox | forged

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(name: str, description: str, parameters: dict):
    def deco(fn):
        return Tool(name=name, description=description, parameters=parameters, fn=fn)

    return deco


@dataclass
class ToolContext:
    project: object  # Project
    cfg: object  # Config
    gpu: object | None = None  # SSHEndpoint or None
    sandbox: object | None = None  # the VPS sandbox-host SSHEndpoint, or None
    hosts: dict = field(default_factory=dict)  # managed hosts: name -> SSHEndpoint
    confirm: Callable[..., bool] = lambda *a, **k: False
    registry: Optional[object] = None  # set after build
    served_ctx: int = 0
    finish_summary: str | None = None
    notices: list[str] = field(default_factory=list)
    # Subagent delegation (feature 4): the child loop needs the model + reasoning
    # tags to run, and its depth so recursion can be capped.
    backend: object | None = None
    think_re: object | None = None
    depth: int = 0


def obj_schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
