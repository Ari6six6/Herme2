import json

from hermes.tools.base import ToolContext
from hermes.tools.web import http_request


class FakeResp:
    status_code = 200
    headers = {"content-type": "text/plain"}
    url = "https://trusted.example/submit"
    text = "ok"


def test_post_to_allowlisted_domain_skips_confirm(project, cfg, monkeypatch, never):
    import hermes.tools.web as web
    monkeypatch.setattr(web.httpx, "request", lambda *a, **k: FakeResp())
    cfg.set("http_allow", [{"domain": "trusted.example", "methods": ["POST"]}])
    ctx = ToolContext(project=project, cfg=cfg, confirm=never)

    out = http_request.fn({"url": "https://trusted.example/submit", "method": "POST",
                        "body": "x"}, ctx)
    assert out.startswith("HTTP 200")


def test_post_to_unlisted_domain_still_confirms(project, cfg, monkeypatch):
    import hermes.tools.web as web
    monkeypatch.setattr(web.httpx, "request", lambda *a, **k: FakeResp())
    ctx = ToolContext(project=project, cfg=cfg, confirm=lambda *a, **k: False)

    out = http_request.fn({"url": "https://trusted.example/submit", "method": "POST",
                        "body": "x"}, ctx)
    assert out == "DENIED by operator."
