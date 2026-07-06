from hermes import cli


def test_remote_server_alive_true_when_pid_running():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "RUNNING\n", "")])
    assert cli._remote_server_alive(ep) is True


def test_remote_server_alive_false_when_no_pid():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(1, "", "")])
    assert cli._remote_server_alive(ep) is False


def test_remote_server_alive_false_without_endpoint():
    assert cli._remote_server_alive(None) is False


def test_vllm_down_hint_never_attached():
    assert "gpu attach" in cli._vllm_down_hint(None)


def test_vllm_down_hint_no_server_launched():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(1, "", "")])
    assert "gpu serve" in cli._vllm_down_hint(ep)


def test_vllm_down_hint_still_warming_up():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "RUNNING\n", "")])
    hint = cli._vllm_down_hint(ep)
    assert "loading" in hint or "warm" in hint
    assert "gpu serve" not in hint
