"""Feature 3: skills system."""

from pathlib import Path

import pytest

from hermes import package, skills as skills_mod
from hermes.project import Project
from hermes.tools import build_registry
from hermes.tools.base import ToolContext


@pytest.fixture
def two_projects(cfg, tmp_path):
    pdir = Path(cfg.get("projects_dir"))
    a = Project.create(pdir, "proja")
    b = Project.create(pdir, "projb")
    return a, b


def _ctx(project, cfg):
    return ToolContext(project=project, cfg=cfg, confirm=lambda *a, **k: True)


def test_write_global_skill_visible_across_projects(two_projects, cfg):
    a, b = two_projects
    skills_mod.write(a, "deploy_django",
                     "Deploy a Django app behind nginx\n\nUse gunicorn; the gotcha "
                     "is collectstatic must run before reload.", scope="global")
    # index in project A shows it...
    assert "deploy_django" in skills_mod.index(a)
    # ...and it's loadable in project B (the acceptance criterion)
    sk = skills_mod.get(b, "deploy_django")
    assert sk is not None
    assert "collectstatic must run before reload" in sk.body
    assert "deploy_django" in skills_mod.index(b)


def test_project_skill_is_local_and_overrides_global(two_projects, cfg):
    a, b = two_projects
    skills_mod.write(a, "shared", "global version body", scope="global")
    skills_mod.write(a, "localonly", "A's local skill", scope="project")
    skills_mod.write(a, "shared", "A's project override", scope="project")
    # local skill not visible in B
    assert skills_mod.get(b, "localonly") is None
    # project scope overrides global of the same name, in A only
    assert "A's project override" in skills_mod.get(a, "shared").body
    assert skills_mod.get(b, "shared").body.startswith("global version body")


def test_description_is_first_nonempty_line_hash_stripped(two_projects, cfg):
    a, _ = two_projects
    skills_mod.write(a, "s1", "# Fix the flaky test\n\nlong body here", scope="global")
    assert skills_mod.get(a, "s1").description == "Fix the flaky test"


def test_index_ten_skills_under_500_tokens(two_projects, cfg):
    a, _ = two_projects
    for i in range(10):
        skills_mod.write(
            a, f"skill_{i}",
            f"Do useful thing number {i} with a reasonably descriptive one-liner\n\n"
            + ("BODY " * 200),  # big body must NOT count toward the index
            scope="global",
        )
    idx = skills_mod.index(a)
    assert idx.count("\n") == 9  # 10 lines
    assert len(idx) // 4 < 500  # index cost, ~4 chars/token
    assert "BODY BODY" not in idx  # bodies stay out of the index


def test_index_absent_when_no_skills(two_projects, cfg):
    a, _ = two_projects
    assert skills_mod.index(a) == ""


# ---- package + tool wiring ---------------------------------------------------
def test_skills_index_in_system_prompt_only_when_enabled(two_projects, cfg):
    a, _ = two_projects
    skills_mod.write(a, "thing", "One-liner for thing\n\nbody", scope="global")
    off = package.assemble(a, "x", {}, cfg)[0]["content"]
    assert "One-liner for thing" not in off
    cfg.set("skills_enabled", True)
    on = package.assemble(a, "x", {}, cfg)[0]["content"]
    assert "## Skills" in on
    assert "`thing`" in on
    assert "One-liner for thing" in on


def test_skill_tools_registered_only_when_enabled(two_projects, cfg):
    a, _ = two_projects
    reg = build_registry(a, cfg, lambda *x, **k: True)
    assert "load_skill" not in reg.names()
    cfg.set("skills_enabled", True)
    reg = build_registry(a, cfg, lambda *x, **k: True)
    assert "load_skill" in reg.names() and "write_skill" in reg.names()


def test_load_skill_tool_returns_body(two_projects, cfg):
    a, _ = two_projects
    skills_mod.write(a, "howto", "Do the thing\n\nStep 1. Step 2.", scope="global")
    cfg.set("skills_enabled", True)
    reg = build_registry(a, cfg, lambda *x, **k: True)
    out = reg.dispatch("load_skill", '{"name": "howto"}', _ctx(a, cfg))
    assert "Step 1. Step 2." in out
    miss = reg.dispatch("load_skill", '{"name": "nope"}', _ctx(a, cfg))
    assert miss.startswith("ERROR")


def test_write_skill_tool_creates_and_updates(two_projects, cfg):
    a, _ = two_projects
    cfg.set("skills_enabled", True)
    reg = build_registry(a, cfg, lambda *x, **k: True)
    ctx = _ctx(a, cfg)
    out = reg.dispatch("write_skill",
                       '{"name": "s", "content": "desc\\n\\nbody"}', ctx)
    assert "created global skill 's'" in out
    out2 = reg.dispatch("write_skill",
                        '{"name": "s", "content": "desc2\\n\\nbody2"}', ctx)
    assert "updated global skill 's'" in out2
    assert "body2" in skills_mod.get(a, "s").body


def test_write_skill_rejects_bad_name(two_projects, cfg):
    a, _ = two_projects
    cfg.set("skills_enabled", True)
    reg = build_registry(a, cfg, lambda *x, **k: True)
    out = reg.dispatch("write_skill",
                       '{"name": "bad name!", "content": "x"}', _ctx(a, cfg))
    assert out.startswith("ERROR")


# ---- post-task nudge through the real loop -----------------------------------
def test_skills_nudge_lets_agent_capture_a_skill(two_projects, cfg):
    from hermes import agent
    from hermes.llm import MockBackend
    a, _ = two_projects
    cfg.set("skills_enabled", True)
    cfg.set("skills_nudge", True)
    # A run that "figures something out": a tool error, then a fix, then finish.
    # After finish, the nudge pass writes a skill.
    script = [
        {"tool": "read_file", "args": {"path": "workspace/missing.txt"}},  # ERROR seen
        {"tool": "write_file",
         "args": {"path": "workspace/ok.txt", "content": "hi"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
        # --- skills nudge pass ---
        {"tool": "write_skill",
         "args": {"name": "avoid_missing",
                  "content": "Check the file exists first\n\nread_file errors if "
                             "the path is absent; list_files before reading."}},
        {"text": "captured it."},
    ]
    result = agent.run(a, "do the thing", cfg, MockBackend(script),
                       gpu=None, env={}, confirm_fn=lambda *x, **k: True)
    assert not result.aborted
    assert result.summary == "done"  # the nudge did NOT overwrite the summary
    sk = skills_mod.get(a, "avoid_missing")
    assert sk is not None and "list_files before reading" in sk.body


def test_no_nudge_on_trivial_run(two_projects, cfg):
    from hermes import agent
    from hermes.llm import MockBackend
    a, _ = two_projects
    cfg.set("skills_enabled", True)
    cfg.set("skills_nudge", True)
    # A clean 2-turn run with no errors -> heuristic says no figuring-out.
    script = [
        {"tool": "write_note", "args": {"text": "all fine"}},
        {"tool": "finish_run", "args": {"summary": "done"}},
    ]
    result = agent.run(a, "trivial", cfg, MockBackend(script),
                       gpu=None, env={}, confirm_fn=lambda *x, **k: True)
    transcript = (a.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "skills"' not in transcript  # nudge did not fire
