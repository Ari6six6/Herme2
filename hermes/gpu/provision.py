"""Provision a model server on whatever box was rented: detect the GPUs, pick a
tier (context length scales with VRAM), install the right runtime, launch,
tunnel, and poll until the OpenAI endpoint answers.

Each model declares its runtime in hermes.models:
- **vLLM** (Hermes-4.3-36B, FP8 safetensors). Hopper/Ada/Blackwell run FP8
  natively; Ampere falls back to weight-only FP8 (Marlin) — works, a bit
  slower; pre-Ampere is unsupported.
- **llama.cpp** (Qwen3.6-27B, Q5_K GGUF). The native GGUF runtime —
  `llama-server`, built with CUDA on the box. Speaks the same OpenAI wire
  protocol, so nothing downstream changes.

Both write ~/vllm.pid and ~/vllm.log so `gpu status`/`down` stay runtime-agnostic.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx

from hermes.models import ModelSpec, resolve
from hermes.ui import dim, yellow

# Kept for the Hermes baseline and back-compat imports; per-model values now
# live on each ModelSpec in hermes.models.
MODEL_MAX_LEN = 524288  # Hermes 4.3 supports up to 512K
MIN_TOTAL_GB = 44  # FP8 36B weights ~37GB + runtime overhead

# vLLM gets its own venv so pip never fights the box's apt-managed packages.
# Installing into the system Python fails with "Cannot uninstall <pkg>, RECORD
# file not found. Hint: The package was installed by debian." — apt packages
# carry no RECORD for pip to remove, so any dependency vLLM wants to upgrade
# (e.g. PyJWT) aborts the whole install. --system-site-packages keeps the box's
# preinstalled CUDA/torch visible (no multi-GB re-download); vLLM's own
# dependency upgrades land inside the venv, shadowing the system copies without
# touching them.
VENV_DIR = "~/.hermes-venv"
VLLM_BIN = f"{VENV_DIR}/bin/vllm"

# llama.cpp is built once from source with CUDA and the binary cached here.
LLAMA_DIR = "~/.hermes-llama"
LLAMA_BIN = f"{LLAMA_DIR}/llama-server"
LLAMA_REPO = "https://github.com/ggml-org/llama.cpp"


class ProvisionError(Exception):
    pass


@dataclass
class ServePlan:
    tensor_parallel: int
    max_model_len: int
    gpu_memory_utilization: float
    total_vram_gb: int
    gpu_names: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def plan_serve(gpus: list[tuple[str, int]], cfg, spec: ModelSpec | None = None) -> ServePlan:
    """gpus: [(name, vram_mb), ...] from nvidia-smi. `spec` is the model being
    served (defaults to whatever the config points at)."""
    spec = spec or resolve(cfg)
    if not gpus:
        raise ProvisionError("no GPUs detected on the box (nvidia-smi empty)")
    total_gb = sum(mb for _, mb in gpus) // 1024
    if total_gb < spec.min_total_gb:
        raise ProvisionError(
            f"only {total_gb}GB total VRAM — {spec.label} needs "
            f"~{spec.min_total_gb}GB+. Rent a bigger box."
        )
    override = cfg.get("max_model_len", 0)
    if override:
        max_len = min(int(override), spec.max_model_len)
    else:
        max_len = spec.context_beyond
        for threshold, length in spec.context_tiers:
            if total_gb < threshold:
                max_len = length
                break
    # vLLM tensor-parallels across GPUs; llama.cpp splits layers across them on
    # its own. Either way every detected GPU is used.
    tensor_parallel = len(gpus)
    notes = list(spec.notes_extra)
    if spec.context_tiers and total_gb < spec.context_tiers[0][0]:
        notes.append(
            "tight fit: small context tier — the agent's package budget "
            "shrinks automatically to keep loops healthy."
        )
    names = [name for name, _ in gpus]
    if spec.server == "vllm" and any(
        "A100" in n or "A40" in n or "3090" in n or "A6000" in n for n in names
    ):
        notes.append("Ampere GPU: FP8 runs as weight-only (Marlin) — works, a bit slower.")
    return ServePlan(
        tensor_parallel=tensor_parallel,
        max_model_len=max_len,
        gpu_memory_utilization=0.95 if total_gb < 72 else 0.92,
        total_vram_gb=total_gb,
        gpu_names=names,
        notes=notes,
    )


def detect_gpus(endpoint) -> list[tuple[str, int]]:
    rc, out, err = endpoint.run(
        "nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits",
        timeout=30,
    )
    if rc != 0:
        raise ProvisionError(f"nvidia-smi failed: {err.strip() or out.strip()}")
    gpus = []
    for line in out.strip().splitlines():
        try:
            name, mem = line.rsplit(",", 1)
            gpus.append((name.strip(), int(float(mem.strip()))))
        except ValueError:
            continue
    return gpus


def vllm_command(cfg, plan: ServePlan, spec: ModelSpec | None = None) -> str:
    """Build the `vllm serve` command for an FP8 safetensors model."""
    spec = spec or resolve(cfg)
    parts = [
        VLLM_BIN, "serve", spec.repo,
        f"--served-model-name {spec.served_name}",
        f"--quantization {spec.quantization}",
        f"--tensor-parallel-size {plan.tensor_parallel}",
        f"--max-model-len {plan.max_model_len}",
        f"--gpu-memory-utilization {plan.gpu_memory_utilization}",
        f"--enable-auto-tool-choice --tool-call-parser {spec.tool_call_parser}",
        f"--port {cfg.get('gpu_port', 8000)}",
    ]
    if spec.tokenizer:
        parts.append(f"--tokenizer {spec.tokenizer}")
    parts += [str(a) for a in cfg.get("extra_vllm_args", [])]
    return " ".join(parts)


def llama_command(cfg, plan: ServePlan, spec: ModelSpec | None = None) -> str:
    """Build the native `llama-server` command. It pulls the GGUF itself from
    HF, offloads every layer to the GPU(s), and serves OpenAI tool calls from
    the model's own chat template (`--jinja`)."""
    spec = spec or resolve(cfg)
    # Exact filename when we have one; otherwise let llama.cpp resolve the file
    # from the repo by quant tag (`-hf user/repo:Q5_K_M`).
    if spec.gguf_file:
        weights = [f"--hf-repo {spec.repo}", f"--hf-file {spec.gguf_file}"]
    else:
        weights = [f"-hf {spec.repo}:{spec.gguf_quant}"]
    parts = [
        LLAMA_BIN,
        *weights,
        f"--alias {spec.served_name}",
        "--host 127.0.0.1",
        f"--port {cfg.get('gpu_port', 8000)}",
        f"--ctx-size {plan.max_model_len}",
        "--n-gpu-layers 999",  # offload all layers; harmless if the model has fewer
        "--jinja",
    ]
    parts += [str(a) for a in cfg.get("extra_llama_args", [])]
    return " ".join(parts)


def _install_vllm(endpoint) -> None:
    print(dim("ensuring vLLM is installed (first time can take a few minutes)..."))
    install = (
        f"test -x {VLLM_BIN} && exit 0; "
        # python3-venv is missing on some base images — install it on demand.
        f"python3 -m venv --system-site-packages {VENV_DIR} 2>/dev/null || "
        f"{{ apt-get update -qq && apt-get install -y -qq python3-venv && "
        f"python3 -m venv --system-site-packages {VENV_DIR}; }} && "
        f"{VENV_DIR}/bin/pip install -q -U pip vllm hf_transfer"
    )
    rc, _, err = endpoint.run(install, timeout=1800)
    if rc != 0:
        raise ProvisionError(f"vLLM install failed: {err.strip()[-800:]}")


def _install_llama(endpoint) -> None:
    print(dim("ensuring llama.cpp is built with CUDA (first time can take several minutes)..."))
    install = (
        f"test -x {LLAMA_BIN} && exit 0; "
        f"mkdir -p {LLAMA_DIR} && "
        "apt-get update -qq && apt-get install -y -qq "
        "git cmake build-essential libcurl4-openssl-dev && "
        f"rm -rf {LLAMA_DIR}/src && "
        f"git clone --depth 1 {LLAMA_REPO} {LLAMA_DIR}/src && "
        f"cmake -S {LLAMA_DIR}/src -B {LLAMA_DIR}/src/build "
        "-DGGML_CUDA=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release && "
        f"cmake --build {LLAMA_DIR}/src/build --config Release -j --target llama-server && "
        f"cp {LLAMA_DIR}/src/build/bin/llama-server {LLAMA_BIN}"
    )
    rc, _, err = endpoint.run(install, timeout=3600)
    if rc != 0:
        raise ProvisionError(
            f"llama.cpp build failed: {err.strip()[-800:]} "
            "(needs the CUDA toolkit — use a CUDA-devel image, not runtime-only)"
        )


def launch(endpoint, cfg, plan: ServePlan, spec: ModelSpec | None = None) -> None:
    spec = spec or resolve(cfg)
    rc, out, _ = endpoint.run("cat ~/vllm.pid 2>/dev/null && kill -0 $(cat ~/vllm.pid) 2>/dev/null && echo RUNNING")
    if "RUNNING" in out:
        print(yellow("a model server is already running on the box (kill it first with `gpu down` to relaunch)."))
        return
    if spec.server == "llama_cpp":
        _install_llama(endpoint)
        cmd = llama_command(cfg, plan, spec)
    else:
        _install_vllm(endpoint)
        cmd = vllm_command(cfg, plan, spec)
    endpoint.run(f"mkdir -p {endpoint.remote_workspace}")
    print(dim(f"launching: {cmd}"))
    rc, _, err = endpoint.run(
        "HF_HUB_ENABLE_HF_TRANSFER=1 nohup " + cmd + " > ~/vllm.log 2>&1 & echo $! > ~/vllm.pid"
    )
    if rc != 0:
        raise ProvisionError(f"launch failed: {err.strip()[-800:]}")


def wait_ready(endpoint, cfg, deadline_s: int = 1800) -> bool:
    """Poll the tunneled endpoint; stream fresh vllm.log lines meanwhile."""
    url = f"http://127.0.0.1:{cfg.get('local_port', 8000)}/v1/models"
    start = time.time()
    seen_bytes = 0
    while time.time() - start < deadline_s:
        try:
            if httpx.get(url, timeout=5).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        rc, out, _ = endpoint.run(
            f"tail -c +{seen_bytes + 1} ~/vllm.log 2>/dev/null", timeout=20
        )
        if rc == 0 and out:
            seen_bytes += len(out.encode())
            for line in out.splitlines()[-30:]:
                print(dim("  | " + line[:160]))
        time.sleep(5)
    return False
