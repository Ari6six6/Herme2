"""Toolbox: run git inside the project workspace (local only, no network).

Wraps the `git` CLI so the agent can version its own work — init a repo,
stage, commit, and inspect history/diffs on the files it produces. This is a
LOCAL tool by design: no `clone`, `fetch`, `pull`, `push`, or `remote` — every
network-crossing git verb is refused here so nothing sneaks past the taint rail
(a network git tool is a separate, tainting tool).

Safety shape:
  * subcommands are an allowlist; git is invoked as an argv list (shell=False),
    so there is no shell to inject into;
  * reads (status/log/diff/branch) run free — they only inspect the workspace,
    like read_file/list_files;
  * mutations (init/add/commit) gate through ctx.confirm, so the operator sees
    the exact git command before it runs;
  * the repo dir is resolved inside the project (resolve_in), so an operation
    can't reach a repo outside the project;
  * commits carry an inline identity (-c user.name/email) so a fresh box can
    commit without the agent mutating global git config.

If `git` isn't installed the tool returns a clear ERROR telling the operator
what to install, rather than crashing the run.
"""

READ_OPS = {"status", "log", "diff", "branch"}
WRITE_OPS = {"init", "add", "commit"}

TOOL = {
    "name": "git_ops",
    "description": (
        "Run git inside the project workspace (LOCAL only — no clone/fetch/pull/"
        "push). operation: status|log|diff|branch (free) or init|add|commit "
        "(operator-confirmed). Use `path` to scope add/diff, `message` for "
        "commit, `repo` for a git dir under the project (default: workspace)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": sorted(READ_OPS | WRITE_OPS),
            },
            "path": {
                "type": "string",
                "description": "file/dir to scope add or diff (relative to repo); "
                               "add uses '-A' when omitted",
            },
            "message": {"type": "string", "description": "commit message (required for commit)"},
            "repo": {
                "type": "string",
                "description": "git dir relative to the project root; defaults to workspace/",
            },
        },
        "required": ["operation"],
    },
}


def run(args, ctx):
    import subprocess

    from hermes.paths import PathDenied, resolve_in

    op = str(args.get("operation", "")).strip()
    if op not in READ_OPS and op not in WRITE_OPS:
        allowed = ", ".join(sorted(READ_OPS | WRITE_OPS))
        return f"ERROR: unknown operation '{op}'. Allowed: {allowed}."

    # Resolve the repo dir inside the project (default: the workspace).
    if args.get("repo"):
        try:
            repo_dir = resolve_in(ctx.project.root, args["repo"])
        except PathDenied:
            return "DENIED: repo must stay inside the project directory."
    else:
        repo_dir = ctx.project.workspace_dir
    if not repo_dir.is_dir():
        return f"ERROR: no such directory: {args.get('repo', 'workspace')}"

    # Build the git argv (list form — no shell, nothing to inject into).
    argv = ["git"]
    if op == "commit":
        # Inline identity so a fresh box commits without touching global config.
        argv += ["-c", "user.name=hermes", "-c", "user.email=hermes@localhost",
                 "-c", "commit.gpgsign=false"]
    argv.append("commit" if op == "commit" else op)

    if op == "log":
        argv += ["--oneline", "-n", "20"]
    elif op == "status":
        argv.append("--short")
    elif op == "branch":
        argv += ["--no-color", "-vv"]
    elif op == "commit":
        msg = str(args.get("message", "")).strip()
        if not msg:
            return "ERROR: commit needs a non-empty `message`."
        argv += ["-m", msg]
    elif op == "add":
        if args.get("path"):
            try:
                resolve_in(repo_dir, args["path"])
            except PathDenied:
                return "DENIED: path must stay inside the repo."
            argv.append(args["path"])
        else:
            argv.append("-A")
    elif op == "diff":
        if args.get("path"):
            try:
                resolve_in(repo_dir, args["path"])
            except PathDenied:
                return "DENIED: path must stay inside the repo."
            argv += ["--", args["path"]]

    if op in WRITE_OPS:
        shown = " ".join(argv[argv.index(op if op != "commit" else "commit"):])
        if not ctx.confirm(
            "agent wants to run a git command that changes the repo:",
            detail=f"  $ git {shown}\n  (repo: {repo_dir})",
        ):
            return "DENIED by operator."

    try:
        proc = subprocess.run(
            argv, cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return ("ERROR: git is not installed. Ask the operator to install it "
                "(Termux: `pkg install git`; Debian/Ubuntu: `apt install git`).")
    except subprocess.TimeoutExpired:
        return "ERROR: git command timed out after 60s"

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        detail = err or out or "(no output)"
        return f"ERROR: git {op} failed (exit {proc.returncode}): {detail[-600:]}"
    body = out or err or "(no output)"
    return f"git {op} ok:\n{body}"
