Version the workspace with git: init, stage, commit, inspect history and diffs.

Use the `git_ops` toolbox tool for version control inside the project. It is
LOCAL only — there is no clone/fetch/pull/push here (a network git tool is a
separate, tainting tool). Everything runs in the project workspace.

Procedure:
1. Equip it once per project: equip_tool("git_ops"). It's callable next turn.
2. In a fresh workspace, initialise first: git_ops(operation="init").
3. Typical loop (init/add/commit ask the operator; status/log/diff/branch are free):
   - git_ops(operation="status")                    # short view of what changed
   - git_ops(operation="add")                        # stage all (-A); pass path= to scope
   - git_ops(operation="commit", message="what/why") # message is REQUIRED
   - git_ops(operation="log")                         # last 20 commits, oneline
   - git_ops(operation="diff")                        # working-tree changes; path= to scope

Gotchas learned the hard way:
- commit with an empty/whitespace message returns an ERROR — always pass a real message.
- Commits carry an inline hermes identity, so a fresh box commits with no
  `git config` step. Don't try to set user.name/user.email yourself.
- Network verbs (clone/fetch/pull/push/remote) come back as "unknown operation" —
  that's deliberate, not a bug.
- To work on a repo in a subdirectory, pass repo="workspace/sub"; it must stay
  inside the project or you get DENIED.
