import pytest

import hermes.gpu.provision as provision
from hermes.gpu.provision import (
    LLAMA_BIN,
    MODEL_MAX_LEN,
    VENV_DIR,
    VLLM_BIN,
    ProvisionError,
    launch,
    llama_command,
    plan_serve,
    vllm_command,
    wait_ready,
)
from hermes.gpu.ssh import SSHEndpoint, SSHError, parse_ssh_string
from hermes.models import get_spec


def test_tier_h200(cfg):
    plan = plan_serve([("NVIDIA H200", 143771)], cfg)
    assert plan.tensor_parallel == 1
    assert plan.max_model_len == 196608
    assert plan.gpu_memory_utilization == 0.92


def test_tier_two_rtx6000pro(cfg):
    plan = plan_serve([("RTX 6000 Pro", 97887), ("RTX 6000 Pro", 97887)], cfg)
    assert plan.tensor_parallel == 2
    assert plan.max_model_len == 262144


def test_tier_single_48gb_is_tight(cfg):
    plan = plan_serve([("RTX 6000 Ada", 49140)], cfg)
    assert plan.max_model_len == 16384
    assert plan.gpu_memory_utilization == 0.95
    assert any("tight" in n for n in plan.notes)


def test_tier_96gb(cfg):
    plan = plan_serve([("RTX 6000 Pro", 97887)], cfg)
    assert plan.max_model_len == 65536


def test_too_small_rejected(cfg):
    with pytest.raises(ProvisionError):
        plan_serve([("RTX 4090", 24564)], cfg)


def test_ampere_note(cfg):
    plan = plan_serve([("NVIDIA A100-SXM4-80GB", 81920)], cfg)
    assert any("Ampere" in n for n in plan.notes)


def test_override_capped_at_model_max(cfg):
    cfg.set("max_model_len", 999999)
    plan = plan_serve([("NVIDIA H200", 143771)], cfg)
    assert plan.max_model_len == MODEL_MAX_LEN


def test_vllm_command(cfg):
    plan = plan_serve([("NVIDIA H200", 143771)], cfg)
    cmd = vllm_command(cfg, plan)
    assert "--tool-call-parser hermes" in cmd
    assert "--enable-auto-tool-choice" in cmd
    assert "--quantization fp8" in cmd
    assert "NousResearch/Hermes-4.3-36B" in cmd
    assert "--tensor-parallel-size 1" in cmd


def test_vllm_command_uses_venv_binary(cfg):
    plan = plan_serve([("NVIDIA H200", 143771)], cfg)
    # vLLM must be invoked from its isolated venv, not the system PATH.
    assert vllm_command(cfg, plan).startswith(f"{VLLM_BIN} serve ")


def test_launch_installs_into_isolated_venv(cfg):
    from conftest import FakeEndpoint

    ep = FakeEndpoint([
        (0, "", ""),  # running-check: not running
        (0, "", ""),  # install
        (0, "", ""),  # mkdir workspace
        (0, "", ""),  # launch
    ])
    launch(ep, cfg, plan_serve([("NVIDIA H200", 143771)], cfg))

    install = ep.calls[1]
    # Never install into the system Python — that's what hits the apt/RECORD
    # uninstall failure. Everything goes through the venv.
    assert f"python3 -m venv --system-site-packages {VENV_DIR}" in install
    assert f"{VENV_DIR}/bin/pip install" in install
    assert "pip install -q -U vllm" not in install  # no bare system install


def test_launch_skips_when_already_running(cfg):
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "RUNNING", "")])
    launch(ep, cfg, plan_serve([("NVIDIA H200", 143771)], cfg))
    assert len(ep.calls) == 1  # bailed before installing


def test_launch_raises_on_install_failure(cfg):
    from conftest import FakeEndpoint

    ep = FakeEndpoint([
        (0, "", ""),  # not running
        (1, "", "Cannot uninstall PyJWT 2.7.0, RECORD file not found."),
    ])
    with pytest.raises(ProvisionError, match="vLLM install failed"):
        launch(ep, cfg, plan_serve([("NVIDIA H200", 143771)], cfg))


def test_qwen_fits_smaller_box(cfg):
    # 24GB card is below Hermes' 44GB floor but enough for the Q5 GGUF.
    from hermes.models import get_spec

    spec = get_spec("qwen")
    plan = plan_serve([("RTX 4090", 24564)], cfg, spec)
    assert plan.max_model_len == 16384


def test_qwen_serves_on_native_llama_cpp(cfg):
    from hermes.models import get_spec

    spec = get_spec("qwen")
    assert spec.server == "llama_cpp"  # native GGUF runtime, not vLLM
    plan = plan_serve([("RTX 4090", 24564)], cfg, spec)
    cmd = llama_command(cfg, plan, spec)
    assert cmd.startswith(f"{LLAMA_BIN} ")
    assert "--hf-repo HauhauCS/Qwen3.6-27B-Uncensored-HauhauCS-Balanced" in cmd
    assert f"--hf-file {spec.gguf_file}" in cmd
    assert "--jinja" in cmd  # OpenAI tool calls from the model's own chat template
    assert "--alias qwen3.6-27b" in cmd
    assert "--n-gpu-layers" in cmd


def test_launch_llama_builds_with_cuda_then_serves(cfg):
    from conftest import FakeEndpoint
    from hermes.models import get_spec

    spec = get_spec("qwen")
    ep = FakeEndpoint([
        (0, "", ""),  # not running
        (0, "", ""),  # build llama.cpp
        (0, "", ""),  # mkdir workspace
        (0, "", ""),  # launch
    ])
    launch(ep, cfg, plan_serve([("RTX 4090", 24564)], cfg, spec), spec)

    build = ep.calls[1]
    assert "llama.cpp" in build and "GGML_CUDA=ON" in build
    assert VENV_DIR not in build  # the native build, not the vLLM venv
    # Launched the native server with tool-calling on.
    assert ep.calls[3].startswith("HF_HUB_ENABLE_HF_TRANSFER=1 nohup " + LLAMA_BIN)
    assert "--jinja" in ep.calls[3]


def test_qwen_official_serves_fp8_on_vllm(cfg):
    from hermes.models import get_spec

    spec = get_spec("qwen-official")
    assert spec.server == "vllm"  # official safetensors → vLLM, not llama.cpp
    plan = plan_serve([("NVIDIA H200", 143771)], cfg, spec)
    cmd = vllm_command(cfg, plan, spec)
    assert "Qwen/Qwen3.6-27B" in cmd
    assert "--quantization fp8" in cmd
    assert "--served-model-name qwen3.6-27b-official" in cmd
    assert "--tool-call-parser hermes" in cmd


def test_qwen_official_fits_32gb_card(cfg):
    from hermes.models import get_spec

    spec = get_spec("qwen-official")
    # 32GB card reports ~31GB — above the 27B FP8 floor, below Hermes' 44.
    plan = plan_serve([("RTX 5090", 32760)], cfg, spec)
    assert plan.max_model_len == 32768


def test_qwen_40b_resolves_gguf_by_quant_tag(cfg):
    from hermes.models import get_spec

    spec = get_spec("qwen-40b")
    assert spec.server == "llama_cpp"
    plan = plan_serve([("RTX 6000 Pro", 49140)], cfg, spec)
    cmd = llama_command(cfg, plan, spec)
    # No exact filename for this repo — llama.cpp resolves it from the quant tag.
    assert "-hf DavidAU/Qwen3.6-40B-Claude-4.6-Opus-Deckard-Heretic-" in cmd
    assert ":Q5_K_M" in cmd
    assert "--hf-file" not in cmd
    assert "--alias qwen3.6-40b" in cmd
    assert "--jinja" in cmd


def test_weights_total_parsed_from_note():
    # The denominator for the download bar comes from each model's own note.
    assert provision._weights_total_bytes(get_spec("qwen")) == 19_000_000_000
    assert provision._weights_total_bytes(get_spec("hermes")) == 37_000_000_000
    # No parseable size → no percentage rather than a made-up one.
    from hermes.models import ModelSpec

    bare = ModelSpec(
        key="x", label="x", repo="x", identity="x", min_total_gb=1,
        max_model_len=1, context_tiers=[], context_beyond=1,
        weights_note="downloads some weights", served_name="x",
    )
    assert provision._weights_total_bytes(bare) is None


def test_fmt_size_switches_gb_to_mb():
    assert provision._fmt_size(4_700_000_000) == "4.7 GB"
    assert provision._fmt_size(312_000_000) == "312 MB"


def test_cache_bytes_sums_both_caches():
    from conftest import FakeEndpoint

    ep = FakeEndpoint([(0, "20401094656\n", "")])
    assert provision._cache_bytes(ep) == 20401094656
    assert "du -sb" in ep.calls[0] and ".cache/llama.cpp" in ep.calls[0]
    # A failed probe never aborts the wait — it just yields None this tick.
    assert provision._cache_bytes(FakeEndpoint([(1, "", "no such dir")])) is None
    assert provision._cache_bytes(FakeEndpoint([(0, "garbage", "")])) is None


class _DownloadThenReady:
    """Endpoint whose cache grows ~2GB per du probe; the /v1/models endpoint
    stays down until `ready_after` httpx polls, so the wait spans a real
    download phase before coming up."""

    def __init__(self, steps):
        self.remote_workspace = "~/hermes-workspace"
        self.calls: list[str] = []
        self._steps = iter(steps)
        self._bytes = 0

    def run(self, command, timeout=120, stdin=None):
        self.calls.append(command)
        if "du -sb" in command:
            self._bytes = next(self._steps, self._bytes)
            return 0, str(self._bytes), ""
        if "tail -c" in command:
            return 0, "warming up the model with an empty run", ""
        return 0, "", ""


def test_wait_ready_reports_download_progress(cfg, monkeypatch, capsys):
    monkeypatch.setattr(provision.time, "sleep", lambda _s: None)
    monkeypatch.setattr(provision.ui, "ENABLED", False)  # deterministic full lines

    # Endpoint comes up on the 4th /v1/models poll; cache grows meanwhile.
    calls = {"n": 0}

    class _Resp:
        status_code = 200

    def fake_get(url, timeout=5):
        calls["n"] += 1
        if calls["n"] < 4:
            raise provision.httpx.ConnectError("down")
        return _Resp()

    monkeypatch.setattr(provision.httpx, "get", fake_get)
    ep = _DownloadThenReady(steps=[2_000_000_000, 4_000_000_000, 6_000_000_000])

    assert wait_ready(ep, cfg, get_spec("qwen"), deadline_s=30) is True
    out = capsys.readouterr().out
    assert "downloading" in out
    assert "/ ~19.0 GB" in out          # denominator from the model's note
    assert "%" in out and "MB/s" in out  # progress and a live rate


def test_wait_ready_times_out_without_hanging(cfg, monkeypatch):
    monkeypatch.setattr(provision.time, "sleep", lambda _s: None)

    def always_down(url, timeout=5):
        raise provision.httpx.ConnectError("down")

    monkeypatch.setattr(provision.httpx, "get", always_down)
    ep = _DownloadThenReady(steps=[1_000_000_000])
    # A zero-length deadline returns False immediately rather than looping.
    assert wait_ready(ep, cfg, get_spec("qwen"), deadline_s=0) is False


def test_parse_ssh_strings():
    assert parse_ssh_string("ssh -p 12345 root@ssh4.vast.ai -L 8080:localhost:8080") == \
        ("root", "ssh4.vast.ai", 12345)
    assert parse_ssh_string("ssh://root@1.2.3.4:2222") == ("root", "1.2.3.4", 2222)
    assert parse_ssh_string("ssh root@host.example") == ("root", "host.example", 22)
    with pytest.raises(SSHError):
        parse_ssh_string("not an ssh string")


def test_tunnel_args_pure(home):
    ep = SSHEndpoint(host="h", port=2222)
    args = ep.tunnel_args(8000, 8000)
    assert "-N" in args
    assert "8000:127.0.0.1:8000" in args
    assert "ExitOnForwardFailure=yes" in args
    assert "ControlMaster=no" in args  # a tunnel must not ride the multiplexed master


def test_probe_net_isolation():
    from conftest import FakeEndpoint

    from hermes.gpu import probe_net_isolation

    assert probe_net_isolation(FakeEndpoint([(0, "NETOK", "")])) is True
    assert probe_net_isolation(FakeEndpoint([(1, "", "unshare: not permitted")])) is False


def test_endpoint_state_carries_net_isolation(home):
    from hermes.gpu import endpoint_from_state

    state = {"host": "h", "port": 22, "user": "root", "net_isolation": True}
    assert endpoint_from_state(state).net_isolation is True
    assert endpoint_from_state({"host": "h"}).net_isolation is False  # old gpu.json


def test_shell_path_quoting():
    from hermes.ssh import shell_path

    assert shell_path("~") == '"$HOME"'
    assert shell_path("~/work space") == '"$HOME"/\'work space\''
    assert shell_path("/plain/path") == "/plain/path"
    assert shell_path("/tmp/$(rm -rf /)") == "'/tmp/$(rm -rf /)'"
