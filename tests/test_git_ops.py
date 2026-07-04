"""git_ops: local git inside the project workspace, no network verbs."""

from hermes.toolbox import git_ops
from hermes.tools.base import ToolContext


def _ctx(project, cfg, confirm=None):
    return ToolContext(project=project, cfg=cfg,
                       confirm=confirm or (lambda *a, **k: True))


def _init_repo(project, cfg):
    return git_ops.run({"operation": "init"}, _ctx(project, cfg))


def test_init_status_add_commit_cycle(project, cfg):
    assert _init_repo(project, cfg).startswith("git init ok")
    (project.workspace_dir / "a.txt").write_text("hello")

    status = git_ops.run({"operation": "status"}, _ctx(project, cfg))
    assert "a.txt" in status  # untracked file shows up

    assert git_ops.run({"operation": "add"}, _ctx(project, cfg)).startswith("git add ok")
    out = git_ops.run(
        {"operation": "commit", "message": "add a.txt"}, _ctx(project, cfg)
    )
    assert out.startswith("git commit ok")

    log = git_ops.run({"operation": "log"}, _ctx(project, cfg))
    assert "add a.txt" in log


def test_commit_needs_message(project, cfg):
    _init_repo(project, cfg)
    out = git_ops.run({"operation": "commit", "message": "   "}, _ctx(project, cfg))
    assert out.startswith("ERROR: commit needs a non-empty")


def test_unknown_operation_rejected(project, cfg):
    out = git_ops.run({"operation": "push"}, _ctx(project, cfg))
    assert out.startswith("ERROR: unknown operation")
    assert "push" in out


def test_network_verbs_are_not_in_the_allowlist(project, cfg):
    # The whole point of this tool being local: no clone/fetch/pull/push/remote.
    for verb in ("clone", "fetch", "pull", "push", "remote"):
        assert verb not in (git_ops.READ_OPS | git_ops.WRITE_OPS)
        assert git_ops.run({"operation": verb}, _ctx(project, cfg)).startswith(
            "ERROR: unknown operation"
        )


def test_mutations_gate_through_confirm(project, cfg):
    _init_repo(project, cfg)
    (project.workspace_dir / "b.txt").write_text("x")
    seen = []

    def confirm(msg, detail="", **k):
        seen.append((msg, detail))
        return False

    out = git_ops.run({"operation": "add"}, _ctx(project, cfg, confirm))
    assert out == "DENIED by operator."
    assert seen and "changes the repo" in seen[0][0]
    assert "$ git add -A" in seen[0][1]


def test_reads_do_not_confirm(project, cfg, never):
    _init_repo(project, cfg)
    # `never` raises if confirm is called; reads must not touch it.
    for op in ("status", "log", "diff", "branch"):
        out = git_ops.run({"operation": op}, _ctx(project, cfg, never))
        assert not out.startswith("DENIED")


def test_repo_path_escape_denied(project, cfg):
    out = git_ops.run(
        {"operation": "status", "repo": "../../etc"}, _ctx(project, cfg)
    )
    assert out.startswith("DENIED")


def test_add_path_escape_denied(project, cfg):
    _init_repo(project, cfg)
    out = git_ops.run(
        {"operation": "add", "path": "../../../etc/passwd"}, _ctx(project, cfg)
    )
    assert out.startswith("DENIED")


def test_commit_confirm_shows_message(project, cfg):
    _init_repo(project, cfg)
    (project.workspace_dir / "c.txt").write_text("y")
    git_ops.run({"operation": "add"}, _ctx(project, cfg))
    seen = []

    def confirm(msg, detail="", **k):
        seen.append(detail)
        return True

    out = git_ops.run(
        {"operation": "commit", "message": "ship it"}, _ctx(project, cfg, confirm)
    )
    assert out.startswith("git commit ok")
    assert 'git commit -m ship it' in seen[0]


def test_diff_shows_working_changes(project, cfg):
    _init_repo(project, cfg)
    f = project.workspace_dir / "d.txt"
    f.write_text("one\n")
    git_ops.run({"operation": "add"}, _ctx(project, cfg))
    git_ops.run({"operation": "commit", "message": "first"}, _ctx(project, cfg))
    f.write_text("two\n")
    out = git_ops.run({"operation": "diff"}, _ctx(project, cfg))
    assert "-one" in out and "+two" in out


def test_status_on_non_repo_reports_error(project, cfg):
    # No init: git status in a plain dir is a clean, informative error string.
    out = git_ops.run({"operation": "status"}, _ctx(project, cfg))
    assert out.startswith("ERROR: git status failed")
