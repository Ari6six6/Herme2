"""App configuration: ~/.hermes/config.json with sane defaults.

HERMES_HOME env var overrides the home dir (used by tests).
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from hermes.ui import yellow

DEFAULTS: dict = {
    "backend": "openai",  # "openai" (vLLM endpoint) or "mock"
    "base_url": "http://127.0.0.1:8000/v1",
    "api_key": "hermes",  # vLLM doesn't check it, but the client wants one
    "model_id": "hermes",  # which row of hermes.models.CATALOG to serve
    "model": "NousResearch/Hermes-4.3-36B",  # served model name the client sends
    "quantization": "fp8",  # on-the-fly FP8; weight-only fallback on Ampere
    "vast_api_key": "",
    "projects_dir": str(Path.home() / "hermes-projects"),
    "current_project": "",
    "sampling": {"temperature": 0.6, "top_p": 0.95, "top_k": 20},
    "max_turns": 40,
    "stall_nudges": 2,  # bounce prose-only turns back N times before accepting them as final
    "phantom_nudges": 1,  # bounce a finish that pasted code but wrote/ran nothing
    "build_proof_nudges": 1,  # in build mode, bounce a finish that never checked the twin
    "verify_code_runs": True,  # after a code task, an independent pass re-runs it in the sandbox
    "verify_rounds": 2,  # how many times that pass may bounce a failed run back
    "verify_max_turns": 6,  # tool-call budget inside one verification/referee pass
    "plan_build_tasks": True,  # build mode: a planner lays out a checklist before building
    "referee_on_deadlock": True,  # build mode: a referee breaks a builder/antithesis deadlock
    "build_live_touch": False,  # sealed build mode: False cuts off ALL live-target reach (no web
                                 # tools, no twin_expand/twin_reground) so `run` can only ever hit
                                 # the twin; set True to re-allow those narrowly-scoped live reads
    "max_tool_result_chars": 8000,
    "package_budget_tokens": 10000,  # scaled down automatically on small contexts
    "history_max_prompts": 30,
    "summaries_max": 8,
    # Directive reconciliation (feature 1). The machinery is off by default; the
    # header recency-rule line is on by default (it's true and cheap regardless).
    "directives_enabled": False,  # distil history into directives.md; send it + last K prompts
    "directive_header_rule": True,  # add the "recent instruction wins" line to the header
    "reconcile_every_runs": 10,  # auto-reconcile every N runs (plus migration on first run)
    "directives_recent_k": 5,  # raw prompts still sent when directives are on
    # Lazy compaction of the live within-prompt conversation (feature 2). Off by
    # default; needs a known served context window to compute the thresholds.
    "compaction_enabled": False,
    "compaction_trigger_frac": 0.5,  # compact when the live context passes this fraction of the window
    "compaction_keep_last_turns": 6,  # always keep this many most-recent turns verbatim
    "compaction_floor_frac": 0.25,  # target size after a compaction (documented target, see USAGE)
    # Skills (feature 3): reusable how-to notes. Index of one-liners in the
    # package; load_skill pulls a full body on demand.
    "skills_enabled": False,  # inject the skills index + register load_skill/write_skill
    "skills_nudge": False,  # after a run that took figuring-out, invite writing/updating a skill
    "skills_nudge_max_turns": 3,  # tool-call budget for that post-task skill-writing pass
    # Subagent delegation (feature 4): a delegate tool that runs a clean child
    # loop with a subset of tools and returns one conclusion.
    "delegate_enabled": False,
    "delegate_max_turns": 20,  # child turn cap (lower than the parent's by default)
    "delegate_max_depth": 1,  # 1 = children don't spawn grandchildren
    # Prefix-cache-friendly package ordering (feature 5): move volatile runtime
    # status (date, GPU, hosts) out of the stable header so the header + persona
    # + tools + skills index stay a byte-identical prefix for vLLM prefix caching.
    "prefix_cache_order": False,
    # Checkpointing (feature 6): snapshot the project before a turn mutates files
    # so a run gone sideways is one revert. On by default — pure safety.
    "checkpointing": True,
    "checkpoint_max": 20,  # keep the most recent N snapshots per project
    # Verification enforcement (feature 7): require an executed verification step
    # before a task is reported done. Adds a header rule + a one-shot harness
    # nudge when a file-mutating run finishes without running anything.
    "verify_before_done": False,
    # Self-build (feature 9): the agent's own source, gated far tighter than
    # project files — off by default, and even when on, a fixed denylist of
    # safety-critical files (the gates themselves) refuses edits regardless.
    "self_build_enabled": False,
    "auto_confirm": False,  # True: unattended mode — approve every y/n gate (local_shell, state-changing web, host writes, forged-tool loads) so a run never stalls waiting for an operator who's away
    "gpu_shell": False,  # False: GPU box is the model's host only; code runs in the air-gapped sandbox. True: also expose remote_shell/read/write for on-card compute
    "allow_gpu_network": False,  # only relevant when gpu_shell is on. False: box may install/build (net), but raw egress + target traffic go via the VPS; True: unrestricted box net
    "sandbox_image": "python:3.12-slim",  # base image for the air-gapped exec container (sandbox_shell)
    "twin_clone_max": 200,  # max requests recording a target's responses makes
    "twin_clone_delay": 0.5,  # polite seconds between reads while recording
    "twin_clone_depth": 0,  # 0 = fingerprint only (no page crawl); >0 follows links
    "twin_port": 8900,  # localhost port the runtime twin container publishes on
    "twin_base_image": "ubuntu:22.04",  # base image the container twin boots from before the recipe
    "twin_serve_step_timeout": 1800,  # per-recipe-step timeout when respinning from the blueprint
    "max_model_len": 0,  # 0 = pick automatically from detected VRAM
    "gpu_port": 8000,
    "local_port": 8000,
    "max_completion_tokens": 8192,
    "extra_vllm_args": [],
    "extra_llama_args": [],  # appended to llama-server for GGUF models
}


def hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))


def config_path() -> Path:
    return hermes_home() / "config.json"


def persona_path() -> Path:
    return hermes_home() / "persona.md"


DEFAULT_PERSONA = """\
You are Hermes: sharp, direct, loyal. You think hard before you act, you keep
your operator informed in plain language, and you finish what you start.
"""


class Config:
    def __init__(self, data: dict):
        self.data = data

    @classmethod
    def load(cls) -> "Config":
        data = copy.deepcopy(DEFAULTS)
        path = config_path()
        if path.exists():
            try:
                stored = json.loads(path.read_text())
                _deep_update(data, stored)
            except (json.JSONDecodeError, OSError) as e:
                print(yellow(f"warning: could not read {path}: {e} — using defaults"))
        return cls(data)

    def save(self) -> None:
        home = hermes_home()
        home.mkdir(parents=True, exist_ok=True)
        config_path().write_text(json.dumps(self.data, indent=2) + "\n")
        os.chmod(config_path(), 0o600)  # holds vast_api_key
        if not persona_path().exists():
            persona_path().write_text(DEFAULT_PERSONA)

    def get(self, key: str, default=None):
        """Dotted-key get: cfg.get("sampling.temperature")."""
        node = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, key: str, value, coerce: bool = True) -> None:
        """Dotted-key set. Strings are type-coerced (bools/ints/floats) so
        `config set max_turns 40` stores an int — but pass coerce=False for values
        that must stay strings even when they look numeric, like a project named
        "2" (otherwise it becomes int 2 and later `projects_dir / 2` blows up)."""
        parts = key.split(".")
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = _coerce(value) if coerce else value

    def __getitem__(self, key: str):
        return self.data[key]


def _deep_update(base: dict, extra: dict) -> None:
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def _coerce(value):
    if not isinstance(value, str):
        return value
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def read_persona(max_chars: int = 2000) -> str:
    path = persona_path()
    if not path.exists():
        return DEFAULT_PERSONA
    text = path.read_text()
    if len(text) > max_chars:
        text = text[:max_chars] + "\n[persona truncated]"
    return text
