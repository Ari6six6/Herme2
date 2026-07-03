"""Fail-closed read-only classifier for commands on managed hosts.

Opposite polarity to the GPU sandbox's deny-list: a command runs without
confirmation ONLY if every part of it is positively recognized as read-only.
Anything the parser cannot vouch for — unknown binaries, redirections,
command substitution, unparseable quoting — goes to the operator.

No allowlist over arbitrary shell is airtight; this one trades a little
convenience (e.g. `git -C dir log` asks) for never guessing.
"""

from __future__ import annotations

import shlex

_SIMPLE: dict = {}

ALLOWLIST: dict[str, dict] = {
    # plain inspection
    "cat": _SIMPLE, "head": _SIMPLE, "tail": _SIMPLE, "wc": _SIMPLE,
    "stat": _SIMPLE, "file": _SIMPLE, "ls": _SIMPLE, "pwd": _SIMPLE,
    "date": _SIMPLE, "grep": _SIMPLE, "echo": _SIMPLE, "sort": _SIMPLE,
    "uniq": _SIMPLE, "cut": _SIMPLE, "tr": _SIMPLE, "diff": _SIMPLE,
    "which": _SIMPLE, "type": _SIMPLE, "realpath": _SIMPLE,
    "readlink": _SIMPLE, "dirname": _SIMPLE, "basename": _SIMPLE,
    "md5sum": _SIMPLE, "sha256sum": _SIMPLE, "printenv": _SIMPLE,
    # system state
    "df": _SIMPLE, "du": _SIMPLE, "free": _SIMPLE, "uname": _SIMPLE,
    "whoami": _SIMPLE, "id": _SIMPLE, "hostname": _SIMPLE, "uptime": _SIMPLE,
    "ps": _SIMPLE, "ss": _SIMPLE, "netstat": _SIMPLE, "lsblk": _SIMPLE,
    "lscpu": _SIMPLE, "lsof": _SIMPLE, "who": _SIMPLE, "w": _SIMPLE,
    "last": _SIMPLE, "nproc": _SIMPLE,
    "env": {"zero_args": True},  # `env CMD` executes CMD
    "top": {"require_batch_flag": True},  # interactive top is useless here
    "dmesg": {"deny_args": {"-C", "-c", "--clear", "--console-off",
                            "--console-on", "--console-level", "-n", "-E", "-D"}},
    "find": {"deny_args": {"-delete", "-exec", "-execdir", "-ok", "-okdir",
                           "-fprint", "-fprintf", "-fls", "-fprint0"}},
    "rg": {"deny_args": {"--pre", "--pre-glob"}},
    "journalctl": {"deny_args": {"--vacuum-size", "--vacuum-time",
                                 "--vacuum-files", "--rotate", "--flush",
                                 "--relinquish-var", "--setup-keys", "--sync"}},
    "systemctl": {"subcommands": {"status", "is-active", "is-enabled",
                                  "is-failed", "is-system-running",
                                  "list-units", "list-unit-files",
                                  "list-timers", "list-sockets",
                                  "list-dependencies", "show", "cat"}},
    "git": {"subcommands": {"status", "log", "diff", "show", "rev-parse",
                            "describe", "shortlog", "blame", "ls-files",
                            "ls-remote", "reflog", "branch"},
            "deny_args": {"--output", "-o", "--exec-path"}},  # these write files
    "docker": {"subcommands": {"ps", "logs", "inspect", "images", "version",
                               "info", "top", "stats", "events", "port",
                               "diff", "history"}},
    "ip": {"subcommands": {"addr", "address", "a", "route", "r", "link", "l",
                           "neigh", "n", "maddr", "rule", "tunnel"},
           "deny_args": {"set", "add", "del", "delete", "flush", "change",
                         "replace", "append"}},
    # bare `nginx` starts the daemon; only config-check/version flags are safe
    "nginx": {"require_args": {"-t", "-T", "-v", "-V"}, "deny_args": {"-s"}},
    "crontab": {"require_args": {"-l"}},
}

# `git branch x` creates a branch; only flag-style invocations are read-only.
_GIT_SUBCOMMANDS_NO_POSITIONAL = {"branch"}


def classify(command: str) -> tuple[bool, str]:
    """(read_only, reason). Fails closed: any doubt means (False, why)."""
    segments, reason = _split_segments(command)
    if segments is None:
        return False, reason
    if not segments:
        return False, "empty command"
    for seg in segments:
        ok, reason = _classify_segment(seg)
        if not ok:
            return False, reason
    return True, "read-only"


def is_read_only(command: str) -> bool:
    return classify(command)[0]


def _split_segments(command: str):
    """Quote-aware split on ; | & && || and newlines. Returns (segments, None)
    or (None, reject_reason) when the command contains constructs that can
    write or execute regardless of the leading word."""
    segs: list[str] = []
    cur: list[str] = []
    in_sq = in_dq = False
    i, n = 0, len(command)
    while i < n:
        c = command[i]
        if in_sq:
            if c == "'":
                in_sq = False
            cur.append(c)
            i += 1
            continue
        if c == "\\":  # escapes the next char outside single quotes
            cur.append(c)
            if i + 1 < n:
                cur.append(command[i + 1])
                i += 2
            else:
                i += 1
            continue
        if c == "'" and not in_dq:
            in_sq = True
            cur.append(c)
            i += 1
            continue
        if c == '"':
            in_dq = not in_dq
            cur.append(c)
            i += 1
            continue
        if c == "`":
            return None, "backtick command substitution"
        if c == "$" and i + 1 < n and command[i + 1] == "(":
            return None, "$(...) command substitution"
        if c == "<" and i + 1 < n and command[i + 1] == "(":
            return None, "<(...) process substitution"
        if c == ">":
            return None, "output redirection writes to the server"
        if c in ";|&\n":
            seg = "".join(cur).strip()
            if seg:
                segs.append(seg)
            cur = []
            if c in "|&" and i + 1 < n and command[i + 1] == c:
                i += 1  # && or ||
            i += 1
            continue
        cur.append(c)
        i += 1
    if in_sq or in_dq:
        return None, "unterminated quote"
    seg = "".join(cur).strip()
    if seg:
        segs.append(seg)
    return segs, None


def _classify_segment(seg: str) -> tuple[bool, str]:
    try:
        tokens = shlex.split(seg)
    except ValueError as e:
        return False, f"unparseable shell: {e}"
    if not tokens:
        return False, "empty segment"
    cmd = tokens[0]
    rule = ALLOWLIST.get(cmd)
    if rule is None:
        return False, f"'{cmd}' is not in the read-only allowlist"
    args = tokens[1:]

    for tok in args:
        base = tok.split("=", 1)[0]
        if tok in rule.get("deny_args", ()) or base in rule.get("deny_args", ()):
            return False, f"'{cmd} {tok}' can modify state"

    if rule.get("zero_args") and args:
        return False, f"'{cmd}' with arguments can execute commands"

    subs = rule.get("subcommands")
    if subs is not None:
        sub = next((t for t in args if not t.startswith("-")), None)
        if sub not in subs:
            return False, f"'{cmd} {sub or '(none)'}' is not a read-only subcommand"
        if cmd == "git" and sub in _GIT_SUBCOMMANDS_NO_POSITIONAL:
            positional = [t for t in args if not t.startswith("-")]
            if len(positional) > 1:
                return False, f"'git {sub} <name>' creates/changes things"

    if rule.get("require_batch_flag"):
        flags = [t for t in args if t.startswith("-") and not t.startswith("--")]
        if not any("b" in f for f in flags):
            return False, f"'{cmd}' without -b is interactive"

    req = rule.get("require_args")
    if req is not None and not (set(args) & req):
        return False, f"'{cmd}' is only read-only with {'/'.join(sorted(req))}"

    return True, "read-only"
