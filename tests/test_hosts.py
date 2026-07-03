"""Managed hosts: registry persistence and the reads-free/writes-confirmed gate."""

import stat

from conftest import FakeEndpoint

from hermes import hosts as hosts_mod
from hermes.tools.base import ToolContext
from hermes.tools.hosts import host_read, host_shell, host_write

WEB = {"host": "1.2.3.4", "port": 22, "user": "root", "note": "primary web"}


def _ctx(project, cfg, confirm, ep):
    return ToolContext(project=project, cfg=cfg, confirm=confirm, hosts={"web": ep})


# -- registry ---------------------------------------------------------------
def test_roundtrip_and_permissions(home):
    hosts_mod.save_hosts({"web": WEB})
    assert hosts_mod.load_hosts() == {"web": WEB}
    mode = stat.S_IMODE(hosts_mod.hosts_path().stat().st_mode)
    assert mode == 0o600


def test_env_line(home):
    assert hosts_mod.hosts_env_line({}) == "none"
    line = hosts_mod.hosts_env_line({"web": WEB})
    assert line == "web=root@1.2.3.4:22 (primary web)"


def test_endpoint_from_record(home):
    ep = hosts_mod.host_endpoint(WEB)
    assert (ep.host, ep.port, ep.user) == ("1.2.3.4", 22, "root")


# -- host_shell gate ---------------------------------------------------------
def test_read_only_command_never_confirms(project, cfg, never):
    ep = FakeEndpoint([(0, "nginx running", "")])
    out = host_shell.fn(
        {"host": "web", "command": "systemctl status nginx"},
        _ctx(project, cfg, never, ep),
    )
    assert "nginx running" in out
    assert ep.calls == ["systemctl status nginx"]


def test_mutating_command_confirmed_runs(project, cfg, yes):
    ep = FakeEndpoint([(0, "restarted", "")])
    out = host_shell.fn(
        {"host": "web", "command": "systemctl restart nginx"},
        _ctx(project, cfg, yes, ep),
    )
    assert "restarted" in out


def test_mutating_command_denied(project, cfg, no):
    ep = FakeEndpoint()
    out = host_shell.fn(
        {"host": "web", "command": "rm -rf /var/www"},
        _ctx(project, cfg, no, ep),
    )
    assert out.startswith("DENIED")
    assert ep.calls == []  # never reached the server


def test_cwd_is_quoted(project, cfg, never):
    ep = FakeEndpoint()
    host_shell.fn(
        {"host": "web", "command": "ls", "cwd": "/srv/$(boom)"},
        _ctx(project, cfg, never, ep),
    )
    assert "cd '/srv/$(boom)' && (ls)" == ep.calls[0]


# -- host_read / host_write --------------------------------------------------
def test_read_free_write_always_confirms(project, cfg, never, no):
    ep = FakeEndpoint([(0, "contents", "")])
    out = host_read.fn({"host": "web", "path": "/etc/nginx/nginx.conf"},
                       _ctx(project, cfg, never, ep))
    assert out == "contents"

    ep2 = FakeEndpoint()
    out = host_write.fn({"host": "web", "path": "/etc/x", "content": "data"},
                        _ctx(project, cfg, no, ep2))
    assert out.startswith("DENIED")
    assert ep2.writes == []


def test_write_confirmed_writes(project, cfg, yes):
    ep = FakeEndpoint([(0, "", "")])
    out = host_write.fn({"host": "web", "path": "/etc/x", "content": "data"},
                        _ctx(project, cfg, yes, ep))
    assert "wrote 4 chars" in out
    assert ep.writes == [("/etc/x", "data")]


def test_unknown_host_lists_known(project, cfg, never):
    out = host_shell.fn({"host": "db", "command": "ls"},
                        _ctx(project, cfg, never, FakeEndpoint()))
    assert out.startswith("ERROR: no managed host 'db'")
    assert "web" in out
