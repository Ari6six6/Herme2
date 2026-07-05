"""local_shell cwd frame: it runs at the project root, matching the file tools.

Regression for the namespace trap where the shell silently started inside
`workspace/`, so a model reasoning in the project-root frame (the frame the
file tools and the `cwd` arg use) wrote `cd workspace && python x.py` and hit
`cd: can't cd to workspace` — then misread the failure as a missing file.
"""

import json

from hermes.tools import build_registry
from hermes.tools.base import ToolContext


def _ctx(project, cfg, confirm):
    registry = build_registry(project, cfg, confirm)
    ctx = ToolContext(project=project, cfg=cfg, confirm=confirm)
    ctx.registry = registry
    return registry, ctx


def test_default_cwd_is_project_root(project, cfg, yes):
    registry, ctx = _ctx(project, cfg, yes)
    out = registry.dispatch("local_shell", json.dumps({"command": "pwd"}), ctx)
    assert str(project.root) in out
    assert str(project.workspace_dir) not in out


def test_workspace_relative_script_runs_without_cd(project, cfg, yes):
    # exactly the flow from the aborted run: write to workspace/, then run it
    # in the project-root frame the model already used for write_file.
    (project.workspace_dir / "recon.py").write_text("print('ran')")
    registry, ctx = _ctx(project, cfg, yes)
    out = registry.dispatch(
        "local_shell",
        json.dumps({"command": "python3 workspace/recon.py"}),
        ctx,
    )
    assert "exit code 0" in out
    assert "ran" in out


def test_cwd_arg_still_anchors_to_project_root(project, cfg, yes):
    registry, ctx = _ctx(project, cfg, yes)
    out = registry.dispatch(
        "local_shell",
        json.dumps({"command": "pwd", "cwd": "workspace"}),
        ctx,
    )
    assert str(project.workspace_dir) in out


def test_cwd_outside_project_denied(project, cfg, yes):
    registry, ctx = _ctx(project, cfg, yes)
    out = registry.dispatch(
        "local_shell",
        json.dumps({"command": "pwd", "cwd": "../.."}),
        ctx,
    )
    assert out.startswith("DENIED")
