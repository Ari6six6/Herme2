"""Feature 1: directive reconciliation."""

from hermes import directives as directives_mod
from hermes import package
from hermes.llm import ChatResult


class RecencyBackend:
    """Stand-in reconciler: reads the history it's handed and resolves the
    curl conflict by recency, the way the real side-call is asked to. Records
    the prompt it saw so tests can assert the FULL history was passed in."""

    def __init__(self):
        self.saw = None

    def chat(self, messages, tools=None, tool_choice=None):
        self.saw = messages[-1]["content"]
        # The later of the two curl instructions wins.
        last = "use curl" if self.saw.rfind("use curl") > self.saw.rfind(
            "never use curl"
        ) else "never use curl"
        rule = ("- curl IS allowed for this project." if last == "use curl"
                else "- Never use curl.")
        return ChatResult(content=f"# Directives\n\n{rule}\n")


class FixedBackend:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, tools=None, tool_choice=None):
        return ChatResult(content=self.text)


def _seed_contradiction(project):
    project.append_history(8, "never use curl, it's banned here")
    project.append_history(12, "add pagination to /list")
    project.append_history(30, "actually, use curl for this one download")


def test_reconcile_resolves_conflict_by_recency(project, cfg):
    _seed_contradiction(project)
    backend = RecencyBackend()
    text = directives_mod.reconcile(project, backend, cfg)
    # the pass was handed the whole history, not a truncated tail
    assert "never use curl" in backend.saw and "use curl for this" in backend.saw
    assert "curl IS allowed" in text
    assert project.read_directives().strip().endswith("curl IS allowed for this project.")


def test_reconcile_noop_without_history(project, cfg):
    assert directives_mod.reconcile(project, FixedBackend("x"), cfg) is None
    assert not project.directives_path.exists()


def test_reconcile_keeps_old_file_on_empty_output(project, cfg):
    project.append_history(1, "always prefer python")
    project.write_directives("- keep this")
    assert directives_mod.reconcile(project, FixedBackend("   "), cfg) is None
    assert "keep this" in project.read_directives()


def test_package_swaps_full_log_for_directives_plus_lastk(project, cfg):
    cfg.set("directives_enabled", True)
    cfg.set("directives_recent_k", 2)
    for i in range(1, 21):
        project.append_history(i, f"prompt number {i} body")
    project.write_directives("- The distilled standing rule.")
    user = package.assemble(project, "go", {}, cfg)[1]["content"]
    assert "# DIRECTIVES" in user
    assert "The distilled standing rule." in user
    # only the last K raw prompts ride along; the old ones are gone from the package
    assert "prompt number 20 body" in user
    assert "prompt number 19 body" in user
    assert "prompt number 1 body" not in user
    assert "prompt number 5 body" not in user


def test_package_unchanged_when_directives_off(project, cfg):
    # Default off -> full recent log, no DIRECTIVES section.
    for i in range(1, 6):
        project.append_history(i, f"prompt {i}")
    user = package.assemble(project, "go", {}, cfg)[1]["content"]
    assert "# DIRECTIVES" not in user
    assert "# PROMPT HISTORY" in user
    assert "prompt 1" in user  # full log still present


def test_header_recency_line_on_by_default(project, cfg):
    system = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "the more recent one wins" in system
    assert "directives.md" in system


def test_header_recency_line_can_be_disabled(project, cfg):
    cfg.set("directive_header_rule", False)
    system = package.assemble(project, "x", {}, cfg)[0]["content"]
    assert "the more recent one wins" not in system


def test_maybe_reconcile_migrates_old_project(project, cfg):
    # An existing project with history but no directives.md -> reconcile on the
    # next run, zero manual steps.
    _seed_contradiction(project)
    assert not project.directives_path.exists()
    ran = directives_mod.maybe_reconcile(project, RecencyBackend(), cfg, run_id=2)
    assert ran
    assert project.directives_path.exists()


def test_maybe_reconcile_periodic_only_every_n(project, cfg):
    project.append_history(1, "prefer python")
    project.write_directives("- prefer python")  # already migrated
    cfg.set("reconcile_every_runs", 10)
    # run 3: not due
    assert not directives_mod.maybe_reconcile(project, FixedBackend("- x"), cfg, run_id=3)
    # run 10: due
    assert directives_mod.maybe_reconcile(project, FixedBackend("- x"), cfg, run_id=10)


def test_maybe_reconcile_noop_on_empty_project(project, cfg):
    assert not directives_mod.maybe_reconcile(project, FixedBackend("x"), cfg, run_id=1)


def test_agent_run_reconciles_then_packages_directives(project, cfg):
    # End-to-end: an old project with a curl contradiction, directives enabled.
    # The run reconciles first (migration), then the finish proceeds normally.
    from hermes import agent
    from hermes.llm import MockBackend
    cfg.set("directives_enabled", True)
    cfg.set("plan_build_tasks", False)
    _seed_contradiction(project)
    script = [
        # 1) the reconcile side-call (plain, no tools)
        {"text": "# Directives\n\n- curl IS allowed for this project."},
        # 2) the run's single working turn
        {"tool": "finish_run", "args": {"summary": "done"}},
    ]
    result = agent.run(project, "do it", cfg, MockBackend(script),
                       gpu=None, env={}, confirm_fn=lambda *a, **k: True)
    assert not result.aborted
    assert "curl IS allowed" in project.read_directives()
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "directives"' in transcript
