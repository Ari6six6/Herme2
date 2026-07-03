"""The Vast.ai client: pause/resume (stop/start) and single-instance lookup,
exercised over httpx.MockTransport so no network is touched."""

import json

import httpx
import pytest

from hermes.gpu import vast


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url=vast.API_BASE)


def test_start_and_stop_set_the_right_state(monkeypatch):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"success": True})

    monkeypatch.setattr(vast, "_client", lambda api_key: _mock_client(handler))

    vast.start_instance("key", 42)
    assert seen["method"] == "PUT"
    assert seen["url"].endswith("/instances/42/")
    assert seen["body"] == {"state": "running"}

    vast.stop_instance("key", 42)
    assert seen["body"] == {"state": "stopped"}


def test_get_instance_finds_by_id_and_misses(monkeypatch):
    def handler(request):
        return httpx.Response(200, json={"instances": [
            {"id": 7, "actual_status": "running", "ssh_host": "ssh9.vast.ai",
             "ssh_port": 12345, "gpu_name": "RTX 6000", "num_gpus": 1,
             "dph_total": 0.5},
        ]})

    monkeypatch.setattr(vast, "_client", lambda api_key: _mock_client(handler))

    inst = vast.get_instance("key", 7)
    assert inst is not None
    assert inst["status"] == "running"
    assert inst["ssh_host"] == "ssh9.vast.ai"
    assert inst["ssh_port"] == 12345
    # an id that isn't in the list resolves to None (paused/destroyed)
    assert vast.get_instance("key", 999) is None


def test_state_change_wraps_http_errors(monkeypatch):
    def handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(vast, "_client", lambda api_key: _mock_client(handler))
    with pytest.raises(vast.VastError):
        vast.start_instance("key", 1)


def test_empty_api_key_raises_before_any_request():
    with pytest.raises(vast.VastError):
        vast.list_instances("")
