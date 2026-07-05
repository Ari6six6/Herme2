"""Feature 6: checkpointing."""

from hermes import agent, checkpoint
from hermes.llm import MockBackend


def test_create_list_restore_round_trip(project):
    (project.workspace_dir / "a.txt").write_text("original")
    cid = checkpoint.create(project, label="before edit")
    assert cid
    metas = checkpoint.list_checkpoints(project)
    assert [m["id"] for m in metas] == [cid]
    assert metas[0]["label"] == "before edit"

    # mutate + add a new file, then revert
    (project.workspace_dir / "a.txt").write_text("CHANGED")
    (project.workspace_dir / "new.txt").write_text("added later")
    assert checkpoint.restore(project, cid)
    assert (project.workspace_dir / "a.txt").read_text() == "original"
    assert not (project.workspace_dir / "new.txt").exists()  # created-after is removed


def test_restore_unknown_id_returns_false(project):
    assert not checkpoint.restore(project, "nope")


def test_excludes_runs_and_twin(project):
    project.new_run()  # creates runs/0001
    project.twin_dir.mkdir(exist_ok=True)
    (project.twin_dir / "big.bin").write_text("x" * 1000)
    cid = checkpoint.create(project)
    snap = project.root / checkpoint.CHECKPOINT_DIRNAME / cid
    assert not (snap / "runs").exists()
    assert not (snap / "twin").exists()
    assert (snap / "workspace").exists()


def test_prune_keeps_only_max(project):
    for i in range(5):
        checkpoint.create(project, label=f"c{i}", max_keep=3)
    assert len(checkpoint.list_checkpoints(project)) == 3


# ---- integration through the agent loop --------------------------------------
def test_run_checkpoints_before_mutation_and_revert_undoes_it(project, cfg):
    cfg.set("plan_build_tasks", False)
    result = agent.run(
        project, "write a file", cfg,
        MockBackend([
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hello"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )
    assert not result.aborted
    assert (project.workspace_dir / "out.txt").read_text() == "hello"
    cps = checkpoint.list_checkpoints(project)
    assert len(cps) == 1
    # the snapshot was taken BEFORE the write -> its workspace has no out.txt
    snap = project.root / checkpoint.CHECKPOINT_DIRNAME / cps[0]["id"]
    assert not (snap / "workspace" / "out.txt").exists()
    # reverting therefore undoes the run's file mutation
    checkpoint.restore(project, cps[0]["id"])
    assert not (project.workspace_dir / "out.txt").exists()
    transcript = (project.runs_dir / "0001" / "transcript.jsonl").read_text()
    assert '"role": "checkpoint"' in transcript


def test_checkpoint_once_per_turn_not_per_call(project, cfg):
    cfg.set("plan_build_tasks", False)
    # two mutating calls in ONE turn -> a single checkpoint
    agent.run(
        project, "write two", cfg,
        MockBackend([
            {"tools": [
                {"tool": "write_file", "args": {"path": "workspace/a.txt", "content": "1"}},
                {"tool": "write_file", "args": {"path": "workspace/b.txt", "content": "2"}},
            ]},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )
    assert len(checkpoint.list_checkpoints(project)) == 1


def test_checkpointing_off_takes_no_snapshots(project, cfg):
    cfg.set("checkpointing", False)
    cfg.set("plan_build_tasks", False)
    agent.run(
        project, "write a file", cfg,
        MockBackend([
            {"tool": "write_file",
             "args": {"path": "workspace/out.txt", "content": "hi"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )
    assert checkpoint.list_checkpoints(project) == []


def test_non_mutating_run_takes_no_snapshot(project, cfg):
    cfg.set("plan_build_tasks", False)
    agent.run(
        project, "just a note", cfg,
        MockBackend([
            {"tool": "write_note", "args": {"text": "looked fine"}},
            {"tool": "finish_run", "args": {"summary": "done"}},
        ]),
        gpu=None, env={}, confirm_fn=lambda *a, **k: True,
    )
    assert checkpoint.list_checkpoints(project) == []
